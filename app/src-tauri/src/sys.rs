//! Running programs from a GUI app.
//!
//! An app opened from the Finder gets a bare PATH (/usr/bin:/bin:/usr/sbin:/sbin):
//! nothing the user installed is on it. Every command goes through `tool_path()`,
//! which adds the places our installers put things. Arguments are passed as a
//! vector, never through a shell, and secrets go through stdin or env, never argv.
//!
//! On Windows the PATH is the one Explorer had when the session opened: what an
//! installer adds later is missing from it, so `tool_path()` adds those places too.
//! Every program starts without a console window (CREATE_NO_WINDOW), and stopping one
//! stops the whole tree it started (taskkill /T), as a process group does on a Mac.
//!
//! On Linux an app started from the desktop's menu gets the session's PATH, which may
//! lack ~/.local/bin until the next login: the same places are added. Links open with
//! xdg-open, and the command window is whichever terminal the desktop has.

use std::collections::HashSet;
use std::ffi::OsString;
use std::io::{self, BufRead, BufReader, Read, Write};
#[cfg(unix)]
use std::os::unix::process::CommandExt;
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender};
use std::thread;
use std::time::{Duration, Instant};

const TICK: Duration = Duration::from_millis(50);
/// Once the program has exited, what it wrote is read within this time. Past it,
/// whatever still holds the pipes open (a job it left in the background) is not waited for.
const DRAIN: Duration = Duration::from_millis(500);

/// "Mac" or "PC", for the sentences that name the computer ("ton Mac", "ce PC").
#[cfg(target_os = "macos")]
pub const MACHINE: &str = "Mac";
#[cfg(not(target_os = "macos"))]
pub const MACHINE: &str = "PC";

#[cfg(not(windows))]
pub fn home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/"))
}

/// The profile folder (C:\Users\<name>), where install.ps1 and Claude Code put everything.
#[cfg(windows)]
pub fn home() -> PathBuf {
    std::env::var_os("USERPROFILE").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(r"C:\"))
}

/// Where the app's work folders go: a downloaded installer, the folder Claude reads.
/// A Mac's temporary folder (/var/folders/…) is the user's own, and they are 0700 in it.
#[cfg(target_os = "macos")]
pub fn work_root() -> io::Result<PathBuf> {
    Ok(std::env::temp_dir())
}

/// Linux's /tmp is every account's. The session's own folder instead (/run/user/<uid>:
/// 0700, the user's, named after no one) when it is really that, else one of the home.
#[cfg(target_os = "linux")]
pub fn work_root() -> io::Result<PathBuf> {
    if let Some(run) = std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from).filter(|d| private_to_me(d)) {
        return Ok(run);
    }
    let root = home().join(".brain-in-a-box").join("tmp");
    crate::perms::create_dir_all(&root)?;
    Ok(root)
}

/// A real folder (not a link), this process's user's, that no one else may enter.
#[cfg(target_os = "linux")]
fn private_to_me(dir: &Path) -> bool {
    use std::os::unix::fs::MetadataExt;
    // /proc/self belongs to whoever this process runs as.
    let Ok(me) = std::fs::metadata("/proc/self").map(|m| m.uid()) else { return false };
    dir.is_absolute()
        && std::fs::symlink_metadata(dir).is_ok_and(|m| m.is_dir() && m.uid() == me && m.mode() & 0o077 == 0)
}

/// Windows' TEMP may point outside the profile (C:\Temp, on many company PCs), where every
/// account can write: another one could swap an installer before it runs. A folder of the
/// profile instead, private like the rest of it (see perms.rs).
#[cfg(windows)]
pub fn work_root() -> io::Result<PathBuf> {
    let root = home().join(".brain-in-a-box").join("tmp");
    crate::perms::create_dir_all(&root)?;
    Ok(root)
}

/// `name` as a program file: "claude" on a Mac, "claude.exe" on Windows.
pub fn exe(name: &str) -> String {
    format!("{name}{}", std::env::consts::EXE_SUFFIX)
}

/// The inherited PATH first (a test can put stubs in front), then our install locations.
pub fn tool_path() -> OsString {
    let mut dirs: Vec<PathBuf> = std::env::var_os("PATH")
        .map(|p| std::env::split_paths(&p).filter(|d| !d.as_os_str().is_empty()).collect())
        .unwrap_or_default();
    dirs.extend(install_dirs(&home()));
    let mut seen = HashSet::new();
    dirs.retain(|d| seen.insert(d.clone()));
    // Only a folder holding the separator cannot be joined: such a one is left out.
    std::env::join_paths(&dirs)
        .or_else(|_| std::env::join_paths(dirs.iter().filter(|d| std::env::join_paths([d]).is_ok())))
        .unwrap_or_default()
}

#[cfg(target_os = "macos")]
fn install_dirs(h: &Path) -> Vec<PathBuf> {
    vec![
        h.join(".local/bin"),
        h.join(".bun/bin"),
        PathBuf::from("/opt/homebrew/bin"),
        PathBuf::from("/usr/local/bin"),
        PathBuf::from("/usr/bin"),
        PathBuf::from("/bin"),
        PathBuf::from("/usr/sbin"),
        PathBuf::from("/sbin"),
    ]
}

/// Claude Code and gbq in ~/.local/bin, Bun in ~/.bun/bin, the system's, then the snaps'
/// (Obsidian may come as one).
#[cfg(target_os = "linux")]
fn install_dirs(h: &Path) -> Vec<PathBuf> {
    vec![
        h.join(".local/bin"),
        h.join(".bun/bin"),
        PathBuf::from("/usr/local/bin"),
        PathBuf::from("/usr/bin"),
        PathBuf::from("/bin"),
        PathBuf::from("/usr/sbin"),
        PathBuf::from("/sbin"),
        PathBuf::from("/snap/bin"),
    ]
}

