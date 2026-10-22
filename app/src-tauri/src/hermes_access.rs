//! The computer's permissions Hermes asks for (docs/proposals/hermes.md, 20/10): the user's
//! files, the screen with the keyboard and the mouse, the voice. No app can give them itself:
//! the app says what each one is for, opens the right page or lets the system ask, then checks.
//! Never TCC's database, never `tccutil`, never a permission given in the user's place.
//!
//! On a Mac:
//! - « disque », the files: Full Disk Access for the Hermes app. The check is Hermes's own
//!   doctor's (listing the folder TCC guards), run by the Hermes app itself, so that macOS
//!   answers for Hermes and not for this app (hermes_access.js).
//! - « ecran », the screen, the keyboard and the mouse: Accessibility and Screen Recording for
//!   CuaDriver, the identity `hermes computer-use doctor` names. Hermes's own flow asks and
//!   checks (`hermes computer-use permissions grant`: 0 once both are given and seen).
//! - « micro », the voice: the microphone for the Hermes app, macOS's own window first.
//!
//! On Windows: the microphone, and Defender's controlled folder access when it is on.

use serde::Serialize;
use serde_json::Value;

use crate::hermes;

/// Where a permission stands.
#[derive(Serialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum State {
    /// Given, and seen given.
    Ok,
    /// Not given yet: the app can ask.
    Missing,
    /// The app could not tell: it says so, never a yes it did not see.
    Unknown,
}

/// One permission, by the id the window knows it by.
#[derive(Serialize, Clone, Debug, PartialEq, Eq)]
pub struct Perm {
    pub id: &'static str,
    pub state: State,
}

const UNKNOWN_PERM: &str = "Je ne connais pas cette autorisation.";

/// Each permission Hermes asks for, in the order the app asks for them; with `only`, that one.
/// None when Hermes is not on this computer, or on a system with none to give.
pub fn status(only: Option<&str>) -> Option<Vec<Perm>> {
    platform::status(&hermes::bin()?, only)
}

/// Asks for one: Ok(true) once it is given and seen, Ok(false) when its page is open for the
/// user (the window then checks every few seconds).
pub fn ask(id: &str) -> Result<bool, String> {
    let bin = hermes::bin().ok_or("Je ne trouve plus Hermes sur cet ordinateur.")?;
    platform::ask(&bin, id)
}

#[cfg(target_os = "macos")]
mod platform {
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::time::Duration;

    use super::{first_fuse, parse_probe, parse_screen, Micro, Perm, Probe, State, UNKNOWN_PERM};
    use crate::{hermes, repair, steps, sys};

    /// The Hermes app's identity: its installer (Hermes-Setup, com.nousresearch.hermes.setup)
    /// can sit in /Applications under the same name.
    const HERMES_APP: &str = "com.nousresearch.hermes";
    const CHECK: &str = include_str!("hermes_access.js");
    /// Starting the Hermes app as Node, listing a folder, reading a status.
    const CHECK_TIMEOUT: Duration = Duration::from_secs(30);
    /// macOS's microphone window waits for the user: the check gives it two minutes.
    const ASK_TIMEOUT: Duration = Duration::from_secs(160);
    /// CuaDriver's window, then System Settings, by hand.
    const GRANT_TIMEOUT: Duration = Duration::from_secs(600);
    /// Hermes downloads CuaDriver.
    const INSTALL_TIMEOUT: Duration = Duration::from_secs(300);
    const NO_APP: &str = "Je ne trouve pas l'app Hermes sur ce Mac. Ouvre Hermes une fois, puis réessaie.";
    const CANNOT_CHECK: &str = "Je n'arrive pas à vérifier les autorisations d'Hermes sur ce Mac. Réessaie dans un instant.";
    const NOT_INSTALLED: &str = "Hermes n'a pas pu installer son outil pour l'écran (CuaDriver). Vérifie la connexion à Internet, puis réessaie.";
    const NOT_GRANTED: &str = "Je ne vois pas encore ces deux autorisations pour CuaDriver. Réessaie : la fenêtre te montre ce qui manque.";

