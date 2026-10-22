//! Bibo's advice: which AI the brain should work with, from what is already on this computer (Claude
//! Code and its login, Codex and its ChatGPT login, the ChatGPT and Claude desktop apps, Hermes Agent).
//!
//! The rules and the sentences are the kit's own: the CONSEIL_JSON block of engine/hooks/_conseil.py,
//! which `importer-memoire conseil` applies in a terminal. The app reads that block from the same file
//! (include_str!) and applies it to what it probes itself: one source for both.
//!
//! Nothing secret is read. Codex's auth.json and Hermes's .env are looked for, never opened; of
//! `claude auth status`, the diagnosis keeps authMethod (detect::login_of) and nothing else. While the kit's gate keeps Codex closed on this system (agents::codex_open), no rule
//! offers Codex.

use std::collections::BTreeMap;
use std::ffi::OsString;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::LazyLock;

use serde::{Deserialize, Serialize};

use crate::detect::{self, ClaudeLogin};
use crate::model::Advice;
use crate::{agents, codex, sys};

const SOURCE: &str = include_str!("../../../engine/hooks/_conseil.py");
const OPEN: &str = "CONSEIL_JSON = r\"\"\"";

#[derive(Deserialize)]
struct Rules {
    regles: Vec<Rule>,
    /// Hermes's sentence: the first whose conditions match.
    hermes: Vec<HermesRule>,
    phrases: BTreeMap<String, String>,
}

#[derive(Deserialize)]
struct HermesRule {
    si: BTreeMap<String, bool>,
    phrase: String,
}

#[derive(Deserialize)]
struct Rule {
    si: BTreeMap<String, bool>,
    conseil: String,
    phrase: String,
}

static RULES: LazyLock<Option<Rules>> = LazyLock::new(|| block(SOURCE).and_then(|b| serde_json::from_str(b).ok()));

/// The JSON between `CONSEIL_JSON = r"""` and the next `"""`.
fn block(text: &str) -> Option<&str> {
    let start = text.find(OPEN)? + OPEN.len();
    let len = text[start..].find("\"\"\"")?;
    Some(&text[start..start + len])
}

/// The facts the rules read, named as CONSEIL_JSON names them.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize)]
pub struct Facts {
    /// Codex may be chosen on this system (_agents.py's gate).
    pub porte: bool,
    /// The Claude desktop app is on this computer, or Claude Code is logged in: installed alone,
    /// Claude Code says nothing of its user (the app installs it too).
    pub claude: bool,
    /// Claude Code is logged in with a subscription.
    pub claude_connecte: bool,
    /// The ChatGPT or Codex desktop app, Codex, or Codex's login.
    pub chatgpt: bool,
    /// Codex's auth.json exists (never opened).
    pub chatgpt_connecte: bool,
    /// Hermes Agent is on this computer.
    pub hermes: bool,
    /// The evening reflection reads Hermes's conversations: $HERMES_DB, else ~/.hermes/state.db, is there.
    pub hermes_lu: bool,
}

impl Facts {
    /// As _conseil.decider reads a fact: one it does not know is false.
    fn get(&self, name: &str) -> bool {
        match name {
            "porte" => self.porte,
            "claude" => self.claude,
            "claude_connecte" => self.claude_connecte,
            "chatgpt" => self.chatgpt,
            "chatgpt_connecte" => self.chatgpt_connecte,
            "hermes" => self.hermes,
            "hermes_lu" => self.hermes_lu,
            _ => false,
        }
    }
}

/// The first rule whose every condition matches, with its sentence, and Hermes's when it is there.
/// None only if the block cannot be read (a test proves it can).
pub fn decide(f: &Facts) -> Option<Advice> {
    let rules = RULES.as_ref()?;
    let matches = |si: &BTreeMap<String, bool>| si.iter().all(|(k, v)| f.get(k) == *v);
    let rule = rules.regles.iter().find(|r| matches(&r.si))?;
    Some(Advice {
        conseil: rule.conseil.clone(),
        text: rules.phrases.get(&rule.phrase)?.clone(),
        hermes: rules.hermes.iter().find(|r| matches(&r.si)).and_then(|r| rules.phrases.get(&r.phrase).cloned()),
    })
}

