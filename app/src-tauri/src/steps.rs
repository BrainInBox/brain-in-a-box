//! The install steps, and what the window does once the install is over.
//!
//! Every step checks first (running it twice is harmless), installs, then checks
//! again: a step is done only when that last check passes. Nothing needs admin
//! rights, everything lands in the user's home. Downloads go to a fresh private
//! folder that is removed when the step ends.
//!
//! On Windows, install.ps1 is the installer: it brings Python, Git, Bun, gbrain and
//! Obsidian along with the brain, so the brain step runs it (without a console, without a
//! question) and reads its sections to show where it is. Only Claude Code has a step
//! of its own, first, as on a Mac.
//!
//! On Linux the steps are the Mac's, Apple's tools replaced by the system's packages (git,
//! python3, curl). Those and Obsidian's official package need root: the system asks the
//! user for their password (pkexec, its own window), for a command fixed here, and only for
//! these two steps. Everything else lands in the home folder, as on a Mac.
//!
//! The agents agents.json lists (agents.rs) each have their step where Claude Code's stands, the
//! preferred one first: Claude Code's, Codex's, or both. Without the file, Claude's alone, as before.

use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
#[cfg(unix)]
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{mpsc, Mutex, MutexGuard, TryLockError};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

use crate::agents::{self, Agents, Engine};
use crate::detect::{self, fmt_version, Found};
use crate::model::StepResult;
#[cfg(target_os = "macos")]
use crate::sys::minutes;
use crate::{codex, config_guard, rapport, repair};
use crate::{perms, sys, voyage};
#[cfg(target_os = "linux")]
use crate::root::{self, OBSIDIAN_DEB_SHA256};

/// Claude Code first: its installer needs none of Apple's tools, and once the user is
/// logged in, Claude can help repair the steps that follow.
#[cfg(target_os = "macos")]
const ORDER: &[&str] = &["claude", "clt", "bun", "obsidian", "brain"];
/// The system's tools first: everything after downloads with curl (a fresh Ubuntu desktop
/// has none), Bun's installer unzips, install.sh needs git and python3.
#[cfg(target_os = "linux")]
const ORDER: &[&str] = &["paquets", "claude", "bun", "obsidian", "brain"];
/// install.ps1 installs all the rest, Python and Git included.
#[cfg(windows)]
const ORDER: &[&str] = &["claude", "brain"];

/// The steps in the order they run: the agents' own where Claude Code's stands in ORDER (their step
/// ids are their names), the preferred one first. Claude alone: ORDER, as before Codex; a choice the
/// kit cannot trust: no agent's step.
pub fn order(agents: &Agents) -> Vec<&'static str> {
    ORDER.iter().flat_map(|&s| if s == "claude" { agents.order.iter().map(|e| e.id()).collect() } else { vec![s] }).collect()
}

/// engine/gbrain-pin.json's, which install.sh follows (a test holds them equal).
#[cfg(not(windows))]
const BUN_PIN: &str = "bun-v1.4.3";
#[cfg(target_os = "macos")]
const OBSIDIAN_URL: &str = "https://github.com/obsidianmd/obsidian-releases/releases/download/v1.14.4/Obsidian-1.14.4.dmg";
#[cfg(target_os = "macos")]
const OBSIDIAN_SIZE: u64 = 233_704_463;
/// Obsidian's Apple developer id: a copy signed by anyone else is thrown away.
#[cfg(target_os = "macos")]
const OBSIDIAN_TEAM: &str = "6JSW4SJWN9";
/// Obsidian's official package, the one install.sh pins (a test holds them equal); its SHA-256
/// is root.rs's, the only package root installs from a file.
#[cfg(target_os = "linux")]
const OBSIDIAN_DEB_URL: &str = "https://github.com/obsidianmd/obsidian-releases/releases/download/v1.14.4/obsidian_1.14.4_amd64.deb";
#[cfg(target_os = "linux")]
const OBSIDIAN_DEB_SIZE: u64 = 108_364_996;

const BRAIN_ZIP: &str = "https://github.com/BrainInBox/brain-in-a-box/archive/refs/heads/master.zip";
/// The installer in the sources: the brain step runs it, and its presence says the
/// unpacked folder is the right one.
#[cfg(not(windows))]
const INSTALLER: &str = "install.sh";
#[cfg(windows)]
const INSTALLER: &str = "install.ps1";
#[cfg(not(windows))]
const CLAUDE_INSTALLER: &str = "https://claude.ai/install.sh";
#[cfg(windows)]
const CLAUDE_INSTALLER: &str = "https://claude.ai/install.ps1";
#[cfg(target_os = "macos")]
const CLT_WAIT: Duration = Duration::from_secs(45 * 60);
/// git shows up in the tools' folder while Apple's installer still writes the rest: its window,
/// still open, is waited for this long more (the user clicks « Terminé » once it says so).
#[cfg(target_os = "macos")]
const CLT_SETTLE: Duration = Duration::from_secs(90);
/// Past these, an installer is stuck rather than slow: it is stopped.
const INSTALLER_LIMIT: Duration = Duration::from_secs(20 * 60);
/// An installer that downloads (Claude Code's: a hundred megabytes or more) is never cut while it moves,
/// on a slow line or mobile data: it is stopped once nothing moved for STALL (no line, no byte more in
/// the folders it writes to), or past SLOW whatever it does.
const PACE: Pace = Pace { stall: Duration::from_secs(5 * 60), limit: Duration::from_secs(60 * 60), tick: Duration::from_secs(5) };
/// install.ps1 also fetches Python, Git and Obsidian through winget.
const BRAIN_LIMIT: Duration = Duration::from_secs(if cfg!(windows) { 60 } else { 40 } * 60);
/// Our work folders (see TempDir): nothing else is ever named like them.
const WORK_PREFIX: &str = ".biab-";
const LINK_HOSTS: &[&str] = &["dashboard.voyageai.com", "docs.voyageai.com", "claude.com", "claude.ai", "obsidian.md", "chatgpt.com"];

/// The sentence for the user: Ok when done, Err with the cause and what to do.
type Step = Result<String, String>;

/// Two clicks must not run two installers over each other. A repair holds it from
/// Claude's analysis to the end of the relaunch.
static RUNNING: Mutex<()> = Mutex::new(());
/// Snapshots of the configuration guard, under ~.
pub(crate) const GUARD_DIR: &str = ".brain-in-a-box/garde";

/// The right to run a step, or why not now.
pub(crate) fn exclusive() -> Result<MutexGuard<'static, ()>, String> {
    match RUNNING.try_lock() {
        Ok(g) => Ok(g),
        Err(TryLockError::Poisoned(p)) => Ok(p.into_inner()),
        Err(TryLockError::WouldBlock) => Err("Une autre étape est déjà en cours. Attends qu'elle finisse.".into()),
    }
}

pub fn run(id: &str, voyage_key: Option<&str>, log: &mut dyn FnMut(String)) -> StepResult {
    match exclusive() {
        Ok(g) => run_held(&g, id, voyage_key, log).result,
        Err(message) => StepResult { id: id.to_string(), ok: false, message },
    }
}

/// A step's result, and the files the configuration guard had to put back.
pub(crate) struct Held {
    pub result: StepResult,
    /// "~/.claude/hooks/brain/x.py (réécrit)": empty unless the guard stepped in.
    pub restored: Vec<String>,
}

/// Runs a step for whoever holds `exclusive()`. The brain step runs under the
/// configuration guard, a first install like a repair.
pub(crate) fn run_held(_g: &MutexGuard<'static, ()>, id: &str, voyage_key: Option<&str>, log: &mut dyn FnMut(String)) -> Held {
    let _awake = KeepAwake::start();
    let mut restored = Vec::new();
    let res = match id {
        #[cfg(target_os = "macos")]
        "clt" => clt(log),
        #[cfg(target_os = "linux")]
        "paquets" => paquets(Path::new(root::PKEXEC), log),
        #[cfg(not(windows))]
        "bun" => bun(log),
        "claude" => claude(log),
        "codex" => codex(log),
        #[cfg(target_os = "macos")]
        "obsidian" => obsidian(log),
        #[cfg(target_os = "linux")]
        "obsidian" => obsidian(Path::new(root::PKEXEC), log),
        "brain" => {
            let home = sys::home();
            let (res, back) = guarded(&home, &home.join(GUARD_DIR), log, |log| brain(voyage_key, log));
            restored = back;
            res
        }
        _ => Err("Étape inconnue.".into()),
    };
    let (ok, message) = match res {
        Ok(m) => (true, m),
        Err(m) => (false, m),
    };
    Held { result: StepResult { id: id.to_string(), ok, message }, restored }
}

/// Runs `step` between a snapshot of the user's configuration and its check: what
/// the step should not have touched is put back, and the step fails.
fn guarded(
    home: &Path,
    store: &Path,
    log: &mut dyn FnMut(String),
    step: impl FnOnce(&mut dyn FnMut(String)) -> Step,
) -> (Step, Vec<String>) {
    let snap = match config_guard::take(home, store) {
        Ok(s) => s,
        Err(e) => {
            return (
                Err(format!(
                    "Je n'ai pas pu mettre de côté tes réglages Claude avant de commencer ({e}). Je n'ai rien installé."
                )),
                Vec::new(),
            )
        }
    };
    let res = step(&mut *log);
    let report = config_guard::settle(snap, home);
    if report.clean() {
        return (res, Vec::new());
    }
    for v in &report.restored {
        log(format!("✗ {v} : je l'ai remis exactement comme avant."));
    }
    for v in &report.failed {
        log(format!("✗ {v} : je n'ai pas pu le remettre comme avant."));
    }
    let named = report.paths().into_iter().take(3).collect::<Vec<_>>().join(", ");
    let message = match &report.kept {
        None => format!(
            "L'installation touchait à des fichiers à toi ({named}) : je les ai remis exactement comme avant. \
             Envoie le rapport à la personne qui t'a conseillé Bibo."
        ),
        Some(copy) => format!(
            "L'installation touchait à des fichiers à toi ({named}) et je n'ai pas pu tout remettre. \
             Une copie de tes fichiers d'avant est dans {}. Envoie le rapport à la personne qui t'a conseillé Bibo.",
            config_guard::tilde(copy, home)
        ),
    };
    (Err(message), report.paths())
}

// ── Apple command line tools (git, python3) ─────────────────────────────────

/// " (git 2.39.5)", or nothing when git does not say.
#[cfg(target_os = "macos")]
fn git_version(clt: &Path) -> String {
    let out = sys::run(&clt.join("usr/bin/git"), &["--version"], Duration::from_secs(10));
    match out.stdout.split_whitespace().nth(2) {
        Some(v) if out.ok() => format!(" (git {v})"),
        _ => String::new(),
    }
}

#[cfg(target_os = "macos")]
fn clt(log: &mut dyn FnMut(String)) -> Step {
    if let Some(dir) = detect::clt_dir() {
        return Ok(format!("Les outils Apple sont déjà installés{}.", git_version(&dir)));
    }
    log("J'ouvre la fenêtre d'Apple : clique sur « Installer », puis accepte la licence.".into());
    let out = sys::run(Path::new("/usr/bin/xcode-select"), &["--install"], Duration::from_secs(30));
    if out.code == -1 {
        return Err("Je n'ai pas pu ouvrir l'installeur d'Apple. Redémarre le Mac, puis réessaie.".into());
    }
    let said = format!("{}{}", out.stdout, out.stderr);
    if said.contains("already installed") {
        return Err("macOS croit que les outils Apple sont déjà là, mais il en manque une partie. \
                    Ouvre Réglages Système > Général > Mise à jour de logiciels, puis réessaie."
            .into());
    }
    // Any other refusal means no window to wait for: do not sit there for 45 minutes.
    if !out.ok() && !apple_installer_open() {
        log(said.trim().to_string());
        return Err("L'installeur d'Apple n'a pas démarré. Redémarre le Mac, puis réessaie.".into());
    }
    let start = Instant::now();
    let mut next_note = Duration::from_secs(30);
    let (mut seen, mut gone) = (false, 0);
    let mut tools_since: Option<Instant> = None;
    loop {
        thread::sleep(Duration::from_secs(5));
        if let Some(dir) = detect::clt_dir() {
            let open = apple_installer_open();
            if tools_since.is_none() && open {
                log("Les outils sont là, Apple finit son installation : clique sur « Terminé » dans sa fenêtre quand elle le propose.".into());
            }
            let since = *tools_since.get_or_insert_with(Instant::now);
            if clt_settled(open, since.elapsed()) {
                return Ok(format!("Les outils Apple sont installés{}.", git_version(&dir)));
            }
            continue;
        }
        // Apple's window stays open until the install is over: if it was there and is
        // gone (three checks in a row), the user closed it.
        if apple_installer_open() {
            (seen, gone) = (true, 0);
        } else if seen {
            gone += 1;
            if gone >= 3 {
                return Err("La fenêtre d'Apple s'est fermée avant la fin. \
                            Relance l'étape et clique sur « Installer »."
                    .into());
            }
        }
        let waited = start.elapsed();
        if waited >= CLT_WAIT {
            return Err("Les outils Apple ne sont toujours pas installés après 45 minutes. \
                        Relance l'étape pour réessayer."
                .into());
        }
        if waited >= next_note {
            log(format!("J'attends la fin de l'installation d'Apple ({})…", minutes(waited)));
            next_note += Duration::from_secs(30);
        }
    }
}

/// The tools are there: done once Apple's window is closed, or CLT_SETTLE after they showed up.
#[cfg(target_os = "macos")]
fn clt_settled(installer_open: bool, since_tools: Duration) -> bool {
    !installer_open || since_tools >= CLT_SETTLE
}

#[cfg(target_os = "macos")]
fn apple_installer_open() -> bool {
    sys::run(Path::new("/usr/bin/pgrep"), &["-f", "Install Command Line Developer Tools"], Duration::from_secs(5)).ok()
}

// ── The system's tools (Linux: git, python3, curl…) ─────────────────────────

/// "git, curl et unzip".
pub(crate) fn list_fr(items: &[String]) -> String {
    match items {
        [] => String::new(),
        [one] => one.clone(),
        [rest @ .., last] => format!("{} et {last}", rest.join(", ")),
    }
}

#[cfg(target_os = "linux")]
fn paquets(pkexec: &Path, log: &mut dyn FnMut(String)) -> Step {
    let os = detect::os_release();
    let debian = detect::debian_like(&os);
    let missing = detect::missing_packages(debian);
    if missing.is_empty() {
        return Ok("Les outils de base sont déjà là (git, python3, curl).".into());
    }
    let names: Vec<String> = missing.iter().map(|p| p.to_string()).collect();
    let listed = list_fr(&names);
    if !debian {
        return Err(format!("Il manque {listed}. Installe-les avec le gestionnaire de paquets de ton système, puis relance l'étape."));
    }
    log(format!("Il manque {listed}. Ton ordinateur va te demander ton mot de passe : c'est pour les installer."));
    let by_hand = format!("sudo apt-get install -y {}", missing.join(" "));
    root::install_packages(pkexec, &missing, &listed, &by_hand, log)?;
    let still: Vec<String> = detect::missing_packages(debian).iter().map(|p| p.to_string()).collect();
    if still.is_empty() {
        Ok(format!("{} {} installés.", capitalize(&listed), if names.len() > 1 { "sont" } else { "est" }))
    } else {
        Err(format!("Il manque encore {} après l'installation. Relance l'étape.", list_fr(&still)))
    }
}

#[cfg(target_os = "linux")]
fn capitalize(s: &str) -> String {
    let mut c = s.chars();
    c.next().map_or_else(String::new, |f| f.to_uppercase().chain(c).collect())
}

// ── Bun ─────────────────────────────────────────────────────────────────────

#[cfg(not(windows))]
fn bun(log: &mut dyn FnMut(String)) -> Step {
    let home = sys::home();
    let found = detect::bun(&home);
    if detect::bun_ok(&found) {
        return Ok(format!("{} est déjà installé.", found.label("Bun")));
    }
    if let Some(v) = found.version() {
        log(format!("Bun {} est trop ancien : je le mets à jour.", fmt_version(v)));
    }
    run_installer("https://bun.sh/install", "Bun", &[BUN_PIN], &[home.join(".bun")], log)?;
    let found = detect::bun(&home);
    match found.version() {
        _ if detect::bun_ok(&found) => Ok(format!("{} est installé.", found.label("Bun"))),
        Some(v) => Err(format!("Bun {} est encore trop ancien après la mise à jour. Relance l'étape.", fmt_version(v))),
        None => Err("Bun ne répond pas après l'installation. Relance l'étape.".into()),
    }
}

// ── Claude Code ─────────────────────────────────────────────────────────────

fn claude(log: &mut dyn FnMut(String)) -> Step {
    let found = detect::claude();
    if found.answers() {
        return Ok(format!("{} est déjà installé.", found.label("Claude Code")));
    }
    // Where its installer downloads Claude Code, then where `claude install` puts it.
    let home = sys::home();
    run_installer(CLAUDE_INSTALLER, "Claude Code", &[], &[home.join(".claude/downloads"), home.join(".local/share/claude")], log)?;
    let found = detect::claude();
    if found.answers() {
        Ok(format!("{} est installé.", found.label("Claude Code")))
    } else {
        Err("Claude Code ne répond pas après l'installation. Relance l'étape.".into())
    }
}

// ── Codex ───────────────────────────────────────────────────────────────────

/// The version the kit tests, and OpenAI's official installer for it: install.sh's and install.ps1's
/// (a test holds them equal). Its script is checked against the SHA-256 they pin before it runs; it
/// checks the program's own digest.
const CODEX_VERSION: &str = "0.162.1";
#[cfg(not(windows))]
const CODEX_INSTALLER: (&str, &str) =
    ("https://github.com/openai/codex/releases/download/rust-v0.162.1/install.sh", "150e3cf675682efeaac115aa3747add3f27887896d04ce6d0b56478d8b428bf6");
#[cfg(windows)]
const CODEX_INSTALLER: (&str, &str) =
    ("https://github.com/openai/codex/releases/download/rust-v0.162.1/install.ps1", "f02eb53365d2bbb91f46c4d46ab8e357d087a8cc100794b3eb0c5b4368d6dc42");
/// What would steer the installer elsewhere (its folder, what it updates), and the Voyage key: none of
/// it reaches the installer (install.ps1's list, install.sh's within it).
const CODEX_INSTALLER_UNSET: &[&str] = &[
    "VOYAGE_API_KEY",
    "CODEX_INSTALL_DIR",
    "CODEX_INSTALL_DAEMON_ONLY",
    "CODEX_INSTALL_DEFER_SELECTION",
    "CODEX_INSTALL_IF_LATEST",
    "CODEX_INSTALL_IF_CURRENT",
    "CODEX_UPDATE_FROM_RELEASE",
    "CODEX_INSTALLER_USE_RELEASES_OPENAI_COM",
];

/// Codex, as install.sh installs it when it is chosen: for this user, no admin rights, in the
/// CODEX_HOME agents.json records. One already there is kept, as install.sh keeps it: an older one
/// is the user's to update.
fn codex(log: &mut dyn FnMut(String)) -> Step {
    let Some(codex_home) = agents::current(&sys::home()).codex_home else {
        return Err("Tu n'as pas choisi de travailler avec Codex : je ne l'installe pas.".into());
    };
    let setup = codex::Setup::for_user(&codex_home).map_err(|e| format!("Je n'ai pas pu créer mon dossier de travail ({e})."))?;
    if let Some(done) = codex_there(&detect::codex(&setup), "est déjà installé") {
        return done;
    }
    let root = sys::work_root().map_err(|e| format!("Je n'ai pas pu créer mon dossier de travail ({e})."))?;
    let tmp = TempDir::new_in(&root)?;
    let (url, sha256) = CODEX_INSTALLER;
    let script = tmp.path().join(if cfg!(windows) { "install.ps1" } else { "install.sh" });
    download(url, &script, "l'installeur de Codex", None, Duration::from_secs(5 * 60), log)?;
    let env = codex_installer_env(&codex_home);
    let env: Vec<(&str, Option<&str>)> = env.iter().map(|(k, v)| (*k, v.as_deref())).collect();
    run_codex_installer(&script, sha256, &env, tmp.path(), log)?;
    codex_there(&detect::codex(&setup), "est installé").unwrap_or_else(|| Err("Codex ne répond pas après l'installation. Relance l'étape.".into()))
}

/// The step's answer once Codex answers: done, or too old for the kit. None while it is missing.
fn codex_there(found: &Found, said: &str) -> Option<Step> {
    match found.version() {
        _ if detect::codex_ok(found) => Some(Ok(format!("{} {said}.", found.label("Codex")))),
        Some(v) => {
            Some(Err(format!("Codex {} est trop ancien pour le kit (il faut {CODEX_VERSION} ou plus) : mets-le à jour, puis relance l'étape.", fmt_version(v))))
        }
        None if found.answers() => Some(Err("Je n'arrive pas à lire la version de Codex : mets-le à jour, puis relance l'étape.".into())),
        None => None,
    }
}

