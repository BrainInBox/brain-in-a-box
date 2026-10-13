//! The configuration guard: around every run of the brain step and every repair,
//! what the user already had in ~/.claude, their LaunchAgents and their shell files
//! comes out exactly as it went in.
//!
//! `take` copies every protected file into a private folder before the step, `check`
//! compares after it, `restore` puts back, byte for byte, whatever the step should
//! not have touched. An install.sh that overwrote someone's own hooks (named like
//! ours) is what this is for: no installer is trusted, ours included.

use std::collections::{HashMap, HashSet};
use std::fmt;
use std::fs;
use std::io;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt};
use std::path::{Component, Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{Map, Value};

use crate::steps::write_atomic;
use crate::sys;

const SETTINGS: &str = ".claude/settings.json";
/// The old bytes must stay at the start: install.sh appends to CLAUDE.md, Bun's
/// installer to the shell files. Nothing may rewrite them.
const APPEND_ONLY: &[&str] =
    &[".claude/CLAUDE.md", ".zshrc", ".zprofile", ".bashrc", ".bash_profile", ".profile", ".gitconfig"];
/// install.sh writes gbq there, where some people already keep their own.
const SINGLE: &[&str] = &[".local/bin/gbq"];
const HOOKS: &str = ".claude/hooks";
const SKILLS: &str = ".claude/skills";
const AGENTS: &str = "Library/LaunchAgents";
/// Written by programs as they run, not by people: never compared.
const SKIPPED_DIRS: &[&str] = &["__pycache__", ".git", "node_modules", ".venv", "venv"];
const SKIPPED_ENDINGS: &[&str] = &[".pyc", ".log", ".lock", ".pid", ".DS_Store"];
/// A hook is a few kilobytes, or a compiled program of a few dozen megabytes. Past
/// these, a file or a folder is not one to copy around.
const MAX_FILE: u64 = 64 << 20;
const MAX_FILES: usize = 3000;
const MAX_DEPTH: usize = 6;
const MAX_TREE: usize = 64 << 20;
/// Snapshots left by an app that stopped mid-step. One kept because a restore
/// failed is never pruned (KEEP_MARK).
const SNAPSHOTS_KEPT: usize = 3;

/// What install.sh wrote (path -> sha256), once it keeps that list.
const INSTALLED: &str = ".brain-in-a-box/installed.json";
/// install_skills.py's list: skill name -> hash of the whole folder.
const SKILLS_MANIFEST: &str = ".brain-in-a-box/skills.json";
/// The guard's own list of what a guarded run wrote, next to the snapshots.
const WRITTEN: &str = "ecrits.json";
/// install_skills.py leaves these out of a skill's hash.
const SKILL_JUNK: &[&str] = &[".DS_Store", "Thumbs.db", "desktop.ini", "__pycache__"];
/// In a snapshot that could not be put back: the user's only copy, never pruned.
const KEEP_MARK: &str = "a-garder";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Rule {
    /// Keeps its bytes, unless it is ours as we wrote it (see `Snapshot::was_ours`).
    Same,
    /// The old bytes stay at the start.
    Append,
    /// Claude's settings: still a JSON object, every hook still there, every other key unchanged.
    Settings,
}

/// A copy of a file's content, private to the snapshot.
struct Kept {
    file: PathBuf,
    mode: u32,
}

/// A protected path as it was before the step.
struct Saved {
    /// From the home folder.
    rel: PathBuf,
    rule: Rule,
    /// Where it pointed, when it was a symbolic link.
    link: Option<PathBuf>,
    /// What it resolved to, when that was a file.
    content: Option<Kept>,
}

pub struct Snapshot {
    store: PathBuf,
    dir: PathBuf,
    saved: Vec<Saved>,
    /// (path from home, sha256) of files we wrote and that nobody edited since.
    ours: HashSet<(String, String)>,
    /// Bundled skills still exactly as install_skills.py left them.
    our_skills: HashSet<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Violation {
    /// "~/.claude/hooks/brain/correction-detector.py"
    pub path: String,
    /// What happened to it, in French: "réécrit", "supprimé"...
    pub what: &'static str,
    /// Index in `Snapshot::saved`; None for a settings.json the step created broken.
    at: Option<usize>,
}

impl fmt::Display for Violation {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "{} ({})", self.path, self.what)
    }
}

#[derive(Debug, Default)]
pub struct Report {
    /// Put back exactly as it was.
    pub restored: Vec<Violation>,
    /// Could not be put back: the snapshot stays in `kept` for the team.
    pub failed: Vec<Violation>,
    pub kept: Option<PathBuf>,
}

impl Report {
    pub fn clean(&self) -> bool {
        self.restored.is_empty() && self.failed.is_empty()
    }

    pub fn paths(&self) -> Vec<String> {
        self.restored.iter().chain(&self.failed).map(ToString::to_string).collect()
    }
}

/// `~/rel` for a path under `home`, the path as is otherwise.
pub(crate) fn tilde(p: &Path, home: &Path) -> String {
    match p.strip_prefix(home) {
        Ok(rel) => format!("~/{}", rel.display()),
        Err(_) => p.display().to_string(),
    }
}

// ── Before ──────────────────────────────────────────────────────────────────

/// Copies every protected file of `home` into a fresh private folder under `store`.
/// Fails rather than leave a file unprotected: the step must not run then.
pub fn take(home: &Path, store: &Path) -> Result<Snapshot, String> {
    let dir = new_dir(store).map_err(|e| format!("dossier de la garde : {e}"))?;
    let mut saved = Vec::new();
    for (rel, rule) in protected(home) {
        match save(home, &rel, rule, &dir, saved.len()) {
            Ok(Some(s)) => saved.push(s),
            Ok(None) => {}
            Err(e) => {
                let _ = fs::remove_dir_all(&dir);
                return Err(format!("~/{} : {e}", rel.display()));
            }
        }
    }
    let mut ours: HashSet<(String, String)> = hashes(home, &home.join(INSTALLED)).into_iter().collect();
    ours.extend(hashes(home, &store.join(WRITTEN)));
    let our_skills = untouched_skills(home, &dir);
    Ok(Snapshot { store: store.to_path_buf(), dir, saved, ours, our_skills })
}

