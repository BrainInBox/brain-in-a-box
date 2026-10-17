//! The configuration guard: around every run of the brain step and every repair,
//! what the user already had in ~/.claude, their scheduled jobs (LaunchAgents on a Mac,
//! systemd's user units on Linux) and their shell files comes out exactly as it went in.
//! When Codex is one of the agents, its folders too (~/.codex, the CODEX_HOME agents.json records)
//! and ~/.agents/skills. With Claude alone the installers do not write there, and the guard leaves
//! them to the user's own Codex, as before the choice existed.
//!
//! `take` copies every protected file into a private folder before the step, `check`
//! compares after it, `restore` puts back, byte for byte, whatever the step should
//! not have touched. An install.sh that overwrote someone's own hooks (named like
//! ours) is what this is for: no installer is trusted, ours included.

use std::collections::{HashMap, HashSet};
use std::ffi::OsStr;
use std::fmt;
use std::fs;
use std::io;
use std::path::{Component, Path, PathBuf};
use std::sync::LazyLock;
use std::time::{SystemTime, UNIX_EPOCH};

use regex::Regex;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

use crate::steps::write_atomic;
use crate::{agents, perms};

const SETTINGS: &str = ".claude/settings.json";
/// The old bytes must stay at the start: install.sh appends to CLAUDE.md, Bun's
/// installer to the shell files. Nothing may rewrite them.
const APPEND_ONLY: &[&str] =
    &[".claude/CLAUDE.md", ".zshrc", ".zprofile", ".bashrc", ".bash_profile", ".profile", ".gitconfig"];
/// install.sh writes gbq there, where some people already keep their own.
const SINGLE: &[&str] = &[".local/bin/gbq"];
const HOOKS: &str = ".claude/hooks";
const SKILLS: &str = ".claude/skills";
/// Codex's own files the kit writes in, in each of Codex's folders (engine/codex/wire.py): hooks.json,
/// like Claude's settings; config.toml, where only the kit's approvals may appear; the global
/// instructions, appended to; the rules (*.rules), the kit's own among the user's.
const CODEX_DEFAULT: &str = ".codex";
const CODEX_HOOKS: &str = "hooks.json";
const CODEX_CONFIG: &str = "config.toml";
const CODEX_APPEND_ONLY: &[&str] = &["AGENTS.md", "AGENTS.override.md"];
const CODEX_RULES: &str = "rules";
/// Where install_skills.py puts the kit's skills for Codex, where people keep theirs too.
const AGENT_SKILLS: &str = ".agents/skills";
/// The user's scheduled jobs, where install.sh writes the kit's.
#[cfg(not(target_os = "linux"))]
const AGENTS: (&str, &[&str]) = ("Library/LaunchAgents", &[".plist"]);
#[cfg(target_os = "linux")]
const AGENTS: (&str, &[&str]) = (".config/systemd/user", &[".service", ".timer"]);
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

/// What install.sh and install.ps1 wrote (path -> sha256).
const INSTALLED: &str = ".brain-in-a-box/installed.json";
/// The kit's sources the last install ran from. install.ps1 copies their hooks as they are:
/// one still exactly like its source there is ours, from before installed.json.
const SOURCES: &str = ".brain-in-a-box/src/engine";
const SOURCE_DIRS: &[&str] = &["hooks", "nightly"];
/// Where the installers put the kit's hooks, and what settings.json runs them from.
const KIT_HOOKS: &str = ".claude/hooks/brain";
/// install.ps1's gbq for Git Bash holds this PC's paths (Python, the brain), so no source
/// matches it: its first lines say it is ours.
const GBQ_MARK: (&str, &[u8]) = (".local/bin/gbq", b"#!/bin/sh\n# brain-in-a-box : gbq pour Git Bash");
/// install_skills.py's lists, one per skills folder: skill name -> hash of the whole folder.
const SKILLS_MANIFESTS: &[(&str, &str)] = &[(".brain-in-a-box/skills.json", SKILLS), (".brain-in-a-box/skills-codex.json", AGENT_SKILLS)];
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
    /// Claude's settings, or Codex's hooks.json: still a JSON object, every hook still there, every
    /// other key unchanged.
    Settings,
    /// Codex's config.toml: only the kit's approvals added (codex_config_change).
    CodexConfig,
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
    /// The folders of the bundled skills still exactly as install_skills.py left them.
    our_skills: HashSet<PathBuf>,
    /// Codex's folders, from home when they are in it.
    codex_homes: Vec<PathBuf>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Violation {
    /// "~/.claude/hooks/brain/correction-detector.py"
    pub path: String,
    /// What happened to it, in French: "réécrit", "supprimé"...
    pub what: &'static str,
    /// Index in `Snapshot::saved`; None for a file of settings the step created wrong (its path).
    at: Result<usize, PathBuf>,
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
        Ok(rel) => format!("~/{}", slashed(rel)),
        Err(_) => p.display().to_string(),
    }
}

/// A path from the home folder written with '/', on Windows too: the same file always
/// has the same name in the lists and in what the user reads.
fn slashed(rel: &Path) -> String {
    rel.components().map(|c| c.as_os_str().to_string_lossy()).collect::<Vec<_>>().join("/")
}

/// A protected path's name in the lists: from home with '/', or whole when it is elsewhere (a
/// CODEX_HOME out of the home folder), as the installers' manifest writes it.
fn key(rel: &Path) -> String {
    if rel.is_absolute() {
        rel.to_string_lossy().into_owned()
    } else {
        slashed(rel)
    }
}

/// As the user reads it: "~/.claude/settings.json", or the whole path.
fn shown(rel: &Path) -> String {
    if rel.is_absolute() {
        rel.display().to_string()
    } else {
        format!("~/{}", slashed(rel))
    }
}

/// Codex's folders when the agents of now list Codex (agents.json's, or a choice made in the window
/// that the installer is about to record): ~/.codex and their CODEX_HOME. From home when they are in
/// it, once each. None with Claude alone.
fn codex_homes(home: &Path) -> Vec<PathBuf> {
    let agents = agents::current(home);
    if !agents.lists(agents::Engine::Codex) {
        return Vec::new();
    }
    let mut homes: Vec<PathBuf> = Vec::new();
    let mut seen = HashSet::new();
    for h in std::iter::once(home.join(CODEX_DEFAULT)).chain(agents.codex_home) {
        if seen.insert(fs::canonicalize(&h).unwrap_or_else(|_| h.clone())) {
            homes.push(h.strip_prefix(home).map_or_else(|_| h.clone(), Path::to_path_buf));
        }
    }
    homes
}

// ── Before ──────────────────────────────────────────────────────────────────

/// Copies every protected file of `home` into a fresh private folder under `store`.
/// Fails rather than leave a file unprotected: the step must not run then.
pub fn take(home: &Path, store: &Path) -> Result<Snapshot, String> {
    take_with(home, store, codex_homes(home))
}

fn take_with(home: &Path, store: &Path, codex_homes: Vec<PathBuf>) -> Result<Snapshot, String> {
    let dir = new_dir(store).map_err(|e| format!("dossier de la garde : {e}"))?;
    let mut saved = Vec::new();
    for (rel, rule) in protected(home, &codex_homes) {
        match save(home, &rel, rule, &dir, saved.len()) {
            Ok(Some(s)) => saved.push(s),
            Ok(None) => {}
            Err(e) => {
                let _ = fs::remove_dir_all(&dir);
                return Err(format!("{} : {e}", shown(&rel)));
            }
        }
    }
    let mut ours: HashSet<(String, String)> = hashes(home, &home.join(INSTALLED)).into_iter().collect();
    ours.extend(hashes(home, &store.join(WRITTEN)));
    ours.extend(shipped(home));
    ours.extend(marked(home));
    let our_skills = untouched_skills(home);
    Ok(Snapshot { store: store.to_path_buf(), dir, saved, ours, our_skills, codex_homes })
}

