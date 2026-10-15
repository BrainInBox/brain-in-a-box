//! Looks at the Mac (or the PC): what is there, what is missing, what blocks the install.
//!
//! `probe()` runs the system commands and gathers raw facts; `decide()` turns
//! them, plus the files under a given home, into checks. All the logic sits in
//! `decide()` and the `check_*` functions, so tests drive them with a fake home.
//!
//! On Windows, install.ps1 installs Python, Git, Bun and Obsidian along with the brain:
//! their checks point to the brain step. On Linux, the system's own tools (git, python3,
//! curl) come first, from the system's packages, as Apple's tools do on a Mac.

use std::path::Path;
use std::thread;
use std::time::Duration;

#[cfg(target_os = "macos")]
use crate::detect::parse_version;
#[cfg(unix)]
use crate::detect::Version;
use crate::detect::{self, fmt_version, Brain, Found};
use crate::model::{Check, Diagnosis, Status};
use crate::{steps, sys};

#[cfg(target_os = "macos")]
const MIN_MACOS: Version = (13, 0, 0);
/// Windows 10 version 1809: install.ps1's floor (winget, curl, tar).
#[cfg(windows)]
const MIN_WINDOWS_BUILD: u32 = 17763;
/// Windows 11 kept the 10 in its version number: the build tells them apart.
#[cfg(windows)]
const WINDOWS_11_BUILD: u32 = 22000;
const DISK_BLOCKING_GB: f64 = 3.0;
const DISK_TIGHT_GB: f64 = 10.0;
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
const OBSIDIAN: Item = Item("obsidian", "Obsidian (pour lire tes notes)", Some(OWN_STEP_OR_BRAIN[1]));
const BRAIN_VAULT: Item = Item("brain_vault", "Ton dossier Brain", Some("brain"));
const BRAIN_HOOKS: Item = Item("brain_hooks", "Les automatismes de Claude", Some("brain"));
const BRAIN_SKILLS: Item = Item("brain_skills", "Les compétences de Claude", Some("brain"));
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
    obsidian: Found,
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
    }
}

#[cfg(target_os = "macos")]
fn probe(home: &Path) -> Facts {
    thread::scope(|s| {
        // The slow probes (network, `claude auth status`) run alongside the rest.
        let github = s.spawn(|| answers("https://github.com"));
        let claude_ai = s.spawn(|| answers("https://claude.ai"));
        let logged_in = s.spawn(detect::claude_logged_in);

        let home_arg = home.to_string_lossy();
        // Fields are evaluated in this order: the threads are joined last.
        Facts {
            macos: command_text("/usr/bin/sw_vers", &["-productVersion"]),
            arch: command_text("/usr/bin/uname", &["-m"]).unwrap_or_default(),
            ram_bytes: command_text("/usr/sbin/sysctl", &["-n", "hw.memsize"]).and_then(|t| t.parse().ok()),
            disk_free_kb: command_text("/bin/df", &["-k", "--", &home_arg]).and_then(|t| parse_df_available_kb(&t)),
            clt: detect::clt_dir().is_some(),
            bun: detect::bun(home),
            claude: detect::claude(),
            obsidian: detect::obsidian(home),
            github: github.join().unwrap_or(false),
            claude_ai: claude_ai.join().unwrap_or(false),
            claude_logged_in: logged_in.join().unwrap_or(false),
        }
    })
}

#[cfg(windows)]
fn probe(home: &Path) -> Facts {
    thread::scope(|s| {
        let github = s.spawn(|| answers("https://github.com"));
        let claude_ai = s.spawn(|| answers("https://claude.ai"));
        let logged_in = s.spawn(detect::claude_logged_in);
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
        let claude = detect::claude();
        let obsidian = detect::obsidian(home);
        let system = system.join().unwrap_or_default();
        Facts {
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
            claude_logged_in: logged_in.join().unwrap_or(false),
            obsidian,
        }
    })
}

