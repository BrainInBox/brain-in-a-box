//! Claude Code, asked one question with nothing but the question: the app's side of the kit's
//! launcher, engine/hooks/_headless_claude.py, as codex.rs is _headless_codex.py's.
//!
//! `claude -p` runs with no tool but its JSON answer, no MCP server, no hook, no skill, none of
//! the user's settings, in a folder of its own, with a short list for environment (`child_env`).
//! The question goes through stdin, after every option. Its stream is checked line by line
//! (`Watch`): the start-up line first, fail closed, and a run that does not behave is stopped at
//! once. The repair asks it (repair.rs).
//!
//! Where the two differ, this side keeps the repair's words: no start-up line is a bad start
//! (the launcher says "start"), no result line is an answer off (the launcher says "api").

use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use crate::detect::{parse_version, Version};
use crate::sys;

/// The first version checked with every flag the question relies on.
pub const MIN_CLAUDE: Version = (2, 1, 296);
/// The question depends on each of these: a Claude Code that lacks one is not used.
pub const REQUIRED_FLAGS: &[&str] = &[
    "--restricted",
    "--safe-mode",
    "--tools",
    "--permission-prompts",
    "--json-schema",
    "--max-budget-usd",
    "--no-session-persistence",
    "--strict-mcp-config",
];

/// One question, as _headless_claude.run takes it. `schema` is the JSON text Claude gets, as is.
pub struct Question<'a> {
    /// What Claude reads on stdin.
    pub text: &'a str,
    pub schema: &'a str,
    pub system: &'a str,
    pub model: Option<&'a str>,
    pub effort: Option<&'a str>,
    pub budget_usd: f64,
    pub max_turns: u32,
}

/// Why there is no answer, in the launcher's kinds (HeadlessError) where the app has them.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ClaudeError {
    Cancelled,
    Timeout,
    Quota,
    Auth,
    /// Claude's service answered with another error.
    Api,
    /// Claude did not start, or did not run, as required (tools, MCP, permissions, key, model...).
    Init,
    /// No answer, or one outside the schema.
    Invalid,
}

/// No shell: each element is one argument. The question goes through stdin, after
/// every option (a variadic option would swallow a prompt placed last).
pub fn argv(q: &Question) -> Vec<String> {
    let mut a: Vec<String> = [
        "-p", "--restricted", "--safe-mode", "--tools", "",
        "--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Agent", "Task", "Skill",
        "--strict-mcp-config", "--disable-slash-commands", "--no-chrome",
        "--settings", r#"{"disableAllHooks":true}"#,
        "--permission-mode", "dontAsk", "--permission-prompts", "none", "--no-session-persistence",
    ]
    .map(String::from)
    .to_vec();
    a.extend(["--max-turns".into(), q.max_turns.to_string(), "--max-budget-usd".into(), format!("{:.2}", q.budget_usd)]);
    if let Some(model) = q.model {
        a.extend(["--model".into(), model.into()]);
    }
    if let Some(effort) = q.effort {
        a.extend(["--effort".into(), effort.into()]);
    }
    a.extend(["--json-schema", q.schema, "--append-system-prompt", q.system, "--output-format", "stream-json", "--verbose"].map(String::from));
    a
}

/// The child's whole environment. USER is required: without it Claude Code misses
/// its login in the keychain. The server-side advisor is off: no extra model, no extra quota.
#[cfg(not(windows))]
pub fn child_env(home: &Path, user: &str, path: &str, tmp: &Path) -> Vec<(&'static str, String)> {
    vec![
        ("HOME", home.to_string_lossy().into_owned()),
        ("USER", user.to_string()),
        ("LOGNAME", user.to_string()),
        ("TMPDIR", tmp.to_string_lossy().into_owned()),
        ("LANG", "fr_FR.UTF-8".into()),
        ("PATH", path.to_string()),
        ("DISABLE_AUTOUPDATER", "1".into()),
        ("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", "1".into()),
    ]
}

