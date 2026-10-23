//! Looks at the Mac (or the PC): what is there, what is missing, what blocks the install.
//!
//! `probe()` runs the system commands and gathers raw facts; `decide()` turns
//! them, plus the files under a given home, into checks. All the logic sits in
//! `decide()` and the `check_*` functions, so tests drive them with a fake home.
//!
//! On Windows, install.ps1 installs Python, Git, Bun and Obsidian along with the brain:
//! their checks point to the brain step. Python and Git come from winget, else from their official
//! installers; Obsidian from winget alone. On Linux, the system's own tools (git, python3,
//! curl) come first, from the system's packages, as Apple's tools do on a Mac.
//!
//! The AI agents are the ones agents.json lists (agents.rs): Claude Code's checks, Codex's, or both,
//! each with its site in the network check and its part of the brain. Without the file, Claude's
//! alone, the checks of before Codex word for word.
//!
//! What is left to install comes with a headline: how long, and how much to download. When something
//! is left, the network's speed is measured on 2 Mo from GitHub, alongside the other probes.

use std::path::Path;
use std::thread;
use std::time::Duration;

use crate::agents::{self, Agents, Engine};
#[cfg(target_os = "macos")]
use crate::detect::parse_version;
#[cfg(unix)]
use crate::detect::Version;
use crate::detect::{self, fmt_version, Brain, ClaudeLogin, Found};
use crate::model::{Check, Diagnosis, Status};
use crate::{advice, codex, steps, sys};

#[cfg(target_os = "macos")]
const MIN_MACOS: Version = (13, 0, 0);
/// Windows 10 version 1809: install.ps1's floor (winget, curl, tar).
#[cfg(windows)]
const MIN_WINDOWS_BUILD: u32 = 17763;
/// Windows 11 kept the 10 in its version number: the build tells them apart.
#[cfg(windows)]
const WINDOWS_11_BUILD: u32 = 22000;
/// Where Codex's service is.
const CHATGPT: &str = "https://chatgpt.com";
const DISK_BLOCKING_GB: f64 = 3.0;
const DISK_TIGHT_GB: f64 = 10.0;
const PROBE_TIMEOUT: Duration = Duration::from_secs(10);
/// A file the install downloads from GitHub (Obsidian's, the Mac step's own: a test holds them
/// equal), of which 2 Mo are timed, for 8 s at most.
const SPEED_URL: &str = "https://github.com/obsidianmd/obsidian-releases/releases/download/v1.14.4/Obsidian-1.14.4.dmg";
const SPEED_BYTES: u64 = 2 * 1024 * 1024;
/// Cut before the 2 Mo, a download says the network's speed only after this long: before, the
/// connection is still speeding up (on a satellite link, the first seconds go to the handshakes).
const SPEED_MIN_SECONDS: f64 = 3.0;
/// 2 Mbit/s: below, the downloads of a first install take an hour or more.
const SLOW_BYTES_PER_S: f64 = 250_000.0;
/// Without a measure, what most connections give (20 Mbit/s).
const USUAL_BYTES_PER_S: f64 = 2_500_000.0;