#[cfg(target_os = "linux")]
fn probe(home: &Path) -> Facts {
    thread::scope(|s| {
        let github = s.spawn(|| answers("https://github.com"));
        let claude_ai = s.spawn(|| answers("https://claude.ai"));
        let logged_in = s.spawn(detect::claude_logged_in);

        let home_arg = home.to_string_lossy();
        let os = detect::os_release();
        let debian = detect::debian_like(&os);
        Facts {
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
            claude: detect::claude(),
            obsidian: detect::obsidian(home),
            github: github.join().unwrap_or(false),
            claude_ai: claude_ai.join().unwrap_or(false),
            claude_logged_in: logged_in.join().unwrap_or(false),
        }
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
    let mut checks = Vec::from(system);
    checks.extend([check_disk(disk_free_gb), check_network(f.github, f.claude_ai)]);
    checks.extend(tools);
    checks.extend([
        check_bun(&f.bun),
        check_claude(&f.claude),
        check_claude_auth(f.claude.answers(), f.claude_logged_in),
        #[cfg(not(target_os = "linux"))]
        check_obsidian(&f.obsidian),
        #[cfg(target_os = "linux")]
        check_obsidian_linux(&f.obsidian, f.linux.obsidian_installable),
    ]);
    checks.extend(check_brain(detect::brain(home), &detect::brain_dir_shown(home)));
    checks.push(check_voyage(detect::embedding_model(home).as_deref()));

    let missing = |step: &str| {
        checks
            .iter()
            .any(|c| c.status == Status::Missing && c.fix.as_deref() == Some(step))
    };
    let todo = steps::ORDER
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
        online: f.github && f.claude_ai,
        checks,
        todo,
        can_install,
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

fn check_network(github: bool, claude_ai: bool) -> Check {
    match (github, claude_ai) {
        (true, true) => NETWORK.is(Status::Ok, "github.com et claude.ai répondent."),
        (true, false) => NETWORK.is(Status::Warn, "claude.ai ne répond pas. Une étape risque d'échouer."),
        (false, true) => NETWORK.is(Status::Warn, "github.com ne répond pas. Une étape risque d'échouer."),
        (false, false) => NETWORK.is(
            Status::Error,
            "Pas d'internet : ni github.com ni claude.ai ne répondent. Vérifie ton Wi-Fi, puis relance.",
        ),
    }
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

/// `at`: the brain's folder as the window says it (detect::brain_dir_shown).
fn check_brain(brain: Brain, at: &str) -> [Check; 4] {
    let piece = |item: Item, present: bool, ok: &str, missing: &str| {
        if present {
            item.is(Status::Ok, ok)
        } else {
            item.is(Status::Missing, missing)
        }
    };
    [
        piece(
            BRAIN_VAULT,
            brain.vault,
            &format!("Il est dans {at}."),
            "Pas encore créé.",
        ),
        piece(
            BRAIN_HOOKS,
            brain.hooks,
            "Claude note tes corrections et tes séances.",
            "Pas encore branchés.",
        ),
        piece(
            BRAIN_SKILLS,
            brain.skills,
            "Installées.",
            "Pas encore installées.",
        ),
        piece(
            BRAIN_MEMORY,
            brain.memory,
            "Prête.",
            "Pas encore créée.",
        ),
    ]
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
            obsidian: Found::Version("1.14.4".into()),
        }
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
        assert_eq!(check_network(true, true).status, Status::Ok);
        assert_eq!(check_network(true, false).status, Status::Warn);
        assert_eq!(check_network(false, true).status, Status::Warn);
        assert_eq!(check_network(false, false).status, Status::Error);
        assert!(http_answered("403"));
        assert!(!http_answered("000"));
        assert!(!http_answered(""));
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
        let none = Brain { vault: false, hooks: false, skills: false, memory: false };
        for c in check_brain(none, "Documents/Brain") {
            assert_eq!((c.status, c.fix.as_deref()), (Status::Missing, Some("brain")), "{}", c.id);
        }
        let all = Brain { vault: true, hooks: true, skills: true, memory: true };
        for c in check_brain(all, "Documents/Brain") {
            assert_eq!(c.status, Status::Ok, "{}", c.id);
        }
        let [_, hooks, _, _] = check_brain(Brain { hooks: false, ..all }, "Documents/Brain");
        assert_eq!((hooks.id.as_str(), hooks.status), ("brain_hooks", Status::Missing));
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