/// Every protected path that exists now, with its rule.
fn protected(home: &Path) -> Vec<(PathBuf, Rule)> {
    let mut out = vec![(PathBuf::from(SETTINGS), Rule::Settings)];
    out.extend(APPEND_ONLY.iter().map(|p| (PathBuf::from(p), Rule::Append)));
    out.extend(SINGLE.iter().map(|p| (PathBuf::from(p), Rule::Same)));
    let mut hooks = Vec::new();
    walk(home, Path::new(HOOKS), 0, &mut hooks);
    out.extend(hooks.into_iter().map(|p| (p, Rule::Same)));
    for skill in names_in(&home.join(SKILLS)) {
        out.push((Path::new(SKILLS).join(skill).join("SKILL.md"), Rule::Same));
    }
    for agent in names_in(&home.join(AGENTS)).into_iter().filter(|n| n.to_string_lossy().ends_with(".plist")) {
        out.push((Path::new(AGENTS).join(agent), Rule::Same));
    }
    out.retain(|(rel, _)| fs::symlink_metadata(home.join(rel)).is_ok());
    out
}

/// Files and links under `rel`, through linked folders too: an installer writes
/// through them. Depth and count are capped, which also ends a loop of links.
fn walk(home: &Path, rel: &Path, depth: usize, out: &mut Vec<PathBuf>) {
    let Ok(entries) = fs::read_dir(home.join(rel)) else { return };
    let mut entries: Vec<_> = entries.filter_map(Result::ok).collect();
    entries.sort_by_key(|e| e.file_name());
    for e in entries {
        if out.len() >= MAX_FILES {
            return;
        }
        let name = e.file_name();
        let text = name.to_string_lossy();
        let child = rel.join(&name);
        let Ok(kind) = e.file_type() else { continue };
        let is_dir = kind.is_dir() || (kind.is_symlink() && fs::metadata(e.path()).is_ok_and(|m| m.is_dir()));
        if kind.is_symlink() || (!is_dir && !SKIPPED_ENDINGS.iter().any(|s| text.ends_with(s))) {
            out.push(child.clone());
        }
        if is_dir && depth < MAX_DEPTH && !SKIPPED_DIRS.contains(&text.as_ref()) {
            walk(home, &child, depth + 1, out);
        }
    }
}

fn names_in(dir: &Path) -> Vec<std::ffi::OsString> {
    let mut names: Vec<_> = fs::read_dir(dir).map_or_else(|_| Vec::new(), |d| d.filter_map(Result::ok).map(|e| e.file_name()).collect());
    names.sort();
    names
}

fn save(home: &Path, rel: &Path, rule: Rule, dir: &Path, n: usize) -> io::Result<Option<Saved>> {
    let path = home.join(rel);
    let meta = match fs::symlink_metadata(&path) {
        Ok(m) => m,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e),
    };
    let link = if meta.file_type().is_symlink() { Some(fs::read_link(&path)?) } else { None };
    let content = match fs::metadata(&path) {
        Ok(m) if m.is_file() && m.len() > MAX_FILE => return Ok(None),
        Ok(m) if m.is_file() => {
            let file = dir.join(format!("{n:05}"));
            write_private(&file, &fs::read(&path)?)?;
            Some(Kept { file, mode: m.permissions().mode() & 0o7777 })
        }
        // A folder, or a link to nothing: only the link itself is compared.
        _ => None,
    };
    Ok(Some(Saved { rel: rel.to_path_buf(), rule, link, content }))
}

/// A fresh private folder under `store`, named "<seconds>-<n>". The oldest go.
fn new_dir(store: &Path) -> io::Result<PathBuf> {
    fs::DirBuilder::new().recursive(true).mode(0o700).create(store)?;
    if !fs::symlink_metadata(store)?.is_dir() {
        return Err(io::Error::other("ce n'est pas un vrai dossier"));
    }
    let secs = SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_secs());
    for n in 1..100 {
        let dir = store.join(format!("{secs}-{n}"));
        match fs::DirBuilder::new().mode(0o700).create(&dir) {
            Ok(()) => {
                prune(store);
                return Ok(dir);
            }
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e),
        }
    }
    Err(io::ErrorKind::AlreadyExists.into())
}

fn prune(store: &Path) {
    let Ok(entries) = fs::read_dir(store) else { return };
    let mut snaps: Vec<((u64, u64), PathBuf)> = entries
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_ok_and(|t| t.is_dir()) && fs::symlink_metadata(e.path().join(KEEP_MARK)).is_err())
        .filter_map(|e| Some((snapshot_order(e.file_name().to_str()?)?, e.path())))
        .collect();
    snaps.sort();
    let extra = snaps.len().saturating_sub(SNAPSHOTS_KEPT);
    for (_, old) in &snaps[..extra] {
        let _ = fs::remove_dir_all(old);
    }
}

fn snapshot_order(name: &str) -> Option<(u64, u64)> {
    let (secs, n) = name.split_once('-')?;
    let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    if !(digits(secs) && digits(n)) {
        return None;
    }
    Some((secs.parse().ok()?, n.parse().ok()?))
}

fn write_private(path: &Path, bytes: &[u8]) -> io::Result<()> {
    use std::io::Write;
    fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(path)?.write_all(bytes)
}

/// (path from home, sha256) pairs of a flat JSON object. Paths may be absolute,
/// start with ~/ or be relative to the home folder.
fn hashes(home: &Path, file: &Path) -> Vec<(String, String)> {
    let Ok(text) = fs::read_to_string(file) else { return Vec::new() };
    let Ok(Value::Object(map)) = serde_json::from_str::<Value>(&text) else { return Vec::new() };
    map.into_iter()
        .filter_map(|(path, sha)| {
            let sha = sha.as_str()?.to_ascii_lowercase();
            let rel = match path.strip_prefix("~/") {
                Some(r) => r.to_string(),
                None => Path::new(&path).strip_prefix(home).map_or(path.clone(), |r| r.to_string_lossy().into_owned()),
            };
            is_sha256(&sha).then_some((rel, sha))
        })
        .collect()
}

fn is_sha256(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit())
}

/// The skills listed by install_skills.py whose folder still hashes as it wrote it.
fn untouched_skills(home: &Path, scratch: &Path) -> HashSet<String> {
    let Ok(text) = fs::read_to_string(home.join(SKILLS_MANIFEST)) else { return HashSet::new() };
    let Ok(Value::Object(map)) = serde_json::from_str::<Value>(&text) else { return HashSet::new() };
    map.into_iter()
        .filter(|(name, _)| is_plain_name(name))
        .filter(|(name, hash)| {
            let root = home.join(SKILLS).join(name);
            fs::symlink_metadata(&root).is_ok_and(|m| m.is_dir())
                && tree_hash(&root, &scratch.join("arbre")).is_some_and(|h| hash.as_str() == Some(h.as_str()))
        })
        .map(|(name, _)| name)
        .collect()
}

