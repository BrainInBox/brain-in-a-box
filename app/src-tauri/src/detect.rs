//! What is installed: the one answer the diagnosis and the install steps share.
//!
//! The probes run a program or look at the disk; the rules behind them take a home
//! folder or a program's output, so tests drive them with fakes.
//!
//! On Windows the answer is the one install.ps1 would give: it finds Python, Git,
//! Claude Code and Obsidian where its Find-* functions look, and leaves the brain where
//! this file looks for it. On Linux, the one install.sh gives: the same system tools
//! (missing_packages), Obsidian where obsidian_found looks.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::Value;

use crate::agents::{self, Agents, Engine};
use crate::{codex, sys};

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
#[cfg(target_os = "macos")]
pub fn clt_dir() -> Option<PathBuf> {
    let dir = PathBuf::from(sys::output_text(Path::new("/usr/bin/xcode-select"), &["-p"], TIMEOUT)?);
    has_clt(&dir).then_some(dir)
}

/// Without the tools, /usr/bin/git and /usr/bin/python3 are decoys that open Apple's
/// install window. So we look for the real git inside the selected folder, and never run it.
#[cfg(target_os = "macos")]
fn has_clt(dir: &Path) -> bool {
    dir.is_absolute() && dir.join("usr/bin/git").is_file()
}

// ── The system's tools (Linux: the paquets step installs them) ──────────────

/// What /etc/os-release says, as install.sh reads it.
#[cfg(target_os = "linux")]
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct OsRelease {
    /// "Ubuntu 24.04.1 LTS"
    pub pretty: Option<String>,
    pub id: String,
    pub like: String,
}

#[cfg(target_os = "linux")]
pub fn os_release() -> OsRelease {
    let text = fs::read_to_string("/etc/os-release").or_else(|_| fs::read_to_string("/usr/lib/os-release"));
    parse_os_release(&text.unwrap_or_default())
}

/// KEY=value lines, the value maybe quoted. Nothing is run or expanded.
#[cfg(target_os = "linux")]
fn parse_os_release(text: &str) -> OsRelease {
    let value = |key: &str| {
        text.lines().filter_map(|l| l.trim().split_once('=')).find(|(k, _)| *k == key).map(|(_, v)| {
            let v = v.trim();
            let unquoted = v.strip_prefix('"').and_then(|v| v.strip_suffix('"')).or_else(|| v.strip_prefix('\'').and_then(|v| v.strip_suffix('\'')));
            unquoted.unwrap_or(v).replace("\\\"", "\"").chars().filter(|c| !c.is_control()).take(80).collect::<String>()
        })
    };
    OsRelease {
        pretty: value("PRETTY_NAME").filter(|p| !p.trim().is_empty()),
        id: value("ID").unwrap_or_default().to_lowercase(),
        like: value("ID_LIKE").unwrap_or_default().to_lowercase(),
    }
}

/// Debian, Ubuntu and their derivatives (Mint, Pop!_OS…), apt-get there: install.sh's debian_like.
#[cfg(target_os = "linux")]
pub fn debian_like(os: &OsRelease) -> bool {
    debian_family(os) && sys::find_tool("apt-get").is_some()
}

#[cfg(target_os = "linux")]
fn debian_family(os: &OsRelease) -> bool {
    let words = format!(" {} {} ", os.id, os.like);
    words.contains(" debian ") || words.contains(" ubuntu ")
}

/// What install.sh's missing_packages lists, by Debian package name, with unzip always: the
/// app unpacks the brain's sources with it, and Bun's installer needs it.
#[cfg(target_os = "linux")]
const SYSTEM_TOOLS: &[(&str, &str)] = &[("python3", "python3"), ("git", "git"), ("curl", "curl"), ("pgrep", "procps"), ("unzip", "unzip")];
#[cfg(target_os = "linux")]
const CA_BUNDLE: &str = "/etc/ssl/certs/ca-certificates.crt";

/// The Debian packages of the tools the kit needs and this machine lacks.
#[cfg(target_os = "linux")]
pub fn missing_packages(debian: bool) -> Vec<&'static str> {
    let ca = fs::metadata(CA_BUNDLE).is_ok_and(|m| m.len() > 0);
    missing_from(&|tool| sys::find_tool(tool).is_some(), debian && !ca)
}