    pub fn status(bin: &Path, only: Option<&str>) -> Option<Vec<Perm>> {
        let wants = |id: &str| only.is_none_or(|o| o == id);
        // Without the Hermes app, nothing to give the files or the voice to.
        let seen = desktop_app(&sys::home()).filter(|_| wants("disque") || wants("micro")).map(|app| check(&app, false));
        let mut perms = Vec::new();
        if let Some(seen) = seen.as_ref().filter(|_| wants("disque")) {
            perms.push(Perm { id: "disque", state: seen.as_ref().map_or(State::Unknown, |p| p.disque) });
        }
        if wants("ecran") {
            perms.push(Perm { id: "ecran", state: screen(bin) });
        }
        if let Some(seen) = seen.as_ref().filter(|_| wants("micro")) {
            perms.push(Perm { id: "micro", state: seen.as_ref().map_or(State::Unknown, |p| p.micro.state()) });
        }
        Some(perms)
    }

    pub fn ask(bin: &Path, id: &str) -> Result<bool, String> {
        match id {
            "ecran" => ask_screen(bin),
            "disque" => ask_files(&desktop_app(&sys::home()).ok_or(NO_APP)?),
            "micro" => ask_voice(&desktop_app(&sys::home()).ok_or(NO_APP)?),
            _ => Err(UNKNOWN_PERM.into()),
        }
    }

    /// Full Disk Access has no window of its own: its page, and the Hermes app in the Finder, to
    /// drag into the list when it is not there yet.
    fn ask_files(app: &Path) -> Result<bool, String> {
        let _ = sys::run(Path::new("/usr/bin/open"), &["-R", &app.to_string_lossy()], CHECK_TIMEOUT);
        repair::open_settings("privacy_all_files").map(|()| false)
    }

    /// macOS's own window first, in Hermes's name. Refused before (or no answer), its page: a
    /// refusal is only taken back there.
    fn ask_voice(app: &Path) -> Result<bool, String> {
        match check(app, true).map(|p| p.micro) {
            Some(Micro::Ok) => Ok(true),
            Some(_) => repair::open_settings("privacy_microphone").map(|()| false),
            None => Err(CANNOT_CHECK.into()),
        }
    }

    /// Hermes's own flow: CuaDriver's window asks for both and closes once macOS shows them
    /// given. CuaDriver not there yet: Hermes installs it first.
    fn ask_screen(bin: &Path) -> Result<bool, String> {
        let status = sys::run(bin, &["computer-use", "permissions", "status", "--json"], hermes::TIMEOUT);
        let missing = serde_json::from_str::<serde_json::Value>(status.stdout.trim())
            .is_ok_and(|v| v.get("installed").and_then(serde_json::Value::as_bool) == Some(false));
        if missing && !sys::run(bin, &["computer-use", "install"], INSTALL_TIMEOUT).ok() {
            return Err(NOT_INSTALLED.into());
        }
        if sys::run(bin, &["computer-use", "permissions", "grant"], GRANT_TIMEOUT).ok() {
            Ok(true)
        } else {
            Err(NOT_GRANTED.into())
        }
    }

    fn screen(bin: &Path) -> State {
        parse_screen(&sys::run(bin, &["computer-use", "permissions", "status", "--json"], hermes::TIMEOUT).stdout)
    }

    /// The Hermes app the user opens: in Applications when `hermes update` copied it there,
    /// else where Hermes builds it.
    pub(super) fn desktop_app(home: &Path) -> Option<PathBuf> {
        [
            PathBuf::from("/Applications/Hermes.app"),
            home.join("Applications/Hermes.app"),
            home.join(".hermes/hermes-agent/apps/desktop/release/mac-arm64/Hermes.app"),
        ]
        .into_iter()
        .find(|app| is_hermes(app))
    }

