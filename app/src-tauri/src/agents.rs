//! Who the app may ask: Claude Code, Codex or both, and in which order (the preferred one first).
//!
//! ~/.brain-in-a-box/agents.json is the only source of the choice, and the kit's _agents.py its only
//! reader: the app runs `_agents.py lire` and takes what it says, so Codex's gate (closed on a system
//! until its path is green there) and the file's checks live in one place. Without the file, Claude
//! alone, exactly as before the choice existed, and nothing is run to know it. A file the kit cannot
//! trust gives no agent at all: whoever chose Codex alone excluded Claude, and nothing falls back to
//! it. No kit on disk: the app's own copy of _agents.py reads it. No Python to run it: Claude alone
//! where the kit's gate keeps Codex closed (the kit trusts no file that lists Codex there, and the
//! installer reads the file itself once Python is in), no agent where Codex can be chosen.
//!
//! The window asks who the brain works with only where that same _agents.py lets Codex be chosen
//! (`codex_open`). What the user picks there holds for the session (`choose`, `current`) until the
//! installer, which alone writes agents.json, records it: the brain step hands it over (BIAB_AGENTS).

use std::ffi::OsString;
use std::fs;
use std::io;
use std::path::{Component, Path, PathBuf};
use std::sync::{LazyLock, Mutex};
use std::time::Duration;

use regex::Regex;
use serde::Serialize;
use serde_json::Value;

use crate::{codex, detect, steps, sys};

const FILE: &str = ".brain-in-a-box/agents.json";
const READ_TIMEOUT: Duration = Duration::from_secs(20);
/// The kit's _agents.py as the app was built with it: its gate, before any kit is on disk.
const BUILT_IN: &str = include_str!("../../../engine/hooks/_agents.py");
/// This system, as _agents.systeme() names it.
const SYSTEM: &str = if cfg!(windows) {
    "windows"
} else if cfg!(target_os = "linux") {
    "linux"
} else {
    "darwin"
};
/// What `current` says of a choice made in the window that agents.json does not record yet.
pub const PENDING: &str = "choix";

/// The choice made in the window, for the home it was made for, until the app quits.
static CHOSEN: Mutex<Option<(PathBuf, Agents)>> = Mutex::new(None);
static GATE: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"(?m)^CODEX_OUVERT = frozenset\((.*)\)\r?$").ok());
static SYSTEM_NAME: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r#"\A(?:"([a-z0-9_]+)"|'([a-z0-9_]+)')\z"#).ok());

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Engine {
    Claude,
    Codex,
}