#[cfg(target_os = "linux")]
fn missing_from(has: &dyn Fn(&str) -> bool, no_ca: bool) -> Vec<&'static str> {
    let mut out: Vec<&'static str> = SYSTEM_TOOLS.iter().filter(|(tool, _)| !has(tool)).map(|(_, pkg)| *pkg).collect();
    if no_ca {
        out.push("ca-certificates");
    }
    out
}

/// install.sh refuses older ones.
#[cfg(target_os = "linux")]
pub const MIN_PYTHON3: Version = (3, 9, 0);

/// python3's version, when it runs.
#[cfg(target_os = "linux")]
pub fn python3() -> Option<Version> {
    let bin = sys::find_tool("python3")?;
    sys::output_text(&bin, &["--version"], TIMEOUT).and_then(|t| parse_version(&t))
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

/// winget, which install.ps1 takes Obsidian from (Python and Git too, else from their official
/// installers): only on the PATH it is given
/// (sys::tool_path), where its `Have winget` looks. Installed when it answers `--version`
/// ("v1.9.25200"). There but silent (NoVersion): its first run on a new profile can outlast the
/// probe, and an alias left behind by an App Installer that is gone answers nothing either.
#[cfg(windows)]
pub fn winget() -> Found {
    let aliases: Vec<PathBuf> = std::env::split_paths(&sys::tool_path())
        .map(|d| d.join("winget.exe"))
        // An app alias is a link that cannot be read: it exists all the same, and runs.
        .filter(|p| fs::symlink_metadata(p).is_ok())
        .collect();
    match aliases.iter().find_map(|p| sys::output_text(p, &["--version"], TIMEOUT)) {
        Some(version) => Found::Version(version),
        None if aliases.is_empty() => Found::Absent,
        None => Found::NoVersion,
    }
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

/// How Claude Code is logged in, as the kit uses it: only a subscription, never a login billed per use.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum ClaudeLogin {
    /// Nobody, or Claude Code does not say.
    #[default]
    LoggedOut,
    /// A subscription for sure: claude.ai with its plan said (Pro, Max, Team, Enterprise), or a
    /// subscription's setup-token (CLAUDE_CODE_OAUTH_TOKEN).
    Subscribed,
    /// claude.ai, its plan not said: Claude Code says null for a free account, as for a plan it could
    /// not read yet.
    Unconfirmed,
    /// Billed per use: an API key (an Anthropic Console account's too), a key helper's, a gateway's
    /// token, Bedrock or Vertex.
    Billed,
}

impl ClaudeLogin {
    /// Someone is logged in, with a subscription or not: what ends the window's wait at the login
    /// step. A login billed per use ends it too (the diagnosis says what it means), else the window
    /// would wait for ever.
    pub fn is_logged_in(self) -> bool {
        self != ClaudeLogin::LoggedOut
    }
}

pub fn claude_logged_in() -> bool {
    claude_login().is_logged_in()
}

pub fn claude_login() -> ClaudeLogin {
    claude_auth_status().map_or(ClaudeLogin::LoggedOut, |s| login_of(&s, setup_token_given()))
}

/// The setup-token is in the environment `claude auth status` inherits (sys::run passes it on).
fn setup_token_given() -> bool {
    std::env::var_os("CLAUDE_CODE_OAUTH_TOKEN").is_some_and(|t| !t.to_string_lossy().trim().is_empty())
}

/// How Claude Code is logged in: "claude.ai" for a claude.ai account, "none"
/// for nobody. Only this field is read: the rest of `claude auth status` (email,
/// organization) never leaves this function.
pub fn claude_auth_method() -> Option<String> {
    claude_auth_status().and_then(|s| auth_method(&s))
}

/// `claude auth status` exits 1 when nobody is logged in.
fn claude_auth_status() -> Option<String> {
    let claude = claude_bin()?;
    let out = sys::run(&claude, &["auth", "status"], TIMEOUT);
    out.ok().then_some(out.stdout)
}

/// `claude auth status` prints JSON, sometimes after a line of notes.
fn auth_status_json(status: &str) -> Option<Value> {
    let json = status.find('{').map_or("", |i| &status[i..]);
    serde_json::Deserializer::from_str(json).into_iter::<Value>().next()?.ok()
}

fn auth_method(status: &str) -> Option<String> {
    auth_status_json(status)?.get("authMethod").and_then(Value::as_str).map(String::from)
}

/// authMethod and the plan's name (subscriptionType), nothing else. Claude Code says the plan of a
/// claude.ai login only when it could read it: null for a free account as for a plan not read yet.
/// "oauth_token" is any token given in the environment: the setup-token's (`setup_token`), or a
/// gateway's ANTHROPIC_AUTH_TOKEN, billed. _conseil.connexion reads it alike, counting claude.ai as
/// a subscription whatever its plan (Unconfirmed is the window's own).
pub(crate) fn login_of(status: &str, setup_token: bool) -> ClaudeLogin {
    let said = auth_status_json(status);
    let field = |k: &str| said.as_ref().and_then(|v| v.get(k)).and_then(Value::as_str);
    match field("authMethod") {
        Some("claude.ai") => match field("subscriptionType") {
            Some("pro" | "max" | "team" | "enterprise") => ClaudeLogin::Subscribed,
            _ => ClaudeLogin::Unconfirmed,
        },
        Some("oauth_token") if setup_token => ClaudeLogin::Subscribed,
        Some("none") | None => ClaudeLogin::LoggedOut,
        Some(_) => ClaudeLogin::Billed,
    }
}

// ── Codex ───────────────────────────────────────────────────────────────────

/// Codex where the kit's launcher finds it (the official installer's place first), installed when it
/// answers `--version` ("codex-cli 0.162.1").
pub fn codex(setup: &codex::Setup) -> Found {
    codex::find(setup).map_or(Found::Absent, |bin| version_of(&bin, CLAUDE_TIMEOUT))
}

/// Recent enough for the kit's launcher, which pauses an older one.
pub fn codex_ok(found: &Found) -> bool {
    let (major, minor, patch) = codex::MIN_CODEX;
    found.version().is_some_and(|v| (u64::from(v.0), u64::from(v.1), u64::from(v.2)) >= (major, minor, patch))
}

// ── Obsidian ────────────────────────────────────────────────────────────────

/// In /Applications or ~/Applications, with its program inside: an empty or
/// half-copied Obsidian.app folder is not an installed Obsidian.
#[cfg(target_os = "macos")]
pub fn obsidian_app(home: &Path) -> Option<PathBuf> {
    app_in(&[PathBuf::from("/Applications"), home.join("Applications")], "Obsidian")
}

#[cfg(target_os = "macos")]
fn app_in(dirs: &[PathBuf], name: &str) -> Option<PathBuf> {
    dirs.iter()
        .map(|d| d.join(format!("{name}.app")))
        .find(|app| sys::is_executable(&app.join("Contents/MacOS").join(name)))
}

/// Installed once the app is there, even if its version cannot be read.
#[cfg(target_os = "macos")]
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

/// Obsidian on Linux, however it came: the .deb (/opt/Obsidian, the one the app and install.sh
/// install), a snap, a Flatpak, an AppImage. What it takes to start it, and where it keeps
/// its list of vaults.
#[cfg(target_os = "linux")]
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Obsidian {
    pub kind: ObsidianKind,
    /// The program, then the arguments before Obsidian's own (Flatpak: run md.obsidian.Obsidian).
    pub program: PathBuf,
    pub args: Vec<String>,
}

#[cfg(target_os = "linux")]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ObsidianKind {
    Deb,
    Snap,
    Flatpak,
    AppImage,
    /// An obsidian command on the PATH, from somewhere else.
    Other,
}