    fn is_hermes(app: &Path) -> bool {
        let plist = app.join("Contents/Info.plist");
        let Some(plist) = plist.to_str().filter(|_| plist.is_file()) else { return false };
        let out = sys::run(Path::new("/usr/bin/plutil"), &["-extract", "CFBundleIdentifier", "raw", "-o", "-", plist], CHECK_TIMEOUT);
        out.ok() && out.stdout.trim() == HERMES_APP
    }

    fn check(app: &Path, ask: bool) -> Option<Probe> {
        check_with(Path::new("/usr/bin/open"), app, ask)
    }

    /// The check, run by the Hermes app itself as Node: never a copy of it (LaunchServices would
    /// register the copy for hermes:// links), never when its fuse is off (the app would open
    /// its window instead). Nothing read: None, never a yes.
    pub(super) fn check_with(open: &Path, app: &Path, ask: bool) -> Option<Probe> {
        let lib = app.join("Contents/Frameworks/Electron Framework.framework/Electron Framework");
        if fs::File::open(lib).ok().and_then(first_fuse) != Some(b'1') {
            return None;
        }
        let work = steps::TempDir::new_in(&std::env::temp_dir()).ok()?;
        let (script, answer) = (work.path().join("check.js"), work.path().join("answer.json"));
        fs::write(&script, CHECK).ok()?;
        let mut args = vec!["-n", "-g", "-j", "-W", "--env", "ELECTRON_RUN_AS_NODE=1"];
        args.extend(["--stdout", answer.to_str()?, "--stderr", "/dev/null", "-a", app.to_str()?, "--args", script.to_str()?]);
        if ask {
            args.push("ask");
        }
        sys::run(open, &args, if ask { ASK_TIMEOUT } else { CHECK_TIMEOUT });
        parse_probe(&fs::read_to_string(&answer).ok()?)
    }
}

#[cfg(windows)]
mod platform {
    use std::path::Path;

    use super::{guard_state, mic_state, parse_reg, Perm, UNKNOWN_PERM};
    use crate::{hermes, repair, sys};

    const MIC: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone";
    /// Defender's controlled folder access, then the apps it lets write.
    const GUARD: &str = "$p = Get-MpPreference; $p.EnableControlledFolderAccess; $p.ControlledFolderAccessAllowedApplications";

    pub fn status(bin: &Path, only: Option<&str>) -> Option<Vec<Perm>> {
        let wants = |id: &str| only.is_none_or(|o| o == id);
        let mut perms = Vec::new();
        if wants("micro") {
            // The microphone for every app, then for the desktop apps Hermes is one of.
            let state = mic_state(reg(MIC).as_deref(), reg(&format!(r"{MIC}\NonPackaged")).as_deref());
            perms.push(Perm { id: "micro", state });
        }
        if wants("dossiers") {
            let out = sys::run(&sys::powershell(), &["-NoProfile", "-NonInteractive", "-Command", GUARD], hermes::TIMEOUT);
            if let Some(state) = out.ok().then(|| guard_state(&out.stdout, &bin.to_string_lossy())).flatten() {
                perms.push(Perm { id: "dossiers", state });
            }
        }
        Some(perms)
    }

    /// Windows asks nothing itself here: its page, then the window checks.
    pub fn ask(_: &Path, id: &str) -> Result<bool, String> {
        match id {
            "micro" => repair::open_settings("privacy_microphone").map(|()| false),
            "dossiers" => repair::open_settings("privacy_files").map(|()| false),
            _ => Err(UNKNOWN_PERM.into()),
        }
    }

    fn reg(key: &str) -> Option<String> {
        let out = sys::run(&sys::system32("reg.exe"), &["query", key, "/v", "Value"], hermes::TIMEOUT);
        out.ok().then(|| parse_reg(&out.stdout)).flatten()
    }
}

/// Linux desktops ask nothing of the kind.
#[cfg(target_os = "linux")]
mod platform {
    use std::path::Path;

    use super::{Perm, UNKNOWN_PERM};

    pub fn status(_: &Path, _: Option<&str>) -> Option<Vec<Perm>> {
        None
    }

    pub fn ask(_: &Path, _: &str) -> Result<bool, String> {
        Err(UNKNOWN_PERM.into())
    }
}