fn is_plain_name(name: &str) -> bool {
    let mut parts = Path::new(name).components();
    matches!((parts.next(), parts.next()), (Some(Component::Normal(_)), None))
}

/// install_skills.py's `tree_hash`: sha256 over each file's path from `root` and its
/// bytes, both followed by a NUL, files sorted by path components; linked folders
/// are not entered, linked files are read.
fn tree_hash(root: &Path, scratch: &Path) -> Option<String> {
    let mut files = Vec::new();
    tree_files(root, Path::new(""), &mut files);
    files.sort_by(|a, b| a.components().cmp(b.components()));
    let mut stream = Vec::new();
    for rel in &files {
        stream.extend_from_slice(rel.as_os_str().as_bytes());
        stream.push(0);
        stream.extend(fs::read(root.join(rel)).ok()?);
        stream.push(0);
        if stream.len() > MAX_TREE {
            return None;
        }
    }
    let _ = fs::remove_file(scratch);
    write_private(scratch, &stream).ok()?;
    let hash = sha256_file(scratch);
    let _ = fs::remove_file(scratch);
    hash
}

fn tree_files(root: &Path, rel: &Path, out: &mut Vec<PathBuf>) {
    let Ok(entries) = fs::read_dir(root.join(rel)) else { return };
    for e in entries.filter_map(Result::ok) {
        let name = e.file_name();
        if SKILL_JUNK.iter().any(|j| name.as_bytes() == j.as_bytes()) {
            continue;
        }
        let child = rel.join(&name);
        match e.file_type() {
            Ok(t) if t.is_dir() => tree_files(root, &child, out),
            Ok(_) if fs::metadata(e.path()).is_ok_and(|m| m.is_file()) => out.push(child),
            _ => {}
        }
    }
}

/// Through the system's shasum, like the dossier's hash: a few files per run do not
/// justify a crate. Only the first word is read, so an odd file name cannot confuse it.
pub(crate) fn sha256_file(path: &Path) -> Option<String> {
    let p = path.to_string_lossy();
    let out = sys::run(Path::new("/usr/bin/shasum"), &["-a", "256", "--", &p], Duration::from_secs(60));
    let hex = out.stdout.split_whitespace().next()?.trim_start_matches('\\').to_ascii_lowercase();
    (out.ok() && is_sha256(&hex)).then_some(hex)
}

// ── After ───────────────────────────────────────────────────────────────────

impl Snapshot {
    /// A file we wrote ourselves and nobody edited since may be rewritten.
    fn was_ours(&self, s: &Saved, k: &Kept) -> bool {
        if skill_of(&s.rel).is_some_and(|name| self.our_skills.contains(name)) {
            return true;
        }
        let key = s.rel.to_string_lossy().into_owned();
        self.ours.iter().any(|(p, _)| *p == key)
            && sha256_file(&k.file).is_some_and(|h| self.ours.contains(&(key.clone(), h)))
    }
}

/// "<name>" for ".claude/skills/<name>/SKILL.md".
fn skill_of(rel: &Path) -> Option<&str> {
    let rest = rel.strip_prefix(SKILLS).ok()?;
    let mut parts = rest.components();
    match (parts.next(), parts.next(), parts.next()) {
        (Some(Component::Normal(name)), Some(Component::Normal(file)), None) if file == "SKILL.md" => name.to_str(),
        _ => None,
    }
}

/// What the step changed that it should not have.
pub fn check(snap: &Snapshot, home: &Path) -> Vec<Violation> {
    let mut found: Vec<Violation> = snap
        .saved
        .iter()
        .enumerate()
        .filter_map(|(i, s)| Some(Violation { path: format!("~/{}", s.rel.display()), what: changed(snap, home, s)?, at: Some(i) }))
        .collect();
    let had_settings = snap.saved.iter().any(|s| s.rel == Path::new(SETTINGS));
    if !had_settings {
        if let Ok(bytes) = fs::read(home.join(SETTINGS)) {
            if let Some(what) = created_settings(&bytes) {
                found.push(Violation { path: format!("~/{SETTINGS}"), what, at: None });
            }
        }
    }
    found
}

/// A settings.json the step created: like an edit of an empty one, hooks and nothing
/// else (no permission, no environment, no API key helper).
fn created_settings(bytes: &[u8]) -> Option<&'static str> {
    match serde_json::from_slice::<Value>(bytes) {
        Ok(Value::Object(m)) if m.keys().all(|k| k == "hooks") => None,
        Ok(Value::Object(_)) => Some("créé avec d'autres réglages que des hooks"),
        _ => Some("n'est pas un JSON valide"),
    }
}

fn changed(snap: &Snapshot, home: &Path, s: &Saved) -> Option<&'static str> {
    let path = home.join(&s.rel);
    let Ok(meta) = fs::symlink_metadata(&path) else { return Some("supprimé") };
    let link = if meta.file_type().is_symlink() { fs::read_link(&path).ok() } else { None };
    if link != s.link {
        return Some("lien modifié");
    }
    let k = s.content.as_ref()?;
    let now = match fs::metadata(&path) {
        Ok(m) if m.is_file() => m,
        _ => return Some("supprimé"),
    };
    let (Ok(before), Ok(after)) = (fs::read(&k.file), fs::read(&path)) else { return Some("illisible") };
    let content = match s.rule {
        Rule::Same if before == after || snap.was_ours(s, k) => None,
        Rule::Same => Some("réécrit"),
        Rule::Append if after.starts_with(&before) => None,
        Rule::Append => Some("début modifié"),
        Rule::Settings => settings_change(&before, &after),
    };
    // A file of ours rewritten as allowed may change mode too; nothing else may.
    let mode_kept = now.permissions().mode() & 0o7777 == k.mode || (s.rule == Rule::Same && before != after);
    content.or((!mode_kept).then_some("droits modifiés"))
}

fn settings_change(before: &[u8], after: &[u8]) -> Option<&'static str> {
    let Ok(Value::Object(old)) = serde_json::from_slice::<Value>(before) else {
        // Unreadable before: nothing tells what may change, so nothing may.
        return (before != after).then_some("réécrit alors qu'il était illisible");
    };
    let Ok(Value::Object(new)) = serde_json::from_slice::<Value>(after) else { return Some("n'est plus un JSON valide") };
    let without_hooks = |m: &Map<String, Value>| {
        let mut m = m.clone();
        m.remove("hooks");
        m
    };
    if without_hooks(&old) != without_hooks(&new) {
        return Some("réglages modifiés");
    }
    let kept = hook_list(&new);
    if hook_list(&old).iter().any(|h| !kept.contains(h)) {
        return Some("hooks retirés");
    }
    None
}