/// The installer's environment: the version the kit tests, no question, the CODEX_HOME chosen, nothing
/// of CODEX_INSTALLER_UNSET. Windows: its own tar first on the PATH, as install.ps1 runs it (a GNU tar
/// in front, Git's Unix tools, reads "C:\…" as a remote host).
fn codex_installer_env(codex_home: &Path) -> Vec<(&'static str, Option<String>)> {
    let mut env: Vec<(&'static str, Option<String>)> = CODEX_INSTALLER_UNSET.iter().map(|k| (*k, None)).collect();
    env.extend([
        ("CODEX_RELEASE", Some(CODEX_VERSION.to_string())),
        ("CODEX_NON_INTERACTIVE", Some("1".to_string())),
        ("CODEX_HOME", Some(codex_home.to_string_lossy().into_owned())),
    ]);
    #[cfg(windows)]
    {
        let mut path = std::ffi::OsString::from(sys::system_root().join("System32"));
        if let Some(rest) = std::env::var_os("PATH").filter(|p| !p.is_empty()) {
            path.push(";");
            path.push(rest);
        }
        env.push(("PATH", Some(path.to_string_lossy().into_owned())));
    }
    env
}

/// Runs the downloaded installer once it is the one pinned, with `sh` (PowerShell on Windows).
fn run_codex_installer(script: &Path, sha256: &str, env: &[(&str, Option<&str>)], cwd: &Path, log: &mut dyn FnMut(String)) -> Result<(), String> {
    log("Je vérifie l'installeur de Codex…".into());
    if config_guard::sha256_file(script).as_deref() != Some(sha256) {
        return Err("L'installeur de Codex téléchargé n'est pas celui attendu : je ne l'ai pas lancé. Réessaie plus tard.".into());
    }
    log("J'installe Codex…".into());
    #[cfg(not(windows))]
    let code = sys::run_streaming(Path::new("/bin/sh"), &[&script.to_string_lossy()], env, Some(cwd), INSTALLER_LIMIT, log);
    #[cfg(windows)]
    let code = run_ps1(script, env, cwd, INSTALLER_LIMIT, log);
    match code {
        0 => Ok(()),
        code => Err(format!("L'installeur de Codex s'est arrêté (code {code}). Vérifie ta connexion internet, puis réessaie.")),
    }
}

// ── Obsidian ────────────────────────────────────────────────────────────────

#[cfg(target_os = "macos")]
fn obsidian(log: &mut dyn FnMut(String)) -> Step {
    let home = sys::home();
    let apps = home.join("Applications");
    if let Ok(me) = fs::metadata(&home).map(|m| perms::owner(&m)) {
        remove_stale_copies(&apps, me);
    }
    let found = detect::obsidian(&home);
    if found != Found::Absent {
        return Ok(format!("{} est déjà installé.", found.label("Obsidian")));
    }
    install_obsidian_into(&apps, log)?;
    match detect::obsidian(&home) {
        Found::Absent => Err("Obsidian n'apparaît pas dans Applications après l'installation. Relance l'étape.".into()),
        found => Ok(format!("{} est installé.", found.label("Obsidian"))),
    }
}

/// Downloads the pinned Obsidian, checks its signature and puts it in `apps`.
#[cfg(target_os = "macos")]
fn install_obsidian_into(apps: &Path, log: &mut dyn FnMut(String)) -> Result<PathBuf, String> {
    let dest = apps.join("Obsidian.app");
    // Not a working Obsidian (detect said so), but maybe the user's own files: never overwritten.
    if fs::symlink_metadata(&dest).is_ok() {
        return Err("Un Obsidian incomplet traîne dans le dossier Applications de ton dossier personnel. \
                    Mets-le à la corbeille, puis relance l'étape."
            .into());
    }
    let tmp = TempDir::new_in(&std::env::temp_dir())?;
    let dmg = tmp.path().join("Obsidian.dmg");
    download(OBSIDIAN_URL, &dmg, "Obsidian", Some(OBSIDIAN_SIZE), Duration::from_secs(60 * 60), log)?;
    if fs::metadata(&dmg).map(|m| m.len()).unwrap_or(0) != OBSIDIAN_SIZE {
        return Err("Le téléchargement d'Obsidian est incomplet. Vérifie ta connexion internet, puis réessaie.".into());
    }

    fs::create_dir_all(apps).map_err(|e| format!("Je n'ai pas pu créer ton dossier Applications ({e})."))?;
    // Copied next to its final place, then moved in one go: a copy cut short never
    // passes for an installed Obsidian. Removed with its content if anything fails.
    let staging = TempDir::new_in(apps)?;
    let copy = staging.path().join("Obsidian.app");
    {
        let mnt = Mount::attach(&dmg, &tmp.path().join("mnt"))?;
        log("Je copie Obsidian dans ton dossier Applications…".into());
        let out = sys::run(
            Path::new("/usr/bin/ditto"),
            &[&mnt.app("Obsidian.app"), &copy.to_string_lossy()],
            Duration::from_secs(10 * 60),
        );
        if !out.ok() {
            log(out.stderr.trim().to_string());
            return Err("La copie d'Obsidian a échoué. Vérifie qu'il reste de la place sur le disque, puis réessaie."
                .into());
        }
    }
    log("Je vérifie la signature d'Obsidian…".into());
    signed_by_obsidian(&copy)?;
    fs::rename(&copy, &dest).map_err(|e| format!("Je n'ai pas pu ranger Obsidian dans Applications ({e})."))?;
    Ok(dest)
}

/// Work folders left in `dir` by a step that was cut short (app quit, Mac turned off):
/// only real folders, named exactly like ours, owned by `owner`. Returns how many went.
pub(crate) fn remove_stale_copies(dir: &Path, owner: u32) -> usize {
    let Ok(entries) = fs::read_dir(dir) else { return 0 };
    let mut removed = 0;
    for entry in entries.filter_map(Result::ok) {
        let name = entry.file_name();
        let ours = name.to_str().and_then(|n| n.strip_prefix(WORK_PREFIX)).is_some_and(is_work_id);
        // DirEntry::metadata does not follow symlinks: a link is never ours.
        if ours && entry.metadata().is_ok_and(|m| m.is_dir() && perms::owner(&m) == owner) && fs::remove_dir_all(entry.path()).is_ok() {
            removed += 1;
        }
    }
    removed
}

/// The shape of `random_hex()`.
fn is_work_id(s: &str) -> bool {
    s.len() == 16 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// Obsidian's official package, pinned and checked, installed by apt as root: in /opt/Obsidian
/// with its sandbox helper, which Ubuntu 24.04's AppArmor profile for Obsidian expects. The
/// download stays in a private folder of the home; root copies it out before checking it.
#[cfg(target_os = "linux")]
fn obsidian(pkexec: &Path, log: &mut dyn FnMut(String)) -> Step {
    let home = sys::home();
    let base = home.join(".brain-in-a-box");
    if let Ok(me) = fs::metadata(&home).map(|m| perms::owner(&m)) {
        remove_stale_copies(&base, me);
    }
    let found = detect::obsidian(&home);
    if found != Found::Absent {
        return Ok(format!("{} est déjà installé.", found.label("Obsidian")));
    }
    if !detect::obsidian_installable(&detect::os_release()) {
        return Err("Obsidian n'a pas de paquet officiel pour ce système. Installe-le depuis obsidian.md ou Flathub, puis clique sur Revérifier.".into());
    }
    fs::create_dir_all(&base).map_err(|e| format!("Je n'ai pas pu créer le dossier .brain-in-a-box ({e})."))?;
    let tmp = TempDir::new_in(&base)?;
    let deb = tmp.path().join("obsidian.deb");
    download(OBSIDIAN_DEB_URL, &deb, "Obsidian", Some(OBSIDIAN_DEB_SIZE), Duration::from_secs(60 * 60), log)?;
    log("Je vérifie le paquet d'Obsidian…".into());
    if config_guard::sha256_file(&deb).as_deref() != Some(OBSIDIAN_DEB_SHA256) {
        return Err("Le paquet d'Obsidian téléchargé n'est pas celui attendu : je l'ai supprimé par sécurité. Réessaie plus tard.".into());
    }
    log("Ton ordinateur va te demander ton mot de passe : c'est pour installer Obsidian.".into());
    let by_hand = format!(
        "d=$(mktemp -d) && curl -fL -o \"$d/obsidian.deb\" {OBSIDIAN_DEB_URL} && echo \"{OBSIDIAN_DEB_SHA256}  $d/obsidian.deb\" | sha256sum -c - && sudo apt-get install -y \"$d/obsidian.deb\""
    );
    root::install_obsidian_deb(pkexec, &deb, &by_hand, log)?;
    match detect::obsidian(&home) {
        Found::Absent => Err("Obsidian n'apparaît pas après l'installation. Relance l'étape.".into()),
        found => Ok(format!("{} est installé.", found.label("Obsidian"))),
    }
}

#[cfg(target_os = "macos")]
fn signed_by_obsidian(app: &Path) -> Result<(), String> {
    let codesign = Path::new("/usr/bin/codesign");
    let app = app.to_string_lossy();
    let valid = sys::run(codesign, &["--verify", "--deep", "--strict", &app], Duration::from_secs(5 * 60)).ok();
    let info = sys::run(codesign, &["-dv", &app], Duration::from_secs(30));
    if valid && team_id(&info.stderr) == Some(OBSIDIAN_TEAM) {
        Ok(())
    } else {
        Err("Cette copie d'Obsidian n'a pas la signature officielle : je l'ai supprimée par sécurité. \
             Réessaie plus tard."
            .into())
    }
}

/// The signer's team, from `codesign -dv` (which writes on stderr).
#[cfg(target_os = "macos")]
fn team_id(codesign_dv: &str) -> Option<&str> {
    codesign_dv.lines().find_map(|l| l.trim().strip_prefix("TeamIdentifier=")).map(str::trim)
}

/// A mounted disk image, detached when dropped, the step failing or not.
#[cfg(target_os = "macos")]
struct Mount(PathBuf);

#[cfg(target_os = "macos")]
impl Mount {
    fn attach(dmg: &Path, at: &Path) -> Result<Self, String> {
        fs::create_dir(at).map_err(|e| format!("Je n'ai pas pu préparer l'ouverture d'Obsidian ({e})."))?;
        // Guard first: if hdiutil mounts and then times out, the image is still detached.
        let mount = Mount(at.to_path_buf());
        let out = sys::run(
            Path::new("/usr/bin/hdiutil"),
            &["attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", &at.to_string_lossy(), &dmg.to_string_lossy()],
            Duration::from_secs(3 * 60),
        );
        if !out.ok() {
            return Err("Je n'ai pas pu ouvrir le fichier d'Obsidian téléchargé. Réessaie.".into());
        }
        Ok(mount)
    }

    fn app(&self, name: &str) -> String {
        self.0.join(name).to_string_lossy().into_owned()
    }
}

#[cfg(target_os = "macos")]
impl Drop for Mount {
    fn drop(&mut self) {
        let hdiutil = Path::new("/usr/bin/hdiutil");
        let at = self.0.to_string_lossy();
        if !sys::run(hdiutil, &["detach", &at], Duration::from_secs(60)).ok() {
            let _ = sys::run(hdiutil, &["detach", "-force", &at], Duration::from_secs(60));
        }
    }
}

// ── The brain (brain-in-a-box's own install.sh) ─────────────────────────────

/// The French names of the parts install.sh did not leave behind, Claude's and Codex's for the agents
/// agents.json lists. Claude alone: the names and their order of before Codex.
fn missing_parts(brain: detect::Brain, home: &Path) -> Vec<String> {
    let claude = |part: fn(detect::ClaudePart) -> bool| brain.claude.is_none_or(part);
    let codex = |part: fn(detect::CodexPart) -> bool| brain.codex.is_none_or(part);
    [
        (format!("le dossier {}", detect::brain_dir_shown(home)), brain.vault),
        ("les automatismes de Claude".into(), claude(|c| c.hooks)),
        ("la mémoire de recherche".into(), brain.memory),
        ("les compétences de Claude".into(), claude(|c| c.skills)),
        ("les automatismes de Codex".into(), codex(|c| c.hooks)),
        ("les consignes de Codex".into(), codex(|c| c.instructions)),
        ("les compétences de Codex".into(), codex(|c| c.skills)),
        ("ton choix d'IA".into(), brain.chosen),
    ]
    .into_iter()
    .filter(|(_, there)| !there)
    .map(|(name, _)| name)
    .collect()
}

/// install.sh and install.ps1 stop, before anything, on a choice the kit cannot trust (agents.rs): it
/// is said before the sources are even downloaded.
fn choice_trusted(agents: &Agents) -> Result<(), String> {
    if !agents.order.is_empty() {
        return Ok(());
    }
    Err(format!(
        "Je ne sais plus avec quelle IA tu travailles : ton choix ({}) {}. Je n'installe rien. Envoie le rapport à la personne qui t'a conseillé Bibo.",
        "~/.brain-in-a-box/agents.json",
        agents_said(&agents.etat)
    ))
}

/// Why the kit does not trust the choice (_agents.lire()), in words.
pub(crate) fn agents_said(etat: &str) -> &'static str {
    match etat {
        "ferme" => "demande Codex, qui n'est pas encore disponible ici",
        "droits" => "peut être modifié par un autre compte",
        "invalide" => "n'est pas un choix que je connais",
        _ => "est illisible",
    }
}

enum Archive {
    Remote(String),
    Local(PathBuf),
}

/// BIAB_ARCHIVE (tests, support) replaces the GitHub zip: a local .zip or an https URL.
fn archive_source(var: Option<String>) -> Result<Archive, String> {
    match var.filter(|v| !v.trim().is_empty()) {
        None => Ok(Archive::Remote(BRAIN_ZIP.into())),
        Some(v) if v.starts_with("https://") => Ok(Archive::Remote(v)),
        Some(v) if v.contains("://") => Err("BIAB_ARCHIVE n'accepte que les adresses https://.".into()),
        Some(v) => Ok(Archive::Local(PathBuf::from(v))),
    }
}

#[cfg(not(windows))]
fn brain(voyage_key: Option<&str>, log: &mut dyn FnMut(String)) -> Step {
    let key = voyage_key.map(str::trim).filter(|k| !k.is_empty());
    // install.sh writes the key into a curl config line: a malformed one could add
    // its own curl directives there.
    if key.is_some_and(|k| !voyage::well_formed(k)) {
        return Err("Cette clé Voyage contient des caractères inattendus. \
                    Copie-la à nouveau depuis le site de Voyage, ou continue sans clé."
            .into());
    }
    let home = sys::home();
    system_tools_ready()?;
    if !detect::bun_ok(&detect::bun(&home)) {
        return Err("Il faut d'abord installer Bun.".into());
    }
    let agents = agents::current(&home);
    choice_trusted(&agents)?;
    if up_to_date(&home, &agents, key) {
        return Ok("Ton cerveau est déjà installé.".into());
    }

    let base = home.join(".brain-in-a-box");
    fs::create_dir_all(&base).map_err(|e| format!("Je n'ai pas pu créer le dossier .brain-in-a-box ({e})."))?;
    let src = install_sources(&base, &archive_source(std::env::var("BIAB_ARCHIVE").ok())?, log)?;

    log("Je mets en place ton cerveau (quelques minutes)…".into());
    // Without a key from the user, the variable is removed: a key inherited from the
    // app's own environment never sneaks in. A choice made in the window goes the same way, for
    // install.sh to record it; without one, nothing is added.
    let choice = agents::installer_env(&agents);
    let mut env = vec![("VOYAGE_API_KEY", key)];
    env.extend(choice.iter().map(|(k, v)| (*k, Some(v.as_str()))));
    let script = src.join("install.sh");
    let mut stop = Stop::default();
    let started = Instant::now();
    let code = sys::run_streaming(Path::new("/bin/bash"), &[&script.to_string_lossy()], &env, Some(&src), BRAIN_LIMIT, &mut |line| {
        let line = match key {
            Some(k) => line.replace(k, "***"),
            None => line,
        };
        if !stop.see(&line) {
            log(line);
        }
    });
    if code != 0 {
        return Err(stop.message(started.elapsed() >= BRAIN_LIMIT));
    }
    let missing = missing_parts(detect::brain(&home, &agents::current(&home)), &home);
    if !missing.is_empty() {
        return Err(incomplete(&missing));
    }
    Ok(match (detect::embedding_model(&home), key) {
        (Some(_), _) => "Ton cerveau est installé dans Documents/Brain, avec la recherche par le sens.".into(),
        (None, Some(_)) => "Ton cerveau est installé dans Documents/Brain. Voyage n'a pas accepté la clé : \
                            il cherche par mots-clés pour l'instant."
            .into(),
        (None, None) => "Ton cerveau est installé dans Documents/Brain. Il cherche par mots-clés ; \
                         tu pourras ajouter une clé Voyage plus tard."
            .into(),
    })
}

/// install.sh runs git and python3: without the Apple tools, that pops Apple's window.
#[cfg(target_os = "macos")]
fn system_tools_ready() -> Result<(), String> {
    if detect::clt_dir().is_none() {
        return Err("Il faut d'abord installer les outils Apple.".into());
    }
    Ok(())
}

/// install.sh stops on a missing package (it may not ask for a password from here).
#[cfg(target_os = "linux")]
fn system_tools_ready() -> Result<(), String> {
    if !detect::missing_packages(detect::debian_like(&detect::os_release())).is_empty() {
        return Err("Il faut d'abord installer les outils de base.".into());
    }
    Ok(())
}

/// install.ps1 from the sources, without a console and without a question: the key comes
/// through VOYAGE_API_KEY like on a Mac, and -NonInteractive with no input tells it
/// nobody is there to answer. It installs Python, Git, Bun and Obsidian too: the step is
/// done once all of them and the brain are there.
#[cfg(windows)]
fn brain(voyage_key: Option<&str>, log: &mut dyn FnMut(String)) -> Step {
    let key = voyage_key.map(str::trim).filter(|k| !k.is_empty());
    // install.sh writes the key into a curl config line; install.ps1 does not, but a key
    // with quotes or line breaks is no Voyage key either way.
    if key.is_some_and(|k| !voyage::well_formed(k)) {
        return Err("Cette clé Voyage contient des caractères inattendus. \
                    Copie-la à nouveau depuis le site de Voyage, ou continue sans clé."
            .into());
    }
    let home = sys::home();
    let agents = agents::current(&home);
    choice_trusted(&agents)?;
    // Git, Bun or Obsidian missing (an install in simple mode): run again, to bring them.
    if up_to_date(&home, &agents, key) && missing_tools(&home).is_empty() {
        return Ok("Ton cerveau est déjà installé.".into());
    }

    let base = home.join(".brain-in-a-box");
    fs::create_dir_all(&base).map_err(|e| format!("Je n'ai pas pu créer le dossier .brain-in-a-box ({e})."))?;
    let src = install_sources(&base, &archive_source(std::env::var("BIAB_ARCHIVE").ok())?, log)?;

    log("Je mets en place ton cerveau, avec Python, Git, la mémoire et Obsidian (10 à 20 minutes)…".into());
    // Without a key from the user, the variable is removed: a key inherited from the
    // app's own environment never sneaks in. The brain's folder, once one was chosen (by the
    // user, a repair, or install.ps1 itself), goes the same way: this app may have started
    // before it was saved. Otherwise install.ps1 chooses.
    let dir = detect::chosen_brain_dir().map(|d| d.to_string_lossy().into_owned());
    let choice = agents::installer_env(&agents);
    let env = brain_ps1_env(key, dir.as_deref(), &choice);
    let mut sections = Sections::default();
    let mut stop = Stop::default();
    let started = Instant::now();
    let code = run_ps1(&src.join(INSTALLER), &env, &src, BRAIN_LIMIT, &mut |line| {
        let line = match key {
            Some(k) => line.replace(k, "***"),
            None => line,
        };
        if stop.see(&line) {
            return;
        }
        if let Some(progress) = sections.progress(&line) {
            log(progress);
        }
        log(line);
    });
    if code != 0 {
        return Err(stop.message(started.elapsed() >= BRAIN_LIMIT));
    }
    let missing = missing_parts(detect::brain(&home, &agents::current(&home)), &home);
    if !missing.is_empty() {
        return Err(incomplete(&missing));
    }
    installed(&detect::brain_dir_shown(&home), detect::embedding_model(&home).is_some(), key.is_some(), &missing_tools(&home))
}

/// install.ps1's environment from the app: the key, the folder, the choice of AI, and BIAB_APP, the app's
/// word that it keeps the PC awake itself (KeepAwake): install.ps1 then compiles nothing for that.
#[cfg(any(windows, test))]
fn brain_ps1_env<'a>(key: Option<&'a str>, dir: Option<&'a str>, choice: &'a [(&'static str, String)]) -> Vec<(&'a str, Option<&'a str>)> {
    let mut env = vec![("VOYAGE_API_KEY", key), ("BRAIN_DIR", dir), ("BIAB_APP", Some("1"))];
    env.extend(choice.iter().map(|(k, v)| (*k, Some(v.as_str()))));
    env
}

/// The brain is in place: said with what it searches by, and what could not come along. Without Python
/// nothing of it runs; without Git or Bun (no gbrain) it works by keywords, without Obsidian it works all
/// the same: the step succeeded, so the window has no « Réessayer »; Bibo's check, another day, sees what
/// is missing and runs the step again, the notes as they are.
#[cfg(any(windows, test))]
fn installed(at: &str, meaning: bool, key: bool, missing: &[&str]) -> Step {
    if missing.contains(&"Python") {
        return Err("Python manque encore après l'installation : ton cerveau ne peut pas marcher sans lui. \
                    Clique sur « Réessayer » ; si ça recommence, envoie le rapport à la personne qui t'a conseillé Bibo."
            .into());
    }
    let engine: Vec<String> = missing.iter().filter(|t| ["Git", "Bun"].contains(t)).map(|t| t.to_string()).collect();
    let mut said = match (engine.is_empty(), meaning, key) {
        (false, ..) => format!(
            "Ton cerveau est installé dans {at} et il marche : il retrouve tes notes par mots-clés. {} n'{} pas pu s'installer : \
             la recherche par le sens viendra plus tard. Un autre jour, rouvre Bibo et relance la vérification de ton PC : \
             il les installera, tes notes ne changent pas.",
            list_fr(&engine),
            if engine.len() > 1 { "ont" } else { "a" }
        ),
        (true, true, _) => format!("Ton cerveau est installé dans {at}, avec la recherche par le sens."),
        (true, false, true) => {
            format!("Ton cerveau est installé dans {at}. Voyage n'a pas accepté la clé : il cherche par mots-clés pour l'instant.")
        }
        (true, false, false) => {
            format!("Ton cerveau est installé dans {at}. Il cherche par mots-clés ; tu pourras ajouter une clé Voyage plus tard.")
        }
    };
    if missing.contains(&"Obsidian") {
        said.push_str(" Obsidian, pour voir tes notes, n'a pas pu s'installer : il est gratuit sur obsidian.md.");
    }
    Ok(said)
}

/// Nothing to do: the brain complete, with the importer « Importer ma mémoire » runs (an install from
/// before it has all the rest, and the button would send the user back here, round and round), and no
/// Voyage key to add.
fn up_to_date(home: &Path, agents: &Agents, key: Option<&str>) -> bool {
    detect::brain(home, agents).complete()
        && home.join(".claude/hooks/brain/importer-memoire.py").is_file()
        && !(key.is_some() && detect::embedding_model(home).is_none())
}

/// The installer ran to its end, and parts of the brain are not there.
fn incomplete(missing: &[String]) -> String {
    format!(
        "L'installation s'est terminée, mais il manque {}. Clique sur « Réessayer » ; si ça recommence, \
         envoie le rapport à la personne qui t'a conseillé Bibo.",
        list_fr(missing)
    )
}

/// Why an installer stopped, from its lines: its own word for the app (install.ps1's BIAB_ARRET=<cause>),
/// or what Windows or a proxy said when it refused. The line it stopped on never reaches the user's
/// sentence: it stays in the log, under « Détails ».
#[derive(Default)]
struct Stop {
    /// install.ps1's word (BIAB_ARRET), which nothing after it changes.
    said: Option<String>,
    /// The last refusal of the section install.ps1 is in: one of an earlier section was got past (Git's
    /// installer blocked, a warning), and says nothing of what stopped it later.
    refused: Option<&'static str>,
}

impl Stop {
    /// Reads `line`; true when it is install.ps1's word for the app, which stays out of the log: a cause
    /// of the catalogue in a log would make Claude's dossier look forged (repair's CATALOGUE_IDS).
    fn see(&mut self, line: &str) -> bool {
        let line = line.trim();
        if let Some(id) = line.strip_prefix("BIAB_ARRET=") {
            self.said = Some(id.trim().to_string());
            return true;
        }
        if line.starts_with("=== ") {
            self.refused = None;
        } else if let Some(cause) = refusal(line) {
            self.refused = Some(cause);
        }
        false
    }

    fn cause(&self) -> Option<&str> {
        self.said.as_deref().or(self.refused)
    }

    /// The brain step's sentence: what happened and what to do.
    fn message(&self, timed_out: bool) -> String {
        if let Some(m) = self.cause().and_then(repair::stop_message) {
            return m;
        }
        if timed_out {
            return "L'installation de ton cerveau a pris trop de temps : ta connexion est sans doute très lente. \
                    Attends d'avoir un Wi-Fi, puis clique sur « Réessayer »."
                .into();
        }
        "L'installation de ton cerveau s'est arrêtée avant la fin. Clique sur « Réessayer » ; si ça recommence, \
         envoie le rapport à la personne qui t'a conseillé Bibo."
            .into()
    }
}

/// What Windows or a proxy says when it refuses, in English or in French: a cause of the catalogue.
fn refusal(line: &str) -> Option<&'static str> {
    let l = line.to_lowercase();
    let said = |words: &[&str]| words.iter().any(|w| l.contains(w));
    // A locked PowerShell; or an execution policy the company imposes (AllSigned, Restricted), which wins over
    // -ExecutionPolicy Bypass: about_Execution_Policies is named in every language of Windows.
    if said(&["language mode", "mode de langage", "about_execution_policies", "not digitally signed", "signé numériquement"]) {
        Some("powershell_verrouille")
    } else if said(&["(407)", "proxy authentication required", "authentification proxy"]) {
        Some("proxy_entreprise")
    } else if said(&["blocked by group policy", "stratégie de groupe"]) {
        Some("strategie_entreprise")
    } else if said(&["contains a virus", "contient un virus"]) {
        Some("antivirus_bloque")
    } else {
        None
    }
}

/// The programs install.ps1 installs along with the brain, by name, when missing.
#[cfg(windows)]
fn missing_tools(home: &Path) -> Vec<&'static str> {
    [
        ("Python", detect::python().is_some()),
        ("Git", detect::git() != Found::Absent),
        ("Bun", detect::bun_ok(&detect::bun(home))),
        ("Obsidian", detect::obsidian(home) != Found::Absent),
    ]
    .into_iter()
    .filter(|(_, there)| !there)
    .map(|(name, _)| name)
    .collect()
}