/// The same short list on Windows, plus what a Windows program needs to start at all
/// (SystemRoot above everything: without it, no network). Claude Code keeps its login
/// in the profile (USERPROFILE). Nothing else of the app's environment goes.
#[cfg(windows)]
pub fn child_env(home: &Path, user: &str, path: &str, tmp: &Path) -> Vec<(&'static str, String)> {
    let tmp = tmp.to_string_lossy().into_owned();
    let profile = |var: &str, under: &str| std::env::var(var).unwrap_or_else(|_| home.join(under).to_string_lossy().into_owned());
    let mut env = vec![
        ("USERPROFILE", home.to_string_lossy().into_owned()),
        ("USERNAME", user.to_string()),
        ("APPDATA", profile("APPDATA", r"AppData\Roaming")),
        ("LOCALAPPDATA", profile("LOCALAPPDATA", r"AppData\Local")),
        ("TEMP", tmp.clone()),
        ("TMP", tmp),
        ("SystemRoot", sys::system_root().to_string_lossy().into_owned()),
        ("LANG", "fr_FR.UTF-8".into()),
        ("PATH", path.to_string()),
        ("DISABLE_AUTOUPDATER", "1".into()),
        ("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", "1".into()),
    ];
    for var in WINDOWS_SYSTEM_ENV {
        if let Ok(v) = std::env::var(var) {
            env.push((var, v));
        }
    }
    env
}

/// Windows' own folders and the machine, nobody's secret.
#[cfg(windows)]
const WINDOWS_SYSTEM_ENV: &[&str] = &[
    "windir", "SystemDrive", "ComSpec", "PATHEXT", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData",
    "CommonProgramFiles", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
];

#[cfg(not(windows))]
pub fn login_name(home: &Path) -> String {
    std::env::var("USER")
        .or_else(|_| std::env::var("LOGNAME"))
        .ok()
        .filter(|u| !u.is_empty())
        .or_else(|| home.file_name().map(|n| n.to_string_lossy().into_owned()))
        .unwrap_or_default()
}

#[cfg(windows)]
pub fn login_name(home: &Path) -> String {
    std::env::var("USERNAME")
        .ok()
        .filter(|u| !u.is_empty())
        .or_else(|| home.file_name().map(|n| n.to_string_lossy().into_owned()))
        .unwrap_or_default()
}

/// Where a question runs: the child's whole environment, its folder, its time, « Arrêter ».
pub struct Place<'a> {
    pub env: &'a [(&'a str, &'a str)],
    pub cwd: &'a Path,
    pub limit: Duration,
    /// Set from anywhere to stop Claude; set here too when its run does not behave.
    pub cancel: &'a AtomicBool,
}

/// What one question leaves: the watch over Claude's stream, the time it took, the answer or why none.
pub struct Asked {
    pub watch: Watch,
    pub secs: f64,
    pub answer: Result<Value, ClaudeError>,
}

/// Claude Code at `claude` asked `q`. Each line it prints goes to `sink` first, as it comes: the
/// repair keeps them, masked. Blocks until it answers, `place.cancel`, or `place.limit`.
pub fn ask(claude: &Path, q: &Question, place: &Place, sink: &mut dyn FnMut(&str)) -> Asked {
    let args = argv(q);
    let args: Vec<&str> = args.iter().map(String::as_str).collect();
    let iso = sys::Isolated { env: place.env, cwd: place.cwd, stdin: q.text, limit: place.limit, cancel: place.cancel };
    let started = Instant::now();
    let mut watch = Watch::default();
    let code = sys::run_isolated(claude, &args, &iso, &mut |line| {
        sink(&line);
        if !watch.line(&line) {
            place.cancel.store(true, Ordering::SeqCst);
        }
    });
    let secs = started.elapsed().as_secs_f64();
    let timed_out = code == -1 && started.elapsed() >= place.limit;
    let answer = watch.conclude(place.cancel.load(Ordering::SeqCst), timed_out);
    Asked { watch, secs, answer }
}

pub struct Init {
    pub version: String,
}

/// Claude's stream, line by line: the start-up check first, then the errors and the result.
#[derive(Default)]
pub struct Watch {
    pub init: Option<Result<Init, String>>,
    /// `error` and `api_error` of the assistant messages ("rate_limit", "authentication_failed"...).
    api_errors: Vec<String>,
    pub result: Option<Value>,
}