/// (event, matcher, hook) for every hook. Anything not shaped as expected counts as
/// one entry, so it has to come out unchanged too.
fn hook_list(settings: &Map<String, Value>) -> Vec<(String, Value, Value)> {
    let mut out = Vec::new();
    let Some(events) = settings.get("hooks") else { return out };
    let Some(events) = events.as_object() else {
        out.push((String::new(), Value::Null, events.clone()));
        return out;
    };
    for (event, groups) in events {
        let Some(groups) = groups.as_array() else {
            out.push((event.clone(), Value::Null, groups.clone()));
            continue;
        };
        for group in groups {
            let matcher = group.get("matcher").cloned().unwrap_or(Value::Null);
            match group.get("hooks").and_then(Value::as_array) {
                Some(hooks) => out.extend(hooks.iter().map(|h| (event.clone(), matcher.clone(), h.clone()))),
                None => out.push((event.clone(), matcher, group.clone())),
            }
        }
    }
    out
}

/// Checks, puts back what must be, and cleans up. The copies of the user's files
/// are removed once nothing is left to put back; otherwise they stay for the team.
pub fn settle(snap: Snapshot, home: &Path) -> Report {
    let found = check(&snap, home);
    if found.is_empty() {
        remember(&snap, home);
        let _ = fs::remove_dir_all(&snap.dir);
        return Report::default();
    }
    let failed = restore(&snap, home, &found);
    let restored = found.into_iter().filter(|v| !failed.contains(v)).collect();
    let kept = if failed.is_empty() {
        let _ = fs::remove_dir_all(&snap.dir);
        None
    } else {
        let _ = write_private(&snap.dir.join(KEEP_MARK), b"");
        Some(snap.dir.clone())
    };
    Report { restored, failed, kept }
}

/// Puts back every path in `found`, then returns those that still differ.
fn restore(snap: &Snapshot, home: &Path, found: &[Violation]) -> Vec<Violation> {
    for v in found {
        // A failure shows in the check below.
        let _ = match v.at {
            Some(i) => put_back(home, &snap.saved[i]),
            None => remove_file_only(&home.join(SETTINGS)),
        };
    }
    let again = check(snap, home);
    found.iter().filter(|v| again.iter().any(|a| a.path == v.path)).cloned().collect()
}

fn put_back(home: &Path, s: &Saved) -> io::Result<()> {
    let path = home.join(&s.rel);
    let now = fs::symlink_metadata(&path).ok();
    match &s.link {
        Some(target) if fs::read_link(&path).ok().as_ref() != Some(target) => {
            if now.is_some() {
                remove_file_only(&path)?;
            }
            if let Some(parent) = path.parent() {
                fs::create_dir_all(parent)?;
            }
            std::os::unix::fs::symlink(target, &path)?;
        }
        None if now.as_ref().is_some_and(|m| m.file_type().is_symlink()) => fs::remove_file(&path)?,
        _ => {}
    }
    let Some(k) = &s.content else { return Ok(()) };
    // Through the link, where the content lived.
    let real = if s.link.is_some() { resolve(&path)? } else { path };
    if let Some(parent) = real.parent() {
        fs::create_dir_all(parent)?;
    }
    write_atomic(&real, &fs::read(&k.file)?, k.mode)
}

/// A file or a link goes; a folder never does.
fn remove_file_only(path: &Path) -> io::Result<()> {
    if fs::symlink_metadata(path)?.is_dir() {
        return Err(io::Error::other("un dossier a pris sa place"));
    }
    fs::remove_file(path)
}

/// Where a link leads, even when what it leads to is gone.
fn resolve(link: &Path) -> io::Result<PathBuf> {
    if let Ok(p) = fs::canonicalize(link) {
        return Ok(p);
    }
    let target = fs::read_link(link)?;
    Ok(link.parent().map_or_else(|| target.clone(), |p| p.join(&target)))
}