/// install.ps1's sections ("=== Preflight ==="), in its order, as the window says them.
/// A section it adds later is simply not announced.
#[cfg(windows)]
const SECTIONS: &[(&str, &str)] = &[
    ("Preflight", "Je vérifie Python et Git, et je les installe s'il le faut"),
    ("Recherche par le sens", "Je prépare la recherche par le sens"),
    ("Brain vault", "Je crée ton dossier Brain"),
    ("Brain hooks", "J'installe les automatismes de Claude"),
    ("Recherche par mots-cles", "Je prépare la recherche par mots-clés"),
    ("Hooks Claude", "Je branche les automatismes dans Claude"),
    ("CLAUDE.md global", "J'ajoute tes consignes pour Claude"),
    ("Skills Claude Code", "J'installe les compétences de Claude"),
    ("Historique du vault", "Je garde l'historique de tes notes"),
    ("Memoire gbrain", "J'installe la mémoire, 2 à 5 minutes"),
    ("Task Scheduler", "Je programme l'entretien de nuit"),
    ("Claude Code + Obsidian", "J'installe Obsidian"),
    ("Verification", "Je vérifie que tout répond"),
];

/// install.ps1's sections that run only when an agent is chosen: Codex's. Said without a count, so that a
/// brain of Claude alone still counts 13, as before Codex.
#[cfg(windows)]
const CHOSEN_SECTIONS: &[(&str, &str)] = &[("Codex", "Je branche Codex")];

/// Where install.ps1 is: each section it starts becomes one line for the window,
/// "▸ Je crée ton dossier Brain (3 sur 13)…". The window shows the latest one.
#[cfg(windows)]
#[derive(Default)]
struct Sections {
    seen: usize,
}

#[cfg(windows)]
impl Sections {
    fn progress(&mut self, line: &str) -> Option<String> {
        let title = line.trim().strip_prefix("=== ")?.strip_suffix(" ===")?;
        if let Some((_, said)) = CHOSEN_SECTIONS.iter().find(|(start, _)| title.starts_with(start)) {
            return Some(format!("▸ {said}…"));
        }
        let n = SECTIONS.iter().position(|(start, _)| title.starts_with(start))?;
        // Never backwards: a title seen twice does not move the count.
        (n >= self.seen).then(|| {
            self.seen = n + 1;
            format!("▸ {} ({} sur {})…", SECTIONS[n].1, n + 1, SECTIONS.len())
        })
    }
}

/// Runs a PowerShell script as -File does: its exit code is the step's, a script that
/// throws ends with 1. No profile, no execution policy in the way, no console window.
#[cfg(windows)]
fn run_ps1(
    script: &Path,
    env: &[(&str, Option<&str>)],
    cwd: &Path,
    limit: Duration,
    log: &mut dyn FnMut(String),
) -> i32 {
    let args = ps1_args(script);
    let args: Vec<&str> = args.iter().map(String::as_str).collect();
    sys::run_streaming(&sys::powershell(), &args, env, Some(cwd), limit, log)
}

#[cfg(windows)]
fn ps1_args(script: &Path) -> Vec<String> {
    ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"].map(String::from).into_iter().chain([script.to_string_lossy().into_owned()]).collect()
}

/// Puts a fresh copy of the brain-in-a-box sources in `<base>/src` and returns that path.
fn install_sources(base: &Path, archive: &Archive, log: &mut dyn FnMut(String)) -> Result<PathBuf, String> {
    // Work inside `base` so the final move is a rename on the same disk.
    let tmp = TempDir::new_in(base)?;
    let zip = match archive {
        Archive::Remote(url) => {
            let z = tmp.path().join("brain.zip");
            download(url, &z, "le moteur du cerveau", None, Duration::from_secs(15 * 60), log)?;
            z
        }
        Archive::Local(p) => p.clone(),
    };
    let out = tmp.path().join("x");
    let unzip = unzip(&zip, &out);
    if !unzip.ok() {
        log(unzip.stderr.trim().to_string());
        return Err("Le fichier du cerveau est illisible. Réessaie.".into());
    }
    let root = source_root(&out).ok_or("Le fichier téléchargé ne contient pas le moteur du cerveau. Réessaie plus tard.")?;

    let src = base.join("src");
    let old = tmp.path().join("old");
    let had_old = fs::symlink_metadata(&src).is_ok();
    if had_old {
        rename(&src, &old).map_err(|e| format!("Je n'ai pas pu remplacer l'ancienne version du cerveau ({e})."))?;
    }
    if let Err(e) = rename(&root, &src) {
        if had_old {
            let _ = rename(&old, &src);
        }
        return Err(format!("Je n'ai pas pu mettre en place le moteur du cerveau ({e})."));
    }
    Ok(src)
}

#[cfg(target_os = "macos")]
fn unzip(zip: &Path, out: &Path) -> sys::Output {
    sys::run(
        Path::new("/usr/bin/ditto"),
        &["-x", "-k", &zip.to_string_lossy(), &out.to_string_lossy()],
        Duration::from_secs(5 * 60),
    )
}

/// Info-ZIP's unzip, which keeps the files' modes (the paquets step brings it). It leaves
/// out the "../" of a name: nothing lands outside `out`.
#[cfg(target_os = "linux")]
fn unzip(zip: &Path, out: &Path) -> sys::Output {
    let Some(unzip) = sys::find_tool("unzip") else {
        return sys::Output { code: -1, stdout: String::new(), stderr: "unzip est introuvable".into() };
    };
    sys::run(&unzip, &["-q", &zip.to_string_lossy(), "-d", &out.to_string_lossy()], Duration::from_secs(5 * 60))
}

/// Windows' own tar (10 1803 and later) reads zips. Not Git's tar, which does not.
#[cfg(windows)]
fn unzip(zip: &Path, out: &Path) -> sys::Output {
    if let Err(e) = fs::create_dir(out) {
        return sys::Output { code: -1, stdout: String::new(), stderr: e.to_string() };
    }
    let (zip, out) = (zip.to_string_lossy(), out.to_string_lossy());
    sys::run(&sys::system32("tar.exe"), &["-x", "-f", &zip, "-C", &out], Duration::from_secs(5 * 60))
}

#[cfg(not(windows))]
fn rename(from: &Path, to: &Path) -> io::Result<()> {
    fs::rename(from, to)
}

/// Freshly unpacked files are still being read by the antivirus for a moment: a folder
/// cannot be moved meanwhile ("access denied"). A few tries, a second apart.
#[cfg(windows)]
fn rename(from: &Path, to: &Path) -> io::Result<()> {
    let mut tries = 0;
    loop {
        match fs::rename(from, to) {
            Err(e) if e.kind() == io::ErrorKind::PermissionDenied && tries < 10 => {
                tries += 1;
                thread::sleep(Duration::from_secs(1));
            }
            res => return res,
        }
    }
}

/// The extracted folder holding the installer and vault-skeleton: the folder itself, or
/// its only subfolder (GitHub zips wrap everything in "<repo>-<branch>/").
fn source_root(dir: &Path) -> Option<PathBuf> {
    let is_root = |d: &Path| d.join(INSTALLER).is_file() && d.join("vault-skeleton").is_dir();
    if is_root(dir) {
        return Some(dir.to_path_buf());
    }
    let mut found = fs::read_dir(dir)
        .ok()?
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_ok_and(|t| t.is_dir()))
        .map(|e| e.path())
        .filter(|p| is_root(p));
    let first = found.next()?;
    found.next().is_none().then_some(first)
}

// ── After the install ───────────────────────────────────────────────────────

/// The user logs in with Claude Code's own command, in Terminal (PowerShell on
/// Windows): the app never sees nor handles Anthropic credentials.
pub fn open_claude_login() -> Result<(), String> {
    let Some(claude) = detect::claude_bin() else {
        return Err("Claude Code n'est pas encore installé.".into());
    };
    open_in_terminal("claude-login", &login_script(&sys::tool_path(), &claude))
}

/// The guide's « Mettre à jour », for a Claude Code too old for it: `claude update`, in a window the
/// user sees.
pub fn open_claude_update() -> Result<(), String> {
    let Some(claude) = detect::claude_bin() else {
        return Err("Claude Code n'est pas encore installé.".into());
    };
    open_in_terminal("claude-update", &claude_script(&sys::tool_path(), &claude, "update"))
}

/// Codex's login, with the user's ChatGPT account (§4.3): its own command, `codex login`, in Terminal
/// (PowerShell on Windows), for the CODEX_HOME of the agents chosen. Never an API key: none reaches it,
/// and its login goes to auth.json, the file the kit reads. The app never logs anyone out.
pub fn open_codex_login() -> Result<(), String> {
    let home = sys::home();
    let (codex, codex_home) = chosen_codex(&home)?;
    open_in_terminal("codex-login", &codex_login_script(&sys::tool_path(), &codex, codex_home_var(&home, &codex_home)))
}

/// How Codex is logged in, for the window: `codex::login`, never what Codex printed.
pub fn codex_login() -> codex::Login {
    let agents = agents::current(&sys::home());
    let listed = agents.lists(Engine::Codex);
    match agents.codex_home.filter(|_| listed).map(|h| codex::Setup::for_user(&h)) {
        Some(Ok(setup)) => codex::login(&setup),
        _ => codex::Login::Unknown,
    }
}

/// Codex and its folder, once Codex is one of the agents and installed.
fn chosen_codex(home: &Path) -> Result<(PathBuf, PathBuf), String> {
    let agents = agents::current(home);
    let listed = agents.lists(Engine::Codex);
    let Some(codex_home) = agents.codex_home.filter(|_| listed) else {
        return Err("Tu n'as pas choisi de travailler avec Codex.".into());
    };
    let setup = codex::Setup::for_user(&codex_home).map_err(|e| format!("Je n'ai pas pu créer mon dossier de travail ({e})."))?;
    let codex = codex::find(&setup).ok_or("Codex n'est pas encore installé.")?;
    Ok((codex, codex_home))
}

/// CODEX_HOME for a window the app opens: only when it is not Codex's own default, ~/.codex. Codex
/// refuses a CODEX_HOME that does not exist, and without the variable it finds that one by itself.
fn codex_home_var<'a>(home: &Path, codex_home: &'a Path) -> Option<&'a Path> {
    (codex_home != home.join(".codex")).then_some(codex_home)
}

/// The preferred agent, in the brain, with the guide: Claude Code as before Codex, or Codex.
pub fn start_onboarding() -> Result<(), String> {
    let home = sys::home();
    match agents::current(&home).order.first() {
        Some(Engine::Claude) => {}
        Some(Engine::Codex) => return start_codex_onboarding(&home),
        None => return Err("Je ne sais plus avec quelle IA tu travailles : ton choix est illisible.".into()),
    }
    let Some(claude) = detect::claude_bin() else {
        return Err("Claude Code n'est pas encore installé.".into());
    };
    let Some(vault) = detect::vault(&home) else {
        return Err("Ton cerveau n'est pas encore installé.".into());
    };
    let guide = onboarding_guide(&home)?;
    open_in_terminal("onboarding", &onboarding_script(&sys::tool_path(), &vault, &guide, &claude, onboarding_mode(&vault, &home)))
}

/// « Parler à Claude »: Claude Code in the brain's folder, with no prompt, in a terminal window of the
/// system's: Windows Terminal when Windows has it, else PowerShell's own window; Terminal on a Mac; the
/// desktop's on Linux. Started there, Claude reads the brain's CLAUDE.md and lessons, and the kit's hooks run.
pub fn start_claude() -> Result<(), String> {
    let home = sys::home();
    open_window("claude", &talk_to_claude(detect::claude_bin(), detect::vault(&home), detect::claude_logged_in)?)
}

/// The window's script, once Claude Code is logged in: otherwise it would open on its login menu, in
/// English, with API billing among its choices.
fn talk_to_claude(claude: Option<PathBuf>, vault: Option<PathBuf>, logged_in: impl FnOnce() -> bool) -> Result<String, String> {
    let Some(claude) = claude else {
        return Err("Claude Code n'est pas encore installé.".into());
    };
    let Some(vault) = vault else {
        return Err("Ton cerveau n'est pas encore installé.".into());
    };
    if !logged_in() {
        return Err("Connecte d'abord Claude à ton compte Claude (abonnement Pro ou Max), puis recommence.".into());
    }
    Ok(claude_in_brain_script(&sys::tool_path(), &vault, &claude))
}

/// What the window says before Claude, which speaks English until it is spoken to: what to type, the
/// question it may ask first (trust this folder), how to end.
const TALK_INTRO: [&str; 3] = [
    "Claude t'écoute : écris ta question, puis appuie sur Entrée.",
    "S'il te demande d'abord en anglais si tu fais confiance à ce dossier (« trust »), choisis la première réponse (« Yes ») : c'est ton cerveau.",
    "Pour finir, ferme cette fenêtre.",
];

fn start_codex_onboarding(home: &Path) -> Result<(), String> {
    let (codex, codex_home) = chosen_codex(home)?;
    let setup = codex::Setup::for_user(&codex_home).map_err(|e| format!("Je n'ai pas pu créer mon dossier de travail ({e})."))?;
    open_in_terminal("onboarding", &codex_onboarding(home, &setup, &codex)?)
}

/// The onboarding's window for Codex, only on the login the kit uses (§4.3): an API key in auth.json
/// would bill the whole session, whatever the window takes out of the environment, and a Codex that
/// is not logged in offers a key on its first screen.
fn codex_onboarding(home: &Path, setup: &codex::Setup, codex: &Path) -> Result<String, String> {
    if let Some(why) = onboarding_refused(codex::login(setup)) {
        return Err(why.into());
    }
    let Some(vault) = detect::vault(home) else {
        return Err("Ton cerveau n'est pas encore installé.".into());
    };
    let guide = onboarding_guide(home)?;
    Ok(codex_onboarding_script(&sys::tool_path(), &vault, &guide, codex, codex_home_var(home, &setup.codex_home)))
}

fn onboarding_refused(login: codex::Login) -> Option<&'static str> {
    use codex::Login;
    match login {
        Login::Chatgpt => None,
        Login::Billed => Some("Codex est connecté avec une clé API, qui serait facturée : connecte-le d'abord à ton compte ChatGPT."),
        Login::Managed => Some("Codex est connecté avec un compte ChatGPT d'entreprise : connecte-le d'abord avec ton abonnement ChatGPT personnel."),
        Login::System => Some("Codex lit des réglages de cet ordinateur qu'un autre compte peut modifier : je ne l'ouvre pas pour ça."),
        Login::LoggedOut | Login::Unknown => Some("Connecte d'abord Codex à ton compte ChatGPT."),
    }
}

/// The guide from the sources the brain step unpacked; when the brain was installed
/// from a terminal (git clone + install.sh) there are none, so the copy built into the app.
fn onboarding_guide(home: &Path) -> Result<PathBuf, String> {
    let from_sources = home.join(".brain-in-a-box/src/onboarding/ONBOARDING.md");
    if from_sources.is_file() {
        return Ok(from_sources);
    }
    let dir = home.join(".brain-in-a-box/onboarding");
    fs::create_dir_all(&dir).map_err(|e| format!("Je n'ai pas pu préparer le guide ({e})."))?;
    let guide = dir.join("ONBOARDING.md");
    write_atomic(&guide, include_str!("../../../onboarding/ONBOARDING.md").as_bytes(), 0o644)
        .map_err(|e| format!("Je n'ai pas pu préparer le guide ({e})."))?;
    Ok(guide)
}

/// The interview's first message, in French, with the brain's real folder: it is not always
/// Documents/Brain (install.ps1 puts a new one in the profile's folder when Windows guards Documents),
/// which a guide from before (sources installed earlier) still names; Claude writes without asking only
/// in its working folder. In acceptEdits, Claude is told so: the guide has it warn of a permission asked
/// in English before its first write, which then never comes.
fn onboarding_prompt(guide: &Path, vault: &Path, accept_edits: bool) -> String {
    let mut p = format!(
        "Lis {} et suis-le pour me personnaliser. Mon cerveau est dans {}. Parle-moi en français, avec des mots simples : \
         je ne suis pas informaticien. Si le guide dit ~/Documents/Brain, c'est ce dossier.",
        guide.display(),
        vault.display()
    );
    if accept_edits {
        p.push_str(" Tu es lancé en mode acceptEdits : tu écris dans ce dossier sans demander la permission, ne m'annonce donc aucune demande.");
    }
    p
}

/// The interview writes the user's notes in the brain without asking each time: that is what it is for.
/// Claude Code then accepts, in its working folder only (the brain), edits and the file commands (mkdir,
/// touch, cp, mv, rm, rmdir, sed; with its PowerShell tool, Set-Content, Add-Content, Clear-Content and
/// Remove-Item); it still asks for any other command, for a path outside the brain, and for its protected
/// paths (.git, .claude).
const ONBOARDING_MODE: [&str; 2] = ["--permission-mode", "acceptEdits"];

/// acceptEdits only where that is the brain alone: a folder of the kit's (its CLAUDE.md and Profile), never
/// the home folder or one above it, Documents or the Desktop (where Windows put them: OneDrive moves both),
/// a folder holding one, a drive. A BRAIN_DIR set to Documents would let Claude write, unasked, Windows
/// PowerShell's profile there (Documents\WindowsPowerShell), which runs at the next start of PowerShell, or
/// remove anything of Documents. Elsewhere Claude asks, as by default.
fn onboarding_mode(vault: &Path, home: &Path) -> &'static [&'static str] {
    if own_brain(vault, home, &rapport::user_folders(home)) {
        &ONBOARDING_MODE
    } else {
        &[]
    }
}

/// Compared as the disk names them, a link followed and Windows' letters and short names as they are:
/// no other name for Documents gets through.
fn own_brain(vault: &Path, home: &Path, shared: &[PathBuf]) -> bool {
    if !vault.is_absolute() {
        return false;
    }
    let real = |p: &Path| fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf());
    let brain = real(vault);
    brain.parent().is_some()
        && !real(home).starts_with(&brain)
        && !shared.iter().any(|d| real(d).starts_with(&brain))
        && brain.join("CLAUDE.md").is_file()
        && brain.join("Profile").is_dir()
}

fn login_script(path: &std::ffi::OsStr, claude: &Path) -> String {
    claude_script(path, claude, "auth login")
}

/// Claude Code with one of the app's own fixed subcommands (`auth login`, `update`), never a word of the user's.
#[cfg(not(windows))]
fn claude_script(path: &std::ffi::OsStr, claude: &Path, subcommand: &str) -> String {
    format!(
        "#!/bin/bash\nexport PATH={}\n{} {subcommand}\necho\necho 'Tu peux fermer cette fenêtre.'\n",
        sh_quote(&path.to_string_lossy()),
        sh_quote(&claude.to_string_lossy())
    )
}

#[cfg(not(windows))]
fn onboarding_script(path: &std::ffi::OsStr, vault: &Path, guide: &Path, claude: &Path, mode: &[&str]) -> String {
    format!(
        "#!/bin/bash\nexport PATH={}\ncd {} || exit 1\n{} {} {}\necho\necho 'Tu peux fermer cette fenêtre.'\n",
        sh_quote(&path.to_string_lossy()),
        sh_quote(&vault.to_string_lossy()),
        sh_quote(&claude.to_string_lossy()),
        mode.join(" "),
        sh_quote(&onboarding_prompt(guide, vault, !mode.is_empty()))
    )
}

