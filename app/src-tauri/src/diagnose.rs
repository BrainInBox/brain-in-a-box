//! Looks at the Mac: what is there, what is missing, what blocks the install.
//!
//! `probe()` runs the system commands and gathers raw facts; `decide()` turns
//! them, plus the files under a given home, into checks. All the logic sits in
//! `decide()` and the `check_*` functions, so tests drive them with a fake home.

use std::path::Path;
use std::thread;
use std::time::Duration;

use crate::detect::{self, fmt_version, parse_version, Brain, Found, Version};
use crate::model::{Check, Diagnosis, Status};
use crate::{steps, sys};

const MIN_MACOS: Version = (13, 0, 0);
const DISK_BLOCKING_GB: f64 = 3.0;
const DISK_TIGHT_GB: f64 = 10.0;
const PROBE_TIMEOUT: Duration = Duration::from_secs(10);

/// One line of the checklist: id, what the user reads, and the install step that fixes it.
#[derive(Clone, Copy)]
struct Item(&'static str, &'static str, Option<&'static str>);

const MACOS: Item = Item("macos", "Version de macOS", None);
const DISK: Item = Item("disk", "Espace disque", None);
const NETWORK: Item = Item("network", "Connexion internet", None);
const CLT: Item = Item("clt", "Outils Apple (git, python3)", Some("clt"));
const BUN: Item = Item("bun", "Bun (le moteur de la mémoire)", Some("bun"));
const CLAUDE: Item = Item("claude", "Claude Code", Some("claude"));
/// No fix step: the window offers a login button and the user logs in to the official CLI themselves.
const CLAUDE_AUTH: Item = Item("claude_auth", "Connexion à Claude", None);
const OBSIDIAN: Item = Item("obsidian", "Obsidian (pour lire tes notes)", Some("obsidian"));
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
        }
    }
}

/// Raw results of the system probes, before any decision.
#[derive(Debug)]
struct Facts {
    /// `sw_vers -productVersion`, e.g. "26.3.1".
    macos: Option<String>,
    arch: String,
    ram_bytes: Option<u64>,
    disk_free_kb: Option<u64>,
    github: bool,
    claude_ai: bool,
    clt: bool,
    bun: Found,
    claude: Found,
    claude_logged_in: bool,
    obsidian: Found,
}

pub fn run() -> Diagnosis {
    let home = sys::home();
    decide(&home, &probe(&home))
}

/// What the window gets when the diagnosis itself broke: one red line, nothing to install.
pub fn failed(why: &str) -> Diagnosis {
    Diagnosis {
        os: "macOS".into(),
        arch: String::new(),
        ram_gb: 0.0,
        disk_free_gb: 0.0,
        online: false,
        checks: vec![INTERNAL.is(
            Status::Error,
            format!("Je n'ai pas pu finir l'examen de ton Mac ({why}). Clique sur Revérifier."),
        )],
        todo: Vec::new(),
        can_install: false,
    }
}

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

fn command_text(program: &str, args: &[&str]) -> Option<String> {
    sys::output_text(Path::new(program), args, PROBE_TIMEOUT)
}

/// Any HTTP status counts: claude.ai answers 403 to curl, which still proves the network works.
fn answers(url: &str) -> bool {
    let args = ["-sS", "-o", "/dev/null", "-m", "8", "-w", "%{http_code}", url];
    http_answered(&sys::run(Path::new("/usr/bin/curl"), &args, PROBE_TIMEOUT).stdout)
}

/// curl prints "000" when nothing answered.
fn http_answered(code: &str) -> bool {
    code.trim().parse::<u16>().is_ok_and(|c| c > 0)
}

