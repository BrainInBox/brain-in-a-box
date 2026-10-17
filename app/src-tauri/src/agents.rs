//! Who the app may ask: Claude Code, Codex or both, and in which order (the preferred one first).
//!
//! ~/.brain-in-a-box/agents.json is the only source of the choice, and the kit's _agents.py its only
//! reader: the app runs `_agents.py lire` and takes what it says, so Codex's gate (closed on a system
//! until its path is green there) and the file's checks live in one place. Without the file, Claude
//! alone, exactly as before the choice existed, and nothing is run to know it. A file the kit cannot
//! trust, or one the app could not have read (no Python, no _agents.py), gives no agent at all:
//! whoever chose Codex alone excluded Claude, and nothing falls back to it.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::Serialize;
use serde_json::Value;

use crate::sys;

const FILE: &str = ".brain-in-a-box/agents.json";
const READ_TIMEOUT: Duration = Duration::from_secs(20);

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
    fn claude_alone() -> Agents {
        Agents { order: vec![Engine::Claude], codex_home: None, etat: "absent".into() }
    }

    fn none(etat: &str) -> Agents {
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
    let Some(script) = scripts(home).into_iter().find(|s| s.is_file()) else { return Agents::none("illisible") };
    let Some(python) = python() else { return Agents::none("illisible") };
    let home_var = if cfg!(windows) { "USERPROFILE" } else { "HOME" };
    let home_text = home.to_string_lossy();
    let script_text = script.to_string_lossy();
    let out = sys::run_with(&python, &["-I", "-B", &script_text, "lire"], &[(home_var, &home_text)], None, READ_TIMEOUT);
    if !out.ok() {
        return Agents::none("illisible");
    }
    parse(&out.stdout).unwrap_or_else(|| Agents::none("illisible"))
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
        Value::String(h) => Some(PathBuf::from(h)).filter(|p| p.is_absolute()),
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
fn kit_python() -> Option<PathBuf> {
    let apple = crate::detect::clt_dir().map(|d| d.join("usr/bin/python3")).filter(|p| sys::is_executable(p));
    apple.or_else(|| sys::which_all("python3").into_iter().find(|p| p != Path::new("/usr/bin/python3")))
}

#[cfg(target_os = "linux")]
fn kit_python() -> Option<PathBuf> {
    sys::find_tool("python3")
}

#[cfg(windows)]
fn kit_python() -> Option<PathBuf> {
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

    #[test]
    fn the_sources_copy_reads_it_before_the_hooks_are_there() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        choose(home, &serde_json::json!({ "version": 1, "agents": ["claude"], "prefere": "claude" }));
        assert_eq!(read_here(home), Agents::none("illisible"), "no _agents.py at all: not read, not trusted");
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
            r#"{"etat": "ok", "agents": ["claude"], "prefere": "claude"}"#,
        ] {
            assert_eq!(ok(bad), None, "{bad}");
        }
    }
}