/// Where install.ps1 and the official installers put things: Claude Code and gbq in
/// ~\.local\bin, Bun, Git for one user or for all (winget picks), then Windows itself.
#[cfg(windows)]
fn install_dirs(h: &Path) -> Vec<PathBuf> {
    let mut dirs = vec![h.join(".local").join("bin"), h.join(".bun").join("bin")];
    if let Some(local) = std::env::var_os("LOCALAPPDATA") {
        dirs.push(Path::new(&local).join(r"Programs\Git\cmd"));
    }
    if let Some(pf) = std::env::var_os("ProgramFiles") {
        dirs.push(Path::new(&pf).join(r"Git\cmd"));
    }
    let root = system_root();
    dirs.extend([root.join("System32"), root.clone(), root.join(r"System32\WindowsPowerShell\v1.0")]);
    dirs
}

/// First executable named `name` on `tool_path()` (name.exe on Windows).
pub fn find_tool(name: &str) -> Option<PathBuf> {
    which_all(name).into_iter().next()
}

/// Every executable named `name` on `tool_path()`, in order (`which -a`).
pub fn which_all(name: &str) -> Vec<PathBuf> {
    let file = exe(name);
    std::env::split_paths(&tool_path()).map(|d| d.join(&file)).filter(|p| is_executable(p)).collect()
}

#[cfg(unix)]
pub fn is_executable(p: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(p).map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0).unwrap_or(false)
}

/// A program file: a .exe. Scripts (.cmd, .bat, .ps1) are never run as tools: a command
/// line handed to cmd.exe is a shell again.
#[cfg(windows)]
pub fn is_executable(p: &Path) -> bool {
    p.extension().is_some_and(|e| e.eq_ignore_ascii_case("exe")) && std::fs::metadata(p).is_ok_and(|m| m.is_file())
}

/// C:\Windows, where the system's own programs are found by their full path, never by PATH.
#[cfg(windows)]
pub fn system_root() -> PathBuf {
    std::env::var_os("SystemRoot").map_or_else(|| PathBuf::from(r"C:\Windows"), PathBuf::from)
}

/// A program of Windows itself: C:\Windows\System32\<name>.
#[cfg(windows)]
pub fn system32(name: &str) -> PathBuf {
    system_root().join("System32").join(name)
}

/// Windows PowerShell 5.1, there on every Windows 10 and 11.
#[cfg(windows)]
pub fn powershell() -> PathBuf {
    system32(r"WindowsPowerShell\v1.0\powershell.exe")
}

/// The system's curl: macOS's, Linux's package's (see the paquets step), or the one
/// Windows 10 (1803) and later ship.
#[cfg(not(windows))]
pub fn curl() -> PathBuf {
    PathBuf::from("/usr/bin/curl")
}

#[cfg(windows)]
pub fn curl() -> PathBuf {
    system32("curl.exe")
}

/// Where curl writes what nobody reads.
#[cfg(not(windows))]
pub const NULL_DEVICE: &str = "/dev/null";
#[cfg(windows)]
pub const NULL_DEVICE: &str = "NUL";

pub struct Output {
    /// Exit code; -1 when killed (timeout) or when the program could not start.
    pub code: i32,
    pub stdout: String,
    pub stderr: String,
}

impl Output {
    pub fn ok(&self) -> bool {
        self.code == 0
    }
}

/// Runs a short command and captures its output. Killed after `timeout`.
pub fn run(program: &Path, args: &[&str], timeout: Duration) -> Output {
    run_with(program, args, &[], None, timeout)
}

/// Trimmed stdout of a command that exited 0 and printed something.
pub fn output_text(program: &Path, args: &[&str], timeout: Duration) -> Option<String> {
    let out = run(program, args, timeout);
    let text = out.stdout.trim();
    (out.ok() && !text.is_empty()).then(|| text.to_string())
}

/// Same as `run`, with extra environment variables and an optional stdin payload (secrets go here).
pub fn run_with(program: &Path, args: &[&str], env: &[(&str, &str)], stdin: Option<&str>, timeout: Duration) -> Output {
    let mut cmd = command(program);
    cmd.args(args).stdin(if stdin.is_some() { Stdio::piped() } else { Stdio::null() });
    for (k, v) in env {
        cmd.env(k, v);
    }
    let (mut child, rx) = match start(&mut cmd) {
        Ok(started) => started,
        Err(e) => return Output { code: -1, stdout: String::new(), stderr: e.to_string() },
    };
    if let (Some(data), Some(mut sin)) = (stdin, child.stdin.take()) {
        // On a thread: a program that never reads its stdin must not keep us past the timeout.
        let data = data.to_string();
        thread::spawn(move || {
            let _ = sin.write_all(data.as_bytes());
        });
    }
    let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
    let code = supervise(&mut child, &rx, timeout, None, &mut |pipe, bytes| match pipe {
        Pipe::Out => stdout.extend(bytes),
        Pipe::Err => stderr.extend(bytes),
    });
    Output {
        code: code.unwrap_or(-1),
        stdout: String::from_utf8_lossy(&stdout).into_owned(),
        stderr: String::from_utf8_lossy(&stderr).into_owned(),
    }
}