// ── What the checks print, read on every system (the tests run them all) ──────────────────

/// The microphone, as the Hermes app's check says it: never asked (macOS's window can ask),
/// refused (only its page takes that back).
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Micro {
    Ok,
    Never,
    Refused,
    Unknown,
}

#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
impl Micro {
    fn state(self) -> State {
        match self {
            Micro::Ok => State::Ok,
            Micro::Never | Micro::Refused => State::Missing,
            Micro::Unknown => State::Unknown,
        }
    }
}

/// What the Hermes app's check saw (hermes_access.js).
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
#[derive(Debug, PartialEq, Eq)]
struct Probe {
    disque: State,
    micro: Micro,
}

#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn parse_probe(json: &str) -> Option<Probe> {
    let v: Value = serde_json::from_str(json.trim()).ok()?;
    let word = |k: &str| v.get(k).and_then(Value::as_str).unwrap_or_default();
    let disque = match word("disque") {
        "ok" => State::Ok,
        "non" => State::Missing,
        _ => State::Unknown,
    };
    let micro = match word("micro") {
        "ok" => Micro::Ok,
        "jamais" => Micro::Never,
        "non" => Micro::Refused,
        _ => Micro::Unknown,
    };
    Some(Probe { disque, micro })
}

/// `hermes computer-use permissions status --json`: both seen given, one seen missing, or
/// CuaDriver not installed yet (Hermes installs it when asked). Null when no CuaDriver runs to
/// look: the app cannot tell.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn parse_screen(json: &str) -> State {
    let Ok(v) = serde_json::from_str::<Value>(json.trim()) else { return State::Unknown };
    let seen = |k: &str| v.get(k).and_then(Value::as_bool);
    match (seen("accessibility"), seen("screen_recording")) {
        (Some(true), Some(true)) => State::Ok,
        (Some(false), _) | (_, Some(false)) => State::Missing,
        _ if seen("installed") == Some(false) => State::Missing,
        _ => State::Unknown,
    }
}

/// Electron's fuses follow their sentinel, a version and a count: RunAsNode is the first ('1'
/// on). Read by chunks: the framework weighs a few hundred megabytes.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn first_fuse(mut r: impl std::io::Read) -> Option<u8> {
    const SENTINEL: &[u8] = b"dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX";
    let (mut seen, mut chunk) = (Vec::new(), vec![0; 1 << 20]);
    loop {
        let n = r.read(&mut chunk).ok()?;
        seen.extend_from_slice(&chunk[..n]);
        if let Some(at) = seen.windows(SENTINEL.len()).position(|w| w == SENTINEL) {
            if let Some(&fuse) = seen.get(at + SENTINEL.len() + 2) {
                return Some(fuse);
            }
        } else {
            // Only what could still start the sentinel stays.
            seen.drain(..seen.len().saturating_sub(SENTINEL.len()));
        }
        if n == 0 {
            return None;
        }
    }
}

/// `reg query <key> /v Value`: Allow or Deny.
#[cfg_attr(not(windows), allow(dead_code))]
fn parse_reg(out: &str) -> Option<String> {
    out.lines().find_map(|l| {
        let mut w = l.split_whitespace();
        (w.next()? == "Value" && w.next()? == "REG_SZ").then(|| w.next().map(str::to_string))?
    })
}

/// The microphone for every app, then for desktop apps: one refused is enough to block Hermes.
#[cfg_attr(not(windows), allow(dead_code))]
fn mic_state(apps: Option<&str>, desktop: Option<&str>) -> State {
    match (apps, desktop) {
        (Some("Deny"), _) | (_, Some("Deny")) => State::Missing,
        (Some("Allow"), Some("Allow")) => State::Ok,
        _ => State::Unknown,
    }
}

/// Controlled folder access on (1; 2 only audits, 0 is off: nothing to ask, None), then the
/// apps it lets write: is Hermes one of them?
#[cfg_attr(not(windows), allow(dead_code))]
fn guard_state(out: &str, hermes: &str) -> Option<State> {
    let mut lines = out.lines().map(str::trim).filter(|l| !l.is_empty());
    if lines.next()? != "1" {
        return None;
    }
    let hermes = hermes.to_lowercase();
    Some(if lines.any(|l| l.to_lowercase() == hermes) { State::Ok } else { State::Missing })
}