impl Engine {
    /// As the window and the report name it.
    pub fn name(self) -> &'static str {
        match self {
            Engine::Claude => "Claude",
            Engine::Codex => "Codex",
        }
    }

    /// As agents.json and the kit's journals write it.
    pub fn id(self) -> &'static str {
        match self {
            Engine::Claude => "claude",
            Engine::Codex => "codex",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Agents {
    /// The engines in the order they are asked: the preferred one, then the other one listed.
    /// Empty: none may be.
    pub order: Vec<Engine>,
    /// Codex's folder (CODEX_HOME) as the install recorded it, only when Codex is listed.
    pub codex_home: Option<PathBuf>,
    /// _agents.lire()'s word: "absent", "ok", or why the file is not trusted.
    pub etat: String,
}

impl Agents {
    pub(crate) fn claude_alone() -> Agents {
        Agents { order: vec![Engine::Claude], codex_home: None, etat: "absent".into() }
    }

    pub(crate) fn none(etat: &str) -> Agents {
        Agents { order: Vec::new(), codex_home: None, etat: etat.into() }
    }

    pub fn lists(&self, engine: Engine) -> bool {
        self.order.contains(&engine)
    }

    /// The kit names the engine that answered only when Codex is one of the agents (_headless.py):
    /// with Claude alone, everything reads and is written as before the choice existed.
    pub fn named(&self) -> bool {
        self.lists(Engine::Codex)
    }
}

/// The user's choice, through the kit's _agents.py.
pub fn read(home: &Path) -> Agents {
    read_with(home, kit_python)
}

fn read_with(home: &Path, python: impl FnOnce() -> Option<PathBuf>) -> Agents {
    // A dangling link is a file there all the same: _agents.py says what it makes of it.
    if fs::symlink_metadata(home.join(FILE)).is_err_and(|e| e.kind() == io::ErrorKind::NotFound) {
        return Agents::claude_alone();
    }
    let Some(python) = python() else {
        return if codex_open(home) { Agents::none("illisible") } else { Agents::claude_alone() };
    };
    kit(home, &python, "lire").and_then(|out| parse(&out)).unwrap_or_else(|| Agents::none("illisible"))
}

/// Who the brain works with now: the choice made in the window while agents.json does not record it
/// yet (etat PENDING), else agents.json, as `read` says.
pub fn current(home: &Path) -> Agents {
    current_with(home, read(home))
}

fn current_with(home: &Path, file: Agents) -> Agents {
    match chosen(home) {
        Some(c) if c.order != file.order || c.codex_home != file.codex_home => c,
        _ => file,
    }
}

fn chosen(home: &Path) -> Option<Agents> {
    let held = CHOSEN.lock().unwrap_or_else(|p| p.into_inner());
    held.as_ref().filter(|(h, _)| h == home).map(|(_, a)| a.clone())
}

/// A choice made in the window that agents.json does not record yet: the brain step hands it over.
pub fn pending(agents: &Agents) -> bool {
    agents.etat == PENDING
}

/// What the brain step adds to the installer's environment for it (install.sh and install.ps1 read
/// BIAB_AGENTS and BIAB_PREFERE): nothing unless a choice is pending, so that without one the
/// installer runs as before the window could choose.
pub fn installer_env(agents: &Agents) -> Vec<(&'static str, String)> {
    let Some(prefere) = agents.order.first().filter(|_| pending(agents)) else {
        return Vec::new();
    };
    let names: Vec<&str> = [Engine::Claude, Engine::Codex].into_iter().filter(|e| agents.lists(*e)).map(Engine::id).collect();
    vec![("BIAB_AGENTS", names.join(",")), ("BIAB_PREFERE", prefere.id().to_string())]
}

/// The user's pick in the window, preferred first: held for this home until agents.json records it.
/// Codex only where the kit's gate lets it be chosen; its folder the one agents.json records, else
/// the one the installer will record (`codex_home_for`).
pub fn choose(home: &Path, names: &[String]) -> Result<Agents, String> {
    let file = read(home);
    let chosen = pick(home, names, codex_open(home), &file, std::env::var_os("CODEX_HOME"))?;
    *CHOSEN.lock().unwrap_or_else(|p| p.into_inner()) = Some((home.to_path_buf(), chosen.clone()));
    Ok(current_with(home, file))
}

fn pick(home: &Path, names: &[String], open: bool, file: &Agents, env: Option<OsString>) -> Result<Agents, String> {
    let mut order = Vec::new();
    for name in names {
        let engine = match name.as_str() {
            "claude" => Engine::Claude,
            "codex" => Engine::Codex,
            _ => return Err("Je ne connais pas cette IA.".into()),
        };
        if order.contains(&engine) {
            return Err("Je ne connais pas ce choix.".into());
        }
        order.push(engine);
    }
    if order.is_empty() {
        return Err("Choisis au moins une IA.".into());
    }
    if !order.contains(&Engine::Codex) {
        return Ok(Agents { order, codex_home: None, etat: PENDING.into() });
    }
    if !open {
        return Err("Codex n'est pas encore disponible ici.".into());
    }
    let codex_home = match file.codex_home.clone().filter(|_| file.lists(Engine::Codex)) {
        Some(h) => h,
        None => codex_home_for(home, env)?,
    };
    Ok(Agents { order, codex_home: Some(codex_home), etat: PENDING.into() })
}

/// _agents.codex_home(): $CODEX_HOME as Codex reads it, else ~/.codex. The app hands its own
/// environment to the installer, which reads the same variable.
fn codex_home_for(home: &Path, env: Option<OsString>) -> Result<PathBuf, String> {
    let raw = env.map(|v| v.to_string_lossy().trim().to_string()).unwrap_or_default();
    if raw.is_empty() {
        return Ok(home.join(".codex"));
    }
    let path = PathBuf::from(raw);
    if !path.is_absolute() {
        return Err("CODEX_HOME n'est pas un chemin complet : je ne sais pas où mettre Codex.".into());
    }
    Ok(normpath(&path))
}

/// os.path.normpath: "." and empty parts gone, ".." taken back, the way Python does it on paper.
fn normpath(path: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for part in path.components() {
        match part {
            Component::CurDir => {}
            Component::ParentDir => {
                if !out.pop() {
                    out.push(part);
                }
            }
            _ => out.push(part),
        }
    }
    out
}

/// Whether Codex can be chosen on this system: the gate of the kit's _agents.py, the copy that reads
/// agents.json (installed, else the sources'), else, before any kit is on disk, the app's own copy of
/// that file. Read without Python, which a fresh Mac or PC does not have yet: only the gate's line as
/// the kit writes it, `CODEX_OUVERT = frozenset({"darwin", ...})`; anything else is closed.
pub fn codex_open(home: &Path) -> bool {
    let text = match scripts(home).into_iter().find(|s| s.is_file()) {
        Some(script) => fs::read_to_string(script).ok(),
        None => Some(BUILT_IN.to_string()),
    };
    text.as_deref().and_then(gate).is_some_and(|systems| systems.iter().any(|s| s == SYSTEM))
}

/// The systems the gate's line opens; None when there is not exactly one such line, or it says
/// something else than a set of quoted names.
fn gate(text: &str) -> Option<Vec<String>> {
    let re = GATE.as_ref()?;
    let mut lines = re.captures_iter(text);
    let inner = lines.next()?.get(1)?.as_str().trim().to_string();
    if lines.next().is_some() {
        return None;
    }
    if inner.is_empty() {
        return Some(Vec::new());
    }
    let names = inner.strip_prefix('{')?.strip_suffix('}')?;
    let name = SYSTEM_NAME.as_ref()?;
    let parts: Vec<&str> = names.split(',').map(str::trim).collect();
    let (last, rest) = parts.split_last()?;
    // A trailing comma is Python's, an empty name anywhere else is not.
    let parts: Vec<&str> = if last.is_empty() { rest.to_vec() } else { parts };
    parts
        .iter()
        .map(|p| name.captures(p).and_then(|c| c.get(1).or(c.get(2))).map(|m| m.as_str().to_string()))
        .collect::<Option<Vec<_>>>()
        .filter(|v| !v.is_empty())
}

/// What the window offers before the diagnosis.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Offer {
    /// Codex can be chosen here: the window asks who the brain works with. Otherwise it asks
    /// nothing and everything goes as before Codex.
    pub open: bool,
    /// What it preselects, the preferred one first; empty when it asks nothing.
    pub agents: Vec<Engine>,
}