/// This computer's facts. `login`: what `claude auth status` said, when the diagnosis read it.
pub fn probe(home: &Path, login: ClaudeLogin) -> Facts {
    let env = |k: &str| std::env::var_os(k);
    let codex_home = codex_home(home, &env);
    let codex = codex::Setup::for_user(codex_home.as_deref().unwrap_or(&home.join(".codex"))).ok().is_some_and(|s| codex::find(&s).is_some());
    let place = Place { home, env: &env, applications: None, path: sys::tool_path() };
    probe_with(&place, detect::claude_bin().is_some(), login, codex, codex_home.as_deref(), agents::codex_open(home))
}

/// The advice now, for the screen that asks who the brain works with (before any diagnosis).
pub fn now(home: &Path) -> Option<Advice> {
    decide(&probe(home, detect::claude_login()))
}

/// What the probes look at: a home, an environment, and (tests) the folders holding macOS apps.
struct Place<'a> {
    home: &'a Path,
    env: &'a dyn Fn(&str) -> Option<OsString>,
    applications: Option<Vec<PathBuf>>,
    path: OsString,
}

fn probe_with(p: &Place, claude_bin: bool, login: ClaudeLogin, codex_bin: bool, codex_home: Option<&Path>, porte: bool) -> Facts {
    let auth = codex_home.is_some_and(|h| there(&h.join("auth.json")));
    let (claude_app, chatgpt_app) = apps(p);
    Facts {
        porte,
        claude: claude_app || (claude_bin && login.is_logged_in()),
        claude_connecte: claude_bin && login == ClaudeLogin::Subscribed,
        chatgpt: codex_bin || auth || chatgpt_app,
        chatgpt_connecte: auth,
        hermes: hermes(p),
        hermes_lu: hermes_read(p),
    }
}

/// _agents.codex_home(): $CODEX_HOME, else ~/.codex; None when it is not a full path.
fn codex_home(home: &Path, env: &dyn Fn(&str) -> Option<OsString>) -> Option<PathBuf> {
    let raw = env("CODEX_HOME").map(|v| v.to_string_lossy().trim().to_string()).unwrap_or_default();
    if raw.is_empty() {
        return Some(home.join(".codex"));
    }
    let path = PathBuf::from(raw);
    path.is_absolute().then_some(path)
}

/// There, without opening it nor following its last part (os.path.lexists).
fn there(p: &Path) -> bool {
    fs::symlink_metadata(p).is_ok()
}

/// (Claude's desktop app, ChatGPT's or Codex's). Linux has none of them.
fn apps(p: &Place) -> (bool, bool) {
    if cfg!(windows) && p.applications.is_none() {
        let local = (p.env)("LOCALAPPDATA").map_or_else(|| p.home.join("AppData").join("Local"), PathBuf::from);
        let packages = local.join("Packages");
        let claude = there(&local.join("AnthropicClaude").join("claude.exe")) || package(&packages, "claude_");
        return (claude, package(&packages, "openai.chatgpt-desktop_"));
    }
    let roots = match &p.applications {
        Some(dirs) => dirs.clone(),
        None if cfg!(target_os = "macos") => vec![PathBuf::from("/Applications"), p.home.join("Applications")],
        None => Vec::new(),
    };
    let app = |root: &Path, name: &str| there(&root.join(name).join("Contents").join("Info.plist"));
    let claude = roots.iter().any(|r| app(r, "Claude.app"));
    let chatgpt = roots.iter().any(|r| app(r, "ChatGPT.app") || app(r, "Codex.app"));
    (claude, chatgpt)
}

/// A Store package folder whose name starts with `prefix` (lowercase): Windows' names ignore case.
fn package(packages: &Path, prefix: &str) -> bool {
    fs::read_dir(packages).is_ok_and(|entries| entries.flatten().any(|e| e.file_name().to_string_lossy().to_lowercase().starts_with(prefix)))
}