#[cfg(test)]
mod tests {
    #[cfg(target_os = "macos")]
    use std::path::Path;

    use super::*;

    /// Hermes's own status (0.22.3, CuaDriver 0.21.0, exit 1): with no CuaDriver running it
    /// cannot read the permissions, and the app never takes that for a yes.
    #[test]
    fn the_screen_is_given_only_when_hermes_sees_both() {
        let status = include_str!("../tests/fixtures/hermes-autorisations/permissions-status.json");
        assert_eq!(parse_screen(status), State::Unknown);
        let given = status.replace("\"accessibility\": null", "\"accessibility\": true").replace("\"screen_recording\": null", "\"screen_recording\": true");
        assert_eq!(parse_screen(&given), State::Ok);
        assert_eq!(parse_screen(&given.replace("\"screen_recording\": true", "\"screen_recording\": false")), State::Missing);
        assert_eq!(parse_screen(r#"{"accessibility": true, "screen_recording": false, "installed": true}"#), State::Missing);
        assert_eq!(parse_screen(r#"{"accessibility": false, "screen_recording": null, "installed": true}"#), State::Missing);
        assert_eq!(parse_screen(r#"{"accessibility": null, "screen_recording": null, "installed": false}"#), State::Missing);
        assert_eq!(parse_screen(r#"{"accessibility": true, "screen_recording": null, "installed": true}"#), State::Unknown);
        assert_eq!(parse_screen("Error: computer use is not available"), State::Unknown);
        assert_eq!(parse_screen(""), State::Unknown);
    }

    #[test]
    fn the_check_says_what_macos_answered_for_hermes() {
        let p = parse_probe(r#"{"disque":"ok","micro":"jamais"}"#).unwrap();
        assert_eq!(p, Probe { disque: State::Ok, micro: Micro::Never });
        assert_eq!(p.micro.state(), State::Missing);
        let p = parse_probe("{\"disque\":\"non\",\"micro\":\"ok\"}\n").unwrap();
        assert_eq!((p.disque, p.micro.state()), (State::Missing, State::Ok));
        let p = parse_probe(r#"{"disque":"inconnu","micro":"non"}"#).unwrap();
        assert_eq!((p.disque, p.micro), (State::Unknown, Micro::Refused));
        // Anything else is not a yes.
        let p = parse_probe(r#"{"disque":true,"micro":"OK"}"#).unwrap();
        assert_eq!((p.disque, p.micro.state()), (State::Unknown, State::Unknown));
        assert_eq!(parse_probe(""), None);
        assert_eq!(parse_probe("Hermes"), None);
    }

    #[test]
    fn the_run_as_node_fuse_is_read_across_chunks() {
        let sentinel = b"dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX";
        let framework = |pad: usize, fuses: &[u8]| {
            let mut b = vec![b'x'; pad];
            b.extend_from_slice(sentinel);
            b.extend_from_slice(&[1, fuses.len() as u8]);
            b.extend_from_slice(fuses);
            b.extend_from_slice(&[b'y'; 64]);
            b
        };
        assert_eq!(first_fuse(&framework(10, b"1011")[..]), Some(b'1'));
        assert_eq!(first_fuse(&framework(10, b"0111")[..]), Some(b'0'));
        // The sentinel, then the fuse, astride the 1 MiB chunks.
        for pad in [(1 << 20) - 20, (1 << 20) - 33, (1 << 20) - 34, (1 << 20) - 1, (2 << 20) + 5] {
            assert_eq!(first_fuse(&framework(pad, b"1")[..]), Some(b'1'), "{pad}");
        }
        // Cut right after the sentinel, or no sentinel: no fuse, so no check.
        let mut cut = vec![b'x'; 100];
        cut.extend_from_slice(sentinel);
        assert_eq!(first_fuse(&cut[..]), None);
        assert_eq!(first_fuse(&[b'x'; 5000][..]), None);
    }

    #[test]
    fn windows_reads_its_microphone_and_its_folder_guard() {
        let reg = "\r\nHKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\ConsentStore\\microphone\r\n    Value    REG_SZ    Allow\r\n\r\n";
        assert_eq!(parse_reg(reg).as_deref(), Some("Allow"));
        assert_eq!(parse_reg("ERROR: The system was unable to find the specified registry key or value."), None);
        assert_eq!(mic_state(Some("Allow"), Some("Allow")), State::Ok);
        assert_eq!(mic_state(Some("Allow"), Some("Deny")), State::Missing);
        assert_eq!(mic_state(Some("Deny"), None), State::Missing);
        assert_eq!(mic_state(Some("Allow"), None), State::Unknown);
        let hermes = r"C:\Users\someone\AppData\Local\hermes\hermes.exe";
        assert_eq!(guard_state("0\r\n", hermes), None);
        assert_eq!(guard_state("2\r\n", hermes), None);
        assert_eq!(guard_state("", hermes), None);
        assert_eq!(guard_state("1\r\n", hermes), Some(State::Missing));
        assert_eq!(guard_state("1\r\nC:\\Tools\\other.exe\r\n", hermes), Some(State::Missing));
        assert_eq!(guard_state("1\r\nC:\\USERS\\SOMEONE\\AppData\\Local\\hermes\\hermes.exe\r\n", hermes), Some(State::Ok));
    }

    #[test]
    fn the_permissions_go_to_the_window_as_it_reads_them() {
        let p = Perm { id: "ecran", state: State::Unknown };
        assert_eq!(serde_json::to_string(&p).unwrap(), r#"{"id":"ecran","state":"unknown"}"#);
        assert_eq!(serde_json::to_string(&State::Missing).unwrap(), r#""missing""#);
        let source = include_str!("hermes_access.rs");
        assert!(!source.contains(concat!("tccutil", " reset")), "never a permission taken back");
        assert!(!source.contains(concat!("TCC", ".db")), "never TCC's database");
        assert!(!source.contains(concat!(" de ", "Hermes")), "d'Hermes");
        assert!(!source.contains(concat!("Herm", "\u{e8}s")), "Hermes, not Herm\u{e8}s");
        let check = include_str!("hermes_access.js");
        assert!(!check.contains("tccutil") && !check.contains(concat!("TCC", ".db")), "the check only lists");
        assert!(!check.contains("writeFile") && !check.contains("unlink") && !check.contains("rmSync"), "the check writes nothing");
    }

    /// The check runs as the Hermes app, as Node, hidden and awaited, from a private folder that
    /// is gone afterwards; what it prints is the answer. Its fuse off: never started.
    #[cfg(target_os = "macos")]
    #[test]
    fn the_check_runs_as_hermes_in_node_and_never_with_its_fuse_off() {
        use std::os::unix::fs::PermissionsExt;
        let tmp = tempfile::tempdir().unwrap();
        let root = std::fs::canonicalize(tmp.path()).unwrap();
        let app = root.join("Hermes.app");
        let lib = app.join("Contents/Frameworks/Electron Framework.framework");
        std::fs::create_dir_all(&lib).unwrap();
        let fuses = |on: &[u8]| {
            let mut b = b"xx dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX".to_vec();
            b.extend_from_slice(&[1, 2]);
            b.extend_from_slice(on);
            std::fs::write(lib.join("Electron Framework"), b).unwrap();
        };
        let argv = root.join("argv");
        // `open`: notes its arguments and the script it was given, answers on --stdout.
        let open = root.join("open");
        let script = format!(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > '{a}'\nout=''\nwhile [ $# -gt 0 ]; do\n  case \"$1\" in --stdout) out=\"$2\";; --args) cp \"$2\" '{a}.js'; ls -ld \"$(dirname \"$2\")\" > '{a}.dir';; esac\n  shift\ndone\nprintf '{{\"disque\":\"non\",\"micro\":\"jamais\"}}' > \"$out\"\n",
            a = argv.display()
        );
        std::fs::write(&open, script).unwrap();
        std::fs::set_permissions(&open, std::fs::Permissions::from_mode(0o755)).unwrap();

        fuses(b"10");
        let seen = platform::check_with(&open, &app, false).expect("an answer");
        assert_eq!(seen, Probe { disque: State::Missing, micro: Micro::Never });
        let args: Vec<String> = std::fs::read_to_string(&argv).unwrap().lines().map(String::from).collect();
        assert_eq!(&args[..6], ["-n", "-g", "-j", "-W", "--env", "ELECTRON_RUN_AS_NODE=1"]);
        let at = |flag: &str| args.iter().position(|a| a == flag).unwrap();
        assert_eq!(args[at("-a") + 1], app.to_str().unwrap());
        assert_eq!(args[at("--stderr") + 1], "/dev/null");
        let script = &args[at("--args") + 1];
        assert!(script.ends_with("/check.js"), "{script}");
        assert_eq!(args.last().unwrap(), script, "reading only: no « ask »");
        assert_eq!(std::fs::read_to_string(root.join("argv.js")).unwrap(), include_str!("hermes_access.js"));
        assert!(std::fs::read_to_string(root.join("argv.dir")).unwrap().starts_with("drwx------"), "private");
        assert!(!Path::new(script).exists(), "gone afterwards");

        platform::check_with(&open, &app, true).expect("an answer");
        assert_eq!(std::fs::read_to_string(&argv).unwrap().lines().last(), Some("ask"));

        std::fs::remove_file(&argv).unwrap();
        fuses(b"01");
        assert_eq!(platform::check_with(&open, &app, false), None);
        assert!(!argv.exists(), "the app is not started: it would open its window");
    }

    /// On this Mac, for real (ignored: run it by name, with `--ignored`): the check run by the
    /// Hermes app the user opens, reading only, and what macOS answered for it.
    #[cfg(target_os = "macos")]
    #[test]
    #[ignore = "starts the real Hermes app as Node, reading only"]
    fn the_real_hermes_app_answers_for_itself() {
        let app = platform::desktop_app(&crate::sys::home()).expect("the Hermes app");
        let started = std::time::Instant::now();
        let seen = platform::check_with(Path::new("/usr/bin/open"), &app, false).expect("Hermes answered");
        eprintln!("{seen:?} in {:?}", started.elapsed());
        assert_ne!(seen.disque, State::Unknown);
        assert_ne!(seen.micro, Micro::Unknown);
    }

    /// Hermes's installer carries the same name in /Applications: only the app's identity counts.
    #[cfg(target_os = "macos")]
    #[test]
    fn only_the_hermes_app_is_checked_never_its_installer() {
        let tmp = tempfile::tempdir().unwrap();
        let home = std::fs::canonicalize(tmp.path()).unwrap();
        let bundle = |app: &Path, id: &str| {
            std::fs::create_dir_all(app.join("Contents")).unwrap();
            let plist = format!("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n<plist version=\"1.0\"><dict><key>CFBundleIdentifier</key><string>{id}</string></dict></plist>\n");
            std::fs::write(app.join("Contents/Info.plist"), plist).unwrap();
        };
        let mine = home.join("Applications/Hermes.app");
        let built = home.join(".hermes/hermes-agent/apps/desktop/release/mac-arm64/Hermes.app");
        bundle(&mine, "com.nousresearch.hermes.setup");
        let found = platform::desktop_app(&home);
        assert!(found.as_deref().is_none_or(|f| f.starts_with("/Applications")), "{found:?}");
        bundle(&built, "com.nousresearch.hermes");
        let found = platform::desktop_app(&home).unwrap();
        assert!(found == built || found.starts_with("/Applications"), "{found:?}");
        bundle(&mine, "com.nousresearch.hermes");
        let found = platform::desktop_app(&home).unwrap();
        assert!(found == mine || found.starts_with("/Applications"), "{found:?}");
    }
}
