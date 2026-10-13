//! What the app does when a plan from Claude names a remedy: a closed list, each
//! condition checked again right before it acts. Nothing is deleted: a broken
//! install is set aside in ~/.brain-in-a-box/mis-de-cote/<plan>/ with a manifest,
//! and « Remettre comme avant » renames it back.

use std::fs;
use std::io;
use std::os::unix::fs::{DirBuilderExt, MetadataExt};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

use crate::detect::{self, Found};
use crate::steps::{self, write_atomic};

pub const SET_ASIDE_DIR: &str = ".brain-in-a-box/mis-de-cote";
const MANIFEST: &str = "manifest.json";
/// EXDEV: not on the same disk. A copy instead of a rename is never tried.
const CROSS_DEVICE: i32 = 18;

/// The only folders a remedy may set aside: (remedy, step, path from home). None is
/// the user's data (Documents/Brain, ~/.gbrain, ~/.claude, the shell files, the
/// LaunchAgents, Obsidian's vault list).
const TARGETS: &[(&str, &str, &str)] = &[
    ("mettre_de_cote_bun", "bun", ".bun"),
    ("mettre_de_cote_obsidian", "obsidian", "Applications/Obsidian.app"),
    ("mettre_de_cote_sources", "brain", ".brain-in-a-box/src"),
    ("mettre_de_cote_gbrain", "brain", "DEV/gbrain"),
];
/// What says an install works: the real probes in the app, fakes in tests.
pub struct Probes {
    pub bun_ok: fn(&Path) -> bool,
    pub obsidian_installed: fn(&Path) -> bool,
}

fn real_bun_ok(home: &Path) -> bool {
    detect::bun_ok(&detect::bun(home))
}

fn real_obsidian_installed(home: &Path) -> bool {
    detect::obsidian(home) != Found::Absent
}

pub const REAL_PROBES: Probes = Probes { bun_ok: real_bun_ok, obsidian_installed: real_obsidian_installed };

pub struct Ctx<'a> {
    pub home: &'a Path,
    /// The user's uid: only what they own may be moved.
    pub me: u32,
    pub probes: &'a Probes,
    /// Where the steps leave their work folders (see steps::TempDir).
    pub work_dirs: &'a [PathBuf],
    /// This plan's folder under SET_ASIDE_DIR.
    pub aside: &'a Path,
}

#[derive(Debug, PartialEq, Eq)]
pub enum Done {
    Nothing,
    /// How many work folders went.
    Cleaned(usize),
    /// Where the folder now is.
    SetAside(PathBuf),
    /// The step runs again without the Voyage key.
    WithoutKey,
}

/// One folder set aside, as manifest.json records it.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
pub struct Moved {
    pub remede: String,
    pub from: PathBuf,
    pub to: PathBuf,
    pub uid: u32,
    pub ts: u64,
    #[serde(default)]
    pub undone: bool,
    /// What the relaunch had put in its place, set aside in turn by the undo.
    #[serde(default)]
    pub apres: Option<PathBuf>,
}

/// Applies one remedy of a plan for `step`, its condition checked first.
/// `key_given`: the step ran with a Voyage key.
pub fn apply(id: &str, step: &str, ctx: &Ctx, key_given: bool) -> Result<Done, String> {
    match id {
        "relancer" => Ok(Done::Nothing),
        "nettoyer_dossiers_travail" => {
            Ok(Done::Cleaned(ctx.work_dirs.iter().map(|d| steps::remove_stale_copies(d, ctx.me)).sum()))
        }
        "relancer_sans_cle_voyage" if step == "brain" => {
            if key_given {
                Ok(Done::WithoutKey)
            } else {
                Err("aucune clé Voyage n'avait été donnée".into())
            }
        }
        _ => {
            let Some(&(_, owner, rel)) = TARGETS.iter().find(|t| t.0 == id) else { return Err("remède inconnu".into()) };
            if owner != step {
                return Err("ce remède n'est pas pour cette étape".into());
            }
            let from = may_set_aside(id, rel, ctx)?;
            set_aside(id, &from, ctx).map(Done::SetAside)
        }
    }
}

