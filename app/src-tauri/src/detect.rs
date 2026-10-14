//! What is installed: the one answer the diagnosis and the install steps share.
//!
//! The probes run a program or look at the disk; the rules behind them take a home
//! folder or a program's output, so tests drive them with fakes.
//!
//! On Windows the answer is the one install.ps1 would give: it finds Python, Git,
//! Claude Code and Obsidian where its Find-* functions look, and leaves the brain where
//! this file looks for it.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::Value;

use crate::sys;

pub type Version = (u32, u32, u32);

/// gbrain refuses older versions.
pub const MIN_BUN: Version = (1, 4, 0);
const TIMEOUT: Duration = Duration::from_secs(10);
/// Claude Code is a large program: its first start after the install can be slow.
const CLAUDE_TIMEOUT: Duration = Duration::from_secs(20);

/// What we learned about an installed program.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Found {
    Absent,
    /// On disk, but its version could not be read.
    NoVersion,
    /// Raw text holding the version, e.g. "2.1.296 (Claude Code)".
    Version(String),
}

impl Found {
    pub fn version(&self) -> Option<Version> {
        match self {
            Found::Version(text) => parse_version(text),
            _ => None,
        }
    }

    /// It ran and said something.
    pub fn answers(&self) -> bool {
        matches!(self, Found::Version(_))
    }

    /// "Bun 1.4.2", or just "Bun" when the version cannot be read.
    pub fn label(&self, name: &str) -> String {
        self.version().map_or_else(|| name.to_string(), |v| format!("{name} {}", fmt_version(v)))
    }
}

// ── Apple command line tools ────────────────────────────────────────────────

/// The tools folder, when it is really there.
#[cfg(not(windows))]
pub fn clt_dir() -> Option<PathBuf> {
    let dir = PathBuf::from(sys::output_text(Path::new("/usr/bin/xcode-select"), &["-p"], TIMEOUT)?);
    has_clt(&dir).then_some(dir)
}

/// Without the tools, /usr/bin/git and /usr/bin/python3 are decoys that open Apple's
/// install window. So we look for the real git inside the selected folder, and never run it.
#[cfg(not(windows))]
fn has_clt(dir: &Path) -> bool {
    dir.is_absolute() && dir.join("usr/bin/git").is_file()
}

// ── Python and Git (Windows: install.ps1 installs them) ─────────────────────

/// Python, as install.ps1's Find-Python finds it.
#[cfg(windows)]
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Python {
    /// What it says sys.executable is.
    pub exe: PathBuf,
    pub version: Version,
}

/// install.ps1 refuses older ones.
#[cfg(windows)]
const MIN_PYTHON: Version = (3, 8, 0);

/// The first Python that really runs code: python.exe and python3.exe on the PATH,
/// then where its installers put it, then the py launcher. The Microsoft Store's
/// python.exe is on the PATH of every Windows but runs nothing (exit 9009): asked
/// to run code, it fails, and is passed over like install.ps1 passes it over.
#[cfg(windows)]
pub fn python() -> Option<Python> {
    let mut found: Vec<PathBuf> = Vec::new();
    for name in ["python.exe", "python3.exe"] {
        // An app alias (the Store's) is a link that cannot be read: it exists all the same.
        found.extend(
            std::env::split_paths(&sys::tool_path())
                .map(|d| d.join(name))
                .filter(|p| fs::symlink_metadata(p).is_ok()),
        );
    }
    let roots = [
        std::env::var_os("LOCALAPPDATA").map(|l| Path::new(&l).join("Programs").join("Python")),
        std::env::var_os("ProgramFiles").map(PathBuf::from),
    ];
    for root in roots.into_iter().flatten() {
        found.extend(python_folders(&root).into_iter().map(|d| d.join("python.exe")));
    }
    found
        .iter()
        .find_map(|exe| python_at(exe, &[]))
        .or_else(|| sys::find_tool("py").and_then(|py| python_at(&py, &["-3"])))
}