impl Watch {
    /// False once the run must stop: Claude did not start as required.
    pub fn line(&mut self, line: &str) -> bool {
        let Ok(v) = serde_json::from_str::<Value>(line) else { return self.init_ok_or_pending() };
        if !v.is_object() {
            return self.init_ok_or_pending();
        }
        let kind = v["type"].as_str().unwrap_or_default().to_string();
        if self.init.is_none() {
            if kind != "system" {
                self.init = Some(Err(format!("« {kind} » avant l'init")));
            } else if v["subtype"] == "init" {
                self.init = Some(check_init(&v));
            }
            // Other system events may come first (ui_invalidate when logged out).
            return self.init_ok_or_pending();
        }
        match kind.as_str() {
            "assistant" => {
                for key in ["error", "api_error"] {
                    if let Some(e) = v[key].as_str() {
                        self.api_errors.push(e.to_string());
                    }
                }
                // The init said no tool but the answer; a tool used anyway (server side,
                // MCP, one the init did not list) ends the run like a bad init.
                if let Some(name) = foreign_tool(&v) {
                    self.init = Some(Err(format!("outil utilisé : {name}")));
                }
            }
            "result" => self.result = Some(v),
            _ => {}
        }
        self.init_ok_or_pending()
    }

    fn init_ok_or_pending(&self) -> bool {
        !matches!(self.init, Some(Err(_)))
    }

    pub fn version(&self) -> Option<String> {
        self.init.as_ref().and_then(|i| i.as_ref().ok()).map(|i| i.version.clone())
    }

    /// Claude's answer, or why there is none. Fail closed: no init, no answer.
    pub fn conclude(&self, cancelled: bool, timed_out: bool) -> Result<Value, ClaudeError> {
        match &self.init {
            Some(Err(_)) => return Err(ClaudeError::Init),
            _ if cancelled => return Err(ClaudeError::Cancelled),
            _ if timed_out => return Err(ClaudeError::Timeout),
            None => return Err(ClaudeError::Init),
            Some(Ok(_)) => {}
        }
        let result = self.result.as_ref().ok_or(ClaudeError::Invalid)?;
        parse_result(result, &self.api_errors)
    }
}

/// The first tool other than the answer itself in an assistant message, any kind
/// (tool_use, server_tool_use, mcp_tool_use...).
fn foreign_tool(v: &Value) -> Option<String> {
    let content = v.pointer("/message/content")?.as_array()?;
    content.iter().find_map(|block| {
        let kind = block.get("type").and_then(Value::as_str)?;
        let name = block.get("name").and_then(Value::as_str).unwrap_or("?");
        (kind.ends_with("tool_use") && !(kind == "tool_use" && name == "StructuredOutput")).then(|| name.to_string())
    })
}

/// The start-up line must show Claude exactly as the options ask: no tool but the
/// answer, no MCP server, no prompt for permissions, no API key, built-in plugins
/// only, a known version, a Sonnet model.
fn check_init(v: &Value) -> Result<Init, String> {
    let s = |k: &str| v.get(k).and_then(Value::as_str);
    if v.get("tools") != Some(&json!(["StructuredOutput"])) {
        return Err(format!("outils : {}", v.get("tools").unwrap_or(&Value::Null)));
    }
    if !v.get("mcp_servers").and_then(Value::as_array).is_some_and(Vec::is_empty) {
        return Err("serveurs MCP".into());
    }
    // What --safe-mode and --disable-slash-commands turn off: the proof they did.
    for field in ["skills", "slash_commands"] {
        if !v.get(field).and_then(Value::as_array).is_some_and(Vec::is_empty) {
            return Err(format!("{field} : {}", v.get(field).unwrap_or(&Value::Null)));
        }
    }
    if s("output_style") != Some("default") {
        return Err(format!("output_style {:?}", s("output_style")));
    }
    if s("permissionMode") != Some("dontAsk") {
        return Err(format!("permissionMode {:?}", s("permissionMode")));
    }
    if s("apiKeySource") != Some("none") {
        return Err(format!("apiKeySource {:?}", s("apiKeySource")));
    }
    let builtin = |p: &Value| {
        p.get("path").and_then(Value::as_str) == Some("builtin")
            && p.get("source").and_then(Value::as_str).is_some_and(|s| s.ends_with("@builtin"))
    };
    if !v.get("plugins").and_then(Value::as_array).is_some_and(|ps| ps.iter().all(builtin)) {
        return Err("plugins".into());
    }
    let version = s("claude_code_version").unwrap_or_default();
    if parse_version(version).is_none_or(|v| v < MIN_CLAUDE) {
        return Err(format!("version {version:?}"));
    }
    if !s("model").is_some_and(|m| m.contains("sonnet")) {
        return Err(format!("modèle {:?}", s("model")));
    }
    Ok(Init { version: version.to_string() })
}