#[cfg(target_os = "linux")]
const OBSIDIAN_DEB: &str = "/opt/Obsidian/obsidian";
#[cfg(target_os = "linux")]
const OBSIDIAN_SNAP: &str = "/snap/bin/obsidian";
#[cfg(target_os = "linux")]
const OBSIDIAN_FLATPAK: &str = "md.obsidian.Obsidian";

/// Where install.sh's obsidian_found looks, in its order: the .deb's, the snap's, a Flatpak,
/// an AppImage in ~/Applications or ~/.local/bin, then any obsidian on the PATH.
#[cfg(target_os = "linux")]
pub fn obsidian_install(home: &Path) -> Option<Obsidian> {
    obsidian_in(home, Path::new("/"))
}

/// The same under `root` (/ in the app, a fake one in tests) for the system's folders.
#[cfg(target_os = "linux")]
fn obsidian_in(home: &Path, root: &Path) -> Option<Obsidian> {
    let at = |p: &str| root.join(p.trim_start_matches('/'));
    let found = |kind, program: PathBuf| Some(Obsidian { kind, program, args: Vec::new() });
    if sys::is_executable(&at(OBSIDIAN_DEB)) {
        return found(ObsidianKind::Deb, at(OBSIDIAN_DEB));
    }
    if sys::is_executable(&at(OBSIDIAN_SNAP)) {
        return found(ObsidianKind::Snap, at(OBSIDIAN_SNAP));
    }
    let flatpak = [home.join(".local/share/flatpak/app"), at("/var/lib/flatpak/app")]
        .iter()
        .any(|d| d.join(OBSIDIAN_FLATPAK).join("current/active").exists());
    if flatpak {
        if let Some(fp) = sys::find_tool("flatpak") {
            return Some(Obsidian { kind: ObsidianKind::Flatpak, program: fp, args: vec!["run".into(), OBSIDIAN_FLATPAK.into()] });
        }
    }
    for dir in [home.join("Applications"), home.join(".local/bin")] {
        let Ok(entries) = fs::read_dir(&dir) else { continue };
        let mut images: Vec<PathBuf> = entries
            .filter_map(Result::ok)
            .map(|e| e.path())
            .filter(|p| p.file_name().and_then(|n| n.to_str()).is_some_and(|n| n.starts_with("Obsidian") && n.ends_with(".AppImage")))
            .filter(|p| sys::is_executable(p))
            .collect();
        images.sort();
        if let Some(image) = images.pop() {
            return found(ObsidianKind::AppImage, image);
        }
    }
    sys::find_tool("obsidian").and_then(|p| found(ObsidianKind::Other, p))
}