/// Python3*\ folders under `root`, in the order Get-ChildItem lists them for install.ps1.
#[cfg(windows)]
fn python_folders(root: &Path) -> Vec<PathBuf> {
    let Ok(entries) = fs::read_dir(root) else { return Vec::new() };
    let mut dirs: Vec<(String, PathBuf)> = entries
        .filter_map(Result::ok)
        .map(|e| (e.file_name().to_string_lossy().to_lowercase(), e.path()))
        .filter(|(name, _)| name.starts_with("python3"))
        .collect();
    dirs.sort();
    dirs.into_iter().map(|(_, p)| p).collect()
}

/// The code install.ps1's Test-Python runs: version, then sys.executable in hex (UTF-8),
/// which no code page can garble.
#[cfg(windows)]
const PYTHON_PROBE: &str = "import sys; print('%d.%d.%d' % tuple(sys.version_info[:3])); print(sys.executable.encode('utf-8').hex())";

#[cfg(windows)]
fn python_at(exe: &Path, pre: &[&str]) -> Option<Python> {
    let mut args = pre.to_vec();
    args.extend(["-I", "-c", PYTHON_PROBE]);
    let out = sys::run(exe, &args, TIMEOUT);
    if !out.ok() {
        return None;
    }
    let found = python_answer(&out.stdout)?;
    found.exe.is_file().then_some(found)
}

/// The probe's two lines, if they show a Python recent enough.
#[cfg(windows)]
fn python_answer(stdout: &str) -> Option<Python> {
    let mut lines = stdout.lines().map(str::trim).filter(|l| !l.is_empty());
    let version = parse_version(lines.next()?).filter(|v| *v >= MIN_PYTHON)?;
    let hex = lines.next()?;
    if hex.len() % 2 != 0 || !hex.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    let bytes: Option<Vec<u8>> = (0..hex.len()).step_by(2).map(|i| u8::from_str_radix(&hex[i..i + 2], 16).ok()).collect();
    let exe = PathBuf::from(String::from_utf8(bytes?).ok()?);
    exe.is_absolute().then_some(Python { exe, version })
}

/// Git, as install.ps1's Find-Git finds it: on the PATH, or in Git's own folders
/// (both are on sys::tool_path()).
#[cfg(windows)]
pub fn git() -> Found {
    sys::find_tool("git").map_or(Found::Absent, |bin| version_of(&bin, TIMEOUT))
}

// ── Bun ─────────────────────────────────────────────────────────────────────

pub fn bun(home: &Path) -> Found {
    version_of(&home.join(".bun").join("bin").join(sys::exe("bun")), TIMEOUT)
}

/// Recent enough for gbrain.
pub fn bun_ok(found: &Found) -> bool {
    found.version().is_some_and(|v| v >= MIN_BUN)
}

// ── Claude Code ─────────────────────────────────────────────────────────────

/// Also looks in ~/.local/bin, where the official installer puts it (see sys::tool_path).
pub fn claude_bin() -> Option<PathBuf> {
    sys::find_tool("claude")
}

/// Installed when it answers `--version`.
pub fn claude() -> Found {
    claude_bin().map_or(Found::Absent, |bin| version_of(&bin, CLAUDE_TIMEOUT))
}

pub fn claude_logged_in() -> bool {
    claude_auth_method().is_some_and(|m| m != "none")
}

/// How Claude Code is logged in: "claude.ai" for a Pro or Max subscription, "none"
/// for nobody. Only this field is read: the rest of `claude auth status` (email,
/// organization) never leaves this function.
pub fn claude_auth_method() -> Option<String> {
    let claude = claude_bin()?;
    let out = sys::run(&claude, &["auth", "status"], TIMEOUT);
    if out.ok() {
        auth_method(&out.stdout)
    } else {
        None
    }
}

/// `claude auth status` prints JSON, sometimes after a line of notes.
fn auth_method(status: &str) -> Option<String> {
    let json = status.find('{').map_or("", |i| &status[i..]);
    let Some(Ok(v)) = serde_json::Deserializer::from_str(json).into_iter::<Value>().next() else { return None };
    v.get("authMethod").and_then(Value::as_str).map(String::from)
}