/// Runs a long command (an installer) and hands every stdout/stderr line to `on_line`
/// as it comes. In `env`, None removes a variable the app itself inherited. Past
/// `limit`, the command and everything it started are killed. Returns the exit code,
/// -1 if it could not start or was killed.
pub fn run_streaming(
    program: &Path,
    args: &[&str],
    env: &[(&str, Option<&str>)],
    cwd: Option<&Path>,
    limit: Duration,
    on_line: &mut dyn FnMut(String),
) -> i32 {
    let mut cmd = command(program);
    cmd.args(args).stdin(Stdio::null());
    for (k, v) in env {
        match v {
            Some(v) => cmd.env(k, v),
            None => cmd.env_remove(k),
        };
    }
    if let Some(d) = cwd {
        cmd.current_dir(d);
    }
    let (mut child, rx) = match start(&mut cmd) {
        Ok(started) => started,
        Err(e) => {
            on_line(format!("impossible de lancer {}: {}", program.display(), e));
            return -1;
        }
    };
    let code = supervise(&mut child, &rx, limit, None, &mut |_, bytes| {
        on_line(strip_ansi(String::from_utf8_lossy(&bytes).trim_end_matches('\n')));
    });
    code.unwrap_or_else(|| {
        on_line(format!("Toujours pas fini au bout de {} : j'arrête.", minutes(limit)));
        -1
    })
}

/// How `run_isolated` runs a program that must not inherit anything from the app.
pub struct Isolated<'a> {
    /// The whole environment: nothing else is passed on.
    pub env: &'a [(&'a str, &'a str)],
    pub cwd: &'a Path,
    pub stdin: &'a str,
    pub limit: Duration,
    /// Set from anywhere (another thread, `on_line` itself): the program and
    /// everything it started are killed at the next tick.
    pub cancel: &'a AtomicBool,
}

/// Runs `program` with only `iso.env`, in `iso.cwd`, with `iso.stdin` as its input,
/// and hands every stdout/stderr line to `on_line`, raw. Returns the exit code, -1
/// if it could not start, was cancelled or ran past `iso.limit`.
pub fn run_isolated(program: &Path, args: &[&str], iso: &Isolated, on_line: &mut dyn FnMut(String)) -> i32 {
    let mut cmd = Command::new(program);
    cmd.args(args).env_clear().envs(iso.env.iter().copied()).current_dir(iso.cwd).stdin(Stdio::piped());
    let (mut child, rx) = match start(&mut cmd) {
        Ok(started) => started,
        Err(e) => {
            on_line(format!("impossible de lancer {}: {}", program.display(), e));
            return -1;
        }
    };
    if let Some(mut sin) = child.stdin.take() {
        // On a thread: a program that never reads its stdin must not keep us past the
        // limit. Dropping `sin` closes the pipe, which ends its input.
        let data = iso.stdin.to_string();
        thread::spawn(move || {
            let _ = sin.write_all(data.as_bytes());
        });
    }
    let code = supervise(&mut child, &rx, iso.limit, Some(iso.cancel), &mut |_, bytes| {
        on_line(String::from_utf8_lossy(&bytes).trim_end_matches(['\n', '\r']).to_string());
    });
    code.unwrap_or(-1)
}

fn command(program: &Path) -> Command {
    let mut cmd = Command::new(program);
    cmd.env("PATH", tool_path());
    #[cfg(windows)]
    if program == powershell() {
        without_ps7_modules(&mut cmd);
    }
    cmd
}

/// An app started from PowerShell 7 inherits its PSModulePath, PowerShell 7's own module
/// folders first: Windows PowerShell would load those and lose its own commands
/// (Get-FileHash, which Claude Code's installer needs). Without the variable, Windows
/// PowerShell uses its defaults.
#[cfg(windows)]
fn without_ps7_modules(cmd: &mut Command) {
    cmd.env_remove("PSModulePath");
}

#[derive(Clone, Copy)]
enum Pipe {
    Out,
    Err,
}

/// Lines from both pipes, newline included, as they come.
type Lines = Receiver<(Pipe, Vec<u8>)>;

/// Spawns the program in a process group of its own, so that a timeout stops it with
/// everything it started, and reads both pipes on threads that send each line on.
fn start(cmd: &mut Command) -> io::Result<(Child, Lines)> {
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    detach(cmd);
    let mut child = cmd.spawn()?;
    let (tx, rx) = mpsc::channel();
    if let Some(out) = child.stdout.take() {
        pump(out, Pipe::Out, tx.clone());
    }
    if let Some(err) = child.stderr.take() {
        pump(err, Pipe::Err, tx);
    }
    Ok((child, rx))
}

/// Raw bytes, newline included: a line that is not UTF-8 must not stop the reading,
/// or the program blocks on a full pipe.
fn pump(r: impl Read + Send + 'static, pipe: Pipe, tx: Sender<(Pipe, Vec<u8>)>) {
    thread::spawn(move || {
        let mut r = BufReader::new(r);
        loop {
            let mut line = Vec::new();
            match r.read_until(b'\n', &mut line) {
                Ok(0) | Err(_) => break,
                Ok(_) if tx.send((pipe, line)).is_err() => break,
                Ok(_) => {}
            }
        }
    });
}

