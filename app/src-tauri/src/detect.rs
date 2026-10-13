//! What is installed: the one answer the diagnosis and the install steps share.
//!
//! The probes run a program or look at the disk; the rules behind them take a home
//! folder or a program's output, so tests drive them with fakes.

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
pub fn clt_dir() -> Option<PathBuf> {
    let dir = PathBuf::from(sys::output_text(Path::new("/usr/bin/xcode-select"), &["-p"], TIMEOUT)?);
    has_clt(&dir).then_some(dir)
}

/// Without the tools, /usr/bin/git and /usr/bin/python3 are decoys that open Apple's
/// install window. So we look for the real git inside the selected folder, and never run it.
fn has_clt(dir: &Path) -> bool {
    dir.is_absolute() && dir.join("usr/bin/git").is_file()
}

// ── Bun ─────────────────────────────────────────────────────────────────────

pub fn bun(home: &Path) -> Found {
    version_of(&home.join(".bun/bin/bun"), TIMEOUT)
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
pub fn obsidian_app(home: &Path) -> Option<PathBuf> {
    app_in(&[PathBuf::from("/Applications"), home.join("Applications")], "Obsidian")
}

fn app_in(dirs: &[PathBuf], name: &str) -> Option<PathBuf> {
    dirs.iter()
        .map(|d| d.join(format!("{name}.app")))
        .find(|app| sys::is_executable(&app.join("Contents/MacOS").join(name)))
}

/// Installed once the app is there, even if its version cannot be read.
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

// ── The brain ───────────────────────────────────────────────────────────────

/// What install.sh leaves behind once it went through.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Brain {
    /// ~/Documents/Brain
    pub vault: bool,
    /// Claude Code's hooks, in ~/.claude/settings.json.
    pub hooks: bool,
    pub skills: bool,
    /// gbrain's database.
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
        hooks: settings.contains("correction-detector.py"),
        skills: home.join(".claude/skills/brain-boost/SKILL.md").is_file(),
        memory: home.join(".gbrain/brain.pglite").exists(),
    }
}

/// The notes folder, once install.sh created it.
pub fn vault(home: &Path) -> Option<PathBuf> {
    let dir = home.join("Documents/Brain");
    dir.is_dir().then_some(dir)
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
        assert_eq!(vault(h), Some(h.join("Documents/Brain")));
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
}