pub fn offer(home: &Path) -> Offer {
    if !codex_open(home) {
        return Offer { open: false, agents: Vec::new() };
    }
    let now = current(home);
    // An install of before the choice: Claude alone, whatever else is on the machine.
    let kit = home.join(".brain-in-a-box").join("installed.json").is_file() || detect::claude_hooks(home);
    let claude = detect::claude_bin().is_some();
    let codex_there =
        codex_home_for(home, std::env::var_os("CODEX_HOME")).ok().and_then(|h| codex::Setup::for_user(&h).ok()).is_some_and(|s| codex::find(&s).is_some());
    Offer { open: true, agents: preselect(&now, kit, claude, codex_there) }
}

/// The choice already made (agents.json, or the window's earlier in the session); without one,
/// Claude alone on an install of before the choice; on a fresh machine, what is installed.
fn preselect(now: &Agents, kit: bool, claude: bool, codex: bool) -> Vec<Engine> {
    if !now.order.is_empty() && now.etat != "absent" {
        return now.order.clone();
    }
    match (kit, claude, codex) {
        (false, true, true) => vec![Engine::Claude, Engine::Codex],
        (false, false, true) => vec![Engine::Codex],
        _ => vec![Engine::Claude],
    }
}

/// Whether Codex keeps sessions without running the kit's hooks since the install wired them (not
/// approved, turned off in /hooks, or no longer trusted after an update): _agents.hooks_muets(), the
/// night check's orange line. Only when Codex is one of the agents; nothing is run otherwise.
pub fn hooks_muets(home: &Path, agents: &Agents) -> bool {
    hooks_muets_with(home, agents, kit_python)
}

fn hooks_muets_with(home: &Path, agents: &Agents, python: impl FnOnce() -> Option<PathBuf>) -> bool {
    if !agents.lists(Engine::Codex) {
        return false;
    }
    python().and_then(|p| kit(home, &p, "hooks-muets")).is_some_and(|out| out.trim() == "oui")
}

/// What `_agents.py <command>` prints, run with the kit's Python as the night's tasks run it. No kit
/// on disk: the app's own copy, the one `codex_open` reads then, from a private folder of its own.
fn kit(home: &Path, python: &Path, command: &str) -> Option<String> {
    let mut built_in = None;
    let script = match scripts(home).into_iter().find(|s| s.is_file()) {
        Some(script) => script,
        None => {
            let dir = built_in.insert(steps::TempDir::new_in(&sys::work_root().ok()?).ok()?);
            let script = dir.path().join("_agents.py");
            fs::write(&script, BUILT_IN).ok()?;
            script
        }
    };
    let home_var = if cfg!(windows) { "USERPROFILE" } else { "HOME" };
    let home_text = home.to_string_lossy();
    let script_text = script.to_string_lossy();
    let out = sys::run_with(python, &["-I", "-B", &script_text, command], &[(home_var, &home_text)], None, READ_TIMEOUT);
    drop(built_in);
    out.ok().then_some(out.stdout)
}

/// The installed copy, next to the hooks (the one the night's tasks read), else the one of the
/// sources the app installed from: the installer records a choice before it copies the hooks.
fn scripts(home: &Path) -> [PathBuf; 2] {
    [
        home.join(".claude").join("hooks").join("brain").join("_agents.py"),
        home.join(".brain-in-a-box").join("src").join("engine").join("hooks").join("_agents.py"),
    ]
}