/// Hands the output to `sink` until the program exits, or kills its whole group at the
/// deadline or once `cancel` is set. Returns the exit code, None when it had to be killed.
fn supervise(
    child: &mut Child,
    rx: &Lines,
    timeout: Duration,
    cancel: Option<&AtomicBool>,
    sink: &mut dyn FnMut(Pipe, Vec<u8>),
) -> Option<i32> {
    let deadline = Instant::now() + timeout;
    let cancelled = || cancel.is_some_and(|c| c.load(Ordering::SeqCst));
    let mut open = true;
    let mut exited: Option<(i32, Instant)> = None;
    loop {
        if open {
            match rx.recv_timeout(TICK) {
                Ok((pipe, bytes)) => sink(pipe, bytes),
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => open = false,
            }
        } else {
            thread::sleep(TICK);
        }
        if exited.is_none() {
            if let Ok(Some(status)) = child.try_wait() {
                exited = Some((status.code().unwrap_or(-1), Instant::now()));
            }
        }
        match exited {
            Some((code, at)) if !open || at.elapsed() >= DRAIN || Instant::now() >= deadline => return Some(code),
            None if Instant::now() >= deadline || cancelled() => break,
            _ => {}
        }
    }
    kill_group(child);
    // What it wrote before being killed still helps to understand why.
    let until = Instant::now() + DRAIN;
    while let Some(left) = until.checked_duration_since(Instant::now()) {
        match rx.recv_timeout(left) {
            Ok((pipe, bytes)) => sink(pipe, bytes),
            Err(_) => break,
        }
    }
    None
}

#[cfg(unix)]
fn detach(cmd: &mut Command) {
    cmd.process_group(0);
}

/// No console window flashes up, and none is shared with the app.
#[cfg(windows)]
fn detach(cmd: &mut Command) {
    cmd.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// The program is not reaped yet, so its pid still names its group (see `start`).
#[cfg(unix)]
fn kill_group(child: &mut Child) {
    let group = format!("-{}", child.id());
    let _ = Command::new("/bin/kill")
        .args(["-KILL", "--", &group])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
    // If /bin/kill could not run, at least the program itself goes.
    let _ = child.kill();
    let _ = child.wait();
}

/// Windows has no process groups: taskkill /T follows the tree from the program down.
/// The program is not reaped yet, so its pid still names it (the app holds its handle).
#[cfg(windows)]
fn kill_group(child: &mut Child) {
    let _ = Command::new(system32("taskkill.exe"))
        .args(["/T", "/F", "/PID", &child.id().to_string()])
        .creation_flags(CREATE_NO_WINDOW)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
    let _ = child.kill();
    let _ = child.wait();
}

/// Hands a link (https://, a Settings page) to the system, which opens it with the
/// right program: `open` on a Mac, Explorer on Windows (which answers 1 even when it
/// worked: only its start counts), xdg-open on Linux (which may wait for the browser:
/// only its start counts there too).
#[cfg(target_os = "macos")]
pub fn open_with_system(target: &str) -> bool {
    run(Path::new("/usr/bin/open"), &[target], Duration::from_secs(15)).ok()
}

#[cfg(target_os = "linux")]
pub fn open_with_system(target: &str) -> bool {
    find_tool("xdg-open").is_some_and(|x| spawn_detached(&x, &[target]).is_ok())
}

/// Starts a program that lives on by itself (Obsidian, a browser, a terminal) and does
/// not wait for it; a thread reaps it once it ends.
#[cfg(target_os = "linux")]
pub fn spawn_detached(program: &Path, args: &[&str]) -> io::Result<()> {
    let mut child = Command::new(program)
        .args(args)
        .env("PATH", tool_path())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .process_group(0)
        .spawn()?;
    thread::spawn(move || {
        let _ = child.wait();
    });
    Ok(())
}

/// The terminals a Linux desktop may have, each with the option that runs a program in it.
/// The Debian alternative comes first: it is the one the user's desktop picked.
#[cfg(target_os = "linux")]
const TERMINALS: &[(&str, &str)] = &[
    ("x-terminal-emulator", "-e"),
    ("gnome-terminal", "--"),
    ("ptyxis", "--"),
    ("kgx", "--"),
    ("konsole", "-e"),
    ("xfce4-terminal", "-x"),
    ("mate-terminal", "-x"),
    ("xterm", "-e"),
];

/// Opens a terminal window on `script` (an executable file of ours) and does not wait for
/// it: Terminal on a Mac. A terminal that quits at once with an error makes way for the next.
#[cfg(target_os = "linux")]
pub fn open_terminal_window(script: &Path) -> Result<(), String> {
    let file = script.to_string_lossy();
    // x-terminal-emulator may hand -e's argument to a shell: only a plain path goes there.
    let plain = !file.chars().any(|c| c.is_whitespace() || "'\"\\$`;&|<>(){}*?[]~#!".contains(c));
    for (name, flag) in TERMINALS.iter().filter(|(n, _)| plain || *n != "x-terminal-emulator") {
        let Some(term) = find_tool(name) else { continue };
        let started = Command::new(&term)
            .args([flag, file.as_ref()])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .process_group(0)
            .spawn();
        let Ok(mut child) = started else { continue };
        // gnome-terminal hands the window to its server and quits; xterm stays.
        let t = Instant::now();
        while t.elapsed() < Duration::from_secs(3) {
            match child.try_wait() {
                Ok(Some(status)) if status.success() => return Ok(()),
                Ok(Some(_)) => break,
                _ => thread::sleep(TICK),
            }
        }
        if child.try_wait().is_ok_and(|s| s.is_none()) {
            thread::spawn(move || {
                let _ = child.wait();
            });
            return Ok(());
        }
    }
    Err("Je n'ai pas trouvé de terminal à ouvrir sur ce PC.".into())
}

#[cfg(windows)]
pub fn open_with_system(target: &str) -> bool {
    spawn_detached(&system_root().join("explorer.exe"), &[target]).is_ok()
}

/// Starts a program that lives on by itself (Obsidian, a browser) and does not wait for it.
#[cfg(windows)]
pub fn spawn_detached(program: &Path, args: &[&str]) -> io::Result<()> {
    Command::new(program)
        .args(args)
        .env("PATH", tool_path())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map(drop)
}

/// Opens a PowerShell window on `script`, a .ps1 in the profile folder, and does not
/// wait for it: the Terminal of a Mac. `start` gives the window a console of its own.
/// The command line is fixed but for the script's place in the profile, named from
/// %USERPROFILE%: no path of the user's goes through cmd.exe's parsing. With `env`,
/// the window gets that environment and nothing else.
#[cfg(windows)]
pub fn open_console(script: &Path, env: Option<&[(String, OsString)]>) -> Result<(), String> {
    let rel = console_script(script, &home()).ok_or("Ce script n'est pas l'un des miens.")?;
    let line = format!(
        r#"/d /v:off /c start "" "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -NoExit -File "%USERPROFILE%\{rel}""#
    );
    let mut cmd = Command::new(system32("cmd.exe"));
    cmd.raw_arg(line).creation_flags(CREATE_NO_WINDOW).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    match env {
        Some(env) => {
            cmd.env_clear().envs(env.iter().map(|(k, v)| (k, v)));
        }
        None => without_ps7_modules(&mut cmd),
    }
    match cmd.status() {
        Ok(s) if s.success() => Ok(()),
        _ => Err("Je n'ai pas pu ouvrir PowerShell.".into()),
    }
}

/// The script's place in `home`, if it is plainly there: letters, digits, . - _ only.
#[cfg(windows)]
fn console_script(script: &Path, home: &Path) -> Option<String> {
    let rel = script.strip_prefix(home).ok()?;
    let parts: Vec<String> = rel
        .components()
        .map(|c| match c {
            std::path::Component::Normal(n) => n.to_str().map(String::from),
            _ => None,
        })
        .collect::<Option<_>>()?;
    let plain = |p: &String| {
        !p.is_empty() && !p.starts_with("..") && p.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'-' | b'_'))
    };
    (!parts.is_empty() && parts.iter().all(plain) && rel.extension().is_some_and(|e| e == "ps1")).then(|| parts.join("\\"))
}