/// Every protected path that exists now, with its rule.
fn protected(home: &Path, codex_homes: &[PathBuf]) -> Vec<(PathBuf, Rule)> {
    let mut out = vec![(PathBuf::from(SETTINGS), Rule::Settings)];
    out.extend(APPEND_ONLY.iter().map(|p| (PathBuf::from(p), Rule::Append)));
    out.extend(SINGLE.iter().map(|p| (PathBuf::from(p), Rule::Same)));
    let mut hooks = Vec::new();
    walk(home, Path::new(HOOKS), 0, &mut hooks);
    out.extend(hooks.into_iter().map(|p| (p, Rule::Same)));
    // Codex's skills folder goes with Codex's folders: guarded only when Codex is one of the agents.
    let skill_dirs: &[&str] = if codex_homes.is_empty() { &[SKILLS] } else { &[SKILLS, AGENT_SKILLS] };
    for skills in skill_dirs {
        for skill in names_in(&home.join(skills)) {
            out.push((Path::new(skills).join(skill).join("SKILL.md"), Rule::Same));
        }
    }
    let (agents, endings) = AGENTS;
    for agent in names_in(&home.join(agents)).into_iter().filter(|n| endings.iter().any(|e| n.to_string_lossy().ends_with(e))) {
        out.push((Path::new(agents).join(agent), Rule::Same));
    }
    for codex in codex_homes {
        out.extend([(codex.join(CODEX_HOOKS), Rule::Settings), (codex.join(CODEX_CONFIG), Rule::CodexConfig)]);
        out.extend(CODEX_APPEND_ONLY.iter().map(|f| (codex.join(f), Rule::Append)));
        let rules = codex.join(CODEX_RULES);
        for rule in names_in(&home.join(&rules)).into_iter().filter(|n| n.to_string_lossy().ends_with(".rules")) {
            out.push((rules.join(rule), Rule::Same));
        }
    }
    out.retain(|(rel, _)| fs::symlink_metadata(home.join(rel)).is_ok());
    out
}

/// The files of settings a step may create: Claude's settings.json, and Codex's hooks.json and
/// config.toml in each of its folders.
fn creatable(codex_homes: &[PathBuf]) -> Vec<(PathBuf, Rule)> {
    let mut out = vec![(PathBuf::from(SETTINGS), Rule::Settings)];
    for codex in codex_homes {
        out.extend([(codex.join(CODEX_HOOKS), Rule::Settings), (codex.join(CODEX_CONFIG), Rule::CodexConfig)]);
    }
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
            Some(Kept { file, mode: perms::mode(&m) })
        }
        // A folder, or a link to nothing: only the link itself is compared.
        _ => None,
    };
    Ok(Some(Saved { rel: rel.to_path_buf(), rule, link, content }))
}

/// A fresh private folder under `store`, named "<seconds>-<n>". The oldest go.
fn new_dir(store: &Path) -> io::Result<PathBuf> {
    perms::create_dir_all(store)?;
    if !fs::symlink_metadata(store)?.is_dir() {
        return Err(io::Error::other("ce n'est pas un vrai dossier"));
    }
    let secs = SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_secs());
    for n in 1..100 {
        let dir = store.join(format!("{secs}-{n}"));
        match perms::create_dir(&dir) {
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
    perms::create_new(path, 0o600)?.write_all(bytes)
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
                None => Path::new(&path).strip_prefix(home).map_or(path.clone(), slashed),
            };
            is_sha256(&sha).then_some((rel, sha))
        })
        .collect()
}

/// (path from home, sha256) of the kit's hooks as the last install's sources hold them, at
/// the place install.ps1 copies them to. Every Windows install made before installed.json
/// existed is told apart from the user's edit that way; on a Mac install.sh fills paths
/// into its hooks, so this matches nothing there and its manifest does the job.
fn shipped(home: &Path) -> Vec<(String, String)> {
    let mut out = Vec::new();
    for dir in SOURCE_DIRS {
        let dir = home.join(SOURCES).join(dir);
        for name in names_in(&dir) {
            let file = dir.join(&name);
            if !fs::symlink_metadata(&file).is_ok_and(|m| m.is_file()) {
                continue;
            }
            if let Some(sha) = sha256_file(&file) {
                out.push((slashed(&Path::new(KIT_HOOKS).join(&name)), sha));
            }
        }
    }
    out
}

/// install.ps1's gbq, as it wrote it (see GBQ_MARK).
fn marked(home: &Path) -> Option<(String, String)> {
    let (rel, mark) = GBQ_MARK;
    let file = home.join(rel);
    let bytes = fs::read(&file).ok().filter(|b| b.starts_with(mark))?;
    fs::symlink_metadata(&file).is_ok_and(|m| m.is_file()).then(|| (rel.to_string(), hex(&Sha256::digest(&bytes))))
}

fn is_sha256(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit())
}

/// The folders of the skills install_skills.py listed (Claude's, Codex's) that still hash as it wrote them.
fn untouched_skills(home: &Path) -> HashSet<PathBuf> {
    let mut out = HashSet::new();
    for (manifest, skills) in SKILLS_MANIFESTS {
        let Ok(text) = fs::read_to_string(home.join(manifest)) else { continue };
        let Ok(Value::Object(map)) = serde_json::from_str::<Value>(&text) else { continue };
        out.extend(
            map.into_iter()
                .filter(|(name, _)| is_plain_name(name))
                .map(|(name, hash)| (Path::new(skills).join(name), hash))
                .filter(|(rel, hash)| {
                    let root = home.join(rel);
                    fs::symlink_metadata(&root).is_ok_and(|m| m.is_dir()) && tree_hash(&root).is_some_and(|h| hash.as_str() == Some(h.as_str()))
                })
                .map(|(rel, _)| rel),
        );
    }
    out
}

fn is_plain_name(name: &str) -> bool {
    let mut parts = Path::new(name).components();
    matches!((parts.next(), parts.next()), (Some(Component::Normal(_)), None))
}

/// install_skills.py's `tree_hash`: sha256 over each file's path from `root` (with '/',
/// `as_posix()`) and its bytes, both followed by a NUL, files sorted as Python sorts
/// paths (by parts; on Windows without regard to case); linked folders are not entered,
/// linked files are read.
fn tree_hash(root: &Path) -> Option<String> {
    let mut files = Vec::new();
    tree_files(root, Path::new(""), &mut files);
    files.sort_by_cached_key(|p| sort_key(p));
    let mut hash = Sha256::new();
    let mut size = 0;
    for rel in &files {
        let bytes = fs::read(root.join(rel)).ok()?;
        size += bytes.len();
        if size > MAX_TREE {
            return None;
        }
        hash.update(slashed(rel).as_bytes());
        hash.update([0]);
        hash.update(&bytes);
        hash.update([0]);
    }
    Some(hex(&hash.finalize()))
}

#[cfg(not(windows))]
fn sort_key(rel: &Path) -> Vec<std::ffi::OsString> {
    rel.components().map(|c| c.as_os_str().to_os_string()).collect()
}

#[cfg(windows)]
fn sort_key(rel: &Path) -> Vec<String> {
    rel.components().map(|c| c.as_os_str().to_string_lossy().to_lowercase()).collect()
}