// ── Obsidian ────────────────────────────────────────────────────────────────

/// In /Applications or ~/Applications, with its program inside: an empty or
/// half-copied Obsidian.app folder is not an installed Obsidian.
#[cfg(not(windows))]
pub fn obsidian_app(home: &Path) -> Option<PathBuf> {
    app_in(&[PathBuf::from("/Applications"), home.join("Applications")], "Obsidian")
}

#[cfg(not(windows))]
fn app_in(dirs: &[PathBuf], name: &str) -> Option<PathBuf> {
    dirs.iter()
        .map(|d| d.join(format!("{name}.app")))
        .find(|app| sys::is_executable(&app.join("Contents/MacOS").join(name)))
}

/// Installed once the app is there, even if its version cannot be read.
#[cfg(not(windows))]
pub fn obsidian(home: &Path) -> Found {
    let Some(app) = obsidian_app(home) else { return Found::Absent };
    let plist = app.join("Contents/Info.plist");
    let plist = plist.to_string_lossy();
    sys::output_text(
        Path::new("/usr/bin/plutil"),
        &["-extract", "CFBundleShortVersionString", "raw", "-o", "-", &plist],
        TIMEOUT,
    )
    .map_or(Found::NoVersion, Found::Version)
}

/// Obsidian.exe where install.ps1's Find-Obsidian looks: winget's per-user install,
/// the older per-user one, then the one for all users.
#[cfg(windows)]
pub fn obsidian_exe(home: &Path) -> Option<PathBuf> {
    let local = std::env::var_os("LOCALAPPDATA").map_or_else(|| home.join("AppData").join("Local"), PathBuf::from);
    let mut places = vec![local.join(r"Programs\Obsidian\Obsidian.exe"), local.join(r"Obsidian\Obsidian.exe")];
    if let Some(pf) = std::env::var_os("ProgramFiles") {
        places.push(Path::new(&pf).join(r"Obsidian\Obsidian.exe"));
    }
    places.into_iter().find(|p| sys::is_executable(p))
}

/// Installed once Obsidian.exe is there. Its version sits in the file's resources,
/// which only PowerShell would read: it is not worth a PowerShell per diagnosis.
#[cfg(windows)]
pub fn obsidian(home: &Path) -> Found {
    obsidian_exe(home).map_or(Found::Absent, |_| Found::NoVersion)
}

// ── The brain ───────────────────────────────────────────────────────────────

/// What install.sh (install.ps1 on Windows) leaves behind once it went through.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Brain {
    /// ~/Documents/Brain
    pub vault: bool,
    /// Claude Code's hooks, in ~/.claude/settings.json.
    pub hooks: bool,
    pub skills: bool,
    /// gbrain's database. On Windows also the keyword index install.ps1 falls back on
    /// when gbrain does not install: the brain finds notes by keywords then.
    pub memory: bool,
}

impl Brain {
    pub fn complete(self) -> bool {
        self.vault && self.hooks && self.skills && self.memory
    }
}

pub fn brain(home: &Path) -> Brain {
    let settings = fs::read_to_string(home.join(".claude/settings.json")).unwrap_or_default();
    Brain {
        vault: vault(home).is_some(),
        // The shell form on a Mac, the exec form (python.exe + the hook in "args") on Windows.
        hooks: settings.contains("correction-detector.py"),
        skills: home.join(".claude/skills/brain-boost/SKILL.md").is_file(),
        memory: home.join(".gbrain/brain.pglite").exists() || keyword_index(home),
    }
}

#[cfg(not(windows))]
fn keyword_index(_: &Path) -> bool {
    false
}

/// install.ps1 writes which search answers (gbrain or brain_search) once one is ready.
#[cfg(windows)]
fn keyword_index(home: &Path) -> bool {
    home.join(".brain-search").join("engine").is_file()
}

/// The notes folder, once install.sh created it.
pub fn vault(home: &Path) -> Option<PathBuf> {
    let dir = brain_dir(home);
    dir.is_dir().then_some(dir)
}