/// A real folder of the user's, where it should be, and not a working install.
fn may_set_aside(id: &str, rel: &str, ctx: &Ctx) -> Result<PathBuf, String> {
    let path = ctx.home.join(rel);
    let meta = fs::symlink_metadata(&path).map_err(|_| format!("~/{rel} n'existe pas"))?;
    if meta.file_type().is_symlink() {
        return Err(format!("~/{rel} est un lien : je n'y touche pas"));
    }
    if !meta.is_dir() {
        return Err(format!("~/{rel} n'est pas un dossier"));
    }
    if meta.uid() != ctx.me {
        return Err(format!("~/{rel} appartient à un autre compte du Mac"));
    }
    if !in_place(ctx.home, rel) {
        return Err(format!("le chemin de ~/{rel} passe par un lien"));
    }
    match id {
        "mettre_de_cote_bun" if (ctx.probes.bun_ok)(ctx.home) => Err("Bun marche : je n'y touche pas".into()),
        "mettre_de_cote_obsidian" if (ctx.probes.obsidian_installed)(ctx.home) => {
            Err("Obsidian marche : je n'y touche pas".into())
        }
        "mettre_de_cote_gbrain" if !ours(&path, ctx.me) => {
            Err("~/DEV/gbrain n'a pas été installé par Brain in a Box : je n'y touche pas".into())
        }
        _ => Ok(path),
    }
}

/// No link on the way from the home folder: the folder is really there.
fn in_place(home: &Path, rel: &str) -> bool {
    let (Ok(real_home), Some(parent)) = (fs::canonicalize(home), Path::new(rel).parent()) else { return false };
    fs::canonicalize(home.join(parent)).is_ok_and(|p| p == real_home.join(parent))
}

/// install.sh marks the gbrain clone it made; a clone without the mark is the user's.
fn ours(gbrain: &Path, me: u32) -> bool {
    fs::symlink_metadata(gbrain.join(".biab-owned")).is_ok_and(|m| m.is_file() && m.uid() == me)
}

fn set_aside(id: &str, from: &Path, ctx: &Ctx) -> Result<PathBuf, String> {
    prepare(ctx.aside, ctx.me).map_err(|e| format!("je n'ai pas pu préparer le dossier mis-de-cote ({e})"))?;
    let to = ctx.aside.join(id);
    if fs::symlink_metadata(&to).is_ok() {
        return Err("déjà mis de côté pour ce plan".into());
    }
    // Written before the move: if the app stops in between, the undo still knows.
    let mut moves = read_manifest(ctx.aside)?;
    let ts = SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_secs());
    moves.push(Moved { remede: id.into(), from: from.into(), to: to.clone(), uid: ctx.me, ts, undone: false, apres: None });
    write_manifest(ctx.aside, &moves)?;
    if let Err(e) = fs::rename(from, &to) {
        moves.pop();
        let _ = write_manifest(ctx.aside, &moves);
        return Err(if e.raw_os_error() == Some(CROSS_DEVICE) {
            "il est sur un autre disque : je ne le copie pas".into()
        } else {
            format!("je n'ai pas pu le déplacer ({e})")
        });
    }
    Ok(to)
}

/// mis-de-cote and the plan's folder in it: real private folders of the user's.
fn prepare(aside: &Path, me: u32) -> io::Result<()> {
    let base = aside.parent().ok_or_else(|| io::Error::other("chemin inattendu"))?;
    fs::DirBuilder::new().recursive(true).mode(0o700).create(aside)?;
    for dir in [base, aside] {
        let m = fs::symlink_metadata(dir)?;
        if !m.is_dir() || m.uid() != me {
            return Err(io::Error::other(format!("{} n'est pas un vrai dossier à toi", dir.display())));
        }
    }
    Ok(())
}

fn read_manifest(aside: &Path) -> Result<Vec<Moved>, String> {
    match fs::read_to_string(aside.join(MANIFEST)) {
        Ok(text) => serde_json::from_str(&text).map_err(|e| format!("manifeste illisible ({e})")),
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(Vec::new()),
        Err(e) => Err(format!("manifeste illisible ({e})")),
    }
}

fn write_manifest(aside: &Path, moves: &[Moved]) -> Result<(), String> {
    let text = serde_json::to_string_pretty(moves).map_err(|e| e.to_string())?;
    write_atomic(&aside.join(MANIFEST), text.as_bytes(), 0o600).map_err(|e| format!("manifeste non écrit ({e})"))
}