/// $HERMES_HOME (else ~/.hermes) with its config, its database or its install folder, or `hermes` on
/// the PATH or in ~/.local/bin. Its .env (keys) and login files are never opened.
fn hermes(p: &Place) -> bool {
    let set = (p.env)("HERMES_HOME").map(|v| v.to_string_lossy().trim().to_string()).unwrap_or_default();
    let dir = if set.is_empty() { p.home.join(".hermes") } else { PathBuf::from(set) };
    if ["config.yaml", "state.db", "hermes-agent"].iter().any(|n| there(&dir.join(n))) {
        return true;
    }
    let exe = sys::exe("hermes");
    std::iter::once(p.home.join(".local").join("bin"))
        .chain(std::env::split_paths(&p.path).filter(|d| d.is_absolute()))
        .any(|d| d.join(&exe).is_file())
}

/// daily-reflection.py's HERMES_DB ($HERMES_DB, else ~/.hermes/state.db) is there: the evening
/// reflection reads Hermes's conversations.
fn hermes_read(p: &Place) -> bool {
    let set = (p.env)("HERMES_DB").map(|v| v.to_string_lossy().trim().to_string()).unwrap_or_default();
    there(&if set.is_empty() { p.home.join(".hermes").join("state.db") } else { PathBuf::from(set) })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::tests::python;
    use serde_json::{json, Value};
    use std::process::Command;

    fn hooks() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..").join("engine").join("hooks")
    }

    fn all_facts() -> Vec<Facts> {
        (0..128u8)
            .map(|b| Facts {
                porte: b & 1 != 0,
                claude: b & 2 != 0,
                claude_connecte: b & 4 != 0,
                chatgpt: b & 8 != 0,
                chatgpt_connecte: b & 16 != 0,
                hermes: b & 32 != 0,
                hermes_lu: b & 64 != 0,
            })
            .collect()
    }

    #[test]
    fn the_kits_block_is_read() {
        let rules = RULES.as_ref().expect("CONSEIL_JSON of _conseil.py parses");
        assert!(rules.regles.len() >= 4, "{} rules", rules.regles.len());
        for r in &rules.regles {
            assert!(rules.phrases.contains_key(&r.phrase), "rule without its sentence: {}", r.phrase);
        }
        assert!(rules.phrases.contains_key("hermes"));
        assert!(all_facts().iter().all(|f| decide(f).is_some()), "a case no rule answers");
    }

    #[test]
    fn while_codex_is_closed_it_is_never_offered() {
        for f in all_facts().into_iter().filter(|f| !f.porte) {
            let a = decide(&f).unwrap();
            assert_eq!(a.conseil, "claude", "{f:?}");
            assert!(!a.text.contains("travailler avec Codex"), "{f:?}: {}", a.text);
        }
        // ChatGPT's user is told what Claude costs, and nothing is promised of Codex.
        let chatgpt = decide(&Facts { chatgpt: true, ..Facts::default() }).unwrap();
        assert!(chatgpt.text.contains("Claude Pro (environ 20\u{a0}€ par mois)"), "{}", chatgpt.text);
        assert!(!chatgpt.text.contains("Codex"), "{}", chatgpt.text);
        let opened = decide(&Facts { porte: true, chatgpt: true, ..Facts::default() }).unwrap();
        assert_eq!(opened.conseil, "codex");
        let both = decide(&Facts { porte: true, claude: true, chatgpt: true, ..Facts::default() }).unwrap();
        assert_eq!(both.conseil, "les_deux");
    }

    #[test]
    fn hermes_gets_its_own_sentence_and_nothing_installs_it() {
        let a = decide(&Facts { hermes: true, ..Facts::default() }).unwrap();
        let h = a.hermes.expect("a sentence when Hermes is there");
        assert!(h.contains("je ne l'installe pas"), "{h}");
        // The evening reading only where the reflection reads it.
        assert!(!h.contains("soir"), "{h}");
        let read = decide(&Facts { hermes: true, hermes_lu: true, ..Facts::default() }).unwrap().hermes.unwrap();
        assert!(read.contains("chaque soir ton cerveau lit aussi ses conversations"), "{read}");
        assert!(decide(&Facts::default()).unwrap().hermes.is_none());
        assert!(decide(&Facts { hermes_lu: true, ..Facts::default() }).unwrap().hermes.is_none());
    }

    /// The kit's _conseil.decider and conseil() on every case: the same advice, word for word.
    #[test]
    fn the_app_and_the_command_advise_alike() {
        let cases: Vec<Value> = all_facts().iter().map(|f| serde_json::to_value(f).unwrap()).collect();
        let script = "import json, sys\nsys.path.insert(0, sys.argv[1])\nimport _conseil\n\
                      out = [_conseil.conseil(f) for f in json.loads(sys.stdin.read())]\n\
                      print(json.dumps([{k: c[k] for k in ('conseil', 'phrase', 'hermes')} for c in out]))";
        let mut child = Command::new(python())
            .args(["-I", "-c", script, &hooks().to_string_lossy()])
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .spawn()
            .unwrap();
        use std::io::Write;
        child.stdin.take().unwrap().write_all(json!(cases).to_string().as_bytes()).unwrap();
        let out = child.wait_with_output().unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        let theirs: Vec<Value> = serde_json::from_slice(&out.stdout).unwrap();
        assert_eq!(theirs.len(), 128);
        for (f, t) in all_facts().iter().zip(theirs) {
            let a = decide(f).unwrap();
            assert_eq!(json!({"conseil": a.conseil, "phrase": a.text, "hermes": a.hermes}), t, "{f:?}");
        }
    }

    fn place<'a>(home: &'a Path, env: &'a dyn Fn(&str) -> Option<OsString>, apps: Vec<PathBuf>) -> Place<'a> {
        Place { home, env, applications: Some(apps), path: OsString::new() }
    }

    fn no_env(_: &str) -> Option<OsString> {
        None
    }

    #[test]
    fn what_the_probes_see() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        let apps = home.join("Applications");
        let p = place(home, &no_env, vec![apps.clone()]);
        let codex_home = home.join(".codex");
        let out = ClaudeLogin::LoggedOut;
        assert_eq!(probe_with(&p, false, out, false, Some(&codex_home), false), Facts::default());

        // Claude Code logged in with a subscription; an API key is not one, yet whoever logged in uses
        // Claude.
        let f = probe_with(&p, true, ClaudeLogin::Subscribed, false, Some(&codex_home), false);
        assert!(f.claude && f.claude_connecte);
        let f = probe_with(&p, true, ClaudeLogin::Billed, false, Some(&codex_home), false);
        assert!(f.claude && !f.claude_connecte);
        // Installed and nobody logged in (the app installs it too): no sign of a Claude user.
        assert_eq!(probe_with(&p, true, out, false, Some(&codex_home), false), Facts::default());
        // A login read without Claude Code is no login of it.
        assert_eq!(probe_with(&p, false, ClaudeLogin::Subscribed, false, Some(&codex_home), false), Facts::default());

        fs::create_dir_all(apps.join("Claude.app/Contents")).unwrap();
        fs::write(apps.join("Claude.app/Contents/Info.plist"), "").unwrap();
        let f = probe_with(&p, false, out, false, Some(&codex_home), false);
        assert!(f.claude && !f.chatgpt);
        fs::create_dir_all(apps.join("Codex.app/Contents")).unwrap();
        fs::write(apps.join("Codex.app/Contents/Info.plist"), "").unwrap();
        assert!(probe_with(&p, false, out, false, Some(&codex_home), false).chatgpt);

        fs::create_dir_all(&codex_home).unwrap();
        fs::write(codex_home.join("auth.json"), "{}").unwrap();
        let f = probe_with(&p, false, out, false, Some(&codex_home), true);
        assert!(f.chatgpt && f.chatgpt_connecte && f.porte);
        // CODEX_HOME that is not a full path: nowhere to look.
        assert!(!probe_with(&p, false, out, false, None, false).chatgpt_connecte);
        assert_eq!(codex_home_of(home, Some("relative/codex")), None);
        assert_eq!(codex_home_of(home, Some("  ")), Some(home.join(".codex")));
    }

    fn codex_home_of(home: &Path, v: Option<&str>) -> Option<PathBuf> {
        let v = v.map(OsString::from);
        codex_home(home, &move |k| if k == "CODEX_HOME" { v.clone() } else { None })
    }

    #[test]
    fn hermes_by_its_folder_or_its_program() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        let p = place(home, &no_env, Vec::new());
        assert!(!hermes(&p));
        // A .env alone (its keys) is not Hermes: nothing reads it.
        fs::create_dir_all(home.join(".hermes")).unwrap();
        fs::write(home.join(".hermes/.env"), "OPENAI_API_KEY=sk-never-read").unwrap();
        assert!(!hermes(&p));
        fs::write(home.join(".hermes/config.yaml"), "").unwrap();
        assert!(hermes(&p));

        let elsewhere = home.join("ailleurs");
        fs::create_dir_all(elsewhere.join("hermes-agent")).unwrap();
        let set = elsewhere.clone().into_os_string();
        let env = move |k: &str| (k == "HERMES_HOME").then(|| set.clone());
        let other = tempfile::tempdir().unwrap();
        assert!(hermes(&place(other.path(), &env, Vec::new())));
        // What the evening reflection reads: ~/.hermes/state.db, or $HERMES_DB.
        assert!(!hermes_read(&place(other.path(), &env, Vec::new())));
        fs::write(elsewhere.join("state.db"), "").unwrap();
        let db = elsewhere.join("state.db").into_os_string();
        let env_db = move |k: &str| (k == "HERMES_DB").then(|| db.clone());
        assert!(hermes_read(&place(other.path(), &env_db, Vec::new())));
        assert!(!hermes_read(&p));
        fs::write(home.join(".hermes/state.db"), "").unwrap();
        assert!(hermes_read(&p));

        let bin = other.path().join(".local").join("bin");
        fs::create_dir_all(&bin).unwrap();
        assert!(!hermes(&place(other.path(), &no_env, Vec::new())));
        fs::write(bin.join(sys::exe("hermes")), "").unwrap();
        assert!(hermes(&place(other.path(), &no_env, Vec::new())));
    }

    /// Codex's auth.json and Hermes's .env as named pipes: opening one for reading would wait for a
    /// writer forever. The probes return, so they only looked.
    #[cfg(unix)]
    #[test]
    fn no_login_file_is_opened() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        for f in [home.join(".codex/auth.json"), home.join(".hermes/.env")] {
            fs::create_dir_all(f.parent().unwrap()).unwrap();
            assert!(Command::new("mkfifo").arg(&f).status().unwrap().success());
        }
        let (tx, rx) = std::sync::mpsc::channel();
        let h = home.to_path_buf();
        std::thread::spawn(move || {
            let p = place(&h, &no_env, Vec::new());
            let _ = tx.send(probe_with(&p, false, ClaudeLogin::LoggedOut, false, Some(&h.join(".codex")), false));
        });
        let f = rx.recv_timeout(std::time::Duration::from_secs(10)).expect("a probe opened a login file");
        assert!(f.chatgpt_connecte && !f.hermes);
    }

    /// The same homes through the kit's _conseil.sondes: the app sees what the command sees.
    #[test]
    fn the_app_and_the_command_probe_alike() {
        let tmp = tempfile::tempdir().unwrap();
        let mut homes = Vec::new();
        for (i, setup) in ["", "claude_app", "chatgpt_app", "auth", "hermes", "hermes_bin", "all"].iter().enumerate() {
            let home = tmp.path().join(format!("h{i}"));
            let apps = home.join("Applications");
            fs::create_dir_all(&apps).unwrap();
            let put = |rel: &str| {
                let p = home.join(rel);
                fs::create_dir_all(p.parent().unwrap()).unwrap();
                fs::write(p, "").unwrap();
            };
            if ["claude_app", "all"].contains(setup) {
                put("Applications/Claude.app/Contents/Info.plist");
            }
            if ["chatgpt_app", "all"].contains(setup) {
                put("Applications/ChatGPT.app/Contents/Info.plist");
            }
            if ["auth", "all"].contains(setup) {
                put(".codex/auth.json");
            }
            if ["hermes", "all"].contains(setup) {
                put(".hermes/state.db");
            }
            if *setup == "hermes_bin" {
                put(&format!(".local/bin/{}", sys::exe("hermes")));
            }
            homes.push(home);
        }
        // Windows looks for its apps elsewhere than in an Applications folder.
        let apps_here = !cfg!(windows);
        let script = "import json, os, sys\nsys.path.insert(0, sys.argv[1])\nimport _conseil\nout = []\n\
                      for h in json.loads(sys.stdin.read()):\n\
                      \x20   env = {'PATH': '', 'LOCALAPPDATA': os.path.join(h, 'AppData', 'Local')}\n\
                      \x20   out.append(_conseil.sondes(home=h, env=env, claude=[], codex=[], applications=[os.path.join(h, 'Applications')], codex_ouvert=frozenset()))\n\
                      print(json.dumps(out))";
        let mut child = Command::new(python())
            .args(["-I", "-c", script, &hooks().to_string_lossy()])
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .spawn()
            .unwrap();
        use std::io::Write;
        let list: Vec<String> = homes.iter().map(|h| h.to_string_lossy().into_owned()).collect();
        child.stdin.take().unwrap().write_all(json!(list).to_string().as_bytes()).unwrap();
        let out = child.wait_with_output().unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        let theirs: Vec<Value> = serde_json::from_slice(&out.stdout).unwrap();
        for (home, t) in homes.iter().zip(theirs) {
            let local = home.join("AppData").join("Local").into_os_string();
            let env = move |k: &str| (k == "LOCALAPPDATA").then(|| local.clone());
            let p = Place { home, env: &env, applications: apps_here.then(|| vec![home.join("Applications")]), path: OsString::new() };
            let ours = probe_with(&p, false, ClaudeLogin::LoggedOut, false, Some(&home.join(".codex")), false);
            assert_eq!(serde_json::to_value(ours).unwrap(), t, "{}", home.display());
        }
    }

    /// `claude auth status` read by the app (detect::login_of) and by the kit (_conseil.connexion):
    /// the same login, with the setup-token given or not, one billed per use counted as a
    /// subscription by neither.
    #[test]
    fn the_app_and_the_command_read_a_login_alike() {
        let plans = ["\"pro\"", "\"max\"", "\"team\"", "\"enterprise\"", "\"free\"", "\"\"", "null", "4"];
        let mut said: Vec<String> = plans.iter().map(|p| format!(r#"{{"authMethod":"claude.ai","subscriptionType":{p}}}"#)).collect();
        said.extend(["claude.ai", "oauth_token", "api_key", "api_key_helper", "third_party", "none"].map(|m| format!(r#"note{{"authMethod":"{m}"}}"#)));
        said.extend([r#"{"loggedIn":true}"#, r#"{"authMethod":4}"#, "Not logged in", "", "{oops"].map(String::from));
        let cases: Vec<(String, bool)> = said.iter().flat_map(|s| [(s.clone(), false), (s.clone(), true)]).collect();
        let script = "import json, sys\nsys.path.insert(0, sys.argv[1])\nimport _conseil\n\
                      print(json.dumps([_conseil.connexion(s, j) for s, j in json.loads(sys.stdin.read())]))";
        let mut child = Command::new(python())
            .args(["-I", "-c", script, &hooks().to_string_lossy()])
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .spawn()
            .unwrap();
        use std::io::Write;
        child.stdin.take().unwrap().write_all(json!(cases).to_string().as_bytes()).unwrap();
        let out = child.wait_with_output().unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        let theirs: Vec<Value> = serde_json::from_slice(&out.stdout).unwrap();
        assert_eq!(theirs.len(), cases.len());
        for ((s, token), t) in cases.iter().zip(theirs) {
            let ours = match detect::login_of(s, *token) {
                ClaudeLogin::Subscribed => json!("abonne"),
                ClaudeLogin::Billed => json!("facture"),
                ClaudeLogin::LoggedOut => Value::Null,
            };
            assert_eq!(ours, t, "{s} {token}");
        }
    }
}