/// One line of the checklist: id, what the user reads, and the install step that fixes it.
#[derive(Clone, Copy)]
struct Item(&'static str, &'static str, Option<&'static str>);

#[cfg(target_os = "macos")]
const MACOS: Item = Item("macos", "Version de macOS", None);
#[cfg(target_os = "linux")]
const LINUX: Item = Item("linux", "Système", None);
#[cfg(windows)]
const WINDOWS: Item = Item("windows", "Version de Windows", None);
const DISK: Item = Item("disk", "Espace disque", None);
const NETWORK: Item = Item("network", "Connexion internet", None);
#[cfg(target_os = "macos")]
const CLT: Item = Item("clt", "Outils Apple (git, python3)", Some("clt"));
#[cfg(target_os = "linux")]
const PAQUETS: Item = Item("paquets", "Outils de base (git, python3, curl)", Some("paquets"));
#[cfg(windows)]
const PYTHON: Item = Item("python", "Python (pour les automatismes)", Some("brain"));
#[cfg(windows)]
const GIT: Item = Item("git", "Git (l'historique de tes notes)", Some("brain"));
/// No fix step: the user installs it from the Microsoft Store.
#[cfg(windows)]
const WINGET: Item = Item("winget", "Outil d'installation de Microsoft", None);
/// The step that installs Bun and Obsidian: their own on a Mac, install.ps1's on Windows.
const OWN_STEP_OR_BRAIN: [&str; 2] = if cfg!(windows) { ["brain", "brain"] } else { ["bun", "obsidian"] };
const BUN: Item = Item("bun", "Le moteur de la mémoire", Some(OWN_STEP_OR_BRAIN[0]));
const CLAUDE: Item = Item("claude", "Claude Code", Some("claude"));
/// No fix step: the window offers a login button and the user logs in to the official CLI themselves.
const CLAUDE_AUTH: Item = Item("claude_auth", "Connexion à Claude", None);
const CODEX: Item = Item("codex", "Codex", Some("codex"));
/// No fix step either: the user logs in to Codex's own CLI, with their ChatGPT account.
const CODEX_AUTH: Item = Item("codex_auth", "Connexion à Codex", None);
/// agents.json lists who the brain works with: one the kit cannot trust blocks.
const AGENTS: Item = Item("agents", "Ton choix d'IA", None);
/// The choice made in the window, which the brain step hands to the installer to record.
const AGENTS_TO_RECORD: Item = Item("agents", "Ton choix d'IA", Some("brain"));
const OBSIDIAN: Item = Item("obsidian", "Obsidian (pour lire tes notes)", Some(OWN_STEP_OR_BRAIN[1]));
const BRAIN_VAULT: Item = Item("brain_vault", "Le dossier de tes notes", Some("brain"));
const BRAIN_HOOKS: Item = Item("brain_hooks", "Les automatismes de Claude", Some("brain"));
const BRAIN_SKILLS: Item = Item("brain_skills", "Les compétences de Claude", Some("brain"));
const BRAIN_CODEX_HOOKS: Item = Item("brain_codex_hooks", "Les automatismes de Codex", Some("brain"));
const BRAIN_CODEX_INSTRUCTIONS: Item = Item("brain_codex_instructions", "Tes consignes pour Codex", Some("brain"));
const BRAIN_CODEX_SKILLS: Item = Item("brain_codex_skills", "Les compétences de Codex", Some("brain"));
const BRAIN_MEMORY: Item = Item("brain_memory", "La mémoire de recherche", Some("brain"));
const VOYAGE: Item = Item("voyage", "Recherche par le sens", None);
const INTERNAL: Item = Item("internal", "Erreur interne", None);

impl Item {
    fn is(self, status: Status, detail: impl Into<String>) -> Check {
        let Item(id, label, fix) = self;
        Check {
            id: id.into(),
            label: label.into(),
            status,
            detail: detail.into(),
            fix: fix.map(String::from),
            path: None,
        }
    }
}

/// Raw results of the system probes, before any decision.
#[derive(Debug)]
struct Facts {
    /// `sw_vers -productVersion`, e.g. "26.3.1".
    #[cfg(target_os = "macos")]
    macos: Option<String>,
    #[cfg(target_os = "linux")]
    linux: LinuxFacts,
    #[cfg(windows)]
    windows: Option<WindowsVersion>,
    arch: String,
    ram_bytes: Option<u64>,
    disk_free_kb: Option<u64>,
    /// What github.com answered: its HTTP status (0 for a bare connection), None when nothing did.
    github: Option<u16>,
    /// The network's speed from GitHub, in bytes per second, when it was measured.
    speed: Option<f64>,
    claude_ai: bool,
    #[cfg(target_os = "macos")]
    clt: bool,
    #[cfg(windows)]
    python: Option<detect::Python>,
    #[cfg(windows)]
    git: Found,
    /// Looked for only when Obsidian is missing.
    #[cfg(windows)]
    winget: Option<Found>,
    bun: Found,
    claude: Found,
    claude_login: ClaudeLogin,
    /// What the advice reads (advice.rs): files only, and the login `claude auth status` gave.
    advice: advice::Facts,
    obsidian: Found,
    /// Who the brain works with (agents.json): Claude's facts are looked for only when Claude is one
    /// of them, Codex's when Codex is.
    agents: Agents,
    chatgpt: bool,
    codex: Option<CodexFacts>,
}

/// Codex, when agents.json lists it.
#[derive(Debug)]
struct CodexFacts {
    found: Found,
    login: codex::Login,
    /// The kit's list of features turned off, which reading the login takes: the brain step installs it.
    features: bool,
    /// Codex keeps sessions without running the kit's hooks (agents::hooks_muets).
    hooks_muets: bool,
}

/// The system and its packages, as install.sh sees them.
#[cfg(target_os = "linux")]
#[derive(Debug, Clone, Default)]
struct LinuxFacts {
    /// /etc/os-release's PRETTY_NAME: "Ubuntu 24.04.1 LTS".
    name: Option<String>,
    /// Debian, Ubuntu or a derivative, with apt-get: the paquets step can install there.
    debian: bool,
    /// Debian names of the tools that are missing.
    missing: Vec<&'static str>,
    python3: Option<Version>,
    /// The official Obsidian package fits this system (x86-64, apt).
    obsidian_installable: bool,
}

#[cfg(windows)]
#[derive(Debug, Clone, PartialEq, Eq)]
struct WindowsVersion {
    build: u32,
    /// "23H2", when Windows says.
    release: Option<String>,
}

#[cfg(windows)]
impl WindowsVersion {
    /// "Windows 11 (23H2)".
    fn name(&self) -> String {
        let n = if self.build >= WINDOWS_11_BUILD { 11 } else { 10 };
        match &self.release {
            Some(r) => format!("Windows {n} ({r})"),
            None => format!("Windows {n}"),
        }
    }
}

pub fn run() -> Diagnosis {
    let home = sys::home();
    decide(&home, &probe(&home))
}

/// What the window gets when the diagnosis itself broke: one red line, nothing to install.
pub fn failed(why: &str) -> Diagnosis {
    Diagnosis {
        os: if cfg!(windows) { "Windows" } else if cfg!(target_os = "linux") { "Linux" } else { "macOS" }.into(),
        arch: String::new(),
        ram_gb: 0.0,
        disk_free_gb: 0.0,
        online: false,
        checks: vec![INTERNAL.is(
            Status::Error,
            format!("Je n'ai pas pu finir l'examen de ton {} ({why}). Clique sur Revérifier.", sys::MACHINE),
        )],
        todo: Vec::new(),
        can_install: false,
        headline: format!("Je n'ai pas pu finir l'examen de ton {}. Clique sur Revérifier.", sys::MACHINE),
        download_mb: 0,
        minutes: 0,
        advice: None,
    }
}

/// Before the probes: something big is left to download (the brain, or Claude Code), so the
/// network's speed is worth 2 Mo. Nothing is, on most visits after the install.
fn left_to_download(home: &Path, agents: &Agents) -> bool {
    !detect::brain(home, agents).complete() || (agents.lists(Engine::Claude) && detect::claude_bin().is_none())
}

#[cfg(target_os = "macos")]
fn probe(home: &Path) -> Facts {
    let agents = agents::current(home);
    let (claude, codex) = (agents.lists(Engine::Claude), agents.lists(Engine::Codex));
    let to_download = left_to_download(home, &agents);
    thread::scope(|s| {
        // The slow probes (network, `claude auth status`, Codex's login) run alongside the rest.
        let github = s.spawn(|| http_status("https://github.com"));
        let speed = s.spawn(move || if to_download { speed() } else { None });
        let claude_ai = s.spawn(move || claude && answers("https://claude.ai"));
        // `claude auth status` once: how Claude is logged in, for its check and the advice.
        let login = s.spawn(move || {
            let login = if claude { detect::claude_login() } else { ClaudeLogin::LoggedOut };
            (login, advice::probe(home, login))
        });
        let chatgpt = s.spawn(move || codex && answers(CHATGPT));
        let codex = s.spawn(|| probe_codex(home, &agents));

        let home_arg = home.to_string_lossy();
        // Fields are evaluated in this order: the threads are joined last.
        let facts = Facts {
            macos: command_text("/usr/bin/sw_vers", &["-productVersion"]),
            arch: command_text("/usr/bin/uname", &["-m"]).unwrap_or_default(),
            ram_bytes: command_text("/usr/sbin/sysctl", &["-n", "hw.memsize"]).and_then(|t| t.parse().ok()),
            disk_free_kb: command_text("/bin/df", &["-k", "--", &home_arg]).and_then(|t| parse_df_available_kb(&t)),
            clt: detect::clt_dir().is_some(),
            bun: detect::bun(home),
            claude: if claude { detect::claude() } else { Found::Absent },
            obsidian: detect::obsidian(home),
            github: github.join().ok().flatten(),
            speed: speed.join().ok().flatten(),
            claude_ai: claude_ai.join().unwrap_or(false),
            claude_login: ClaudeLogin::LoggedOut,
            advice: advice::Facts::default(),
            chatgpt: chatgpt.join().unwrap_or(false),
            codex: codex.join().ok().flatten(),
            agents: agents.clone(),
        };
        let (claude_login, seen) = login.join().unwrap_or_default();
        Facts { claude_login, advice: seen, ..facts }
    })
}

/// Codex's facts, when Codex is one of the agents: where it is, its login (read only once it
/// answers), whether it runs the kit's hooks.
fn probe_codex(home: &Path, agents: &Agents) -> Option<CodexFacts> {
    let codex_home = agents.codex_home.as_deref().filter(|_| agents.lists(Engine::Codex))?;
    let setup = codex::Setup::for_user(codex_home).ok();
    let found = setup.as_ref().map_or(Found::Absent, detect::codex);
    let login = match &setup {
        Some(s) if found.answers() => codex::login(s),
        _ => codex::Login::Unknown,
    };
    let features = setup.as_ref().is_some_and(|s| s.features.is_file());
    Some(CodexFacts { found, login, features, hooks_muets: agents::hooks_muets(home, agents) })
}

#[cfg(windows)]
fn probe(home: &Path) -> Facts {
    let agents = agents::current(home);
    let (claude, codex) = (agents.lists(Engine::Claude), agents.lists(Engine::Codex));
    let to_download = left_to_download(home, &agents);
    thread::scope(|s| {
        let github = s.spawn(|| http_status("https://github.com"));
        let speed = s.spawn(move || if to_download { speed() } else { None });
        let claude_ai = s.spawn(move || claude && answers("https://claude.ai"));
        // `claude auth status` once: how Claude is logged in, for its check and the advice.
        let login = s.spawn(move || {
            let login = if claude { detect::claude_login() } else { ClaudeLogin::LoggedOut };
            (login, advice::probe(home, login))
        });
        let chatgpt = s.spawn(move || codex && answers(CHATGPT));
        let codex = s.spawn(|| probe_codex(home, &agents));
        // PowerShell takes a second or two to start: alongside the rest too.
        let system = s.spawn(|| {
            let out = sys::run(
                &sys::powershell(),
                &["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", WINDOWS_PROBE],
                SYSTEM_PROBE_TIMEOUT,
            );
            parse_windows_probe(&out.stdout)
        });

        let python = detect::python();
        let git = detect::git();
        let bun = detect::bun(home);
        let claude = if claude { detect::claude() } else { Found::Absent };
        let obsidian = detect::obsidian(home);
        let winget = (obsidian == Found::Absent).then(detect::winget);
        let system = system.join().unwrap_or_default();
        let facts = Facts {
            windows: system.version,
            arch: system.arch,
            ram_bytes: system.ram_bytes,
            disk_free_kb: system.free_bytes.map(|b| b / 1024),
            github: github.join().ok().flatten(),
            speed: speed.join().ok().flatten(),
            claude_ai: claude_ai.join().unwrap_or(false),
            python,
            git,
            winget,
            bun,
            claude,
            claude_login: ClaudeLogin::LoggedOut,
            advice: advice::Facts::default(),
            obsidian,
            chatgpt: chatgpt.join().unwrap_or(false),
            codex: codex.join().ok().flatten(),
            agents: agents.clone(),
        };
        let (claude_login, seen) = login.join().unwrap_or_default();
        Facts { claude_login, advice: seen, ..facts }
    })
}

#[cfg(target_os = "linux")]
fn probe(home: &Path) -> Facts {
    let agents = agents::current(home);
    let (claude, codex) = (agents.lists(Engine::Claude), agents.lists(Engine::Codex));
    let to_download = left_to_download(home, &agents);
    thread::scope(|s| {
        let github = s.spawn(|| http_status("https://github.com"));
        let speed = s.spawn(move || if to_download { speed() } else { None });
        let claude_ai = s.spawn(move || claude && answers("https://claude.ai"));
        // `claude auth status` once: how Claude is logged in, for its check and the advice.
        let login = s.spawn(move || {
            let login = if claude { detect::claude_login() } else { ClaudeLogin::LoggedOut };
            (login, advice::probe(home, login))
        });
        let chatgpt = s.spawn(move || codex && answers(CHATGPT));
        let codex = s.spawn(|| probe_codex(home, &agents));

        let home_arg = home.to_string_lossy();
        let os = detect::os_release();
        let debian = detect::debian_like(&os);
        let facts = Facts {
            linux: LinuxFacts {
                name: os.pretty.clone(),
                debian,
                missing: detect::missing_packages(debian),
                python3: detect::python3(),
                obsidian_installable: detect::obsidian_installable(&os),
            },
            arch: linux_arch(&command_text("/usr/bin/uname", &["-m"]).unwrap_or_default()),
            ram_bytes: std::fs::read_to_string("/proc/meminfo").ok().and_then(|t| mem_total_bytes(&t)),
            // -P: one line per filesystem, however long its name.
            disk_free_kb: command_text("/bin/df", &["-P", "-k", "--", &home_arg]).and_then(|t| parse_df_available_kb(&t)),
            bun: detect::bun(home),
            claude: if claude { detect::claude() } else { Found::Absent },
            obsidian: detect::obsidian(home),
            github: github.join().ok().flatten(),
            speed: speed.join().ok().flatten(),
            claude_ai: claude_ai.join().unwrap_or(false),
            claude_login: ClaudeLogin::LoggedOut,
            advice: advice::Facts::default(),
            chatgpt: chatgpt.join().unwrap_or(false),
            codex: codex.join().ok().flatten(),
            agents: agents.clone(),
        };
        let (claude_login, seen) = login.join().unwrap_or_default();
        Facts { claude_login, advice: seen, ..facts }
    })
}

/// The window says arm64 for any ARM processor, as macOS and Windows do.
#[cfg(target_os = "linux")]
fn linux_arch(uname: &str) -> String {
    match uname.trim() {
        "aarch64" | "arm64" => "arm64".into(),
        other => other.into(),
    }
}

/// "MemTotal:       16314512 kB" of /proc/meminfo, in bytes.
#[cfg(target_os = "linux")]
fn mem_total_bytes(meminfo: &str) -> Option<u64> {
    let line = meminfo.lines().find(|l| l.starts_with("MemTotal:"))?;
    let kb: u64 = line.split_whitespace().nth(1)?.parse().ok()?;
    (kb > 0).then_some(kb * 1024)
}

#[cfg(windows)]
const SYSTEM_PROBE_TIMEOUT: Duration = Duration::from_secs(25);

/// One PowerShell for the facts Windows only gives to programs: the build and release
/// (registry), the processor of the machine (not of this process, which an ARM PC may
/// emulate), the memory and the free space on the profile's drive. Single quotes only:
/// the script travels as one argument.
#[cfg(windows)]
const WINDOWS_PROBE: &str = "$ErrorActionPreference = 'SilentlyContinue'; \
$k = Get-ItemProperty -LiteralPath 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion'; \
$e = Get-ItemProperty -LiteralPath 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment'; \
$rel = if ($k.DisplayVersion) { $k.DisplayVersion } else { $k.ReleaseId }; \
$ram = (Get-CimInstance -ClassName Win32_ComputerSystem).TotalPhysicalMemory; \
$free = (New-Object System.IO.DriveInfo -ArgumentList ([IO.Path]::GetPathRoot($env:USERPROFILE))).AvailableFreeSpace; \
'build=' + $k.CurrentBuildNumber; 'release=' + $rel; 'arch=' + $e.PROCESSOR_ARCHITECTURE; 'ram=' + $ram; 'free=' + $free";

#[cfg(windows)]
#[derive(Debug, Default, PartialEq, Eq)]
struct WindowsFacts {
    version: Option<WindowsVersion>,
    arch: String,
    ram_bytes: Option<u64>,
    free_bytes: Option<u64>,
}

/// The probe's key=value lines; anything missing or odd stays unknown.
#[cfg(windows)]
fn parse_windows_probe(out: &str) -> WindowsFacts {
    let value = |key: &str| {
        out.lines()
            .filter_map(|l| l.trim().split_once('='))
            .find(|(k, _)| *k == key)
            .map(|(_, v)| v.trim().to_string())
            .filter(|v| !v.is_empty())
    };
    let number = |key: &str| value(key).and_then(|v| v.parse::<u64>().ok()).filter(|n| *n > 0);
    let release = value("release").filter(|r| r.len() <= 8 && r.bytes().all(|b| b.is_ascii_alphanumeric()));
    WindowsFacts {
        version: value("build").and_then(|b| b.parse().ok()).map(|build| WindowsVersion { build, release }),
        arch: match value("arch").as_deref() {
            Some("AMD64") => "x86_64".into(),
            Some("ARM64") => "arm64".into(),
            Some(other) => other.to_ascii_lowercase(),
            None => String::new(),
        },
        ram_bytes: number("ram"),
        free_bytes: value("free").and_then(|v| v.parse::<u64>().ok()),
    }
}

#[cfg(unix)]
fn command_text(program: &str, args: &[&str]) -> Option<String> {
    sys::output_text(Path::new(program), args, PROBE_TIMEOUT)
}

/// Any HTTP status counts: claude.ai answers 403 to curl, which still proves the network works.
pub(crate) fn answers(url: &str) -> bool {
    http_status(url).is_some()
}

/// The HTTP status a site answers with, None when nothing answered. A Linux without curl yet (the
/// paquets step brings it) gets a plain connection instead: 0 when it is accepted.
fn http_status(url: &str) -> Option<u16> {
    if cfg!(target_os = "linux") && !sys::is_executable(&sys::curl()) {
        return connects(url).then_some(0);
    }
    let args = ["-sS", "-o", sys::NULL_DEVICE, "-m", "8", "-w", "%{http_code}", url];
    http_code(&sys::run(&sys::curl(), &args, PROBE_TIMEOUT).stdout)
}

/// The site's port 443 accepts a connection within 8 s, name lookup included.
fn connects(url: &str) -> bool {
    use std::net::{TcpStream, ToSocketAddrs};
    use std::sync::mpsc;
    let Some(host) = url.strip_prefix("https://").map(|r| r.split(['/', '?', '#']).next().unwrap_or_default().to_string()) else {
        return false;
    };
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let ok = (host.as_str(), 443)
            .to_socket_addrs()
            .is_ok_and(|mut addrs| addrs.any(|a| TcpStream::connect_timeout(&a, Duration::from_secs(8)).is_ok()));
        let _ = tx.send(ok);
    });
    rx.recv_timeout(Duration::from_secs(10)).unwrap_or(false)
}

/// curl prints "000" when nothing answered.
fn http_code(out: &str) -> Option<u16> {
    out.trim().parse::<u16>().ok().filter(|c| *c > 0)
}

/// Bytes per second of 2 Mo from GitHub, or what came of them in 8 s. None when the file did not
/// come (no curl yet, no network, a filter): the network check says why.
fn speed() -> Option<f64> {
    if !sys::is_executable(&sys::curl()) {
        return None;
    }
    let range = format!("0-{}", SPEED_BYTES - 1);
    // --max-filesize: a server that would send the whole file instead of the range is not waited for.
    let max = (SPEED_BYTES + SPEED_BYTES / 2).to_string();
    let args = [
        "-sS", "-L", "-r", &range, "--max-filesize", &max, "-o", sys::NULL_DEVICE, "-m", "8", "-w",
        "%{http_code} %{size_download} %{time_starttransfer} %{time_total}", SPEED_URL,
    ];
    parse_speed(&sys::run(&sys::curl(), &args, PROBE_TIMEOUT).stdout)
}

/// curl's "206 2097152 0.112 0.146": bytes over the time they took to come, from the first one
/// (the connection and the redirect to GitHub's servers are not the network's speed). Cut by the
/// time limit after less than SPEED_MIN_SECONDS of data, it says nothing.
fn parse_speed(out: &str) -> Option<f64> {
    let mut fields = out.split_whitespace().map(str::parse::<f64>);
    let mut next = || fields.next()?.ok();
    let (code, bytes, start, total) = (next()?, next()?, next()?, next()?);
    if !(code == 200.0 || code == 206.0) || bytes <= 0.0 {
        return None;
    }
    if bytes < SPEED_BYTES as f64 && total - start < SPEED_MIN_SECONDS {
        return None;
    }
    Some(bytes / (total - start).max(0.05))
}

