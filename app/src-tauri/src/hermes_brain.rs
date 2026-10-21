//! Hermes linked to the brain (docs/proposals/hermes.md, « Lien avec le brain »): one entry of
//! Hermes's own memory says where the brain is and how to read it. Hermes puts its memory in its
//! system prompt at the start of every conversation, so it knows where to look when asked
//! « qu'est-ce que tu sais de moi ? », and the facts stay in the brain, the one place they live.
//!
//! The memory, because Hermes's Claude plugin runs Claude without the user's settings, MCP
//! servers or CLAUDE.md: it is what every conversation reads, whichever AI Hermes goes through.
//!
//! Written the way Hermes's memory tool writes (tools/memory_tool_store.py): entries split by
//! "\n§\n", the same lock (MEMORY.md.lock, flock), an atomic replace that keeps the file's mode,
//! the memory's character limit kept. Only this entry is ever added or replaced.

use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::{detect, hermes, sys};

/// Hermes's separator between two memory entries (ENTRY_DELIMITER).
const DELIMITER: &str = "\n§\n";
/// How the entry starts: the app finds its own entry by it, and touches no other.
const MARK: &str = "Cerveau (Brain in a Box)";
/// Hermes's own memory_char_limit, when its config does not say.
const LIMIT: usize = 2200;

const NOT_INSTALLED: &str = "Hermes ne répond pas sur ton ordinateur : je ne peux pas encore le relier à ton cerveau. Réessaie dans un moment.";
const NO_BRAIN: &str = "Ton cerveau n'est pas encore installé : je relierai Hermes à lui juste après.";
const NO_MEMORY: &str = "Je n'ai pas trouvé où Hermes garde sa mémoire. Réessaie dans un moment.";
const FULL: &str = "La mémoire d'Hermes est pleine : il n'y a plus la place d'y noter où est ton cerveau. Demande à Hermes de faire le tri dans sa mémoire, puis réessaie.";
const NOT_WRITTEN: &str = "Je n'ai pas pu écrire dans la mémoire d'Hermes. Réessaie dans un moment.";
const MEMORY_OFF: &str = "La mémoire d'Hermes est éteinte : je n'y touche pas. Il lira ton cerveau quand tu la rallumeras.";
/// The step's result, shown at the end: what changed for the user, then what to try.
pub const LINKED: &str = "Hermes sait maintenant où lire ton cerveau. Demande-lui : « Qu'est-ce que tu sais de moi ? »";

/// The entry: where the brain is, which notes say who the user is, how to find the others.
pub fn entry(brain: &Path) -> String {
    format!(
        "{MARK} : les notes de la personne sont dans {}. Quand elle te demande ce que tu sais d'elle, ou te parle \
         de sa vie, de son travail ou de ses projets, lis d'abord Profile/about.md, Profile/business.md, \
         Profile/memory.md et Profile/soul.md dans ce dossier. Pour retrouver une autre note : gbq query \"les mots cherchés\".",
        brain.display()
    )
}

/// What putting `entry` in a memory holding `current` gives.
#[derive(Debug, PartialEq)]
pub enum Merge {
    /// The entry is there already, word for word.
    Same,
    /// The new text: the entry after the others, or in place of an older one of its own.
    Write(String),
    /// No room: the entries would pass the memory's limit.
    Full,
}

/// `entry` in `current`, MEMORY.md's text: the other entries are kept as they are, in order.
pub fn merge(current: &str, entry: &str, limit: usize) -> Merge {
    let mut entries: Vec<&str> = current.split(DELIMITER).map(|e| e.trim_matches('\n')).filter(|e| !e.trim().is_empty()).collect();
    match entries.iter().position(|e| e.trim_start().starts_with(MARK)) {
        Some(i) if entries[i].trim() == entry => return Merge::Same,
        Some(i) => entries[i] = entry,
        None => entries.push(entry),
    }
    // Counted as Hermes counts it: its entries stripped, joined by the separator.
    let size = entries.iter().map(|e| e.trim().chars().count()).sum::<usize>() + DELIMITER.chars().count() * (entries.len() - 1);
    if size > limit {
        return Merge::Full;
    }
    Merge::Write(entries.join(DELIMITER))
}

