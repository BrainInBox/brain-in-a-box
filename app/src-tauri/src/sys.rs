//! Running programs from a GUI app.
//!
//! An app opened from the Finder gets a bare PATH (/usr/bin:/bin:/usr/sbin:/sbin):
//! nothing the user installed is on it. Every command goes through `tool_path()`,
//! which adds the places our installers put things. Arguments are passed as a
//! vector, never through a shell, and secrets go through stdin or env, never argv.

use std::collections::HashSet;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::os::unix::process::CommandExt;
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

pub fn home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/"))
}

/// The inherited PATH first (a test can put stubs in front), then our install locations.
pub fn tool_path() -> String {
    let h = home();
    let mut dirs: Vec<String> = std::env::var("PATH")
        .unwrap_or_default()
        .split(':')
        .filter(|d| !d.is_empty())
        .map(String::from)
        .collect();
    for d in [
        h.join(".local/bin"),
        h.join(".bun/bin"),
        PathBuf::from("/opt/homebrew/bin"),
        PathBuf::from("/usr/local/bin"),
        PathBuf::from("/usr/bin"),
        PathBuf::from("/bin"),
        PathBuf::from("/usr/sbin"),
        PathBuf::from("/sbin"),
    ] {
        dirs.push(d.to_string_lossy().into_owned());
    }
    let mut seen = HashSet::new();
    dirs.retain(|d| seen.insert(d.clone()));
    dirs.join(":")
}

/// First executable named `name` on `tool_path()`.
pub fn find_tool(name: &str) -> Option<PathBuf> {
    tool_path()
        .split(':')
        .map(|d| Path::new(d).join(name))
        .find(|p| is_executable(p))
}

pub fn is_executable(p: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(p).map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0).unwrap_or(false)
}

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
    cmd
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
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped()).process_group(0);
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

/// The program is not reaped yet, so its pid still names its group (see `start`).
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
    use std::fs;

    fn alive(pid: &str) -> bool {
        Command::new("/bin/kill").args(["-0", pid]).stderr(Stdio::null()).status().is_ok_and(|s| s.success())
    }

    #[test]
    fn strips_colors_and_carriage_returns() {
        assert_eq!(strip_ansi("\u{1b}[1;32m✓\u{1b}[0m ok\r"), "✓ ok");
    }

    #[test]
    fn run_captures_output_and_exit_code() {
        let o = run(Path::new("/bin/sh"), &["-c", "echo hi; echo err >&2; exit 3"], Duration::from_secs(5));
        assert_eq!((o.code, o.stdout.trim(), o.stderr.trim()), (3, "hi", "err"));
        assert_eq!(output_text(Path::new("/bin/sh"), &["-c", "echo ' 1.2 '"], Duration::from_secs(5)).as_deref(), Some("1.2"));
        assert_eq!(output_text(Path::new("/bin/sh"), &["-c", "echo 1.2; exit 1"], Duration::from_secs(5)), None);
    }

    #[test]
    fn run_kills_on_timeout() {
        let t = Instant::now();
        let o = run(Path::new("/bin/sleep"), &["5"], Duration::from_millis(200));
        assert_eq!(o.code, -1);
        assert!(t.elapsed() < Duration::from_secs(2));
    }

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

    #[test]
    fn stdin_reaches_the_program() {
        let o = run_with(Path::new("/bin/cat"), &[], &[], Some("secret"), Duration::from_secs(5));
        assert_eq!(o.stdout, "secret");
    }

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

    fn isolated<'a>(env: &'a [(&'a str, &'a str)], cwd: &'a Path, stdin: &'a str, cancel: &'a AtomicBool) -> Isolated<'a> {
        Isolated { env, cwd, stdin, limit: Duration::from_secs(5), cancel }
    }

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
}