fn decide(home: &Path, f: &Facts) -> Diagnosis {
    let disk_free_gb = f.disk_free_kb.map(kb_to_gb);
    #[cfg(target_os = "macos")]
    let system = [check_macos(f.macos.as_deref())];
    #[cfg(target_os = "linux")]
    let system = [check_linux(&f.linux)];
    #[cfg(windows)]
    let system = [check_windows(f.windows.as_ref())];
    #[cfg(target_os = "macos")]
    let tools = vec![check_clt(f.clt)];
    #[cfg(target_os = "linux")]
    let tools = vec![check_paquets(&f.linux)];
    // Looked for only when Obsidian is missing (None: not needed). There but silent, install.ps1 tries
    // it all the same.
    #[cfg(windows)]
    let winget = f.winget.as_ref().is_none_or(|w| *w != Found::Absent);
    #[cfg(windows)]
    let tools = {
        let mut tools = vec![check_python(f.python.as_ref()), check_git(&f.git)];
        tools.extend(check_winget(f.obsidian == Found::Absent, f.winget.as_ref()));
        tools
    };
    let hosts = hosts(f);
    let mut checks = Vec::from(system);
    checks.push(check_disk(disk_free_gb));
    // Its place: what it says of a slow network needs what is left to download, known at the end.
    let network_at = checks.len();
    checks.extend(tools);
    checks.push(check_bun(&f.bun));
    for engine in &f.agents.order {
        match engine {
            Engine::Claude => checks.extend([check_claude(&f.claude), check_claude_auth(f.claude.answers(), f.claude_login)]),
            Engine::Codex => checks.extend(f.codex.iter().flat_map(|c| [check_codex(&c.found), check_codex_auth(c)])),
        }
    }
    if f.agents.order.is_empty() {
        checks.push(check_agents(&f.agents.etat));
    } else if agents::pending(&f.agents) {
        checks.push(AGENTS_TO_RECORD.is(Status::Missing, "Je le note en installant ton cerveau."));
    }
    checks.extend([
        #[cfg(target_os = "macos")]
        check_obsidian(&f.obsidian),
        #[cfg(windows)]
        check_obsidian_windows(&f.obsidian, winget),
        #[cfg(target_os = "linux")]
        check_obsidian_linux(&f.obsidian, f.linux.obsidian_installable),
    ]);
    let muets = f.codex.as_ref().is_some_and(|c| c.hooks_muets);
    checks.extend(check_brain(detect::brain(home, &f.agents), &detect::brain_dir_shown(home), muets));
    checks.push(check_voyage(detect::embedding_model(home).as_deref()));

    let missing = |step: &str| {
        checks
            .iter()
            .any(|c| c.status == Status::Missing && c.fix.as_deref() == Some(step))
    };
    let todo: Vec<String> = steps::order(&f.agents)
        .iter()
        .filter(|s| missing(s))
        .map(|s| s.to_string())
        .collect();
    let download_mb = download_mb(&checks, &todo);
    let minutes = minutes(&todo, download_mb, f.speed);
    let slow = f.speed.is_some_and(|s| s < SLOW_BYTES_PER_S) && download_mb > 0;
    let slow_note = slow.then(|| {
        format!(
            "prévois {} en tout, avec {} à télécharger. En données mobiles, attends d'avoir un Wi-Fi.",
            fmt_minutes(minutes),
            fmt_mb(download_mb)
        )
    });
    // A 403 for github.com is a filter, unless GitHub's file came all the same (the speed's measure).
    let filtered = f.github == Some(403) && f.speed.is_none();
    checks.insert(network_at, check_network(&hosts, filtered, slow_note.as_deref()));
    let can_install = checks.iter().all(|c| c.status != Status::Error);
    let headline = headline(&checks, &todo, download_mb, minutes, slow);

    #[cfg(target_os = "macos")]
    let os = f.macos.as_ref().map_or("macOS".into(), |v| format!("macOS {v}"));
    #[cfg(target_os = "linux")]
    let os = f.linux.name.clone().unwrap_or_else(|| "Linux".into());
    #[cfg(windows)]
    let os = f.windows.as_ref().map_or("Windows".into(), WindowsVersion::name);
    Diagnosis {
        os,
        arch: f.arch.clone(),
        // Apple (and Windows) count RAM in binary gigabytes: "24 Go" is 24 GiB.
        ram_gb: f.ram_bytes.map_or(0.0, |b| round1(b as f64 / (1u64 << 30) as f64)),
        // Rounded down like the detail text, so both read "2,9" when 3 are needed.
        disk_free_gb: disk_free_gb.map_or(0.0, floor1),
        online: hosts.iter().all(|(_, up)| *up),
        checks,
        todo,
        can_install,
        headline,
        download_mb,
        minutes,
        advice: advice::decide(&f.advice),
    }
}

#[cfg(target_os = "macos")]
fn check_macos(version: Option<&str>) -> Check {
    match version.and_then(|v| Some((v, parse_version(v)?))) {
        Some((v, parsed)) if parsed >= MIN_MACOS => MACOS.is(Status::Ok, format!("macOS {v}")),
        Some((v, _)) => MACOS.is(
            Status::Error,
            format!(
                "macOS {v} est trop ancien. Il faut macOS 13 ou plus : mets ton Mac à jour dans Préférences Système."
            ),
        ),
        None => MACOS.is(Status::Warn, "Je n'ai pas pu lire la version de macOS."),
    }
}

/// Debian, Ubuntu and their derivatives are where the app was tried; elsewhere it says so,
/// without blocking: the paquets check says what the system lacks.
#[cfg(target_os = "linux")]
fn check_linux(f: &LinuxFacts) -> Check {
    match (&f.name, f.debian) {
        (Some(name), true) => LINUX.is(Status::Ok, name.clone()),
        (Some(name), false) => LINUX.is(
            Status::Warn,
            format!("{name} : je ne l'ai pas encore essayé (Ubuntu et Debian oui). Ça devrait marcher ; s'il manque des outils, je te le dis."),
        ),
        (None, _) => LINUX.is(Status::Warn, "Je n'ai pas pu lire le nom du système."),
    }
}

#[cfg(windows)]
fn check_windows(version: Option<&WindowsVersion>) -> Check {
    match version {
        Some(v) if v.build >= MIN_WINDOWS_BUILD => WINDOWS.is(Status::Ok, v.name()),
        Some(v) => WINDOWS.is(
            Status::Error,
            format!(
                "{} est trop ancien. Il faut Windows 10 version 1809 ou plus : mets ton PC à jour dans Windows Update.",
                v.name()
            ),
        ),
        None => WINDOWS.is(Status::Warn, "Je n'ai pas pu lire la version de Windows."),
    }
}

fn check_disk(free_gb: Option<f64>) -> Check {
    match free_gb {
        Some(gb) if gb < DISK_BLOCKING_GB => DISK.is(
            Status::Error,
            format!(
                "Il reste {} Go, il en faut au moins 3. Fais un peu de place, puis relance.",
                fmt_gb(gb)
            ),
        ),
        Some(gb) if gb < DISK_TIGHT_GB => DISK.is(
            Status::Warn,
            format!("Il reste {} Go. Ça passe, mais c'est juste.", fmt_gb(gb)),
        ),
        Some(gb) => DISK.is(Status::Ok, format!("{} Go libres", fmt_gb(gb))),
        None => DISK.is(Status::Warn, "Je n'ai pas pu mesurer l'espace libre."),
    }
}

/// The sites the install needs: github.com, then each agent's (claude.ai, chatgpt.com), in the order
/// of the agents. Claude alone: github.com and claude.ai, as before Codex.
fn hosts(f: &Facts) -> Vec<(&'static str, bool)> {
    let mut hosts = vec![("github.com", f.github.is_some())];
    hosts.extend(f.agents.order.iter().map(|e| match e {
        Engine::Claude => ("claude.ai", f.claude_ai),
        Engine::Codex => ("chatgpt.com", f.chatgpt),
    }));
    hosts
}

/// Blocks when none of the sites answers, or when a filter answers for github.com (`filtered`: a 403),
/// where the brain comes from: only github.com's, as claude.ai turns curl away with a 403 of its own.
/// `slow`: what a slow network means for the downloads left.
fn check_network(hosts: &[(&str, bool)], filtered: bool, slow: Option<&str>) -> Check {
    if filtered {
        return NETWORK.is(
            Status::Error,
            "Ce réseau bloque github.com, d'où je télécharge ton cerveau. Au travail ou à l'école, demande au \
             service informatique d'autoriser github.com. Sinon, connecte-toi à un autre réseau, puis clique sur Revérifier.",
        );
    }
    let names = |up: bool| hosts.iter().filter(|(_, ok)| *ok == up).map(|(h, _)| h.to_string()).collect::<Vec<_>>();
    let (up, down) = (names(true), names(false));
    if down.is_empty() {
        let verb = if up.len() == 1 { "répond" } else { "répondent" };
        return match slow {
            Some(note) => NETWORK.is(Status::Warn, format!("{} {verb}, mais lentement : {note}", steps::list_fr(&up))),
            None => NETWORK.is(Status::Ok, format!("{} {verb}.", steps::list_fr(&up))),
        };
    }
    if up.is_empty() {
        let none = match down.as_slice() {
            [one] => format!("{one} ne répond pas"),
            _ => format!("{} ne répondent", down.iter().map(|h| format!("ni {h}")).collect::<Vec<_>>().join(if down.len() == 2 { " " } else { ", " })),
        };
        return NETWORK.is(Status::Error, format!("Pas d'internet : {none}. Vérifie ton Wi-Fi, puis relance."));
    }
    let verb = if down.len() == 1 { "ne répond pas" } else { "ne répondent pas" };
    NETWORK.is(Status::Warn, format!("{} {verb}. Une étape risque d'échouer.", steps::list_fr(&down)))
}

#[cfg(target_os = "macos")]
fn check_clt(found: bool) -> Check {
    if found {
        CLT.is(Status::Ok, "git et python3 sont là.")
    } else {
        CLT.is(
            Status::Missing,
            "Pas encore installés. Apple s'en charge, il suffit de cliquer.",
        )
    }
}

/// What the kit needs from the system. On Debian and Ubuntu the paquets step installs it,
/// with the user's password; elsewhere the user does, with the system's own tool.
#[cfg(target_os = "linux")]
fn check_paquets(f: &LinuxFacts) -> Check {
    let listed = |pkgs: &[&str]| crate::steps::list_fr(&pkgs.iter().map(|p| p.to_string()).collect::<Vec<_>>());
    match f.python3 {
        Some(v) if v < detect::MIN_PYTHON3 => {
            return PAQUETS.is(
                Status::Error,
                format!(
                    "python3 {} est trop ancien : il faut {} ou plus. Mets ton système à jour, puis clique sur Revérifier.",
                    fmt_version(v),
                    fmt_version(detect::MIN_PYTHON3)
                ),
            )
        }
        _ => {}
    }
    match (f.missing.as_slice(), f.debian) {
        ([], _) => PAQUETS.is(Status::Ok, "git, python3, curl et unzip sont là."),
        (missing, true) => PAQUETS.is(
            Status::Missing,
            format!("Il manque {}. Je les installe : ton ordinateur te demandera ton mot de passe.", listed(missing)),
        ),
        (missing, false) => PAQUETS.is(
            Status::Error,
            format!(
                "Il manque {} : installe-les avec le gestionnaire de paquets de ton système, puis clique sur Revérifier.",
                listed(missing)
            ),
        ),
    }
}

/// Python and Git: install.ps1 takes them from winget, else from their official installers (its
/// Official-Install, a test holds it there).
#[cfg(windows)]
const INSTALLED_FOR_YOU: &str = "Pas encore installé : je l'installe pour toi.";

/// The Microsoft Store's python.exe does not count: it runs nothing.
#[cfg(windows)]
fn check_python(found: Option<&detect::Python>) -> Check {
    match found {
        Some(p) => Check {
            path: Some(p.exe.to_string_lossy().into_owned()),
            ..PYTHON.is(Status::Ok, format!("Python {}", fmt_version(p.version)))
        },
        None => PYTHON.is(Status::Missing, INSTALLED_FOR_YOU),
    }
}

/// Found is installed, as for install.ps1, even when it does not tell its version.
#[cfg(windows)]
fn check_git(found: &Found) -> Check {
    match found {
        Found::Absent => GIT.is(Status::Missing, INSTALLED_FOR_YOU),
        _ if found.version().is_some() => GIT.is(Status::Ok, found.label("Git")),
        _ => GIT.is(Status::Ok, "Git est installé."),
    }
}

/// Without winget, install.ps1 brings Obsidian no more and says so at the end.
#[cfg(windows)]
fn check_obsidian_windows(found: &Found, winget: bool) -> Check {
    match found {
        Found::Absent if !winget => OBSIDIAN.is(
            Status::Warn,
            "Pas installé : je ne peux pas l'installer sans l'outil d'installation de Microsoft (voir sa ligne). Tu \
             peux aussi le télécharger toi-même, gratuitement, sur obsidian.md.",
        ),
        _ => check_obsidian(found),
    }
}