#[cfg(not(windows))]
fn brain_dir(home: &Path) -> PathBuf {
    home.join("Documents/Brain")
}

/// install.ps1's own default: BRAIN_DIR, which it saves for the user, else Documents\Brain
/// in the profile (never the Documents OneDrive may have moved).
#[cfg(windows)]
fn brain_dir(home: &Path) -> PathBuf {
    std::env::var_os("BRAIN_DIR")
        .filter(|d| !d.is_empty())
        .map_or_else(|| home.join("Documents").join("Brain"), PathBuf::from)
}

/// Set once search by meaning works ("voyage:voyage-4"). The file also holds API
/// keys: only this one field is read out of it.
pub fn embedding_model(home: &Path) -> Option<String> {
    let text = fs::read_to_string(home.join(".gbrain/config.json")).ok()?;
    let config: Value = serde_json::from_str(&text).ok()?;
    let model = config.get("embedding_model")?.as_str()?.trim();
    (!model.is_empty()).then(|| model.to_string())
}

// ── Versions ────────────────────────────────────────────────────────────────

fn version_of(bin: &Path, timeout: Duration) -> Found {
    if !sys::is_executable(bin) {
        return Found::Absent;
    }
    sys::output_text(bin, &["--version"], timeout).map_or(Found::NoVersion, Found::Version)
}

/// First number in the text: "26.3.1", "1.4.2-canary.3", "2.1.296 (Claude Code)".
pub fn parse_version(text: &str) -> Option<Version> {
    let word = text
        .split_whitespace()
        .map(|w| w.trim_start_matches('v'))
        .find(|w| w.starts_with(|c: char| c.is_ascii_digit()))?;
    let end = word
        .find(|c: char| !c.is_ascii_digit() && c != '.')
        .unwrap_or(word.len());
    let mut parts = word[..end].split('.').filter(|p| !p.is_empty()).map(str::parse::<u32>);
    let major = parts.next()?.ok()?;
    let minor = parts.next().unwrap_or(Ok(0)).ok()?;
    let patch = parts.next().unwrap_or(Ok(0)).ok()?;
    Some((major, minor, patch))
}