/// Renames back, last first, what this plan set aside. What the relaunch put in its
/// place is set aside in turn ("<remède>.apres"): nothing is deleted. Returns the
/// paths put back, "~/.bun".
pub fn undo(home: &Path, me: u32, aside: &Path) -> Result<Vec<String>, String> {
    let mut moves = read_manifest(aside)?;
    let mut back = Vec::new();
    for i in (0..moves.len()).rev() {
        if moves[i].undone {
            continue;
        }
        let m = moves[i].clone();
        // Only what the manifest may name: our targets, from this plan's folder.
        let rel = TARGETS.iter().find(|t| t.0 == m.remede).map(|t| t.2).ok_or("manifeste inattendu")?;
        if m.from != home.join(rel) || m.to != aside.join(&m.remede) || !in_place(home, rel) {
            return Err("manifeste inattendu".into());
        }
        let kept = fs::symlink_metadata(&m.to).map_err(|_| format!("~/{rel} n'est plus dans mis-de-cote"))?;
        if kept.file_type().is_symlink() || kept.uid() != me {
            return Err(format!("ce qui est mis de côté pour ~/{rel} a changé"));
        }
        let mut apres = None;
        if fs::symlink_metadata(&m.from).is_ok() {
            let dest = free_name(aside, &format!("{}.apres", m.remede));
            fs::rename(&m.from, &dest).map_err(|e| format!("je n'ai pas pu déplacer le nouveau ~/{rel} ({e})"))?;
            apres = Some(dest);
        }
        if let Err(e) = fs::rename(&m.to, &m.from) {
            if let Some(a) = &apres {
                let _ = fs::rename(a, &m.from);
            }
            return Err(format!("je n'ai pas pu remettre ~/{rel} ({e})"));
        }
        moves[i].undone = true;
        moves[i].apres = apres;
        write_manifest(aside, &moves)?;
        back.push(format!("~/{rel}"));
    }
    Ok(back)
}

fn free_name(dir: &Path, name: &str) -> PathBuf {
    let mut p = dir.join(name);
    let mut n = 2;
    while fs::symlink_metadata(&p).is_ok() {
        p = dir.join(format!("{name}-{n}"));
        n += 1;
    }
    p
}

/// Something of this plan is still set aside.
pub fn pending(aside: &Path) -> bool {
    read_manifest(aside).is_ok_and(|m| m.iter().any(|m| !m.undone))
}