/// install.ps1 takes Obsidian from winget alone: without it, the install goes on without Obsidian,
/// said in orange with the Store's way to get it. There but silent (its first run on a new profile is
/// slow): orange, and a minute to wait. Windows' own names, in French and in English: a PC may be
/// either.
#[cfg(windows)]
fn check_winget(obsidian_missing: bool, winget: Option<&Found>) -> Option<Check> {
    let winget = winget.filter(|w| obsidian_missing && !w.answers())?;
    let store = "Ouvre le Microsoft Store, cherche « Programme d'installation d'application (App Installer) » et clique \
                 sur « Obtenir (Get) » ou « Mettre à jour (Update) ». S'il n'y a que « Ouvrir (Open) », redémarre ton \
                 PC. Puis clique sur Revérifier.";
    Some(match winget {
        Found::Absent => WINGET.is(
            Status::Warn,
            format!("Il me manque l'outil d'installation de Microsoft pour installer Obsidian : je continue sans. {store}"),
        ),
        _ => WINGET.is(
            Status::Warn,
            format!(
                "L'outil d'installation de Microsoft ne répond pas encore : il me sert à installer Obsidian. Attends une \
                 minute, puis clique sur Revérifier. Toujours rien ? {store}"
            ),
        ),
    })
}

fn check_bun(found: &Found) -> Check {
    if detect::bun_ok(found) {
        return BUN.is(Status::Ok, found.label("Bun"));
    }
    match (found, found.version()) {
        (Found::Absent, _) => BUN.is(Status::Missing, "Pas encore installé."),
        (_, Some(v)) => BUN.is(
            Status::Missing,
            format!(
                "Bun {} est trop ancien (il faut {} ou plus). Je vais le mettre à jour.",
                fmt_version(v),
                fmt_version(detect::MIN_BUN)
            ),
        ),
        (_, None) => BUN.is(Status::Missing, "Bun est là mais ne répond pas. Je vais le réinstaller."),
    }
}

fn check_claude(found: &Found) -> Check {
    match found {
        Found::Version(_) if found.version().is_some() => CLAUDE.is(Status::Ok, found.label("Claude Code")),
        Found::Version(_) => CLAUDE.is(Status::Ok, "Claude Code est installé."),
        Found::NoVersion => CLAUDE.is(
            Status::Missing,
            "Claude Code est là mais ne répond pas. Je vais le réinstaller.",
        ),
        Found::Absent => CLAUDE.is(Status::Missing, "Pas encore installé."),
    }
}

/// Only a subscription is used (never a login billed per use). A claude.ai account whose plan Claude
/// Code does not say (free, or not read yet) and a login billed per use are said, without blocking:
/// a developer billed on purpose installs all the same. The window has no button to change accounts:
/// the person who advised Bibo can.
fn check_claude_auth(claude_works: bool, login: ClaudeLogin) -> Check {
    match (claude_works, login) {
        (false, _) => CLAUDE_AUTH.is(Status::Missing, "Tu te connecteras une fois Claude Code installé."),
        (true, ClaudeLogin::Subscribed) => CLAUDE_AUTH.is(Status::Ok, "Tu es connecté."),
        (true, ClaudeLogin::Unconfirmed) => CLAUDE_AUTH.is(
            Status::Warn,
            "Tu es connecté, mais Claude ne me dit pas quel est ton abonnement. Ton cerveau demande Claude Pro \
             (environ 20\u{a0}€ par mois) ou Max : un compte gratuit ne suffit pas. Si tu as Pro ou Max, tout va bien.",
        ),
        (true, ClaudeLogin::LoggedOut) => CLAUDE_AUTH.is(Status::Missing, "Pas encore connecté. Tu le feras en un clic."),
        (true, ClaudeLogin::Billed) => CLAUDE_AUTH.is(
            Status::Warn,
            "Claude Code est connecté à un compte payé à chaque message (clé API, passerelle…), pas à un abonnement : \
             je ne m'en sers ni pour le guide ni pour le résumé du soir. Rien ne bloque : tout le reste s'installe. \
             Pour passer à ton abonnement Claude Pro ou Max, demande de l'aide à la personne qui t'a conseillé Bibo.",
        ),
    }
}

/// Codex as the kit's launcher takes it: an older one is the user's to update (install.sh keeps it too).
fn check_codex(found: &Found) -> Check {
    match found {
        Found::Absent => CODEX.is(Status::Missing, "Pas encore installé."),
        Found::NoVersion => CODEX.is(Status::Missing, "Codex est là mais ne répond pas. Je vais le réinstaller."),
        _ if detect::codex_ok(found) => CODEX.is(Status::Ok, found.label("Codex")),
        _ => Check {
            fix: None,
            ..CODEX.is(
                Status::Warn,
                match found.version() {
                    Some(v) => format!("Codex {} est trop ancien pour le kit (il faut {} ou plus) : mets-le à jour.", fmt_version(v), min_codex()),
                    None => "Je n'arrive pas à lire la version de Codex : mets-le à jour.".into(),
                },
            )
        },
    }
}

fn min_codex() -> String {
    let (major, minor, patch) = codex::MIN_CODEX;
    format!("{major}.{minor}.{patch}")
}

/// How Codex is logged in (codex::login, §4.3): only a ChatGPT account of a personal plan is used,
/// never an API key, and the app never logs anyone out.
fn check_codex_auth(c: &CodexFacts) -> Check {
    use codex::Login;
    let (status, detail) = match c.login {
        _ if !c.found.answers() => (Status::Warn, "Tu te connecteras une fois Codex installé.".to_string()),
        Login::Chatgpt => (Status::Ok, "Tu es connecté avec ton compte ChatGPT.".into()),
        Login::LoggedOut => (Status::Warn, "Pas encore connecté. Tu le feras en un clic, avec ton compte ChatGPT.".into()),
        Login::Billed => (
            Status::Warn,
            "Codex est connecté avec une clé API, qui serait facturée : je ne m'en sers pas. \
             Connecte-le à ton compte ChatGPT en un clic : ta connexion remplacera la clé."
                .into(),
        ),
        Login::Managed => (
            Status::Warn,
            "Codex est connecté avec un compte ChatGPT d'entreprise (Business, Enterprise, Edu) : je ne m'en sers pas. \
             Il faut un abonnement ChatGPT personnel."
                .into(),
        ),
        Login::System => (
            Status::Warn,
            format!(
                "Codex lit des réglages de ce {} qu'un autre compte peut modifier (le dossier ProgramData\\OpenAI\\Codex) : \
                 je ne m'en sers pas tant que ce dossier n'est pas protégé.",
                sys::MACHINE
            ),
        ),
        Login::Unknown if !c.features => (Status::Warn, "Je regarderai ta connexion une fois ton cerveau installé.".into()),
        Login::Unknown => (Status::Warn, "Je n'ai pas pu lire ta connexion à Codex.".into()),
    };
    CODEX_AUTH.is(status, detail)
}

/// A choice the kit cannot trust (agents.rs): no agent is wired, nothing is installed.
fn check_agents(etat: &str) -> Check {
    AGENTS.is(
        Status::Error,
        format!(
            "Je ne sais plus avec quelle IA tu travailles : ton choix (~/.brain-in-a-box/agents.json) {}. \
             Je n'installe rien : écris à l'équipe, elle t'aidera.",
            steps::agents_said(etat)
        ),
    )
}

fn check_obsidian(found: &Found) -> Check {
    match found {
        Found::Absent => OBSIDIAN.is(Status::Missing, "Pas encore installé."),
        // Shown as parsed, like the other tools: the raw plist string never reaches the window.
        _ if found.version().is_some() => OBSIDIAN.is(Status::Ok, found.label("Obsidian")),
        _ => OBSIDIAN.is(Status::Ok, "Obsidian est installé."),
    }
}

/// Linux: where its official package does not fit (ARM, another family than Debian's),
/// Obsidian is the user's to install, and nothing waits on it.
#[cfg(target_os = "linux")]
fn check_obsidian_linux(found: &Found, installable: bool) -> Check {
    match found {
        Found::Absent if !installable => Check {
            fix: None,
            ..OBSIDIAN.is(
                Status::Warn,
                "Pas de paquet officiel pour ce système : installe-le depuis obsidian.md ou Flathub pour feuilleter tes notes.",
            )
        },
        _ => check_obsidian(found),
    }
}

/// `at`: the brain's folder as the window says it (detect::brain_dir_shown). The parts of each agent
/// agents.json lists, Claude's then Codex's, between the vault and the memory. `muets`: Codex keeps
/// sessions without running the kit's hooks.
fn check_brain(brain: Brain, at: &str, muets: bool) -> Vec<Check> {
    let piece = |item: Item, present: bool, ok: &str, missing: &str| {
        if present {
            item.is(Status::Ok, ok)
        } else {
            item.is(Status::Missing, missing)
        }
    };
    let mut checks = vec![piece(BRAIN_VAULT, brain.vault, &format!("Il est dans {at}."), "Pas encore créé.")];
    if let Some(claude) = brain.claude {
        checks.extend([
            piece(BRAIN_HOOKS, claude.hooks, "Claude note tes corrections et tes séances.", "Pas encore branchés."),
            piece(BRAIN_SKILLS, claude.skills, "Installées.", "Pas encore installées."),
        ]);
    }
    if let Some(codex) = brain.codex {
        // Approving them is Codex's own (/hooks): the brain step would not change it.
        let hooks = match piece(BRAIN_CODEX_HOOKS, codex.hooks, "Codex note tes corrections et tes séances.", "Pas encore branchés.") {
            c if c.status == Status::Ok && muets => {
                Check { fix: None, ..BRAIN_CODEX_HOOKS.is(Status::Warn, "Codex ne les lance pas : ouvre Codex, tape /hooks et approuve-les.") }
            }
            c => c,
        };
        checks.extend([
            hooks,
            piece(BRAIN_CODEX_INSTRUCTIONS, codex.instructions, "Codex les lit à chaque séance.", "Pas encore ajoutées."),
            piece(BRAIN_CODEX_SKILLS, codex.skills, "Installées.", "Pas encore installées."),
        ]);
    }
    checks.push(piece(BRAIN_MEMORY, brain.memory, "Prête.", "Pas encore créée."));
    checks
}

/// The model name ("voyage:voyage-4") means nothing to the user: on or off is enough. Off is no
/// worry: an option, offered after the install.
fn check_voyage(model: Option<&str>) -> Check {
    match model {
        Some(m) if m.starts_with("voyage:") => VOYAGE.is(Status::Ok, "Activée (Voyage AI)"),
        Some(_) => VOYAGE.is(Status::Ok, "Activée"),
        None => VOYAGE.is(
            Status::Missing,
            "Ton cerveau retrouve tes notes par mots-clés. La recherche par le sens est une option, à ajouter après si tu veux.",
        ),
    }
}

/// Roughly what each piece left to install downloads, in Mo: the installers' files as they weighed
/// in October 2026 (Claude Code 2.1.308, Codex 0.162.1, Obsidian 1.14.4, Bun 1.4.3, Git 2.56,
/// Python 3.12), Apple's tools, Debian's packages, and gbrain with its own packages.
fn piece_mb(id: &str) -> u32 {
    match id {
        "claude" => 300,
        "codex" => 100,
        "clt" => 800,
        "paquets" => 50,
        "python" => 30,
        "git" => 70,
        "bun" => 45,
        "obsidian" if cfg!(windows) => 340,
        "obsidian" if cfg!(target_os = "macos") => 235,
        "obsidian" => 110,
        "brain_memory" => 100,
        _ => 0,
    }
}

/// What the steps left download: each missing piece they bring, and the kit's archive when the
/// brain step runs.
fn download_mb(checks: &[Check], todo: &[String]) -> u32 {
    let runs = |fix: &Option<String>| fix.as_ref().is_some_and(|f| todo.contains(f));
    let pieces: u32 = checks.iter().filter(|c| c.status == Status::Missing && runs(&c.fix)).map(|c| piece_mb(&c.id)).sum();
    pieces + if todo.iter().any(|s| s == "brain") { 10 } else { 0 }
}

/// The steps' own work (installers, the brain's memory), then the downloads at the speed measured, or
/// at a usual one.
fn minutes(todo: &[String], download_mb: u32, speed: Option<f64>) -> u32 {
    if todo.is_empty() {
        return 0;
    }
    let work: u32 = todo
        .iter()
        .map(|s| match s.as_str() {
            "brain" => 8,
            "clt" => 5,
            "claude" | "codex" | "paquets" => 2,
            _ => 1,
        })
        .sum();
    let download = f64::from(download_mb) * 1e6 / speed.unwrap_or(USUAL_BYTES_PER_S).max(1.0) / 60.0;
    work + download.ceil() as u32
}

/// « environ 15 minutes », « environ 1 h 30 », « plusieurs heures »: rounded, a no-break space
/// between a number and its unit.
fn fmt_minutes(m: u32) -> String {
    match m {
        0..=57 => format!("environ {}\u{a0}minutes", ((m + 2) / 5 * 5).max(5)),
        58..=299 => match (m + 5) / 10 * 10 {
            r if r % 60 == 0 => format!("environ {}\u{a0}h", r / 60),
            r => format!("environ {}\u{a0}h\u{a0}{}", r / 60, r % 60),
        },
        _ => "plusieurs heures".into(),
    }
}

