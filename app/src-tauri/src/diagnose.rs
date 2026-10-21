//! Looks at the Mac (or the PC): what is there, what is missing, what blocks the install.
//!
//! `probe()` runs the system commands and gathers raw facts; `decide()` turns
//! them, plus the files under a given home, into checks. All the logic sits in
//! `decide()` and the `check_*` functions, so tests drive them with a fake home.
//!
//! On Windows, install.ps1 installs Python, Git, Bun and Obsidian along with the brain:
//! their checks point to the brain step. On Linux, the system's own tools (git, python3,
//! curl) come first, from the system's packages, as Apple's tools do on a Mac.
//!
//! The AI agents are the ones agents.json lists (agents.rs): Claude Code's checks, Codex's, or both,
//! each with its site in the network check and its part of the brain. Without the file, Claude's
//! alone, the checks of before Codex word for word.

use std::path::Path;
use std::thread;
use std::time::Duration;

use crate::agents::{self, Agents, Engine};
#[cfg(target_os = "macos")]
use crate::detect::parse_version;
#[cfg(unix)]
use crate::detect::Version;
use crate::detect::{self, fmt_version, Brain, Found};
use crate::model::{Check, Diagnosis, Status};
use crate::{advice, codex, hermes, steps, sys};

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
/// install.sh's Hermes takes 4.4 GB (21/10/2026): below this, it waits for room, the brain does not.
const HERMES_DISK_GB: f64 = 6.0;
const PROBE_TIMEOUT: Duration = Duration::from_secs(10);

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
/// The step that installs Bun and Obsidian: their own on a Mac, install.ps1's on Windows.
const OWN_STEP_OR_BRAIN: [&str; 2] = if cfg!(windows) { ["brain", "brain"] } else { ["bun", "obsidian"] };
const BUN: Item = Item("bun", "Bun (le moteur de la mémoire)", Some(OWN_STEP_OR_BRAIN[0]));
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
const BRAIN_VAULT: Item = Item("brain_vault", "Ton dossier Brain", Some("brain"));
const BRAIN_HOOKS: Item = Item("brain_hooks", "Les automatismes de Claude", Some("brain"));
const BRAIN_SKILLS: Item = Item("brain_skills", "Les compétences de Claude", Some("brain"));
const BRAIN_CODEX_HOOKS: Item = Item("brain_codex_hooks", "Les automatismes de Codex", Some("brain"));
const BRAIN_CODEX_INSTRUCTIONS: Item = Item("brain_codex_instructions", "Tes consignes pour Codex", Some("brain"));
const BRAIN_CODEX_SKILLS: Item = Item("brain_codex_skills", "Les compétences de Codex", Some("brain"));
const BRAIN_MEMORY: Item = Item("brain_memory", "La mémoire de recherche", Some("brain"));
const VOYAGE: Item = Item("voyage", "Recherche par le sens", None);
/// Hermes's line where nothing installs it: an Intel Mac, where it does not run, Windows and Linux.
const HERMES: Item = Item("hermes", "Hermes (ton IA au quotidien)", None);
/// On a Mac it runs on, the Hermes step installs it, repairs it, updates it and puts its app in place.
const HERMES_TO_INSTALL: Item = Item("hermes", "Hermes (ton IA au quotidien)", Some("hermes"));
const HERMES_AI: Item = Item("hermes_ia", "Connexion d'Hermes", None);
/// Where the Hermes step runs, the connection step follows it: Hermes's first answer ends it.
const HERMES_AI_TO_CONNECT: Item = Item("hermes_ia", "Connexion d'Hermes", Some("hermes_ia"));
const HERMES_BRAIN: Item = Item("hermes_cerveau", "Hermes et ton cerveau", None);
/// Once connected, the link step notes in Hermes's memory where the brain is (hermes_brain.rs).
const HERMES_BRAIN_TO_LINK: Item = Item("hermes_cerveau", "Hermes et ton cerveau", Some("hermes_cerveau"));
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
    github: bool,
    claude_ai: bool,
    #[cfg(target_os = "macos")]
    clt: bool,
    #[cfg(windows)]
    python: Option<detect::Python>,
    #[cfg(windows)]
    git: Found,
    bun: Found,
    claude: Found,
    claude_logged_in: bool,
    /// What the advice reads (advice.rs): files only, and the login `claude auth status` gave.
    advice: advice::Facts,
    obsidian: Found,
    /// Who the brain works with (agents.json): Claude's facts are looked for only when Claude is one
    /// of them, Codex's when Codex is.
    agents: Agents,
    chatgpt: bool,
    codex: Option<CodexFacts>,
    hermes: hermes::Facts,
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
        advice: None,
    }
}

