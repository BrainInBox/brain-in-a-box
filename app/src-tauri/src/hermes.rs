//! Hermes, Nous Research's agent: the app installs it, connects it to the user's AI, links
//! it to the brain, to WhatsApp and Telegram, and walks the user through the computer's
//! permissions (docs/proposals/hermes.md).
//!
//! Every Hermes feature finds the same `hermes` command here, and what the app knows of it
//! (`probe`): read only, never a secret, never a request to an AI.

use std::path::PathBuf;
use std::time::Duration;

use serde_json::Value;

use crate::detect::{Found, Version};
use crate::sys;

/// Long enough for a `hermes` subcommand that only reads its config.
pub const TIMEOUT: Duration = Duration::from_secs(20);

/// The first Hermes whose plugin runs Claude on the user's subscription.
pub const MIN_HERMES: Version = (0, 21, 4);

/// The `hermes` command. The official installer puts it in ~/.local/bin, which is on
/// sys::tool_path on a Mac and on Linux.
pub fn bin() -> Option<PathBuf> {
    sys::find_tool("hermes")
}

/// What the app knows of Hermes.
#[derive(Debug, Clone, PartialEq)]
pub struct Facts {
    /// Hermes runs on this computer: every one but an Intel Mac (its installer is Apple Silicon only).
    pub supported: bool,
    /// The first line of `hermes --version`, e.g. "Hermes Agent v0.22.2 (2026.9.24) · stable".
    pub found: Found,
    /// On a Mac, Hermes.app is in Applications, where the user opens Hermes. Elsewhere always
    /// true: nothing installs an app there.
    pub app: bool,
    /// The provider of Hermes's model, e.g. "openai-codex"; None until one is chosen.
    pub provider: Option<String>,
    /// Once connected, Hermes's memory says where the brain is (hermes_brain.rs).
    pub brain: bool,
}

impl Default for Facts {
    fn default() -> Self {
        Facts { supported: true, found: Found::Absent, app: false, provider: None, brain: false }
    }
}

/// `hermes --version`, the provider of its model (`config get` masks credentials, and only
/// the provider's id is kept), then whether its memory says where the brain is. Hermes reads
/// its own $HERMES_HOME.
pub fn probe() -> Facts {
    let supported = supported();
    let app = has_app();
    let Some(bin) = bin() else { return Facts { supported, app, ..Facts::default() } };
    let found = version_of(&bin);
    let model = found.answers().then(|| sys::run(&bin, &["config", "get", "model", "--json"], TIMEOUT));
    let provider = model.filter(sys::Output::ok).and_then(|o| parse_provider(&o.stdout));
    let brain = provider.is_some() && crate::hermes_brain::linked(&bin, &sys::home());
    Facts { supported, found, app, provider, brain }
}

/// `hermes --version`, Absent without the command. Only the Mac's Hermes step asks it again.
#[cfg(target_os = "macos")]
pub fn found() -> Found {
    bin().map_or(Found::Absent, |bin| version_of(&bin))
}

fn version_of(bin: &std::path::Path) -> Found {
    let version = sys::run(bin, &["--version"], TIMEOUT);
    match version.stdout.lines().map(str::trim).find(|l| !l.is_empty()) {
        Some(line) if version.ok() => Found::Version(line.to_string()),
        _ => Found::NoVersion,
    }
}

/// Apple Silicon: under Rosetta `uname -m` says x86_64 there too, this flag is the hardware's.
#[cfg(target_os = "macos")]
pub fn supported() -> bool {
    let out = sys::run(std::path::Path::new("/usr/sbin/sysctl"), &["-n", "hw.optional.arm64"], Duration::from_secs(5));
    out.stdout.trim() == "1"
}

#[cfg(not(target_os = "macos"))]
pub fn supported() -> bool {
    true
}

/// Hermes.app, Hermes's official app, in /Applications (where its site's package goes) or in
/// ~/Applications (where the app puts it).
#[cfg(target_os = "macos")]
pub fn app() -> Option<PathBuf> {
    app_in(&[PathBuf::from("/Applications"), sys::home().join("Applications")])
}

/// The first `Hermes.app` in `dirs` that is a whole app, not an empty folder left behind.
#[cfg(target_os = "macos")]
pub(crate) fn app_in(dirs: &[PathBuf]) -> Option<PathBuf> {
    dirs.iter().map(|d| d.join("Hermes.app")).find(|a| a.join("Contents/Info.plist").is_file())
}