/// « 900 Mo », « 1,5 Go »: rounded up (after « environ »), a no-break space before the unit.
fn fmt_mb(mb: u32) -> String {
    let fifties = mb.div_ceil(50).max(1) * 50;
    if fifties < 1000 {
        return format!("{fifties}\u{a0}Mo");
    }
    let tenths = mb.div_ceil(100);
    match tenths % 10 {
        0 => format!("{}\u{a0}Go", tenths / 10),
        d => format!("{},{d}\u{a0}Go", tenths / 10),
    }
}

/// « Claude et ton cerveau »: the agents by name, then what the other steps bring.
fn installing(todo: &[String]) -> String {
    let mut parts: Vec<String> = Vec::new();
    for step in todo {
        let part = match step.as_str() {
            "claude" => "Claude",
            "codex" => "Codex",
            "brain" => "ton cerveau",
            "clt" => "les outils d'Apple",
            "paquets" => "les outils de base",
            "bun" => "le moteur de la mémoire",
            "obsidian" => "Obsidian",
            _ => continue,
        };
        parts.push(part.into());
    }
    // The brain step brings the tools along: « ton cerveau » says them all.
    if todo.iter().any(|s| s == "brain") {
        parts.retain(|p| ["Claude", "Codex", "ton cerveau"].contains(&p.as_str()));
    }
    steps::list_fr(&parts)
}

/// The sentence over the list: what blocks, else that all is there, else what gets installed, how
/// long it takes and how much it downloads.
fn headline(checks: &[Check], todo: &[String], download_mb: u32, minutes: u32, slow: bool) -> String {
    let machine = sys::MACHINE;
    match checks.iter().filter(|c| c.status == Status::Error).count() {
        0 => {}
        1 => return "Une chose bloque l'installation : la ligne en rouge dit quoi faire. Ensuite, clique sur Revérifier.".into(),
        _ => return "Des choses bloquent l'installation : les lignes en rouge disent quoi faire. Ensuite, clique sur Revérifier.".into(),
    }
    if todo.is_empty() {
        return format!("Tout est déjà installé sur ton {machine}.");
    }
    let (what, time, size) = (installing(todo), fmt_minutes(minutes), fmt_mb(download_mb));
    if slow {
        format!(
            "Ton {machine} est prêt, mais ta connexion est lente. J'installe {what} : prévois {time}, avec {size} \
             à télécharger. En données mobiles, attends d'avoir un Wi-Fi."
        )
    } else {
        format!("Ton {machine} est prêt. J'installe {what} : {time} et {size} à télécharger.")
    }
}

/// "Available" column of `df -k`: the field just before the first "NN%", which
/// still holds when the filesystem name has spaces ("map auto_home").
#[cfg(unix)]
fn parse_df_available_kb(output: &str) -> Option<u64> {
    let fields: Vec<&str> = output.lines().skip(1).flat_map(str::split_whitespace).collect();
    let capacity = fields.iter().position(|f| f.ends_with('%'))?;
    fields.get(capacity.checked_sub(1)?)?.parse().ok()
}

/// Decimal gigabytes, the unit the Finder shows.
fn kb_to_gb(kb: u64) -> f64 {
    kb as f64 * 1024.0 / 1e9
}

fn round1(x: f64) -> f64 {
    (x * 10.0).round() / 10.0
}

fn floor1(x: f64) -> f64 {
    (x * 10.0).floor() / 10.0
}