/// `_agents.py lire`'s line. Anything else than what it writes is no answer.
fn parse(stdout: &str) -> Option<Agents> {
    let v: Value = serde_json::from_str(stdout.trim()).ok()?;
    let etat = v.get("etat")?.as_str()?.to_string();
    let engine = |name: &str| match name {
        "claude" => Some(Engine::Claude),
        "codex" => Some(Engine::Codex),
        _ => None,
    };
    let mut order: Vec<Engine> = v.get("agents")?.as_array()?.iter().map(|a| a.as_str().and_then(engine)).collect::<Option<_>>()?;
    let trusted = etat == "ok" || etat == "absent";
    if order.is_empty() {
        return (!trusted).then(|| Agents::none(&etat));
    }
    let prefere = v.get("prefere")?.as_str().and_then(engine)?;
    if !trusted || !order.contains(&prefere) || (order.len() == 2 && order[0] == order[1]) {
        return None;
    }
    order.sort_by_key(|e| *e != prefere);
    let codex_home = match v.get("codex_home")? {
        Value::Null => None,
        Value::String(h) if Path::new(h).is_absolute() => Some(PathBuf::from(h)),
        _ => return None,
    };
    if codex_home.is_some() != order.contains(&Engine::Codex) {
        return None;
    }
    Some(Agents { order, codex_home, etat })
}

/// The Python the kit's hooks run with. Without Apple's tools, /usr/bin/python3 opens their
/// install window: theirs, or another one.
#[cfg(target_os = "macos")]
pub(crate) fn kit_python() -> Option<PathBuf> {
    let apple = crate::detect::clt_dir().map(|d| d.join("usr/bin/python3")).filter(|p| sys::is_executable(p));
    apple.or_else(|| sys::which_all("python3").into_iter().find(|p| p != Path::new("/usr/bin/python3")))
}

#[cfg(target_os = "linux")]
pub(crate) fn kit_python() -> Option<PathBuf> {
    sys::find_tool("python3")
}