/// Hermes's memory holds the entry for `brain`, word for word.
pub fn holds(current: &str, brain: &Path) -> bool {
    merge(current, &entry(brain), usize::MAX) == Merge::Same
}

/// `hermes config get memory --json`, e.g. {"memory_enabled": true, "memory_char_limit": 2200, ...}:
/// whether Hermes reads its memory, and its limit. What it does not say is Hermes's default.
pub fn parse_settings(json: &str) -> (bool, usize) {
    let v = serde_json::from_str::<Value>(json.trim()).unwrap_or(Value::Null);
    let on = v.get("memory_enabled").and_then(Value::as_bool).unwrap_or(true);
    let limit = v.get("memory_char_limit").and_then(Value::as_u64).and_then(|n| usize::try_from(n).ok()).unwrap_or(LIMIT);
    (on, limit)
}

/// MEMORY.md, next to the config `hermes config path` prints: Hermes says where its home is,
/// profiles and $HERMES_HOME included, rather than the app guessing it.
pub fn parse_memory_file(config_path: &str) -> Option<PathBuf> {
    let config = PathBuf::from(config_path.lines().map(str::trim).rfind(|l| !l.is_empty())?);
    config.is_absolute().then(|| config.parent().map(|home| home.join("memories").join("MEMORY.md")))?
}

fn memory_file(bin: &Path) -> Option<PathBuf> {
    let out = sys::run(bin, &["config", "path"], hermes::TIMEOUT);
    out.ok().then(|| parse_memory_file(&out.stdout))?
}

/// Hermes's memory says where this user's brain is. Read only: for the diagnosis.
pub fn linked(bin: &Path, home: &Path) -> bool {
    let brain = detect::brain_dir(home);
    memory_file(bin).and_then(|f| fs::read_to_string(f).ok()).is_some_and(|text| holds(&text, &brain))
}

/// The install step: Hermes's memory gets the brain's entry, unless it holds it already.
pub fn step(log: &mut dyn FnMut(String)) -> Result<String, String> {
    let bin = hermes::bin().ok_or(NOT_INSTALLED)?;
    let home = sys::home();
    let brain = detect::brain_dir(&home);
    if !brain.is_dir() {
        return Err(NO_BRAIN.into());
    }
    log("$ hermes config get memory --json".into());
    let settings = sys::run(&bin, &["config", "get", "memory", "--json"], hermes::TIMEOUT);
    if !settings.ok() {
        return Err(NOT_INSTALLED.into());
    }
    let (on, limit) = parse_settings(&settings.stdout);
    if !on {
        log("La mémoire d'Hermes est éteinte : je la laisse comme elle est.".into());
        return Ok(MEMORY_OFF.into());
    }
    log("$ hermes config path".into());
    let file = memory_file(&bin).ok_or(NO_MEMORY)?;
    log(format!("Je note dans la mémoire d'Hermes où est ton cerveau : {}.", detect::brain_dir_shown(&home)));
    match put(&file, &entry(&brain), limit) {
        Ok(Merge::Same) => log("C'était déjà noté.".into()),
        Ok(Merge::Write(_)) => log("C'est noté.".into()),
        Ok(Merge::Full) => return Err(FULL.into()),
        Err(e) => {
            log(format!("{}: {e}", file.display()));
            return Err(NOT_WRITTEN.into());
        }
    }
    Ok(LINKED.into())
}