/// Every folder still set aside is there, with its manifest.
pub fn intact(aside: &Path) -> bool {
    read_manifest(aside).is_ok_and(|m| m.iter().filter(|m| !m.undone).all(|m| fs::symlink_metadata(&m.to).is_ok()))
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use std::os::unix::fs::{symlink, PermissionsExt};

    /// The remedies that need nothing on disk.
    const OTHERS: &[&str] = &["relancer", "nettoyer_dossiers_travail", "relancer_sans_cle_voyage"];

    /// Whether `apply` knows this remedy.
    pub(crate) fn known(id: &str) -> bool {
        OTHERS.contains(&id) || TARGETS.iter().any(|t| t.0 == id)
    }

    fn works(_: &Path) -> bool {
        true
    }

    fn broken(_: &Path) -> bool {
        false
    }

    const BROKEN: Probes = Probes { bun_ok: broken, obsidian_installed: broken };
    const WORKING: Probes = Probes { bun_ok: works, obsidian_installed: works };

    struct Home {
        _tmp: tempfile::TempDir,
        root: PathBuf,
        home: PathBuf,
        aside: PathBuf,
        me: u32,
        work: Vec<PathBuf>,
    }

    impl Home {
        fn new() -> Self {
            let tmp = tempfile::tempdir().unwrap();
            let root = fs::canonicalize(tmp.path()).unwrap();
            let home = root.join("home");
            fs::create_dir_all(&home).unwrap();
            let me = fs::metadata(&home).unwrap().uid();
            let aside = home.join(SET_ASIDE_DIR).join("20261013-184455-bun");
            let work = vec![root.join("tmp"), home.join("Applications"), home.join(".brain-in-a-box")];
            Home { _tmp: tmp, root, home, aside, me, work }
        }

        fn ctx<'a>(&'a self, probes: &'a Probes) -> Ctx<'a> {
            Ctx { home: &self.home, me: self.me, probes, work_dirs: &self.work, aside: &self.aside }
        }

        fn tree(&self, rel: &str) {
            let p = self.home.join(rel);
            fs::create_dir_all(p.join("bin")).unwrap();
            fs::write(p.join("bin/tool"), format!("{rel} v1")).unwrap();
            fs::set_permissions(p.join("bin/tool"), fs::Permissions::from_mode(0o751)).unwrap();
        }
    }

    /// Paths, kinds, modes and bytes of everything under `dir`.
    fn listing(dir: &Path) -> Vec<(String, u32, Vec<u8>)> {
        let mut out = Vec::new();
        let mut stack = vec![dir.to_path_buf()];
        while let Some(d) = stack.pop() {
            for e in fs::read_dir(&d).unwrap().filter_map(Result::ok) {
                let m = fs::symlink_metadata(e.path()).unwrap();
                let rel = e.path().strip_prefix(dir).unwrap().to_string_lossy().into_owned();
                let bytes = if m.is_file() { fs::read(e.path()).unwrap() } else { Vec::new() };
                out.push((rel, m.permissions().mode(), bytes));
                if m.is_dir() {
                    stack.push(e.path());
                }
            }
        }
        out.sort();
        out
    }

    #[test]
    fn a_broken_bun_is_set_aside_and_comes_back_as_it_was() {
        let h = Home::new();
        h.tree(".bun");
        let before = listing(&h.home.join(".bun"));
        let done = apply("mettre_de_cote_bun", "bun", &h.ctx(&BROKEN), false).unwrap();
        let to = h.aside.join("mettre_de_cote_bun");
        assert_eq!(done, Done::SetAside(to.clone()));
        assert!(!h.home.join(".bun").exists());
        assert_eq!(listing(&to), before);
        let manifest: Vec<Moved> = serde_json::from_str(&fs::read_to_string(h.aside.join(MANIFEST)).unwrap()).unwrap();
        assert_eq!((manifest[0].from.clone(), manifest[0].to.clone(), manifest[0].uid), (h.home.join(".bun"), to.clone(), h.me));
        assert_eq!(fs::metadata(h.aside.join(MANIFEST)).unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(fs::metadata(&h.aside).unwrap().permissions().mode() & 0o777, 0o700);
        assert!(pending(&h.aside) && intact(&h.aside));

        // The relaunch put a new one in its place: it is set aside in turn.
        h.tree(".bun");
        fs::write(h.home.join(".bun/bin/tool"), "new").unwrap();
        assert_eq!(undo(&h.home, h.me, &h.aside).unwrap(), ["~/.bun"]);
        assert_eq!(listing(&h.home.join(".bun")), before);
        assert_eq!(fs::read(h.aside.join("mettre_de_cote_bun.apres/bin/tool")).unwrap(), b"new");
        assert!(!pending(&h.aside));
        assert!(undo(&h.home, h.me, &h.aside).unwrap().is_empty(), "a second undo has nothing to do");
    }

    #[test]
    fn nothing_moves_that_is_not_a_broken_install_of_ours() {
        let h = Home::new();
        let refuse = |id: &str, step: &str, probes: &Probes, why: &str| {
            let err = apply(id, step, &h.ctx(probes), false).unwrap_err();
            assert!(err.contains(why), "{id}: {err}");
        };
        refuse("mettre_de_cote_bun", "bun", &BROKEN, "n'existe pas");
        h.tree(".bun");
        refuse("mettre_de_cote_bun", "bun", &WORKING, "Bun marche");
        refuse("mettre_de_cote_bun", "brain", &BROKEN, "pas pour cette étape");
        refuse("mettre_de_cote_documents", "brain", &BROKEN, "remède inconnu");
        let other_uid = Ctx { me: h.me.wrapping_add(1), ..h.ctx(&BROKEN) };
        assert!(apply("mettre_de_cote_bun", "bun", &other_uid, false).unwrap_err().contains("autre compte"));

        // A link, even to a real broken install, is never followed.
        let elsewhere = h.root.join("elsewhere");
        fs::create_dir_all(elsewhere.join("Obsidian.app")).unwrap();
        fs::create_dir_all(h.home.join("Applications")).unwrap();
        symlink(elsewhere.join("Obsidian.app"), h.home.join("Applications/Obsidian.app")).unwrap();
        refuse("mettre_de_cote_obsidian", "obsidian", &BROKEN, "est un lien");
        fs::remove_file(h.home.join("Applications/Obsidian.app")).unwrap();
        h.tree("Applications/Obsidian.app");
        refuse("mettre_de_cote_obsidian", "obsidian", &WORKING, "Obsidian marche");
        // ~/Applications itself a link: the path does not lead where it says.
        fs::rename(h.home.join("Applications"), h.root.join("apps")).unwrap();
        symlink(h.root.join("apps"), h.home.join("Applications")).unwrap();
        refuse("mettre_de_cote_obsidian", "obsidian", &BROKEN, "passe par un lien");

        // The user's own gbrain clone: no mark, or a mark that is a link.
        h.tree("DEV/gbrain");
        refuse("mettre_de_cote_gbrain", "brain", &BROKEN, "pas été installé par Brain in a Box");
        symlink(h.root.join("x"), h.home.join("DEV/gbrain/.biab-owned")).unwrap();
        refuse("mettre_de_cote_gbrain", "brain", &BROKEN, "pas été installé par Brain in a Box");
        fs::remove_file(h.home.join("DEV/gbrain/.biab-owned")).unwrap();
        fs::write(h.home.join("DEV/gbrain/.biab-owned"), "").unwrap();
        assert!(matches!(apply("mettre_de_cote_gbrain", "brain", &h.ctx(&BROKEN), false), Ok(Done::SetAside(_))));

        fs::write(h.home.join(".brain-in-a-box/src"), "a file").unwrap();
        refuse("mettre_de_cote_sources", "brain", &BROKEN, "pas un dossier");
        assert!(h.home.join(".bun").is_dir() && elsewhere.join("Obsidian.app").is_dir());
    }

    #[test]
    fn the_same_target_is_set_aside_once_per_plan() {
        let h = Home::new();
        h.tree(".brain-in-a-box/src");
        assert!(apply("mettre_de_cote_sources", "brain", &h.ctx(&BROKEN), false).is_ok());
        h.tree(".brain-in-a-box/src");
        let err = apply("mettre_de_cote_sources", "brain", &h.ctx(&BROKEN), false).unwrap_err();
        assert!(err.contains("déjà mis de côté"), "{err}");
        assert_eq!(read_manifest(&h.aside).unwrap().len(), 1);
    }

    #[test]
    fn cleaning_takes_only_our_work_folders() {
        let h = Home::new();
        for dir in &h.work {
            fs::create_dir_all(dir.join(".biab-0123456789abcdef/x")).unwrap();
            fs::create_dir_all(dir.join(".biab-notours/x")).unwrap();
            fs::create_dir_all(dir.join("mine")).unwrap();
        }
        assert_eq!(apply("nettoyer_dossiers_travail", "brain", &h.ctx(&BROKEN), false), Ok(Done::Cleaned(3)));
        for dir in &h.work {
            assert!(!dir.join(".biab-0123456789abcdef").exists());
            assert!(dir.join(".biab-notours/x").exists() && dir.join("mine").exists());
        }
        let other = Ctx { me: h.me.wrapping_add(1), ..h.ctx(&BROKEN) };
        fs::create_dir_all(h.work[0].join(".biab-fedcba9876543210")).unwrap();
        assert_eq!(apply("nettoyer_dossiers_travail", "bun", &other, false), Ok(Done::Cleaned(0)));
    }

    #[test]
    fn the_key_is_dropped_only_when_there_was_one() {
        let h = Home::new();
        assert_eq!(apply("relancer_sans_cle_voyage", "brain", &h.ctx(&BROKEN), true), Ok(Done::WithoutKey));
        assert!(apply("relancer_sans_cle_voyage", "brain", &h.ctx(&BROKEN), false).is_err());
        assert!(apply("relancer_sans_cle_voyage", "bun", &h.ctx(&BROKEN), true).is_err());
        assert_eq!(apply("relancer", "clt", &h.ctx(&BROKEN), false), Ok(Done::Nothing));
    }

    #[test]
    fn an_undo_only_moves_what_the_manifest_may_name() {
        let h = Home::new();
        h.tree(".bun");
        apply("mettre_de_cote_bun", "bun", &h.ctx(&BROKEN), false).unwrap();
        let good = read_manifest(&h.aside).unwrap();
        let precious = h.root.join("precious");
        fs::create_dir_all(&precious).unwrap();
        for tamper in [
            Moved { from: h.home.join("Documents/Brain"), ..good[0].clone() },
            Moved { to: precious.clone(), ..good[0].clone() },
            Moved { remede: "mettre_de_cote_documents".into(), ..good[0].clone() },
        ] {
            write_manifest(&h.aside, &[tamper]).unwrap();
            assert!(undo(&h.home, h.me, &h.aside).is_err());
        }
        assert!(precious.is_dir() && !h.home.join(".bun").exists());
        write_manifest(&h.aside, &good).unwrap();
        assert!(undo(&h.home, h.me.wrapping_add(1), &h.aside).is_err(), "kept by someone else");
        assert_eq!(undo(&h.home, h.me, &h.aside).unwrap(), ["~/.bun"]);
    }

    #[test]
    fn the_catalogue_never_targets_the_users_data() {
        const NEVER: &[&str] = &["Documents", ".gbrain", ".claude", ".claude.json", "Library", ".zshrc", ".bashrc", ".profile"];
        for (id, _, rel) in TARGETS {
            assert!(known(id));
            assert!(!NEVER.iter().any(|n| Path::new(rel).starts_with(n)), "{rel}");
        }
        assert!(OTHERS.iter().all(|id| known(id)) && !known("formater"));
    }
}