/// Claude Code alone, in the brain, after a word in French.
#[cfg(not(windows))]
fn claude_in_brain_script(path: &std::ffi::OsStr, vault: &Path, claude: &Path) -> String {
    let intro = TALK_INTRO.map(|l| format!("echo {}", sh_quote(l))).join("\n");
    format!(
        "#!/bin/bash\nexport PATH={}\ncd {} || exit 1\nprintf '\\033]0;Claude\\007'\n{intro}\necho\n{}\necho\necho 'Tu peux fermer cette fenêtre.'\n",
        sh_quote(&path.to_string_lossy()),
        sh_quote(&vault.to_string_lossy()),
        sh_quote(&claude.to_string_lossy())
    )
}

/// The keys that would have Codex bill an API: never in a window the app opens for it.
const CODEX_KEYS: &[&str] = &["OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_BASE_URL"];
/// Where `codex login` keeps the login: auth.json, the file the kit reads (codex.rs passes the same).
const CODEX_LOGIN_STORE: &str = r#"cli_auth_credentials_store="file""#;

/// Terminal runs the user's shell profile first, which may export a CODEX_HOME of its own: the
/// folder agents.json records is the one set, and without one (~/.codex) none is left.
#[cfg(not(windows))]
fn codex_env_lines(path: &std::ffi::OsStr, codex_home: Option<&Path>) -> String {
    let mut s = format!("#!/bin/bash\nexport PATH={}\n", sh_quote(&path.to_string_lossy()));
    match codex_home {
        Some(h) => s.push_str(&format!("export CODEX_HOME={}\n", sh_quote(&h.to_string_lossy()))),
        None => s.push_str("unset CODEX_HOME\n"),
    }
    s.push_str(&format!("unset {}\n", CODEX_KEYS.join(" ")));
    s
}

#[cfg(not(windows))]
fn codex_login_script(path: &std::ffi::OsStr, codex: &Path, codex_home: Option<&Path>) -> String {
    format!(
        "{}{} -c {} login\necho\necho 'Tu peux fermer cette fenêtre.'\n",
        codex_env_lines(path, codex_home),
        sh_quote(&codex.to_string_lossy()),
        sh_quote(CODEX_LOGIN_STORE)
    )
}

#[cfg(not(windows))]
fn codex_onboarding_script(path: &std::ffi::OsStr, vault: &Path, guide: &Path, codex: &Path, codex_home: Option<&Path>) -> String {
    format!(
        "{}cd {} || exit 1\n{} -c {} {}\necho\necho 'Tu peux fermer cette fenêtre.'\n",
        codex_env_lines(path, codex_home),
        sh_quote(&vault.to_string_lossy()),
        sh_quote(&codex.to_string_lossy()),
        sh_quote(CODEX_LOGIN_STORE),
        sh_quote(&onboarding_prompt(guide, vault, false))
    )
}

/// Single quotes keep everything literal in a POSIX shell, except the quote itself,
/// written '\''.
#[cfg(not(windows))]
fn sh_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', r"'\''"))
}

#[cfg(windows)]
fn claude_script(path: &std::ffi::OsStr, claude: &Path, subcommand: &str) -> String {
    format!(
        "$ErrorActionPreference = 'Stop'\r\n$env:Path = {}\r\n& {} {subcommand}\r\nWrite-Host ''\r\nWrite-Host 'Tu peux fermer cette fenêtre.'\r\n",
        ps_quote(&path.to_string_lossy()),
        ps_quote(&claude.to_string_lossy())
    )
}

#[cfg(windows)]
fn onboarding_script(path: &std::ffi::OsStr, vault: &Path, guide: &Path, claude: &Path, mode: &[&str]) -> String {
    format!(
        "$ErrorActionPreference = 'Stop'\r\n$env:Path = {}\r\nSet-Location -LiteralPath {}\r\n& {} {} {}\r\n\
         Write-Host ''\r\nWrite-Host 'Tu peux fermer cette fenêtre.'\r\n",
        ps_quote(&path.to_string_lossy()),
        ps_quote(&vault.to_string_lossy()),
        ps_quote(&claude.to_string_lossy()),
        mode.join(" "),
        ps_quote(&onboarding_prompt(guide, vault, !mode.is_empty()))
    )
}

#[cfg(windows)]
fn claude_in_brain_script(path: &std::ffi::OsStr, vault: &Path, claude: &Path) -> String {
    let intro = TALK_INTRO.map(|l| format!("Write-Host {}", ps_quote(l))).join("\r\n");
    format!(
        "$ErrorActionPreference = 'Stop'\r\n$env:Path = {}\r\nSet-Location -LiteralPath {}\r\n$Host.UI.RawUI.WindowTitle = 'Claude'\r\n\
         {intro}\r\nWrite-Host ''\r\n& {}\r\nWrite-Host ''\r\nWrite-Host 'Tu peux fermer cette fenêtre.'\r\n",
        ps_quote(&path.to_string_lossy()),
        ps_quote(&vault.to_string_lossy()),
        ps_quote(&claude.to_string_lossy())
    )
}

/// PowerShell runs the user's profile first, which may set a CODEX_HOME of its own: see above.
#[cfg(windows)]
fn codex_env_lines(path: &std::ffi::OsStr, codex_home: Option<&Path>) -> String {
    let mut s = format!("$ErrorActionPreference = 'Stop'\r\n$env:Path = {}\r\n", ps_quote(&path.to_string_lossy()));
    match codex_home {
        Some(h) => s.push_str(&format!("$env:CODEX_HOME = {}\r\n", ps_quote(&h.to_string_lossy()))),
        None => s.push_str("Remove-Item -LiteralPath Env:CODEX_HOME -ErrorAction SilentlyContinue\r\n"),
    }
    for k in CODEX_KEYS {
        s.push_str(&format!("Remove-Item -LiteralPath Env:{k} -ErrorAction SilentlyContinue\r\n"));
    }
    s
}

#[cfg(windows)]
fn codex_login_script(path: &std::ffi::OsStr, codex: &Path, codex_home: Option<&Path>) -> String {
    format!(
        "{}& {} -c {} login\r\nWrite-Host ''\r\nWrite-Host 'Tu peux fermer cette fenêtre.'\r\n",
        codex_env_lines(path, codex_home),
        ps_quote(&codex.to_string_lossy()),
        ps_quote(CODEX_LOGIN_STORE)
    )
}

#[cfg(windows)]
fn codex_onboarding_script(path: &std::ffi::OsStr, vault: &Path, guide: &Path, codex: &Path, codex_home: Option<&Path>) -> String {
    format!(
        "{}Set-Location -LiteralPath {}\r\n& {} -c {} {}\r\nWrite-Host ''\r\nWrite-Host 'Tu peux fermer cette fenêtre.'\r\n",
        codex_env_lines(path, codex_home),
        ps_quote(&vault.to_string_lossy()),
        ps_quote(&codex.to_string_lossy()),
        ps_quote(CODEX_LOGIN_STORE),
        ps_quote(&onboarding_prompt(guide, vault, false))
    )
}

/// A PowerShell single-quoted string: everything literal but the quote, written twice.
/// PowerShell also takes the typographic quotes ‘ ’ ‚ ‛ for one: each is doubled too.
#[cfg(windows)]
pub(crate) fn ps_quote(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('\'');
    for c in s.chars() {
        if matches!(c, '\'' | '\u{2018}' | '\u{2019}' | '\u{201A}' | '\u{201B}') {
            out.push(c);
        }
        out.push(c);
    }
    out.push('\'');
    out
}

/// Opens `script` in a window the user sees: Terminal on a Mac (a .command file),
/// PowerShell on Windows (a .ps1), the desktop's terminal on Linux (a .sh it runs).
/// `name` is the file's name without its extension.
#[cfg(target_os = "macos")]
pub(crate) fn open_in_terminal(name: &str, script: &str) -> Result<(), String> {
    let file = terminal_file(&format!("{name}.command"), script.as_bytes(), 0o700)?;
    if sys::run(Path::new("/usr/bin/open"), &["-a", "Terminal", &file.to_string_lossy()], Duration::from_secs(15)).ok() {
        Ok(())
    } else {
        Err("Je n'ai pas pu ouvrir le Terminal.".into())
    }
}

#[cfg(target_os = "linux")]
pub(crate) fn open_in_terminal(name: &str, script: &str) -> Result<(), String> {
    sys::open_terminal_window(&terminal_file(&format!("{name}.sh"), script.as_bytes(), 0o700)?)
}

#[cfg(windows)]
pub(crate) fn open_in_terminal(name: &str, script: &str) -> Result<(), String> {
    sys::open_console(&write_terminal_script(name, script)?, None)
}

/// A window to talk in: `open_in_terminal`'s, but on Windows, Windows Terminal when Windows has it.
#[cfg(not(windows))]
fn open_window(name: &str, script: &str) -> Result<(), String> {
    open_in_terminal(name, script)
}

#[cfg(windows)]
fn open_window(name: &str, script: &str) -> Result<(), String> {
    let file = write_terminal_script(name, script)?;
    let Some(wt) = windows_terminal() else { return sys::open_console(&file, None) };
    let started = std::process::Command::new(wt)
        .args(wt_args(&file, &sys::powershell()))
        // As sys::open_console: PowerShell 7's module folders would hide Windows PowerShell's own commands.
        .env_remove("PSModulePath")
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn();
    match started {
        Ok(_) => Ok(()),
        Err(_) => sys::open_console(&file, None),
    }
}

/// Windows Terminal's command (an app execution alias, which only its link's own metadata shows), when it
/// is there: on every Windows 11, and on a Windows 10 that got it from the Store.
#[cfg(windows)]
fn windows_terminal() -> Option<PathBuf> {
    let wt = PathBuf::from(std::env::var_os("LOCALAPPDATA")?).join(r"Microsoft\WindowsApps\wt.exe");
    fs::symlink_metadata(&wt).is_ok().then_some(wt)
}

/// A new window of Windows Terminal, in the script's folder, running it with Windows PowerShell as
/// `sys::open_console` does. Only the folder may hold a space or an accent: it goes as an option of its
/// own (-d), and Windows Terminal rebuilds the command line from words that hold none.
#[cfg(any(windows, test))]
fn wt_args(script: &Path, powershell: &Path) -> Vec<std::ffi::OsString> {
    let folder = script.parent().unwrap_or(Path::new("."));
    let name = script.file_name().unwrap_or_default();
    let mut args: Vec<std::ffi::OsString> = ["-w", "new", "-d"].iter().map(Into::into).collect();
    args.push(folder.into());
    args.push(powershell.into());
    args.extend(["-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-File"].iter().map(Into::into));
    args.push(name.into());
    args
}

/// Windows PowerShell reads a script without a byte order mark in the old code page:
/// with one, accents in a path or a sentence survive.
#[cfg(windows)]
pub(crate) fn write_terminal_script(name: &str, script: &str) -> Result<PathBuf, String> {
    let bytes = [&[0xEF, 0xBB, 0xBF][..], script.as_bytes()].concat();
    terminal_file(&format!("{name}.ps1"), &bytes, 0o600)
}

fn terminal_file(file: &str, bytes: &[u8], mode: u32) -> Result<PathBuf, String> {
    let dir = sys::home().join(".brain-in-a-box");
    fs::create_dir_all(&dir).map_err(|e| format!("Je n'ai pas pu créer le dossier .brain-in-a-box ({e})."))?;
    let file = dir.join(file);
    write_atomic(&file, bytes, mode).map_err(|e| format!("Je n'ai pas pu préparer la fenêtre de commandes ({e})."))?;
    Ok(file)
}

pub fn open_vault() -> Result<(), String> {
    open_in_obsidian(None)
}

/// A note of the brain (an import's), the vault opened on it.
pub fn open_note(note: &Path) -> Result<(), String> {
    open_in_obsidian(Some(note))
}

/// The vault in Obsidian, on `note` when given and the vault is one Obsidian knows already.
fn open_in_obsidian(note: Option<&Path>) -> Result<(), String> {
    let home = sys::home();
    let Some(vault) = detect::vault(&home) else {
        return Err("Ton cerveau n'est pas encore installé.".into());
    };
    let Some(app) = obsidian_program(&home) else {
        return Err("Obsidian n'est pas encore installé.".into());
    };
    if note.is_some_and(|n| !n.starts_with(&vault) || !n.is_file()) {
        return Err("Cette note n'est plus dans ton cerveau.".into());
    }
    let vault = vault.to_string_lossy();
    let by_uri = format!("obsidian://open?path={}", percent_encode(&note.map_or_else(|| vault.to_string(), |n| n.to_string_lossy().into_owned())));
    // A running Obsidian rewrites its vault list when it quits and would drop our
    // entry: ask it directly instead.
    if obsidian_running() {
        return open_obsidian(&app, Some(&by_uri));
    }
    let config = obsidian_config(&home);
    let existing = match fs::read_to_string(&config) {
        Ok(s) => Some(s),
        Err(e) if e.kind() == io::ErrorKind::NotFound => None,
        Err(_) => return unregistered(&app, &vault),
    };
    let now = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0);
    match add_vault(existing.as_deref(), &vault, &random_hex(), now) {
        VaultList::AlreadyThere => open_obsidian(&app, Some(&by_uri)),
        VaultList::Updated(json) => {
            let mode = fs::metadata(&config).map(|m| perms::mode(&m) & 0o777).unwrap_or(0o644);
            let saved =
                config.parent().map_or(Ok(()), fs::create_dir_all).and_then(|_| write_atomic(&config, json.as_bytes(), mode));
            if saved.is_err() {
                return unregistered(&app, &vault);
            }
            open_obsidian(&app, None)
        }
        VaultList::Unreadable => unregistered(&app, &vault),
    }
}

/// Obsidian's vault list could not be updated: open Obsidian anyway and say how to
/// pick the folder by hand.
#[cfg(not(target_os = "linux"))]
fn unregistered(app: &Path, vault: &str) -> Result<(), String> {
    open_obsidian(app, None)?;
    Err(unregistered_message(vault))
}

#[cfg(target_os = "linux")]
fn unregistered(app: &detect::Obsidian, vault: &str) -> Result<(), String> {
    open_obsidian(app, None)?;
    Err(unregistered_message(vault))
}

fn unregistered_message(vault: &str) -> String {
    let folder = if cfg!(windows) { format!("le dossier {vault}") } else { "Documents > Brain".into() };
    format!("Je n'ai pas pu ajouter ton cerveau à Obsidian. Dans Obsidian, choisis « Ouvrir un dossier comme coffre », puis {folder}.")
}

#[cfg(target_os = "macos")]
fn obsidian_program(home: &Path) -> Option<PathBuf> {
    detect::obsidian_app(home)
}

#[cfg(windows)]
fn obsidian_program(home: &Path) -> Option<PathBuf> {
    detect::obsidian_exe(home)
}

#[cfg(target_os = "linux")]
fn obsidian_program(home: &Path) -> Option<detect::Obsidian> {
    detect::obsidian_install(home)
}

#[cfg(target_os = "macos")]
fn obsidian_config(home: &Path) -> PathBuf {
    home.join("Library/Application Support/obsidian/obsidian.json")
}

/// Electron's folder for Obsidian: ~/.config, or the sandbox's own for a snap or a Flatpak.
#[cfg(target_os = "linux")]
fn obsidian_config(home: &Path) -> PathBuf {
    let config = match detect::obsidian_install(home).map(|o| o.kind) {
        Some(detect::ObsidianKind::Snap) => home.join("snap/obsidian/current/.config"),
        Some(detect::ObsidianKind::Flatpak) => home.join(".var/app/md.obsidian.Obsidian/config"),
        _ => std::env::var_os("XDG_CONFIG_HOME").map(PathBuf::from).filter(|p| p.is_absolute()).unwrap_or_else(|| home.join(".config")),
    };
    config.join("obsidian").join("obsidian.json")
}

#[cfg(windows)]
fn obsidian_config(home: &Path) -> PathBuf {
    let roaming = std::env::var_os("APPDATA").map_or_else(|| home.join(r"AppData\Roaming"), PathBuf::from);
    roaming.join("obsidian").join("obsidian.json")
}

#[cfg(target_os = "macos")]
fn obsidian_running() -> bool {
    sys::run(Path::new("/usr/bin/pgrep"), &["-x", "Obsidian"], Duration::from_secs(5)).ok()
}

#[cfg(target_os = "linux")]
fn obsidian_running() -> bool {
    sys::find_tool("pgrep").is_some_and(|pgrep| sys::run(&pgrep, &["-x", "obsidian"], Duration::from_secs(5)).ok())
}

#[cfg(windows)]
fn obsidian_running() -> bool {
    let out = sys::run(&sys::system32("tasklist.exe"), &["/FI", "IMAGENAME eq Obsidian.exe", "/NH"], Duration::from_secs(10));
    out.stdout.to_ascii_lowercase().contains("obsidian.exe")
}

/// By the bundle's path, not its name nor the obsidian:// scheme: a copy made a
/// minute ago may not be known to macOS (Launch Services) yet.
#[cfg(target_os = "macos")]
fn open_obsidian(app: &Path, uri: Option<&str>) -> Result<(), String> {
    let app = app.to_string_lossy();
    let mut args = vec!["-a", app.as_ref()];
    args.extend(uri);
    if sys::run(Path::new("/usr/bin/open"), &args, Duration::from_secs(15)).ok() {
        Ok(())
    } else {
        Err("Je n'ai pas pu ouvrir Obsidian.".into())
    }
}

/// Obsidian.exe itself, the link as its argument: what its obsidian:// handler runs.
#[cfg(windows)]
fn open_obsidian(app: &Path, uri: Option<&str>) -> Result<(), String> {
    let args: Vec<&str> = uri.into_iter().collect();
    sys::spawn_detached(app, &args).map_err(|_| "Je n'ai pas pu ouvrir Obsidian.".to_string())
}

/// The program as its desktop entry starts it, the link as its argument (Electron hands it to
/// a running Obsidian).
#[cfg(target_os = "linux")]
fn open_obsidian(app: &detect::Obsidian, uri: Option<&str>) -> Result<(), String> {
    let args: Vec<&str> = app.args.iter().map(String::as_str).chain(uri).collect();
    sys::spawn_detached(&app.program, &args).map_err(|_| "Je n'ai pas pu ouvrir Obsidian.".to_string())
}

#[derive(Debug, PartialEq)]
enum VaultList {
    AlreadyThere,
    Updated(String),
    /// Not the JSON we expect: left untouched, we never rewrite what we do not understand.
    Unreadable,
}

/// Adds `vault` to the content of Obsidian's obsidian.json, keeping everything else.
fn add_vault(existing: Option<&str>, vault: &str, id: &str, ts: u64) -> VaultList {
    let parsed = match existing {
        Some(s) if !s.trim().is_empty() => serde_json::from_str(s),
        _ => Ok(json!({})),
    };
    let Ok(mut root) = parsed else { return VaultList::Unreadable };
    let Some(vaults) = root.as_object_mut().and_then(|o| o.entry("vaults").or_insert_with(|| json!({})).as_object_mut()) else {
        return VaultList::Unreadable;
    };
    if vaults.values().any(|v| v.get("path").and_then(Value::as_str).is_some_and(|p| same_folder(p, vault))) {
        return VaultList::AlreadyThere;
    }
    if vaults.contains_key(id) {
        return VaultList::Unreadable;
    }
    vaults.insert(id.to_string(), json!({ "path": vault, "ts": ts, "open": true }));
    match serde_json::to_string(&root) {
        Ok(s) => VaultList::Updated(s),
        Err(_) => VaultList::Unreadable,
    }
}

#[cfg(unix)]
fn same_folder(a: &str, b: &str) -> bool {
    a.trim_end_matches('/') == b.trim_end_matches('/')
}

/// Windows paths ignore case and take either slash.
#[cfg(windows)]
fn same_folder(a: &str, b: &str) -> bool {
    let norm = |p: &str| p.replace('/', "\\").trim_end_matches('\\').to_lowercase();
    norm(a) == norm(b)
}

/// Everything but unreserved characters, '/' included, as Obsidian's URI scheme expects.
fn percent_encode(s: &str) -> String {
    s.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => (b as char).to_string(),
            _ => format!("%{b:02X}"),
        })
        .collect()
}

pub fn open_link(url: &str) -> Result<(), String> {
    if !link_allowed(url) {
        return Err("Ce lien n'est pas autorisé.".into());
    }
    if sys::open_with_system(url) {
        Ok(())
    } else {
        Err("Je n'ai pas pu ouvrir ton navigateur.".into())
    }
}

/// https only, on a few known hosts, written plainly (no port, no user@, no spaces, no
/// comma: Explorer, which opens it on Windows, reads one as the end of an argument).
fn link_allowed(url: &str) -> bool {
    let Some(rest) = url.strip_prefix("https://") else { return false };
    let host = &rest[..rest.find(['/', '?', '#']).unwrap_or(rest.len())];
    LINK_HOSTS.contains(&host) && url.len() <= 2048 && url.bytes().all(|b| b.is_ascii_graphic() && b != b'\\' && b != b',')
}

// ── Plumbing ────────────────────────────────────────────────────────────────

/// Downloads `url` to `dest` and logs the progress every few seconds. `size` is the
/// expected byte count, when known.
fn download(
    url: &str,
    dest: &Path,
    what: &str,
    size: Option<u64>,
    timeout: Duration,
    log: &mut dyn FnMut(String),
) -> Result<(), String> {
    download_via(url, dest, what, size, timeout, system_proxy(url).as_deref(), log)
}

/// `download`, through `proxy` when the system sends the site through one.
fn download_via(
    url: &str,
    dest: &Path,
    what: &str,
    size: Option<u64>,
    timeout: Duration,
    proxy: Option<&str>,
    log: &mut dyn FnMut(String),
) -> Result<(), String> {
    log(format!("Je télécharge {what}…"));
    let mut args = curl_download_args(url, dest);
    if let Some(p) = proxy {
        // Before the address, the last word.
        let at = args.len() - 1;
        args.splice(at..at, curl_proxy_args(p));
    }
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let args: Vec<&str> = args.iter().map(String::as_str).collect();
        let _ = tx.send(sys::run(&sys::curl(), &args, timeout));
    });
    let out = loop {
        match rx.recv_timeout(Duration::from_secs(5)) {
            Ok(out) => break out,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                let got = fs::metadata(dest).map(|m| m.len()).unwrap_or(0);
                log(progress(got, size));
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                return Err(format!("Le téléchargement de {what} s'est interrompu. Clique sur « Réessayer »."))
            }
        }
    };
    if !out.ok() {
        log(out.stderr.trim().to_string());
        // Through a company's proxy, nothing of the user's Wi-Fi is the matter: what to ask its IT service.
        if let Some(m) = proxy.and_then(|_| repair::stop_message("proxy_entreprise")) {
            return Err(m);
        }
        return Err(format!("Je n'ai pas pu télécharger {what}. Vérifie ta connexion internet, puis clique sur « Réessayer »."));
    }
    Ok(())
}