/// "30 s", "2 min".
pub fn minutes(d: Duration) -> String {
    match d.as_secs() {
        s if s < 60 => format!("{s} s"),
        s => format!("{} min", s / 60),
    }
}

/// Installers color their output; the window shows plain text.
pub fn strip_ansi(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\u{1b}' {
            if chars.peek() == Some(&'[') {
                chars.next();
                for c2 in chars.by_ref() {
                    if c2.is_ascii_alphabetic() {
                        break;
                    }
                }
            }
            continue;
        }
        if c != '\r' {
            out.push(c);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    #[cfg(unix)]
    use std::fs;

    #[cfg(unix)]
    fn alive(pid: &str) -> bool {
        Command::new("/bin/kill").args(["-0", pid]).stderr(Stdio::null()).status().is_ok_and(|s| s.success())
    }

    /// Whatever TEMP says (C:\Temp, open to every account), the work folders stay in the profile.
    #[cfg(windows)]
    #[test]
    fn work_folders_live_in_the_profile() {
        let root = work_root().unwrap();
        assert!(root.starts_with(home()) && root.is_dir(), "{}", root.display());
    }

    #[test]
    fn strips_colors_and_carriage_returns() {
        assert_eq!(strip_ansi("\u{1b}[1;32m✓\u{1b}[0m ok\r"), "✓ ok");
    }

    #[cfg(unix)]
    #[test]
    fn run_captures_output_and_exit_code() {
        let o = run(Path::new("/bin/sh"), &["-c", "echo hi; echo err >&2; exit 3"], Duration::from_secs(5));
        assert_eq!((o.code, o.stdout.trim(), o.stderr.trim()), (3, "hi", "err"));
        assert_eq!(output_text(Path::new("/bin/sh"), &["-c", "echo ' 1.2 '"], Duration::from_secs(5)).as_deref(), Some("1.2"));
        assert_eq!(output_text(Path::new("/bin/sh"), &["-c", "echo 1.2; exit 1"], Duration::from_secs(5)), None);
    }

    #[cfg(unix)]
    #[test]
    fn run_kills_on_timeout() {
        let t = Instant::now();
        let o = run(Path::new("/bin/sleep"), &["5"], Duration::from_millis(200));
        assert_eq!(o.code, -1);
        assert!(t.elapsed() < Duration::from_secs(2));
    }

    #[cfg(unix)]
    #[test]
    fn a_background_job_does_not_hold_the_pipes_past_the_timeout() {
        // `sleep` keeps stdout and stderr open long after the shell is gone.
        let t = Instant::now();
        let o = run(Path::new("/bin/sh"), &["-c", "sleep 30 & echo $!"], Duration::from_secs(2));
        let took = t.elapsed();
        let _ = Command::new("/bin/kill").arg(o.stdout.trim()).status();
        assert_eq!(o.code, 0);
        assert!(took < Duration::from_secs(2), "{took:?}");
    }

    #[cfg(unix)]
    #[test]
    fn timeout_kills_what_the_program_started() {
        let dir = tempfile::tempdir().unwrap();
        let pidfile = dir.path().join("pid");
        let script = format!("sleep 30 & echo $! > '{}'; wait", pidfile.display());
        let t = Instant::now();
        let o = run(Path::new("/bin/sh"), &["-c", &script], Duration::from_millis(500));
        assert_eq!(o.code, -1);
        assert!(t.elapsed() < Duration::from_secs(2));
        let pid = fs::read_to_string(&pidfile).unwrap();
        // Killed, then reaped by launchd: give it a moment.
        let t = Instant::now();
        while alive(pid.trim()) && t.elapsed() < Duration::from_secs(3) {
            thread::sleep(TICK);
        }
        assert!(!alive(pid.trim()), "sleep {} survived", pid.trim());
    }

    #[cfg(unix)]
    #[test]
    fn stdin_reaches_the_program() {
        let o = run_with(Path::new("/bin/cat"), &[], &[], Some("secret"), Duration::from_secs(5));
        assert_eq!(o.stdout, "secret");
    }

    #[cfg(unix)]
    #[test]
    fn streaming_hands_over_every_line() {
        let mut lines = Vec::new();
        let code = run_streaming(
            Path::new("/bin/sh"),
            &["-c", "echo a; echo b >&2; exit 2"],
            &[],
            None,
            Duration::from_secs(5),
            &mut |l| lines.push(l),
        );
        lines.sort();
        assert_eq!((code, lines), (2, vec!["a".to_string(), "b".to_string()]));
    }

    #[cfg(unix)]
    #[test]
    fn streaming_stops_at_its_limit() {
        let mut lines = Vec::new();
        let t = Instant::now();
        let code = run_streaming(
            Path::new("/bin/sh"),
            &["-c", "echo started; sleep 30 & sleep 30"],
            &[],
            None,
            Duration::from_millis(300),
            &mut |l| lines.push(l),
        );
        assert_eq!(code, -1);
        assert!(t.elapsed() < Duration::from_secs(2));
        assert_eq!(lines.first().map(String::as_str), Some("started"));
        assert_eq!(lines.last().map(String::as_str), Some("Toujours pas fini au bout de 0 s : j'arrête."));
    }

    #[cfg(unix)]
    #[test]
    fn streaming_sets_and_removes_variables() {
        let mut lines = Vec::new();
        let code = run_streaming(
            Path::new("/bin/sh"),
            &["-c", r#"echo "${HOME-unset} $BIAB_T""#],
            &[("HOME", None), ("BIAB_T", Some("x"))],
            None,
            Duration::from_secs(5),
            &mut |l| lines.push(l),
        );
        assert_eq!((code, lines), (0, vec!["unset x".to_string()]));
    }

    #[cfg(unix)]
    #[test]
    fn a_line_that_is_not_utf8_does_not_stop_the_stream() {
        let mut lines = Vec::new();
        let code = run_streaming(
            Path::new("/bin/sh"),
            &["-c", r"printf 'a\377\nb\n'"],
            &[],
            None,
            Duration::from_secs(5),
            &mut |l| lines.push(l),
        );
        assert_eq!((code, lines), (0, vec!["a\u{fffd}".to_string(), "b".to_string()]));
    }

    #[cfg(unix)]
    fn isolated<'a>(env: &'a [(&'a str, &'a str)], cwd: &'a Path, stdin: &'a str, cancel: &'a AtomicBool) -> Isolated<'a> {
        Isolated { env, cwd, stdin, limit: Duration::from_secs(5), cancel }
    }

    #[cfg(unix)]
    #[test]
    fn isolated_programs_get_only_the_given_environment() {
        let dir = tempfile::tempdir().unwrap();
        let cancel = AtomicBool::new(false);
        let env = [("HOME", "/nowhere"), ("BIAB_T", "x y")];
        let mut lines = Vec::new();
        let code = run_isolated(Path::new("/usr/bin/env"), &[], &isolated(&env, dir.path(), "", &cancel), &mut |l| lines.push(l));
        lines.sort();
        assert_eq!((code, lines), (0, vec!["BIAB_T=x y".to_string(), "HOME=/nowhere".to_string()]));
    }

    #[cfg(unix)]
    #[test]
    fn isolated_programs_read_stdin_in_their_folder() {
        let dir = tempfile::tempdir().unwrap();
        let cwd = fs::canonicalize(dir.path()).unwrap();
        let cancel = AtomicBool::new(false);
        let mut lines = Vec::new();
        let iso = isolated(&[], &cwd, "le dossier\nsur deux lignes\n", &cancel);
        let code = run_isolated(Path::new("/bin/sh"), &["-c", "pwd -P; /bin/cat; exit 4"], &iso, &mut |l| lines.push(l));
        assert_eq!(code, 4);
        assert_eq!(lines, [cwd.to_string_lossy().as_ref(), "le dossier", "sur deux lignes"]);
    }

    #[cfg(unix)]
    #[test]
    fn a_program_that_ignores_a_big_stdin_does_not_block() {
        let dir = tempfile::tempdir().unwrap();
        let cancel = AtomicBool::new(false);
        let big = "x".repeat(4 << 20);
        let t = Instant::now();
        let code = run_isolated(Path::new("/bin/sleep"), &["0.1"], &isolated(&[], dir.path(), &big, &cancel), &mut |_| {});
        assert_eq!(code, 0);
        assert!(t.elapsed() < Duration::from_secs(3), "{:?}", t.elapsed());
    }

    #[cfg(unix)]
    #[test]
    fn cancel_kills_the_program_and_what_it_started() {
        let dir = tempfile::tempdir().unwrap();
        let pidfile = dir.path().join("pid");
        let script = format!("/bin/sleep 30 & echo $! > '{}'; wait", pidfile.display());
        let cancel = AtomicBool::new(false);
        let t = Instant::now();
        let code = thread::scope(|s| {
            s.spawn(|| {
                thread::sleep(Duration::from_millis(300));
                cancel.store(true, Ordering::SeqCst);
            });
            run_isolated(Path::new("/bin/sh"), &["-c", &script], &isolated(&[], dir.path(), "", &cancel), &mut |_| {})
        });
        assert_eq!(code, -1);
        assert!(t.elapsed() < Duration::from_secs(3), "{:?}", t.elapsed());
        let pid = fs::read_to_string(&pidfile).unwrap();
        let t = Instant::now();
        while alive(pid.trim()) && t.elapsed() < Duration::from_secs(3) {
            thread::sleep(TICK);
        }
        assert!(!alive(pid.trim()), "sleep {} survived", pid.trim());
    }

    #[cfg(unix)]
    #[test]
    fn a_line_can_stop_the_program() {
        let dir = tempfile::tempdir().unwrap();
        let cancel = AtomicBool::new(false);
        let mut seen = Vec::new();
        let t = Instant::now();
        let code = run_isolated(
            Path::new("/bin/sh"),
            &["-c", "echo stop; exec /bin/sleep 30"],
            &isolated(&[], dir.path(), "", &cancel),
            &mut |l| {
                if l == "stop" {
                    cancel.store(true, Ordering::SeqCst);
                }
                seen.push(l);
            },
        );
        assert_eq!((code, seen), (-1, vec!["stop".to_string()]));
        assert!(t.elapsed() < Duration::from_secs(3));
    }

    #[cfg(unix)]
    #[test]
    fn isolated_stops_at_its_limit_and_says_when_it_cannot_start() {
        let dir = tempfile::tempdir().unwrap();
        let cancel = AtomicBool::new(false);
        let iso = Isolated { limit: Duration::from_millis(300), ..isolated(&[], dir.path(), "", &cancel) };
        let t = Instant::now();
        assert_eq!(run_isolated(Path::new("/bin/sleep"), &["30"], &iso, &mut |_| {}), -1);
        assert!(t.elapsed() < Duration::from_secs(2));
        let mut lines = Vec::new();
        assert_eq!(run_isolated(&dir.path().join("missing"), &[], &iso, &mut |l| lines.push(l)), -1);
        assert!(lines[0].starts_with("impossible de lancer"), "{lines:?}");
    }

    #[test]
    fn durations_read_well() {
        assert_eq!(minutes(Duration::from_secs(30)), "30 s");
        assert_eq!(minutes(Duration::from_secs(150)), "2 min");
    }

    #[test]
    fn program_names_follow_the_system() {
        assert_eq!(exe("claude"), if cfg!(windows) { "claude.exe" } else { "claude" });
        let path = tool_path();
        let dirs: Vec<PathBuf> = std::env::split_paths(&path).collect();
        assert!(dirs.contains(&home().join(".local").join("bin")) && dirs.contains(&home().join(".bun").join("bin")));
        let mut seen = HashSet::new();
        assert!(dirs.iter().all(|d| seen.insert(d.clone())), "no folder twice");
    }

    // ── Windows: the same promises, with Windows' own programs ──────────────

    #[cfg(windows)]
    fn cmd(script: &str) -> (PathBuf, Vec<String>) {
        (system32("cmd.exe"), vec!["/d".into(), "/c".into(), script.into()])
    }

    #[cfg(windows)]
    fn alive(pid: &str) -> bool {
        let filter = format!("PID eq {pid}");
        let o = run(&system32("tasklist.exe"), &["/FI", &filter, "/NH"], Duration::from_secs(20));
        o.stdout.split_whitespace().any(|w| w == pid)
    }

    /// PowerShell starts a ping that outlives it unless the whole tree is stopped,
    /// and writes the ping's pid to $env:BIAB_PIDFILE.
    #[cfg(windows)]
    const TREE: &str = "$p = Start-Process -FilePath ping.exe -ArgumentList '-n','60','127.0.0.1' -PassThru -WindowStyle Hidden; \
                        Set-Content -LiteralPath $env:BIAB_PIDFILE -Value $p.Id; $p.WaitForExit()";

    #[cfg(windows)]
    fn wait_for_pid(file: &Path) -> String {
        let t = Instant::now();
        while t.elapsed() < Duration::from_secs(60) {
            if let Ok(pid) = std::fs::read_to_string(file) {
                if !pid.trim().is_empty() {
                    return pid.trim().to_string();
                }
            }
            thread::sleep(TICK);
        }
        panic!("the ping never started");
    }

    #[cfg(windows)]
    fn gone(pid: &str) -> bool {
        let t = Instant::now();
        while alive(pid) && t.elapsed() < Duration::from_secs(10) {
            thread::sleep(Duration::from_millis(200));
        }
        !alive(pid)
    }

    #[cfg(windows)]
    #[test]
    fn windows_run_captures_output_and_exit_code() {
        let (c, a) = cmd("echo hi& echo err 1>&2& exit /b 3");
        let a: Vec<&str> = a.iter().map(String::as_str).collect();
        let o = run(&c, &a, Duration::from_secs(20));
        assert_eq!((o.code, o.stdout.trim(), o.stderr.trim()), (3, "hi", "err"));
        let o = run_with(&system32("sort.exe"), &[], &[], Some("secret\r\n"), Duration::from_secs(20));
        assert_eq!(o.stdout.trim(), "secret");
        assert!(find_tool("cmd").is_some_and(|p| p.ends_with("cmd.exe")));
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("x.cmd"), "@echo hi").unwrap();
        assert!(!is_executable(&dir.path().join("x.cmd")), "a script is never a tool");
    }

    #[cfg(windows)]
    #[test]
    fn windows_timeout_kills_the_whole_tree() {
        let dir = tempfile::tempdir().unwrap();
        let pidfile = dir.path().join("pid");
        let pidfile_s = pidfile.to_string_lossy().into_owned();
        let watcher = thread::spawn({
            let pidfile = pidfile.clone();
            move || wait_for_pid(&pidfile)
        });
        let t = Instant::now();
        let o = run_with(&powershell(), &["-NoProfile", "-Command", TREE], &[("BIAB_PIDFILE", &pidfile_s)], None, Duration::from_secs(20));
        assert_eq!(o.code, -1, "{}", o.stderr);
        assert!(t.elapsed() < Duration::from_secs(30));
        let pid = watcher.join().unwrap();
        assert!(gone(&pid), "ping {pid} survived");
    }

    #[cfg(windows)]
    #[test]
    fn windows_cancel_kills_the_whole_tree() {
        let dir = tempfile::tempdir().unwrap();
        let pidfile = dir.path().join("pid");
        let pidfile_s = pidfile.to_string_lossy().into_owned();
        // What PowerShell itself needs to start, and the pid file.
        let kept: Vec<(String, String)> = ["SystemRoot", "windir", "USERPROFILE", "TEMP", "TMP", "LOCALAPPDATA", "APPDATA"]
            .iter()
            .filter_map(|k| std::env::var(k).ok().map(|v| (k.to_string(), v)))
            .chain([("PATH".to_string(), system32("").to_string_lossy().into_owned()), ("BIAB_PIDFILE".to_string(), pidfile_s)])
            .collect();
        let env: Vec<(&str, &str)> = kept.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect();
        let cancel = AtomicBool::new(false);
        let iso = Isolated { env: &env, cwd: dir.path(), stdin: "", limit: Duration::from_secs(120), cancel: &cancel };
        let code = thread::scope(|s| {
            s.spawn(|| {
                wait_for_pid(&pidfile);
                cancel.store(true, Ordering::SeqCst);
            });
            run_isolated(&powershell(), &["-NoProfile", "-Command", TREE], &iso, &mut |_| {})
        });
        assert_eq!(code, -1);
        let pid = std::fs::read_to_string(&pidfile).unwrap();
        assert!(gone(pid.trim()), "ping {} survived", pid.trim());
    }

    #[cfg(windows)]
    #[test]
    fn windows_streaming_sets_and_removes_variables() {
        let (c, a) = cmd("echo a& (echo b)1>&2& echo [%USERNAME%] [%BIAB_T%]& exit /b 2");
        let a: Vec<&str> = a.iter().map(String::as_str).collect();
        let mut lines = Vec::new();
        let code = run_streaming(&c, &a, &[("USERNAME", None), ("BIAB_T", Some("x"))], None, Duration::from_secs(20), &mut |l| {
            lines.push(l)
        });
        lines.sort();
        assert_eq!((code, lines), (2, vec!["[%USERNAME%] [x]".to_string(), "a".to_string(), "b".to_string()]));
    }

    #[cfg(windows)]
    #[test]
    fn windows_isolated_programs_get_only_the_given_environment() {
        let dir = tempfile::tempdir().unwrap();
        let cancel = AtomicBool::new(false);
        let root = system_root().to_string_lossy().into_owned();
        let env = [("SystemRoot", root.as_str()), ("BIAB_T", "x y")];
        let iso = Isolated { env: &env, cwd: dir.path(), stdin: "", limit: Duration::from_secs(20), cancel: &cancel };
        let (c, a) = cmd("set");
        let a: Vec<&str> = a.iter().map(String::as_str).collect();
        let mut lines = Vec::new();
        assert_eq!(run_isolated(&c, &a, &iso, &mut |l| lines.push(l)), 0);
        let keys: Vec<String> = lines.iter().filter_map(|l| l.split_once('=')).map(|(k, _)| k.to_ascii_uppercase()).collect();
        assert!(keys.contains(&"BIAB_T".to_string()) && keys.contains(&"SYSTEMROOT".to_string()), "{lines:?}");
        for inherited in ["USERNAME", "USERPROFILE", "PATH", "APPDATA"] {
            assert!(!keys.contains(&inherited.to_string()), "{inherited} passed through: {lines:?}");
        }
        let mut said = Vec::new();
        assert_eq!(run_isolated(&dir.path().join("missing.exe"), &[], &iso, &mut |l| said.push(l)), -1);
        assert!(said[0].starts_with("impossible de lancer"), "{said:?}");
    }

    /// Run from PowerShell 7 (the CI's shell, or a user's), the app must still give
    /// Windows PowerShell its own commands: Claude Code's installer uses Get-FileHash.
    #[cfg(windows)]
    #[test]
    fn windows_powershell_keeps_its_own_commands() {
        let o = run(&powershell(), &["-NoProfile", "-NonInteractive", "-Command", "(Get-Command Get-FileHash).Name"], Duration::from_secs(60));
        assert_eq!((o.code, o.stdout.trim()), (0, "Get-FileHash"), "{}", o.stderr);
    }

    #[cfg(windows)]
    #[test]
    fn windows_consoles_open_only_our_scripts() {
        let home = Path::new(r"C:\Users\Jo Doe");
        let ok = console_script(&home.join(r".brain-in-a-box\claude-login.ps1"), home);
        assert_eq!(ok.as_deref(), Some(r".brain-in-a-box\claude-login.ps1"));
        for bad in [
            r"C:\Users\Jo Doe\..\x.ps1",
            r"C:\Users\Jo Doe\a b.ps1",
            r"C:\Users\Jo Doe\x&calc.ps1",
            r"C:\Users\Jo Doe\%TEMP%.ps1",
            r"C:\Users\Jo Doe\x.cmd",
            r"C:\Users\Jo Doe",
            r"C:\Temp\x.ps1",
        ] {
            assert_eq!(console_script(Path::new(bad), home), None, "{bad}");
        }
        assert!(open_console(Path::new(r"C:\Temp\x.ps1"), None).is_err());
    }
}
