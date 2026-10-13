//! Running programs from a GUI app.
//!
//! An app opened from the Finder gets a bare PATH (/usr/bin:/bin:/usr/sbin:/sbin):
//! nothing the user installed is on it. Every command goes through `tool_path()`,
//! which adds the places our installers put things. Arguments are passed as a
//! vector, never through a shell, and secrets go through stdin or env, never argv.

use std::collections::HashSet;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

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

/// Same, with extra environment variables and an optional stdin payload (secrets go here).
pub fn run_with(program: &Path, args: &[&str], env: &[(&str, &str)], stdin: Option<&str>, timeout: Duration) -> Output {
    let mut cmd = Command::new(program);
    cmd.args(args)
        .env("PATH", tool_path())
        .stdin(if stdin.is_some() { Stdio::piped() } else { Stdio::null() })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    for (k, v) in env {
        cmd.env(k, v);
    }
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => return Output { code: -1, stdout: String::new(), stderr: e.to_string() },
    };
    if let (Some(data), Some(mut sin)) = (stdin, child.stdin.take()) {
        let _ = sin.write_all(data.as_bytes());
    }
    // Drain both pipes on threads so a chatty program never blocks on a full pipe.
    let drain = |r: Option<Box<dyn Read + Send>>| {
        thread::spawn(move || {
            let mut s = String::new();
            if let Some(mut r) = r {
                let _ = r.read_to_string(&mut s);
            }
            s
        })
    };
    let out = drain(child.stdout.take().map(|r| Box::new(r) as Box<dyn Read + Send>));
    let err = drain(child.stderr.take().map(|r| Box::new(r) as Box<dyn Read + Send>));
    let deadline = Instant::now() + timeout;
    let code = loop {
        match child.try_wait() {
            Ok(Some(st)) => break st.code().unwrap_or(-1),
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(50)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                break -1;
            }
        }
    };
    Output { code, stdout: out.join().unwrap_or_default(), stderr: err.join().unwrap_or_default() }
}

/// Runs a long command (an installer) and hands every stdout/stderr line to `on_line`
/// as it comes. Returns the exit code (-1 if it could not start).
pub fn run_streaming(
    program: &Path,
    args: &[&str],
    env: &[(&str, &str)],
    cwd: Option<&Path>,
    on_line: &mut dyn FnMut(String),
) -> i32 {
    let mut cmd = Command::new(program);
    cmd.args(args).env("PATH", tool_path()).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
    for (k, v) in env {
        cmd.env(k, v);
    }
    if let Some(d) = cwd {
        cmd.current_dir(d);
    }
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            on_line(format!("impossible de lancer {}: {}", program.display(), e));
            return -1;
        }
    };
    let (tx, rx) = mpsc::channel::<String>();
    let pump = |r: Box<dyn Read + Send>, tx: mpsc::Sender<String>| {
        thread::spawn(move || {
            for line in BufReader::new(r).lines().map_while(Result::ok) {
                if tx.send(line).is_err() {
                    break;
                }
            }
        })
    };
    let mut pumps = Vec::new();
    if let Some(o) = child.stdout.take() {
        pumps.push(pump(Box::new(o), tx.clone()));
    }
    if let Some(e) = child.stderr.take() {
        pumps.push(pump(Box::new(e), tx.clone()));
    }
    drop(tx);
    for line in rx {
        on_line(strip_ansi(&line));
    }
    for p in pumps {
        let _ = p.join();
    }
    child.wait().ok().and_then(|s| s.code()).unwrap_or(-1)
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

    #[test]
    fn strips_colors_and_carriage_returns() {
        assert_eq!(strip_ansi("\u{1b}[1;32m✓\u{1b}[0m ok\r"), "✓ ok");
    }

    #[test]
    fn run_captures_output_and_exit_code() {
        let o = run(Path::new("/bin/sh"), &["-c", "echo hi; echo err >&2; exit 3"], Duration::from_secs(5));
        assert_eq!((o.code, o.stdout.trim(), o.stderr.trim()), (3, "hi", "err"));
    }

    #[test]
    fn run_kills_on_timeout() {
        let t = Instant::now();
        let o = run(Path::new("/bin/sleep"), &["5"], Duration::from_millis(200));
        assert_eq!(o.code, -1);
        assert!(t.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn stdin_reaches_the_program() {
        let o = run_with(Path::new("/bin/cat"), &[], &[], Some("secret"), Duration::from_secs(5));
        assert_eq!(o.stdout, "secret");
    }

    #[test]
    fn streaming_hands_over_every_line() {
        let mut lines = Vec::new();
        let code = run_streaming(Path::new("/bin/sh"), &["-c", "echo a; echo b >&2; exit 2"], &[], None, &mut |l| lines.push(l));
        lines.sort();
        assert_eq!((code, lines), (2, vec!["a".to_string(), "b".to_string()]));
    }
}