fn tree_files(root: &Path, rel: &Path, out: &mut Vec<PathBuf>) {
    let Ok(entries) = fs::read_dir(root.join(rel)) else { return };
    for e in entries.filter_map(Result::ok) {
        let name = e.file_name();
        if SKILL_JUNK.iter().any(|j| name == OsStr::new(j)) {
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

pub(crate) fn sha256_file(path: &Path) -> Option<String> {
    let mut file = fs::File::open(path).ok()?;
    let mut hash = Sha256::new();
    io::copy(&mut file, &mut hash).ok()?;
    Some(hex(&hash.finalize()))
}

/// Lowercase hexadecimal, as shasum and hashlib write it.
pub(crate) fn hex(bytes: &[u8]) -> String {
    use std::fmt::Write;
    bytes.iter().fold(String::with_capacity(bytes.len() * 2), |mut out, b| {
        let _ = write!(out, "{b:02x}");
        out
    })
}

// ── After ───────────────────────────────────────────────────────────────────

impl Snapshot {
    /// A file we wrote ourselves and nobody edited since may be rewritten.
    fn was_ours(&self, s: &Saved, k: &Kept) -> bool {
        if skill_of(&s.rel).is_some_and(|dir| self.our_skills.contains(dir)) {
            return true;
        }
        let key = key(&s.rel);
        self.ours.iter().any(|(p, _)| *p == key)
            && sha256_file(&k.file).is_some_and(|h| self.ours.contains(&(key.clone(), h)))
    }
}

/// The skill's folder, for "<skills>/<name>/SKILL.md" in Claude's or Codex's skills.
fn skill_of(rel: &Path) -> Option<&Path> {
    let dir = rel.parent()?;
    let skills = dir.parent()?;
    let named = matches!(dir.components().next_back(), Some(Component::Normal(_)));
    (rel.file_name()? == "SKILL.md" && named && [SKILLS, AGENT_SKILLS].iter().any(|s| skills == Path::new(s))).then_some(dir)
}

/// What the step changed that it should not have.
pub fn check(snap: &Snapshot, home: &Path) -> Vec<Violation> {
    let mut found: Vec<Violation> = snap
        .saved
        .iter()
        .enumerate()
        .filter_map(|(i, s)| Some(Violation { path: shown(&s.rel), what: changed(snap, home, s)?, at: Ok(i) }))
        .collect();
    for (rel, rule) in creatable(&snap.codex_homes) {
        if snap.saved.iter().any(|s| s.rel == rel) {
            continue;
        }
        let Ok(bytes) = fs::read(home.join(&rel)) else { continue };
        let what = match rule {
            Rule::CodexConfig => codex_config_change(b"", &bytes, rel.parent().unwrap_or(&rel), home),
            _ => created_settings(&bytes),
        };
        if let Some(what) = what {
            found.push(Violation { path: shown(&rel), what, at: Err(rel) });
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
        Rule::Settings => settings_change(&before, &after, home),
        Rule::CodexConfig => codex_config_change(&before, &after, s.rel.parent().unwrap_or(&s.rel), home),
    };
    // A file of ours rewritten as allowed may change mode too; nothing else may.
    let mode_kept = perms::mode(&now) == k.mode || (s.rule == Rule::Same && before != after);
    content.or((!mode_kept).then_some("droits modifiés"))
}

fn settings_change(before: &[u8], after: &[u8], home: &Path) -> Option<&'static str> {
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
    // One of the kit's hooks may be re-pointed (another Python, the exec form instead of the
    // shell one) or lose a second copy: install.ps1 does both. Still run for the same event,
    // it is still there.
    let kit_kept: HashSet<(&str, String)> =
        kept.iter().filter_map(|(event, _, h)| Some((event.as_str(), kit_hook(h, home)?))).collect();
    let still_run = |event: &str, h: &Value| kit_hook(h, home).is_some_and(|name| kit_kept.contains(&(event, name)));
    if hook_list(&old).iter().any(|(event, matcher, h)| !kept.contains(&(event.clone(), matcher.clone(), h.clone())) && !still_run(event, h)) {
        return Some("hooks retirés");
    }
    None
}

/// The kit's hook a settings entry runs ("correction-detector.py"), whatever runs it: the
/// shell form ("python3 /Users/jo/.claude/hooks/brain/x.py", "~/.claude/hooks/brain/x.py") or
/// the exec form (python.exe, the script in "args"), with any Python; Codex's forms too
/// ("<hook> --agent codex", "& '<python.exe>' '<hook>' --agent codex"). None for anything else.
fn kit_hook(hook: &Value, home: &Path) -> Option<String> {
    let mut text = hook.get("command")?.as_str()?.to_string();
    for arg in hook.get("args").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str) {
        text.push(' ');
        text.push_str(arg);
    }
    kit_hook_in(&text, home)
}

fn kit_hook_in(text: &str, home: &Path) -> Option<String> {
    // Windows paths have either slash and any case.
    let plain = |s: &str| s.replace('\\', "/").to_lowercase();
    let text = plain(text);
    let home = plain(&home.to_string_lossy());
    let rest = [home.as_str(), "~", "$home", "${home}", "%userprofile%"].iter().find_map(|root| {
        let at = format!("{}/{KIT_HOOKS}/", root.trim_end_matches('/'));
        text.find(&at).map(|i| &text[i + at.len()..])
    })?;
    let name: String = rest.chars().take_while(|c| !c.is_whitespace() && !matches!(c, '"' | '\'' | '/')).collect();
    (!name.is_empty()).then_some(name)
}

/// A table header of Codex's approvals, its key: [hooks.state."<key>"], the key as wire.py writes it.
static APPROVAL: LazyLock<Regex> = LazyLock::new(|| Regex::new(r#"^\[hooks\.state\.("(?:[^"\\\x00-\x1f]|\\.)*")\]$"#).unwrap());
/// What an approval holds: the hash of the hook's command (wire.py's trust()).
static TRUSTED_HASH: LazyLock<Regex> = LazyLock::new(|| Regex::new(r#"^trusted_hash = "sha256:[0-9a-f]{64}"$"#).unwrap());

/// What a step may change in Codex's config.toml, as engine/codex/wire.py's approve() does: tables
/// [hooks.state."<key>"] added at its end, each with one hash and approving a hook of the kit's where
/// this folder's hooks.json has it now; in such a table already there, its hash and the comment naming
/// the command it approves (a hook of the kit's moved to another Python). Nothing else: no other
/// setting, no approval of a hook that is not the kit's, nothing of the user's changed or removed.
fn codex_config_change(before: &[u8], after: &[u8], codex_home: &Path, home: &Path) -> Option<&'static str> {
    if before == after {
        return None;
    }
    let Ok(old) = std::str::from_utf8(before) else { return Some("réécrit alors qu'il était illisible") };
    let Ok(new) = std::str::from_utf8(after) else { return Some("réglages modifiés") };
    let old: Vec<&str> = if old.is_empty() { Vec::new() } else { old.split('\n').collect() };
    let new: Vec<&str> = new.split('\n').collect();
    if new.len() < old.len() {
        return Some("réglages modifiés");
    }
    let approval = |line: &str| APPROVAL.captures(line.trim()).and_then(|c| serde_json::from_str::<String>(&c[1]).ok());
    let kit = |key: &str| kit_approval(key, codex_home, home);
    let mut table: Option<String> = None;
    for (a, b) in old.iter().zip(&new) {
        if b.trim_start().starts_with('[') {
            table = approval(b);
        }
        if a == b {
            continue;
        }
        let both = |ok: &dyn Fn(&str) -> bool| ok(a.trim()) && ok(b.trim());
        let hash_or_note = both(&|l| TRUSTED_HASH.is_match(l)) || both(&|l| l.starts_with("# command: "));
        if !(hash_or_note && table.as_deref().is_some_and(kit)) {
            return Some("réglages modifiés");
        }
    }
    let mut keys: HashSet<String> = old.iter().filter_map(|l| approval(l)).collect();
    // The table being added, and how many hashes it holds.
    let mut adding: Option<usize> = None;
    for line in &new[old.len()..] {
        let t = line.trim();
        if t.is_empty() || t.starts_with('#') {
            continue;
        }
        if let Some(key) = approval(t) {
            if adding.is_some_and(|n| n != 1) || !kit(&key) || !keys.insert(key) {
                return Some("réglages modifiés");
            }
            adding = Some(0);
        } else if TRUSTED_HASH.is_match(t) && adding.is_some() {
            adding = adding.map(|n| n + 1);
        } else {
            return Some("réglages modifiés");
        }
    }
    adding.is_some_and(|n| n != 1).then_some("réglages modifiés")
}

/// "<hooks.json>:<event>:<group>:<entry>" names one of the kit's hooks in this folder's hooks.json as
/// it is now: the hooks.json Codex reaches (through a link or not), a group without a matcher, an
/// entry every command of which runs the same hook of the kit's.
fn kit_approval(key: &str, codex_home: &Path, home: &Path) -> bool {
    let mut parts = key.rsplitn(4, ':');
    let (Some(entry), Some(group), Some(event), Some(path)) = (parts.next(), parts.next(), parts.next(), parts.next()) else { return false };
    let event = match event {
        "user_prompt_submit" => "UserPromptSubmit",
        "stop" => "Stop",
        "session_start" => "SessionStart",
        _ => return false,
    };
    let (Ok(group), Ok(entry)) = (group.parse::<usize>(), entry.parse::<usize>()) else { return false };
    let hooks_json = home.join(codex_home).join(CODEX_HOOKS);
    let same = Path::new(path).is_absolute() && matches!((fs::canonicalize(path), fs::canonicalize(&hooks_json)), (Ok(a), Ok(b)) if a == b);
    let Some(Value::Object(d)) = same.then(|| fs::read(&hooks_json).ok().and_then(|b| serde_json::from_slice(&b).ok())).flatten() else {
        return false;
    };
    let Some(group) = d.get("hooks").and_then(|h| h.get(event)).and_then(|a| a.get(group)) else { return false };
    if group.get("matcher").is_some() {
        return false;
    }
    let Some(Value::Object(h)) = group.get("hooks").and_then(|h| h.get(entry)) else { return false };
    let named: Vec<Option<String>> =
        ["command", "commandWindows", "command_windows"].iter().filter_map(|k| h.get(*k)).map(|c| c.as_str().and_then(|c| kit_hook_in(c, home))).collect();
    named.first().is_some_and(|first| first.is_some() && named.iter().all(|n| n == first))
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
        let _ = match &v.at {
            Ok(i) => put_back(home, &snap.saved[*i]),
            Err(created) => remove_file_only(&home.join(created)),
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
            perms::symlink(target, &path)?;
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
/// next run may replace them (the installers' own list is installed.json).
fn remember(snap: &Snapshot, home: &Path) {
    let before: HashMap<&Path, &Saved> = snap.saved.iter().map(|s| (s.rel.as_path(), s)).collect();
    let file = snap.store.join(WRITTEN);
    let mut written: Map<String, Value> = hashes(home, &file).into_iter().map(|(p, h)| (p, Value::String(h))).collect();
    let mut grew = false;
    for (rel, rule) in protected(home, &snap.codex_homes) {
        let path = home.join(&rel);
        if rule != Rule::Same || !fs::symlink_metadata(&path).is_ok_and(|m| m.is_file()) {
            continue;
        }
        let new = match before.get(rel.as_path()) {
            Some(s) => s.content.as_ref().is_none_or(|k| fs::read(&k.file).ok() != fs::read(&path).ok()),
            None => true,
        };
        if let Some(hash) = new.then(|| sha256_file(&path)).flatten() {
            written.insert(key(&rel), Value::String(hash));
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
    #[cfg(unix)]
    use std::os::unix::fs::{symlink, PermissionsExt};
    #[cfg(not(windows))]
    use std::time::Duration;

    #[cfg(not(windows))]
    use crate::sys;

    struct Home {
        _tmp: tempfile::TempDir,
        #[cfg(not(windows))]
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
            Home {
                _tmp: tmp,
                #[cfg(not(windows))]
                root,
                home,
                store,
            }
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
        #[cfg(not(windows))]
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
    /// The user's own scheduled job, next to where install.sh writes the kit's.
    #[cfg(not(target_os = "linux"))]
    const USER_AGENT: &str = "Library/LaunchAgents/com.me.backup.plist";
    #[cfg(target_os = "linux")]
    const USER_AGENT: &str = ".config/systemd/user/backup.timer";
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
        perms::set_mode(&h.home.join(".claude/hooks/guard.sh"), 0o755).unwrap();
        h.write(".claude/settings.json", USER_SETTINGS);
        h.write(".claude/CLAUDE.md", "# Mes règles\n");
        h.write(".zshrc", "export PATH=$HOME/bin:$PATH\n");
        h.write(USER_AGENT, "<plist>mine</plist>\n");
        h.write(".local/bin/gbq", "#!/bin/sh\n# my gbq\n");
    }

    fn snapshot_of(h: &Home) -> Vec<(String, Vec<u8>)> {
        let mut files = Vec::new();
        for (rel, _) in protected(&h.home, &[PathBuf::from(CODEX_DEFAULT)]) {
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

    #[cfg(not(windows))]
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

    #[cfg(not(windows))]
    #[test]
    fn rewritten_or_deleted_hooks_come_back_byte_for_byte() {
        let h = Home::new();
        personal(&h);
        let before = snapshot_of(&h);
        let snap = h.take();
        h.install(&format!(
            r#"printf 'ours\n' > "$H/.claude/hooks/brain/correction-detector.py.tmp"
               mv "$H/.claude/hooks/brain/correction-detector.py.tmp" "$H/.claude/hooks/brain/correction-detector.py"
               chmod +x "$H/.claude/hooks/brain/correction-detector.py"
               rm "$H/.claude/hooks/brain/my-own.py"
               chmod 600 "$H/.claude/hooks/guard.sh"
               echo '<plist>ours</plist>' > "$H/{USER_AGENT}"
               echo '# our gbq' > "$H/.local/bin/gbq"
               echo 'nouveau' > "$H/.claude/CLAUDE.md"
               echo 'export X=1' > "$H/.zshrc""#
        ));
        let found = check(&snap, &h.home);
        let mut named: Vec<String> = found.iter().map(ToString::to_string).collect();
        named.sort();
        let agent = format!("~/{USER_AGENT} (réécrit)");
        let mut expected = vec![
            "~/.claude/CLAUDE.md (début modifié)",
            "~/.claude/hooks/brain/correction-detector.py (réécrit)",
            "~/.claude/hooks/brain/my-own.py (supprimé)",
            "~/.claude/hooks/guard.sh (droits modifiés)",
            "~/.local/bin/gbq (réécrit)",
            "~/.zshrc (début modifié)",
            agent.as_str(),
        ];
        expected.sort();
        assert_eq!(named, expected);
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

    /// What install.ps1 makes of an older install's settings: the Python it finds now, the
    /// exec form, one copy of each hook. The same hooks still run: nothing to put back.
    #[test]
    fn a_kit_hook_may_change_python_or_form_but_not_go() {
        use serde_json::json;
        let home = Path::new(if cfg!(windows) { r"C:\Users\Jo Doe" } else { "/Users/Jo Doe" });
        let script = |name: &str| home.join(".claude").join("hooks").join("brain").join(name).to_string_lossy().into_owned();
        let exec = |py: &str, name: &str| json!({ "type": "command", "command": py, "args": [script(name)] });
        let settings = |hooks: Value| serde_json::to_vec(&json!({ "env": { "A": "1" }, "hooks": hooks })).unwrap();
        let mine = json!([{ "matcher": "Bash", "hooks": [{ "type": "command", "command": "~/.claude/hooks/guard.sh" }] }]);
        let old_py = r"C:\Python311\python.exe";
        let new_py = r"C:\Users\Jo Doe\AppData\Local\Programs\Python\Python312\python.exe";
        let before = settings(json!({
            "UserPromptSubmit": [{ "matcher": "", "hooks": [exec(old_py, "correction-detector.py")] }],
            "Stop": [
                { "matcher": "", "hooks": [{ "type": "command", "command": format!("\"{old_py}\" \"{}\"", script("session-logger.py")) }] },
                { "matcher": "x", "hooks": [exec(old_py, "session-logger.py")] }
            ],
            "SessionStart": [{ "hooks": [{ "type": "command", "command": "python3 ~/.claude/hooks/brain/gbrain-update-check.py" }] }],
            "PreToolUse": mine
        }));
        let after = |ups: Value, stop: Value, pre: Value| {
            settings(json!({
                "UserPromptSubmit": ups,
                "Stop": stop,
                "SessionStart": [{ "hooks": [exec(new_py, "gbrain-update-check.py")] }],
                "PreToolUse": pre
            }))
        };
        let ups = json!([{ "matcher": "", "hooks": [exec(new_py, "correction-detector.py")] }]);
        let stop = json!([{ "matcher": "", "hooks": [exec(new_py, "session-logger.py")] }]);
        assert_eq!(settings_change(&before, &after(ups.clone(), stop.clone(), mine.clone()), home), None);

        let cases = [
            ("a kit hook gone", after(json!([]), stop.clone(), mine.clone())),
            ("a kit hook under another event", after(json!([]), json!([{ "hooks": [exec(new_py, "session-logger.py"), exec(new_py, "correction-detector.py")] }]), mine.clone())),
            ("the user's own hook gone", after(ups.clone(), stop.clone(), json!([]))),
        ];
        for (label, now) in cases {
            assert_eq!(settings_change(&before, &now, home), Some("hooks retirés"), "{label}");
        }
        // Another script in the same folder is another hook.
        assert_eq!(kit_hook(&exec(new_py, "correction-detector.py"), home).as_deref(), Some("correction-detector.py"));
        assert_eq!(kit_hook(&json!({ "type": "command", "command": "~/.claude/hooks/guard.sh" }), home), None);
    }

    /// An install made before installed.json (every Windows one until then): a hook still
    /// exactly like the sources it was copied from is the kit's to update; one the user
    /// changed stays theirs.
    #[test]
    fn a_hook_as_the_last_install_left_it_may_be_updated() {
        let h = Home::new();
        h.write(".brain-in-a-box/src/engine/hooks/correction-detector.py", "v1\n");
        h.write(".brain-in-a-box/src/engine/hooks/session-logger.py", "v1\n");
        h.write(".brain-in-a-box/src/engine/nightly/windows-nightly.py", "n1\n");
        h.write(".claude/hooks/brain/correction-detector.py", "v1\n");
        h.write(".claude/hooks/brain/windows-nightly.py", "n1\n");
        h.write(".claude/hooks/brain/session-logger.py", "v1, adapted on this PC\n");
        // install.ps1's Git Bash gbq names the Python and the brain of the day.
        h.write(".local/bin/gbq", "#!/bin/sh\n# brain-in-a-box : gbq pour Git Bash, meme logique que gbq.cmd\nexec '/c/Py311/python'\n");
        let snap = h.take();
        for f in ["correction-detector.py", "windows-nightly.py", "session-logger.py"] {
            h.write(&format!(".claude/hooks/brain/{f}"), "v2\n");
        }
        h.write(".local/bin/gbq", "#!/bin/sh\n# brain-in-a-box : gbq pour Git Bash, meme logique que gbq.cmd\nexec '/c/Py312/python'\n");
        let found: Vec<String> = check(&snap, &h.home).iter().map(ToString::to_string).collect();
        assert_eq!(found, ["~/.claude/hooks/brain/session-logger.py (réécrit)"]);
        settle(snap, &h.home);
        assert_eq!(h.read(".claude/hooks/brain/session-logger.py"), b"v1, adapted on this PC\n");
        assert_eq!(h.read(".claude/hooks/brain/correction-detector.py"), b"v2\n");
    }

    #[cfg(not(windows))]
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

    #[cfg(not(windows))]
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

    #[cfg(not(windows))]
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

    #[cfg(not(windows))]
    fn python_tree_hash(dir: &Path) -> Option<String> {
        // Without Apple's tools, /usr/bin/python3 opens their install window.
        #[cfg(target_os = "macos")]
        crate::detect::clt_dir()?;
        #[cfg(target_os = "linux")]
        sys::find_tool("python3")?;
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

    #[cfg(not(windows))]
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
        let ours = tree_hash(&skill).unwrap();
        if let Some(theirs) = python_tree_hash(&skill) {
            assert_eq!(ours, theirs);
        }

        h.write(".brain-in-a-box/skills.json", &format!(r#"{{"brain-boost":"{ours}","../evil":"{ours}"}}"#));
        h.write(".claude/skills/mine/SKILL.md", "# mine\n");
        let snap = h.take();
        assert_eq!(snap.our_skills, HashSet::from([PathBuf::from(".claude/skills/brain-boost")]));
        h.install(r#"echo v2 > "$H/.claude/skills/brain-boost/SKILL.md"; echo v2 > "$H/.claude/skills/mine/SKILL.md""#);
        let found: Vec<String> = check(&snap, &h.home).iter().map(ToString::to_string).collect();
        assert_eq!(found, ["~/.claude/skills/mine/SKILL.md (réécrit)"]);
        settle(snap, &h.home);
    }

    #[cfg(not(windows))]
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

    #[cfg(not(windows))]
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

    #[cfg(not(windows))]
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
        assert_eq!(skill_of(Path::new(".claude/skills/x/SKILL.md")), Some(Path::new(".claude/skills/x")));
        assert_eq!(skill_of(Path::new(".agents/skills/x/SKILL.md")), Some(Path::new(".agents/skills/x")));
        assert_eq!(skill_of(Path::new(".claude/skills/x/y/SKILL.md")), None);
        assert_eq!(skill_of(Path::new(".claude/skills/x/README.md")), None);
        assert_eq!(skill_of(Path::new("elsewhere/skills/x/SKILL.md")), None);
    }

    /// The incident, replayed on a copy of the real install.sh in a temp home: no
    /// network (gbrain already cloned, bun and gbrain faked), a fake launchctl first
    /// on the PATH (Linux: systemctl and crontab). Without a manifest or a git history,
    /// install.sh still replaces hooks, gbq and LaunchAgents (systemd units) named like
    /// ours (after a backup): the guard puts every one of them back. An unreadable
    /// settings.json stops install.sh before it writes anything. Either way the user's
    /// files come out byte for byte.
    #[cfg(unix)]
    #[test]
    fn the_real_install_sh_cannot_damage_the_users_files() {
        #[cfg(target_os = "macos")]
        if crate::detect::clt_dir().is_none() {
            eprintln!("no Apple command line tools (git, python3): skipped");
            return;
        }
        #[cfg(target_os = "linux")]
        if sys::find_tool("git").is_none() || sys::find_tool("python3").is_none() {
            eprintln!("no git or python3: skipped");
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
            let calls = h.root.join("scheduler-calls");
            let record = format!("echo \"$0 $@\" >> '{}'", calls.display());
            // Linux: the user's systemd answers when the machine runs systemd (else cron), as
            // in a desktop session; crontab has nothing yet.
            #[cfg(target_os = "linux")]
            let scheduler = [
                ("systemctl", record.clone()),
                ("crontab", format!("[ \"$1\" = -l ] && {{ echo 'no crontab for me' >&2; exit 1; }}; {record}")),
                ("claude", "echo 2.1.296".into()),
            ];
            #[cfg(target_os = "macos")]
            let scheduler = [("launchctl", record.clone())];
            for (name, body) in scheduler.into_iter().chain([
                ("bun", "exit 0".into()),
                ("curl", "echo 'no network in this test' >&2; exit 7".into()),
            ]) {
                fs::create_dir_all(&fake).unwrap();
                fs::write(fake.join(name), format!("#!/bin/sh\n{body}\n")).unwrap();
                fs::set_permissions(fake.join(name), fs::Permissions::from_mode(0o755)).unwrap();
            }
            h.write(".bun/bin/gbrain", "#!/bin/sh\nexit 0\n");
            fs::set_permissions(h.home.join(".bun/bin/gbrain"), fs::Permissions::from_mode(0o755)).unwrap();
            fs::create_dir_all(h.home.join("DEV/gbrain/.git")).unwrap();

            let user = sys::output_text(Path::new("/usr/bin/id"), &["-un"], Duration::from_secs(5)).unwrap();
            personal(&h);
            // Claude alone, next to a Codex of the user's own: nothing of it is touched (not guarded
            // then: what install.sh itself does is compared below).
            personal_codex_files(&h);
            #[cfg(target_os = "macos")]
            h.write(&format!("Library/LaunchAgents/com.{user}.gbrain-nightly.plist"), "<plist>my own nightly</plist>\n");
            #[cfg(target_os = "linux")]
            h.write(".config/systemd/user/gbrain-nightly.timer", "[Timer]\n# my own nightly\n");
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
                assert!(!calls.exists(), "nothing scheduled");
            } else {
                assert_eq!(code, 0, "{}", log.join("\n"));
                assert!(h.home.join(".claude/hooks/brain/session-recap.py").is_file(), "install.sh went through");
                let said = fs::read_to_string(&calls).unwrap();
                #[cfg(target_os = "macos")]
                assert!(said.contains("bootstrap"), "launchctl was the fake one: {said}");
                #[cfg(target_os = "linux")]
                assert!(said.contains("systemctl --user enable") || said.contains("crontab "), "the scheduler was the fake one: {said}");
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
                        assert_eq!(now.map(|b| settings_change(bytes, b, &h.home)), Some(None), "{rel}");
                    }
                    _ => assert_eq!(now, Some(bytes), "{rel} changed"),
                }
            }
        }
    }

    // ── Codex's files ───────────────────────────────────────────────────────

    /// Codex chosen, its folder ~/.codex (agents.json, the gate open in this home's copy of the kit).
    fn codex_chosen(h: &Home) {
        crate::agents::tests::plant_agents_py(&crate::agents::tests::installed(&h.home), true);
        let chosen = serde_json::json!({ "version": 1, "agents": ["codex"], "prefere": "codex", "codex_home": h.home.join(".codex") });
        crate::agents::tests::choose(&h.home, &chosen);
    }

    /// The user's own Codex, and Codex chosen.
    fn personal_codex(h: &Home) {
        personal_codex_files(h);
        codex_chosen(h);
    }

    /// The user's own Codex: a hook of theirs in each of two events, their settings and their own
    /// approval of their Stop hook in config.toml, their instructions, rules and a skill.
    fn personal_codex_files(h: &Home) {
        let hooks = serde_json::json!({
            "hooks": {
                "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "/opt/mine/prompt-hook" }] }],
                "Stop": [{ "hooks": [{ "type": "command", "command": "/opt/mine/stop-hook" }] }]
            },
            "mine": { "keep": true }
        });
        h.write(".codex/hooks.json", &serde_json::to_string_pretty(&hooks).unwrap());
        let mine = h.home.join(".codex").join("hooks.json");
        h.write(
            ".codex/config.toml",
            &format!(
                "model = \"gpt-5\"\n\n[projects.\"/Users/jo/code\"]\ntrust_level = \"trusted\"\n\n[hooks.state.{}]\ntrusted_hash = \"sha256:{}\"\n",
                serde_json::to_string(&format!("{}:stop:0:0", mine.display())).unwrap(),
                "a".repeat(64)
            ),
        );
        h.write(".codex/AGENTS.md", "# Mes consignes\n");
        h.write(".codex/rules/default.rules", "prefix_rule(pattern=[\"git\", \"status\"], decision=\"allow\")\n");
        h.write(".agents/skills/mine/SKILL.md", "# mine\n");
    }

    /// engine/codex/wire.py, as install.sh runs it (install.ps1's form on Windows), on this home's Codex.
    fn wire(h: &Home, op: &str) {
        let repo = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let python = crate::agents::tests::python();
        let codex = h.home.join(".codex");
        let hooks = h.home.join(".claude").join("hooks").join("brain");
        let arg = if op == "global" { repo.join("engine/CLAUDE.global.template.md") } else { hooks };
        let (python_text, wire, codex_text, arg_text) =
            (python.to_string_lossy(), repo.join("engine/codex/wire.py"), codex.to_string_lossy(), arg.to_string_lossy());
        let wire_text = wire.to_string_lossy();
        let mut args = vec!["-I", "-B", wire_text.as_ref()];
        if cfg!(windows) {
            args.extend(["--ps1", python_text.as_ref()]);
        }
        args.extend([op, codex_text.as_ref(), arg_text.as_ref()]);
        let home = h.home.to_string_lossy();
        let out = crate::sys::run_with(&python, &args, &[("HOME", &home), ("USERPROFILE", &home)], None, std::time::Duration::from_secs(60));
        assert!(out.ok(), "wire.py {op}: {} {}", out.stdout, out.stderr);
    }

    fn codex_key(h: &Home, event: &str, group: usize, entry: usize) -> String {
        format!("{}:{event}:{group}:{entry}", h.home.join(".codex").join("hooks.json").display())
    }

    fn approval(key: &str, hash: char) -> String {
        format!("[hooks.state.{}]\ntrusted_hash = \"sha256:{}\"\n", serde_json::to_string(key).unwrap(), hash.to_string().repeat(64))
    }

    /// What the kit writes for Codex, by the installers' own wire.py, next to what the user keeps
    /// there: nothing to put back. Its skills and rules land as files of its own.
    #[test]
    fn the_kits_lines_in_codex_files_pass_and_the_users_stay() {
        let h = Home::new();
        personal_codex(&h);
        let before = snapshot_of(&h);
        let snap = h.take();
        for op in ["merge", "approve", "global"] {
            wire(&h, op);
        }
        h.write(".codex/rules/brain-in-a-box.rules", "prefix_rule(pattern=[\"gbq\", \"query\"], decision=\"allow\")\n");
        h.write(".agents/skills/brain-boost/SKILL.md", "# boost\n");
        let config = String::from_utf8(h.read(".codex/config.toml")).unwrap();
        assert_eq!(config.matches("[hooks.state.").count(), 7, "the user's approval and the kit's six: {config}");
        let report = settle(snap, &h.home);
        assert!(report.clean(), "{report:?}");
        for (rel, bytes) in before {
            let now = h.read(&rel);
            match rel.replace('\\', "/").as_str() {
                ".codex/config.toml" | ".codex/AGENTS.md" => assert!(now.starts_with(&bytes), "{rel}"),
                ".codex/hooks.json" => assert_eq!(settings_change(&bytes, &now, &h.home), None),
                _ => assert_eq!(now, bytes, "{rel}"),
            }
        }
        // A second run of the kit changes nothing more; a config.toml it creates holds its tables only.
        let snap = h.take();
        wire(&h, "approve");
        assert!(settle(snap, &h.home).clean());
        // The kit's skill for Codex, as install_skills.py listed it, is the kit's to update; without
        // the list (and the guard's own record of the run that wrote it), it is kept as it is.
        fs::remove_file(h.store.join(WRITTEN)).unwrap();
        let snap = h.take();
        h.write(".agents/skills/brain-boost/SKILL.md", "# boost v2\n");
        assert!(!settle(snap, &h.home).clean());
        let ours = tree_hash(&h.home.join(".agents/skills/brain-boost")).unwrap();
        h.write(".brain-in-a-box/skills-codex.json", &format!(r#"{{"brain-boost":"{ours}"}}"#));
        let snap = h.take();
        h.write(".agents/skills/brain-boost/SKILL.md", "# boost v2\n");
        assert!(settle(snap, &h.home).clean());
        fs::remove_file(h.home.join(".codex/config.toml")).unwrap();
        let snap = h.take();
        wire(&h, "approve");
        assert!(settle(snap, &h.home).clean());
        assert!(h.home.join(".codex/config.toml").is_file());
    }

    /// Anything else a step does to Codex's files is put back, byte for byte: a setting, an approval
    /// of a hook that is not the kit's (the user's, a hash slipped into their table, another
    /// hooks.json), the user's own approval rewritten, a hook removed, a rewritten file.
    #[test]
    fn codex_files_come_back_when_a_step_oversteps() {
        type Edit = Box<dyn Fn(&Home)>;
        // What a step appends to config.toml, written for this home's hooks.json.
        let append = |text: fn(&Home) -> String| -> Edit {
            Box::new(move |h: &Home| {
                let mut c = h.read(".codex/config.toml");
                c.extend_from_slice(text(h).as_bytes());
                fs::write(h.home.join(".codex/config.toml"), c).unwrap();
            })
        };
        let replace = |rel: &'static str, from: String, to: String| -> Edit {
            Box::new(move |h: &Home| {
                let text = String::from_utf8(h.read(rel)).unwrap();
                assert!(text.contains(&from), "{rel}: {from}");
                fs::write(h.home.join(rel), text.replacen(&from, &to, 1)).unwrap();
            })
        };
        fn user_prompt(h: &Home) -> String {
            codex_key(h, "user_prompt_submit", 0, 0)
        }
        fn kit_prompt(h: &Home) -> String {
            codex_key(h, "user_prompt_submit", 1, 0)
        }
        let cases: Vec<(&str, &str, Edit)> = vec![
            ("a setting added", "réglages modifiés", append(|_| "\nmodel_provider = \"elsewhere\"\n".into())),
            // A blank line does not end a TOML table: this hash would be the user's last table's.
            ("a hash in the user's last table", "réglages modifiés", append(|_| format!("\ntrusted_hash = \"sha256:{}\"\n", "b".repeat(64)))),
            ("the user's hook approved", "réglages modifiés", append(|h| format!("\n{}", approval(&user_prompt(h), 'c')))),
            (
                "another hooks.json's hook approved",
                "réglages modifiés",
                append(|h| format!("\n{}", approval(&kit_prompt(h).replace(".codex", ".codex-other"), 'c'))),
            ),
            ("the kit's table twice", "réglages modifiés", append(|h| format!("\n{}{}", approval(&kit_prompt(h), 'c'), approval(&kit_prompt(h), 'd')))),
            (
                "two hashes in a table",
                "réglages modifiés",
                append(|h| format!("\n{}trusted_hash = \"sha256:{}\"\n", approval(&kit_prompt(h), 'c'), "d".repeat(64))),
            ),
            ("the user's approval changed", "réglages modifiés", replace(".codex/config.toml", "a".repeat(64), "e".repeat(64))),
            ("a value of the user's changed", "réglages modifiés", replace(".codex/config.toml", "gpt-5".into(), "gpt-4".into())),
            ("the user's hook removed", "hooks retirés", replace(".codex/hooks.json", "/opt/mine/stop-hook".into(), "/opt/other".into())),
            ("a key of hooks.json changed", "réglages modifiés", replace(".codex/hooks.json", "\"keep\": true".into(), "\"keep\": false".into())),
            ("AGENTS.md rewritten", "début modifié", replace(".codex/AGENTS.md", "Mes".into(), "Nos".into())),
            ("a rule of the user's rewritten", "réécrit", replace(".codex/rules/default.rules", "status".into(), "push".into())),
            ("a skill of the user's rewritten", "réécrit", replace(".agents/skills/mine/SKILL.md", "mine".into(), "ours".into())),
            (
                "a hook of the kit's under a matcher approved",
                "réglages modifiés",
                Box::new(|h: &Home| {
                    let hook = h.home.join(".claude/hooks/brain/correction-detector.py");
                    let mut d: Value = serde_json::from_slice(&h.read(".codex/hooks.json")).unwrap();
                    let entry = serde_json::json!({ "type": "command", "command": format!("{} --agent codex", hook.display()) });
                    d["hooks"]["UserPromptSubmit"].as_array_mut().unwrap().push(serde_json::json!({ "matcher": "x", "hooks": [entry] }));
                    fs::write(h.home.join(".codex/hooks.json"), d.to_string()).unwrap();
                    let mut c = h.read(".codex/config.toml");
                    c.extend_from_slice(format!("\n{}", approval(&codex_key(h, "user_prompt_submit", 2, 0), 'c')).as_bytes());
                    fs::write(h.home.join(".codex/config.toml"), c).unwrap();
                }),
            ),
            (
                "a hook of the kit's that runs something else on Windows approved",
                "réglages modifiés",
                Box::new(|h: &Home| {
                    let mut d: Value = serde_json::from_slice(&h.read(".codex/hooks.json")).unwrap();
                    d["hooks"]["UserPromptSubmit"][1]["hooks"][0]["commandWindows"] = "C:\\elsewhere\\run.exe".into();
                    fs::write(h.home.join(".codex/hooks.json"), d.to_string()).unwrap();
                    let mut c = h.read(".codex/config.toml");
                    c.extend_from_slice(format!("\n{}", approval(&codex_key(h, "user_prompt_submit", 1, 0), 'c')).as_bytes());
                    fs::write(h.home.join(".codex/config.toml"), c).unwrap();
                }),
            ),
        ];
        // Control: the kit's own approval, appended the same way, passes.
        let h = Home::new();
        personal_codex(&h);
        wire(&h, "merge");
        let snap = h.take();
        append(|h| format!("\n{}", approval(&kit_prompt(h), 'c')))(&h);
        assert!(check(&snap, &h.home).is_empty());
        settle(snap, &h.home);
        for (label, what, edit) in cases {
            let h = Home::new();
            personal_codex(&h);
            wire(&h, "merge");
            let before = snapshot_of(&h);
            let snap = h.take();
            edit(&h);
            let found = check(&snap, &h.home);
            assert_eq!(found.iter().map(|v| v.what).collect::<Vec<_>>(), [what], "{label}");
            let report = settle(snap, &h.home);
            assert!(report.failed.is_empty(), "{label}: {report:?}");
            // A hook added to hooks.json may stay (Codex asks before it runs one nobody approved).
            for ((rel, was), (_, now)) in before.iter().zip(&snapshot_of(&h)) {
                match rel.replace('\\', "/").as_str() {
                    ".codex/hooks.json" => assert_eq!(settings_change(was, now, &h.home), None, "{label}"),
                    _ => assert!(was == now, "{label}: {rel}"),
                }
            }
            assert_eq!(snapshot_of(&h).len(), before.len(), "{label}");
        }
    }

    /// The kit's own table, its hash and the comment naming its command changed in place (its hook moved
    /// to another Python, wire.py on Windows): allowed for a hook of the kit's only.
    #[test]
    fn a_kit_approval_may_change_its_hash_in_place() {
        let h = Home::new();
        personal_codex(&h);
        wire(&h, "merge");
        wire(&h, "approve");
        let text = String::from_utf8(h.read(".codex/config.toml")).unwrap();
        let start = text.find(&format!("[hooks.state.{}]", serde_json::to_string(&codex_key(&h, "user_prompt_submit", 1, 0)).unwrap())).unwrap();
        let table: String = text[start..].lines().take(3).flat_map(|l| [l, "\n"]).collect();
        assert!(table.lines().nth(2).is_some_and(|l| l.starts_with("# command: ")), "{table}");
        let mut moved: Vec<String> = table.lines().map(String::from).collect();
        moved[1] = format!("trusted_hash = \"sha256:{}\"", "f".repeat(64));
        moved[2] = "# command: & 'C:\\Python313\\python.exe' '…' --agent codex".into();
        let moved: String = moved.iter().flat_map(|l| [l.as_str(), "\n"]).collect();
        let snap = h.take();
        fs::write(h.home.join(".codex/config.toml"), text.replacen(&table, &moved, 1)).unwrap();
        assert!(check(&snap, &h.home).is_empty());
        settle(snap, &h.home);
        // The kit's table given a second hash in place of its comment: TOML Codex refuses.
        let snap = h.take();
        let now = String::from_utf8(h.read(".codex/config.toml")).unwrap();
        let comment = moved.lines().nth(2).unwrap();
        fs::write(h.home.join(".codex/config.toml"), now.replacen(comment, &format!("trusted_hash = \"sha256:{}\"", "1".repeat(64)), 1)).unwrap();
        assert_eq!(check(&snap, &h.home).first().map(|v| v.what), Some("réglages modifiés"));
        settle(snap, &h.home);
        // A hash slipped in where a setting was: not a hash of the kit's table.
        let snap = h.take();
        let now = String::from_utf8(h.read(".codex/config.toml")).unwrap();
        fs::write(h.home.join(".codex/config.toml"), now.replacen("trust_level = \"trusted\"", &format!("trusted_hash = \"sha256:{}\"", "f".repeat(64)), 1))
            .unwrap();
        assert_eq!(check(&snap, &h.home).first().map(|v| v.what), Some("réglages modifiés"));
        settle(snap, &h.home);
    }

    /// A config.toml or a hooks.json a step creates holds what the kit writes there, or goes.
    #[test]
    fn codex_files_a_step_creates_hold_the_kits_only() {
        let h = Home::new();
        codex_chosen(&h);
        wire(&h, "merge");
        let kit_prompt = codex_key(&h, "user_prompt_submit", 0, 0);
        for (label, bytes, kept) in [
            ("the kit's approval", approval(&kit_prompt, 'c').into_bytes(), true),
            ("a setting", b"model = \"x\"\n".to_vec(), false),
            ("a setting after the kit's approval", format!("{}model = \"x\"\n", approval(&kit_prompt, 'c')).into_bytes(), false),
            ("not text", vec![0xff, 0xfe], false),
        ] {
            let _ = fs::remove_file(h.home.join(".codex/config.toml"));
            let snap = h.take();
            fs::write(h.home.join(".codex/config.toml"), bytes).unwrap();
            let report = settle(snap, &h.home);
            assert_eq!((report.clean(), h.home.join(".codex/config.toml").exists()), (kept, kept), "{label}: {report:?}");
        }
        fs::remove_file(h.home.join(".codex/hooks.json")).unwrap();
        let snap = h.take();
        h.write(".codex/hooks.json", r#"{"hooks": {}, "other": 1}"#);
        let found = check(&snap, &h.home);
        assert_eq!(found.iter().map(|v| (v.path.as_str(), v.what)).collect::<Vec<_>>(), [("~/.codex/hooks.json", "créé avec d'autres réglages que des hooks")]);
        settle(snap, &h.home);
        assert!(!h.home.join(".codex/hooks.json").exists());
    }

    /// Claude alone: Codex's folders are not the step's (install.sh does not touch them then, and
    /// master never looked at them). The user's own Codex writing there meanwhile (a folder trusted,
    /// /hooks, a skill of theirs) is neither reported nor put back, as on master.
    #[test]
    fn with_claude_alone_codexs_folders_are_left_to_the_user() {
        for claude in [None, Some(serde_json::json!({ "version": 1, "agents": ["claude"], "prefere": "claude" }))] {
            let h = Home::new();
            personal_codex_files(&h);
            if let Some(chosen) = &claude {
                crate::agents::tests::plant_agents_py(&crate::agents::tests::installed(&h.home), false);
                crate::agents::tests::choose(&h.home, chosen);
            }
            assert_eq!(codex_homes(&h.home), Vec::<PathBuf>::new(), "{claude:?}");
            let snap = h.take();
            let config = String::from_utf8(h.read(".codex/config.toml")).unwrap().replace("gpt-5", "gpt-4");
            h.write(".codex/config.toml", &config);
            h.write(".agents/skills/mine/SKILL.md", "# changed by the user's Codex\n");
            assert!(check(&snap, &h.home).is_empty(), "{claude:?}");
            assert!(settle(snap, &h.home).clean(), "{claude:?}");
            assert_eq!(String::from_utf8(h.read(".codex/config.toml")).unwrap(), config);
            assert_eq!(h.read(".agents/skills/mine/SKILL.md"), b"# changed by the user's Codex\n");
        }
    }

    /// The CODEX_HOME agents.json records is guarded too, wherever it is.
    #[test]
    fn the_codex_home_agents_json_records_is_guarded_wherever_it_is() {
        let h = Home::new();
        let elsewhere = h.home.parent().unwrap().join("Codex é");
        fs::create_dir_all(&elsewhere).unwrap();
        fs::write(elsewhere.join("AGENTS.md"), "# mine\n").unwrap();
        crate::agents::tests::plant_agents_py(&crate::agents::tests::installed(&h.home), true);
        let chosen = serde_json::json!({ "version": 1, "agents": ["codex"], "prefere": "codex", "codex_home": elsewhere });
        crate::agents::tests::choose(&h.home, &chosen);
        assert_eq!(codex_homes(&h.home), [PathBuf::from(CODEX_DEFAULT), elsewhere.clone()]);
        let snap = h.take();
        fs::write(elsewhere.join("AGENTS.md"), "# ours\n").unwrap();
        let found: Vec<String> = check(&snap, &h.home).iter().map(ToString::to_string).collect();
        assert_eq!(found, [format!("{} (début modifié)", elsewhere.join("AGENTS.md").display())]);
        settle(snap, &h.home);
        assert_eq!(fs::read_to_string(elsewhere.join("AGENTS.md")).unwrap(), "# mine\n");
        // ~/.codex itself, named in agents.json: once.
        crate::agents::tests::choose(
            &h.home,
            &serde_json::json!({ "version": 1, "agents": ["codex"], "prefere": "codex", "codex_home": h.home.join(".codex") }),
        );
        assert_eq!(codex_homes(&h.home), [PathBuf::from(CODEX_DEFAULT)]);
    }

    // ── Windows: the same promises, the installer's writes done by hand ─────

    #[cfg(windows)]
    #[test]
    fn windows_a_rewritten_hook_or_a_removed_one_comes_back() {
        let h = Home::new();
        personal(&h);
        let before = snapshot_of(&h);
        let snap = h.take();
        // What install.ps1 does on a clean run: our hooks next to the user's, CLAUDE.md
        // appended, our hooks added to settings.json.
        h.write(".claude/hooks/brain/session-logger.py", "ours\n");
        fs::write(h.home.join(".claude/CLAUDE.md"), "# Mes règles\n\r\n<!-- brain-in-a-box -->\r\nnos règles\n").unwrap();
        edit_settings(&h, |d| {
            let stop = serde_json::json!([{ "hooks": [{ "type": "command", "command": "python.exe", "args": ["x\\session-logger.py"] }] }]);
            d["hooks"].as_object_mut().unwrap().insert("Stop".into(), stop);
        });
        let report = settle(snap, &h.home);
        assert!(report.clean(), "{report:?}");
        let written = fs::read_to_string(h.store.join(WRITTEN)).unwrap();
        assert!(written.contains(".claude/hooks/brain/session-logger.py"), "keys use '/': {written}");

        // What it must not do: rewrite the user's hook, remove one, rewrite CLAUDE.md, mark one read-only.
        let snap = h.take();
        fs::write(h.home.join(".claude/hooks/brain/correction-detector.py"), "ours").unwrap();
        fs::remove_file(h.home.join(".claude/hooks/brain/my-own.py")).unwrap();
        fs::write(h.home.join(".claude/CLAUDE.md"), "nouveau").unwrap();
        perms::set_mode(&h.home.join(".claude/hooks/guard.sh"), 0o444).unwrap();
        let mut named: Vec<String> = check(&snap, &h.home).iter().map(ToString::to_string).collect();
        named.sort();
        assert_eq!(
            named,
            [
                "~/.claude/CLAUDE.md (début modifié)",
                "~/.claude/hooks/brain/correction-detector.py (réécrit)",
                "~/.claude/hooks/brain/my-own.py (supprimé)",
                "~/.claude/hooks/guard.sh (droits modifiés)",
            ]
        );
        let report = settle(snap, &h.home);
        assert_eq!((report.restored.len(), report.failed.len()), (4, 0), "{report:?}");
        assert_eq!(perms::mode(&fs::metadata(h.home.join(".claude/hooks/guard.sh")).unwrap()), 0o600);
        for (rel, bytes) in before {
            if rel != ".claude/CLAUDE.md" && rel != SETTINGS {
                assert_eq!(fs::read(h.home.join(&rel)).unwrap(), bytes, "{rel}");
            }
        }
    }

    /// install.ps1's installed.json names its files as Place-File writes them: absolute
    /// Windows paths, hashes in any case.
    #[cfg(windows)]
    #[test]
    fn windows_install_ps1_manifest_names_its_files() {
        let h = Home::new();
        h.write(".claude/hooks/brain/x.py", "v1\n");
        let v1 = sha256_file(&h.home.join(".claude/hooks/brain/x.py")).unwrap();
        let key = h.home.join(r".claude\hooks\brain\x.py").display().to_string();
        h.write(INSTALLED, &serde_json::json!({ key: v1.to_uppercase() }).to_string());
        let snap = h.take();
        h.write(".claude/hooks/brain/x.py", "v2\n");
        assert!(settle(snap, &h.home).clean());
    }

    #[cfg(windows)]
    #[test]
    fn windows_a_settings_file_it_creates_holds_hooks_only() {
        let h = Home::new();
        let snap = h.take();
        h.write(".claude/settings.json", r#"{"hooks": {}, "permissions": {"defaultMode": "bypassPermissions"}}"#);
        assert!(settle(snap, &h.home).failed.is_empty());
        assert!(!h.home.join(SETTINGS).exists());
        let snap = h.take();
        h.write(".claude/settings.json", r#"{"hooks": {}}"#);
        assert!(settle(snap, &h.home).clean());
    }

    /// The hash must be install_skills.py's own, or a skill update would look like the
    /// user's edit: Python sorts Windows paths without regard to case.
    #[cfg(windows)]
    #[test]
    fn windows_skill_folders_hash_like_install_skills() {
        let h = Home::new();
        let skill = h.home.join(".claude/skills/brain-boost");
        h.write(".claude/skills/brain-boost/SKILL.md", "# boost\n");
        h.write(".claude/skills/brain-boost/a/b.md", "b");
        h.write(".claude/skills/brain-boost/a.b", "dot");
        h.write(".claude/skills/brain-boost/Z.md", "capital");
        h.write(".claude/skills/brain-boost/b.md", "lower after capital Z in bytes, before it without case");
        h.write(".claude/skills/brain-boost/__pycache__/x.pyc", "junk");
        h.write(".claude/skills/brain-boost/Thumbs.db", "junk");
        let ours = tree_hash(&skill).unwrap();
        let Some(python) = crate::detect::python() else {
            eprintln!("no Python: compared with nothing");
            return;
        };
        let engine = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../engine");
        let code = "import sys; sys.path.insert(0, sys.argv[1]); import install_skills as m; print(m.tree_hash(sys.argv[2]))";
        let out = crate::sys::run(
            &python.exe,
            &["-B", "-I", "-c", code, &engine.to_string_lossy(), &skill.to_string_lossy()],
            std::time::Duration::from_secs(60),
        );
        assert!(out.ok(), "{}", out.stderr);
        assert_eq!(ours, out.stdout.trim());
    }
}
