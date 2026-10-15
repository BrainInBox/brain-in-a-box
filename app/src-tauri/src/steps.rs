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

use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::{mpsc, Mutex, MutexGuard, TryLockError};
use std::thread;
#[cfg(target_os = "macos")]
use std::time::Instant;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

use crate::config_guard;
#[cfg(not(windows))]
use crate::detect::fmt_version;
use crate::detect::{self, Found};
use crate::model::StepResult;
#[cfg(target_os = "macos")]
use crate::sys::minutes;
use crate::{perms, sys, voyage};
#[cfg(target_os = "linux")]
use crate::root;

/// Claude Code first: its installer needs none of Apple's tools, and once the user is
/// logged in, Claude can help repair the steps that follow.
#[cfg(target_os = "macos")]
pub const ORDER: &[&str] = &["claude", "clt", "bun", "obsidian", "brain"];
/// The system's tools first: everything after downloads with curl (a fresh Ubuntu desktop
/// has none), Bun's installer unzips, install.sh needs git and python3.
#[cfg(target_os = "linux")]
pub const ORDER: &[&str] = &["paquets", "claude", "bun", "obsidian", "brain"];
/// install.ps1 installs all the rest, Python and Git included.
#[cfg(windows)]
pub const ORDER: &[&str] = &["claude", "brain"];

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
/// Obsidian's official package, the one install.sh pins (a test holds them equal).
#[cfg(target_os = "linux")]
const OBSIDIAN_DEB_URL: &str = "https://github.com/obsidianmd/obsidian-releases/releases/download/v1.14.4/obsidian_1.14.4_amd64.deb";
#[cfg(target_os = "linux")]
const OBSIDIAN_DEB_SIZE: u64 = 108_364_996;
#[cfg(target_os = "linux")]
const OBSIDIAN_DEB_SHA256: &str = "85b10dcba6edfc1c0460a6d18260cf31c30447a444bd858a6440b9c9c8806d25";

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
/// install.ps1 also fetches Python, Git and Obsidian through winget.
const BRAIN_LIMIT: Duration = Duration::from_secs(if cfg!(windows) { 60 } else { 40 } * 60);
/// Our work folders (see TempDir): nothing else is ever named like them.
const WORK_PREFIX: &str = ".biab-";
const LINK_HOSTS: &[&str] = &["dashboard.voyageai.com", "docs.voyageai.com", "claude.com", "claude.ai", "obsidian.md"];

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
    let mut restored = Vec::new();
    let res = match id {
        #[cfg(target_os = "macos")]
        "clt" => clt(log),
        #[cfg(target_os = "linux")]
        "paquets" => paquets(Path::new(root::PKEXEC), log),
        #[cfg(not(windows))]
        "bun" => bun(log),
        "claude" => claude(log),
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
             Copie le rapport et envoie-le à l'équipe."
        ),
        Some(copy) => format!(
            "L'installation touchait à des fichiers à toi ({named}) et je n'ai pas pu tout remettre. \
             Une copie de tes fichiers d'avant est dans {}. Copie le rapport et envoie-le à l'équipe.",
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
#[cfg(target_os = "linux")]
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
    root::run(pkexec, root::APT_INSTALL, &missing, &listed, &by_hand, log)?;
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
    run_installer("https://bun.sh/install", "Bun", &[BUN_PIN], log)?;
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
    run_installer(CLAUDE_INSTALLER, "Claude Code", &[], log)?;
    let found = detect::claude();
    if found.answers() {
        Ok(format!("{} est installé.", found.label("Claude Code")))
    } else {
        Err("Claude Code ne répond pas après l'installation. Relance l'étape.".into())
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
    let deb_arg = deb.to_string_lossy();
    let by_hand = format!(
        "d=$(mktemp -d) && curl -fL -o \"$d/obsidian.deb\" {OBSIDIAN_DEB_URL} && echo \"{OBSIDIAN_DEB_SHA256}  $d/obsidian.deb\" | sha256sum -c - && sudo apt-get install -y \"$d/obsidian.deb\""
    );
    root::run(pkexec, root::APT_INSTALL_DEB, &[deb_arg.as_ref(), OBSIDIAN_DEB_SHA256], "Obsidian", &by_hand, log)?;
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

/// The French names of the parts install.sh did not leave behind.
fn missing_parts(brain: detect::Brain, home: &Path) -> Vec<String> {
    [
        (format!("le dossier {}", detect::brain_dir_shown(home)), brain.vault),
        ("les automatismes de Claude".into(), brain.hooks),
        ("la mémoire de recherche".into(), brain.memory),
        ("les compétences de Claude".into(), brain.skills),
    ]
    .into_iter()
    .filter(|(_, there)| !there)
    .map(|(name, _)| name)
    .collect()
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
    // Already there: run again only to add a Voyage key the brain does not have yet.
    if detect::brain(&home).complete() && !(key.is_some() && detect::embedding_model(&home).is_none()) {
        return Ok("Ton cerveau est déjà installé.".into());
    }

    let base = home.join(".brain-in-a-box");
    fs::create_dir_all(&base).map_err(|e| format!("Je n'ai pas pu créer le dossier .brain-in-a-box ({e})."))?;
    let src = install_sources(&base, &archive_source(std::env::var("BIAB_ARCHIVE").ok())?, log)?;

    log("Je mets en place ton cerveau (quelques minutes)…".into());
    // Without a key from the user, the variable is removed: a key inherited from the
    // app's own environment never sneaks in.
    let env = [("VOYAGE_API_KEY", key)];
    let script = src.join("install.sh");
    let mut last = String::new();
    let code = sys::run_streaming(Path::new("/bin/bash"), &[&script.to_string_lossy()], &env, Some(&src), BRAIN_LIMIT, &mut |line| {
        let line = match key {
            Some(k) => line.replace(k, "***"),
            None => line,
        };
        if !line.trim().is_empty() {
            last = line.trim().chars().take(200).collect();
        }
        log(line);
    });
    if code != 0 {
        return Err(format!(
            "L'installation du cerveau s'est arrêtée (code {code}) sur : « {last} ». \
             Relance l'étape ; si ça recommence, envoie le journal à l'équipe."
        ));
    }
    let missing = missing_parts(detect::brain(&home), &home);
    if !missing.is_empty() {
        return Err(format!("L'installation s'est terminée, mais il manque {}. Relance l'étape.", missing.join(", ")));
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
    // Already there: run again only to add a Voyage key the brain does not have yet.
    if detect::brain(&home).complete()
        && missing_tools(&home).is_empty()
        && !(key.is_some() && detect::embedding_model(&home).is_none())
    {
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
    let env = [("VOYAGE_API_KEY", key), ("BRAIN_DIR", dir.as_deref())];
    let mut last = String::new();
    let mut sections = Sections::default();
    let code = run_ps1(&src.join(INSTALLER), &env, &src, BRAIN_LIMIT, &mut |line| {
        let line = match key {
            Some(k) => line.replace(k, "***"),
            None => line,
        };
        if let Some(progress) = sections.progress(&line) {
            log(progress);
        }
        if !line.trim().is_empty() {
            last = line.trim().chars().take(200).collect();
        }
        log(line);
    });
    if code != 0 {
        return Err(format!(
            "L'installation du cerveau s'est arrêtée (code {code}) sur : « {last} ». \
             Relance l'étape ; si ça recommence, envoie le journal à l'équipe."
        ));
    }
    let mut missing = missing_parts(detect::brain(&home), &home);
    missing.extend(missing_tools(&home).into_iter().map(String::from));
    if !missing.is_empty() {
        return Err(format!(
            "L'installation s'est terminée, mais il manque {}. Relance l'étape ; \
             si ça recommence, copie le rapport et envoie-le à l'équipe.",
            missing.join(", ")
        ));
    }
    let at = detect::brain_dir_shown(&home);
    Ok(match (detect::embedding_model(&home), key) {
        (Some(_), _) => format!("Ton cerveau est installé dans {at}, avec la recherche par le sens."),
        (None, Some(_)) => format!(
            "Ton cerveau est installé dans {at}. Voyage n'a pas accepté la clé : il cherche par mots-clés pour l'instant."
        ),
        (None, None) => format!(
            "Ton cerveau est installé dans {at}. Il cherche par mots-clés ; tu pourras ajouter une clé Voyage plus tard."
        ),
    })
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
    let script = script.to_string_lossy();
    let args = ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script.as_ref()];
    sys::run_streaming(&sys::powershell(), &args, env, Some(cwd), limit, log)
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

pub fn start_onboarding() -> Result<(), String> {
    let home = sys::home();
    let Some(claude) = detect::claude_bin() else {
        return Err("Claude Code n'est pas encore installé.".into());
    };
    let Some(vault) = detect::vault(&home) else {
        return Err("Ton cerveau n'est pas encore installé.".into());
    };
    let guide = onboarding_guide(&home)?;
    open_in_terminal("onboarding", &onboarding_script(&sys::tool_path(), &vault, &guide, &claude))
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

fn onboarding_prompt(guide: &Path) -> String {
    format!("Read {} and run it to personalize me.", guide.display())
}

#[cfg(not(windows))]
fn login_script(path: &std::ffi::OsStr, claude: &Path) -> String {
    format!(
        "#!/bin/bash\nexport PATH={}\n{} auth login\necho\necho 'Tu peux fermer cette fenêtre.'\n",
        sh_quote(&path.to_string_lossy()),
        sh_quote(&claude.to_string_lossy())
    )
}

#[cfg(not(windows))]
fn onboarding_script(path: &std::ffi::OsStr, vault: &Path, guide: &Path, claude: &Path) -> String {
    format!(
        "#!/bin/bash\nexport PATH={}\ncd {} || exit 1\n{} {}\necho\necho 'Tu peux fermer cette fenêtre.'\n",
        sh_quote(&path.to_string_lossy()),
        sh_quote(&vault.to_string_lossy()),
        sh_quote(&claude.to_string_lossy()),
        sh_quote(&onboarding_prompt(guide))
    )
}

/// Single quotes keep everything literal in a POSIX shell, except the quote itself,
/// written '\''.
#[cfg(not(windows))]
fn sh_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', r"'\''"))
}

#[cfg(windows)]
fn login_script(path: &std::ffi::OsStr, claude: &Path) -> String {
    format!(
        "$ErrorActionPreference = 'Stop'\r\n$env:Path = {}\r\n& {} auth login\r\nWrite-Host ''\r\nWrite-Host 'Tu peux fermer cette fenêtre.'\r\n",
        ps_quote(&path.to_string_lossy()),
        ps_quote(&claude.to_string_lossy())
    )
}

#[cfg(windows)]
fn onboarding_script(path: &std::ffi::OsStr, vault: &Path, guide: &Path, claude: &Path) -> String {
    format!(
        "$ErrorActionPreference = 'Stop'\r\n$env:Path = {}\r\nSet-Location -LiteralPath {}\r\n& {} {}\r\n\
         Write-Host ''\r\nWrite-Host 'Tu peux fermer cette fenêtre.'\r\n",
        ps_quote(&path.to_string_lossy()),
        ps_quote(&vault.to_string_lossy()),
        ps_quote(&claude.to_string_lossy()),
        ps_quote(&onboarding_prompt(guide))
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
    let home = sys::home();
    let Some(vault) = detect::vault(&home) else {
        return Err("Ton cerveau n'est pas encore installé.".into());
    };
    let Some(app) = obsidian_program(&home) else {
        return Err("Obsidian n'est pas encore installé.".into());
    };
    let vault = vault.to_string_lossy();
    let by_uri = format!("obsidian://open?path={}", percent_encode(&vault));
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
    log(format!("Je télécharge {what}…"));
    let args = curl_download_args(url, dest);
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
                return Err(format!("Le téléchargement de {what} s'est interrompu. Réessaie."))
            }
        }
    };
    if !out.ok() {
        log(out.stderr.trim().to_string());
        return Err(format!("Je n'ai pas pu télécharger {what}. Vérifie ta connexion internet, puis réessaie."));
    }
    Ok(())
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

fn progress(got: u64, size: Option<u64>) -> String {
    let mb = got / 1_000_000;
    match size {
        Some(total) if total > 0 => {
            format!("Téléchargement : {} % ({mb} Mo sur {})", (got * 100 / total).min(100), total / 1_000_000)
        }
        _ => format!("Téléchargement : {mb} Mo"),
    }
}

/// Downloads an official installer script and runs it with bash (PowerShell on Windows).
fn run_installer(url: &str, name: &str, args: &[&str], log: &mut dyn FnMut(String)) -> Result<(), String> {
    let root = sys::work_root().map_err(|e| format!("Je n'ai pas pu créer mon dossier de travail ({e})."))?;
    let tmp = TempDir::new_in(&root)?;
    let script = tmp.path().join(if cfg!(windows) { "install.ps1" } else { "install.sh" });
    download(url, &script, &format!("l'installeur de {name}"), None, Duration::from_secs(5 * 60), log)?;
    log(format!("J'installe {name}…"));
    #[cfg(not(windows))]
    let code = {
        let script = script.to_string_lossy();
        let argv: Vec<&str> = std::iter::once(script.as_ref()).chain(args.iter().copied()).collect();
        sys::run_streaming(Path::new("/bin/bash"), &argv, &[], Some(tmp.path()), INSTALLER_LIMIT, log)
    };
    // Its arguments are never needed on Windows (Bun comes with install.ps1).
    #[cfg(windows)]
    let code = {
        debug_assert!(args.is_empty());
        run_ps1(&script, &[], tmp.path(), INSTALLER_LIMIT, log)
    };
    match code {
        0 => Ok(()),
        code => Err(format!(
            "L'installeur de {name} s'est arrêté (code {code}). Vérifie ta connexion internet, puis réessaie."
        )),
    }
}

/// A private folder (0700) removed with all its content when dropped.
struct TempDir(PathBuf);

impl TempDir {
    fn new_in(parent: &Path) -> Result<Self, String> {
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

    fn path(&self) -> &Path {
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
        ] {
            assert!(link_allowed(ok), "{ok} should open");
        }
        for bad in [
            "http://claude.ai/",
            "https://claude.ai.evil.com/",
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
        fs::write(&script, onboarding_script(&path, &vault, &guide, &bin.join("claude"))).unwrap();
        let o = bash(&script);
        assert!(o.ok(), "{}", o.stderr);
        assert!(o.stdout.contains("Tu peux fermer cette fenêtre."));
        assert_eq!(
            fs::read_to_string(&out).unwrap(),
            format!("{}\nRead {} and run it to personalize me.\n", vault.display(), guide.display())
        );

        let script = root.join("claude-login.command");
        fs::write(&script, login_script(&path, &bin.join("claude"))).unwrap();
        assert!(bash(&script).ok());
        let seen = fs::read_to_string(&out).unwrap();
        assert_eq!(seen.lines().skip(1).collect::<Vec<_>>(), ["auth", "login"]);
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
        let all = detect::Brain { vault: true, hooks: true, skills: true, memory: true };
        let home = Path::new("/Users/jo");
        assert!(missing_parts(all, home).is_empty());
        assert_eq!(missing_parts(detect::Brain { hooks: false, ..all }, home), ["les automatismes de Claude"]);
        assert_eq!(missing_parts(detect::Brain { vault: false, hooks: false, skills: false, memory: false }, home).len(), 4);
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
        assert_eq!(titles.len(), SECTIONS.len(), "{titles:?}");
        let mut sections = Sections::default();
        for (i, title) in titles.iter().enumerate() {
            let said = sections.progress(&format!("=== {title} ===")).unwrap_or_else(|| panic!("{title}"));
            assert!(said.starts_with("▸ ") && said.ends_with(&format!("({} sur {})…", i + 1, SECTIONS.len())), "{said}");
        }
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
        let vault = Path::new(r"C:\Users\Jo O'Brien\Documents\Brain");
        let guide = Path::new(r"C:\Users\Jo O'Brien\.brain-in-a-box\src\onboarding\ONBOARDING.md");
        let onboarding = onboarding_script(&path, vault, guide, claude);
        assert!(onboarding.contains(r"Set-Location -LiteralPath 'C:\Users\Jo O''Brien\Documents\Brain'"), "{onboarding}");
        assert!(onboarding.contains(r"run it to personalize me.'"), "{onboarding}");
        assert!(same_folder(r"C:\Users\jo\Documents\Brain\", "c:/users/JO/Documents/Brain"));
    }
}