#[cfg(windows)]
pub(crate) fn kit_python() -> Option<PathBuf> {
    crate::detect::python().map(|p| p.exe)
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use std::process::Command;

    /// The Python the kit runs with on this system.
    pub(crate) fn python() -> PathBuf {
        let name = if cfg!(windows) { "python" } else { "python3" };
        let out = Command::new(name)
            .args(["-I", "-c", "import sys; print(sys.executable)"])
            .output()
            .unwrap_or_else(|e| panic!("reading agents.json takes {name}: {e}"));
        assert!(out.status.success(), "{name}: {}", String::from_utf8_lossy(&out.stderr));
        PathBuf::from(String::from_utf8(out.stdout).unwrap().trim())
    }

    fn repo() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
    }

    /// The kit's _agents.py in `dir`, Codex open on every system when `open` (as the other benches
    /// open it: test-hooks.sh, codex_captures.open_gate), else as shipped.
    pub(crate) fn plant_agents_py(dir: &Path, open: bool) {
        let text = fs::read_to_string(repo().join("engine").join("hooks").join("_agents.py")).unwrap();
        let text = if open {
            let re = regex::Regex::new(r"(?m)^CODEX_OUVERT = frozenset\(.*\)$").unwrap();
            assert_eq!(re.find_iter(&text).count(), 1, "the gate's line moved");
            re.replace(&text, r#"CODEX_OUVERT = frozenset({"darwin", "linux", "windows"})"#).into_owned()
        } else {
            text
        };
        fs::create_dir_all(dir).unwrap();
        fs::write(dir.join("_agents.py"), text).unwrap();
    }

    pub(crate) fn installed(home: &Path) -> PathBuf {
        home.join(".claude").join("hooks").join("brain")
    }

    /// agents.json as the installer writes it (0600).
    pub(crate) fn choose(home: &Path, d: &Value) {
        let p = home.join(FILE);
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::write(&p, d.to_string()).unwrap();
        #[cfg(unix)]
        crate::perms::set_mode(&p, 0o600).unwrap();
    }

    fn read_here(home: &Path) -> Agents {
        read_with(home, || Some(python()))
    }

    #[test]
    fn without_agents_json_claude_alone_and_nothing_runs() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        plant_agents_py(&installed(home), true);
        assert_eq!(read_with(home, || panic!("Python was asked")), Agents::claude_alone());
        assert!(!read_with(home, || panic!("Python was asked")).named());
    }

    #[test]
    fn the_kits_reader_decides_and_its_gate_stays_closed() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        let codex = home.join("Données é").join(".codex");
        plant_agents_py(&installed(home), false);
        choose(home, &serde_json::json!({ "version": 1, "agents": ["claude"], "prefere": "claude" }));
        let claude = read_here(home);
        assert_eq!((claude.order.as_slice(), claude.etat.as_str(), claude.named()), ([Engine::Claude].as_slice(), "ok", false));
        let both = serde_json::json!({ "version": 1, "agents": ["claude", "codex"], "prefere": "codex", "codex_home": codex });
        choose(home, &both);
        // As shipped: Codex is closed here, the file is not trusted, no one is asked.
        assert_eq!(read_here(home), Agents::none("ferme"));
        plant_agents_py(&installed(home), true);
        let open = read_here(home);
        assert_eq!(open, Agents { order: vec![Engine::Codex, Engine::Claude], codex_home: Some(codex.clone()), etat: "ok".into() });
        assert!(open.named() && open.lists(Engine::Claude));
        choose(home, &serde_json::json!({ "version": 1, "agents": ["codex"], "prefere": "codex", "codex_home": codex }));
        assert_eq!(read_here(home).order, [Engine::Codex]);
        choose(home, &serde_json::json!({ "version": 1, "agents": ["claude"], "prefere": "codex" }));
        assert_eq!(read_here(home), Agents::none("invalide"));
    }

    /// No kit on disk (Claude Code reinstalled over ~/.claude, an install made from a terminal, so no
    /// sources): the app's own copy of _agents.py reads the file, as master's Bibo would have gone on.
    #[test]
    fn without_a_kit_on_disk_the_apps_own_copy_reads_it() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        choose(home, &serde_json::json!({ "version": 1, "agents": ["claude"], "prefere": "claude", "codex_home": null, "pose_par": "install.sh" }));
        assert_eq!(read_here(home), Agents { order: vec![Engine::Claude], codex_home: None, etat: "ok".into() });
        // As shipped, the app's copy keeps Codex closed: a file that lists it is not trusted.
        choose(home, &serde_json::json!({ "version": 1, "agents": ["codex"], "prefere": "codex", "codex_home": home.join(".codex") }));
        assert_eq!(read_here(home), Agents::none("ferme"));
        assert!(!home.join(".claude").exists() && !home.join(".brain-in-a-box").join("src").exists(), "nothing written for it");
    }

    /// No Python to run the kit's reader: where Codex cannot be chosen, the kit trusts no file that
    /// lists it, so Claude alone, as on master (the installer reads the file itself once Python is
    /// there). Where it can, no agent: the window asks the choice again anyway.
    #[test]
    fn without_python_the_gate_decides() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        choose(home, &serde_json::json!({ "version": 1, "agents": ["claude"], "prefere": "claude" }));
        assert_eq!(read_with(home, || None), Agents::claude_alone(), "nothing on disk, the app's gate closed");
        plant_agents_py(&installed(home), false);
        assert_eq!(read_with(home, || None), Agents::claude_alone());
        plant_agents_py(&installed(home), true);
        assert_eq!(read_with(home, || None), Agents::none("illisible"));
    }

    #[test]
    fn the_sources_copy_reads_it_before_the_hooks_are_there() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        choose(home, &serde_json::json!({ "version": 1, "agents": ["claude"], "prefere": "claude" }));
        plant_agents_py(&home.join(".brain-in-a-box").join("src").join("engine").join("hooks"), false);
        assert_eq!(read_here(home).order, [Engine::Claude]);
        // The installed one comes first: the one the night's tasks read.
        plant_agents_py(&installed(home), true);
        let codex = home.join(".codex");
        choose(home, &serde_json::json!({ "version": 1, "agents": ["codex"], "prefere": "codex", "codex_home": codex }));
        assert_eq!(read_here(home).order, [Engine::Codex]);
        assert_eq!(read_with(home, || None), Agents::none("illisible"), "no Python: not trusted");
    }

    /// A reader that fails, or says something else than its line: not trusted, no agent.
    #[test]
    fn a_reader_that_does_not_answer_gives_no_agent() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        choose(home, &serde_json::json!({ "version": 1, "agents": ["claude"], "prefere": "claude" }));
        fs::create_dir_all(installed(home)).unwrap();
        for script in [
            "import sys\nprint('{\"etat\": \"ok\", \"agents\": [\"claude\"], \"prefere\": \"claude\", \"codex_home\": null}')\nsys.exit(3)\n",
            "print('agents=claude prefere=claude')\n",
        ] {
            fs::write(installed(home).join("_agents.py"), script).unwrap();
            assert_eq!(read_here(home), Agents::none("illisible"), "{script}");
        }
    }

    #[cfg(unix)]
    #[test]
    fn a_link_in_its_place_is_the_kits_to_judge() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        plant_agents_py(&installed(home), true);
        fs::create_dir_all(home.join(".brain-in-a-box")).unwrap();
        std::os::unix::fs::symlink(home.join("nowhere.json"), home.join(FILE)).unwrap();
        assert_eq!(read_here(home), Agents::none("illisible"));
    }

    /// The kit says whether Codex runs its hooks; nothing is run when Codex is not one of the agents.
    #[test]
    fn whether_codex_runs_the_hooks_is_the_kits_to_say() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        let codex = home.join("Données é").join(".codex");
        plant_agents_py(&installed(home), true);
        let state = home.join(".brain-in-a-box");
        fs::create_dir_all(&state).unwrap();
        // Wired long ago, a session since, and no hook run since.
        fs::write(state.join("codex-hooks-poses.json"), r#"{"version": 1, "date": "2000-01-01T00:00:00Z"}"#).unwrap();
        let day = codex.join("sessions").join("2026").join("10").join("17");
        fs::create_dir_all(&day).unwrap();
        fs::write(day.join("rollout-2026-10-17T10-00-00-0199f0a0-0000-7000-8000-000000000001.jsonl"), "{}\n").unwrap();

        let both = serde_json::json!({ "version": 1, "agents": ["claude", "codex"], "prefere": "claude", "codex_home": codex });
        choose(home, &both);
        let agents = read_here(home);
        assert!(hooks_muets_with(home, &agents, || Some(python())));
        assert!(!hooks_muets_with(&home.join("elsewhere"), &agents, || Some(python())), "another home, no agents.json there: nothing said");
        assert!(!hooks_muets_with(home, &agents, || None));
        // A hook ran since: the hooks run.
        fs::write(state.join("codex-hooks-vus.json"), r#"{"version": 1, "hooks": {"session-logger": "2999-01-01T00:00:00Z"}}"#).unwrap();
        assert!(!hooks_muets_with(home, &agents, || Some(python())));
        for alone in [Agents::claude_alone(), Agents::none("ferme")] {
            assert!(!hooks_muets_with(home, &alone, || panic!("Python was asked")));
        }
    }

    #[test]
    fn only_what_the_reader_writes_is_an_answer() {
        let ok = |s: &str| parse(s);
        assert_eq!(
            ok(r#"{"etat": "ok", "agents": ["claude", "codex"], "prefere": "claude", "codex_home": "/c"}"#).map(|a| a.order),
            if cfg!(windows) { None } else { Some(vec![Engine::Claude, Engine::Codex]) },
            "an absolute folder only"
        );
        assert_eq!(ok(r#"{"etat": "droits", "agents": [], "prefere": null, "codex_home": null}"#), Some(Agents::none("droits")));
        for bad in [
            "",
            "agents=claude",
            r#"{"etat": "ok", "agents": [], "prefere": null, "codex_home": null}"#,
            r#"{"etat": "ferme", "agents": ["claude"], "prefere": "claude", "codex_home": null}"#,
            r#"{"etat": "ok", "agents": ["claude"], "prefere": "codex", "codex_home": null}"#,
            r#"{"etat": "ok", "agents": ["claude", "claude"], "prefere": "claude", "codex_home": null}"#,
            r#"{"etat": "ok", "agents": ["gemini"], "prefere": "gemini", "codex_home": null}"#,
            r#"{"etat": "ok", "agents": ["codex"], "prefere": "codex", "codex_home": null}"#,
            r#"{"etat": "ok", "agents": ["claude"], "prefere": "claude", "codex_home": "/c"}"#,
            r#"{"etat": "ok", "agents": ["codex"], "prefere": "codex", "codex_home": "relative"}"#,
            r#"{"etat": "ok", "agents": ["claude"], "prefere": "claude", "codex_home": "relative"}"#,
            r#"{"etat": "ok", "agents": ["claude"], "prefere": "claude"}"#,
        ] {
            assert_eq!(ok(bad), None, "{bad}");
        }
    }

    /// The kit's _agents.py with its gate's line replaced by `line`, in `dir`.
    fn plant_gate(dir: &Path, line: &str) {
        let text = fs::read_to_string(repo().join("engine").join("hooks").join("_agents.py")).unwrap();
        let re = regex::Regex::new(r"(?m)^CODEX_OUVERT = frozenset\(.*\)$").unwrap();
        assert_eq!(re.find_iter(&text).count(), 1, "the gate's line moved");
        fs::create_dir_all(dir).unwrap();
        fs::write(dir.join("_agents.py"), re.replace(&text, regex::NoExpand(line)).into_owned()).unwrap();
    }

    /// `_agents.py ouvert`, the kit's own answer: exit code 0 when Codex can be chosen here.
    fn kit_says_open(script: &Path) -> bool {
        let out = Command::new(python()).args(["-I", "-B"]).arg(script).arg("ouvert").output().unwrap();
        match out.status.code() {
            Some(0) => true,
            Some(1) => false,
            other => panic!("_agents.py ouvert: {other:?} {}", String::from_utf8_lossy(&out.stderr)),
        }
    }

    #[test]
    fn the_gate_is_the_kits_own_line() {
        let all = r#"CODEX_OUVERT = frozenset({"darwin", "linux", "windows"})"#;
        for line in [
            "CODEX_OUVERT = frozenset()",
            all,
            r#"CODEX_OUVERT = frozenset({"darwin"})"#,
            r#"CODEX_OUVERT = frozenset({"linux"})"#,
            r#"CODEX_OUVERT = frozenset({"windows"})"#,
            r#"CODEX_OUVERT = frozenset({'darwin', 'linux',})"#,
            r#"CODEX_OUVERT = frozenset({"linux", "windows"})"#,
        ] {
            let tmp = tempfile::tempdir().unwrap();
            plant_gate(&installed(tmp.path()), line);
            assert_eq!(codex_open(tmp.path()), kit_says_open(&installed(tmp.path()).join("_agents.py")), "{line}");
        }
        // As shipped: closed here, and the app's own copy is that very file.
        let shipped = repo().join("engine").join("hooks").join("_agents.py");
        assert_eq!(BUILT_IN, fs::read_to_string(&shipped).unwrap());
        assert!(gate(BUILT_IN).is_some(), "the shipped gate must be a line the app reads");
        let fresh = tempfile::tempdir().unwrap();
        assert_eq!(codex_open(fresh.path()), kit_says_open(&shipped), "no kit on disk: the app's copy");
        assert!(!codex_open(fresh.path()), "Codex is closed on every system as shipped");
        // The copy that reads agents.json decides: the installed one, else the sources'.
        let tmp = tempfile::tempdir().unwrap();
        let sources = tmp.path().join(".brain-in-a-box").join("src").join("engine").join("hooks");
        plant_gate(&sources, all);
        assert!(codex_open(tmp.path()));
        plant_gate(&installed(tmp.path()), "CODEX_OUVERT = frozenset()");
        assert!(!codex_open(tmp.path()));
        // Anything the app cannot read for sure is closed, even where Python would open it.
        for odd in [
            r#"CODEX_OUVERT = frozenset(["darwin", "linux", "windows"])"#,
            r#"CODEX_OUVERT = frozenset({"darwin", "linux", "windows"})  # open"#,
            r#"CODEX_OUVERT = frozenset({"dar" "win", "linux", "windows"})"#,
            r#"CODEX_OUVERT = frozenset({"darwin",, "linux", "windows"})"#,
        ] {
            plant_gate(&installed(tmp.path()), odd);
            assert!(!codex_open(tmp.path()), "{odd}");
        }
        assert_eq!(gate(&format!("{all}\n{all}\n")), None, "two gates: none");
        assert_eq!(gate("x = 1\n"), None, "no gate: none");
        // A kit file there that cannot be read: closed, neither the sources' nor the app's copy.
        plant_gate(&sources, all);
        let mut bytes = fs::read(sources.join("_agents.py")).unwrap();
        bytes.extend_from_slice(b"\n# \xff\n");
        fs::write(installed(tmp.path()).join("_agents.py"), bytes).unwrap();
        assert!(!codex_open(tmp.path()));
    }

    /// _agents.codex_home(env, home), as the installer records it. Printed in UTF-8 (-X utf8): Windows'
    /// Python would print an accented path in the console's code page.
    fn kit_codex_home(home: &Path, env: Option<&str>) -> Option<PathBuf> {
        let code = "import json, sys; sys.path.insert(0, sys.argv[1]); import _agents\n\
                    env = {} if sys.argv[3] == '-' else {'CODEX_HOME': sys.argv[3]}\n\
                    try:\n    print(_agents.codex_home(env, sys.argv[2]))\nexcept _agents.Refus:\n    print('-')";
        let out = Command::new(python())
            .args(["-I", "-B", "-X", "utf8", "-c", code])
            .arg(repo().join("engine").join("hooks"))
            .arg(home)
            .arg(env.unwrap_or("-"))
            .output()
            .unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        let said = String::from_utf8(out.stdout).unwrap().trim_end_matches(['\r', '\n']).to_string();
        (said != "-").then(|| PathBuf::from(said))
    }

    #[test]
    fn codexs_folder_is_the_one_the_installer_records() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        let root = if cfg!(windows) { r"C:\" } else { "/" };
        let cases: Vec<Option<String>> = vec![
            None,
            Some(String::new()),
            Some("   ".into()),
            Some(format!("{root}x{0}.codex", std::path::MAIN_SEPARATOR)),
            Some(format!("  {root}x{0}.{0}a{0}{0}b{0}..{0}c{0}  ", std::path::MAIN_SEPARATOR)),
            Some(format!("{root}Données é{0}codex", std::path::MAIN_SEPARATOR)),
            Some("relative/codex".into()),
        ];
        for env in cases {
            let ours = codex_home_for(home, env.clone().map(OsString::from)).ok();
            assert_eq!(ours, kit_codex_home(home, env.as_deref()), "{env:?}");
        }
    }

    #[test]
    fn a_choice_holds_until_agents_json_records_it() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        let codex = home.join(".codex");
        let names = |n: &[&str]| n.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        // As shipped, Codex is closed: refused, and nothing is held.
        plant_agents_py(&installed(home), false);
        assert!(super::choose(home, &names(&["codex"])).is_err());
        assert_eq!(current_with(home, Agents::claude_alone()), Agents::claude_alone());
        // Claude alone without agents.json is what the absent file says already: nothing to record.
        assert_eq!(super::choose(home, &names(&["claude"])).unwrap(), Agents::claude_alone());
        assert!(installer_env(&current_with(home, Agents::claude_alone())).is_empty());

        plant_agents_py(&installed(home), true);
        let picked = super::choose(home, &names(&["codex", "claude"])).unwrap();
        assert_eq!(picked, Agents { order: vec![Engine::Codex, Engine::Claude], codex_home: Some(codex.clone()), etat: PENDING.into() });
        assert!(pending(&current(home)));
        assert_eq!(installer_env(&current(home)), [("BIAB_AGENTS", "claude,codex".to_string()), ("BIAB_PREFERE", "codex".to_string())]);
        // Another home is not concerned: its own agents.json speaks.
        assert_eq!(current_with(&home.join("x"), Agents::claude_alone()), Agents::claude_alone());
        // Once the installer wrote it, agents.json speaks again, and nothing more is handed over.
        choose(home, &serde_json::json!({ "version": 1, "agents": ["claude", "codex"], "prefere": "codex", "codex_home": codex }));
        let recorded = current(home);
        assert_eq!((recorded.etat.as_str(), recorded.order.as_slice()), ("ok", [Engine::Codex, Engine::Claude].as_slice()));
        assert!(installer_env(&recorded).is_empty());
        // The folder agents.json records stays Codex's.
        let elsewhere = home.join("ailleurs");
        choose(home, &serde_json::json!({ "version": 1, "agents": ["codex"], "prefere": "codex", "codex_home": elsewhere }));
        assert_eq!(super::choose(home, &names(&["claude", "codex"])).unwrap().codex_home, Some(elsewhere));
        // Back to Claude alone: handed over too, the file lists Codex.
        let back = super::choose(home, &names(&["claude"])).unwrap();
        assert_eq!((back.order.as_slice(), back.codex_home.as_ref(), back.etat.as_str()), ([Engine::Claude].as_slice(), None, PENDING));
        assert_eq!(installer_env(&back), [("BIAB_AGENTS", "claude".to_string()), ("BIAB_PREFERE", "claude".to_string())]);
        // Untrusted agents.json: the window's choice is what the installer gets.
        choose(home, &serde_json::json!({ "version": 2 }));
        assert_eq!(super::choose(home, &names(&["codex"])).unwrap().order, [Engine::Codex]);
        for bad in [&[][..], &["gemini"][..], &["codex", "codex"][..]] {
            assert!(super::choose(home, &names(bad)).is_err(), "{bad:?}");
        }
        *CHOSEN.lock().unwrap() = None;
    }

    #[test]
    fn what_the_window_preselects() {
        let both = Agents { order: vec![Engine::Codex, Engine::Claude], codex_home: Some("/c".into()), etat: "ok".into() };
        assert_eq!(preselect(&both, true, false, false), both.order, "agents.json's choice, whatever is installed");
        let picked = Agents { etat: PENDING.into(), ..both.clone() };
        assert_eq!(preselect(&picked, false, false, false), both.order, "the window's earlier choice");
        let absent = Agents::claude_alone();
        assert_eq!(preselect(&absent, true, true, true), [Engine::Claude], "an install of before the choice: Claude alone");
        assert_eq!(preselect(&absent, false, true, true), [Engine::Claude, Engine::Codex]);
        assert_eq!(preselect(&absent, false, false, true), [Engine::Codex]);
        assert_eq!(preselect(&absent, false, true, false), [Engine::Claude]);
        assert_eq!(preselect(&absent, false, false, false), [Engine::Claude]);
        assert_eq!(preselect(&Agents::none("droits"), false, false, true), [Engine::Codex]);
        // Closed: nothing is offered, and nothing is looked for.
        let tmp = tempfile::tempdir().unwrap();
        plant_agents_py(&installed(tmp.path()), false);
        assert_eq!(offer(tmp.path()), Offer { open: false, agents: Vec::new() });
        plant_agents_py(&installed(tmp.path()), true);
        assert!(offer(tmp.path()).open);
    }
}