/// The proxy Windows sends `url` through (its settings, a PAC script, WPAD), as its own web requests find
/// it: curl.exe never reads them, and without it a PC behind a company's proxy has no internet for curl.
/// None when the site is reached directly, or PowerShell does not say in time.
#[cfg(windows)]
pub(crate) fn system_proxy(url: &str) -> Option<String> {
    let out = ask_system_proxy(url);
    out.ok().then(|| proxy_url(&out.stdout)).flatten()
}

#[cfg(windows)]
fn ask_system_proxy(url: &str) -> sys::Output {
    const ASK: &str = "$u = [Uri]$env:BIAB_URL; $p = [Net.WebRequest]::GetSystemWebProxy().GetProxy($u); if ($p -ne $u) { $p.AbsoluteUri }";
    sys::run_with(&sys::powershell(), &["-NoProfile", "-NonInteractive", "-Command", ASK], &[("BIAB_URL", url)], None, Duration::from_secs(10))
}

#[cfg(not(windows))]
pub(crate) fn system_proxy(_url: &str) -> Option<String> {
    None
}

/// https only, redirects included; a stalled connection gives up instead of waiting
/// for the timeout. `-q` comes first so ~/.curlrc cannot turn off certificate checks
/// for scripts we then run.
fn curl_download_args(url: &str, dest: &Path) -> Vec<String> {
    let mut args: Vec<String> = [
        "-q", "-fsSL", "--proto", "=https", "--proto-redir", "=https", "--retry", "3",
        "--connect-timeout", "20", "--speed-limit", "1024", "--speed-time", "60", "-o",
    ]
    .map(String::from)
    .to_vec();
    args.extend([dest.to_string_lossy().into_owned(), url.to_string()]);
    args
}

/// Through the proxy, offering it the user's own Windows login when it asks (NTLM or Kerberos: -U : is
/// Windows' own credentials for curl.exe), whichever it takes. A proxy that looks inside HTTPS signs with
/// the company's certificate, often without a revocation list: the check is then best effort (Schannel's
/// 0x80092012 otherwise), a revoked certificate still refused.
pub(crate) fn curl_proxy_args(proxy: &str) -> Vec<String> {
    ["--proxy", proxy, "--proxy-anyauth", "--proxy-user", ":", "--ssl-revoke-best-effort"].map(String::from).to_vec()
}

/// A proxy's address as PowerShell prints it: one plain http:// address, or nothing.
#[cfg(any(windows, test))]
fn proxy_url(said: &str) -> Option<String> {
    let p = said.trim();
    let plain = p.len() <= 256 && p.bytes().all(|b| b.is_ascii_graphic() && !b"\"'`\\".contains(&b));
    (plain && p.starts_with("http://") && p.len() > "http://".len()).then(|| p.to_string())
}

/// Runs Claude Code's installer (install.ps1, next to it) once Windows PowerShell's web requests offer the
/// user's Windows login to a proxy that asks for it: they never do by themselves (407). Its end is the
/// installer's: its code, or a throw.
#[cfg(any(windows, test))]
const INSTALLER_WRAPPER: &str = "try { [Net.WebRequest]::DefaultWebProxy.Credentials = [Net.CredentialCache]::DefaultNetworkCredentials } catch {}\r\n\
                                 & (Join-Path $PSScriptRoot 'install.ps1')\r\nexit $LASTEXITCODE\r\n";

fn progress(got: u64, size: Option<u64>) -> String {
    let mb = got / 1_000_000;
    match size {
        Some(total) if total > 0 => {
            format!("Téléchargement : {} % ({mb} Mo sur {})", (got * 100 / total).min(100), total / 1_000_000)
        }
        _ => format!("Téléchargement : {mb} Mo"),
    }
}

/// Downloads an official installer script and runs it with bash (PowerShell on Windows), watching
/// `watched`, where it downloads and installs, for its progress (see PACE).
fn run_installer(url: &str, name: &str, args: &[&str], watched: &[PathBuf], log: &mut dyn FnMut(String)) -> Result<(), String> {
    let root = sys::work_root().map_err(|e| format!("Je n'ai pas pu créer mon dossier de travail ({e})."))?;
    let tmp = TempDir::new_in(&root)?;
    let script = tmp.path().join(if cfg!(windows) { "install.ps1" } else { "install.sh" });
    download(url, &script, &format!("l'installeur de {name}"), None, Duration::from_secs(5 * 60), log)?;
    log(format!("J'installe {name}…"));
    #[cfg(not(windows))]
    let (program, argv) = {
        let argv: Vec<String> = std::iter::once(script.to_string_lossy().into_owned()).chain(args.iter().map(|a| a.to_string())).collect();
        (PathBuf::from("/bin/bash"), argv)
    };
    // Its arguments are never needed on Windows (Bun comes with install.ps1).
    #[cfg(windows)]
    let (program, argv) = {
        debug_assert!(args.is_empty());
        let wrapper = tmp.path().join("lance.ps1");
        fs::write(&wrapper, INSTALLER_WRAPPER).map_err(|e| format!("Je n'ai pas pu préparer l'installeur de {name} ({e})."))?;
        (sys::powershell(), ps1_args(&wrapper))
    };
    let argv: Vec<&str> = argv.iter().map(String::as_str).collect();
    let mut stop = Stop::default();
    let ended = run_watched(&program, &argv, &[], tmp.path(), watched, &PACE, &mut |line| {
        stop.see(&line);
        log(line);
    });
    installer_ended(name, ended, stop.cause())
}

/// The sentence for an installer's end: a download that no longer moves or never ends says so, with what
/// to do on a slow line; a PowerShell or a proxy a company locks says what to ask its IT service.
fn installer_ended(name: &str, ended: Ended, cause: Option<&str>) -> Result<(), String> {
    match ended {
        Ended::Code(0) => Ok(()),
        Ended::Stalled => Err(format!(
            "Le téléchargement de {name} n'avance plus depuis {} minutes : ta connexion est coupée ou trop lente. \
             Si tu es en données mobiles, attends d'avoir un Wi-Fi, puis clique sur « Réessayer ».",
            PACE.stall.as_secs() / 60
        )),
        Ended::TooSlow => Err(format!(
            "Ta connexion est très lente : {name} ne s'est pas installé en une heure. Attends d'avoir un Wi-Fi, \
             puis clique sur « Réessayer »."
        )),
        Ended::Code(_) => Err(cause
            .filter(|c| ["powershell_verrouille", "proxy_entreprise"].contains(c))
            .and_then(repair::stop_message)
            .unwrap_or_else(|| {
                format!("L'installeur de {name} s'est arrêté avant la fin. Vérifie ta connexion internet, puis clique sur « Réessayer ».")
            })),
    }
}

/// How long an installer may run, and stay still.
struct Pace {
    /// Nothing new for this long (no line, no byte): stopped.
    stall: Duration,
    /// Stopped past this, whatever it does.
    limit: Duration,
    /// How often its folders are looked at.
    tick: Duration,
}

#[derive(Debug, PartialEq)]
enum Ended {
    Code(i32),
    Stalled,
    TooSlow,
}

/// Runs an installer as `sys::run_streaming` does, stopped (with all it started) once neither its lines
/// nor the bytes in `watched` moved for `pace.stall`, or past `pace.limit`.
fn run_watched(
    program: &Path,
    args: &[&str],
    env: &[(&str, Option<&str>)],
    cwd: &Path,
    watched: &[PathBuf],
    pace: &Pace,
    log: &mut dyn FnMut(String),
) -> Ended {
    let (cancel, done, stalled) = (AtomicBool::new(false), AtomicBool::new(false), AtomicBool::new(false));
    let lines = AtomicU64::new(0);
    let start = Instant::now();
    let code = thread::scope(|s| {
        s.spawn(|| {
            let mut seen = (bytes_in(watched), lines.load(Ordering::SeqCst));
            let (mut moved, mut next) = (Instant::now(), Instant::now() + pace.tick);
            while !done.load(Ordering::SeqCst) {
                thread::sleep(pace.tick.min(Duration::from_millis(100)));
                if Instant::now() < next {
                    continue;
                }
                next += pace.tick;
                let now = (bytes_in(watched), lines.load(Ordering::SeqCst));
                if now != seen {
                    (seen, moved) = (now, Instant::now());
                } else if moved.elapsed() >= pace.stall {
                    stalled.store(true, Ordering::SeqCst);
                    cancel.store(true, Ordering::SeqCst);
                    return;
                }
            }
        });
        let code = sys::run_streaming_until(program, args, env, Some(cwd), pace.limit, &cancel, &mut |line| {
            lines.fetch_add(1, Ordering::SeqCst);
            log(line);
        });
        done.store(true, Ordering::SeqCst);
        code
    });
    if stalled.load(Ordering::SeqCst) {
        Ended::Stalled
    } else if code == -1 && start.elapsed() >= pace.limit {
        Ended::TooSlow
    } else {
        Ended::Code(code)
    }
}

/// The bytes the files under `dirs` hold, their subfolders' included (links not followed), up to a few
/// thousand entries. Each file's own size: Windows' folder listing keeps an old one while a download writes.
fn bytes_in(dirs: &[PathBuf]) -> u64 {
    let mut todo = dirs.to_vec();
    let (mut total, mut seen) = (0u64, 0usize);
    while let Some(dir) = todo.pop() {
        let Ok(entries) = fs::read_dir(&dir) else { continue };
        for e in entries.filter_map(Result::ok) {
            seen += 1;
            if seen > 10_000 {
                return total;
            }
            match fs::symlink_metadata(e.path()) {
                Ok(m) if m.is_dir() => todo.push(e.path()),
                Ok(m) => total += m.len(),
                Err(_) => {}
            }
        }
    }
    total
}

/// The computer kept awake while a step runs: a laptop on battery falls asleep in the middle of a long
/// install, which stops it. The screen may go dark, the system does not sleep. Let go when dropped, and
/// when the app quits: caffeinate waits on the app (macOS), the inhibitor ends with the pipe the app holds
/// (Linux, systemd-inhibit), the state belongs to the step's thread (Windows).
struct KeepAwake {
    #[cfg(unix)]
    held: Option<std::process::Child>,
}

#[cfg(target_os = "macos")]
impl KeepAwake {
    fn start() -> Self {
        let app = std::process::id().to_string();
        KeepAwake { held: sys::spawn_held(Path::new("/usr/bin/caffeinate"), &["-i", "-w", &app], Stdio::null()).ok() }
    }
}

#[cfg(target_os = "linux")]
impl KeepAwake {
    fn start() -> Self {
        let held = sys::find_tool("systemd-inhibit").zip(sys::find_tool("cat")).and_then(|(inhibit, cat)| {
            let cat = cat.to_string_lossy();
            let args = ["--what=sleep:idle", "--who=Brain in a Box", "--why=Installation en cours", "--mode=block", cat.as_ref()];
            sys::spawn_held(&inhibit, &args, Stdio::piped()).ok()
        });
        KeepAwake { held }
    }
}

#[cfg(unix)]
impl Drop for KeepAwake {
    fn drop(&mut self) {
        if let Some(mut held) = self.held.take() {
            drop(held.stdin.take());
            let _ = held.kill();
            let _ = held.wait();
        }
    }
}

#[cfg(windows)]
const ES_CONTINUOUS: u32 = 0x8000_0000;
#[cfg(windows)]
const ES_SYSTEM_REQUIRED: u32 = 0x0000_0001;

#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn SetThreadExecutionState(flags: u32) -> u32;
}

#[cfg(windows)]
impl KeepAwake {
    fn start() -> Self {
        // SAFETY: plain flags, for the calling thread; the previous state is not needed.
        unsafe { SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) };
        KeepAwake {}
    }
}

#[cfg(windows)]
impl Drop for KeepAwake {
    fn drop(&mut self) {
        // SAFETY: as above; the thread may sleep again.
        unsafe { SetThreadExecutionState(ES_CONTINUOUS) };
    }
}

/// A private folder (0700) removed with all its content when dropped.
pub(crate) struct TempDir(PathBuf);

impl TempDir {
    pub(crate) fn new_in(parent: &Path) -> Result<Self, String> {
        let fail = |e: io::Error| {
            format!("Je n'ai pas pu créer un dossier de travail ({e}). Vérifie qu'il reste de la place sur le disque.")
        };
        for _ in 0..8 {
            let p = parent.join(format!("{WORK_PREFIX}{}", random_hex()));
            match perms::create_dir(&p) {
                Ok(()) => return Ok(TempDir(p)),
                Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(e) => return Err(fail(e)),
            }
        }
        Err(fail(io::ErrorKind::AlreadyExists.into()))
    }

    pub(crate) fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// 16 hex digits, the shape of Obsidian's vault ids. Not a secret: RandomState's
/// OS-seeded keys are random enough and save a dependency.
fn random_hex() -> String {
    use std::hash::{BuildHasher, Hasher};
    let mut h = std::collections::hash_map::RandomState::new().build_hasher();
    h.write_u128(SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0));
    format!("{:016x}", h.finish())
}

/// Writes through a temporary file and a rename: readers see the old or the new
/// file, never half of one.
pub(crate) fn write_atomic(path: &Path, data: &[u8], mode: u32) -> io::Result<()> {
    let mut tmp = path.as_os_str().to_owned();
    tmp.push(".biab-tmp");
    let tmp = PathBuf::from(tmp);
    let _ = fs::remove_file(&tmp);
    let res = (|| {
        let mut f = perms::create_new(&tmp, mode)?;
        f.write_all(data)?;
        f.sync_all()?;
        drop(f);
        // The umask may have trimmed the mode at creation.
        perms::set_mode(&tmp, mode)?;
        // Windows refuses to replace a read-only file, where a Mac replaces it.
        #[cfg(windows)]
        if fs::metadata(path).is_ok_and(|m| m.permissions().readonly()) {
            perms::set_mode(path, 0o600)?;
        }
        fs::rename(&tmp, path)
    })();
    if res.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    res
}

#[cfg(test)]
mod tests {
    use super::*;
    #[cfg(unix)]
    use std::os::unix::fs::{MetadataExt, PermissionsExt};

    #[cfg(not(windows))]
    fn bash(script: &Path) -> sys::Output {
        sys::run(Path::new("/bin/bash"), &[&script.to_string_lossy()], Duration::from_secs(10))
    }

    #[cfg(unix)]
    fn write_exec(path: &Path, content: &str) {
        fs::write(path, content).unwrap();
        fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
    }

    #[test]
    fn only_known_https_links_open() {
        for ok in [
            "https://dashboard.voyageai.com/",
            "https://dashboard.voyageai.com/organization/api-keys",
            "https://docs.voyageai.com/docs/pricing",
            "https://claude.com/pricing",
            "https://claude.ai/download",
            "https://claude.ai",
            "https://obsidian.md/download?x=1#top",
            "https://chatgpt.com",
            "https://chatgpt.com/",
        ] {
            assert!(link_allowed(ok), "{ok} should open");
        }
        for bad in [
            "http://claude.ai/",
            "https://claude.ai.evil.com/",
            "https://chatgpt.com.evil.com/",
            "https://chat.openai.com/",
            "https://evil.com/claude.ai/",
            "https://claude.ai@evil.com/",
            "https://claude.ai:8443/",
            "https://CLAUDE.AI/",
            "https://www.obsidian.md/",
            "https://claude.ai/a b",
            "https://claude.ai\\@evil.com/",
            "https://claude.ai/\n",
            "https://claude.ai/a,/select,C:\\Windows",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "-a Calculator",
            "",
        ] {
            assert!(!link_allowed(bad), "{bad:?} should be refused");
        }
        assert!(!link_allowed(&format!("https://claude.ai/{}", "a".repeat(3000))));
        assert!(open_link("https://evil.com/").is_err());
    }

    #[test]
    fn vault_list_is_created_when_missing() {
        let VaultList::Updated(json) = add_vault(None, "/Users/jo/Documents/Brain", "0123456789abcdef", 42) else { panic!() };
        let v: Value = serde_json::from_str(&json).unwrap();
        assert_eq!(v, json!({"vaults": {"0123456789abcdef": {"path": "/Users/jo/Documents/Brain", "ts": 42, "open": true}}}));
        assert!(matches!(add_vault(Some("  \n"), "/b", "a", 1), VaultList::Updated(_)));
    }

    #[test]
    fn vault_list_keeps_what_is_there() {
        let before =
            r#"{"vaults":{"b85c76e85c189889":{"path":"/Users/jo/Notes","ts":1779198301077,"open":true}},"updateDisabled":true}"#;
        let VaultList::Updated(json) = add_vault(Some(before), "/Users/jo/Documents/Brain", "fedcba9876543210", 7) else {
            panic!()
        };
        let v: Value = serde_json::from_str(&json).unwrap();
        assert_eq!(v["updateDisabled"], json!(true));
        assert_eq!(v["vaults"]["b85c76e85c189889"], json!({"path": "/Users/jo/Notes", "ts": 1779198301077u64, "open": true}));
        assert_eq!(v["vaults"]["fedcba9876543210"]["path"], json!("/Users/jo/Documents/Brain"));
    }

    #[test]
    fn vault_already_listed_is_left_alone() {
        let before = r#"{"vaults":{"664d155756b01bd0":{"path":"/Users/jo/Documents/Brain/","ts":1}}}"#;
        assert_eq!(add_vault(Some(before), "/Users/jo/Documents/Brain", "x", 2), VaultList::AlreadyThere);
    }