fn decide(home: &Path, f: &Facts) -> Diagnosis {
    let disk_free_gb = f.disk_free_kb.map(kb_to_gb);
    let mut checks = vec![
        check_macos(f.macos.as_deref()),
        check_disk(disk_free_gb),
        check_network(f.github, f.claude_ai),
        check_clt(f.clt),
        check_bun(&f.bun),
        check_claude(&f.claude),
        check_claude_auth(f.claude.answers(), f.claude_logged_in),
        check_obsidian(&f.obsidian),
    ];
    checks.extend(check_brain(detect::brain(home)));
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

    Diagnosis {
        os: f.macos.as_ref().map_or("macOS".into(), |v| format!("macOS {v}")),
        arch: f.arch.clone(),
        // Apple counts RAM in binary gigabytes: "24 Go" is 24 GiB.
        ram_gb: f.ram_bytes.map_or(0.0, |b| round1(b as f64 / (1u64 << 30) as f64)),
        // Rounded down like the detail text, so both read "2,9" when 3 are needed.
        disk_free_gb: disk_free_gb.map_or(0.0, floor1),
        online: f.github && f.claude_ai,
        checks,
        todo,
        can_install,
    }
}

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
        (true, false) => CLAUDE_AUTH.is(Status::Warn, "Pas encore connecté. Tu le feras en un clic à la fin."),
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

fn check_brain(brain: Brain) -> [Check; 4] {
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
            "Il est dans Documents/Brain.",
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
            macos: Some("26.3.1".into()),
            arch: "arm64".into(),
            ram_bytes: Some(25_769_803_776),
            disk_free_kb: Some(169_941_844),
            github: true,
            claude_ai: true,
            clt: true,
            bun: Found::Version("1.4.2".into()),
            claude: Found::Version("2.1.296 (Claude Code)".into()),
            claude_logged_in: true,
            obsidian: Found::Version("1.14.4".into()),
        }
    }

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

    #[test]
    fn reads_free_space_from_df() {
        let mac = "Filesystem   1024-blocks      Used Available Capacity iused      ifree %iused  Mounted on\n\
                   /dev/disk3s5   482797652 271864108 169941844    62% 5700672 1699418440    0%   /System/Volumes/Data\n";
        assert_eq!(parse_df_available_kb(mac), Some(169_941_844));
        let spaced = "Filesystem 1024-blocks Used Available Capacity Mounted on\n\
                      map auto_home 0 0 0 100% /System/Volumes/Data/home\n";
        assert_eq!(parse_df_available_kb(spaced), Some(0));
        assert_eq!(parse_df_available_kb("df: /nope: No such file or directory"), None);
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
        assert_eq!((old.status, old.fix.as_deref()), (Status::Missing, Some("bun")));
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
        for c in check_brain(none) {
            assert_eq!((c.status, c.fix.as_deref()), (Status::Missing, Some("brain")), "{}", c.id);
        }
        let all = Brain { vault: true, hooks: true, skills: true, memory: true };
        for c in check_brain(all) {
            assert_eq!(c.status, Status::Ok, "{}", c.id);
        }
        let [_, hooks, _, _] = check_brain(Brain { hooks: false, ..all });
        assert_eq!((hooks.id.as_str(), hooks.status), ("brain_hooks", Status::Missing));
    }

    #[test]
    fn voyage_says_on_or_off_in_plain_words() {
        assert_eq!(check_voyage(Some("voyage:voyage-4")).detail, "Activée (Voyage AI)");
        assert_eq!(check_voyage(Some("zeroentropy:zembed-1")).detail, "Activée");
        let off = check_voyage(None);
        assert_eq!((off.status, off.fix), (Status::Warn, None));
    }

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
        assert_eq!(d.todo, ["clt", "bun", "claude", "brain"]);
        assert!(d.can_install);
        // Warnings never land in the todo list.
        assert_eq!(by_id(&d, "claude_auth").status, Status::Warn);
        assert_eq!(by_id(&d, "voyage").status, Status::Warn);
    }

    #[test]
    fn an_error_blocks_the_install() {
        let tmp = tempfile::tempdir().unwrap();
        fake_brain(tmp.path());
        let old_mac = Facts {
            macos: Some("12.7.6".into()),
            ..healthy_facts()
        };
        assert!(!decide(tmp.path(), &old_mac).can_install);
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
        assert_eq!(
            (absent.status, absent.fix.as_deref()),
            (Status::Missing, Some("obsidian"))
        );
    }
}