/// "174", or "2,5" below 10 (French decimal comma). Rounded down, so that
/// 2.99 never reads "3,0" next to "il en faut au moins 3".
fn fmt_gb(gb: f64) -> String {
    if gb >= 10.0 {
        format!("{}", gb.floor())
    } else {
        format!("{:.1}", floor1(gb)).replace('.', ",")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detect::tests::fake_brain;

    fn by_id<'a>(d: &'a Diagnosis, id: &str) -> &'a Check {
        d.checks
            .iter()
            .find(|c| c.id == id)
            .unwrap_or_else(|| panic!("no check {id}"))
    }

    fn healthy_facts() -> Facts {
        Facts {
            #[cfg(target_os = "macos")]
            macos: Some("26.3.1".into()),
            #[cfg(target_os = "linux")]
            linux: LinuxFacts {
                name: Some("Ubuntu 24.04.1 LTS".into()),
                debian: true,
                missing: Vec::new(),
                python3: Some((3, 12, 3)),
                obsidian_installable: true,
            },
            #[cfg(windows)]
            windows: Some(WindowsVersion { build: 26100, release: Some("24H2".into()) }),
            arch: "arm64".into(),
            ram_bytes: Some(25_769_803_776),
            disk_free_kb: Some(169_941_844),
            github: Some(200),
            speed: None,
            claude_ai: true,
            #[cfg(target_os = "macos")]
            clt: true,
            #[cfg(windows)]
            python: Some(detect::Python { exe: r"C:\Users\jo\AppData\Local\Programs\Python\Python312\python.exe".into(), version: (3, 12, 10) }),
            #[cfg(windows)]
            git: Found::Version("git version 2.51.0.windows.1".into()),
            #[cfg(windows)]
            winget: None,
            bun: Found::Version("1.4.2".into()),
            claude: Found::Version("2.1.296 (Claude Code)".into()),
            claude_login: ClaudeLogin::Subscribed,
            advice: advice::Facts { claude: true, claude_connecte: true, ..advice::Facts::default() },
            obsidian: Found::Version("1.14.4".into()),
            agents: Agents::claude_alone(),
            chatgpt: false,
            codex: None,
        }
    }

    #[test]
    fn the_advice_comes_with_the_checks() {
        let tmp = tempfile::tempdir().unwrap();
        let d = decide(tmp.path(), &healthy_facts());
        let a = d.advice.expect("an advice");
        assert_eq!(a.conseil, "claude");
        assert!(a.text.starts_with("Tu as déjà Claude Code, connecté"), "{}", a.text);
        assert!(a.hermes.is_none());
        let hermes = Facts { advice: advice::Facts { hermes: true, ..healthy_facts().advice }, ..healthy_facts() };
        assert!(decide(tmp.path(), &hermes).advice.unwrap().hermes.is_some());
        // The JSON the window and --diagnose get.
        let v = serde_json::to_value(decide(tmp.path(), &healthy_facts())).unwrap();
        assert_eq!(v["advice"]["conseil"], "claude");
        assert!(failed("x").advice.is_none());
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn macos_13_is_the_floor() {
        assert_eq!(check_macos(Some("12.7.6")).status, Status::Error);
        assert_eq!(check_macos(Some("13.0")).status, Status::Ok);
        assert_eq!(check_macos(Some("26.3.1")).detail, "macOS 26.3.1");
        assert_eq!(check_macos(None).status, Status::Warn);
        assert_eq!(check_macos(Some("garbage")).status, Status::Warn);
    }

    #[test]
    fn disk_thresholds() {
        assert_eq!(check_disk(Some(2.99)).status, Status::Error);
        assert_eq!(check_disk(Some(3.0)).status, Status::Warn);
        assert_eq!(check_disk(Some(9.99)).status, Status::Warn);
        assert_eq!(check_disk(Some(10.0)).status, Status::Ok);
        assert_eq!(check_disk(None).status, Status::Warn);
        assert!(check_disk(Some(2.5)).detail.contains("2,5 Go"));
        assert!(check_disk(Some(2.99)).detail.contains("2,9 Go"));
        assert_eq!(check_disk(Some(174.02)).detail, "174 Go libres");
    }

    #[cfg(unix)]
    #[test]
    fn reads_free_space_from_df() {
        let mac = "Filesystem   1024-blocks      Used Available Capacity iused      ifree %iused  Mounted on\n\
                   /dev/disk3s5   482797652 271864108 169941844    62% 5700672 1699418440    0%   /System/Volumes/Data\n";
        assert_eq!(parse_df_available_kb(mac), Some(169_941_844));
        let spaced = "Filesystem 1024-blocks Used Available Capacity Mounted on\n\
                      map auto_home 0 0 0 100% /System/Volumes/Data/home\n";
        assert_eq!(parse_df_available_kb(spaced), Some(0));
        assert_eq!(parse_df_available_kb("df: /nope: No such file or directory"), None);
        // GNU df -P (Linux).
        let gnu = "Filesystem     1024-blocks      Used Available Capacity Mounted on\n\
                   /dev/nvme0n1p2   490617784 211874012 253748420      46% /\n";
        assert_eq!(parse_df_available_kb(gnu), Some(253_748_420));
        assert!((kb_to_gb(169_941_844) - 174.02).abs() < 0.01);
    }

    #[test]
    fn network_blocks_only_when_both_sites_are_down() {
        let net = |github, claude_ai| {
            let c = check_network(&[("github.com", github), ("claude.ai", claude_ai)], false, None);
            (c.status, c.detail)
        };
        // Claude alone: word for word as before Codex.
        assert_eq!(net(true, true), (Status::Ok, "github.com et claude.ai répondent.".into()));
        assert_eq!(net(true, false), (Status::Warn, "claude.ai ne répond pas. Une étape risque d'échouer.".into()));
        assert_eq!(net(false, true), (Status::Warn, "github.com ne répond pas. Une étape risque d'échouer.".into()));
        assert_eq!(net(false, false), (Status::Error, "Pas d'internet : ni github.com ni claude.ai ne répondent. Vérifie ton Wi-Fi, puis relance.".into()));
        assert_eq!(http_code("403"), Some(403));
        assert_eq!(http_code("000"), None);
        assert_eq!(http_code(""), None);
    }

    /// Each agent's site; none answering blocks, one of them down warns.
    #[test]
    fn the_network_check_names_each_agents_site() {
        let three = |a: bool, b, c| check_network(&[("github.com", a), ("claude.ai", b), ("chatgpt.com", c)], false, None);
        assert_eq!(three(true, true, true).detail, "github.com, claude.ai et chatgpt.com répondent.");
        let down = three(true, false, false);
        assert_eq!((down.status, down.detail.as_str()), (Status::Warn, "claude.ai et chatgpt.com ne répondent pas. Une étape risque d'échouer."));
        let none = three(false, false, false);
        assert_eq!(
            (none.status, none.detail.as_str()),
            (Status::Error, "Pas d'internet : ni github.com, ni claude.ai, ni chatgpt.com ne répondent. Vérifie ton Wi-Fi, puis relance.")
        );
        assert_eq!(check_network(&[("github.com", true)], false, None).detail, "github.com répond.");
        let alone = check_network(&[("github.com", false)], false, None);
        assert_eq!((alone.status, alone.detail.as_str()), (Status::Error, "Pas d'internet : github.com ne répond pas. Vérifie ton Wi-Fi, puis relance."));
        let f = Facts { agents: codex_agents(vec![Engine::Codex, Engine::Claude]), chatgpt: true, ..healthy_facts() };
        assert_eq!(hosts(&f), [("github.com", true), ("chatgpt.com", true), ("claude.ai", true)]);
        assert_eq!(hosts(&healthy_facts()), [("github.com", true), ("claude.ai", true)]);
        let tmp = tempfile::tempdir().unwrap();
        assert!(!decide(tmp.path(), &Facts { chatgpt: false, ..f }).online, "Codex's site counts for the window's « en ligne »");
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn missing_apple_tools_are_installed_by_their_step() {
        assert_eq!(check_clt(true).status, Status::Ok);
        let missing = check_clt(false);
        assert_eq!((missing.status, missing.fix.as_deref()), (Status::Missing, Some("clt")));
    }

    #[test]
    fn old_bun_gets_reinstalled() {
        assert_eq!(check_bun(&Found::Version("1.4.0".into())).detail, "Bun 1.4.0");
        let old = check_bun(&Found::Version("1.3.9".into()));
        assert_eq!((old.status, old.fix.as_deref()), (Status::Missing, Some(OWN_STEP_OR_BRAIN[0])));
        assert!(old.detail.contains("1.3.9") && old.detail.contains("1.4.0"), "{}", old.detail);
        assert_eq!(check_bun(&Found::Absent).detail, "Pas encore installé.");
        assert!(check_bun(&Found::NoVersion).detail.contains("ne répond pas"));
        assert!(check_bun(&Found::Version("garbage".into())).detail.contains("ne répond pas"));
    }

    #[test]
    fn claude_and_its_login() {
        assert_eq!(
            check_claude(&Found::Version("2.1.296 (Claude Code)".into())).detail,
            "Claude Code 2.1.296"
        );
        assert_eq!(check_claude(&Found::Version("?".into())).status, Status::Ok);
        assert_eq!(check_claude(&Found::Absent).fix.as_deref(), Some("claude"));
        assert_eq!(check_claude(&Found::NoVersion).status, Status::Missing);
        // Not logged in yet: nothing to worry about, and no step of its own (the window asks).
        for (works, login) in [(false, ClaudeLogin::LoggedOut), (false, ClaudeLogin::Subscribed), (true, ClaudeLogin::LoggedOut)] {
            let c = check_claude_auth(works, login);
            assert_eq!((c.status, c.fix), (Status::Missing, None), "{works} {login:?}");
        }
        assert_eq!(check_claude_auth(true, ClaudeLogin::Subscribed).status, Status::Ok);
    }

    /// claude.ai without its plan: a free account, most often (Claude Code reads the plan when it logs
    /// in). Said in orange, with what the brain needs and its price, never blocking.
    #[test]
    fn a_claude_ai_account_without_a_plan_is_told_what_the_brain_needs() {
        let c = check_claude_auth(true, ClaudeLogin::Unconfirmed);
        assert_eq!((c.status, c.fix), (Status::Warn, None));
        for said in ["Tu es connecté", "Claude Pro (environ 20\u{a0}€ par mois) ou Max", "un compte gratuit ne suffit pas", "Si tu as Pro ou Max, tout va bien."] {
            assert!(c.detail.contains(said), "{said}: {}", c.detail);
        }
        let tmp = tempfile::tempdir().unwrap();
        fake_brain(tmp.path());
        let d = decide(tmp.path(), &Facts { claude_login: ClaudeLogin::Unconfirmed, ..healthy_facts() });
        assert!(d.can_install && d.todo.is_empty());
        assert_eq!(by_id(&d, "claude_auth").status, Status::Warn);
    }

    /// A login billed per use is not a subscription: said, never blocking, and the developer billed
    /// on purpose is told nothing stops.
    #[test]
    fn only_a_subscription_is_a_login() {
        let billed = check_claude_auth(true, ClaudeLogin::Billed);
        assert_eq!((billed.status, billed.fix), (Status::Warn, None));
        for said in ["payé à chaque message (clé API, passerelle…), pas à un abonnement", "Rien ne bloque", "la personne qui t'a conseillé Bibo"] {
            assert!(billed.detail.contains(said), "{said}: {}", billed.detail);
        }
        // No button is named so, and the window cannot change the account: the text must not send the
        // user looking for either.
        for wrong in ["Continue quand même", "Connecte plutôt", "avec une clé API"] {
            assert!(!billed.detail.contains(wrong), "{wrong}: {}", billed.detail);
        }
        let tmp = tempfile::tempdir().unwrap();
        fake_brain(tmp.path());
        let d = decide(tmp.path(), &Facts { claude_login: ClaudeLogin::Billed, ..healthy_facts() });
        assert!(d.can_install && d.todo.is_empty());
        assert_eq!(by_id(&d, "claude_auth").status, Status::Warn);
    }

    #[test]
    fn brain_pieces_point_to_the_brain_step() {
        use detect::{ClaudePart, CodexPart};
        let claude = ClaudePart { hooks: true, skills: true };
        let none = Brain { vault: false, memory: false, claude: Some(ClaudePart { hooks: false, skills: false }), codex: None, chosen: true };
        let ids = |checks: &[Check]| checks.iter().map(|c| c.id.clone()).collect::<Vec<_>>();
        let pieces = check_brain(none, "Documents/Brain", false);
        assert_eq!(ids(&pieces), ["brain_vault", "brain_hooks", "brain_skills", "brain_memory"], "Claude alone: as before Codex");
        for c in pieces {
            assert_eq!((c.status, c.fix.as_deref()), (Status::Missing, Some("brain")), "{}", c.id);
        }
        let all = Brain { vault: true, memory: true, claude: Some(claude), codex: None, chosen: true };
        for c in check_brain(all, "Documents/Brain", true) {
            assert_eq!(c.status, Status::Ok, "{}", c.id);
        }
        let hooks = check_brain(Brain { claude: Some(ClaudePart { hooks: false, ..claude }), ..all }, "Documents/Brain", false).remove(1);
        assert_eq!((hooks.id.as_str(), hooks.status), ("brain_hooks", Status::Missing));

        let codex = CodexPart { hooks: true, instructions: true, skills: true };
        let both = check_brain(Brain { codex: Some(codex), ..all }, "Documents/Brain", false);
        assert_eq!(
            ids(&both),
            ["brain_vault", "brain_hooks", "brain_skills", "brain_codex_hooks", "brain_codex_instructions", "brain_codex_skills", "brain_memory"]
        );
        assert!(both.iter().all(|c| c.status == Status::Ok));
        // Wired, but Codex runs none of them: its /hooks, not a step.
        let muets = check_brain(Brain { claude: None, codex: Some(codex), ..all }, "Documents/Brain", true).remove(1);
        assert_eq!((muets.id.as_str(), muets.status, muets.fix), ("brain_codex_hooks", Status::Warn, None));
        assert!(muets.detail.contains("/hooks"), "{}", muets.detail);
        let unwired = check_brain(Brain { claude: None, codex: Some(CodexPart { hooks: false, ..codex }), ..all }, "Documents/Brain", true).remove(1);
        assert_eq!((unwired.status, unwired.fix.as_deref()), (Status::Missing, Some("brain")), "not wired: the brain step wires them");
    }

    /// Nothing of the kit, nothing the install brings: a computer as it comes, its network fine.
    fn fresh_facts() -> Facts {
        Facts {
            #[cfg(target_os = "macos")]
            clt: false,
            #[cfg(target_os = "linux")]
            linux: LinuxFacts { missing: vec!["git", "curl"], ..healthy_facts().linux },
            #[cfg(windows)]
            python: None,
            #[cfg(windows)]
            git: Found::Absent,
            #[cfg(windows)]
            winget: Some(Found::Version("v1.9.25200".into())),
            bun: Found::Absent,
            claude: Found::Absent,
            claude_login: ClaudeLogin::LoggedOut,
            advice: advice::Facts::default(),
            obsidian: Found::Absent,
            ..healthy_facts()
        }
    }

    fn codex_agents(order: Vec<Engine>) -> Agents {
        let codex_home = order.contains(&Engine::Codex).then(|| std::path::PathBuf::from("/nowhere/.codex"));
        Agents { order, codex_home, etat: "ok".into() }
    }

    fn codex_facts(login: codex::Login) -> CodexFacts {
        CodexFacts { found: Found::Version("codex-cli 0.162.1".into()), login, features: true, hooks_muets: false }
    }

    /// Codex's checks and step where Claude's stand, for the agents agents.json lists: Codex alone has
    /// none of Claude's, and nothing of Claude is looked at.
    #[test]
    fn each_agent_listed_has_its_checks() {
        let tmp = tempfile::tempdir().unwrap();
        let codex_home = tmp.path().join(".codex");
        let ids = |d: &Diagnosis| d.checks.iter().map(|c| c.id.clone()).collect::<Vec<_>>();
        let with = |order: Vec<Engine>| Facts {
            agents: Agents { codex_home: Some(codex_home.clone()), ..codex_agents(order) },
            chatgpt: true,
            codex: Some(codex_facts(codex::Login::LoggedOut)),
            claude: Found::Absent,
            claude_login: ClaudeLogin::LoggedOut,
            ..healthy_facts()
        };
        let d = decide(tmp.path(), &with(vec![Engine::Codex]));
        let all = ids(&d);
        for id in ["codex", "codex_auth", "brain_vault", "brain_codex_hooks", "brain_codex_instructions", "brain_codex_skills", "brain_memory"] {
            assert!(all.contains(&id.to_string()), "{id} in {all:?}");
        }
        for id in ["claude", "claude_auth", "brain_hooks", "brain_skills", "agents"] {
            assert!(!all.contains(&id.to_string()), "{id} in {all:?}");
        }
        assert!(by_id(&d, "network").detail.contains("chatgpt.com") && !by_id(&d, "network").detail.contains("claude.ai"));
        assert_eq!(by_id(&d, "codex_auth").status, Status::Warn);
        assert_eq!(d.todo, ["brain"]);

        let missing =
            Facts { codex: Some(CodexFacts { found: Found::Absent, ..codex_facts(codex::Login::Unknown) }), ..with(vec![Engine::Codex, Engine::Claude]) };
        let d = decide(tmp.path(), &missing);
        assert_eq!(d.todo, ["codex", "claude", "brain"], "the agents' steps where Claude's stands, the preferred one first");
        assert_eq!(by_id(&d, "codex").fix.as_deref(), Some("codex"));
        let pos = |id: &str| ids(&d).iter().position(|x| x == id).unwrap();
        assert!(pos("codex") < pos("claude"), "the preferred one first");
        assert_eq!(by_id(&d, "codex_auth").detail, "Tu te connecteras une fois Codex installé.");
    }

    #[test]
    fn codex_is_used_only_as_the_kit_takes_it() {
        let ok = check_codex(&Found::Version("codex-cli 0.162.1".into()));
        assert_eq!((ok.status, ok.detail.as_str()), (Status::Ok, "Codex 0.162.1"));
        let old = check_codex(&Found::Version("codex-cli 0.150.0".into()));
        assert_eq!((old.status, old.fix), (Status::Warn, None), "an older one stays the user's, as install.sh keeps it");
        assert!(old.detail.contains("0.150.0") && old.detail.contains("0.162.1"), "{}", old.detail);
        assert_eq!(check_codex(&Found::Version("?".into())).status, Status::Warn);
        for (found, fix) in [(Found::Absent, "codex"), (Found::NoVersion, "codex")] {
            let c = check_codex(&found);
            assert_eq!((c.status, c.fix.as_deref()), (Status::Missing, Some(fix)), "{found:?}");
        }
        use codex::Login;
        let said = |login, features| check_codex_auth(&CodexFacts { features, ..codex_facts(login) });
        assert_eq!(said(Login::Chatgpt, true).status, Status::Ok);
        for login in [Login::LoggedOut, Login::Billed, Login::Managed, Login::System, Login::Unknown] {
            let c = said(login, true);
            assert_eq!((c.status, c.fix), (Status::Warn, None), "{login:?}");
        }
        assert!(said(Login::Billed, true).detail.contains("clé API") && said(Login::Billed, true).detail.contains("ChatGPT"));
        // No command to type: the login button replaces the key. A workspace account is said as one,
        // the system's settings as theirs (no login fixes those).
        assert!(!said(Login::Billed, true).detail.contains("codex logout"));
        assert!(said(Login::Managed, true).detail.contains("personnel") && !said(Login::Managed, true).detail.contains("administrateur"));
        assert!(said(Login::System, true).detail.contains("ProgramData") && !said(Login::System, true).detail.contains("Connecte"));
        assert!(said(Login::Unknown, false).detail.contains("une fois ton cerveau installé"));
        assert_ne!(said(Login::Unknown, false).detail, said(Login::Unknown, true).detail);
        let absent = check_codex_auth(&CodexFacts { found: Found::Absent, ..codex_facts(Login::Unknown) });
        assert_eq!(absent.detail, "Tu te connecteras une fois Codex installé.");
    }

    /// A choice the kit cannot trust: no agent is looked at, and nothing installs until it is made again.
    #[test]
    fn an_untrusted_choice_blocks_and_says_why() {
        let tmp = tempfile::tempdir().unwrap();
        fake_brain(tmp.path());
        let f = Facts { agents: Agents::none("ferme"), claude: Found::Absent, claude_login: ClaudeLogin::LoggedOut, ..healthy_facts() };
        let d = decide(tmp.path(), &f);
        assert!(!d.can_install);
        let a = by_id(&d, "agents");
        assert_eq!(a.status, Status::Error);
        assert!(a.detail.contains("demande Codex"), "{}", a.detail);
        let ids: Vec<&str> = d.checks.iter().map(|c| c.id.as_str()).collect();
        assert!(!ids.iter().any(|id| ["claude", "claude_auth", "brain_hooks", "codex"].contains(id)), "{ids:?}");
        assert_eq!(by_id(&d, "network").detail, "github.com répond.");
    }

    /// A choice made in the window that agents.json does not record yet: a brain otherwise complete is
    /// installed again, for the installer to record it; once recorded, nothing to do.
    #[test]
    fn a_choice_made_in_the_window_is_recorded_by_the_brain_step() {
        let tmp = tempfile::tempdir().unwrap();
        fake_brain(tmp.path());
        let picked = Agents { order: vec![Engine::Claude], codex_home: None, etat: agents::PENDING.into() };
        let d = decide(tmp.path(), &Facts { agents: picked.clone(), ..healthy_facts() });
        let a = by_id(&d, "agents");
        assert_eq!((a.status, a.fix.as_deref(), a.label.as_str()), (Status::Missing, Some("brain"), "Ton choix d'IA"));
        assert!(d.can_install);
        assert_eq!(d.todo, ["brain"]);
        // The brain step does not stop at « déjà installé » until the installer recorded it.
        assert!(!detect::brain(tmp.path(), &picked).complete());
        let recorded = Agents { etat: "ok".into(), ..picked };
        assert!(detect::brain(tmp.path(), &recorded).complete());
        let d = decide(tmp.path(), &Facts { agents: recorded, ..healthy_facts() });
        assert!(d.checks.iter().all(|c| c.id != "agents"), "{:?}", d.checks);
        assert!(d.todo.is_empty(), "{:?}", d.todo);
    }

    #[test]
    fn voyage_says_on_or_off_in_plain_words() {
        assert_eq!(check_voyage(Some("voyage:voyage-4")).detail, "Activée (Voyage AI)");
        assert_eq!(check_voyage(Some("zeroentropy:zembed-1")).detail, "Activée");
        // Off is an option for after the install, no worry: nothing orange, no step.
        let off = check_voyage(None);
        assert_eq!((off.status, off.fix), (Status::Missing, None));
        assert!(off.detail.contains("option") && off.detail.contains("après"), "{}", off.detail);
        let tmp = tempfile::tempdir().unwrap();
        fake_brain(tmp.path());
        let d = decide(tmp.path(), &healthy_facts());
        assert!(d.todo.is_empty() && d.can_install, "{:?}", d.todo);
    }

    /// What a fresh computer shows before the install: nothing orange nor red when all that is missing
    /// installs by itself, and a headline that says how long and how much to download.
    #[test]
    fn a_fresh_computer_has_nothing_orange_and_says_what_it_downloads() {
        let tmp = tempfile::tempdir().unwrap();
        let d = decide(tmp.path(), &fresh_facts());
        assert!(d.can_install, "{:?}", d.checks);
        let orange: Vec<&str> = d.checks.iter().filter(|c| c.status == Status::Warn).map(|c| c.id.as_str()).collect();
        assert!(orange.is_empty(), "{orange:?}");
        let machine = sys::MACHINE;
        assert!(
            d.headline.starts_with(&format!("Ton {machine} est prêt. J'installe Claude et ton cerveau : environ ")),
            "{}",
            d.headline
        );
        // A fast enough network: nothing said of Wi-Fi (a PC on a cable would wonder why).
        assert!(d.headline.ends_with(" à télécharger.") && !d.headline.contains("Wi-Fi"), "{}", d.headline);
        // Claude Code (300 Mo), the brain's memory and the kit, and each tool of the system.
        assert!(d.download_mb > 400, "{}", d.download_mb);
        assert!(d.headline.contains(&fmt_mb(d.download_mb)) && d.headline.contains(&fmt_minutes(d.minutes)), "{}", d.headline);
        assert!((10..=60).contains(&d.minutes), "{}", d.minutes);
        for c in &d.checks {
            for word in ["winget", "Bun (", "Ton dossier Brain"] {
                assert!(!c.label.contains(word) && !c.detail.contains(word), "{word} in {c:?}");
            }
        }
        assert_eq!(by_id(&d, "bun").label, "Le moteur de la mémoire");
        assert_eq!(by_id(&d, "brain_vault").label, "Le dossier de tes notes");
    }

    /// The other headlines: all there, something blocking, nothing measured.
    #[test]
    fn the_headline_says_what_blocks_or_that_all_is_there() {
        let tmp = tempfile::tempdir().unwrap();
        fake_brain(tmp.path());
        let d = decide(tmp.path(), &healthy_facts());
        assert_eq!((d.headline.as_str(), d.download_mb, d.minutes), (format!("Tout est déjà installé sur ton {}.", sys::MACHINE).as_str(), 0, 0));
        let offline = decide(tmp.path(), &Facts { github: None, claude_ai: false, ..healthy_facts() });
        assert_eq!(offline.headline, "Une chose bloque l'installation : la ligne en rouge dit quoi faire. Ensuite, clique sur Revérifier.");
        let both = decide(tmp.path(), &Facts { github: None, claude_ai: false, disk_free_kb: Some(1_000_000), ..healthy_facts() });
        assert!(both.headline.starts_with("Des choses bloquent l'installation : les lignes en rouge"), "{}", both.headline);
        assert!(failed("x").headline.contains("Revérifier"));
    }

    /// A filter that answers for github.com (403) blocks, naming it: the brain comes from there.
    /// claude.ai's 403 is its own, to curl: it proves the network works.
    #[test]
    fn a_filter_on_github_blocks_and_names_it() {
        let hosts = [("github.com", true), ("claude.ai", true)];
        let c = check_network(&hosts, true, None);
        assert_eq!((c.status, c.fix), (Status::Error, None));
        assert!(c.detail.contains("bloque github.com") && c.detail.contains("Revérifier"), "{}", c.detail);
        assert_eq!(check_network(&hosts, false, None).status, Status::Ok);
        let tmp = tempfile::tempdir().unwrap();
        fake_brain(tmp.path());
        let d = decide(tmp.path(), &Facts { github: Some(403), ..healthy_facts() });
        assert!(!d.can_install);
        assert_eq!(by_id(&d, "network").status, Status::Error);
        let ids: Vec<&str> = d.checks.iter().map(|c| c.id.as_str()).collect();
        assert_eq!(ids.iter().position(|id| *id == "network"), Some(2), "{ids:?}");
        for code in [200, 301, 429] {
            assert_eq!(by_id(&decide(tmp.path(), &Facts { github: Some(code), ..healthy_facts() }), "network").status, Status::Ok);
        }
    }

    /// github.com's 403, while GitHub's file came all the same (GitHub turning curl away, not a
    /// filter): the downloads pass, nothing blocks.
    #[test]
    fn a_403_from_github_blocks_nothing_when_its_file_comes() {
        let tmp = tempfile::tempdir().unwrap();
        let d = decide(tmp.path(), &Facts { github: Some(403), speed: Some(5_000_000.0), ..fresh_facts() });
        assert!(d.can_install, "{:?}", d.checks);
        assert_eq!(by_id(&d, "network").status, Status::Ok);
        let blocked = decide(tmp.path(), &Facts { github: Some(403), speed: None, ..fresh_facts() });
        assert_eq!(by_id(&blocked, "network").status, Status::Error);
    }

    /// Below 2 Mbit/s, the network check and the headline say how long the downloads take.
    #[test]
    fn a_slow_network_says_how_long() {
        let tmp = tempfile::tempdir().unwrap();
        let slow = Facts { speed: Some(100_000.0), ..fresh_facts() };
        let d = decide(tmp.path(), &slow);
        assert!(d.can_install);
        let net = by_id(&d, "network");
        assert_eq!(net.status, Status::Warn);
        assert!(net.detail.contains("mais lentement : prévois environ ") && net.detail.contains("En données mobiles"), "{}", net.detail);
        assert!(d.minutes > 60, "{}", d.minutes);
        // The time is the whole install's (downloads and work), the volume the downloads'.
        let whole = format!("prévois {} en tout, avec {} à télécharger.", fmt_minutes(d.minutes), fmt_mb(d.download_mb));
        assert!(net.detail.contains(&whole), "{}", net.detail);
        assert!(d.headline.contains("ta connexion est lente") && d.headline.contains("attends d'avoir un Wi-Fi"), "{}", d.headline);
        // « compte » would read as the Claude account.
        assert!(!net.detail.contains("compte") && !d.headline.contains("compte"), "{}", d.headline);
        // Past 5 h: « plusieurs heures », with no « environ » before it.
        let crawling = decide(tmp.path(), &Facts { speed: Some(20_000.0), ..fresh_facts() });
        for said in [&by_id(&crawling, "network").detail, &crawling.headline] {
            assert!(said.contains("prévois plusieurs heures") && !said.contains("environ plusieurs"), "{said}");
        }
        // Fast, or nothing left to download: nothing said of the speed.
        let fast = decide(tmp.path(), &Facts { speed: Some(5_000_000.0), ..fresh_facts() });
        assert_eq!(by_id(&fast, "network").status, Status::Ok);
        assert!(fast.minutes < d.minutes);
        fake_brain(tmp.path());
        let done = decide(tmp.path(), &Facts { speed: Some(100_000.0), ..healthy_facts() });
        assert_eq!(by_id(&done, "network").status, Status::Ok);
    }

    #[test]
    fn the_speed_is_read_from_curl() {
        // 2 Mo in 0.034 s after the first byte (counted as 0.05 s at least); 571 Ko in 5 s, cut by -m
        // (curl exits 28 and still says).
        assert!(parse_speed("206 2097152 0.112093 0.145865").is_some_and(|s| s > 40e6));
        let slow = parse_speed("206 584704 0.097472 5.056582").unwrap();
        assert!((117_000.0..119_000.0).contains(&slow), "{slow}");
        assert!(parse_speed("200 2097152 0.5 0.5").is_some_and(|s| s > 40e6), "no time measured: fast");
        for nothing in ["000 0 0 0", "403 1234 0.1 0.2", "206 0 0 5.0", "206 12", "", "abc 1 2 3"] {
            assert_eq!(parse_speed(nothing), None, "{nothing}");
        }
        // A satellite link: 3.6 s of handshakes and redirect, then 300 Ko in the 1.4 s left, still
        // speeding up. Nothing said, rather than « prévois environ 4 h » on a fast enough network.
        assert_eq!(parse_speed("206 300000 3.6 5.0"), None);
        assert_eq!(parse_speed("206 400000 5.1 8.0"), None);
        // Cut after 3 s of data or more, the speed holds.
        assert!(parse_speed("206 900000 4.0 8.0").is_some_and(|s| (220_000.0..230_000.0).contains(&s)));
    }

    /// The speed is measured on the file the Mac step downloads: moving Obsidian on moves both, and a
    /// file gone (404) would leave the measure silent for good.
    #[test]
    fn the_speed_is_measured_on_the_file_the_mac_step_downloads() {
        let steps = include_str!("steps.rs");
        assert!(steps.contains(&format!("const OBSIDIAN_URL: &str = \"{SPEED_URL}\";")), "{SPEED_URL}");
    }

    /// What the Windows lines say follows install.ps1: Python and Git « je l'installe pour toi », with or
    /// without winget, as it falls back on their official installers (#65); Obsidian from winget alone,
    /// hence the winget line. On every system: install.ps1 is the same file.
    #[test]
    fn the_winget_line_follows_install_ps1() {
        let ps1 = include_str!("../../../install.ps1");
        assert!(ps1.contains("function Official-Install"), "install.ps1 lost its fallback: Python and Git need winget again");
        for tool in ["Python", "Git"] {
            assert!(ps1.contains(&format!("= Install-Missing \"{tool}\"")), "{tool}");
        }
        assert!(ps1.contains("Winget-Install \"Obsidian.Obsidian\""));
    }

    #[test]
    fn durations_and_sizes_read_simply() {
        // A no-break space between a number and its unit (shown here as « _ »): « 20 » and « Mo »
        // never part at the end of a line. Past 5 h, no « environ ».
        let m = |n| fmt_minutes(n).replace('\u{a0}', "_");
        assert_eq!([m(1), m(12), m(13), m(57), m(58), m(95), m(120), m(299), m(300)], [
            "environ 5_minutes", "environ 10_minutes", "environ 15_minutes", "environ 55_minutes", "environ 1_h",
            "environ 1_h_40", "environ 2_h", "environ 5_h", "plusieurs heures"
        ]);
        let mb = |n| fmt_mb(n).replace('\u{a0}', "_");
        assert_eq!([mb(1), mb(30), mb(890), mb(950), mb(951), mb(999), mb(1000), mb(1480), mb(2000)], [
            "50_Mo", "50_Mo", "900_Mo", "950_Mo", "1_Go", "1_Go", "1_Go", "1,5_Go", "2_Go"
        ]);
        let todo = |s: &[&str]| s.iter().map(|x| x.to_string()).collect::<Vec<_>>();
        assert_eq!(installing(&todo(&["claude", "clt", "bun", "obsidian", "brain"])), "Claude et ton cerveau");
        assert_eq!(installing(&todo(&["brain"])), "ton cerveau");
        assert_eq!(installing(&todo(&["obsidian"])), "Obsidian");
        assert_eq!(installing(&todo(&["claude", "bun"])), "Claude et le moteur de la mémoire");
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn nothing_to_do_on_a_complete_mac() {
        let tmp = tempfile::tempdir().unwrap();
        fake_brain(tmp.path());
        let d = decide(tmp.path(), &healthy_facts());
        assert!(d.todo.is_empty(), "{:?}", d.todo);
        assert!(d.can_install && d.online);
        assert_eq!((d.os.as_str(), d.ram_gb, d.disk_free_gb), ("macOS 26.3.1", 24.0, 174.0));
        assert!(d.checks.iter().all(|c| c.status == Status::Ok), "{:?}", d.checks);
        assert_eq!(d.checks.len(), 13);
        assert_eq!(by_id(&d, "voyage").detail, "Activée (Voyage AI)");
        assert!(!format!("{d:?}").contains("SECRET"));
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn todo_follows_the_install_order() {
        let tmp = tempfile::tempdir().unwrap();
        let facts = Facts {
            clt: false,
            bun: Found::Version("1.2.0".into()),
            claude: Found::Absent,
            claude_login: ClaudeLogin::LoggedOut,
            ..healthy_facts()
        };
        let d = decide(tmp.path(), &facts);
        assert_eq!(d.todo, ["claude", "clt", "bun", "brain"]);
        assert!(d.can_install);
        // What has no step of its own never lands in the todo list.
        assert_eq!(by_id(&d, "claude_auth").fix, None);
        assert_eq!(by_id(&d, "voyage").fix, None);
    }

    #[test]
    fn an_error_blocks_the_install() {
        let tmp = tempfile::tempdir().unwrap();
        fake_brain(tmp.path());
        let old_system = Facts {
            #[cfg(target_os = "macos")]
            macos: Some("12.7.6".into()),
            #[cfg(target_os = "linux")]
            linux: LinuxFacts { python3: Some((3, 8, 10)), ..healthy_facts().linux },
            #[cfg(windows)]
            windows: Some(WindowsVersion { build: 17134, release: Some("1803".into()) }),
            ..healthy_facts()
        };
        assert!(!decide(tmp.path(), &old_system).can_install);
        let offline = Facts {
            github: None,
            claude_ai: false,
            ..healthy_facts()
        };
        let d = decide(tmp.path(), &offline);
        assert!(!d.can_install && !d.online);
        // 2.96 Go: the number and the sentence both say 2,9, never 3.0 next to "au moins 3".
        let full_disk = Facts {
            disk_free_kb: Some(2_890_625),
            ..healthy_facts()
        };
        let d = decide(tmp.path(), &full_disk);
        let disk = by_id(&d, "disk");
        assert_eq!((disk.status, d.disk_free_gb), (Status::Error, 2.9));
        assert!(disk.detail.contains("2,9 Go"), "{}", disk.detail);
        assert!(!d.can_install);
    }

    #[test]
    fn a_broken_diagnosis_blocks_with_a_readable_line() {
        let d = failed("task panicked");
        assert!(!d.can_install && d.todo.is_empty());
        let [c] = d.checks.as_slice() else { panic!("{:?}", d.checks) };
        assert_eq!((c.label.as_str(), c.status), ("Erreur interne", Status::Error));
        assert!(c.detail.contains("Revérifier"));
    }

    #[test]
    fn obsidian_shows_a_clean_version() {
        assert_eq!(
            check_obsidian(&Found::Version("1.14.4\n".into())).detail,
            "Obsidian 1.14.4"
        );
        assert_eq!(
            check_obsidian(&Found::Version("<b>".into())).detail,
            "Obsidian est installé."
        );
        assert_eq!(check_obsidian(&Found::NoVersion).status, Status::Ok);
        let absent = check_obsidian(&Found::Absent);
        let step = if cfg!(windows) { "brain" } else { "obsidian" };
        assert_eq!((absent.status, absent.fix.as_deref()), (Status::Missing, Some(step)));
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn nothing_to_do_on_a_complete_linux() {
        let tmp = tempfile::tempdir().unwrap();
        fake_brain(tmp.path());
        let d = decide(tmp.path(), &healthy_facts());
        assert!(d.todo.is_empty(), "{:?}", d.todo);
        assert!(d.can_install && d.online);
        assert_eq!((d.os.as_str(), d.ram_gb, d.disk_free_gb), ("Ubuntu 24.04.1 LTS", 24.0, 174.0));
        assert!(d.checks.iter().all(|c| c.status == Status::Ok), "{:?}", d.checks);
        let ids: Vec<&str> = d.checks.iter().map(|c| c.id.as_str()).collect();
        assert_eq!(
            ids,
            [
                "linux", "disk", "network", "paquets", "bun", "claude", "claude_auth", "obsidian", "brain_vault", "brain_hooks",
                "brain_skills", "brain_memory", "voyage"
            ]
        );
        assert!(!format!("{d:?}").contains("SECRET"));
    }

    /// A fresh Ubuntu desktop: no git nor curl, nothing of the kit. The system's tools come first.
    #[cfg(target_os = "linux")]
    #[test]
    fn a_fresh_ubuntu_gets_its_tools_first() {
        let tmp = tempfile::tempdir().unwrap();
        let facts = Facts {
            linux: LinuxFacts { missing: vec!["git", "curl"], ..healthy_facts().linux },
            bun: Found::Absent,
            claude: Found::Absent,
            claude_login: ClaudeLogin::LoggedOut,
            obsidian: Found::Absent,
            ..healthy_facts()
        };
        let d = decide(tmp.path(), &facts);
        assert_eq!(d.todo, ["paquets", "claude", "bun", "obsidian", "brain"]);
        assert!(d.can_install);
        let p = by_id(&d, "paquets");
        assert_eq!((p.status, p.fix.as_deref()), (Status::Missing, Some("paquets")));
        assert!(p.detail.contains("git et curl") && p.detail.contains("mot de passe"), "{}", p.detail);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn elsewhere_the_user_installs_what_the_system_lacks() {
        let tmp = tempfile::tempdir().unwrap();
        let fedora = LinuxFacts { name: Some("Fedora Linux 41".into()), debian: false, obsidian_installable: false, ..healthy_facts().linux };
        let d = decide(tmp.path(), &Facts { linux: fedora.clone(), obsidian: Found::Absent, ..healthy_facts() });
        assert!(d.can_install, "{:?}", d.checks);
        assert_eq!(by_id(&d, "linux").status, Status::Warn);
        // No official Obsidian package here: a word, no step.
        let o = by_id(&d, "obsidian");
        assert_eq!((o.status, o.fix.as_deref()), (Status::Warn, None));
        assert!(!d.todo.contains(&"obsidian".to_string()));
        let lacking = Facts { linux: LinuxFacts { missing: vec!["git"], ..fedora }, ..healthy_facts() };
        let d = decide(tmp.path(), &lacking);
        assert!(!d.can_install);
        assert!(by_id(&d, "paquets").detail.contains("gestionnaire de paquets"));
        assert_eq!(linux_arch("aarch64\n"), "arm64");
        assert_eq!(mem_total_bytes("MemTotal:       16314512 kB\nMemFree: 1 kB\n"), Some(16_314_512 * 1024));
        assert_eq!(mem_total_bytes("nothing"), None);
    }

    #[cfg(windows)]
    #[test]
    fn nothing_to_do_on_a_complete_pc() {
        let tmp = tempfile::tempdir().unwrap();
        fake_brain(tmp.path());
        let d = decide(tmp.path(), &healthy_facts());
        assert!(d.todo.is_empty(), "{:?}", d.todo);
        assert!(d.can_install && d.online);
        assert_eq!((d.os.as_str(), d.ram_gb, d.disk_free_gb), ("Windows 11 (24H2)", 24.0, 174.0));
        assert!(d.checks.iter().all(|c| c.status == Status::Ok), "{:?}", d.checks);
        let ids: Vec<&str> = d.checks.iter().map(|c| c.id.as_str()).collect();
        assert_eq!(
            ids,
            [
                "windows", "disk", "network", "python", "git", "bun", "claude", "claude_auth", "obsidian", "brain_vault",
                "brain_hooks", "brain_skills", "brain_memory", "voyage"
            ]
        );
        assert_eq!(by_id(&d, "python").detail, "Python 3.12.10");
        assert!(by_id(&d, "python").path.as_deref().is_some_and(|p| p.ends_with(r"Python312\python.exe")));
        assert_eq!(by_id(&d, "git").path, None);
        assert_eq!(by_id(&d, "git").detail, "Git 2.51.0");
        assert!(!format!("{d:?}").contains("SECRET"));
    }

    /// install.ps1 brings Python, Git, Bun and Obsidian along with the brain.
    #[cfg(windows)]
    #[test]
    fn install_ps1_is_the_brain_step() {
        let tmp = tempfile::tempdir().unwrap();
        let facts = Facts {
            python: None,
            git: Found::Absent,
            bun: Found::Version("1.2.0".into()),
            claude: Found::Absent,
            claude_login: ClaudeLogin::LoggedOut,
            obsidian: Found::Absent,
            ..healthy_facts()
        };
        let d = decide(tmp.path(), &facts);
        assert_eq!(d.todo, ["claude", "brain"]);
        for id in ["python", "git", "bun", "obsidian", "brain_vault"] {
            let c = by_id(&d, id);
            assert_eq!((c.status, c.fix.as_deref()), (Status::Missing, Some("brain")), "{id}");
        }
        assert_eq!(by_id(&d, "claude").fix.as_deref(), Some("claude"));
        assert_eq!(check_git(&Found::NoVersion).status, Status::Ok, "found is installed, as for install.ps1");
        assert_eq!(by_id(&d, "python").detail, "Pas encore installé : je l'installe pour toi.");
    }

    /// winget absent: Python and Git come all the same (install.ps1's official installers), Obsidian
    /// does not. Said in orange before the install, the Store's names in French and in English, and
    /// what will not come is not counted in the download.
    #[cfg(windows)]
    #[test]
    fn no_winget_is_said_before_the_install() {
        let tmp = tempfile::tempdir().unwrap();
        let d = decide(tmp.path(), &Facts { winget: Some(Found::Absent), ..fresh_facts() });
        assert!(d.can_install, "{:?}", d.checks);
        let w = by_id(&d, "winget");
        assert_eq!((w.status, w.fix.as_deref(), w.label.as_str()), (Status::Warn, None, "Outil d'installation de Microsoft"));
        assert!(w.detail.starts_with("Il me manque l'outil d'installation de Microsoft pour installer Obsidian : je continue sans."), "{}", w.detail);
        for said in [
            "« Programme d'installation d'application (App Installer) »", "« Obtenir (Get) »", "« Mettre à jour (Update) »",
            "S'il n'y a que « Ouvrir (Open) », redémarre ton PC.", "Revérifier",
        ] {
            assert!(w.detail.contains(said), "{said}: {}", w.detail);
        }
        for id in ["python", "git"] {
            assert_eq!((by_id(&d, id).status, by_id(&d, id).detail.as_str()), (Status::Missing, "Pas encore installé : je l'installe pour toi."), "{id}");
        }
        let o = by_id(&d, "obsidian");
        assert_eq!(o.status, Status::Warn);
        assert!(o.detail.contains("(voir sa ligne)") && o.detail.ends_with("Tu peux aussi le télécharger toi-même, gratuitement, sur obsidian.md."), "{}", o.detail);
        let with_winget = decide(tmp.path(), &Facts { winget: None, ..fresh_facts() });
        assert_eq!(d.download_mb + piece_mb("obsidian"), with_winget.download_mb);

        // winget there, or not looked for (Obsidian there): no line.
        for winget in [Some(Found::Version("v1.9.25200".into())), None] {
            let d = decide(tmp.path(), &Facts { winget, ..fresh_facts() });
            assert!(d.checks.iter().all(|c| c.id != "winget"), "{:?}", d.checks);
            assert!(d.can_install);
        }
        assert!(check_winget(false, Some(&Found::Absent)).is_none());

        // There but silent (its first run on a new profile outlasts the probe): install.ps1 tries it
        // all the same. Orange, a minute to wait.
        let d = decide(tmp.path(), &Facts { winget: Some(Found::NoVersion), ..fresh_facts() });
        assert!(d.can_install, "{:?}", d.checks);
        let w = by_id(&d, "winget");
        assert_eq!(w.status, Status::Warn);
        assert!(w.detail.contains("ne répond pas encore") && w.detail.contains("Attends une minute"), "{}", w.detail);
        assert_eq!(by_id(&d, "obsidian").status, Status::Missing);
    }

    #[cfg(windows)]
    #[test]
    fn windows_10_1809_is_the_floor() {
        let v = |build, release: Option<&str>| WindowsVersion { build, release: release.map(String::from) };
        assert_eq!(check_windows(Some(&v(17134, Some("1803")))).status, Status::Error);
        assert_eq!(check_windows(Some(&v(17763, Some("1809")))).detail, "Windows 10 (1809)");
        assert_eq!(check_windows(Some(&v(22631, Some("23H2")))).detail, "Windows 11 (23H2)");
        assert_eq!(check_windows(Some(&v(26100, None))).detail, "Windows 11");
        assert_eq!(check_windows(None).status, Status::Warn);
    }

    #[cfg(windows)]
    #[test]
    fn the_powershell_probe_is_read_strictly() {
        let out = "build=22631\r\nrelease=23H2\r\narch=ARM64\r\nram=17179869184\r\nfree=120000000000\r\n";
        let f = parse_windows_probe(out);
        assert_eq!(f.version, Some(WindowsVersion { build: 22631, release: Some("23H2".into()) }));
        assert_eq!((f.arch.as_str(), f.ram_bytes, f.free_bytes), ("arm64", Some(17_179_869_184), Some(120_000_000_000)));
        let odd = parse_windows_probe("build=\nrelease=<b>x</b>\narch=AMD64\nram=lots\nfree=-1\n");
        assert_eq!(odd, WindowsFacts { version: None, arch: "x86_64".into(), ram_bytes: None, free_bytes: None });
        assert_eq!(parse_windows_probe(""), WindowsFacts::default());
        assert!(!WINDOWS_PROBE.contains('"'), "the script travels as one argument");
    }
}