    #[test]
    fn unexpected_vault_list_is_never_rewritten() {
        for bad in [r#"{"vaults":{"#, "not json", "[]", r#"{"vaults":[]}"#, r#"{"vaults":"x"}"#] {
            assert_eq!(add_vault(Some(bad), "/b", "id", 1), VaultList::Unreadable, "{bad}");
        }
        // An id collision would overwrite someone else's vault.
        assert_eq!(add_vault(Some(r#"{"vaults":{"id":{"path":"/other"}}}"#), "/b", "id", 1), VaultList::Unreadable);
    }

    /// Apple's installer still writes the tools when git is already there (a GitHub VM showed
    /// install.sh started 20 s before Apple's window said it was over).
    #[cfg(target_os = "macos")]
    #[test]
    fn apple_tools_are_done_when_apples_window_is() {
        assert!(clt_settled(false, Duration::ZERO));
        assert!(!clt_settled(true, Duration::from_secs(30)));
        assert!(clt_settled(true, CLT_SETTLE));
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn team_id_comes_from_codesign_output() {
        let dv = "Executable=/Applications/Obsidian.app/Contents/MacOS/Obsidian\nIdentifier=md.obsidian\n\
                  Signature size=8976\nTeamIdentifier=6JSW4SJWN9\nRuntime Version=14.0.0\n";
        assert_eq!(team_id(dv), Some(OBSIDIAN_TEAM));
        assert_eq!(team_id("TeamIdentifier=not set\n"), Some("not set"));
        assert_eq!(team_id("code object is not signed at all\n"), None);
        assert_ne!(team_id("TeamIdentifier=ABCDE12345\n"), Some(OBSIDIAN_TEAM));
    }

    #[cfg(not(windows))]
    #[test]
    fn sh_quote_survives_bash() {
        let dir = tempfile::tempdir().unwrap();
        for s in ["plain", "with space", "Jo's \"brain\"", "$HOME `id` $(id) \\ ! * ;|&", "''", "line\nbreak", ""] {
            let script = dir.path().join("q.sh");
            fs::write(&script, format!("printf %s {}", sh_quote(s))).unwrap();
            assert_eq!(bash(&script).stdout, s);
        }
    }

    #[cfg(not(windows))]
    #[test]
    fn terminal_scripts_quote_odd_paths() {
        let dir = tempfile::tempdir().unwrap();
        let root = fs::canonicalize(dir.path()).unwrap();
        let home = root.join("Jo's home $x");
        let vault = home.join("Documents/Brain");
        fs::create_dir_all(&vault).unwrap();
        let bin = root.join("b'in dir");
        fs::create_dir_all(&bin).unwrap();
        let out = root.join("out");
        write_exec(
            &bin.join("claude"),
            &format!("#!/bin/sh\n{{ pwd -P; printf '%s\\n' \"$@\"; }} > {}\n", sh_quote(&out.to_string_lossy())),
        );

        let script = root.join("onboarding.command");
        let guide = onboarding_guide(&home).unwrap();
        assert!(fs::read_to_string(&guide).unwrap().contains("ONBOARDING"), "built-in guide written");
        let path = bin.clone().into_os_string();
        fs::write(&script, onboarding_script(&path, &vault, &guide, &bin.join("claude"), &ONBOARDING_MODE)).unwrap();
        let o = bash(&script);
        assert!(o.ok(), "{}", o.stderr);
        assert!(o.stdout.contains("Tu peux fermer cette fenêtre."));
        // In the brain, accepting edits there, told in French where the brain is.
        assert_eq!(fs::read_to_string(&out).unwrap(), format!("{}\n--permission-mode\nacceptEdits\n{}\n", vault.display(), onboarding_prompt(&guide, &vault, true)));

        let script = root.join("claude-login.command");
        fs::write(&script, login_script(&path, &bin.join("claude"))).unwrap();
        assert!(bash(&script).ok());
        let seen = fs::read_to_string(&out).unwrap();
        assert_eq!(seen.lines().skip(1).collect::<Vec<_>>(), ["auth", "login"]);

        let script = root.join("claude-update.command");
        fs::write(&script, claude_script(&path, &bin.join("claude"), "update")).unwrap();
        assert!(bash(&script).ok());
        let seen = fs::read_to_string(&out).unwrap();
        assert_eq!(seen.lines().skip(1).collect::<Vec<_>>(), ["update"]);
    }

    /// Codex's login and onboarding windows: its own commands, in the brain for the onboarding, its
    /// folder only when not its default, and no key from the user's shell ever reaches it.
    #[cfg(not(windows))]
    #[test]
    fn codex_windows_run_its_own_commands_without_a_key() {
        let dir = tempfile::tempdir().unwrap();
        let root = fs::canonicalize(dir.path()).unwrap();
        let home = root.join("Jo's home $x");
        let vault = home.join("Documents/Brain");
        fs::create_dir_all(&vault).unwrap();
        let bin = root.join("b'in dir");
        fs::create_dir_all(&bin).unwrap();
        let out = root.join("out");
        let said = sh_quote(&out.to_string_lossy());
        write_exec(
            &bin.join("codex"),
            &format!(
                "#!/bin/sh\n{{ pwd -P; printf '%s\\n' \"$@\"; echo \"home=${{CODEX_HOME-unset}}\"; /usr/bin/env | /usr/bin/grep -E '^(OPENAI|CODEX_A)' || true; }} > {said}\n"
            ),
        );
        let codex = bin.join("codex");
        let path = bin.clone().into_os_string();
        let keys = [("OPENAI_API_KEY", "sk-x"), ("CODEX_API_KEY", "sk-y"), ("CODEX_ACCESS_TOKEN", "t"), ("OPENAI_BASE_URL", "http://x")];
        let run = |name: &str, text: String| {
            let script = root.join(name);
            fs::write(&script, text).unwrap();
            let o = sys::run_with(Path::new("/bin/bash"), &[&script.to_string_lossy()], &keys, None, Duration::from_secs(10));
            assert!(o.ok(), "{}", o.stderr);
            assert!(o.stdout.contains("Tu peux fermer cette fenêtre."));
            fs::read_to_string(&out).unwrap()
        };

        // The fake codex sees the window's PATH only (the folder it is in): env and grep by their paths.
        let seen = run("codex-login.command", codex_login_script(&path, &codex, None));
        let lines: Vec<&str> = seen.lines().skip(1).collect();
        assert_eq!(lines, ["-c", r#"cli_auth_credentials_store="file""#, "login", "home=unset"]);

        let other = home.join("Données é").join("codex'");
        let seen = run("codex-login.command", codex_login_script(&path, &codex, Some(&other)));
        assert_eq!(seen.lines().last(), Some(format!("home={}", other.display()).as_str()));

        let guide = onboarding_guide(&home).unwrap();
        let seen = run("onboarding.command", codex_onboarding_script(&path, &vault, &guide, &codex, None));
        assert_eq!(seen, format!("{}\n-c\ncli_auth_credentials_store=\"file\"\n{}\nhome=unset\n", vault.display(), onboarding_prompt(&guide, &vault, false)));

        // A CODEX_HOME the user's shell profile exports (Terminal runs it): the folder agents.json
        // records wins, ~/.codex being Codex's own default.
        let shell = [("CODEX_HOME", "/elsewhere")];
        for (name, text) in [
            ("codex-login.command", codex_login_script(&path, &codex, None)),
            ("onboarding.command", codex_onboarding_script(&path, &vault, &guide, &codex, None)),
        ] {
            let script = root.join(name);
            fs::write(&script, text).unwrap();
            let o = sys::run_with(Path::new("/bin/bash"), &[&script.to_string_lossy()], &shell, None, Duration::from_secs(10));
            assert!(o.ok(), "{}", o.stderr);
            assert_eq!(fs::read_to_string(&out).unwrap().lines().last(), Some("home=unset"), "{name}");
        }

        assert_eq!(codex_home_var(&home, &home.join(".codex")), None);
        assert_eq!(codex_home_var(&home, &other), Some(other.as_path()));
    }

    /// The onboarding opens Codex on the ChatGPT login the kit checked, never on an API key (it would
    /// be billed), a workspace account or no login at all: refused before any window, saying why.
    #[test]
    fn codex_onboarding_needs_the_chatgpt_login() {
        let tmp = tempfile::tempdir().unwrap();
        let root = fs::canonicalize(tmp.path()).unwrap();
        let home = root.join("home");
        let work = root.join("work");
        fs::create_dir_all(&work).unwrap();
        crate::repair::tests::plant_codex(&home, &serde_json::json!({}), &[]);
        let setup = crate::repair::tests::codex_setup(&home, &work);
        let codex = Path::new("codex");
        let auth = home.join(".codex").join("auth.json");
        let chatgpt = fs::read_to_string(&auth).unwrap();
        for (label, file, said) in [
            ("an API key", Some(r#"{"auth_mode": "apikey", "OPENAI_API_KEY": "sk-proj-not-a-real-key"}"#), "clé API"),
            ("a workspace account", Some(r#"{"auth_mode": "chatgpt", "tokens": {"refresh_token": "r"}}"#), "compte ChatGPT"),
            ("not logged in", None, "compte ChatGPT"),
        ] {
            match file {
                Some(f) => fs::write(&auth, f).unwrap(),
                None => fs::remove_file(&auth).unwrap(),
            }
            let err = codex_onboarding(&home, &setup, codex).expect_err(label);
            assert!(err.contains(said), "{label}: {err}");
        }
        fs::write(&auth, chatgpt).unwrap();
        // Logged in with ChatGPT: the brain is what is missing now (Windows may know another brain
        // folder, install.ps1's), then the window's script.
        match codex_onboarding(&home, &setup, codex) {
            Err(e) => assert_eq!(e, "Ton cerveau n'est pas encore installé."),
            Ok(script) => assert!(cfg!(windows) && script.contains("pour me personnaliser"), "{script}"),
        }
        #[cfg(not(windows))]
        {
            fs::create_dir_all(home.join("Documents/Brain")).unwrap();
            let script = codex_onboarding(&home, &setup, codex).unwrap();
            assert!(script.contains("pour me personnaliser"), "{script}");
        }
        assert_eq!(onboarding_refused(codex::Login::Chatgpt), None);
        for login in [codex::Login::Billed, codex::Login::Managed, codex::Login::System, codex::Login::LoggedOut, codex::Login::Unknown] {
            assert!(onboarding_refused(login).is_some(), "{login:?}");
        }
    }

    #[test]
    fn atomic_write_sets_the_mode() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("x.command");
        fs::write(&p, "old").unwrap();
        perms::set_mode(&p, 0o644).unwrap();
        write_atomic(&p, b"new", 0o700).unwrap();
        assert_eq!(fs::read_to_string(&p).unwrap(), "new");
        #[cfg(unix)]
        assert_eq!(fs::metadata(&p).unwrap().permissions().mode() & 0o777, 0o700);
        assert_eq!(fs::read_dir(dir.path()).unwrap().count(), 1);
        // Read-only, where Windows has nothing else to say.
        write_atomic(&p, b"newer", 0o400).unwrap();
        assert_eq!(perms::mode(&fs::metadata(&p).unwrap()), 0o400);
        perms::set_mode(&p, 0o600).unwrap();
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn disk_image_is_detached_even_on_failure() {
        let dir = tempfile::tempdir().unwrap();
        let vol = dir.path().join("vol");
        fs::create_dir_all(vol.join("Obsidian.app/Contents")).unwrap();
        let dmg = dir.path().join("t.dmg");
        let made = sys::run(
            Path::new("/usr/bin/hdiutil"),
            &["create", "-quiet", "-srcfolder", &vol.to_string_lossy(), "-volname", "T", &dmg.to_string_lossy()],
            Duration::from_secs(60),
        );
        assert!(made.ok(), "{}", made.stderr);
        let at = dir.path().join("mnt");
        let mounted = || sys::run(Path::new("/sbin/mount"), &[], Duration::from_secs(5)).stdout.contains(&*at.to_string_lossy());
        let attempt = || -> Result<(), String> {
            let m = Mount::attach(&dmg, &at)?;
            assert!(Path::new(&m.app("Obsidian.app")).is_dir() && mounted());
            Err("copy failed".into())
        };
        assert!(attempt().is_err());
        assert!(!mounted());
        assert!(Mount::attach(&dir.path().join("missing.dmg"), &dir.path().join("mnt2")).is_err());
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn apps_signed_by_someone_else_are_refused() {
        assert!(signed_by_obsidian(Path::new("/System/Applications/Calculator.app")).is_err());
        assert!(signed_by_obsidian(Path::new("/nonexistent/Obsidian.app")).is_err());
    }

    #[test]
    fn missing_brain_parts_are_named() {
        use detect::{Brain, ClaudePart, CodexPart};
        let claude = ClaudePart { hooks: true, skills: true };
        let all = Brain { vault: true, memory: true, claude: Some(claude), codex: None, chosen: true };
        let home = Path::new("/Users/jo");
        assert!(missing_parts(all, home).is_empty());
        assert_eq!(missing_parts(Brain { claude: Some(ClaudePart { hooks: false, ..claude }), ..all }, home), ["les automatismes de Claude"]);
        let nothing = Brain { vault: false, memory: false, claude: Some(ClaudePart { hooks: false, skills: false }), ..all };
        assert_eq!(
            missing_parts(nothing, home),
            [
                format!("le dossier {}", detect::brain_dir_shown(home)).as_str(),
                "les automatismes de Claude",
                "la mémoire de recherche",
                "les compétences de Claude"
            ],
            "Claude alone: the parts and their order as before Codex"
        );
        let codex = CodexPart { hooks: true, instructions: false, skills: false };
        assert_eq!(missing_parts(Brain { claude: None, codex: Some(codex), ..all }, home), ["les consignes de Codex", "les compétences de Codex"]);
        assert_eq!(missing_parts(Brain { claude: None, chosen: false, ..all }, home), ["ton choix d'IA"]);
    }

    /// The agents' steps stand where Claude Code's does, the preferred one first; Claude alone runs the
    /// steps of before Codex.
    #[test]
    fn each_agent_listed_has_its_step() {
        use crate::agents::Engine;
        let with = |order: Vec<Engine>| Agents { codex_home: order.contains(&Engine::Codex).then(|| PathBuf::from("/c")), order, etat: "ok".into() };
        assert_eq!(order(&Agents::claude_alone()), ORDER);
        let expected =
            |agents: &[&'static str]| -> Vec<&'static str> { ORDER.iter().flat_map(|&s| if s == "claude" { agents.to_vec() } else { vec![s] }).collect() };
        assert_eq!(order(&with(vec![Engine::Codex])), expected(&["codex"]));
        assert_eq!(order(&with(vec![Engine::Codex, Engine::Claude])), expected(&["codex", "claude"]));
        assert_eq!(order(&with(vec![Engine::Claude, Engine::Codex])), expected(&["claude", "codex"]));
        assert_eq!(order(&Agents::none("ferme")), expected(&[]));
        #[cfg(target_os = "macos")]
        assert_eq!(order(&with(vec![Engine::Codex, Engine::Claude])), ["codex", "claude", "clt", "bun", "obsidian", "brain"]);
        #[cfg(target_os = "linux")]
        assert_eq!(order(&with(vec![Engine::Codex])), ["paquets", "codex", "bun", "obsidian", "brain"]);
        #[cfg(windows)]
        assert_eq!(order(&with(vec![Engine::Claude, Engine::Codex])), ["claude", "codex", "brain"]);
    }

    /// The brain step stops on a choice the kit cannot trust, before it downloads anything; it says why.
    #[test]
    fn an_untrusted_choice_stops_the_brain_step_first() {
        assert_eq!(choice_trusted(&Agents::claude_alone()), Ok(()));
        for (etat, why) in [("ferme", "demande Codex"), ("illisible", "est illisible"), ("droits", "autre compte"), ("invalide", "pas un choix")] {
            let err = choice_trusted(&Agents::none(etat)).unwrap_err();
            assert!(err.contains(why) && err.contains("agents.json") && err.contains("Je n'installe rien"), "{err}");
        }
    }

    /// Bibo installs the Codex install.sh and install.ps1 install, through the same official installer,
    /// checked against the same SHA-256.
    #[test]
    fn codex_is_the_one_the_installers_pin() {
        let pin = |script: &str, prefix: &str| -> (String, String) {
            let value = |key: &str| {
                script
                    .lines()
                    .find_map(|l| l.trim().strip_prefix(&format!("{prefix}{key}")).map(|r| r.trim_start_matches([' ', '=']).trim_matches('"').to_string()))
                    .unwrap_or_else(|| panic!("{key}"))
            };
            let version = value("CODEX_VERSION");
            let url = value("CODEX_INSTALLER_URL").replace("$CODEX_VERSION", &version);
            (version, format!("{url} {}", value("CODEX_INSTALLER_SHA256")))
        };
        let sh = pin(include_str!("../../../install.sh"), "");
        let ps1 = pin(include_str!("../../../install.ps1"), "$");
        assert_eq!(sh.0, CODEX_VERSION);
        assert_eq!(ps1.0, CODEX_VERSION);
        let (url, sha) = CODEX_INSTALLER;
        assert_eq!(if cfg!(windows) { &ps1.1 } else { &sh.1 }, &format!("{url} {sha}"));
        assert_eq!(codex::MIN_CODEX, (0, 162, 1), "the version the launcher's contract was checked with");
    }

    /// The installer gets the version, no question, the CODEX_HOME chosen; none of its own settings
    /// from the app's environment, nor the Voyage key. One that is not the pinned one never runs.
    #[cfg(not(windows))]
    #[test]
    fn codex_installer_runs_only_as_pinned() {
        let dir = tempfile::tempdir().unwrap();
        let out = dir.path().join("env.txt");
        let script = dir.path().join("install.sh");
        let names = ["CODEX_RELEASE", "CODEX_NON_INTERACTIVE", "CODEX_HOME", "VOYAGE_API_KEY", "CODEX_INSTALL_DIR", "CODEX_UPDATE_FROM_RELEASE"];
        let to = sh_quote(&out.to_string_lossy());
        let body = names.map(|n| format!("printf '%s=%s\\n' {n} \"${{{n}-unset}}\" >> {to}\n")).concat();
        fs::write(&script, body).unwrap();
        let codex_home = dir.path().join("Données é").join(".codex");
        let env = codex_installer_env(&codex_home);
        let mut env: Vec<(&str, Option<&str>)> = env.iter().map(|(k, v)| (*k, v.as_deref())).collect();
        // What the app's own environment could hold: set here for the child, then removed by the list.
        let unset: Vec<&str> = env.iter().filter(|(_, v)| v.is_none()).map(|(k, _)| *k).collect();
        assert!(unset.contains(&"VOYAGE_API_KEY") && unset.contains(&"CODEX_INSTALL_DIR"), "{unset:?}");
        let mut lines = Vec::new();
        let sha = config_guard::sha256_file(&script).unwrap();
        assert_eq!(
            run_codex_installer(&script, &"0".repeat(64), &env, dir.path(), &mut |l| lines.push(l)).map_err(|e| e.contains("pas celui attendu")),
            Err(true)
        );
        assert!(!out.exists(), "a script that is not the pinned one never ran");
        env.insert(0, ("VOYAGE_API_KEY", Some("pa-not-for-codex")));
        env.insert(0, ("CODEX_INSTALL_DIR", Some("/elsewhere")));
        run_codex_installer(&script, &sha, &env, dir.path(), &mut |l| lines.push(l)).unwrap();
        let seen = fs::read_to_string(&out).unwrap();
        let want = format!(
            "CODEX_RELEASE=0.162.1\nCODEX_NON_INTERACTIVE=1\nCODEX_HOME={}\nVOYAGE_API_KEY=unset\nCODEX_INSTALL_DIR=unset\nCODEX_UPDATE_FROM_RELEASE=unset\n",
            codex_home.display()
        );
        assert_eq!(seen, want);
        fs::write(&script, "exit 3\n").unwrap();
        let sha = config_guard::sha256_file(&script).unwrap();
        assert!(run_codex_installer(&script, &sha, &env, dir.path(), &mut |_| {}).unwrap_err().contains("code 3"));
    }

    #[test]
    fn codex_already_there_is_kept_and_an_old_one_said() {
        assert_eq!(codex_there(&Found::Version("codex-cli 0.162.1".into()), "est déjà installé"), Some(Ok("Codex 0.162.1 est déjà installé.".into())));
        let old = codex_there(&Found::Version("codex-cli 0.150.0".into()), "est installé").unwrap().unwrap_err();
        assert!(old.contains("0.150.0") && old.contains("0.162.1") && old.contains("mets-le à jour"), "{old}");
        assert!(codex_there(&Found::Version("?".into()), "x").unwrap().is_err());
        assert_eq!(codex_there(&Found::NoVersion, "x"), None);
        assert_eq!(codex_there(&Found::Absent, "x"), None);
    }

    /// The official installer of the pinned Codex, for real, through the step's own code, into a
    /// throwaway home: `cargo test -- --ignored codex_real` (test.yml runs it on each system).
    #[test]
    #[ignore]
    fn codex_real_install_answers() {
        let dir = tempfile::tempdir().unwrap();
        // As agents.json records it: a plain path (Windows' \\?\ form is no folder to Join-Path).
        let home = dir.path().to_path_buf();
        let codex_home = home.join(".codex");
        let local = home.join("AppData").join("Local");
        fs::create_dir_all(&local).unwrap();
        let script = home.join(if cfg!(windows) { "install.ps1" } else { "install.sh" });
        download(CODEX_INSTALLER.0, &script, "l'installeur de Codex", None, Duration::from_secs(5 * 60), &mut |l| println!("{l}")).unwrap();
        let mut env = codex_installer_env(&codex_home);
        let home_text = home.to_string_lossy().into_owned();
        env.extend([("HOME", Some(home_text.clone())), ("USERPROFILE", Some(home_text)), ("LOCALAPPDATA", Some(local.to_string_lossy().into_owned()))]);
        let env: Vec<(&str, Option<&str>)> = env.iter().map(|(k, v)| (*k, v.as_deref())).collect();
        run_codex_installer(&script, CODEX_INSTALLER.1, &env, &home, &mut |l| println!("{l}")).unwrap();
        let local_text = local.to_string_lossy().into_owned();
        let setup = codex::Setup {
            home: home.clone(),
            codex_home,
            work_root: home.clone(),
            features: home.join("none.json"),
            layers: codex::Layers::default(),
            vars: Box::new(move |k| (k == "LOCALAPPDATA").then(|| local_text.clone())),
            command: None,
        };
        let found = detect::codex(&setup);
        assert_eq!(codex_there(&found, "est installé"), Some(Ok(format!("Codex {CODEX_VERSION} est installé."))), "{found:?}");
    }

    #[cfg(unix)]
    #[test]
    fn only_our_stale_copies_are_removed() {
        let dir = tempfile::tempdir().unwrap();
        let apps = dir.path().join("Applications");
        let ours = apps.join(".biab-0123456789abcdef");
        fs::create_dir_all(ours.join("Obsidian.app/Contents")).unwrap();
        let kept = [".biab-FEDCBA9876543210", ".biab-0123", ".biab-0123456789abcdef0", "biab-0123456789abcdef", ".biab-tmp", "Obsidian.app"];
        for name in kept {
            fs::create_dir_all(apps.join(name).join("x")).unwrap();
        }
        fs::write(apps.join(".biab-1111111111111111"), "a file").unwrap();
        let elsewhere = dir.path().join("elsewhere");
        fs::create_dir_all(&elsewhere).unwrap();
        fs::write(elsewhere.join("precious"), "x").unwrap();
        std::os::unix::fs::symlink(&elsewhere, apps.join(".biab-2222222222222222")).unwrap();
        let me = fs::metadata(dir.path()).unwrap().uid();

        remove_stale_copies(&apps, me.wrapping_add(1));
        assert!(ours.exists(), "someone else's folder stays");
        remove_stale_copies(&apps, me);
        assert!(!ours.exists());
        for name in kept {
            assert!(apps.join(name).join("x").exists(), "{name}");
        }
        assert!(apps.join(".biab-1111111111111111").is_file());
        assert!(apps.join(".biab-2222222222222222").exists() && elsewhere.join("precious").exists());
        remove_stale_copies(&dir.path().join("missing"), me);
    }

    #[test]
    fn archive_override_is_https_or_a_file() {
        assert!(matches!(archive_source(None), Ok(Archive::Remote(u)) if u == BRAIN_ZIP));
        assert!(matches!(archive_source(Some(" ".into())), Ok(Archive::Remote(u)) if u == BRAIN_ZIP));
        assert!(
            matches!(archive_source(Some("https://x.test/a.zip".into())), Ok(Archive::Remote(u)) if u == "https://x.test/a.zip")
        );
        assert!(matches!(archive_source(Some("/tmp/a.zip".into())), Ok(Archive::Local(p)) if p == Path::new("/tmp/a.zip")));
        assert!(archive_source(Some("http://x.test/a.zip".into())).is_err());
        assert!(archive_source(Some("file:///tmp/a.zip".into())).is_err());
    }

    /// A zip shaped like GitHub's: everything under "brain-in-a-box-master/".
    fn fake_archive(root: &Path, with_installer: bool) -> PathBuf {
        let parent = root.join(format!("repo-{with_installer}"));
        let repo = parent.join("brain-in-a-box-master");
        fs::create_dir_all(repo.join("vault-skeleton/Profil")).unwrap();
        if with_installer {
            fs::write(repo.join(INSTALLER), "echo hi\n").unwrap();
        }
        let zip = root.join(format!("biab-{with_installer}.zip"));
        #[cfg(target_os = "macos")]
        let o = sys::run(
            Path::new("/usr/bin/ditto"),
            &["-c", "-k", "--keepParent", &repo.to_string_lossy(), &zip.to_string_lossy()],
            Duration::from_secs(30),
        );
        // Linux: Python's zipfile, which every machine running these tests has.
        #[cfg(target_os = "linux")]
        let o = sys::run(
            Path::new("/usr/bin/python3"),
            &["-I", "-m", "zipfile", "-c", &zip.to_string_lossy(), &repo.to_string_lossy()],
            Duration::from_secs(30),
        );
        #[cfg(windows)]
        let o = sys::run(
            &sys::system32("tar.exe"),
            &["-a", "-c", "-f", &zip.to_string_lossy(), "-C", &parent.to_string_lossy(), "brain-in-a-box-master"],
            Duration::from_secs(30),
        );
        assert!(o.ok(), "{}", o.stderr);
        zip
    }

    #[test]
    fn sources_replace_the_previous_copy() {
        let dir = tempfile::tempdir().unwrap();
        let base = dir.path().join("base");
        fs::create_dir_all(base.join("src")).unwrap();
        fs::write(base.join("src/stale.txt"), "old").unwrap();

        let src = install_sources(&base, &Archive::Local(fake_archive(dir.path(), true)), &mut |_| {}).unwrap();
        assert_eq!(src, base.join("src"));
        assert!(src.join(INSTALLER).is_file() && src.join("vault-skeleton/Profil").is_dir());
        assert!(!src.join("stale.txt").exists());
        let left: Vec<_> = fs::read_dir(&base).unwrap().map(|e| e.unwrap().file_name()).collect();
        assert_eq!(left, vec!["src"], "the work folder must be gone");
    }

    #[test]
    fn a_bad_archive_keeps_the_previous_copy() {
        let dir = tempfile::tempdir().unwrap();
        let base = dir.path().join("base");
        fs::create_dir_all(base.join("src")).unwrap();
        fs::write(base.join("src").join(INSTALLER), "previous").unwrap();

        assert!(install_sources(&base, &Archive::Local(fake_archive(dir.path(), false)), &mut |_| {}).is_err());
        assert!(install_sources(&base, &Archive::Local(dir.path().join("nope.zip")), &mut |_| {}).is_err());
        assert_eq!(fs::read_to_string(base.join("src").join(INSTALLER)).unwrap(), "previous");
        assert_eq!(fs::read_dir(&base).unwrap().count(), 1);
    }

    #[test]
    fn source_root_needs_exactly_one_match() {
        let dir = tempfile::tempdir().unwrap();
        let d = dir.path();
        assert_eq!(source_root(d), None);
        for name in ["a", "b"] {
            fs::create_dir_all(d.join(name).join("vault-skeleton")).unwrap();
            fs::write(d.join(name).join(INSTALLER), "").unwrap();
        }
        assert_eq!(source_root(d), None, "two candidates: ambiguous");
        fs::remove_dir_all(d.join("b")).unwrap();
        assert_eq!(source_root(d), Some(d.join("a")));
    }

    #[test]
    fn temp_dir_is_private_and_removed() {
        let parent = tempfile::tempdir().unwrap();
        let path = {
            let t = TempDir::new_in(parent.path()).unwrap();
            fs::write(t.path().join("f"), "x").unwrap();
            #[cfg(unix)]
            assert_eq!(fs::metadata(t.path()).unwrap().permissions().mode() & 0o777, 0o700);
            t.path().to_path_buf()
        };
        assert!(!path.exists());
        assert_ne!(random_hex(), random_hex());
        assert_eq!(random_hex().len(), 16);
    }

    #[test]
    fn obsidian_uri_encodes_the_path() {
        assert_eq!(percent_encode("/Users/Jo Doe/Documents/Brain"), "%2FUsers%2FJo%20Doe%2FDocuments%2FBrain");
        assert_eq!(percent_encode("é&?#"), "%C3%A9%26%3F%23");
    }

    #[test]
    fn progress_reads_well() {
        assert_eq!(progress(117_000_000, Some(233_704_463)), "Téléchargement : 50 % (117 Mo sur 233)");
        assert_eq!(progress(3_000_000, None), "Téléchargement : 3 Mo");
    }

    #[test]
    fn downloads_ignore_curlrc_and_stay_on_https() {
        let args = curl_download_args("https://bun.sh/install", Path::new("/t/install.sh"));
        assert_eq!(args[0], "-q", "curl only skips ~/.curlrc when -q comes first");
        assert!(args.windows(2).any(|w| w == ["--proto", "=https"]));
        assert!(args.windows(2).any(|w| w == ["--proto-redir", "=https"]));
        assert_eq!(args[args.len() - 3..], ["-o", "/t/install.sh", "https://bun.sh/install"]);
    }

    #[test]
    fn brain_refuses_a_key_that_could_inject_curl_directives() {
        // Refused before anything else runs or is touched.
        for bad in ["pa-x\"\noutput = \"/tmp/pwn\"", "pa-x\\y", "pa-x y"] {
            let mut lines = Vec::new();
            let err = brain(Some(bad), &mut |l| lines.push(l)).unwrap_err();
            assert!(err.contains("caractères inattendus"), "{err}");
            assert!(!err.contains(bad) && lines.is_empty());
        }
    }

    #[test]
    fn the_guard_puts_back_what_a_step_overwrote() {
        let dir = tempfile::tempdir().unwrap();
        let home = fs::canonicalize(dir.path()).unwrap();
        let hook = home.join(".claude/hooks/brain/correction-detector.py");
        fs::create_dir_all(hook.parent().unwrap()).unwrap();
        fs::write(&hook, "mine").unwrap();
        let store = home.join(GUARD_DIR);
        let mut lines = Vec::new();
        let (res, back) = guarded(&home, &store, &mut |l| lines.push(l), |log| {
            log("J'installe…".into());
            fs::write(home.join(".claude/hooks/brain/correction-detector.py"), "ours").unwrap();
            Ok("Ton cerveau est installé.".into())
        });
        let err = res.unwrap_err();
        assert!(err.contains("(~/.claude/hooks/brain/correction-detector.py (réécrit))") && err.contains("comme avant"), "{err}");
        assert_eq!(back, ["~/.claude/hooks/brain/correction-detector.py (réécrit)"]);
        assert_eq!(fs::read_to_string(&hook).unwrap(), "mine");
        assert_eq!(lines[0], "J'installe…");
        assert!(lines[1].starts_with("✗ ~/.claude/hooks/brain/correction-detector.py (réécrit)"), "{lines:?}");

        // A step that leaves the user's files alone keeps its own result.
        let (res, back) = guarded(&home, &store, &mut |_| {}, |_| Err("Bun ne répond pas.".into()));
        assert_eq!((res, back.len()), (Err("Bun ne répond pas.".into()), 0));

        // No snapshot, no step.
        let file_store = home.join("not-a-folder");
        fs::write(&file_store, "").unwrap();
        let (res, _) = guarded(&home, &file_store, &mut |_| {}, |_| panic!("the step ran without its guard"));
        assert!(res.unwrap_err().contains("Je n'ai rien installé"));
    }

    #[test]
    fn unknown_step_fails_cleanly() {
        let r = run("rm -rf", None, &mut |_| {});
        assert!(!r.ok);
        assert_eq!(r.id, "rm -rf");
    }

    /// Real download (233 MB) into a temp folder: `cargo test -- --ignored obsidian_real`.
    #[cfg(target_os = "macos")]
    #[test]
    #[ignore]
    fn obsidian_real_download_lands_signed() {
        let apps = tempfile::tempdir().unwrap();
        let app = install_obsidian_into(apps.path(), &mut |l| println!("{l}")).unwrap();
        assert!(sys::is_executable(&app.join("Contents/MacOS/Obsidian")));
        assert!(signed_by_obsidian(&app).is_ok());
        let leftovers: Vec<_> = fs::read_dir(apps.path()).unwrap().filter_map(Result::ok).map(|e| e.file_name()).collect();
        assert_eq!(leftovers, vec![std::ffi::OsString::from("Obsidian.app")], "no staging folder left");
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn lists_read_as_french() {
        let l = |v: &[&str]| list_fr(&v.iter().map(|s| s.to_string()).collect::<Vec<_>>());
        assert_eq!((l(&[]), l(&["git"]), l(&["git", "curl"]), l(&["git", "curl", "unzip"])), (
            String::new(),
            "git".into(),
            "git et curl".into(),
            "git, curl et unzip".into()
        ));
    }

    /// Obsidian's package, as install.sh pins it: the same URL, the same SHA-256.
    #[cfg(target_os = "linux")]
    #[test]
    fn obsidian_is_the_package_install_sh_pins() {
        let script = include_str!("../../../install.sh");
        let value = |key: &str| script.lines().find_map(|l| l.strip_prefix(&format!("{key}="))).unwrap().trim_matches('"').to_string();
        let version = value("OBSIDIAN_VERSION");
        assert_eq!(value("OBSIDIAN_DEB_SHA256"), OBSIDIAN_DEB_SHA256);
        let url = value("OBSIDIAN_DEB_URL").replace("$OBSIDIAN_VERSION", &version).replace("${OBSIDIAN_VERSION}", &version);
        assert_eq!(url, OBSIDIAN_DEB_URL);
    }

    /// The Bun the app installs is the one gbrain is tested with, which install.sh installs.
    #[cfg(not(windows))]
    #[test]
    fn bun_is_the_one_gbrain_pin_json_names() {
        let pin: Value = serde_json::from_str(include_str!("../../../engine/gbrain-pin.json")).unwrap();
        assert_eq!(BUN_PIN, format!("bun-v{}", pin["bun"].as_str().unwrap()));
    }

    #[cfg(windows)]
    #[test]
    fn install_ps1_sections_are_all_known_and_in_order() {
        // Every section install.ps1 announces, from its own source: none may go unnoticed.
        let script = include_str!("../../../install.ps1");
        let titles: Vec<&str> = script
            .lines()
            .filter_map(|l| l.trim_start().strip_prefix("Say \""))
            .filter_map(|l| l.split('"').next())
            .filter(|t| !t.starts_with("Telechargement"))
            .collect();
        assert_eq!(titles.len(), SECTIONS.len() + CHOSEN_SECTIONS.len(), "{titles:?}");
        let mut sections = Sections::default();
        let mut counted = 0;
        for title in &titles {
            let said = sections.progress(&format!("=== {title} ===")).unwrap_or_else(|| panic!("{title}"));
            if let Some((_, chosen)) = CHOSEN_SECTIONS.iter().find(|(start, _)| title.starts_with(start)) {
                // Codex's, which only a brain with Codex runs: no count, Claude alone's stays 13.
                assert_eq!(said, format!("▸ {chosen}…"));
                continue;
            }
            counted += 1;
            assert!(said.starts_with("▸ ") && said.ends_with(&format!("({counted} sur {})…", SECTIONS.len())), "{said}");
        }
        assert_eq!(counted, SECTIONS.len());
        // The download (irm | iex only), an unknown section, a plain line: nothing to say.
        let mut fresh = Sections::default();
        for quiet in ["=== Telechargement de brain-in-a-box ===", "=== Nouveau ===", "  [OK] python", "Preflight"] {
            assert_eq!(fresh.progress(quiet), None, "{quiet}");
        }
        assert!(fresh.progress("=== Memoire gbrain (2 a 5 minutes la premiere fois) ===").is_some());
        assert_eq!(fresh.progress("=== Preflight ==="), None, "never backwards");
    }

    #[cfg(windows)]
    #[test]
    fn ps_quote_survives_powershell() {
        let dir = tempfile::tempdir().unwrap();
        for (i, s) in ["plain", "with space", "Jo's \"brain\"", "Jo\u{2019}s ‘x’ ‚y‛", "$HOME `id` $(id) ; & | \\", "''", "é à", ""]
            .iter()
            .enumerate()
        {
            let out = dir.path().join(format!("out{i}"));
            let script = dir.path().join(format!("q{i}.ps1"));
            let body = format!("[IO.File]::WriteAllText({}, {})\r\n", ps_quote(&out.to_string_lossy()), ps_quote(s));
            fs::write(&script, [&[0xEF, 0xBB, 0xBF][..], body.as_bytes()].concat()).unwrap();
            let o = sys::run(
                &sys::powershell(),
                &["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", &script.to_string_lossy()],
                Duration::from_secs(60),
            );
            assert!(o.ok(), "{s}: {}", o.stderr);
            assert_eq!(fs::read_to_string(&out).unwrap(), *s);
        }
    }

    #[cfg(windows)]
    #[test]
    fn windows_scripts_quote_every_path() {
        let path = std::ffi::OsString::from(r"C:\Users\Jo O'Brien\.local\bin;C:\Windows");
        let claude = Path::new(r"C:\Users\Jo O'Brien\.local\bin\claude.exe");
        let login = login_script(&path, claude);
        assert!(login.contains(r"$env:Path = 'C:\Users\Jo O''Brien\.local\bin;C:\Windows'"), "{login}");
        assert!(login.contains(r"& 'C:\Users\Jo O''Brien\.local\bin\claude.exe' auth login"), "{login}");
        let update = claude_script(&path, claude, "update");
        assert!(update.contains(r"& 'C:\Users\Jo O''Brien\.local\bin\claude.exe' update"), "{update}");
        let vault = Path::new(r"C:\Users\Jo O'Brien\Documents\Brain");
        let guide = Path::new(r"C:\Users\Jo O'Brien\.brain-in-a-box\src\onboarding\ONBOARDING.md");
        let onboarding = onboarding_script(&path, vault, guide, claude, &ONBOARDING_MODE);
        assert!(onboarding.contains(r"Set-Location -LiteralPath 'C:\Users\Jo O''Brien\Documents\Brain'"), "{onboarding}");
        assert!(onboarding.contains(r"\claude.exe' --permission-mode acceptEdits 'Lis C:\Users\Jo O''Brien\.brain-in-a-box\src\onboarding\ONBOARDING.md et suis-le pour me personnaliser. Mon cerveau est dans C:\Users\Jo O''Brien\Documents\Brain. Parle-moi en français"), "{onboarding}");
        let talk = claude_in_brain_script(&path, vault, claude);
        assert!(talk.contains(r"Set-Location -LiteralPath 'C:\Users\Jo O''Brien\Documents\Brain'"), "{talk}");
        assert!(talk.contains("& 'C:\\Users\\Jo O''Brien\\.local\\bin\\claude.exe'\r\n"), "no prompt, no option: {talk}");
        assert!(same_folder(r"C:\Users\jo\Documents\Brain\", "c:/users/JO/Documents/Brain"));

        let codex = Path::new(r"C:\Users\Jo O'Brien\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe");
        let home = Path::new(r"C:\Users\Jo O'Brien\Codex ‘x’");
        for script in [codex_login_script(&path, codex, Some(home)), codex_onboarding_script(&path, vault, guide, codex, None)] {
            assert!(script.contains(r"$env:Path = 'C:\Users\Jo O''Brien\.local\bin;C:\Windows'"), "{script}");
            for k in CODEX_KEYS {
                assert!(script.contains(&format!("Remove-Item -LiteralPath Env:{k} -ErrorAction SilentlyContinue")), "{script}");
            }
        }
        let login = codex_login_script(&path, codex, Some(home));
        assert!(login.contains(r"$env:CODEX_HOME = 'C:\Users\Jo O''Brien\Codex ‘‘x’’'"), "{login}");
        assert!(
            login.contains(r#"& 'C:\Users\Jo O''Brien\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe' -c 'cli_auth_credentials_store="file"' login"#),
            "{login}"
        );
        let onboarding = codex_onboarding_script(&path, vault, guide, codex, None);
        assert!(onboarding.contains("Remove-Item -LiteralPath Env:CODEX_HOME -ErrorAction SilentlyContinue"), "{onboarding}");
        assert!(!onboarding.contains("$env:CODEX_HOME"), "{onboarding}");
        assert!(onboarding.contains(r"Set-Location -LiteralPath 'C:\Users\Jo O''Brien\Documents\Brain'"), "{onboarding}");
        assert!(
            onboarding.contains(
                r#"codex.exe' -c 'cli_auth_credentials_store="file"' 'Lis C:\Users\Jo O''Brien\.brain-in-a-box\src\onboarding\ONBOARDING.md et suis-le pour me personnaliser. Mon cerveau est dans C:\Users\Jo O''Brien\Documents\Brain."#
            ),
            "{onboarding}"
        );
    }
    /// The interview speaks French, plainly, and names the brain's real folder (Windows may have put it in
    /// the profile's folder): never ~/Documents/Brain written in for every one.
    #[test]
    fn the_interview_is_asked_in_french_with_the_real_folder() {
        let (guide, vault) = (Path::new("/k/onboarding/ONBOARDING.md"), Path::new("/Users/jo/Brain"));
        let p = onboarding_prompt(guide, vault, false);
        assert!(p.starts_with("Lis /k/onboarding/ONBOARDING.md et suis-le pour me personnaliser."), "{p}");
        assert!(p.contains("Mon cerveau est dans /Users/jo/Brain.") && p.contains("français") && p.contains("mots simples"), "{p}");
        assert!(!p.contains("personalize") && !p.contains("acceptEdits"), "{p}");
        // A guide from before may still name ~/Documents/Brain: Claude is told it means this folder, where it works.
        assert!(p.contains("Si le guide dit ~/Documents/Brain, c'est ce dossier."), "{p}");
        // In acceptEdits, no permission to announce (the guide's « Before your first write »).
        let p = onboarding_prompt(guide, vault, true);
        assert!(p.ends_with("Tu es lancé en mode acceptEdits : tu écris dans ce dossier sans demander la permission, ne m'annonce donc aucune demande."), "{p}");
        assert_eq!(ONBOARDING_MODE, ["--permission-mode", "acceptEdits"]);
    }

    /// The interview accepts edits only in a brain that is a folder of its own, the kit's: never the home
    /// folder, a parent of it, Documents or a drive (Windows' PowerShell profile is in Documents, and
    /// acceptEdits lets Claude write it, or rm, without asking).
    #[test]
    fn the_interview_accepts_edits_only_in_a_folder_of_its_own() {
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().join("jo");
        let kit_brain = |d: &Path| {
            fs::create_dir_all(d.join("Profile")).unwrap();
            fs::write(d.join("CLAUDE.md"), "# Brain").unwrap();
        };
        for d in [home.join("Documents/Brain"), home.join("Brain"), dir.path().join("D/notes/Brain")] {
            kit_brain(&d);
            assert_eq!(onboarding_mode(&d, &home), ONBOARDING_MODE, "{}", d.display());
        }
        for d in [home.clone(), home.join("Documents"), home.join("Desktop"), dir.path().to_path_buf()] {
            kit_brain(&d);
            assert!(onboarding_mode(&d, &home).is_empty(), "{}", d.display());
        }
        let bare = home.join("Notes");
        fs::create_dir_all(&bare).unwrap();
        assert!(onboarding_mode(&bare, &home).is_empty(), "not the kit's brain");
        assert!(onboarding_mode(Path::new("/"), &home).is_empty());
        assert!(onboarding_mode(Path::new("Brain"), &home).is_empty(), "a relative path");
    }

    /// Documents and the Desktop where Windows put them (OneDrive moves both, and the PowerShell profile
    /// with Documents), and under any other name for them: a link to Documents, Documents in other
    /// letters. Neither one, nor a folder that holds one, is a brain of its own.
    #[test]
    fn the_interview_sees_documents_where_windows_put_it() {
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().join("jo");
        let documents = home.join("OneDrive").join("Documents");
        let shared = [documents.clone(), home.join("OneDrive").join("Bureau")];
        let kit_brain = |d: &Path| {
            fs::create_dir_all(d.join("Profile")).unwrap();
            fs::write(d.join("CLAUDE.md"), "# Brain").unwrap();
        };
        for d in [documents.clone(), home.join("OneDrive"), shared[1].clone()] {
            kit_brain(&d);
            assert!(!own_brain(&d, &home, &shared), "{}", d.display());
        }
        let brain = documents.join("Brain");
        kit_brain(&brain);
        assert!(own_brain(&brain, &home, &shared));
        #[cfg(unix)]
        {
            let link = dir.path().join("lien");
            std::os::unix::fs::symlink(&documents, &link).unwrap();
            assert!(!own_brain(&link, &home, &shared), "a link to Documents");
        }
        #[cfg(windows)]
        {
            let upper = PathBuf::from(documents.to_string_lossy().to_uppercase());
            assert!(!own_brain(&upper, &home, &shared), "Documents in capitals: {}", upper.display());
        }
    }

    /// « Parler à Claude »: Claude Code in the brain, nothing typed for the user, no option.
    #[cfg(not(windows))]
    #[test]
    fn talking_to_claude_opens_it_in_the_brain_without_a_prompt() {
        let dir = tempfile::tempdir().unwrap();
        let root = fs::canonicalize(dir.path()).unwrap();
        let vault = root.join("Jo's home $x").join("Brain é");
        fs::create_dir_all(&vault).unwrap();
        let bin = root.join("b'in");
        fs::create_dir_all(&bin).unwrap();
        let out = root.join("out");
        write_exec(&bin.join("claude"), &format!("#!/bin/sh\n{{ pwd -P; echo \"args=$#\"; }} > {}\n", sh_quote(&out.to_string_lossy())));
        let script = root.join("claude.command");
        fs::write(&script, claude_in_brain_script(&bin.clone().into_os_string(), &vault, &bin.join("claude"))).unwrap();
        let o = bash(&script);
        assert!(o.ok() && o.stdout.contains("Tu peux fermer cette fenêtre."), "{}", o.stderr);
        assert_eq!(fs::read_to_string(&out).unwrap(), format!("{}\nargs=0\n", vault.display()));
        // Before Claude, in French: what to type, the question it asks in English, how to end.
        let before = &o.stdout[..o.stdout.find("Tu peux fermer").unwrap()];
        assert!(before.contains("Claude t'écoute") && before.contains("trust") && before.contains("ferme cette fenêtre"), "{}", o.stdout);
        // Not logged in: Claude Code would open on its login menu, in English, API billing among its choices.
        let claude = Some(bin.join("claude"));
        let err = talk_to_claude(claude.clone(), Some(vault.clone()), || false).unwrap_err();
        assert!(err.contains("Connecte d'abord"), "{err}");
        assert!(talk_to_claude(claude, Some(vault.clone()), || true).unwrap().contains("écoute : écris ta question"));
        assert_eq!(talk_to_claude(None, Some(vault), || true).unwrap_err(), "Claude Code n'est pas encore installé.");
    }

    /// Windows Terminal runs the script from its folder, which goes as an option of its own: the words of
    /// the command it rebuilds hold no space.
    #[test]
    fn windows_terminal_opens_the_script_from_its_folder() {
        let script = Path::new("/Users/Jo Doe/.brain-in-a-box/claude.ps1");
        let ps = Path::new("/w/powershell.exe");
        let args: Vec<String> = wt_args(script, ps).iter().map(|a| a.to_string_lossy().into_owned()).collect();
        assert_eq!(
            args,
            ["-w", "new", "-d", "/Users/Jo Doe/.brain-in-a-box", "/w/powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-File", "claude.ps1"]
        );
        assert!(args[4..].iter().all(|a| !a.contains(' ') && !a.contains(';')), "{args:?}");
    }

    /// No sentence of the install sends anyone to a team, which does not exist: the report goes to the
    /// person who advised Bibo.
    #[test]
    fn no_one_is_sent_to_a_team() {
        let team = ["l'é", "quipe"].concat();
        for (name, src) in [
            ("steps.rs", include_str!("steps.rs")),
            ("repair.rs", include_str!("repair.rs")),
            ("root.rs", include_str!("root.rs")),
            ("diagnose.rs", include_str!("diagnose.rs")),
        ] {
            assert!(!src.contains(&team), "{name} still names a team");
        }
    }

    /// A stop is said in one French sentence that says what to do: never the line PowerShell stopped on.
    #[test]
    fn a_stop_says_what_to_do_never_the_raw_line() {
        let raw = [
            "  [OK] python (C:\\Users\\Jo\\AppData\\Local\\Programs\\Python\\Python314\\python.exe)",
            "brain-in-a-box : installation arretee - gbrain : bun install a echoue",
            "At C:\\Users\\Jo\\.brain-in-a-box\\src\\install.ps1:91 char:5",
            "+     throw \"brain-in-a-box : installation arretee - $t\"",
            "+ CategoryInfo          : OperationStopped: (brain-in-a-box ...stallation arretee:String) [], RuntimeException",
            "+ FullyQualifiedErrorId : brain-in-a-box : installation arretee - gbrain : bun install a echoue",
        ];
        let mut stop = Stop::default();
        for l in raw {
            assert!(!stop.see(l), "{l} stays in the log");
        }
        for timed_out in [false, true] {
            let m = stop.message(timed_out);
            assert!(!m.contains("FullyQualifiedErrorId") && !m.contains("arretee") && !m.contains("code 1"), "{m}");
            assert!(m.contains("« Réessayer »"), "{m}");
            assert_eq!(m.contains("conseillé Bibo"), !timed_out, "{m}");
            assert_eq!(m.contains("Wi-Fi"), timed_out, "{m}");
        }
    }

    /// install.ps1's word for the app (BIAB_ARRET=<cause>) gives the cause's sentence and what to do; the
    /// line itself stays out of the log, where a cause of the catalogue would look forged to the repair.
    #[test]
    fn biab_arret_gives_its_cause_and_what_to_do() {
        let mut stop = Stop::default();
        assert!(!stop.see("=== Preflight ==="));
        assert!(stop.see("BIAB_ARRET=winget_absent"));
        assert!(!stop.see("  [X]  Python introuvable. Installe Python 3 depuis https://www.python.org/downloads/ puis relance la meme commande."));
        let m = stop.message(false);
        assert!(m.contains("winget") && m.contains("App Installer") && m.contains("« Réessayer »"), "{m}");
        assert_eq!(Some(m), repair::stop_message("winget_absent"));
        // A word it does not know (an older or newer install.ps1): the plain sentence.
        let mut odd = Stop::default();
        assert!(odd.see("BIAB_ARRET=inconnu_ici"));
        assert!(odd.message(false).starts_with("L'installation de ton cerveau s'est arrêtée avant la fin."));
        // What Windows says when it refuses, in English or in French.
        for (line, cause) in [
            ("Cannot invoke method. Method invocation is supported only on core types in this language mode.", "powershell_verrouille"),
            ("Impossible d'appeler la méthode. L'appel de méthode est pris en charge uniquement sur les types principaux dans ce mode de langage.", "powershell_verrouille"),
            ("Invoke-RestMethod : The remote server returned an error: (407) Proxy Authentication Required.", "proxy_entreprise"),
            ("This program is blocked by group policy. For more information, contact your system administrator.", "strategie_entreprise"),
            ("Ce programme est bloqué par une stratégie de groupe.", "strategie_entreprise"),
            // An execution policy a company imposes (AllSigned, Restricted) wins over -ExecutionPolicy Bypass.
            ("File C:\\Users\\Jo\\.brain-in-a-box\\src\\install.ps1 cannot be loaded because running scripts is disabled on this system. For more information, see about_Execution_Policies at https:/go.microsoft.com/fwlink/?LinkID=135170.", "powershell_verrouille"),
            ("File C:\\x\\install.ps1 cannot be loaded. The file C:\\x\\install.ps1 is not digitally signed. You cannot run this script on the current system.", "powershell_verrouille"),
            ("Impossible de charger le fichier C:\\x\\install.ps1, car l'exécution de scripts est désactivée sur ce système. Pour plus d'informations, consultez about_Execution_Policies à l'adresse https://go.microsoft.com/fwlink/?LinkID=135170.", "powershell_verrouille"),
            ("Le fichier C:\\x\\install.ps1 n'est pas signé numériquement. Vous ne pouvez pas exécuter ce script sur le système actuel.", "powershell_verrouille"),
            ("Operation did not complete successfully because the file contains a virus or potentially unwanted software.", "antivirus_bloque"),
        ] {
            assert_eq!(refusal(line), Some(cause), "{line}");
            let m = repair::stop_message(cause).unwrap();
            assert!(m.contains("service informatique"), "{cause}: {m}");
        }
        assert_eq!(refusal("Downloading Claude Code 2.1.300…"), None);
    }

    /// A refusal install.ps1 got past (Git's installer blocked, a warning) says nothing of a later stop in
    /// another section; within one, the last refusal is the one said.
    #[test]
    fn a_refusal_counts_in_its_own_section_the_last_one_said() {
        let mut stop = Stop::default();
        for l in [
            "=== Preflight ===",
            "  [!]  Git : son installeur officiel n'a pas tourne : This program is blocked by group policy.",
            "  [OK] python (C:\\Python314\\python.exe)",
            "=== Brain vault (C:\\Users\\jo\\Brain) ===",
            "+ FullyQualifiedErrorId : System.IO.IOException",
        ] {
            stop.see(l);
        }
        assert!(stop.message(false).starts_with("L'installation de ton cerveau s'est arrêtée avant la fin."), "{}", stop.message(false));
        let mut stop = Stop::default();
        stop.see("=== Memoire gbrain (2 a 5 minutes la premiere fois) ===");
        stop.see("Invoke-RestMethod : The remote server returned an error: (407) Proxy Authentication Required.");
        stop.see("This program is blocked by group policy. For more information, contact your system administrator.");
        assert_eq!(Some(stop.message(false)), repair::stop_message("strategie_entreprise"));
        // install.ps1's own word wins over any refusal, whatever comes after.
        stop.see("BIAB_ARRET=reseau");
        stop.see("=== Verification ===");
        assert_eq!(Some(stop.message(false)), repair::stop_message("reseau"));
    }

    /// The brain installed without Git or Bun works by keywords: done, saying what comes later; without
    /// Obsidian, done too. Without Python, nothing runs: not done.
    #[test]
    fn git_or_bun_missing_leave_a_brain_that_works() {
        let at = "C:\\Users\\jo\\Brain";
        let m = installed(at, false, false, &["Git", "Bun"]).unwrap();
        assert!(m.starts_with("Ton cerveau est installé dans C:\\Users\\jo\\Brain et il marche : il retrouve tes notes par mots-clés."), "{m}");
        assert!(m.contains("Git et Bun n'ont pas pu s'installer") && m.contains("la recherche par le sens viendra plus tard"), "{m}");
        // The step succeeded: the window shows no « Réessayer » then. Bibo's check, another day, brings them.
        assert!(!m.contains("Réessayer") && m.contains("rouvre Bibo"), "{m}");
        let m = installed(at, true, true, &["Bun"]).unwrap();
        assert!(m.contains("Bun n'a pas pu s'installer") && m.contains("mots-clés"), "{m}");
        let m = installed(at, true, false, &["Obsidian"]).unwrap();
        assert!(m.contains("avec la recherche par le sens") && m.ends_with("il est gratuit sur obsidian.md."), "{m}");
        assert_eq!(installed(at, false, false, &[]).unwrap(), format!("Ton cerveau est installé dans {at}. Il cherche par mots-clés ; tu pourras ajouter une clé Voyage plus tard."));
        let err = installed(at, false, false, &["Python", "Git"]).unwrap_err();
        assert!(err.contains("Python") && err.contains("« Réessayer »"), "{err}");
    }

    /// « Mettre à jour mon cerveau » on an install from before the importer: the step installs again
    /// (it used to say « déjà installé », and the button sent the user back to it, round and round).
    #[cfg(not(windows))]
    #[test]
    fn a_brain_without_the_importer_is_not_up_to_date() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        crate::detect::tests::fake_brain(home);
        let claude = Agents::claude_alone();
        assert!(detect::brain(home, &claude).complete(), "complete() stays as it is: the guide and « Content de te revoir » read it");
        assert!(!up_to_date(home, &claude, None));
        let importer = home.join(".claude/hooks/brain/importer-memoire.py");
        fs::create_dir_all(importer.parent().unwrap()).unwrap();
        fs::write(&importer, "# importer").unwrap();
        assert!(up_to_date(home, &claude, None));
        // A key to add, a brain that has search by meaning already: nothing to do either.
        assert!(up_to_date(home, &claude, Some("pa-x")));
    }

    /// An installer that keeps downloading is never cut; one that stays still is, after its pause.
    #[cfg(unix)]
    #[test]
    fn a_slow_download_goes_on_a_stalled_one_stops() {
        let dir = tempfile::tempdir().unwrap();
        let watched = dir.path().join("downloads");
        fs::create_dir_all(&watched).unwrap();
        let file = sh_quote(&watched.join("claude-x").to_string_lossy());
        let pace = Pace { stall: Duration::from_millis(1500), limit: Duration::from_secs(30), tick: Duration::from_millis(200) };
        // Silent, but its file grows: three times the pause, and done.
        let grows = dir.path().join("grows.sh");
        fs::write(&grows, format!("for i in $(seq 1 30); do printf 0123456789 >> {file}; sleep 0.2; done\n")).unwrap();
        let t = Instant::now();
        let ended = run_watched(Path::new("/bin/sh"), &[&grows.to_string_lossy()], &[], dir.path(), std::slice::from_ref(&watched), &pace, &mut |_| {});
        assert_eq!(ended, Ended::Code(0));
        assert!(t.elapsed() > pace.stall * 3, "{:?}", t.elapsed());
        // Talks, then nothing: stopped after the pause, everything it started with it.
        let still = dir.path().join("still.sh");
        let mark = dir.path().join("after");
        fs::write(&still, format!("echo starting\nsleep 20\ntouch {}\n", sh_quote(&mark.to_string_lossy()))).unwrap();
        let mut lines = Vec::new();
        let t = Instant::now();
        let ended = run_watched(Path::new("/bin/sh"), &[&still.to_string_lossy()], &[], dir.path(), std::slice::from_ref(&watched), &pace, &mut |l| lines.push(l));
        assert_eq!(ended, Ended::Stalled);
        assert!(t.elapsed() < Duration::from_secs(10) && lines == ["starting"], "{:?} {lines:?}", t.elapsed());
        // Past its limit, moving or not.
        let slow = Pace { stall: Duration::from_secs(30), limit: Duration::from_secs(1), tick: Duration::from_millis(200) };
        assert_eq!(run_watched(Path::new("/bin/sh"), &[&grows.to_string_lossy()], &[], dir.path(), &[watched], &slow, &mut |_| {}), Ended::TooSlow);
        assert!(!mark.exists());
        // What the window says then.
        let stalled = installer_ended("Claude Code", Ended::Stalled, None).unwrap_err();
        assert!(stalled.contains("n'avance plus depuis 5 minutes") && stalled.contains("Wi-Fi") && stalled.contains("« Réessayer »"), "{stalled}");
        let slow = installer_ended("Claude Code", Ended::TooSlow, None).unwrap_err();
        assert!(slow.contains("très lente") && slow.contains("Wi-Fi"), "{slow}");
        let locked = installer_ended("Claude Code", Ended::Code(1), Some("powershell_verrouille")).unwrap_err();
        assert!(locked.contains("service informatique"), "{locked}");
        let other = installer_ended("Claude Code", Ended::Code(1), Some("antivirus_bloque")).unwrap_err();
        assert!(other.starts_with("L'installeur de Claude Code s'est arrêté avant la fin.") && !other.contains("code 1"), "{other}");
        assert_eq!(installer_ended("Claude Code", Ended::Code(0), None), Ok(()));
        assert_eq!(PACE.stall, Duration::from_secs(5 * 60));
        assert_eq!(PACE.limit, Duration::from_secs(60 * 60));
    }

    /// What a passing test saw, as a notice of the CI run (GitHub reads it in the step's output): the test
    /// harness keeps println! to itself, not a write to stdout's handle.
    #[cfg(windows)]
    fn ci_notice(said: &str) {
        if std::env::var_os("GITHUB_ACTIONS").is_some() {
            let _ = io::stdout().lock().write_all(format!("\n::notice::{}\n", said.replace(['\r', '\n'], " ")).as_bytes());
        }
    }

    /// A proxy that lets no tunnel through (407, Negotiate or NTLM asked; then 403), and hands over each
    /// request it got.
    fn refusing_proxy() -> (String, mpsc::Receiver<String>) {
        use std::io::{BufRead, BufReader};
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            for conn in listener.incoming().take(8) {
                let Ok(mut conn) = conn else { continue };
                let _ = conn.set_read_timeout(Some(Duration::from_secs(10)));
                let mut reader = BufReader::new(conn.try_clone().unwrap());
                loop {
                    let (mut head, mut line) = (String::new(), String::new());
                    while reader.read_line(&mut line).is_ok_and(|n| n > 0) && line != "\r\n" {
                        head.push_str(&line);
                        line.clear();
                    }
                    if head.is_empty() {
                        break;
                    }
                    let answered = head.contains("Proxy-Authorization:");
                    let _ = tx.send(head);
                    let reply = if answered {
                        "HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                    } else {
                        "HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Negotiate\r\nProxy-Authenticate: NTLM\r\nContent-Length: 0\r\n\r\n"
                    };
                    if conn.write_all(reply.as_bytes()).is_err() || answered {
                        break;
                    }
                }
            }
        });
        (format!("http://{addr}/"), rx)
    }

    /// Behind a company's proxy, a download goes through it, offering the user's own Windows login when it
    /// asks (curl.exe ignores the system's proxy by itself); refused, the sentence says what to ask the IT
    /// service. Without a proxy, the connection, and the window's button.
    #[test]
    fn a_download_goes_through_the_proxy_and_says_so_when_refused() {
        let dir = tempfile::tempdir().unwrap();
        let dest = dir.path().join("x");
        let url = "https://brain-in-a-box.invalid/x";
        let (proxy, asked) = refusing_proxy();
        let err = download_via(url, &dest, "l'installeur", None, Duration::from_secs(60), Some(&proxy), &mut |_| {}).unwrap_err();
        assert_eq!(Some(err), repair::stop_message("proxy_entreprise"));
        let heads: Vec<String> = asked.try_iter().collect();
        assert!(heads.first().is_some_and(|h| h.starts_with("CONNECT brain-in-a-box.invalid:443 ")), "{heads:?}");
        // Whether Windows hands curl.exe a token for the session's login depends on the machine (a runner of
        // 2026-10-07 did, NTLM; one of 2026-10-21 did not): said in a CI notice, not asserted.
        #[cfg(windows)]
        {
            let offered = heads.iter().find_map(|h| h.lines().find(|l| l.starts_with("Proxy-Authorization:")).map(|l| l.split(' ').take(2).collect::<Vec<_>>().join(" ")));
            ci_notice(&format!("a_download_goes_through_the_proxy: {} request(s), the Windows login offered: {offered:?}", heads.len()));
        }
        let err = download_via(url, &dest, "l'installeur", None, Duration::from_secs(60), None, &mut |_| {}).unwrap_err();
        assert!(err.contains("connexion internet") && err.contains("« Réessayer »"), "{err}");
    }

    /// The system's proxy for a site, as PowerShell says it: a plain http:// address, or nothing.
    #[test]
    fn the_system_proxy_is_read_plainly() {
        assert_eq!(proxy_url("http://proxy.corp.local:8080/\r\n"), Some("http://proxy.corp.local:8080/".to_string()));
        let long = format!("http://{}/", "x".repeat(300));
        for odd in ["", "https://claude.ai/install.ps1", "proxy:8080", "http://a b:80/", "http://x\"y/", "http://p:1/\nhttp://q:2/", long.as_str()] {
            assert_eq!(proxy_url(odd), None, "{odd:?}");
        }
        // A proxy that opens HTTPS to look inside signs with the company's certificate, often without a
        // revocation list: Windows' curl.exe (Schannel) then fails (0x80092012) unless the check is best
        // effort (a revoked certificate still fails).
        assert_eq!(
            curl_proxy_args("http://p:8080/"),
            ["--proxy", "http://p:8080/", "--proxy-anyauth", "--proxy-user", ":", "--ssl-revoke-best-effort"]
        );
    }

    /// Claude Code's installer downloads with Windows PowerShell's web requests, which never offer the
    /// user's Windows login to a proxy that asks for it (407): it runs after a line that does.
    #[test]
    fn claude_codes_installer_runs_with_the_windows_login_for_a_proxy() {
        assert!(
            INSTALLER_WRAPPER.starts_with("try { [Net.WebRequest]::DefaultWebProxy.Credentials = [Net.CredentialCache]::DefaultNetworkCredentials } catch {}\r\n"),
            "{INSTALLER_WRAPPER}"
        );
        assert!(INSTALLER_WRAPPER.contains("& (Join-Path $PSScriptRoot 'install.ps1')\r\n") && INSTALLER_WRAPPER.ends_with("exit $LASTEXITCODE\r\n"));
    }

    #[cfg(windows)]
    #[test]
    fn the_wrapper_runs_the_installer_with_the_windows_login() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(
            dir.path().join("install.ps1"),
            "Write-Output ([Net.WebRequest]::DefaultWebProxy.Credentials -eq [Net.CredentialCache]::DefaultNetworkCredentials)\r\nexit 3\r\n",
        )
        .unwrap();
        let wrapper = dir.path().join("lance.ps1");
        fs::write(&wrapper, INSTALLER_WRAPPER).unwrap();
        let args = ps1_args(&wrapper);
        let args: Vec<&str> = args.iter().map(String::as_str).collect();
        let o = sys::run(&sys::powershell(), &args, Duration::from_secs(60));
        assert_eq!((o.code, o.stdout.trim()), (3, "True"), "{}", o.stderr);
        // Its stop is the installer's: a throw ends the run with an error code.
        fs::write(dir.path().join("install.ps1"), "throw 'Failed to get latest version: (407) Proxy Authentication Required.'\r\n").unwrap();
        let o = sys::run(&sys::powershell(), &args, Duration::from_secs(60));
        assert!(o.code != 0 && o.stderr.contains("(407)"), "{} {}", o.code, o.stderr);
    }

    /// The app keeps the PC awake itself while install.ps1 runs: it says so (install.ps1 then compiles
    /// nothing for it, see Set-Awake).
    #[test]
    fn install_ps1_is_told_the_app_runs_it() {
        let choice = vec![("BIAB_AGENTS", "claude".to_string())];
        let env = brain_ps1_env(Some("k"), None, &choice);
        assert!(env.contains(&("BIAB_APP", Some("1"))), "{env:?}");
        assert!(env.contains(&("VOYAGE_API_KEY", Some("k"))) && env.contains(&("BRAIN_DIR", None)) && env.contains(&("BIAB_AGENTS", Some("claude"))), "{env:?}");
    }

    /// « Parler à Claude »'s window on a real Windows: Windows Terminal when it is there (the script then
    /// runs in one of its sessions: WT_SESSION is set), else PowerShell's own window. The script runs.
    #[cfg(windows)]
    #[test]
    fn a_window_of_windows_runs_its_script() {
        let dir = sys::home().join(".brain-in-a-box");
        let mark = dir.join(format!("fenetre-{}.txt", std::process::id()));
        let _ = fs::remove_file(&mark);
        let script = format!("Set-Content -LiteralPath {} -Value ('wt=' + $env:WT_SESSION) -Encoding ascii\r\n", ps_quote(&mark.to_string_lossy()));
        open_window("test-fenetre", &script).unwrap();
        let start = Instant::now();
        while !mark.is_file() && start.elapsed() < Duration::from_secs(90) {
            thread::sleep(Duration::from_millis(500));
        }
        thread::sleep(Duration::from_millis(500));
        let said = fs::read_to_string(&mark).unwrap_or_default();
        let _ = fs::remove_file(&mark);
        ci_notice(&format!("a_window_of_windows_runs_its_script: Windows Terminal {:?}, the script wrote {said:?}", windows_terminal()));
        assert!(said.starts_with("wt="), "no window ran the script");
        assert_eq!(said.trim().len() > "wt=".len(), windows_terminal().is_some(), "{said}");
    }

    /// The query of the system's proxy runs, and a runner reaches the internet directly.
    #[cfg(windows)]
    #[test]
    fn the_system_proxy_is_asked_to_windows() {
        let o = ask_system_proxy("https://github.com/BrainInBox/brain-in-a-box");
        assert!(o.ok() && o.stdout.trim().is_empty(), "{} {} {}", o.code, o.stdout, o.stderr);
        assert_eq!(system_proxy("https://claude.ai/install.ps1"), None);
    }

    /// The computer stays awake while a step runs, and is let go after.
    #[cfg(target_os = "macos")]
    #[test]
    fn a_step_keeps_the_mac_awake_until_it_ends() {
        let mut awake = KeepAwake::start();
        let held = awake.held.as_mut().expect("caffeinate started");
        let pid = held.id().to_string();
        assert!(held.try_wait().unwrap().is_none(), "caffeinate runs");
        let cmd = sys::run(Path::new("/bin/ps"), &["-o", "command=", "-p", &pid], Duration::from_secs(5)).stdout;
        assert_eq!(cmd.trim(), format!("/usr/bin/caffeinate -i -w {}", std::process::id()));
        drop(awake);
        assert!(!sys::run(Path::new("/bin/ps"), &["-p", &pid], Duration::from_secs(5)).ok(), "caffeinate is gone");
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn a_step_asks_linux_not_to_sleep_and_lets_go() {
        // Started whenever the system has systemd-inhibit (a runner has no session to inhibit for: it may
        // stop at once), and gone, reaped, once the step ends.
        let mut awake = KeepAwake::start();
        let there = sys::find_tool("systemd-inhibit").is_some() && sys::find_tool("cat").is_some();
        assert_eq!(awake.held.is_some(), there, "systemd-inhibit there: {there}");
        let pid = awake.held.as_mut().map(|h| h.id());
        drop(awake);
        if let Some(pid) = pid {
            assert!(!Path::new(&format!("/proc/{pid}")).exists(), "systemd-inhibit {pid} outlived the step");
        }
    }

    #[cfg(windows)]
    #[test]
    fn a_step_keeps_the_pc_awake_until_it_ends() {
        let awake = KeepAwake::start();
        // SAFETY: plain flags; each call answers the state before it.
        assert_eq!(unsafe { SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) }, ES_CONTINUOUS | ES_SYSTEM_REQUIRED);
        drop(awake);
        assert_eq!(unsafe { SetThreadExecutionState(ES_CONTINUOUS) }, ES_CONTINUOUS);
    }
}