/// The structured answer from the last result. The errors Claude's service types on
/// the assistant message come first; their text is only a fallback.
fn parse_result(result: &Value, api_errors: &[String]) -> Result<Value, ClaudeError> {
    let subtype = result.get("subtype").and_then(Value::as_str).unwrap_or_default();
    let is_error = result.get("is_error").and_then(Value::as_bool).unwrap_or(true);
    match subtype {
        "success" if !is_error => result.get("structured_output").filter(|o| o.is_object()).cloned().ok_or(ClaudeError::Invalid),
        "success" => {
            let said = result.get("result").and_then(Value::as_str).unwrap_or_default();
            let typed = |names: &[&str]| api_errors.iter().any(|e| names.contains(&e.as_str()));
            if typed(&["rate_limit", "usage_limit_reached"]) || (said.contains("hit your") && said.contains("limit")) {
                Err(ClaudeError::Quota)
            } else if typed(&["authentication_failed"]) || said.contains("/login") || said.contains("Not logged in") {
                Err(ClaudeError::Auth)
            } else {
                Err(ClaudeError::Api)
            }
        }
        // error_max_turns, error_max_budget_usd, error_max_structured_output_retries...
        _ => Err(ClaudeError::Invalid),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const FIXTURES: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/repair");

    fn fixture(name: &str) -> String {
        std::fs::read_to_string(format!("{FIXTURES}/{name}")).unwrap()
    }

    #[cfg(not(windows))]
    #[test]
    fn child_env_is_the_short_list() {
        let env = child_env(Path::new("/Users/jo"), "jo", "/bin", Path::new("/t/tmp"));
        let keys: Vec<&str> = env.iter().map(|(k, _)| *k).collect();
        assert_eq!(
            keys,
            ["HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "PATH", "DISABLE_AUTOUPDATER", "CLAUDE_CODE_DISABLE_ADVISOR_TOOL"]
        );
        assert_eq!(env[0].1, "/Users/jo");
        assert_eq!(env[3].1, "/t/tmp");
    }

    #[cfg(windows)]
    #[test]
    fn child_env_is_the_short_list() {
        let env = child_env(Path::new(r"C:\Users\jo"), "jo", r"C:\bin", Path::new(r"C:\t\tmp"));
        let keys: Vec<&str> = env.iter().map(|(k, _)| *k).collect();
        assert_eq!(
            keys[..11],
            [
                "USERPROFILE", "USERNAME", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "SystemRoot", "LANG", "PATH",
                "DISABLE_AUTOUPDATER", "CLAUDE_CODE_DISABLE_ADVISOR_TOOL"
            ]
        );
        assert!(keys[11..].iter().all(|k| WINDOWS_SYSTEM_ENV.contains(k)), "{keys:?}");
        assert_eq!((env[0].1.as_str(), env[4].1.as_str()), (r"C:\Users\jo", r"C:\t\tmp"));
        for secret in ["ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "GITHUB_TOKEN", "USERDOMAIN", "ONEDRIVE"] {
            assert!(!keys.iter().any(|k| k.eq_ignore_ascii_case(secret)), "{secret}");
        }
    }

    fn first_init(name: &str) -> Value {
        fixture(name)
            .lines()
            .filter_map(|l| serde_json::from_str::<Value>(l).ok())
            .find(|v| v["type"] == "system" && v["subtype"] == "init")
            .unwrap()
    }

    #[test]
    fn init_must_match_the_options() {
        let ok = first_init("plan-ok.jsonl");
        assert_eq!(check_init(&ok).map(|i| i.version).as_deref(), Ok("2.1.296"));
        for bad in ["init-bash", "init-mcp", "init-api-key", "init-old-version", "init-opus", "init-plugin"] {
            assert!(check_init(&first_init(&format!("{bad}.jsonl"))).is_err(), "{bad}");
        }
        for field in [
            "tools",
            "mcp_servers",
            "permissionMode",
            "apiKeySource",
            "plugins",
            "claude_code_version",
            "model",
            "skills",
            "slash_commands",
            "output_style",
        ] {
            let mut v = ok.clone();
            v.as_object_mut().unwrap().remove(field);
            assert!(check_init(&v).is_err(), "without {field}");
        }
        // A user's skill, command or output style in the init: --safe-mode did not hold.
        for (field, value) in [("skills", json!(["brain-boost"])), ("slash_commands", json!(["mx-fix"])), ("output_style", json!("Explanatory"))] {
            let mut v = ok.clone();
            v[field] = value;
            assert!(check_init(&v).is_err(), "{field}");
        }
        let mut auto = ok.clone();
        auto["permissionMode"] = json!("bypassPermissions");
        assert!(check_init(&auto).is_err());
    }

    fn watch(name: &str) -> (Watch, bool) {
        let mut w = Watch::default();
        let mut went_on = true;
        for line in fixture(name).lines() {
            went_on &= w.line(line);
        }
        (w, went_on)
    }

    #[test]
    fn the_stream_gives_a_verdict_or_a_reason() {
        let cases: &[(&str, Result<(), ClaudeError>)] = &[
            ("plan-ok.jsonl", Ok(())),
            ("plan-suspect.jsonl", Ok(())),
            ("plan-round2-effort-high.jsonl", Ok(())),
            ("enum-invented.jsonl", Ok(())),
            ("remedy-other-step.jsonl", Ok(())),
            ("no-structured-output.jsonl", Err(ClaudeError::Invalid)),
            ("broken-json.jsonl", Err(ClaudeError::Invalid)),
            ("budget-exceeded.jsonl", Err(ClaudeError::Invalid)),
            ("max-turns.jsonl", Err(ClaudeError::Invalid)),
            ("quota.jsonl", Err(ClaudeError::Quota)),
            ("auth-expired.jsonl", Err(ClaudeError::Auth)),
            ("not-logged-in.jsonl", Err(ClaudeError::Auth)),
            ("init-bash.jsonl", Err(ClaudeError::Init)),
            ("init-mcp.jsonl", Err(ClaudeError::Init)),
            ("init-api-key.jsonl", Err(ClaudeError::Init)),
            ("init-old-version.jsonl", Err(ClaudeError::Init)),
            ("init-opus.jsonl", Err(ClaudeError::Init)),
            ("init-plugin.jsonl", Err(ClaudeError::Init)),
        ];
        for (name, expected) in cases {
            let (w, went_on) = watch(name);
            let got = w.conclude(false, false).map(|_| ());
            assert_eq!(&got, expected, "{name}");
            assert_eq!(went_on, *expected != Err(ClaudeError::Init), "{name}");
        }
        let (w, _) = watch("plan-ok.jsonl");
        assert_eq!(w.conclude(true, false), Err(ClaudeError::Cancelled));
        assert_eq!(w.conclude(false, true), Err(ClaudeError::Timeout));
        assert_eq!(Watch::default().conclude(false, false), Err(ClaudeError::Init));
        let mut early = Watch::default();
        assert!(!early.line(r#"{"type":"assistant","message":{}}"#), "an answer before the init");
    }

    #[test]
    fn quota_and_auth_fall_back_on_the_text() {
        let r = |text: &str| json!({ "type": "result", "subtype": "success", "is_error": true, "result": text });
        assert_eq!(parse_result(&r("You've hit your weekly limit · resets Mon"), &[]), Err(ClaudeError::Quota));
        assert_eq!(parse_result(&r("Not logged in · Please run /login"), &[]), Err(ClaudeError::Auth));
        assert_eq!(parse_result(&r("Overloaded"), &[]), Err(ClaudeError::Api));
        assert_eq!(parse_result(&r(""), &["usage_limit_reached".into()]), Err(ClaudeError::Quota));
        let no_flag = json!({ "subtype": "success", "structured_output": {} });
        assert_eq!(parse_result(&no_flag, &[]), Err(ClaudeError::Api), "is_error missing counts as an error");
    }

    #[test]
    fn a_tool_used_anyway_stops_claude() {
        let ok = fixture("plan-ok.jsonl");
        let mut lines: Vec<&str> = ok.lines().collect();
        let bash = lines[1].replacen(r#""name":"StructuredOutput""#, r#""name":"Bash""#, 1);
        lines[1] = &bash;
        let server = r#"{"type":"assistant","message":{"content":[{"type":"server_tool_use","name":"web_search","input":{}}]}}"#;
        for stream in [lines.join("\n"), format!("{}\n{server}\n{}", lines[0], lines[2..].join("\n"))] {
            let mut w = Watch::default();
            let mut went_on = true;
            for l in stream.lines() {
                went_on &= w.line(l);
            }
            assert!(!went_on, "{stream}");
            assert_eq!(w.conclude(true, false).map(|_| ()), Err(ClaudeError::Init));
        }
        // The answer itself is no foreign tool.
        assert_eq!(foreign_tool(&serde_json::from_str(ok.lines().nth(1).unwrap()).unwrap()), None);
    }
}