/// `entry` put in `file` under Hermes's lock: written only when it changes something.
fn put(file: &Path, entry: &str, limit: usize) -> io::Result<Merge> {
    let dir = file.parent().ok_or_else(|| io::Error::other("no folder"))?;
    make_dir(dir)?;
    let _lock = lock(file)?;
    let current = match fs::read_to_string(file) {
        Ok(text) => text,
        Err(e) if e.kind() == io::ErrorKind::NotFound => String::new(),
        Err(e) => return Err(e),
    };
    let merged = merge(&current, entry, limit);
    if let Merge::Write(text) = &merged {
        replace(file, text)?;
    }
    Ok(merged)
}

/// `text` in place of `file`, as Hermes's atomic_write_text does: a temporary file beside it,
/// with the file's mode, renamed over it. A reader sees the old text or the new, never half.
fn replace(file: &Path, text: &str) -> io::Result<()> {
    let mut name = file.file_name().unwrap_or_default().to_os_string();
    name.push(".bibo.tmp");
    let tmp = file.with_file_name(name);
    let written = (|| {
        let mut f = private_file(&tmp)?;
        f.write_all(text.as_bytes())?;
        f.sync_all()?;
        if let Ok(m) = fs::metadata(file) {
            fs::set_permissions(&tmp, m.permissions())?;
        }
        fs::rename(&tmp, file)
    })();
    if written.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    written
}

/// Owner only: the memory says who the user is.
#[cfg(unix)]
fn make_dir(dir: &Path) -> io::Result<()> {
    use std::os::unix::fs::DirBuilderExt;
    fs::DirBuilder::new().recursive(true).mode(0o700).create(dir)
}

#[cfg(not(unix))]
fn make_dir(dir: &Path) -> io::Result<()> {
    fs::create_dir_all(dir)
}

#[cfg(unix)]
fn private_file(path: &Path) -> io::Result<fs::File> {
    use std::os::unix::fs::OpenOptionsExt;
    fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(path)
}

#[cfg(not(unix))]
fn private_file(path: &Path) -> io::Result<fs::File> {
    fs::File::create(path)
}