#[cfg(target_os = "macos")]
fn probe(home: &Path) -> Facts {
    let agents = agents::current(home);
    let (claude, codex) = (agents.lists(Engine::Claude), agents.lists(Engine::Codex));
    thread::scope(|s| {
        // The slow probes (network, `claude auth status`, Codex's login) run alongside the rest.
        let github = s.spawn(|| answers("https://github.com"));
        let claude_ai = s.spawn(move || claude && answers("https://claude.ai"));
        // `claude auth status` once: whether Claude is logged in, and for the advice how.
        let login = s.spawn(move || {
            let method = if claude { detect::claude_auth_method() } else { None };
            (method.as_deref().is_some_and(|m| m != "none"), advice::probe(home, method.as_deref()))
        });
        let chatgpt = s.spawn(move || codex && answers(CHATGPT));
        let codex = s.spawn(|| probe_codex(home, &agents));
        let hermes = s.spawn(hermes::probe);

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
            github: github.join().unwrap_or(false),
            claude_ai: claude_ai.join().unwrap_or(false),
            claude_logged_in: false,
            advice: advice::Facts::default(),
            chatgpt: chatgpt.join().unwrap_or(false),
            codex: codex.join().ok().flatten(),
            hermes: hermes.join().unwrap_or_default(),
            agents: agents.clone(),
        };
        let (claude_logged_in, seen) = login.join().unwrap_or_default();
        Facts { claude_logged_in, advice: seen, ..facts }
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
    thread::scope(|s| {
        let github = s.spawn(|| answers("https://github.com"));
        let claude_ai = s.spawn(move || claude && answers("https://claude.ai"));
        // `claude auth status` once: whether Claude is logged in, and for the advice how.
        let login = s.spawn(move || {
            let method = if claude { detect::claude_auth_method() } else { None };
            (method.as_deref().is_some_and(|m| m != "none"), advice::probe(home, method.as_deref()))
        });
        let chatgpt = s.spawn(move || codex && answers(CHATGPT));
        let codex = s.spawn(|| probe_codex(home, &agents));
        let hermes = s.spawn(hermes::probe);
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
        let system = system.join().unwrap_or_default();
        let facts = Facts {
            windows: system.version,
            arch: system.arch,
            ram_bytes: system.ram_bytes,
            disk_free_kb: system.free_bytes.map(|b| b / 1024),
            github: github.join().unwrap_or(false),
            claude_ai: claude_ai.join().unwrap_or(false),
            python,
            git,
            bun,
            claude,
            claude_logged_in: false,
            advice: advice::Facts::default(),
            obsidian,
            chatgpt: chatgpt.join().unwrap_or(false),
            codex: codex.join().ok().flatten(),
            hermes: hermes.join().unwrap_or_default(),
            agents: agents.clone(),
        };
        let (claude_logged_in, seen) = login.join().unwrap_or_default();
        Facts { claude_logged_in, advice: seen, ..facts }
    })
}

#[cfg(target_os = "linux")]
fn probe(home: &Path) -> Facts {
    let agents = agents::current(home);
    let (claude, codex) = (agents.lists(Engine::Claude), agents.lists(Engine::Codex));
    thread::scope(|s| {
        let github = s.spawn(|| answers("https://github.com"));
        let claude_ai = s.spawn(move || claude && answers("https://claude.ai"));
        // `claude auth status` once: whether Claude is logged in, and for the advice how.
        let login = s.spawn(move || {
            let method = if claude { detect::claude_auth_method() } else { None };
            (method.as_deref().is_some_and(|m| m != "none"), advice::probe(home, method.as_deref()))
        });
        let chatgpt = s.spawn(move || codex && answers(CHATGPT));
        let codex = s.spawn(|| probe_codex(home, &agents));
        let hermes = s.spawn(hermes::probe);

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
            github: github.join().unwrap_or(false),
            claude_ai: claude_ai.join().unwrap_or(false),
            claude_logged_in: false,
            advice: advice::Facts::default(),
            chatgpt: chatgpt.join().unwrap_or(false),
            codex: codex.join().ok().flatten(),
            hermes: hermes.join().unwrap_or_default(),
            agents: agents.clone(),
        };
        let (claude_logged_in, seen) = login.join().unwrap_or_default();
        Facts { claude_logged_in, advice: seen, ..facts }
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
/// A Linux without curl yet (the paquets step brings it) gets a plain connection instead.
pub(crate) fn answers(url: &str) -> bool {
    if cfg!(target_os = "linux") && !sys::is_executable(&sys::curl()) {
        return connects(url);
    }
    let args = ["-sS", "-o", sys::NULL_DEVICE, "-m", "8", "-w", "%{http_code}", url];
    http_answered(&sys::run(&sys::curl(), &args, PROBE_TIMEOUT).stdout)
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
fn http_answered(code: &str) -> bool {
    code.trim().parse::<u16>().is_ok_and(|c| c > 0)
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
    #[cfg(windows)]
    let tools = vec![check_python(f.python.as_ref()), check_git(&f.git)];
    let hosts = hosts(f);
    let mut checks = Vec::from(system);
    checks.extend([check_disk(disk_free_gb), check_network(&hosts)]);
    checks.extend(tools);
    checks.push(check_bun(&f.bun));
    for engine in &f.agents.order {
        match engine {
            Engine::Claude => checks.extend([check_claude(&f.claude), check_claude_auth(f.claude.answers(), f.claude_logged_in)]),
            Engine::Codex => checks.extend(f.codex.iter().flat_map(|c| [check_codex(&c.found), check_codex_auth(c)])),
        }
    }
    if f.agents.order.is_empty() {
        checks.push(check_agents(&f.agents.etat));
    } else if agents::pending(&f.agents) {
        checks.push(AGENTS_TO_RECORD.is(Status::Missing, "Je le note en installant ton cerveau."));
    }
    checks.extend([
        #[cfg(not(target_os = "linux"))]
        check_obsidian(&f.obsidian),
        #[cfg(target_os = "linux")]
        check_obsidian_linux(&f.obsidian, f.linux.obsidian_installable),
    ]);
    let muets = f.codex.as_ref().is_some_and(|c| c.hooks_muets);
    checks.extend(check_brain(detect::brain(home, &f.agents), &detect::brain_dir_shown(home), muets));
    checks.push(check_voyage(detect::embedding_model(home).as_deref()));
    checks.extend(check_hermes(&f.hermes, disk_free_gb));

    let missing = |step: &str| {
        checks
            .iter()
            .any(|c| c.status == Status::Missing && c.fix.as_deref() == Some(step))
    };
    let todo = steps::order(&f.agents)
        .iter()
        .filter(|s| missing(s))
        .map(|s| s.to_string())
        .collect();
    let can_install = checks.iter().all(|c| c.status != Status::Error);

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
    let mut hosts = vec![("github.com", f.github)];
    hosts.extend(f.agents.order.iter().map(|e| match e {
        Engine::Claude => ("claude.ai", f.claude_ai),
        Engine::Codex => ("chatgpt.com", f.chatgpt),
    }));
    hosts
}

/// Blocks only when none of the sites answers.
fn check_network(hosts: &[(&str, bool)]) -> Check {
    let names = |up: bool| hosts.iter().filter(|(_, ok)| *ok == up).map(|(h, _)| h.to_string()).collect::<Vec<_>>();
    let (up, down) = (names(true), names(false));
    if down.is_empty() {
        let verb = if up.len() == 1 { "répond" } else { "répondent" };
        return NETWORK.is(Status::Ok, format!("{} {verb}.", steps::list_fr(&up)));
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

/// The Microsoft Store's python.exe does not count: it runs nothing.
#[cfg(windows)]
fn check_python(found: Option<&detect::Python>) -> Check {
    match found {
        Some(p) => Check {
            path: Some(p.exe.to_string_lossy().into_owned()),
            ..PYTHON.is(Status::Ok, format!("Python {}", fmt_version(p.version)))
        },
        None => PYTHON.is(Status::Missing, "Pas encore installé. Je le prends chez Microsoft (winget)."),
    }
}

/// Found is installed, as for install.ps1, even when it does not tell its version.
#[cfg(windows)]
fn check_git(found: &Found) -> Check {
    match found {
        Found::Absent => GIT.is(Status::Missing, "Pas encore installé. Je le prends chez Microsoft (winget)."),
        _ if found.version().is_some() => GIT.is(Status::Ok, found.label("Git")),
        _ => GIT.is(Status::Ok, "Git est installé."),
    }
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

fn check_claude_auth(claude_works: bool, logged_in: bool) -> Check {
    match (claude_works, logged_in) {
        (false, _) => CLAUDE_AUTH.is(Status::Warn, "Tu te connecteras une fois Claude Code installé."),
        (true, true) => CLAUDE_AUTH.is(Status::Ok, "Tu es connecté."),
        (true, false) => CLAUDE_AUTH.is(Status::Warn, "Pas encore connecté. Tu le feras en un clic."),
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

/// The model name ("voyage:voyage-4") means nothing to the user: on or off is enough.
fn check_voyage(model: Option<&str>) -> Check {
    match model {
        Some(m) if m.starts_with("voyage:") => VOYAGE.is(Status::Ok, "Activée (Voyage AI)"),
        Some(_) => VOYAGE.is(Status::Ok, "Activée"),
        None => VOYAGE.is(
            Status::Warn,
            "Recherche par mots-clés seulement. Une clé Voyage permet de chercher par le sens.",
        ),
    }
}

/// Hermes as hermes::probe found it: never blocks. On a Mac it runs on, the Hermes step (the last
/// one: the brain works without it) settles what is missing; elsewhere nothing installs it yet. Its
/// connection and its link to the brain only once it answers; on an Intel Mac, where it does not
/// run, one line says so.
fn check_hermes(h: &hermes::Facts, free_gb: Option<f64>) -> Vec<Check> {
    if !h.supported && !h.found.answers() {
        return vec![HERMES.is(Status::Warn, "Hermes ne fonctionne que sur les Mac à puce Apple : ton cerveau marche très bien sans lui.")];
    }
    let mine = cfg!(target_os = "macos") && h.supported;
    let to_fix = |what: String, then: &str| {
        if mine {
            HERMES_TO_INSTALL.is(Status::Missing, format!("{what}{then}"))
        } else {
            HERMES.is(Status::Warn, what)
        }
    };
    let found = match (&h.found, h.found.version()) {
        (Found::Absent, _) if mine && free_gb.is_some_and(|gb| gb < HERMES_DISK_GB) => HERMES.is(
            Status::Warn,
            format!(
                "Il faut {} Go libres pour l'installer, il en reste {} : fais un peu de place, puis relance.",
                HERMES_DISK_GB,
                fmt_gb(free_gb.unwrap_or_default())
            ),
        ),
        (Found::Absent, _) => to_fix("Pas encore installé.".into(), ""),
        (Found::NoVersion, _) => to_fix("Hermes est là mais ne répond pas.".into(), " Je vais le réparer."),
        (_, Some(v)) if v < hermes::MIN_HERMES => to_fix(
            format!("Hermes {} est trop ancien (il faut {} ou plus).", fmt_version(v), fmt_version(hermes::MIN_HERMES)),
            " Je vais le mettre à jour.",
        ),
        _ if !h.app && mine => to_fix(format!("{} est là, mais pas son app.", h.found.label("Hermes")), " Je la range dans Applications."),
        _ => HERMES.is(Status::Ok, h.found.label("Hermes")),
    };
    // Installed or repaired by its step first, then connected by the next one.
    let installing = mine && found.status == Status::Missing;
    if !h.found.answers() && !installing {
        return vec![found];
    }
    let ai = match &h.provider {
        Some(p) => HERMES_AI.is(Status::Ok, hermes::provider_said(p)),
        None if mine && !h.found.answers() => HERMES_AI_TO_CONNECT.is(Status::Missing, "Je le brancherai sur ton IA juste après."),
        None if mine => HERMES_AI_TO_CONNECT.is(Status::Missing, "Pas encore branché sur ton IA."),
        None => HERMES_AI.is(Status::Warn, "Pas encore branché sur ton IA."),
    };
    // Then linked to the brain: once connected, its memory says where the brain is.
    let brain = match &h.provider {
        Some(_) if h.brain => HERMES_BRAIN.is(Status::Ok, "Il sait où lire ton cerveau."),
        _ if mine && ai.status == Status::Missing => HERMES_BRAIN_TO_LINK.is(Status::Missing, "Je le relierai à ton cerveau juste après."),
        _ if mine => HERMES_BRAIN_TO_LINK.is(Status::Missing, "Pas encore relié à ton cerveau."),
        _ => HERMES_BRAIN.is(Status::Warn, "Pas encore relié à ton cerveau."),
    };
    vec![found, ai, brain]
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
            github: true,
            claude_ai: true,
            #[cfg(target_os = "macos")]
            clt: true,
            #[cfg(windows)]
            python: Some(detect::Python { exe: r"C:\Users\jo\AppData\Local\Programs\Python\Python312\python.exe".into(), version: (3, 12, 10) }),
            #[cfg(windows)]
            git: Found::Version("git version 2.51.0.windows.1".into()),
            bun: Found::Version("1.4.2".into()),
            claude: Found::Version("2.1.296 (Claude Code)".into()),
            claude_logged_in: true,
            advice: advice::Facts { claude: true, claude_connecte: true, ..advice::Facts::default() },
            obsidian: Found::Version("1.14.4".into()),
            agents: Agents::claude_alone(),
            chatgpt: false,
            codex: None,
            hermes: hermes::Facts {
                supported: true,
                found: Found::Version("Hermes Agent v0.22.2 (2026.9.24) · stable".into()),
                app: true,
                provider: Some("claude-subscription-directsdk-experimental".into()),
                brain: true,
            },
        }
    }

    #[test]
    fn hermes_is_reported_and_never_blocks() {
        let tmp = tempfile::tempdir().unwrap();
        let healthy = healthy_facts();
        let d = decide(tmp.path(), &healthy);
        let (h, ai, brain) = (by_id(&d, "hermes"), by_id(&d, "hermes_ia"), by_id(&d, "hermes_cerveau"));
        assert_eq!((h.status, h.detail.as_str()), (Status::Ok, "Hermes 0.22.2"));
        assert_eq!((ai.status, ai.detail.as_str()), (Status::Ok, "Il passe par ton abonnement Claude."));
        assert_eq!((brain.status, brain.detail.as_str()), (Status::Ok, "Il sait où lire ton cerveau."));
        // The last lines, after the brain's.
        assert_eq!(d.checks.iter().rev().take(3).map(|c| c.id.as_str()).collect::<Vec<_>>(), ["hermes_cerveau", "hermes_ia", "hermes"]);

        let hermes_with = |found: Found, provider: Option<&str>, supported: bool| {
            let h = hermes::Facts { supported, found, app: true, brain: provider.is_some(), provider: provider.map(String::from) };
            decide(tmp.path(), &Facts { hermes: h, ..healthy_facts() })
        };
        // On a Mac the Hermes step settles it, last, and the next ones connect it and link it to the
        // brain; elsewhere nothing installs it yet.
        let (to_fix, step): (Status, &[&str]) =
            if cfg!(target_os = "macos") { (Status::Missing, &["hermes", "hermes_ia", "hermes_cerveau"]) } else { (Status::Warn, &[]) };
        let then = |said: &str| if cfg!(target_os = "macos") { said.to_string() } else { String::new() };
        for (found, detail) in [
            (Found::Absent, "Pas encore installé.".to_string()),
            (Found::NoVersion, format!("Hermes est là mais ne répond pas.{}", then(" Je vais le réparer."))),
        ] {
            let d = hermes_with(found, None, true);
            assert_eq!((by_id(&d, "hermes").status, by_id(&d, "hermes").detail.as_str()), (to_fix, detail.as_str()));
            if cfg!(target_os = "macos") {
                let ai = by_id(&d, "hermes_ia");
                assert_eq!((ai.status, ai.detail.as_str(), ai.fix.as_deref()), (Status::Missing, "Je le brancherai sur ton IA juste après.", Some("hermes_ia")));
                let brain = by_id(&d, "hermes_cerveau");
                assert_eq!(
                    (brain.status, brain.detail.as_str(), brain.fix.as_deref()),
                    (Status::Missing, "Je le relierai à ton cerveau juste après.", Some("hermes_cerveau"))
                );
            } else {
                assert!(d.checks.iter().all(|c| c.id != "hermes_ia" && c.id != "hermes_cerveau"), "no connection without Hermes");
            }
            assert!(d.can_install);
            assert_eq!(d.todo, [decide(tmp.path(), &healthy).todo, step.iter().map(|s| s.to_string()).collect()].concat());
        }
        let old = hermes_with(Found::Version("Hermes Agent v0.21.3 (2026.8.30) · stable".into()), Some("anthropic"), true);
        assert_eq!(by_id(&old, "hermes").status, to_fix);
        assert_eq!(
            by_id(&old, "hermes").detail,
            format!("Hermes 0.21.3 est trop ancien (il faut 0.21.4 ou plus).{}", then(" Je vais le mettre à jour."))
        );
        assert_eq!(by_id(&old, "hermes_ia").detail, "Il passe par Claude.");
        let unplugged = hermes_with(healthy.hermes.found.clone(), None, true);
        let ai = by_id(&unplugged, "hermes_ia");
        assert_eq!((ai.status, ai.detail.as_str()), (to_fix, "Pas encore branché sur ton IA."));
        let then_linked: &[&str] = if cfg!(target_os = "macos") { &["hermes_ia", "hermes_cerveau"] } else { &[] };
        assert!(unplugged.todo.ends_with(&then_linked.iter().map(|s| s.to_string()).collect::<Vec<_>>()), "{:?}", unplugged.todo);
        assert_eq!(unplugged.todo.contains(&"hermes_ia".to_string()), cfg!(target_os = "macos"), "{:?}", unplugged.todo);
        assert!(!decide(tmp.path(), &healthy).todo.contains(&"hermes_ia".to_string()), "connected already");
        // Connected but its memory not yet saying where the brain is: on a Mac the link comes next,
        // alone; elsewhere it is only reported.
        let unlinked = decide(tmp.path(), &Facts { hermes: hermes::Facts { brain: false, ..healthy.hermes.clone() }, ..healthy_facts() });
        let brain = by_id(&unlinked, "hermes_cerveau");
        let link_step = if cfg!(target_os = "macos") { Some("hermes_cerveau") } else { None };
        assert_eq!((brain.status, brain.detail.as_str(), brain.fix.as_deref()), (to_fix, "Pas encore relié à ton cerveau.", link_step));
        assert_eq!(by_id(&unlinked, "hermes_ia").status, Status::Ok);
        assert_eq!(unlinked.todo, [decide(tmp.path(), &healthy).todo, link_step.iter().map(|s| s.to_string()).collect()].concat());

        let intel = hermes_with(Found::Absent, None, false);
        assert_eq!(by_id(&intel, "hermes").status, Status::Warn);
        assert!(by_id(&intel, "hermes").detail.contains("puce Apple"), "{}", by_id(&intel, "hermes").detail);
        assert!(intel.checks.iter().all(|c| c.id != "hermes_ia" && c.id != "hermes_cerveau"));
        assert_eq!(intel.todo, decide(tmp.path(), &healthy).todo, "the Hermes step does not run there");
        // One that runs anyway is taken as it is, app or not.
        let ran = hermes_with(healthy.hermes.found.clone(), Some("openai-codex"), false);
        assert_eq!(by_id(&ran, "hermes_ia").detail, "Il passe par ton compte ChatGPT.");
        assert!(by_id(&ran, "hermes").fix.is_none() && by_id(&ran, "hermes_ia").fix.is_none() && by_id(&ran, "hermes_cerveau").fix.is_none());
        let ran_bare = Facts { hermes: hermes::Facts { supported: false, app: false, ..healthy.hermes.clone() }, ..healthy_facts() };
        assert_eq!(by_id(&decide(tmp.path(), &ran_bare), "hermes").status, Status::Ok);
    }

    #[test]
    fn hermes_without_its_app_or_room() {
        let tmp = tempfile::tempdir().unwrap();
        let healthy = healthy_facts();
        let bare = Facts { hermes: hermes::Facts { app: false, ..healthy.hermes.clone() }, ..healthy_facts() };
        let d = decide(tmp.path(), &bare);
        let h = by_id(&d, "hermes");
        if cfg!(target_os = "macos") {
            assert_eq!((h.status, h.detail.as_str()), (Status::Missing, "Hermes 0.22.2 est là, mais pas son app. Je la range dans Applications."));
            assert_eq!(d.todo.last().map(String::as_str), Some("hermes"));
        } else {
            assert_eq!(h.status, Status::Ok, "app is always true there");
        }
        // install.sh's 4.4 GB wait for room; the brain does not.
        let absent = hermes::Facts { found: Found::Absent, provider: None, ..healthy.hermes.clone() };
        let tight = Facts { hermes: absent.clone(), disk_free_kb: Some(4_200_000), ..healthy_facts() };
        let d = decide(tmp.path(), &tight);
        let h = by_id(&d, "hermes");
        if cfg!(target_os = "macos") {
            assert_eq!(
                (h.status, h.detail.as_str(), h.fix.as_deref()),
                (Status::Warn, "Il faut 6 Go libres pour l'installer, il en reste 4,3 : fais un peu de place, puis relance.", None)
            );
        }
        assert!(d.can_install && !d.todo.contains(&"hermes".to_string()));
        assert!(d.checks.iter().all(|c| c.id != "hermes_ia"), "nothing to connect while there is no room for Hermes");
        let roomy = Facts { hermes: absent, disk_free_kb: Some(5_900_000), ..healthy_facts() };
        let d = decide(tmp.path(), &roomy);
        assert_eq!(by_id(&d, "hermes").detail, "Pas encore installé.");
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
            let c = check_network(&[("github.com", github), ("claude.ai", claude_ai)]);
            (c.status, c.detail)
        };
        // Claude alone: word for word as before Codex.
        assert_eq!(net(true, true), (Status::Ok, "github.com et claude.ai répondent.".into()));
        assert_eq!(net(true, false), (Status::Warn, "claude.ai ne répond pas. Une étape risque d'échouer.".into()));
        assert_eq!(net(false, true), (Status::Warn, "github.com ne répond pas. Une étape risque d'échouer.".into()));
        assert_eq!(net(false, false), (Status::Error, "Pas d'internet : ni github.com ni claude.ai ne répondent. Vérifie ton Wi-Fi, puis relance.".into()));
        assert!(http_answered("403"));
        assert!(!http_answered("000"));
        assert!(!http_answered(""));
    }

    /// Each agent's site; none answering blocks, one of them down warns.
    #[test]
    fn the_network_check_names_each_agents_site() {
        let three = |a, b, c| check_network(&[("github.com", a), ("claude.ai", b), ("chatgpt.com", c)]);
        assert_eq!(three(true, true, true).detail, "github.com, claude.ai et chatgpt.com répondent.");
        let down = three(true, false, false);
        assert_eq!((down.status, down.detail.as_str()), (Status::Warn, "claude.ai et chatgpt.com ne répondent pas. Une étape risque d'échouer."));
        let none = three(false, false, false);
        assert_eq!(
            (none.status, none.detail.as_str()),
            (Status::Error, "Pas d'internet : ni github.com, ni claude.ai, ni chatgpt.com ne répondent. Vérifie ton Wi-Fi, puis relance.")
        );
        assert_eq!(check_network(&[("github.com", true)]).detail, "github.com répond.");
        let alone = check_network(&[("github.com", false)]);
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
        let unknown = check_claude_auth(false, false);
        assert_eq!((unknown.status, unknown.fix), (Status::Warn, None));
        assert_eq!(check_claude_auth(true, false).status, Status::Warn);
        assert_eq!(check_claude_auth(true, true).status, Status::Ok);
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
            claude_logged_in: false,
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
        let f = Facts { agents: Agents::none("ferme"), claude: Found::Absent, claude_logged_in: false, ..healthy_facts() };
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
        let off = check_voyage(None);
        assert_eq!((off.status, off.fix), (Status::Warn, None));
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
        assert_eq!(d.checks.len(), 16);
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
            claude_logged_in: false,
            ..healthy_facts()
        };
        let d = decide(tmp.path(), &facts);
        assert_eq!(d.todo, ["claude", "clt", "bun", "brain"]);
        assert!(d.can_install);
        // Warnings never land in the todo list.
        assert_eq!(by_id(&d, "claude_auth").status, Status::Warn);
        assert_eq!(by_id(&d, "voyage").status, Status::Warn);
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
            github: false,
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
            claude_logged_in: false,
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
            claude_logged_in: false,
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