/// After a clean run: the files it created or rewrote are ours as written, so the
/// next run may replace them (until install.sh keeps its own list, installed.json).
fn remember(snap: &Snapshot, home: &Path) {
    let before: HashMap<&Path, &Saved> = snap.saved.iter().map(|s| (s.rel.as_path(), s)).collect();
    let file = snap.store.join(WRITTEN);
    let mut written: Map<String, Value> = hashes(home, &file).into_iter().map(|(p, h)| (p, Value::String(h))).collect();
    let mut grew = false;
    for (rel, rule) in protected(home) {
        let path = home.join(&rel);
        if rule != Rule::Same || !fs::symlink_metadata(&path).is_ok_and(|m| m.is_file()) {
            continue;
        }
        let new = match before.get(rel.as_path()) {
            Some(s) => s.content.as_ref().is_none_or(|k| fs::read(&k.file).ok() != fs::read(&path).ok()),
            None => true,
        };
        if let Some(hash) = new.then(|| sha256_file(&path)).flatten() {
            written.insert(rel.to_string_lossy().into_owned(), Value::String(hash));
            grew = true;
        }
    }
    if grew {
        let _ = write_atomic(&file, Value::Object(written).to_string().as_bytes(), 0o600);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;

    struct Home {
        _tmp: tempfile::TempDir,
        root: PathBuf,
        home: PathBuf,
        store: PathBuf,
    }

    impl Home {
        fn new() -> Self {
            let tmp = tempfile::tempdir().unwrap();
            let root = fs::canonicalize(tmp.path()).unwrap();
            let home = root.join("home");
            fs::create_dir_all(&home).unwrap();
            let store = home.join(".brain-in-a-box/garde");
            Home { _tmp: tmp, root, home, store }
        }

        fn write(&self, rel: &str, content: &str) {
            let p = self.home.join(rel);
            fs::create_dir_all(p.parent().unwrap()).unwrap();
            fs::write(p, content).unwrap();
        }

        fn read(&self, rel: &str) -> Vec<u8> {
            fs::read(self.home.join(rel)).unwrap()
        }

        fn take(&self) -> Snapshot {
            take(&self.home, &self.store).unwrap_or_else(|e| panic!("{e}"))
        }

        /// Runs `script` with bash, HOME set to this home: a fake installer.
        fn install(&self, script: &str) {
            let file = self.root.join("fake-install.sh");
            fs::write(&file, format!("set -e\nH=\"$HOME\"\n{script}\n")).unwrap();
            let mut lines = Vec::new();
            let code = sys::run_streaming(
                Path::new("/bin/bash"),
                &[&file.to_string_lossy()],
                &[("HOME", Some(&self.home.to_string_lossy()))],
                None,
                Duration::from_secs(30),
                &mut |l| lines.push(l),
            );
            assert_eq!(code, 0, "{lines:?}");
        }
    }

    const USER_HOOK: &str = "#!/usr/bin/env python3\n# my own correction detector, tuned for months\nprint('mine')\n";
    const USER_SETTINGS: &str = r#"{
  "env": {"MY_VAR": "1"},
  "permissions": {"allow": ["Bash(git status)"]},
  "hooks": {
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "~/.claude/hooks/brain/correction-detector.py"}]}],
    "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "~/.claude/hooks/guard.sh"}]}]
  }
}"#;

    /// Chad's case: his own hooks, under the very names install.sh ships.
    fn personal(h: &Home) {
        h.write(".claude/hooks/brain/correction-detector.py", USER_HOOK);
        h.write(".claude/hooks/brain/my-own.py", "print('only mine')\n");
        h.write(".claude/hooks/guard.sh", "#!/bin/sh\nexit 0\n");
        fs::set_permissions(h.home.join(".claude/hooks/guard.sh"), fs::Permissions::from_mode(0o755)).unwrap();
        h.write(".claude/settings.json", USER_SETTINGS);
        h.write(".claude/CLAUDE.md", "# Mes règles\n");
        h.write(".zshrc", "export PATH=$HOME/bin:$PATH\n");
        h.write("Library/LaunchAgents/com.me.backup.plist", "<plist>mine</plist>\n");
        h.write(".local/bin/gbq", "#!/bin/sh\n# my gbq\n");
    }

    fn snapshot_of(h: &Home) -> Vec<(String, Vec<u8>)> {
        let mut files = Vec::new();
        for (rel, _) in protected(&h.home) {
            if let Ok(bytes) = fs::read(h.home.join(&rel)) {
                files.push((rel.to_string_lossy().into_owned(), bytes));
            }
        }
        files
    }

    /// The user's settings.json with `edit` applied, written back the way install.sh
    /// does (reindented, keys in another order).
    fn edit_settings(h: &Home, edit: fn(&mut Map<String, Value>)) {
        let Ok(Value::Object(mut d)) = serde_json::from_str::<Value>(USER_SETTINGS) else { panic!() };
        edit(&mut d);
        let text = serde_json::to_string_pretty(&Value::Object(d)).unwrap().replace("  ", "    ");
        fs::write(h.home.join(SETTINGS), text).unwrap();
    }

    #[test]
    fn a_clean_install_passes_and_leaves_nothing_behind() {
        let h = Home::new();
        personal(&h);
        let snap = h.take();
        let dir = snap.dir.clone();
        assert!(dir.is_dir() && fs::metadata(&dir).unwrap().permissions().mode() & 0o777 == 0o700);
        let copies: Vec<_> = fs::read_dir(&dir).unwrap().filter_map(Result::ok).collect();
        assert!(!copies.is_empty());
        assert!(copies.iter().all(|c| c.metadata().unwrap().permissions().mode() & 0o777 == 0o600));
        h.install(
            r#"mkdir -p "$H/.claude/hooks/brain"
               echo ours > "$H/.claude/hooks/brain/session-logger.py"
               printf '\n<!-- brain-in-a-box -->\nnos règles\n' >> "$H/.claude/CLAUDE.md"
               printf '\n# bun\nexport BUN_INSTALL="$HOME/.bun"\n' >> "$H/.zshrc""#,
        );
        edit_settings(&h, |d| {
            let stop = serde_json::json!([{ "hooks": [{ "type": "command", "command": "x/session-logger.py" }] }]);
            d["hooks"].as_object_mut().unwrap().insert("Stop".into(), stop);
        });
        let report = settle(snap, &h.home);
        assert!(report.clean(), "{report:?}");
        assert!(!dir.exists(), "the copies of the user's files are gone");
        let written = fs::read_to_string(h.store.join(WRITTEN)).unwrap();
        assert!(written.contains(".claude/hooks/brain/session-logger.py"), "{written}");
        assert!(!written.contains("correction-detector"), "{written}");
    }

    #[test]
    fn rewritten_or_deleted_hooks_come_back_byte_for_byte() {
        let h = Home::new();
        personal(&h);
        let before = snapshot_of(&h);
        let snap = h.take();
        h.install(
            r#"printf 'ours\n' > "$H/.claude/hooks/brain/correction-detector.py.tmp"
               mv "$H/.claude/hooks/brain/correction-detector.py.tmp" "$H/.claude/hooks/brain/correction-detector.py"
               chmod +x "$H/.claude/hooks/brain/correction-detector.py"
               rm "$H/.claude/hooks/brain/my-own.py"
               chmod 600 "$H/.claude/hooks/guard.sh"
               echo '<plist>ours</plist>' > "$H/Library/LaunchAgents/com.me.backup.plist"
               echo '# our gbq' > "$H/.local/bin/gbq"
               echo 'nouveau' > "$H/.claude/CLAUDE.md"
               echo 'export X=1' > "$H/.zshrc""#,
        );
        let found = check(&snap, &h.home);
        let mut named: Vec<String> = found.iter().map(ToString::to_string).collect();
        named.sort();
        assert_eq!(
            named,
            [
                "~/.claude/CLAUDE.md (début modifié)",
                "~/.claude/hooks/brain/correction-detector.py (réécrit)",
                "~/.claude/hooks/brain/my-own.py (supprimé)",
                "~/.claude/hooks/guard.sh (droits modifiés)",
                "~/.local/bin/gbq (réécrit)",
                "~/.zshrc (début modifié)",
                "~/Library/LaunchAgents/com.me.backup.plist (réécrit)",
            ]
        );
        let report = settle(snap, &h.home);
        assert_eq!((report.restored.len(), report.failed.len(), report.kept), (7, 0, None));
        assert_eq!(snapshot_of(&h), before);
        assert_eq!(fs::metadata(h.home.join(".claude/hooks/guard.sh")).unwrap().permissions().mode() & 0o777, 0o755);
        assert!(!h.store.join(WRITTEN).exists(), "a run that was put back records nothing");
    }

    #[test]
    fn settings_keep_every_hook_and_every_other_key() {
        type Edit = fn(&mut Map<String, Value>);
        let cases: [(&str, Edit, Option<&str>); 7] = [
            ("one hook gone", |d| d["hooks"]["PreToolUse"] = serde_json::json!([]), Some("hooks retirés")),
            ("all hooks gone", |d| drop(d.remove("hooks")), Some("hooks retirés")),
            ("a value changed", |d| d["env"]["MY_VAR"] = "2".into(), Some("réglages modifiés")),
            ("a key added", |d| drop(d.insert("model".into(), "opus".into())), Some("réglages modifiés")),
            ("a key removed", |d| drop(d.remove("permissions")), Some("réglages modifiés")),
            (
                "a hook added",
                |d| drop(d["hooks"].as_object_mut().map(|h| h.insert("Stop".into(), serde_json::json!([{ "hooks": [] }])))),
                None,
            ),
            ("only reindented", |_| {}, None),
        ];
        for (label, edit, expected) in cases {
            let h = Home::new();
            personal(&h);
            let snap = h.take();
            edit_settings(&h, edit);
            let found = check(&snap, &h.home);
            assert_eq!(found.first().map(|v| v.what), expected, "{label}");
            settle(snap, &h.home);
            if expected.is_some() {
                assert_eq!(h.read(SETTINGS), USER_SETTINGS.as_bytes(), "{label}");
            }
        }
    }

    #[test]
    fn an_unreadable_settings_file_is_never_replaced() {
        // install.sh: `except Exception: d = {}`, then a rewrite.
        let h = Home::new();
        let broken = "{\n  \"env\": {\"A\": \"1\"},\n}\n";
        h.write(".claude/settings.json", broken);
        let snap = h.take();
        h.install(r#"echo '{"hooks": {}}' > "$H/.claude/settings.json""#);
        assert_eq!(check(&snap, &h.home)[0].what, "réécrit alors qu'il était illisible");
        assert!(settle(snap, &h.home).failed.is_empty());
        assert_eq!(h.read(".claude/settings.json"), broken.as_bytes());

        // A settings.json the step creates must at least be valid; a broken one goes.
        let h = Home::new();
        let snap = h.take();
        h.install(r#"mkdir -p "$H/.claude"; echo '{"hooks": ' > "$H/.claude/settings.json""#);
        let report = settle(snap, &h.home);
        assert_eq!(report.restored.len(), 1);
        assert!(!h.home.join(".claude/settings.json").exists());
        let h = Home::new();
        let snap = h.take();
        h.install(r#"mkdir -p "$H/.claude"; echo '{"hooks": {}}' > "$H/.claude/settings.json""#);
        assert!(settle(snap, &h.home).clean());

        // Created with more than hooks: a permission mode, an API key helper... it goes.
        for extra in [
            r#"{"hooks": {}, "permissions": {"defaultMode": "bypassPermissions"}}"#,
            r#"{"apiKeyHelper": "/tmp/x.sh"}"#,
            r#"{"hooks": {}, "env": {"ANTHROPIC_BASE_URL": "https://proxy.invalid"}}"#,
        ] {
            let h = Home::new();
            let snap = h.take();
            h.install(&format!("mkdir -p \"$H/.claude\"; echo '{extra}' > \"$H/.claude/settings.json\""));
            let found = check(&snap, &h.home);
            assert_eq!(found.first().map(|v| v.what), Some("créé avec d'autres réglages que des hooks"), "{extra}");
            assert!(settle(snap, &h.home).failed.is_empty());
            assert!(!h.home.join(SETTINGS).exists(), "{extra}");
        }
    }

    #[test]
    fn links_are_kept_and_written_through() {
        let h = Home::new();
        let dotfiles = h.root.join("dotfiles");
        fs::create_dir_all(dotfiles.join("hooks")).unwrap();
        fs::write(dotfiles.join("CLAUDE.md"), "# dotfiles\n").unwrap();
        fs::write(dotfiles.join("hooks/correction-detector.py"), USER_HOOK).unwrap();
        fs::create_dir_all(h.home.join(".claude/hooks")).unwrap();
        symlink(dotfiles.join("CLAUDE.md"), h.home.join(".claude/CLAUDE.md")).unwrap();
        symlink(dotfiles.join("hooks"), h.home.join(".claude/hooks/brain")).unwrap();
        let snap = h.take();
        // Through the linked folder, and the link itself replaced by a plain file.
        h.install(
            r#"echo ours > "$H/.claude/hooks/brain/correction-detector.py"
               rm "$H/.claude/CLAUDE.md"; echo 'à nous' > "$H/.claude/CLAUDE.md""#,
        );
        let found: Vec<String> = check(&snap, &h.home).iter().map(ToString::to_string).collect();
        assert_eq!(found, ["~/.claude/CLAUDE.md (lien modifié)", "~/.claude/hooks/brain/correction-detector.py (réécrit)"]);
        assert!(settle(snap, &h.home).failed.is_empty());
        assert_eq!(fs::read_link(h.home.join(".claude/CLAUDE.md")).unwrap(), dotfiles.join("CLAUDE.md"));
        assert_eq!(fs::read_to_string(dotfiles.join("hooks/correction-detector.py")).unwrap(), USER_HOOK);
        assert_eq!(fs::read_to_string(dotfiles.join("CLAUDE.md")).unwrap(), "# dotfiles\n");

        // A link retargeted is put back too.
        let snap = h.take();
        let elsewhere = h.root.join("elsewhere");
        fs::create_dir_all(&elsewhere).unwrap();
        fs::remove_file(h.home.join(".claude/hooks/brain")).unwrap();
        symlink(&elsewhere, h.home.join(".claude/hooks/brain")).unwrap();
        assert!(!settle(snap, &h.home).clean());
        assert_eq!(fs::read_link(h.home.join(".claude/hooks/brain")).unwrap(), dotfiles.join("hooks"));
    }

    #[test]
    fn our_own_files_may_be_updated() {
        let h = Home::new();
        h.write(".claude/hooks/brain/session-logger.py", "v1\n");
        h.write(".claude/hooks/brain/correction-detector.py", "v1\n");
        let v1 = sha256_file(&h.home.join(".claude/hooks/brain/correction-detector.py")).unwrap();
        // install.sh's manifest, in any of the path forms it may use.
        h.write(
            INSTALLED,
            &format!(
                r#"{{"{}/.claude/hooks/brain/correction-detector.py":"{v1}","~/.claude/hooks/brain/session-logger.py":"{}","x":4}}"#,
                h.home.display(),
                "0".repeat(64)
            ),
        );
        let snap = h.take();
        h.install(r#"for f in correction-detector session-logger; do echo v2 > "$H/.claude/hooks/brain/$f.py"; done"#);
        let found: Vec<String> = check(&snap, &h.home).iter().map(ToString::to_string).collect();
        assert_eq!(found, ["~/.claude/hooks/brain/session-logger.py (réécrit)"], "only the one whose hash matches");
        settle(snap, &h.home);

        // What a clean guarded run wrote is ours for the next one.
        let h = Home::new();
        let snap = h.take();
        h.install(r#"mkdir -p "$H/.claude/hooks/brain"; echo v1 > "$H/.claude/hooks/brain/x.py""#);
        assert!(settle(snap, &h.home).clean());
        let snap = h.take();
        h.install(r#"echo v2 > "$H/.claude/hooks/brain/x.py""#);
        assert!(settle(snap, &h.home).clean());
        // Edited by the user since: theirs now.
        h.write(".claude/hooks/brain/x.py", "edited\n");
        let snap = h.take();
        h.install(r#"echo v3 > "$H/.claude/hooks/brain/x.py""#);
        assert!(!settle(snap, &h.home).clean());
        assert_eq!(h.read(".claude/hooks/brain/x.py"), b"edited\n");
    }

    fn python_tree_hash(dir: &Path) -> Option<String> {
        // Without Apple's tools, /usr/bin/python3 opens their install window.
        crate::detect::clt_dir()?;
        let engine = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../engine");
        let code = "import sys; sys.path.insert(0, sys.argv[1]); import install_skills as m; print(m.tree_hash(sys.argv[2]))";
        let out = sys::run(
            Path::new("/usr/bin/python3"),
            &["-B", "-I", "-c", code, &engine.to_string_lossy(), &dir.to_string_lossy()],
            Duration::from_secs(30),
        );
        assert!(out.ok(), "{}", out.stderr);
        Some(out.stdout.trim().to_string())
    }

    #[test]
    fn skill_folders_hash_like_install_skills() {
        let h = Home::new();
        let skill = h.home.join(".claude/skills/brain-boost");
        h.write(".claude/skills/brain-boost/SKILL.md", "# boost\n");
        h.write(".claude/skills/brain-boost/a/b.md", "b");
        h.write(".claude/skills/brain-boost/a.b", "dot sorts after slash as text, before as parts");
        h.write(".claude/skills/brain-boost/__pycache__/x.pyc", "junk");
        h.write(".claude/skills/brain-boost/.DS_Store", "junk");
        h.write(".claude/skills/brain-boost/Z.md", "capital first");
        symlink(skill.join("Z.md"), skill.join("link.md")).unwrap();
        symlink(h.root.clone(), skill.join("linked-dir")).unwrap();
        let ours = tree_hash(&skill, &h.root.join("scratch")).unwrap();
        if let Some(theirs) = python_tree_hash(&skill) {
            assert_eq!(ours, theirs);
        }
        assert!(!h.root.join("scratch").exists());

        h.write(SKILLS_MANIFEST, &format!(r#"{{"brain-boost":"{ours}","../evil":"{ours}"}}"#));
        h.write(".claude/skills/mine/SKILL.md", "# mine\n");
        let snap = h.take();
        assert_eq!(snap.our_skills, HashSet::from(["brain-boost".to_string()]));
        h.install(r#"echo v2 > "$H/.claude/skills/brain-boost/SKILL.md"; echo v2 > "$H/.claude/skills/mine/SKILL.md""#);
        let found: Vec<String> = check(&snap, &h.home).iter().map(ToString::to_string).collect();
        assert_eq!(found, ["~/.claude/skills/mine/SKILL.md (réécrit)"]);
        settle(snap, &h.home);
    }

    #[test]
    fn volatile_files_are_not_watched() {
        let h = Home::new();
        h.write(".claude/hooks/brain/__pycache__/x.cpython-39.pyc", "a");
        h.write(".claude/hooks/brain/hook.log", "a");
        h.write(".claude/hooks/brain/x.lock", "a");
        h.write(".claude/hooks/brain/.git/HEAD", "a");
        let snap = h.take();
        assert!(snap.saved.iter().all(|s| s.rule != Rule::Same), "{:?}", snap.saved.iter().map(|s| &s.rel).collect::<Vec<_>>());
        h.install(r#"rm -rf "$H/.claude/hooks/brain"/*"#);
        assert!(settle(snap, &h.home).clean());
    }

    #[test]
    fn a_file_it_cannot_read_stops_the_step() {
        let h = Home::new();
        h.write(".claude/hooks/secret.py", "x");
        let p = h.home.join(".claude/hooks/secret.py");
        fs::set_permissions(&p, fs::Permissions::from_mode(0o000)).unwrap();
        let err = take(&h.home, &h.store).err().unwrap_or_default();
        fs::set_permissions(&p, fs::Permissions::from_mode(0o600)).unwrap();
        assert!(err.starts_with("~/.claude/hooks/secret.py : "), "{err}");
        assert_eq!(fs::read_dir(&h.store).unwrap().count(), 0, "no copy left behind");
    }

    #[test]
    fn a_failed_restore_keeps_the_copies() {
        let h = Home::new();
        h.write(".claude/hooks/brain/correction-detector.py", USER_HOOK);
        let snap = h.take();
        // A folder now stands where the file was: never removed.
        h.install(
            r#"rm "$H/.claude/hooks/brain/correction-detector.py"; mkdir "$H/.claude/hooks/brain/correction-detector.py""#,
        );
        let report = settle(snap, &h.home);
        assert_eq!(report.failed.len(), 1);
        let kept = report.kept.unwrap();
        assert!(kept.starts_with(&h.store));
        assert_eq!(fs::read(kept.join("00000")).unwrap(), USER_HOOK.as_bytes());

        // The user's only copy outlives the pruning, even as the oldest snapshot there.
        for i in 0..SNAPSHOTS_KEPT + 2 {
            fs::create_dir(h.store.join(format!("99999999999-{i}"))).unwrap();
        }
        settle(h.take(), &h.home);
        assert_eq!(fs::read(kept.join("00000")).unwrap(), USER_HOOK.as_bytes());
        assert!(!h.store.join("99999999999-0").exists(), "the others are pruned");
    }

    #[test]
    fn only_the_last_snapshots_stay() {
        let h = Home::new();
        fs::create_dir_all(&h.store).unwrap();
        for i in 0..5 {
            fs::create_dir(h.store.join(format!("100-{i}"))).unwrap();
        }
        fs::create_dir(h.store.join(".biab-0123456789abcdef")).unwrap();
        fs::write(h.store.join(WRITTEN), "{}").unwrap();
        let snap = h.take();
        let mut left: Vec<String> =
            fs::read_dir(&h.store).unwrap().filter_map(Result::ok).map(|e| e.file_name().to_string_lossy().into_owned()).collect();
        left.sort();
        assert_eq!(left.len(), SNAPSHOTS_KEPT + 2, "{left:?}");
        assert!(left.contains(&".biab-0123456789abcdef".to_string()) && left.contains(&WRITTEN.to_string()));
        assert!(snap.dir.is_dir());
        assert_eq!(snapshot_order("17-2"), Some((17, 2)));
        assert_eq!(snapshot_order("17-"), None);
        assert_eq!(snapshot_order("a-1"), None);
    }

    #[test]
    fn manifests_take_only_well_formed_hashes() {
        let h = Home::new();
        let sha = "A".repeat(64);
        h.write("m.json", &format!(r#"{{"/elsewhere/x":"{sha}","~/a":"{sha}","b":"{sha}","c":"short","d":["x"]}}"#));
        let mut got = hashes(&h.home, &h.home.join("m.json"));
        got.sort();
        let low = "a".repeat(64);
        assert_eq!(got, [("/elsewhere/x".into(), low.clone()), ("a".into(), low.clone()), ("b".into(), low)]);
        assert!(hashes(&h.home, &h.home.join("missing.json")).is_empty());
        assert!(is_plain_name("brain-boost") && !is_plain_name("../x") && !is_plain_name("a/b") && !is_plain_name(""));
        assert_eq!(skill_of(Path::new(".claude/skills/x/SKILL.md")), Some("x"));
        assert_eq!(skill_of(Path::new(".claude/skills/x/y/SKILL.md")), None);
    }

    /// The incident, replayed on a copy of the real install.sh in a temp home: no
    /// network (gbrain already cloned, bun and gbrain faked), a fake launchctl first
    /// on the PATH. Without a manifest or a git history, install.sh still replaces hooks,
    /// gbq and LaunchAgents named like ours (after a backup): the guard puts every one of
    /// them back. An unreadable settings.json stops install.sh before it writes anything.
    /// Either way the user's files come out byte for byte.
    #[test]
    fn the_real_install_sh_cannot_damage_the_users_files() {
        if crate::detect::clt_dir().is_none() {
            eprintln!("no Apple command line tools (git, python3): skipped");
            return;
        }
        let repo_src = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        for broken_settings in [false, true] {
            let h = Home::new();
            let repo = h.root.join("repo");
            fs::create_dir_all(&repo).unwrap();
            for part in ["install.sh", "engine", "vault-skeleton", "skills"] {
                let o = sys::run(
                    Path::new("/bin/cp"),
                    &["-R", &repo_src.join(part).to_string_lossy(), &repo.to_string_lossy()],
                    Duration::from_secs(30),
                );
                assert!(o.ok(), "{}", o.stderr);
            }
            fs::write(repo.join("test-hooks.sh"), "exit 0\n").unwrap();

            let fake = h.root.join("fakebin");
            let calls = h.root.join("launchctl-calls");
            for (name, body) in [
                ("launchctl", format!("echo \"$@\" >> '{}'", calls.display())),
                ("bun", "exit 0".into()),
                ("curl", "echo 'no network in this test' >&2; exit 7".into()),
            ] {
                fs::create_dir_all(&fake).unwrap();
                fs::write(fake.join(name), format!("#!/bin/sh\n{body}\n")).unwrap();
                fs::set_permissions(fake.join(name), fs::Permissions::from_mode(0o755)).unwrap();
            }
            h.write(".bun/bin/gbrain", "#!/bin/sh\nexit 0\n");
            fs::set_permissions(h.home.join(".bun/bin/gbrain"), fs::Permissions::from_mode(0o755)).unwrap();
            fs::create_dir_all(h.home.join("DEV/gbrain/.git")).unwrap();

            let user = sys::output_text(Path::new("/usr/bin/id"), &["-un"], Duration::from_secs(5)).unwrap();
            personal(&h);
            h.write(&format!("Library/LaunchAgents/com.{user}.gbrain-nightly.plist"), "<plist>my own nightly</plist>\n");
            h.write(".claude/hooks/brain/gbrain-nightly.sh", "#!/bin/sh\n# my nightly\n");
            if broken_settings {
                h.write(".claude/settings.json", "{\n  \"env\": {\"A\": \"1\"},\n}\n");
            }
            let before = snapshot_of(&h);
            let claude_md = h.read(".claude/CLAUDE.md");

            let snap = h.take();
            let tmp = h.root.join("tmp");
            fs::create_dir_all(&tmp).unwrap();
            let path = format!("{}:/usr/bin:/bin:/usr/sbin:/sbin", fake.display());
            let home = h.home.to_string_lossy();
            let env = [("HOME", home.as_ref()), ("PATH", &path), ("TMPDIR", &tmp.to_string_lossy()), ("USER", &user), ("LANG", "C")];
            let cancel = std::sync::atomic::AtomicBool::new(false);
            let iso = sys::Isolated { env: &env, cwd: &repo, stdin: "", limit: Duration::from_secs(120), cancel: &cancel };
            let mut log = Vec::new();
            let code = sys::run_isolated(Path::new("/bin/bash"), &[&repo.join("install.sh").to_string_lossy()], &iso, &mut |l| log.push(l));
            if broken_settings {
                // Since P0, an unreadable settings.json stops install.sh before it writes anything.
                assert_ne!(code, 0, "{}", log.join("\n"));
                assert!(log.iter().any(|l| l.contains("left untouched")), "{}", log.join("\n"));
                assert!(!calls.exists(), "nothing loaded in launchd");
            } else {
                assert_eq!(code, 0, "{}", log.join("\n"));
                assert!(h.home.join(".claude/hooks/brain/session-recap.py").is_file(), "install.sh went through");
                assert!(fs::read_to_string(&calls).unwrap().contains("bootstrap"), "launchctl was the fake one");
            }

            let report = settle(snap, &h.home);
            eprintln!("install.sh (settings.json {}): {:?}", if broken_settings { "broken" } else { "valid" }, report.paths());
            assert!(report.failed.is_empty(), "{report:?}");
            let after = snapshot_of(&h);
            for (rel, bytes) in &before {
                let now = after.iter().find(|(r, _)| r == rel).map(|(_, b)| b);
                match rel.as_str() {
                    ".claude/CLAUDE.md" => assert!(now.is_some_and(|b| b.starts_with(&claude_md)), "{rel}"),
                    // A valid settings.json may gain our hooks, and nothing else.
                    SETTINGS if !broken_settings => {
                        assert_eq!(now.map(|b| settings_change(bytes, b)), Some(None), "{rel}");
                    }
                    _ => assert_eq!(now, Some(bytes), "{rel} changed"),
                }
            }
        }
    }
}