#[cfg(target_os = "macos")]
fn has_app() -> bool {
    app().is_some()
}

#[cfg(not(target_os = "macos"))]
fn has_app() -> bool {
    true
}

/// The provider in `hermes config get model --json`, e.g. {"default": "gpt-5.5", "provider":
/// "openai-codex"}. "auto" or nothing is no choice; an old config's bare model string is a
/// choice of the user's, whose provider Hermes works out ("custom").
pub fn parse_provider(json: &str) -> Option<String> {
    let provider = match serde_json::from_str::<Value>(json.trim()).ok()? {
        Value::Object(m) => m.get("provider")?.as_str()?.trim().to_string(),
        Value::String(s) if !s.trim().is_empty() => "custom".into(),
        _ => return None,
    };
    (!provider.is_empty() && provider != "auto").then_some(provider)
}

/// The provider, in the user's words.
pub fn provider_said(id: &str) -> &'static str {
    match id {
        "claude-subscription-directsdk-experimental" => "Il passe par ton abonnement Claude.",
        "anthropic" => "Il passe par Claude.",
        "openai-codex" => "Il passe par ton compte ChatGPT.",
        _ => "Il passe par l'IA que tu lui as choisie.",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detect::parse_version;
    use std::path::Path;

    /// What `hermes` printed on a Mac (Hermes 0.22.2), its paths made anonymous.
    fn fixture(name: &str) -> String {
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/hermes").join(name);
        std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()))
    }

    #[test]
    fn the_version_is_read_from_hermes_own_words() {
        let first = fixture("version.txt").lines().next().unwrap().to_string();
        let found = Found::Version(first);
        assert_eq!(found.version(), Some((0, 22, 2)));
        assert_eq!(found.label("Hermes"), "Hermes 0.22.2");
        assert!(found.version().unwrap() >= MIN_HERMES);
        assert!(parse_version("Hermes Agent v0.21.3 (2026.8.30) · stable").unwrap() < MIN_HERMES);
    }

    #[test]
    fn only_the_provider_is_kept_of_the_model() {
        assert_eq!(parse_provider(&fixture("model.json")).as_deref(), Some("anthropic"));
        for (json, provider) in [
            (r#"{"default": "gpt-5.5", "provider": "openai-codex"}"#, Some("openai-codex")),
            (
                r#"{"default": "claude-opus-5-5", "provider": "claude-subscription-directsdk-experimental"}"#,
                Some("claude-subscription-directsdk-experimental"),
            ),
            (r#""anthropic/claude-sonnet-4""#, Some("custom")),
            (r#"{"default": "x", "provider": "auto"}"#, None),
            (r#"{"provider": " "}"#, None),
            (r#"{"default": "x"}"#, None),
            ("{}", None),
            ("null", None),
            (r#""""#, None),
            ("", None),
            ("pas du json", None),
        ] {
            assert_eq!(parse_provider(json).as_deref(), provider, "{json}");
        }
    }

    #[test]
    fn each_provider_is_said_plainly() {
        assert!(provider_said("claude-subscription-directsdk-experimental").contains("abonnement Claude"));
        assert!(provider_said("openai-codex").contains("ChatGPT"));
        assert!(provider_said("openrouter").contains("que tu lui as choisie"));
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn the_app_is_a_whole_hermes_app_in_either_applications_folder() {
        let tmp = tempfile::tempdir().unwrap();
        let (system, mine) = (tmp.path().join("Applications"), tmp.path().join("home/Applications"));
        let dirs = [system.clone(), mine.clone()];
        assert_eq!(app_in(&dirs), None);
        // An empty folder left by a copy cut short is no app.
        std::fs::create_dir_all(mine.join("Hermes.app/Contents")).unwrap();
        assert_eq!(app_in(&dirs), None);
        std::fs::write(mine.join("Hermes.app/Contents/Info.plist"), "<plist/>").unwrap();
        assert_eq!(app_in(&dirs), Some(mine.join("Hermes.app")));
        std::fs::create_dir_all(system.join("Hermes.app/Contents")).unwrap();
        std::fs::write(system.join("Hermes.app/Contents/Info.plist"), "<plist/>").unwrap();
        assert_eq!(app_in(&dirs), Some(system.join("Hermes.app")), "the one from Hermes's site first");
    }
}