/// Hermes's own lock on `file`: an exclusive flock on `file`.lock (fcntl.flock in its
/// _file_lock), owner only, held until the returned file is dropped. Hermes holds it for a
/// write's milliseconds: five seconds at most.
#[cfg(unix)]
fn lock(file: &Path) -> io::Result<fs::File> {
    use std::os::unix::fs::OpenOptionsExt;
    use std::os::unix::io::AsRawFd;
    let mut name = file.as_os_str().to_os_string();
    name.push(".lock");
    let f = fs::OpenOptions::new().read(true).write(true).create(true).truncate(false).mode(0o600).open(PathBuf::from(name))?;
    for _ in 0..50 {
        // SAFETY: flock on a descriptor `f` owns; closing it releases the lock.
        if unsafe { flock::flock(f.as_raw_fd(), flock::LOCK_EX | flock::LOCK_NB) } == 0 {
            return Ok(f);
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    Err(io::Error::new(io::ErrorKind::WouldBlock, "Hermes is writing its memory"))
}

/// flock(2), in the C library std links already: File::try_lock needs Rust 1.89, the app 1.85.
#[cfg(unix)]
mod flock {
    use std::os::raw::c_int;
    /// The same on macOS and Linux (sys/file.h).
    pub const LOCK_EX: c_int = 2;
    pub const LOCK_NB: c_int = 4;
    extern "C" {
        pub fn flock(fd: c_int, operation: c_int) -> c_int;
    }
}

/// Nothing plans the step where Hermes's step does not run (diagnose.rs): no lock to share.
#[cfg(not(unix))]
fn lock(_: &Path) -> io::Result<Option<fs::File>> {
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;

    const FIXTURES: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/hermes-cerveau");

    fn fixture(name: &str) -> String {
        fs::read_to_string(Path::new(FIXTURES).join(name)).unwrap()
    }

    /// Hermes's own reading of MEMORY.md (_read_file): split on the separator, stripped, no empty one.
    fn hermes_entries(text: &str) -> Vec<String> {
        text.split(DELIMITER).map(|e| e.trim().to_string()).filter(|e| !e.is_empty()).collect()
    }

    fn brain() -> PathBuf {
        PathBuf::from("/Users/someone/Documents/Brain")
    }

    #[test]
    fn the_entry_says_where_the_brain_is_and_how_to_read_it() {
        let e = entry(&brain());
        assert!(e.starts_with(MARK), "{e}");
        assert!(e.contains("/Users/someone/Documents/Brain."), "{e}");
        for read in ["Profile/about.md", "Profile/business.md", "Profile/memory.md", "Profile/soul.md", "gbq query"] {
            assert!(e.contains(read), "{read} missing: {e}");
        }
        assert!(e.contains("ce que tu sais d'elle"), "{e}");
        // One entry, small beside the memory's 2,200 characters: Hermes keeps room to learn.
        assert!(!e.contains('§') && !e.contains('\n'), "{e}");
        assert!(e.chars().count() < 500, "{} characters", e.chars().count());
    }

    #[test]
    fn the_entry_goes_after_hermes_own_entries_which_stay_as_they_are() {
        // Written by Hermes's memory tool itself (README).
        let current = fixture("MEMORY.md");
        let before = hermes_entries(&current);
        assert_eq!(before.len(), 2, "{current}");
        let e = entry(&brain());
        let Merge::Write(text) = merge(&current, &e, LIMIT) else { panic!("not written") };
        assert_eq!(hermes_entries(&text), [before.clone(), vec![e.clone()]].concat());
        assert!(text.starts_with(&current.trim_end_matches('\n').to_string()), "the others not touched:\n{text}");
        // Once there, nothing more to write.
        assert_eq!(merge(&text, &e, LIMIT), Merge::Same);
        assert!(holds(&text, &brain()) && !holds(&current, &brain()));
    }

    #[test]
    fn a_moved_brain_replaces_its_own_entry_and_no_other() {
        let current = fixture("MEMORY.md");
        let Merge::Write(old) = merge(&current, &entry(Path::new("/Users/someone/Brain")), LIMIT) else { panic!() };
        let mut last = old.clone();
        last.push_str(DELIMITER);
        last.push_str("Awa préfère les réponses courtes.");
        let e = entry(&brain());
        let Merge::Write(text) = merge(&last, &e, LIMIT) else { panic!("not replaced") };
        let mut want = hermes_entries(&last);
        want[2] = e.clone();
        assert_eq!(hermes_entries(&text), want, "same place, the others kept");
        assert!(!holds(&last, &brain()) && holds(&text, &brain()));
    }

    #[test]
    fn an_empty_memory_gets_the_entry_alone() {
        let e = entry(&brain());
        for empty in ["", "\n", "\n§\n"] {
            assert_eq!(merge(empty, &e, LIMIT), Merge::Write(e.clone()), "{empty:?}");
        }
    }

    #[test]
    fn the_memory_limit_is_kept() {
        let current = fixture("MEMORY.md");
        let e = entry(&brain());
        let Merge::Write(text) = merge(&current, &e, LIMIT) else { panic!() };
        let size = hermes_entries(&text).join(DELIMITER).chars().count();
        // Right at the limit it fits; one character less and it does not.
        assert_eq!(merge(&current, &e, size), Merge::Write(text));
        assert_eq!(merge(&current, &e, size - 1), Merge::Full);
        // Already there, it is not counted again.
        assert_eq!(merge(&format!("{current}{DELIMITER}{e}"), &e, 10), Merge::Same);
    }

    #[test]
    fn hermes_memory_settings() {
        assert_eq!(parse_settings(&fixture("config-get-memory.json")), (true, 2200));
        assert_eq!(parse_settings(r#"{"memory_enabled": false, "memory_char_limit": 3000}"#), (false, 3000));
        // What Hermes does not say is its default.
        assert_eq!(parse_settings(""), (true, LIMIT));
        assert_eq!(parse_settings("{}"), (true, LIMIT));
    }

    #[test]
    fn the_memory_is_beside_hermes_config() {
        assert_eq!(parse_memory_file(&fixture("config-path.txt")), Some(PathBuf::from("/Users/someone/.hermes/memories/MEMORY.md")));
        assert_eq!(
            parse_memory_file("/Users/someone/.hermes/profiles/travail/config.yaml\n"),
            Some(PathBuf::from("/Users/someone/.hermes/profiles/travail/memories/MEMORY.md"))
        );
        assert_eq!(parse_memory_file(""), None);
        assert_eq!(parse_memory_file("config.yaml"), None, "never a guess relative to the app");
    }

    #[cfg(unix)]
    #[test]
    fn written_in_place_with_its_mode_under_hermes_lock() {
        use std::os::unix::fs::PermissionsExt;
        let tmp = tempfile::tempdir().unwrap();
        let file = tmp.path().join("memories").join("MEMORY.md");
        let e = entry(&brain());
        // No memory yet: the folder and the file, owner only.
        assert!(matches!(put(&file, &e, LIMIT).unwrap(), Merge::Write(_)));
        assert_eq!(fs::read_to_string(&file).unwrap(), e);
        let mode = |p: &Path| fs::metadata(p).unwrap().permissions().mode() & 0o777;
        assert_eq!((mode(file.parent().unwrap()), mode(&file)), (0o700, 0o600));
        assert_eq!(mode(&tmp.path().join("memories").join("MEMORY.md.lock")), 0o600, "Hermes's lock file");

        // Hermes's own text and mode are kept; nothing left beside it.
        fs::write(&file, fixture("MEMORY.md")).unwrap();
        fs::set_permissions(&file, fs::Permissions::from_mode(0o640)).unwrap();
        assert!(matches!(put(&file, &e, LIMIT).unwrap(), Merge::Write(_)));
        assert_eq!(mode(&file), 0o640);
        assert!(holds(&fs::read_to_string(&file).unwrap(), &brain()));
        let mut names: Vec<String> = fs::read_dir(file.parent().unwrap()).unwrap().map(|d| d.unwrap().file_name().to_string_lossy().into_owned()).collect();
        names.sort();
        assert_eq!(names, ["MEMORY.md", "MEMORY.md.lock"]);

        // Twice is once: the file is not even rewritten.
        let written = fs::metadata(&file).unwrap().modified().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(20));
        assert_eq!(put(&file, &e, LIMIT).unwrap(), Merge::Same);
        assert_eq!(fs::metadata(&file).unwrap().modified().unwrap(), written);

        // Full: nothing written.
        let before = fs::read_to_string(&file).unwrap();
        assert_eq!(put(&file, &entry(Path::new("/elsewhere")), 100).unwrap(), Merge::Full);
        assert_eq!(fs::read_to_string(&file).unwrap(), before);
    }

    #[cfg(unix)]
    #[test]
    fn waits_while_hermes_holds_its_lock() {
        use std::os::unix::io::AsRawFd;
        let tmp = tempfile::tempdir().unwrap();
        let file = tmp.path().join("MEMORY.md");
        let held = fs::OpenOptions::new().write(true).create(true).truncate(false).open(tmp.path().join("MEMORY.md.lock")).unwrap();
        // SAFETY: flock on a descriptor `held` owns, as Hermes's _file_lock does.
        assert_eq!(unsafe { flock::flock(held.as_raw_fd(), flock::LOCK_EX) }, 0);
        let start = std::time::Instant::now();
        let release = std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(400));
            drop(held);
        });
        assert!(matches!(put(&file, &entry(&brain()), LIMIT).unwrap(), Merge::Write(_)));
        assert!(start.elapsed() >= std::time::Duration::from_millis(300), "written while Hermes held its lock");
        release.join().unwrap();
    }
}