pub fn fmt_version((major, minor, patch): Version) -> String {
    format!("{major}.{minor}.{patch}")
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;

    /// Everything install.sh leaves, with search by meaning on.
    pub(crate) fn fake_brain(home: &Path) {
        fs::create_dir_all(home.join("Documents/Brain")).unwrap();
        fs::create_dir_all(home.join(".claude/skills/brain-boost")).unwrap();
        fs::write(home.join(".claude/skills/brain-boost/SKILL.md"), "# skill").unwrap();
        let hook = r#"{"type":"command","command":"/Users/me/.claude/hooks/brain/correction-detector.py"}"#;
        fs::write(
            home.join(".claude/settings.json"),
            format!(r#"{{"hooks":{{"UserPromptSubmit":[{{"hooks":[{hook}]}}]}}}}"#),
        )
        .unwrap();
        fs::create_dir_all(home.join(".gbrain/brain.pglite")).unwrap();
        let config = r#"{"embedding_model":"voyage:voyage-4","voyage_api_key":"pa-SECRET"}"#;
        fs::write(home.join(".gbrain/config.json"), config).unwrap();
    }

    #[cfg(unix)]
    fn write_exec(path: &Path, content: &str) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, content).unwrap();
        fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
    }

    #[test]
    fn reads_versions_as_the_tools_print_them() {
        assert_eq!(parse_version("26.3.1\n"), Some((26, 3, 1)));
        assert_eq!(parse_version("13.0"), Some((13, 0, 0)));
        assert_eq!(parse_version("1.4.2\n"), Some((1, 4, 2)));
        assert_eq!(parse_version("1.4.2-canary.20+abc"), Some((1, 4, 2)));
        assert_eq!(parse_version("2.1.296 (Claude Code)"), Some((2, 1, 296)));
        assert_eq!(parse_version("v1.3.10"), Some((1, 3, 10)));
        assert_eq!(parse_version("v1.10"), Some((1, 10, 0)));
        assert_eq!(parse_version(""), None);
        assert_eq!(parse_version("bun"), None);
        assert_eq!(parse_version("command not found"), None);
    }

    #[test]
    fn bun_must_be_1_4_or_later() {
        for ok in ["1.4.0", "1.4.2-canary.3\n", "1.10.1", "2.0.0"] {
            assert!(bun_ok(&Found::Version(ok.into())), "{ok}");
        }
        for old in ["1.3.9", "", "garbage"] {
            assert!(!bun_ok(&Found::Version(old.into())), "{old}");
        }
        assert!(!bun_ok(&Found::Absent) && !bun_ok(&Found::NoVersion));
    }

    #[test]
    fn labels_show_a_clean_version() {
        assert_eq!(Found::Version("2.1.296 (Claude Code)".into()).label("Claude Code"), "Claude Code 2.1.296");
        assert_eq!(Found::Version("<b>".into()).label("Obsidian"), "Obsidian");
        assert_eq!(Found::NoVersion.label("Obsidian"), "Obsidian");
        assert!(Found::Version("<b>".into()).answers());
        assert!(!Found::NoVersion.answers() && !Found::Absent.answers());
    }

    #[cfg(unix)]
    #[test]
    fn a_program_counts_once_it_tells_its_version() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        assert_eq!(bun(home), Found::Absent);
        let bin = home.join(".bun/bin/bun");
        write_exec(&bin, "#!/bin/sh\necho 1.4.2\n");
        assert_eq!(bun(home), Found::Version("1.4.2".into()));
        write_exec(&bin, "#!/bin/sh\nexit 1\n");
        assert_eq!(bun(home), Found::NoVersion);
        fs::set_permissions(&bin, fs::Permissions::from_mode(0o644)).unwrap();
        assert_eq!(bun(home), Found::Absent);
    }

    #[cfg(not(windows))]
    #[test]
    fn clt_needs_the_real_git_in_the_selected_folder() {
        let tmp = tempfile::tempdir().unwrap();
        assert!(!has_clt(&tmp.path().join("gone")));
        assert!(!has_clt(tmp.path()));
        fs::create_dir_all(tmp.path().join("usr/bin")).unwrap();
        fs::write(tmp.path().join("usr/bin/git"), "").unwrap();
        assert!(has_clt(tmp.path()));
        assert!(!has_clt(Path::new("")));
    }

    #[cfg(unix)]
    #[test]
    fn obsidian_counts_only_with_its_program_inside() {
        let tmp = tempfile::tempdir().unwrap();
        let (system, mine) = (tmp.path().join("system"), tmp.path().join("mine"));
        let dirs = [system.clone(), mine.clone()];
        assert_eq!(app_in(&dirs, "Obsidian"), None);
        fs::create_dir_all(system.join("Obsidian.app/Contents/MacOS")).unwrap();
        assert_eq!(app_in(&dirs, "Obsidian"), None, "an empty bundle");
        let exe = mine.join("Obsidian.app/Contents/MacOS/Obsidian");
        write_exec(&exe, "");
        assert_eq!(app_in(&dirs, "Obsidian"), Some(mine.join("Obsidian.app")));
        fs::set_permissions(&exe, fs::Permissions::from_mode(0o644)).unwrap();
        assert_eq!(app_in(&dirs, "Obsidian"), None, "not executable");
        write_exec(&system.join("Obsidian.app/Contents/MacOS/Obsidian"), "");
        assert_eq!(app_in(&dirs, "Obsidian"), Some(system.join("Obsidian.app")));
    }

    #[test]
    fn auth_status_gives_only_the_method() {
        let logged = include_str!("../tests/fixtures/repair/auth-status-claude-ai.json");
        assert_eq!(auth_method(logged).as_deref(), Some("claude.ai"));
        let out = include_str!("../tests/fixtures/repair/auth-status-none.json");
        assert_eq!(auth_method(out).as_deref(), Some("none"));
        assert_eq!(auth_method("note: update available\n{\"authMethod\":\"api_key\"}\n").as_deref(), Some("api_key"));
        for nothing in [r#"{"loggedIn":true}"#, r#"{"authMethod":4}"#, "Not logged in", "", "{oops"] {
            assert_eq!(auth_method(nothing), None, "{nothing}");
        }
    }

    #[test]
    fn brain_needs_all_four_markers() {
        let tmp = tempfile::tempdir().unwrap();
        let h = tmp.path();
        let none = Brain { vault: false, hooks: false, skills: false, memory: false };
        assert_eq!(brain(h), none);
        assert!(!brain(h).complete());
        fs::create_dir_all(h.join("Documents")).unwrap();
        fs::write(h.join("Documents/Brain"), "a file, not the vault").unwrap();
        assert_eq!(vault(h), None);
        fs::remove_file(h.join("Documents/Brain")).unwrap();
        fake_brain(h);
        assert!(brain(h).complete());
        assert_eq!(vault(h), Some(brain_dir(h)));
        fs::write(h.join(".claude/settings.json"), r#"{"hooks":{}}"#).unwrap();
        assert_eq!(brain(h), Brain { vault: true, hooks: false, skills: true, memory: true });
        assert!(!brain(h).complete());
    }

    #[test]
    fn embedding_model_is_the_only_field_read() {
        let tmp = tempfile::tempdir().unwrap();
        let h = tmp.path();
        assert_eq!(embedding_model(h), None);
        fs::create_dir_all(h.join(".gbrain")).unwrap();
        for keywords_only in [
            r#"{"voyage_api_key":"pa-SECRET"}"#,
            r#"{"embedding_model":""}"#,
            r#"{"embedding_model":"  "}"#,
            r#"{"embedding_model":null}"#,
            r#"{"embedding_model":4}"#,
            "{oops",
        ] {
            fs::write(h.join(".gbrain/config.json"), keywords_only).unwrap();
            assert_eq!(embedding_model(h), None, "{keywords_only}");
        }
        fs::write(h.join(".gbrain/config.json"), r#"{"embedding_model":" voyage:voyage-4 ","voyage_api_key":"pa-SECRET"}"#)
            .unwrap();
        assert_eq!(embedding_model(h).as_deref(), Some("voyage:voyage-4"));
    }

    #[cfg(windows)]
    #[test]
    fn a_keyword_index_is_a_memory_on_windows() {
        let tmp = tempfile::tempdir().unwrap();
        let h = tmp.path();
        assert!(!brain(h).memory);
        fs::create_dir_all(h.join(".brain-search")).unwrap();
        fs::write(h.join(".brain-search/engine"), "brain_search\n").unwrap();
        assert!(brain(h).memory);
    }

    #[cfg(windows)]
    #[test]
    fn python_is_the_one_that_runs_code() {
        let exe = std::env::current_exe().unwrap();
        let hex = crate::config_guard::hex(exe.to_string_lossy().as_bytes());
        let found = python_answer(&format!("3.12.4\r\n{hex}\r\n")).unwrap();
        assert_eq!((found.exe, found.version), (exe.clone(), (3, 12, 4)));
        for refused in [
            "Python was not found; run without arguments to install from the Microsoft Store\r\n".to_string(),
            format!("3.7.9\n{hex}\n"),
            format!("2.7.18\n{hex}\n"),
            "3.12.4\n".into(),
            "3.12.4\nzz\n".into(),
            "3.12.4\n616263\n".into(),
            String::new(),
        ] {
            assert_eq!(python_answer(&refused), None, "{refused}");
        }
    }

    #[cfg(windows)]
    #[test]
    fn python_folders_in_listing_order() {
        let tmp = tempfile::tempdir().unwrap();
        for d in ["Python310", "Python312", "Python27", "python311", "Other"] {
            fs::create_dir_all(tmp.path().join(d)).unwrap();
        }
        let names: Vec<String> =
            python_folders(tmp.path()).iter().map(|p| p.file_name().unwrap().to_string_lossy().into_owned()).collect();
        assert_eq!(names, ["Python310", "python311", "Python312"]);
        assert!(python_folders(&tmp.path().join("missing")).is_empty());
    }
}