/// Installed once it is there. Only the .deb tells its version (dpkg).
#[cfg(target_os = "linux")]
pub fn obsidian(home: &Path) -> Found {
    match obsidian_install(home) {
        None => Found::Absent,
        Some(o) if o.kind == ObsidianKind::Deb => sys::find_tool("dpkg-query")
            .and_then(|dq| sys::output_text(&dq, &["-W", "-f=${Version}", "obsidian"], TIMEOUT))
            .map_or(Found::NoVersion, Found::Version),
        Some(_) => Found::NoVersion,
    }
}

/// The official .deb is for x86-64 only: elsewhere Obsidian comes from Flathub or obsidian.md.
#[cfg(target_os = "linux")]
pub fn obsidian_installable(os: &OsRelease) -> bool {
    std::env::consts::ARCH == "x86_64" && debian_like(os)
}

// ── The brain ───────────────────────────────────────────────────────────────

/// What install.sh (install.ps1 on Windows) leaves behind once it went through: the vault and the
/// memory, then the part of each agent agents.json lists. Without the file, Claude's alone, as before
/// Codex.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Brain {
    /// ~/Documents/Brain
    pub vault: bool,
    /// gbrain's database. On Windows also the keyword index install.ps1 falls back on
    /// when gbrain does not install: the brain finds notes by keywords then.
    pub memory: bool,
    /// Claude Code's part, when Claude is one of the agents.
    pub claude: Option<ClaudePart>,
    /// Codex's, when Codex is one of them.
    pub codex: Option<CodexPart>,
    /// agents.json can be trusted, or is absent: otherwise no agent is wired (agents.rs), and the brain
    /// is not whole until the choice is made again. Not either while a choice made in the window waits
    /// for the installer to record it (agents::pending).
    pub chosen: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ClaudePart {
    /// Claude Code's hooks, in ~/.claude/settings.json.
    pub hooks: bool,
    pub skills: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CodexPart {
    /// The kit's hooks, in Codex's hooks.json.
    pub hooks: bool,
    /// The kit's block in the global instructions Codex reads (engine/codex/wire.py writes it).
    pub instructions: bool,
    /// The kit's skills, in ~/.agents/skills.
    pub skills: bool,
}

impl Brain {
    pub fn complete(self) -> bool {
        self.vault
            && self.memory
            && self.chosen
            && self.claude.is_none_or(|c| c.hooks && c.skills)
            && self.codex.is_none_or(|c| c.hooks && c.instructions && c.skills)
    }
}

pub fn brain(home: &Path, agents: &Agents) -> Brain {
    Brain {
        vault: vault(home).is_some(),
        memory: home.join(".gbrain/brain.pglite").exists() || keyword_index(home),
        claude: agents
            .lists(Engine::Claude)
            .then(|| ClaudePart { hooks: claude_hooks(home), skills: home.join(".claude/skills/brain-boost/SKILL.md").is_file() }),
        codex: agents.codex_home.as_deref().filter(|_| agents.lists(Engine::Codex)).map(|codex_home| CodexPart {
            // The command form on a Mac, the PowerShell one on Windows: the hook's name either way.
            hooks: fs::read_to_string(codex_home.join("hooks.json")).unwrap_or_default().contains("correction-detector.py"),
            instructions: codex_instructions(codex_home).contains(KIT_MARK),
            skills: home.join(".agents/skills/brain-boost/SKILL.md").is_file(),
        }),
        chosen: !agents.order.is_empty() && !agents::pending(agents),
    }
}

/// The kit's hooks are in Claude Code's settings.json: the shell form on a Mac, the exec form
/// (python.exe + the hook in "args") on Windows.
pub fn claude_hooks(home: &Path) -> bool {
    fs::read_to_string(home.join(".claude/settings.json")).unwrap_or_default().contains("correction-detector.py")
}

/// What the installers write before the kit's block in an instructions file.
const KIT_MARK: &str = "<!-- brain-in-a-box -->";

/// The global instructions Codex reads: AGENTS.override.md when it holds text, else AGENTS.md
/// (codex-rs/codex-home/src/instructions/mod.rs; wire.py writes the kit's block where Codex reads).
fn codex_instructions(codex_home: &Path) -> String {
    let text = |name: &str| fs::read(codex_home.join(name)).map(|b| String::from_utf8_lossy(&b).into_owned()).unwrap_or_default();
    let over = text("AGENTS.override.md");
    if over.trim().is_empty() {
        text("AGENTS.md")
    } else {
        over
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
pub fn brain_dir(home: &Path) -> PathBuf {
    home.join("Documents/Brain")
}

/// Where install.ps1 puts the brain: the folder chosen for it, else Documents\Brain in the
/// profile (never the Documents OneDrive may have moved).
#[cfg(windows)]
pub fn brain_dir(home: &Path) -> PathBuf {
    chosen_brain_dir().unwrap_or_else(|| home.join("Documents").join("Brain"))
}

/// BRAIN_DIR of this process, else the one install.ps1 saved for the user (HKCU\Environment):
/// an app started before the install does not have it in its environment, and install.ps1
/// may have chosen it itself (out of Documents, which controlled folder access protects).
/// None: install.ps1 chooses.
#[cfg(windows)]
pub fn chosen_brain_dir() -> Option<PathBuf> {
    std::env::var_os("BRAIN_DIR").filter(|d| !d.is_empty()).or_else(saved_brain_dir).map(PathBuf::from)
}

#[cfg(windows)]
fn saved_brain_dir() -> Option<std::ffi::OsString> {
    use winreg::{enums::HKEY_CURRENT_USER, RegKey};
    let dir: std::ffi::OsString = RegKey::predef(HKEY_CURRENT_USER).open_subkey("Environment").ok()?.get_value("BRAIN_DIR").ok()?;
    (!dir.is_empty()).then_some(dir)
}

/// Saves `dir` as the user's BRAIN_DIR, where install.ps1 saves it: its next run puts the brain there.
#[cfg(windows)]
pub fn save_brain_dir(dir: &Path) -> std::io::Result<()> {
    use winreg::{enums::HKEY_CURRENT_USER, RegKey};
    let (env, _) = RegKey::predef(HKEY_CURRENT_USER).create_subkey("Environment")?;
    env.set_value("BRAIN_DIR", &dir.as_os_str())
}

#[cfg(not(windows))]
pub fn save_brain_dir(_: &Path) -> std::io::Result<()> {
    Err(std::io::ErrorKind::Unsupported.into())
}

/// The brain's folder as the window says it: "Documents/Brain" on a Mac; on Windows the
/// whole path, since install.ps1 may have put it elsewhere.
pub fn brain_dir_shown(home: &Path) -> String {
    if cfg!(windows) {
        brain_dir(home).display().to_string()
    } else {
        "Documents/Brain".into()
    }
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

    #[cfg(target_os = "macos")]
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

    #[cfg(target_os = "macos")]
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

    #[cfg(target_os = "linux")]
    #[test]
    fn os_release_is_read_as_install_sh_reads_it() {
        let ubuntu = "PRETTY_NAME=\"Ubuntu 24.04.1 LTS\"\nNAME=\"Ubuntu\"\nID=ubuntu\nID_LIKE=debian\nVERSION_ID=\"24.04\"\n";
        let os = parse_os_release(ubuntu);
        assert_eq!(os, OsRelease { pretty: Some("Ubuntu 24.04.1 LTS".into()), id: "ubuntu".into(), like: "debian".into() });
        assert!(debian_family(&os));
        let mint = parse_os_release("ID=linuxmint\nID_LIKE='ubuntu debian'\nPRETTY_NAME='Linux Mint 22'\n");
        assert!(debian_family(&mint) && mint.pretty.as_deref() == Some("Linux Mint 22"));
        for other in ["ID=fedora\nPRETTY_NAME=\"Fedora Linux 41\"\n", "ID=arch\n", "ID=debianish\n", ""] {
            assert!(!debian_family(&parse_os_release(other)), "{other}");
        }
        // Nothing is expanded, nothing past the line is taken.
        let odd = parse_os_release("PRETTY_NAME=\"$(reboot) \\\"x\\\"\u{1b}[31m\"\nID=\n");
        assert_eq!(odd.pretty.as_deref(), Some("$(reboot) \"x\"[31m"));
        assert_eq!(parse_os_release("PRETTY_NAME=\"\"\n").pretty, None);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn missing_tools_are_named_as_debian_packages() {
        let none = missing_from(&|_| true, false);
        assert!(none.is_empty());
        let fresh_desktop = missing_from(&|t| !["git", "curl"].contains(&t), false);
        assert_eq!(fresh_desktop, ["git", "curl"]);
        assert_eq!(missing_from(&|_| false, true), ["python3", "git", "curl", "procps", "unzip", "ca-certificates"]);
    }

    /// The packages install.sh asks for are the app's, and the only ones root installs: what
    /// install.sh would stop on, the paquets step installs first, and nothing more (root.rs).
    #[cfg(target_os = "linux")]
    #[test]
    fn the_app_installs_the_packages_install_sh_needs_and_root_no_other() {
        let script = include_str!("../../../install.sh");
        let body = script.split("missing_packages() {").nth(1).and_then(|b| b.split("\n}").next()).unwrap();
        let mut named: Vec<&str> = body.lines().filter_map(|l| l.split("p=\"$p ").nth(1)).filter_map(|r| r.split('"').next()).collect();
        let mut app: Vec<&str> = SYSTEM_TOOLS.iter().map(|(_, p)| *p).chain(["ca-certificates"]).collect();
        let mut root = crate::root::PACKAGES.to_vec();
        for v in [&mut named, &mut app, &mut root] {
            v.sort_unstable();
        }
        assert_eq!(named, app);
        assert_eq!(app, root);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn obsidian_is_found_however_it_came() {
        let tmp = tempfile::tempdir().unwrap();
        let (home, root) = (tmp.path().join("home"), tmp.path().join("root"));
        fs::create_dir_all(&home).unwrap();
        assert_eq!(obsidian_in(&home, &root).map(|o| o.kind), sys::find_tool("obsidian").map(|_| ObsidianKind::Other));
        let image = home.join("Applications/Obsidian-1.14.4.AppImage");
        write_exec(&image, "");
        let o = obsidian_in(&home, &root).unwrap();
        assert_eq!((o.kind, o.program), (ObsidianKind::AppImage, image.clone()));
        fs::set_permissions(&image, fs::Permissions::from_mode(0o644)).unwrap();
        assert_ne!(obsidian_in(&home, &root).map(|o| o.kind), Some(ObsidianKind::AppImage), "not executable");
        write_exec(&root.join("snap/bin/obsidian"), "");
        assert_eq!(obsidian_in(&home, &root).map(|o| o.kind), Some(ObsidianKind::Snap));
        let deb = root.join("opt/Obsidian/obsidian");
        write_exec(&deb, "");
        let o = obsidian_in(&home, &root).unwrap();
        assert_eq!((o.kind, o.program, o.args.len()), (ObsidianKind::Deb, deb, 0));
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

    /// Only a subscription counts as one: claude.ai with its plan said, or the setup-token. claude.ai
    /// without a plan (Claude Code says null for a free account as for a plan not read yet) is
    /// unconfirmed. Any other token (a gateway's) and an API key are billed per use.
    #[test]
    fn a_login_counts_only_with_a_subscription() {
        use ClaudeLogin::*;
        assert_eq!(login_of(include_str!("../tests/fixtures/repair/auth-status-claude-ai.json"), false), Subscribed);
        assert_eq!(login_of(include_str!("../tests/fixtures/repair/auth-status-none.json"), false), LoggedOut);
        for plan in [r#""pro""#, r#""max""#, r#""team""#, r#""enterprise""#] {
            let said = format!(r#"{{"loggedIn":true,"authMethod":"claude.ai","subscriptionType":{plan}}}"#);
            assert_eq!(login_of(&said, false), Subscribed, "{plan}");
        }
        for plan in ["null", r#""free""#, r#""""#, "4"] {
            let said = format!(r#"{{"loggedIn":true,"authMethod":"claude.ai","subscriptionType":{plan}}}"#);
            assert_eq!(login_of(&said, false), Unconfirmed, "{plan}");
        }
        assert_eq!(login_of(r#"{"loggedIn":true,"authMethod":"claude.ai"}"#, false), Unconfirmed);
        // The setup-token is a subscription's: its plan is never said.
        assert_eq!(login_of(r#"{"loggedIn":true,"authMethod":"oauth_token","subscriptionType":null}"#, true), Subscribed);
        assert_eq!(login_of(r#"{"loggedIn":true,"authMethod":"oauth_token"}"#, true), Subscribed);
        assert_eq!(login_of(r#"{"loggedIn":true,"authMethod":"oauth_token"}"#, false), Billed);
        for billed in ["api_key", "api_key_helper", "third_party"] {
            assert_eq!(login_of(&format!(r#"note: update available{{"authMethod":"{billed}"}}"#), true), Billed, "{billed}");
        }
        for nothing in [r#"{"loggedIn":true}"#, r#"{"authMethod":4}"#, "Not logged in", "", "{oops"] {
            assert_eq!(login_of(nothing, true), LoggedOut, "{nothing}");
        }
        // The login step's wait ends for any login: billed, it would never end otherwise.
        assert!(Subscribed.is_logged_in() && Unconfirmed.is_logged_in() && Billed.is_logged_in() && !LoggedOut.is_logged_in());
    }

    #[test]
    fn brain_needs_all_four_markers() {
        let tmp = tempfile::tempdir().unwrap();
        let h = tmp.path();
        let claude = Agents::claude_alone();
        let none = Brain { vault: false, memory: false, claude: Some(ClaudePart { hooks: false, skills: false }), codex: None, chosen: true };
        assert_eq!(brain(h, &claude), none);
        assert!(!brain(h, &claude).complete());
        fs::create_dir_all(h.join("Documents")).unwrap();
        fs::write(h.join("Documents/Brain"), "a file, not the vault").unwrap();
        assert_eq!(vault(h), None);
        fs::remove_file(h.join("Documents/Brain")).unwrap();
        fake_brain(h);
        assert!(brain(h, &claude).complete());
        assert_eq!(vault(h), Some(brain_dir(h)));
        fs::write(h.join(".claude/settings.json"), r#"{"hooks":{}}"#).unwrap();
        let unhooked = Brain { vault: true, memory: true, claude: Some(ClaudePart { hooks: false, skills: true }), codex: None, chosen: true };
        assert_eq!(brain(h, &claude), unhooked);
        assert!(!brain(h, &claude).complete());
        assert!(!claude_hooks(h));
    }

    /// Codex's part: its hooks.json, the instructions it reads, the skills in ~/.agents. Only the agents
    /// agents.json lists count, and a choice the kit cannot trust is never a whole brain.
    #[test]
    fn each_agent_listed_has_its_part() {
        let tmp = tempfile::tempdir().unwrap();
        let h = tmp.path();
        let codex_home = h.join("Données é").join(".codex");
        let agents = |order: Vec<Engine>| {
            let codex_home = order.contains(&Engine::Codex).then(|| codex_home.clone());
            Agents { order, codex_home, etat: "ok".into() }
        };
        fake_brain(h);
        let both = agents(vec![Engine::Codex, Engine::Claude]);
        let codex = agents(vec![Engine::Codex]);
        assert_eq!(brain(h, &codex).claude, None, "Claude excluded: its files say nothing");
        assert_eq!(brain(h, &codex).codex, Some(CodexPart { hooks: false, instructions: false, skills: false }));
        assert!(!brain(h, &both).complete() && !brain(h, &codex).complete());

        fake_codex_brain(h, &codex_home);
        assert!(brain(h, &both).complete() && brain(h, &codex).complete());
        fs::remove_file(h.join(".claude/settings.json")).unwrap();
        assert!(brain(h, &codex).complete(), "Codex alone needs nothing of Claude's");
        assert!(!brain(h, &both).complete());

        // AGENTS.override.md with text is what Codex reads: the block must be there.
        fs::write(codex_home.join("AGENTS.override.md"), "# mine\n").unwrap();
        assert!(!brain(h, &codex).codex.unwrap().instructions);
        assert!(!brain(h, &codex).complete());
        fs::write(codex_home.join("AGENTS.override.md"), " \n").unwrap();
        assert!(brain(h, &codex).codex.unwrap().instructions, "blank: Codex reads AGENTS.md");
        fs::write(codex_home.join("hooks.json"), r#"{"hooks": {}}"#).unwrap();
        assert!(!brain(h, &codex).codex.unwrap().hooks);
        fs::remove_dir_all(h.join(".agents")).unwrap();
        assert!(!brain(h, &codex).codex.unwrap().skills);

        // A choice the kit cannot trust: no agent, never complete.
        fake_brain(h);
        let untrusted = brain(h, &Agents::none("ferme"));
        assert_eq!((untrusted.claude, untrusted.codex, untrusted.chosen), (None, None, false));
        assert!(untrusted.vault && untrusted.memory && !untrusted.complete());
    }

    /// What install.sh leaves for Codex (engine/codex/wire.py, install_skills.py).
    pub(crate) fn fake_codex_brain(home: &Path, codex_home: &Path) {
        fs::create_dir_all(codex_home).unwrap();
        let hook = home.join(".claude/hooks/brain/correction-detector.py");
        let hooks = serde_json::json!({ "hooks": { "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": format!("{} --agent codex", hook.display()), "timeout": 30 }] }] } });
        fs::write(codex_home.join("hooks.json"), hooks.to_string()).unwrap();
        fs::write(codex_home.join("AGENTS.md"), "# mine\n\n<!-- brain-in-a-box -->\n# AGENTS.md\n").unwrap();
        fs::create_dir_all(home.join(".agents/skills/brain-boost")).unwrap();
        fs::write(home.join(".agents/skills/brain-boost/SKILL.md"), "# skill").unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn codex_counts_once_it_tells_a_recent_enough_version() {
        let tmp = tempfile::tempdir().unwrap();
        let setup = codex::Setup {
            home: tmp.path().to_path_buf(),
            codex_home: tmp.path().join(".codex"),
            work_root: tmp.path().to_path_buf(),
            features: tmp.path().join("none.json"),
            layers: codex::Layers::default(),
            vars: Box::new(|_| None),
            command: None,
        };
        assert_eq!(codex(&setup), Found::Absent);
        let bin = tmp.path().join(".local/bin/codex");
        write_exec(&bin, "#!/bin/sh\necho codex-cli 0.162.1\n");
        assert_eq!(codex(&setup), Found::Version("codex-cli 0.162.1".into()));
        assert!(codex_ok(&codex(&setup)));
        for (said, ok) in [("codex-cli 0.161.9", false), ("codex-cli 0.163.0-alpha.11", true), ("codex-cli 1.0.0", true), ("?", false)] {
            assert_eq!(codex_ok(&Found::Version(said.into())), ok, "{said}");
        }
        assert!(!codex_ok(&Found::NoVersion) && !codex_ok(&Found::Absent));
        write_exec(&bin, "#!/bin/sh\nexit 1\n");
        assert_eq!(codex(&setup), Found::NoVersion);
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
        assert!(!brain(h, &Agents::claude_alone()).memory);
        fs::create_dir_all(h.join(".brain-search")).unwrap();
        fs::write(h.join(".brain-search/engine"), "brain_search\n").unwrap();
        assert!(brain(h, &Agents::claude_alone()).memory);
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
