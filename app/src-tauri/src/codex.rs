//! Codex, the other AI the kit can work with: found where OpenAI's installer puts it, its ChatGPT
//! login read without a billed key, and one question asked with nothing but the question.
//!
//! The contract is the kit's own, engine/hooks/_headless_codex.py, check for check: the same -c,
//! the same refusals and pauses, with the same words, in the same order, so that the app and the
//! kit's night tasks stop asking Codex for the same reasons. Codex has no "no tool" switch and a tool
//! that runs does not show in its stream (docs/proposals/codex.md, §2.2): "no tool" holds by
//! prevention alone, every check failing closed. The test `the_app_and_the_launcher_answer_alike`
//! runs this file and the launcher side by side on the scenarios of engine/tests/codex_contract.py.
//!
//! Where the two differ, this side is the stricter: a stream line that opens an object and is not
//! JSON ends the run (Python reads NaN and skips the rest), an id_token with a character outside
//! base64url is unreadable (Python drops it), a profile that is not a regular file is unreadable
//! (Python would wait on a pipe).
//!
//! Nothing asks Codex a question for the user yet: the repair does from phase 9 on, the screens
//! from 10b, once agents.json lists Codex on a system where _agents.py lets it be chosen.

use std::collections::{HashSet, VecDeque};
use std::fs;
use std::io::{self, Read};
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::LazyLock;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use regex::Regex;
use serde::Serialize;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

use crate::{perms, sys};

/// The version step 0 checked every option and setting below with.
pub const MIN_CODEX: (u64, u64, u64) = (0, 162, 1);
const REQUIRED_FLAGS: &[&str] = &[
    "--json",
    "--ephemeral",
    "--skip-git-repo-check",
    "--ignore-user-config",
    "--ignore-rules",
    "--sandbox",
    "--cd",
    "--output-schema",
    "--output-last-message",
];
const PROBE_TIMEOUT: Duration = Duration::from_secs(60);
const FILE_CAP: usize = 1 << 20;
const FEATURES_CAP: usize = 256 * 1024;
const ANSWER_CAP: usize = 4_000_000;
/// _headless_claude.py's, which runs Codex for the launcher: a longer line is dropped, more output ends the run.
const LINE_CAP: usize = 4_000_000;
const STREAM_CAP: usize = 32_000_000;
const ERR_TAIL: usize = 20;
const FEATURES_FILE: &str = "codex-features.json";
/// The features that give the model a tool, run code, or reach a server of their own (step 0's
/// classification and the spike's list): the data file must keep them, and Codex must know them.
const CORE: &[&str] = &[
    "shell_tool",
    "view_image",
    "image_generation",
    "goals",
    "sleep_tool",
    "code_mode_host",
    "apps",
    "plugins",
    "remote_plugin",
    "multi_agent",
    "multi_agent_v2",
    "memories",
    "skill_search",
    "tool_suggest",
    "browser_use",
    "computer_use",
    "in_app_browser",
    "code_mode",
    "hooks",
];
/// Stays on whatever -c says; adds no tool while shell_tool (in CORE) is off (step 0).
const FORCED: &[&str] = &["unified_exec"];
/// Every -c but the features, the instructions, the effort and the MCP servers, in the launcher's order.
const SETTINGS: &[&str] = &[
    r#"model_provider="openai""#,
    r#"openai_base_url="""#,
    r#"chatgpt_base_url="https://chatgpt.com/backend-api/""#,
    r#"cli_auth_credentials_store="file""#,
    r#"approval_policy="never""#,
    r#"web_search="disabled""#,
    "agents.enabled=false",
    "tools.experimental_request_user_input.enabled=false",
    "tools.update_plan.enabled=false",
    "skills.include_instructions=false",
    "skills.bundled.enabled=false",
    "project_doc_max_bytes=0",
    "include_environment_context=false",
    "include_permissions_instructions=false",
    "include_apps_instructions=false",
    "include_collaboration_mode_instructions=false",
    // A system config.toml's own, both passed after every turn: its text to the model, the
    // session (the vault) to its program.
    r#"developer_instructions="""#,
    "notify=[]",
    r#"history.persistence="none""#,
    "check_for_update_on_startup=false",
    "analytics.enabled=false",
    "feedback.enabled=false",
    // A system config.toml's [otel] is read: its log exporter would get the whole prompt.
    r#"otel.exporter="none""#,
    r#"otel.trace_exporter="none""#,
    r#"otel.metrics_exporter="none""#,
    "otel.log_user_prompt=false",
    "otel.log_agent_responses=false",
];
const LOGIN_OK: &str = "Logged in using ChatGPT";
const LOGIN_BILLED: &[&str] = &[
    "Logged in using an API key",
    "Logged in using workload identity",
    "Logged in using access token",
    "Logged in using personal access token",
    "Logged in using Amazon Bedrock",
];
/// Plans without a workspace: a Business, Edu or Enterprise workspace's own settings pass over every -c.
const PERSONAL_PLANS: &[&str] = &["free", "go", "plus", "pro", "prolite", "promax"];
/// The items of an answer: the message, its reasoning, warnings ("Code Mode is unavailable...").
const ITEMS: &[&str] = &["agent_message", "reasoning", "error"];
const PROXY_VARS: &[&str] =
    &["HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "all_proxy", "no_proxy", "SSL_CERT_FILE", "CODEX_CA_CERTIFICATE"];
const WINDOWS_VARS: &[&str] = &[
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "SystemRoot",
    "SystemDrive",
    "windir",
    "PATHEXT",
    "COMSPEC",
    "USERNAME",
    "ProgramFiles",
    "ProgramW6432",
    "ProgramData",
];
/// The names $CODEX_HOME/.env may give Codex (with WINDOWS_VARS and PROXY_VARS).
const ENV_NAMES: &[&str] = &["HOME", "PATH", "LANG", "USER", "LOGNAME", "TMPDIR", "TEMP", "TMP", "BRAIN_HEADLESS"];
const EXTRA_DIRS: &[&str] = &["/opt/homebrew/bin", "/usr/local/bin"];
const CHECK_INSTRUCTIONS: &str = "Answer only with the JSON object of the schema.";

static FEATURE_NAME: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"\A[a-z][a-z0-9_]{0,63}\z").ok());
static ROW_NAME: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\z").ok());
static MCP_NAME: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"\A[A-Za-z0-9_-]{1,64}\z").ok());
static MODEL_NAME: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\z").ok());
static EFFORT: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"\A[a-z]{1,16}\z").ok());
static DOTENV_NAME: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"\A[A-Za-z_][A-Za-z0-9_.]*\z").ok());
static VERSION: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"([0-9]+)\.([0-9]+)\.([0-9]+)(?:-[0-9A-Za-z.]+)?").ok());
static HELP_WORDS: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"[^A-Za-z0-9-]+").ok());
static QUOTA: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"(?i)hit your (usage limit|spend cap)|usage limit|quota exceeded|upgrade to plus").ok());
static AUTH: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"(?i)\b401\b|unauthori[sz]ed|missing bearer|could not be refreshed|sign in again|log ?in again|not logged in").ok());
/// Python's `\s` and str.split(): Unicode's spaces and the four separators below 0x20 it adds.
const SPACE: &str = r"[\s\x1c-\x1f]";

fn matches(re: &Option<Regex>, text: &str) -> bool {
    re.as_ref().is_some_and(|r| r.is_match(text))
}

// ── Errors ──────────────────────────────────────────────────────────────────

/// The launcher's HeadlessError kinds, plus what Python calls a ValueError (a question the call
/// cannot take) and the repair's « Arrêter ».
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Kind {
    Start,
    Unsupported,
    Auth,
    Init,
    Timeout,
    Quota,
    Api,
    Invalid,
    Value,
    Cancelled,
}

/// Why the checks before a question refused: Codex is paused (unsupported) or not logged in as needed (auth).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Reason {
    Version,
    Flags,
    LoggedOut,
    Billed,
    Features,
    Dotenv,
    Managed,
    Mcp,
}

/// `detail` is for the journal, in English like the launcher's: never what Codex printed.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CodexError {
    pub kind: Kind,
    pub reason: Option<Reason>,
    pub detail: String,
}

impl std::fmt::Display for CodexError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{:?}: {}", self.kind, self.detail)
    }
}

fn fail(kind: Kind, detail: impl Into<String>) -> CodexError {
    CodexError { kind, reason: None, detail: detail.into() }
}

fn paused(reason: Reason, detail: impl Into<String>) -> CodexError {
    CodexError { kind: Kind::Unsupported, reason: Some(reason), detail: detail.into() }
}

fn auth(reason: Reason, detail: impl Into<String>) -> CodexError {
    CodexError { kind: Kind::Auth, reason: Some(reason), detail: detail.into() }
}

fn value(detail: impl Into<String>) -> CodexError {
    fail(Kind::Value, detail)
}

// ── Where it all is ─────────────────────────────────────────────────────────

/// The environment the app was started with, or a stand-in (tests).
pub type Vars = Box<dyn Fn(&str) -> Option<String> + Send + Sync>;

/// An administrator's layers, which pass over the kit's -c. None: this system's own.
#[derive(Default)]
pub struct Layers {
    /// requirements.toml and a legacy managed_config.toml.
    pub managed: Option<Vec<PathBuf>>,
    /// macOS configuration profiles (com.openai.codex).
    pub mdm: Option<Vec<PathBuf>>,
    /// Windows' %ProgramData%; None: the known folder, as Codex finds it.
    pub program_data: Option<PathBuf>,
}

impl Layers {
    fn program_data(&self) -> Option<PathBuf> {
        #[cfg(windows)]
        if self.program_data.is_none() {
            return windows_acl::known_program_data();
        }
        self.program_data.clone()
    }

    fn managed_files(&self) -> Vec<PathBuf> {
        if let Some(list) = &self.managed {
            return list.clone();
        }
        let root = if cfg!(windows) {
            self.program_data().unwrap_or_else(|| PathBuf::from(r"C:\ProgramData")).join("OpenAI").join("Codex")
        } else {
            PathBuf::from("/etc/codex")
        };
        vec![root.join("requirements.toml"), root.join("managed_config.toml")]
    }

    fn mdm_files(&self, user: Option<&str>) -> Vec<PathBuf> {
        if let Some(list) = &self.mdm {
            return list.clone();
        }
        if !cfg!(target_os = "macos") {
            return Vec::new();
        }
        let base = Path::new("/Library/Managed Preferences");
        let mut files = vec![base.join("com.openai.codex.plist")];
        files.extend(user.map(|u| base.join(u).join("com.openai.codex.plist")));
        files
    }
}

/// What a question to Codex, or a look at it, needs to know.
pub struct Setup {
    pub home: PathBuf,
    /// The CODEX_HOME the installer recorded in agents.json: launchd and the Task Scheduler do not
    /// see one exported in a shell profile, and the login lives there.
    pub codex_home: PathBuf,
    /// Where the private folder of each call goes.
    pub work_root: PathBuf,
    /// The kit's list of the features turned off (engine/codex-features.json, installed next to the launcher).
    pub features: PathBuf,
    pub layers: Layers,
    pub vars: Vars,
    /// argv that stands for codex (tests); None: the user's.
    pub command: Option<Vec<String>>,
}

impl Setup {
    /// The user's: their home and the app's environment, the app's work folder, the list the
    /// install put next to the kit's launcher, this system's layers.
    #[cfg_attr(not(test), allow(dead_code))] // phase 9 on
    pub fn for_user(codex_home: &Path) -> io::Result<Setup> {
        let home = sys::home();
        Ok(Setup {
            features: installed_features(&home),
            work_root: sys::work_root()?,
            codex_home: codex_home.to_path_buf(),
            home,
            layers: Layers::default(),
            vars: Box::new(|k| std::env::var(k).ok()),
            command: None,
        })
    }

    /// A variable, unless unset or empty (the launcher reads `os.environ.get(k)` as true or false).
    fn var(&self, name: &str) -> Option<String> {
        (self.vars)(name).filter(|v| !v.is_empty())
    }
}

/// Where the install puts it: next to the launcher that reads it (~/.claude/hooks/brain).
fn installed_features(home: &Path) -> PathBuf {
    home.join(".claude").join("hooks").join("brain").join(FEATURES_FILE)
}

// ── The binary ──────────────────────────────────────────────────────────────

/// The user's codex: the official installer's place first, then the PATH (and Homebrew's folders,
/// which launchd's bare PATH lacks). On Windows only an .exe.
pub fn find(setup: &Setup) -> Option<PathBuf> {
    if cfg!(windows) {
        let base = setup.var("LOCALAPPDATA").map_or_else(|| setup.home.join("AppData").join("Local"), PathBuf::from);
        let local = base.join("Programs").join("OpenAI").join("Codex").join("bin").join("codex.exe");
        if local.is_file() {
            return Some(local);
        }
        let path = setup.var("PATH")?;
        return which("codex.exe", std::env::split_paths(&path).collect()).filter(|p| p.extension().is_some_and(|e| e.eq_ignore_ascii_case("exe")));
    }
    let local = setup.home.join(".local").join("bin").join("codex");
    if local.is_file() && sys::is_executable(&local) {
        return Some(local);
    }
    let mut dirs: Vec<PathBuf> = setup.var("PATH").map(|p| std::env::split_paths(&p).collect()).unwrap_or_default();
    dirs.extend(EXTRA_DIRS.iter().map(PathBuf::from));
    which("codex", dirs)
}

fn which(name: &str, dirs: Vec<PathBuf>) -> Option<PathBuf> {
    let mut seen = HashSet::new();
    dirs.into_iter()
        // An empty entry would mean the current folder.
        .filter(|d| !d.as_os_str().is_empty() && seen.insert(d.clone()))
        .map(|d| d.join(name))
        .find(|p| fs::metadata(p).is_ok_and(|m| !m.is_dir()) && (cfg!(windows) || sys::is_executable(p)))
}

/// The program and the arguments that come before ours (tests run a stand-in through python).
struct Codex {
    program: PathBuf,
    prefix: Vec<String>,
}

fn resolve(setup: &Setup) -> Result<Codex, CodexError> {
    let codex = match &setup.command {
        Some(argv) if !argv.is_empty() => Codex { program: PathBuf::from(&argv[0]), prefix: argv[1..].to_vec() },
        _ => {
            let program = find(setup).ok_or_else(|| fail(Kind::Start, "Codex not found (~/.local/bin/codex or PATH)"))?;
            Codex { program, prefix: Vec::new() }
        }
    };
    // npm's codex.cmd goes through cmd.exe, which reads our arguments as its own syntax.
    if cfg!(windows) && !codex.program.extension().is_some_and(|e| e.eq_ignore_ascii_case("exe")) {
        return Err(fail(Kind::Start, format!("{} is not an .exe", file_name(&codex.program))));
    }
    Ok(codex)
}

fn file_name(p: &Path) -> String {
    p.file_name().map_or_else(String::new, |n| n.to_string_lossy().into_owned())
}

/// The CODEX_HOME agents.json recorded: Codex refuses one that is not a folder, and its login is there.
fn codex_home(setup: &Setup) -> Result<PathBuf, CodexError> {
    let h = &setup.codex_home;
    if !h.is_absolute() || !h.is_dir() {
        return Err(auth(Reason::LoggedOut, "Codex's folder (CODEX_HOME) is not there: run `codex login` with your ChatGPT account, then the install again"));
    }
    Ok(normpath(h))
}

/// os.path.normpath: `.` and empty parts gone, `..` taken back, without looking at the disk.
fn normpath(p: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    let mut names = 0usize;
    for c in p.components() {
        match c {
            Component::Prefix(_) | Component::RootDir => out.push(c.as_os_str()),
            Component::CurDir => {}
            Component::ParentDir if names > 0 => {
                out.pop();
                names -= 1;
            }
            Component::ParentDir => {
                if !out.has_root() {
                    out.push("..");
                }
            }
            Component::Normal(n) => {
                out.push(n);
                names += 1;
            }
        }
    }
    if out.as_os_str().is_empty() {
        PathBuf::from(".")
    } else {
        out
    }
}

/// A path as Python's realpath gives it on Windows: without \\?\ when it can be.
fn realpath(p: &Path) -> Option<PathBuf> {
    let real = fs::canonicalize(p).ok()?;
    #[cfg(windows)]
    {
        let s = real.to_string_lossy();
        if let Some(unc) = s.strip_prefix(r"\\?\UNC\") {
            return Some(PathBuf::from(format!(r"\\{unc}")));
        }
        if let Some(local) = s.strip_prefix(r"\\?\") {
            return Some(PathBuf::from(local));
        }
    }
    Some(real)
}

/// PATH of the child: the folder of codex (and of its link target) and the system's, nothing the user added.
fn child_path(program: &Path, setup: &Setup) -> String {
    let abs = if program.is_absolute() { normpath(program) } else { normpath(&std::env::current_dir().unwrap_or_default().join(program)) };
    let real = realpath(program).unwrap_or_else(|| abs.clone());
    let parent = |p: &Path| p.parent().map_or_else(|| p.to_path_buf(), Path::to_path_buf);
    let mut dirs = vec![parent(&abs), parent(&real)];
    if cfg!(windows) {
        let root = PathBuf::from(setup.var("SystemRoot").unwrap_or_else(|| r"C:\Windows".into()));
        dirs.extend([root.join("System32"), root.clone(), root.join("System32").join("Wbem")]);
    } else {
        dirs.extend(["/usr/bin", "/bin", "/usr/sbin", "/sbin"].map(PathBuf::from));
    }
    let mut seen = HashSet::new();
    let dirs: Vec<String> = dirs.iter().map(|d| d.to_string_lossy().into_owned()).filter(|d| seen.insert(d.clone())).collect();
    dirs.join(if cfg!(windows) { ";" } else { ":" })
}

fn login_name(setup: &Setup) -> Option<String> {
    let name = setup.var("USER").or_else(|| setup.var("LOGNAME")).or_else(|| setup.var("USERNAME"));
    if name.is_some() || cfg!(windows) {
        return name;
    }
    sys::output_text(Path::new("/usr/bin/id"), &["-un"], Duration::from_secs(10))
}

/// The child's whole environment, an allow-list: no OPENAI_API_KEY, CODEX_API_KEY,
/// CODEX_ACCESS_TOKEN or OPENAI_BASE_URL (Codex would use the key before the login, and bill
/// it), no RUST_LOG, no other variable of the app's.
fn child_env(setup: &Setup, tmp: &Path, path: String, codex_home: &Path) -> Vec<(String, String)> {
    let mut env: Vec<(String, String)> = vec![
        ("HOME".into(), setup.home.to_string_lossy().into_owned()),
        ("PATH".into(), path),
        ("LANG".into(), setup.var("LANG").unwrap_or_else(|| "en_US.UTF-8".into())),
        ("CODEX_HOME".into(), codex_home.to_string_lossy().into_owned()),
        ("BRAIN_HEADLESS".into(), "1".into()),
    ];
    if let Some(user) = login_name(setup) {
        env.push(("USER".into(), user.clone()));
        env.push(("LOGNAME".into(), user));
    }
    let tmp = tmp.to_string_lossy().into_owned();
    if cfg!(windows) {
        env.extend(WINDOWS_VARS.iter().filter_map(|k| setup.var(k).map(|v| (k.to_string(), v))));
        env.push(("TEMP".into(), tmp.clone()));
        env.push(("TMP".into(), tmp));
    } else {
        env.push(("TMPDIR".into(), tmp));
    }
    env.extend(PROXY_VARS.iter().filter_map(|k| setup.var(k).map(|v| (k.to_string(), v))));
    env
}

// ── The settings ────────────────────────────────────────────────────────────

/// A small regular file's text: never waits on a pipe, never reads more than cap, UTF-8 or an error.
fn read_small(path: &Path, cap: usize) -> io::Result<String> {
    String::from_utf8(read_capped(path, cap)?).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))
}

/// A small regular file's bytes: never waits on a pipe, never reads more than cap.
fn read_capped(path: &Path, cap: usize) -> io::Result<Vec<u8>> {
    let mut open = fs::OpenOptions::new();
    open.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        open.custom_flags(O_NONBLOCK);
    }
    let f = open.open(path)?;
    if !f.metadata()?.is_file() {
        return Err(io::Error::other(format!("{} is not a regular file", path.display())));
    }
    let mut data = Vec::new();
    f.take(cap as u64 + 1).read_to_end(&mut data)?;
    if data.len() > cap {
        return Err(io::Error::other(format!("{} is too big", path.display())));
    }
    Ok(data)
}

#[cfg(target_os = "macos")]
const O_NONBLOCK: i32 = 0x0004;
#[cfg(target_os = "linux")]
const O_NONBLOCK: i32 = 0o4000;

/// The names engine/codex-features.json turns off.
fn load_features(path: &Path) -> Result<Vec<String>, CodexError> {
    let bad = |why: &str| paused(Reason::Features, format!("{FEATURES_FILE} {why}: Codex is paused until the install is run again"));
    let d: Value = read_small(path, FEATURES_CAP).ok().and_then(|t| serde_json::from_str(&t).ok()).ok_or_else(|| bad("is missing or unreadable"))?;
    if !d.get("version").is_some_and(|v| v.is_i64() && v.as_i64() == Some(1)) {
        return Err(bad("is not a version 1 list"));
    }
    let names: Vec<String> = d
        .get("coupees")
        .and_then(Value::as_array)
        .filter(|a| !a.is_empty())
        .and_then(|a| a.iter().map(|n| n.as_str().filter(|n| matches(&FEATURE_NAME, n)).map(String::from)).collect())
        .filter(|n: &Vec<String>| n.iter().collect::<HashSet<_>>().len() == n.len())
        .ok_or_else(|| bad("holds something that is not a feature name"))?;
    let missing: Vec<&str> = CORE.iter().copied().filter(|c| !names.iter().any(|n| n == c)).collect();
    if !missing.is_empty() {
        return Err(bad(&format!("lacks {}", missing.join(", "))));
    }
    Ok(names)
}

/// json.dumps of a string: a TOML basic string, everything but printable ASCII escaped.
fn toml_string(s: &str) -> String {
    let mut out = String::from("\"");
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            ' '..='~' => out.push(c),
            _ => {
                for unit in c.encode_utf16(&mut [0; 2]) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
    }
    out.push('"');
    out
}

/// Every -c of the call: our features off, SETTINGS, the instructions file, the effort and the MCP servers off.
fn config_flags(coupees: &[String], mcp_off: &[String], instructions: Option<&Path>, effort: Option<&str>) -> Vec<String> {
    let mut items: Vec<String> = coupees.iter().map(|n| format!("features.{n}=false")).collect();
    items.extend(SETTINGS.iter().map(|s| s.to_string()));
    if let Some(i) = instructions {
        items.push(format!("model_instructions_file={}", toml_string(&i.to_string_lossy())));
    }
    if let Some(e) = effort {
        items.push(format!("model_reasoning_effort={}", toml_string(e)));
    }
    items.extend(mcp_off.iter().map(|n| format!("mcp_servers.{n}.enabled=false")));
    items.into_iter().flat_map(|i| ["-c".to_string(), i]).collect()
}

/// codex's arguments. The prompt is `-`: stdin only (an argument and stdin would both be sent).
fn exec_argv(cage: &Cage, coupees: &[String], mcp_off: &[String], model: Option<&str>, effort: Option<&str>) -> Vec<String> {
    let path = |p: &Path| p.to_string_lossy().into_owned();
    let mut a: Vec<String> = ["exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules", "-s", "read-only", "-C"]
        .map(String::from)
        .to_vec();
    a.extend([path(&cage.work), "--output-schema".into(), path(&cage.schema), "-o".into(), path(&cage.answer)]);
    if let Some(m) = model {
        a.extend(["-m".into(), m.into()]);
    }
    a.extend(config_flags(coupees, mcp_off, Some(&cage.system), effort));
    a.push("-".into());
    a
}

/// Codex sends the schema in strict mode: every object closed, every property required.
fn strict_schema(schema: &Value, at: &str) -> Result<(), CodexError> {
    match schema {
        Value::Array(list) => {
            for (i, sub) in list.iter().enumerate() {
                strict_schema(sub, &format!("{at}[{i}]"))?;
            }
            Ok(())
        }
        Value::Object(o) => {
            if o.get("type").and_then(Value::as_str) == Some("object") || o.contains_key("properties") {
                if o.get("additionalProperties") != Some(&Value::Bool(false)) {
                    return Err(value(format!("{at}: Codex answers in strict mode: additionalProperties must be false")));
                }
                let mut props: Vec<String> = o.get("properties").and_then(Value::as_object).map(|p| p.keys().cloned().collect()).unwrap_or_default();
                let mut required: Vec<String> = match o.get("required") {
                    None | Some(Value::Null) => Vec::new(),
                    Some(Value::Array(r)) => r.iter().map(|x| x.as_str().map(String::from).unwrap_or_else(|| x.to_string())).collect(),
                    Some(other) => vec![other.to_string()],
                };
                props.sort();
                required.sort();
                if props != required {
                    return Err(value(format!("{at}: Codex answers in strict mode: every property must be required")));
                }
            }
            for (key, sub) in o {
                match key.as_str() {
                    "properties" | "$defs" | "definitions" => {
                        if let Some(subs) = sub.as_object() {
                            for (name, s) in subs {
                                strict_schema(s, &format!("{at}.{name}"))?;
                            }
                        }
                    }
                    "items" | "anyOf" | "oneOf" | "allOf" => strict_schema(sub, &format!("{at}.{key}"))?,
                    _ => {}
                }
            }
            Ok(())
        }
        _ => Ok(()),
    }
}

// ── Running codex ───────────────────────────────────────────────────────────

/// Codex's folders for one call: fresh, private, outside any vault, gone afterwards. Named like the
/// launcher's (brain-codex-*/travail): the reflection leaves out a session that ran in one.
struct Cage {
    root: PathBuf,
    work: PathBuf,
    schema: PathBuf,
    system: PathBuf,
    answer: PathBuf,
    env: Vec<(String, String)>,
    /// The same with an empty CODEX_HOME: the layers the call reads, the user's config.toml aside.
    empty_env: Vec<(String, String)>,
}

impl Cage {
    fn new(codex: &Codex, setup: &Setup, codex_home: &Path) -> Result<Cage, CodexError> {
        let made = |e: io::Error| fail(Kind::Start, format!("cannot make Codex's folder: {e}"));
        let root = fresh_dir(&setup.work_root).map_err(made)?;
        let mut cage = Cage {
            work: root.join("travail"),
            schema: root.join("schema.json"),
            system: root.join("system.md"),
            answer: root.join("answer.json"),
            root,
            env: Vec::new(),
            empty_env: Vec::new(),
        };
        let tmp = cage.root.join("tmp");
        let empty = cage.root.join("vide");
        for d in [&cage.work, &tmp, &empty] {
            perms::create_dir(d).map_err(made)?;
        }
        cage.env = child_env(setup, &tmp, child_path(&codex.program, setup), codex_home);
        cage.empty_env = cage.env.iter().map(|(k, v)| (k.clone(), if k == "CODEX_HOME" { empty.to_string_lossy().into_owned() } else { v.clone() })).collect();
        Ok(cage)
    }
}

impl Drop for Cage {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

/// work_root/brain-codex-<8 letters or digits>, made 0700, as its real path.
fn fresh_dir(work_root: &Path) -> io::Result<PathBuf> {
    static COUNT: AtomicU32 = AtomicU32::new(0);
    const LETTERS: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789_";
    for _ in 0..100 {
        let nanos = SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_nanos());
        let seed = format!("{nanos}-{}-{}", std::process::id(), COUNT.fetch_add(1, Ordering::SeqCst));
        let name: String = Sha256::digest(seed.as_bytes()).iter().take(8).map(|b| LETTERS[*b as usize % LETTERS.len()] as char).collect();
        let dir = work_root.join(format!("brain-codex-{name}"));
        match perms::create_dir(&dir) {
            Ok(()) => return realpath(&dir).ok_or_else(|| io::Error::other("the folder went away")),
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e),
        }
    }
    Err(io::Error::other("no free name"))
}

struct Ran {
    code: Option<i32>,
    timed_out: bool,
    /// Stopped by `on_out` (or by the caller's cancel).
    stopped: bool,
    err: Vec<String>,
}

/// Codex with only `env`, in the cage's work folder, `stdin` as its input. Each stdout line goes to
/// `on_out`, which returns false to stop the run; stderr keeps its last lines. Past `limit`, or once
/// the caller's `cancel` is set, Codex and what it started are killed. `cancel` is also how a run
/// stops itself.
fn run(p: &Call, args: &[String], env: &[(String, String)], stdin: &str, limit: Duration, on_out: &mut dyn FnMut(&str) -> bool) -> Result<Ran, CodexError> {
    let (codex, cancel) = (p.codex, p.cancel);
    let argv: Vec<&str> = codex.prefix.iter().chain(args).map(String::as_str).collect();
    let env: Vec<(&str, &str)> = env.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect();
    let iso = sys::Isolated { env: &env, cwd: &p.cage.work, stdin, limit, cancel };
    let started = Instant::now();
    let (mut err, mut total, mut stopped, mut overflow) = (VecDeque::new(), 0usize, false, false);
    let code = sys::run_isolated_pipes(&codex.program, &argv, &iso, &mut |pipe, line| {
        if stopped || line.len() > LINE_CAP {
            return;
        }
        match pipe {
            sys::Pipe::Err => {
                let mut end = line.len().min(1000);
                while !line.is_char_boundary(end) {
                    end -= 1;
                }
                err.push_back(line[..end].trim_end_matches(is_space).to_string());
                if err.len() > ERR_TAIL {
                    err.pop_front();
                }
            }
            sys::Pipe::Out => {
                total += line.len() + 1;
                overflow = total > STREAM_CAP;
                if overflow || !on_out(&line) {
                    stopped = true;
                    cancel.store(true, Ordering::SeqCst);
                }
            }
        }
    })
    .map_err(|e| fail(Kind::Start, format!("cannot run {}: {e}", file_name(&codex.program))))?;
    if overflow {
        return Err(fail(Kind::Invalid, format!("more than {STREAM_CAP} bytes of output")));
    }
    let stopped = stopped || (code.is_none() && cancel.load(Ordering::SeqCst));
    Ok(Ran { timed_out: code.is_none() && !stopped && started.elapsed() >= limit, code, stopped, err: err.into() })
}

/// What every run of Codex goes with: Codex, the call's folders, and the caller's « Arrêter ».
struct Call<'a> {
    codex: &'a Codex,
    cage: &'a Cage,
    cancel: &'a AtomicBool,
}

/// (exit code, stdout, last stderr lines) of a short probe. Its stderr is ours to compare, never to copy.
fn probe(p: &Call, args: &[String], env: &[(String, String)], what: &str) -> Result<(Option<i32>, String, Vec<String>), CodexError> {
    let mut lines = Vec::new();
    let ran = run(p, args, env, "", PROBE_TIMEOUT, &mut |l| {
        lines.push(l.to_string());
        true
    })?;
    if ran.stopped {
        return Err(fail(Kind::Cancelled, "stopped"));
    }
    if ran.timed_out {
        return Err(fail(Kind::Start, format!("codex {what} did not answer in {}s", PROBE_TIMEOUT.as_secs())));
    }
    Ok((ran.code, lines.join("\n"), ran.err))
}

fn words(list: &[&str]) -> Vec<String> {
    list.iter().map(|s| s.to_string()).collect()
}

// ── The checks before a question ────────────────────────────────────────────

fn check_version(p: &Call) -> Result<String, CodexError> {
    let (_, out, _) = probe(p, &words(&["--version"]), &p.cage.env, "--version")?;
    let caps =
        VERSION.as_ref().and_then(|re| re.captures(&out)).ok_or_else(|| fail(Kind::Start, format!("codex --version said {}", py_repr(&brief(&out, 120)))))?;
    // As Python's int(): any number of digits, compared whole.
    let n = |i: usize| caps.get(i).map_or(0, |m| m.as_str().parse::<u64>().unwrap_or(u64::MAX));
    let version = caps.get(0).map_or("", |m| m.as_str()).to_string();
    if (n(1), n(2), n(3)) < MIN_CODEX {
        let (a, b, c) = MIN_CODEX;
        return Err(paused(Reason::Version, format!("Codex {version} is older than {a}.{b}.{c}: update it (the install does)")));
    }
    let (_, help, _) = probe(p, &words(&["exec", "--help"]), &p.cage.env, "exec --help")?;
    let seen: HashSet<&str> = HELP_WORDS.as_ref().map(|re| re.split(&help).collect()).unwrap_or_default();
    let missing: Vec<&str> = REQUIRED_FLAGS.iter().copied().filter(|f| !seen.contains(f)).collect();
    if !missing.is_empty() {
        return Err(paused(Reason::Flags, format!("this codex exec lacks {}", missing.join(", "))));
    }
    Ok(version)
}

/// Names $CODEX_HOME/.env gives Codex's own environment (it skips CODEX_*); values never read into
/// anything. Err when a line cannot be read the way the kit reads it.
fn dotenv_names(codex_home: &Path) -> Result<Vec<String>, ()> {
    let p = codex_home.join(".env");
    if fs::symlink_metadata(&p).is_err() {
        return Ok(Vec::new());
    }
    let text = read_small(&p, FILE_CAP).map_err(|_| ())?;
    let mut names = Vec::new();
    for raw in splitlines(&text) {
        let mut line = strip(raw);
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some(rest) = line.strip_prefix("export ") {
            line = rest.trim_start_matches(is_space);
        }
        let (name, eq) = match line.split_once('=') {
            Some((n, _)) => (strip(n), true),
            None => (line, false),
        };
        if !eq || !matches(&DOTENV_NAME, name) {
            return Err(());
        }
        if !name.to_uppercase().starts_with("CODEX_") {
            names.push(name.to_string());
        }
    }
    Ok(names)
}

fn dotenv_refused(name: &str) -> bool {
    let n = name.to_uppercase();
    let known = ENV_NAMES.iter().chain(WINDOWS_VARS).chain(PROXY_VARS).any(|k| k.to_uppercase() == n);
    n == "RUST_LOG" || n.starts_with("OPENAI_") || n.starts_with("SSL_") || n.ends_with("_PROXY") || n.contains("CA") || !known
}

fn check_dotenv(codex_home: &Path) -> Result<(), CodexError> {
    let names = dotenv_names(codex_home)
        .map_err(|()| paused(Reason::Dotenv, "Codex's .env (in CODEX_HOME) cannot be read the way Codex reads it: Codex is paused while it is there"))?;
    let mut refused: Vec<String> = names.iter().filter(|n| dotenv_refused(n)).map(|n| label(n)).collect();
    refused.sort();
    refused.dedup();
    if !refused.is_empty() {
        refused.truncate(5);
        return Err(paused(
            Reason::Dotenv,
            format!(
                "Codex's .env (in CODEX_HOME) sets {}, which could send the question elsewhere or bill it: Codex is paused while it does",
                refused.join(", ")
            ),
        ));
    }
    Ok(())
}

fn settings_in(text: &str) -> bool {
    splitlines(text).iter().map(|l| strip(l)).any(|l| !l.is_empty() && !l.starts_with('#'))
}

/// requirements.toml pins or bounds what -c sets (provider, URLs, features, sandbox...), a legacy
/// managed_config.toml passes over every -c, and so does a profile's: any one with a setting pauses Codex.
fn check_managed(setup: &Setup) -> Result<(), CodexError> {
    for p in setup.layers.managed_files() {
        if fs::symlink_metadata(&p).is_err() {
            continue;
        }
        let text = read_small(&p, FILE_CAP).unwrap_or_else(|_| "unreadable".into());
        if settings_in(&text) {
            let dir = p.parent().map_or_else(String::new, |d| d.display().to_string());
            return Err(paused(Reason::Managed, format!("{} ({dir}) sets Codex's settings above the kit's: Codex is paused while it does", file_name(&p))));
        }
    }
    let user = login_name(setup);
    for p in setup.layers.mdm_files(user.as_deref()) {
        if fs::symlink_metadata(&p).is_ok() && profile_forces(&p) {
            return Err(paused(
                Reason::Managed,
                format!("a configuration profile ({}) sets Codex's settings above the kit's: Codex is paused while it does", file_name(&p)),
            ));
        }
    }
    Ok(())
}

/// A profile that sets config_toml_base64 or requirements_toml_base64, or that cannot be read.
/// Only XML (as plistlib detects it) and binary plists, like plistlib.
#[cfg(target_os = "macos")]
fn profile_forces(path: &Path) -> bool {
    let Ok(data) = read_capped(path, FILE_CAP) else { return true };
    let xml = [&b"<?xml"[..], b"<plist", b"\xef\xbb\xbf<?xml", b"\xef\xbb\xbf<plist"].iter().any(|p| data.starts_with(p));
    let parsed = if data.starts_with(b"bplist00") {
        plist::Value::from_reader(io::Cursor::new(&data))
    } else if xml {
        plist::Value::from_reader_xml(io::Cursor::new(&data))
    } else {
        return true;
    };
    match parsed {
        Err(_) => true,
        Ok(plist::Value::Dictionary(d)) => ["config_toml_base64", "requirements_toml_base64"].iter().any(|k| d.get(k).is_some_and(plist_truthy)),
        Ok(_) => false,
    }
}

/// Python's truth of what plistlib reads.
#[cfg(target_os = "macos")]
fn plist_truthy(v: &plist::Value) -> bool {
    match v {
        plist::Value::String(s) => !s.is_empty(),
        plist::Value::Data(d) => !d.is_empty(),
        plist::Value::Boolean(b) => *b,
        plist::Value::Integer(i) => i.as_signed() != Some(0) && i.as_unsigned() != Some(0),
        plist::Value::Real(r) => *r != 0.0,
        plist::Value::Array(a) => !a.is_empty(),
        plist::Value::Dictionary(d) => !d.is_empty(),
        _ => true,
    }
}

/// Only a Mac reads profiles (Layers::mdm_files): one given anywhere else is refused.
#[cfg(not(target_os = "macos"))]
fn profile_forces(_: &Path) -> bool {
    true
}

/// The plan in a ChatGPT id_token, read as Codex reads it (the claim https://api.openai.com/auth).
/// The token is not checked: it is the user's own file.
fn chatgpt_plan(id_token: Option<&Value>) -> Option<String> {
    let parts: Vec<&str> = id_token?.as_str()?.split('.').collect();
    if parts.len() != 3 || parts.iter().any(|p| p.is_empty()) {
        return None;
    }
    let claims: Value = serde_json::from_slice(&base64url(parts[1])?).ok()?;
    let plan = claims.get("https://api.openai.com/auth")?.get("chatgpt_plan_type")?.as_str()?;
    Some(plan.to_lowercase())
}

/// base64url as urlsafe_b64decode reads it once padded, '+' and '/' too; any other character refused.
fn base64url(s: &str) -> Option<Vec<u8>> {
    let s = s.trim_end_matches('=');
    if s.len() % 4 == 1 {
        return None;
    }
    let (mut out, mut bits, mut n) = (Vec::new(), 0u32, 0u32);
    for c in s.bytes() {
        let v = match c {
            b'A'..=b'Z' => c - b'A',
            b'a'..=b'z' => c - b'a' + 26,
            b'0'..=b'9' => c - b'0' + 52,
            b'-' | b'+' => 62,
            b'_' | b'/' => 63,
            _ => return None,
        };
        bits = (bits << 6) | u32::from(v);
        n += 6;
        if n >= 8 {
            n -= 8;
            out.push((bits >> n) as u8);
            bits &= (1 << n) - 1;
        }
    }
    Some(out)
}

/// Python's truth of a JSON value: null, false, 0, "", [] and {} are false.
fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64() != Some(0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

const BILLED: &str = "Codex is logged in another way than with a ChatGPT account (an API key would be billed): run `codex logout`, then `codex login` with your ChatGPT account.";

/// auth.json says a ChatGPT login of a personal plan, with what refreshes it. Nothing else of it is
/// read: a real ChatGPT login also stores an exchanged OPENAI_API_KEY, which Codex does not use for the model.
fn check_auth_file(codex_home: &Path) -> Result<(), CodexError> {
    let d: Value = read_small(&codex_home.join("auth.json"), FILE_CAP)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .ok_or_else(|| auth(Reason::LoggedOut, "Codex is not logged in (no readable auth.json): run `codex login` with your ChatGPT account"))?;
    let mode = d.as_object().and_then(|o| o.get("auth_mode"));
    if mode.and_then(Value::as_str) == Some("chatgpt") {
        let tokens = d.get("tokens").and_then(Value::as_object);
        if tokens.and_then(|t| t.get("refresh_token")).and_then(Value::as_str).is_none_or(|r| strip(r).is_empty()) {
            return Err(auth(Reason::LoggedOut, "Codex's ChatGPT login is incomplete: run `codex login` again"));
        }
        if !chatgpt_plan(tokens.and_then(|t| t.get("id_token"))).is_some_and(|p| PERSONAL_PLANS.contains(&p.as_str())) {
            return Err(paused(
                Reason::Managed,
                "Codex is logged in with a ChatGPT workspace account (Business, Edu, Enterprise), or one the kit cannot read: its administrator's settings pass over the kit's, so Codex is paused",
            ));
        }
        return Ok(());
    }
    let keyed = d.is_object() && truthy(d.get("OPENAI_API_KEY"));
    if !(truthy(mode) || keyed) {
        return Err(auth(Reason::LoggedOut, "Codex is not logged in: run `codex login` with your ChatGPT account"));
    }
    Err(auth(Reason::Billed, BILLED))
}

/// `codex login status`, read as Codex reads auth.json for the call. Its line, on stderr, is
/// compared to fixed ones and never kept: an API key's shows part of the key.
fn check_login(p: &Call, flags: &[String]) -> Result<(), CodexError> {
    let args: Vec<String> = flags.iter().cloned().chain(words(&["login", "status"])).collect();
    let (code, _, err) = probe(p, &args, &p.cage.env, "login status")?;
    let first = err.iter().map(|l| strip(l)).find(|l| !l.is_empty()).unwrap_or("");
    if code == Some(0) && first == LOGIN_OK {
        return Ok(());
    }
    if LOGIN_BILLED.iter().any(|b| first.starts_with(b)) {
        return Err(auth(Reason::Billed, BILLED));
    }
    Err(auth(Reason::LoggedOut, "Codex's ChatGPT login could not be confirmed (codex login status): run `codex login` with your ChatGPT account"))
}

/// `codex features list` with our -c: every CORE feature known to this Codex, and none on but FORCED
/// and the removed stage. Fail closed: a line the kit cannot read, a feature it does not know turned on.
fn check_features(text: &str, coupees: &[String]) -> Result<(), CodexError> {
    let bad = |why: String| paused(Reason::Features, format!("{why}: Codex is paused until the kit knows this version (re-run the install)"));
    let mut rows: Vec<(String, String, bool)> = Vec::new();
    for line in splitlines(text) {
        if strip(line).is_empty() {
            continue;
        }
        let parts: Vec<&str> = split_space(line);
        let Some((last, middle)) = parts.split_last() else { continue };
        if parts.len() < 3 || !matches!(*last, "true" | "false") || !matches(&ROW_NAME, parts[0]) {
            return Err(bad("codex features list printed a line the kit cannot read".into()));
        }
        let row = (parts[0].to_string(), middle[1..].join(" "), *last == "true");
        match rows.iter_mut().find(|r| r.0 == row.0) {
            Some(r) => *r = row,
            None => rows.push(row),
        }
    }
    let missing: Vec<&str> = CORE.iter().copied().filter(|c| !rows.iter().any(|r| r.0 == *c)).collect();
    if !missing.is_empty() {
        return Err(bad(format!("this Codex does not list {}, which the kit turns off", missing.join(", "))));
    }
    let mut on: Vec<&str> = rows.iter().filter(|(n, stage, v)| *v && stage != "removed" && !FORCED.contains(&n.as_str())).map(|r| r.0.as_str()).collect();
    on.sort();
    let (stuck, unknown): (Vec<&str>, Vec<&str>) = on.iter().partition(|n| coupees.iter().any(|c| c == *n));
    if !stuck.is_empty() {
        return Err(bad(format!("Codex keeps {} on although the kit turns it off", stuck[..stuck.len().min(8)].join(", "))));
    }
    if !unknown.is_empty() {
        return Err(bad(format!("Codex turns on {}, which the kit does not know", unknown[..unknown.len().min(8)].join(", "))));
    }
    Ok(())
}

/// [(name, enabled)] of `codex mcp list --json`.
fn mcp_list(p: &Call, env: &[(String, String)], flags: &[String]) -> Result<Vec<(String, bool)>, CodexError> {
    let args: Vec<String> = flags.iter().cloned().chain(words(&["mcp", "list", "--json"])).collect();
    let (code, out, _) = probe(p, &args, env, "mcp list")?;
    let v: Option<Value> = if code == Some(0) { serde_json::from_str(&out).ok() } else { None };
    v.as_ref()
        .and_then(Value::as_array)
        .and_then(|list| list.iter().map(|s| Some((s.get("name")?.as_str()?.to_string(), s.get("enabled")?.as_bool()?))).collect())
        .ok_or_else(|| paused(Reason::Mcp, "codex mcp list did not answer as the kit reads it: Codex is paused"))
}

/// Whether the user's config.toml defines this MCP server: the call ignores that file. Comments are
/// cut first (from any #: a # inside a string only hides more, never declares).
fn declared_by_user(codex_home: &Path, name: &str) -> bool {
    let Ok(text) = read_small(&codex_home.join("config.toml"), FILE_CAP) else { return false };
    let text: Vec<&str> = splitlines(&text).into_iter().map(|l| l.split('#').next().unwrap_or("")).collect();
    let text = text.join("\n");
    let n = regex::escape(name);
    let name = format!(r#"(?:"{n}"|'{n}'|{n})"#);
    let found = |re: &str| Regex::new(re).is_ok_and(|r| r.is_match(&text));
    if found(&format!(r"mcp_servers{SPACE}*\.{SPACE}*{name}{SPACE}*[\].=]")) {
        return true;
    }
    if found(&format!(r"(?m)^{SPACE}*\[{SPACE}*mcp_servers{SPACE}*\]|mcp_servers{SPACE}*=")) {
        return found(&format!(r"(?:\A|[^A-Za-z0-9_-]){name}{SPACE}*="));
    }
    false
}

/// The MCP servers the call would start: those of the layers it reads (system, project), listed
/// with an empty CODEX_HOME, are turned off by name. Not the user's config.toml's: the call ignores
/// that file, and naming one of its servers would break the call. Any server still on with the
/// user's CODEX_HOME must be one of that file's. Returns the names turned off.
fn check_mcp(p: &Call, codex_home: &Path, flags_for: &dyn Fn(&[String]) -> Vec<String>) -> Result<Vec<String>, CodexError> {
    let names: Vec<String> = mcp_list(p, &p.cage.empty_env, &flags_for(&[]))?.into_iter().map(|(n, _)| n).collect();
    let odd: Vec<String> = names.iter().filter(|n| !matches(&MCP_NAME, n)).map(|n| label(n)).collect();
    if !odd.is_empty() {
        return Err(paused(
            Reason::Mcp,
            format!("an MCP server of Codex's system settings has a name the kit cannot turn off ({}): Codex is paused", odd[..odd.len().min(5)].join(", ")),
        ));
    }
    let flags = flags_for(&names);
    let still: Vec<String> = mcp_list(p, &p.cage.empty_env, &flags)?.into_iter().filter(|(_, on)| *on).map(|(n, _)| label(&n)).collect();
    if !still.is_empty() {
        return Err(paused(Reason::Mcp, format!("MCP server {} stays on: Codex is paused", still[..still.len().min(5)].join(", "))));
    }
    let unknown: Vec<String> = mcp_list(p, &p.cage.env, &flags)?
        .into_iter()
        .filter(|(n, on)| *on && !names.contains(n) && !declared_by_user(codex_home, n))
        .map(|(n, _)| label(&n))
        .collect();
    if !unknown.is_empty() {
        return Err(paused(
            Reason::Mcp,
            format!("MCP server {} comes from settings the kit cannot turn off: Codex is paused", unknown[..unknown.len().min(5)].join(", ")),
        ));
    }
    Ok(names)
}

/// Every check before a question, as the question runs: (version, MCP servers to turn off).
/// `features list` reads the user's config.toml, which the call ignores: it is read with the user's
/// CODEX_HOME and with an empty one. Windows: the system settings' folder before any probe loads it.
fn preflight(p: &Call, setup: &Setup, codex_home: &Path, coupees: &[String], effort: Option<&str>) -> Result<(String, Vec<String>), CodexError> {
    check_system_settings(&setup.layers)?;
    let version = check_version(p)?;
    check_dotenv(codex_home)?;
    check_managed(setup)?;
    check_auth_file(codex_home)?;
    let flags_for = |mcp_off: &[String]| config_flags(coupees, mcp_off, Some(&p.cage.system), effort);
    check_login(p, &flags_for(&[]))?;
    for env in [&p.cage.env, &p.cage.empty_env] {
        let args: Vec<String> = flags_for(&[]).into_iter().chain(words(&["features", "list"])).collect();
        let (code, out, _) = probe(p, &args, env, "features list")?;
        if code != Some(0) {
            let code = code.map_or_else(|| "None".to_string(), |c| c.to_string());
            return Err(paused(Reason::Features, format!("codex features list failed (exit code {code}; is Codex's config.toml valid?): Codex is paused")));
        }
        check_features(&out, coupees)?;
    }
    Ok((version, check_mcp(p, codex_home, &flags_for)?))
}

// ── Windows: %ProgramData%\OpenAI\Codex ─────────────────────────────────────

/// Who may own the folders of Codex's system settings and change them: SYSTEM, the Administrators,
/// TrustedInstaller (Codex's own list); CREATOR OWNER and OWNER RIGHTS stand for that owner.
const OWNERS: &[&str] = &["S-1-5-18", "S-1-5-32-544", "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"];
const WRITERS_TOO: &[&str] = &["S-1-3-0", "S-1-3-4"];
/// What changes a folder or a file (Codex's STANDARD_USER_MUTATION_MASK), and the generic rights as they map onto a file's.
const MUTATION: u32 = 0x2 | 0x4 | 0x10 | 0x100 | 0x40 | 0x10000 | 0x40000 | 0x80000;
const GENERIC: &[(u32, u32)] = &[(0x1000_0000, 0x1F01FF), (0x4000_0000, 0x120116), (0x8000_0000, 0x120089), (0x2000_0000, 0x1200A0)];
const ACE_ALLOWED: u8 = 0;
const ACES_DENIED: &[u8] = &[1, 6, 10, 12];
const INHERIT_ONLY: u8 = 0x08;
const OBJECT_INHERIT: u8 = 0x01;

/// One entry of an access list: its type, flags, mask and account (allow and deny entries only).
#[derive(Debug, Clone, PartialEq, Eq)]
struct Ace {
    kind: u8,
    flags: u8,
    mask: u32,
    sid: Option<String>,
}

/// S-1-5-32-544 from a SID's bytes.
fn sid_string(raw: &[u8]) -> Option<String> {
    if raw.len() < 8 || raw[0] != 1 || raw.len() < 8 + 4 * usize::from(raw[1]) {
        return None;
    }
    let authority = raw[2..8].iter().fold(0u64, |a, b| (a << 8) | u64::from(*b));
    let subs = raw[8..8 + 4 * usize::from(raw[1])].chunks(4).map(|c| u32::from_le_bytes([c[0], c[1], c[2], c[3]]).to_string());
    Some(std::iter::once(format!("S-1-{authority}")).chain(subs).collect::<Vec<_>>().join("-"))
}

/// The entries of an access list (its bytes, header included).
#[cfg_attr(not(any(windows, test)), allow(dead_code))]
fn parse_acl(raw: &[u8]) -> Option<Vec<Ace>> {
    let size = usize::from(u16::from_le_bytes([*raw.get(2)?, *raw.get(3)?]));
    let count = u16::from_le_bytes([*raw.get(4)?, *raw.get(5)?]);
    let raw = raw.get(..size)?;
    let (mut at, mut aces) = (8usize, Vec::new());
    for _ in 0..count {
        let head = raw.get(at..at + 8)?;
        let (kind, flags, len) = (head[0], head[1], usize::from(u16::from_le_bytes([head[2], head[3]])));
        if len < 8 || at + len > size {
            return None;
        }
        let mask = u32::from_le_bytes([head[4], head[5], head[6], head[7]]);
        let sid = if matches!(kind, 0 | 1) { Some(sid_string(&raw[at + 8..at + len])?) } else { None };
        aces.push(Ace { kind, flags, mask, sid });
        at += len;
    }
    Some(aces)
}

/// Why another account than SYSTEM, the Administrators or TrustedInstaller could change this file or
/// folder (or, child_files, the files it gets), or None. Stricter than Codex's own probe: any other
/// account counts, an allow entry of another kind is refused unread, a deny entry never loosens.
#[cfg_attr(not(any(windows, test)), allow(dead_code))]
fn who_may_change(owner: &str, aces: Option<&[Ace]>, child_files: bool) -> Option<String> {
    if !OWNERS.contains(&owner) {
        return Some(format!("its owner is {owner}"));
    }
    let Some(aces) = aces else { return Some("it has no access list".into()) };
    for a in aces {
        if ACES_DENIED.contains(&a.kind) {
            continue;
        }
        if a.kind != ACE_ALLOWED {
            return Some(format!("it has an access entry of a kind the kit does not read ({})", a.kind));
        }
        if a.flags & INHERIT_ONLY != 0 && !(child_files && a.flags & OBJECT_INHERIT != 0) {
            continue;
        }
        let mask = GENERIC.iter().fold(a.mask, |m, (generic, rights)| if a.mask & generic != 0 { m | rights } else { m });
        let sid = a.sid.as_deref().unwrap_or("None");
        if mask & MUTATION != 0 && !OWNERS.contains(&sid) && !WRITERS_TOO.contains(&sid) {
            return Some(format!("{sid} may change {}", if a.flags & INHERIT_ONLY != 0 { "what it holds" } else { "it" }));
        }
    }
    None
}

/// Windows: %ProgramData%\OpenAI and its Codex folder, when there, and the files Codex reads in it,
/// belong to SYSTEM, the Administrators or TrustedInstaller, and grant no other account a right to
/// change them or what they hold. Otherwise Codex is paused (managed): a standard account can make
/// that folder when it is missing, and Codex loads it all the same. Before any probe, and again just
/// before the question.
#[cfg(not(windows))]
fn check_system_settings(_: &Layers) -> Result<(), CodexError> {
    Ok(())
}

#[cfg(windows)]
fn check_system_settings(layers: &Layers) -> Result<(), CodexError> {
    use std::os::windows::fs::MetadataExt;
    const REPARSE_POINT: u32 = 0x400;
    let stop = |path: &Path, why: &str| {
        paused(
            Reason::Managed,
            format!(
                "{}, where Codex reads its system settings, {why}: another account could put settings there that pass over the kit's, so Codex is paused",
                path.display()
            ),
        )
    };
    let Some(root) = layers.program_data() else { return Err(stop(Path::new("%ProgramData%"), "cannot be found")) };
    let codex_dir = root.join("OpenAI").join("Codex");
    let checked = [
        (root.join("OpenAI"), true, false),
        (codex_dir.clone(), true, true),
        (codex_dir.join("config.toml"), false, false),
        (codex_dir.join("requirements.toml"), false, false),
        (codex_dir.join("managed_config.toml"), false, false),
    ];
    for (path, folder, child) in checked {
        let st = match fs::symlink_metadata(&path) {
            Ok(st) => st,
            Err(e) if e.kind() == io::ErrorKind::NotFound && folder => return Ok(()),
            Err(e) if e.kind() == io::ErrorKind::NotFound => continue,
            Err(_) => return Err(stop(&path, "cannot be read")),
        };
        if st.file_attributes() & REPARSE_POINT != 0 || st.file_type().is_symlink() {
            return Err(stop(&path, "is a link"));
        }
        if !(if folder { st.is_dir() } else { st.is_file() }) {
            return Err(stop(&path, if folder { "is not a folder" } else { "is not a file" }));
        }
        let (owner, aces) = windows_acl::security(&path).map_err(|_| stop(&path, "has permissions the kit cannot read"))?;
        if let Some(why) = who_may_change(&owner, aces.as_deref(), child) {
            return Err(stop(&path, &format!("can be changed by another account ({why})")));
        }
    }
    Ok(())
}

#[cfg(windows)]
mod windows_acl {
    //! Windows' own answers, without a crate: the known folder %ProgramData%, an owner and access list.
    use std::ffi::{c_void, OsString};
    use std::io;
    use std::os::windows::ffi::{OsStrExt, OsStringExt};
    use std::path::{Path, PathBuf};
    use std::ptr::null_mut;

    use super::{parse_acl, sid_string, Ace};

    #[repr(C)]
    struct Guid {
        data1: u32,
        data2: u16,
        data3: u16,
        data4: [u8; 8],
    }

    /// FOLDERID_ProgramData, 62AB5D82-FDC1-4DC3-A9DD-070D1D495D97.
    const PROGRAM_DATA: Guid = Guid { data1: 0x62AB_5D82, data2: 0xFDC1, data3: 0x4DC3, data4: [0xA9, 0xDD, 0x07, 0x0D, 0x1D, 0x49, 0x5D, 0x97] };
    const SE_FILE_OBJECT: i32 = 1;
    const OWNER_SECURITY_INFORMATION: u32 = 0x1;
    const DACL_SECURITY_INFORMATION: u32 = 0x4;

    #[link(name = "shell32")]
    extern "system" {
        fn SHGetKnownFolderPath(id: *const Guid, flags: u32, token: *mut c_void, path: *mut *mut u16) -> i32;
    }
    #[link(name = "ole32")]
    extern "system" {
        fn CoTaskMemFree(mem: *mut c_void);
    }
    #[link(name = "advapi32")]
    extern "system" {
        fn GetNamedSecurityInfoW(
            name: *const u16,
            kind: i32,
            info: u32,
            owner: *mut *mut c_void,
            group: *mut *mut c_void,
            dacl: *mut *mut c_void,
            sacl: *mut *mut c_void,
            descriptor: *mut *mut c_void,
        ) -> u32;
    }
    #[link(name = "kernel32")]
    extern "system" {
        fn LocalFree(mem: *mut c_void) -> *mut c_void;
    }

    /// %ProgramData% as Codex finds it: the known folder, never the variable a parent process may set.
    pub fn known_program_data() -> Option<PathBuf> {
        let mut found: *mut u16 = null_mut();
        // SAFETY: a GUID that lives for the call; Windows hands back a string we free once, even on failure.
        let hr = unsafe { SHGetKnownFolderPath(&PROGRAM_DATA, 0, null_mut(), &mut found) };
        let path = (hr == 0 && !found.is_null()).then(|| {
            // SAFETY: a NUL-terminated wide string Windows allocated.
            let len = (0..).take_while(|&i| unsafe { *found.add(i) } != 0).count();
            PathBuf::from(OsString::from_wide(unsafe { std::slice::from_raw_parts(found, len) }))
        });
        // SAFETY: found is null or Windows' allocation.
        unsafe { CoTaskMemFree(found.cast()) };
        path
    }

    /// (owner, access list) of a file or folder; None for a NULL access list (anyone may do anything).
    pub fn security(path: &Path) -> io::Result<(String, Option<Vec<Ace>>)> {
        let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
        let (mut owner, mut dacl, mut descriptor) = (null_mut(), null_mut(), null_mut());
        // SAFETY: a NUL-terminated path; the owner and the access list point into `descriptor`, freed below.
        let rc = unsafe {
            GetNamedSecurityInfoW(
                wide.as_ptr(),
                SE_FILE_OBJECT,
                OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                &mut owner,
                null_mut(),
                &mut dacl,
                null_mut(),
                &mut descriptor,
            )
        };
        if rc != 0 {
            return Err(io::Error::from_raw_os_error(rc as i32));
        }
        let unreadable = || io::Error::other(format!("{} has a SID or an access list the kit cannot read", path.display()));
        let read = || -> io::Result<(String, Option<Vec<Ace>>)> {
            if owner.is_null() {
                return Err(unreadable());
            }
            // SAFETY: a SID: 8 bytes of header, then 4 per sub-authority (the count is its second byte).
            let head = unsafe { std::slice::from_raw_parts(owner as *const u8, 8) };
            let sid = unsafe { std::slice::from_raw_parts(owner as *const u8, 8 + 4 * usize::from(head[1])) };
            let who = sid_string(sid).ok_or_else(unreadable)?;
            if dacl.is_null() {
                return Ok((who, None));
            }
            // SAFETY: an ACL: 8 bytes of header whose bytes 2 and 3 give its whole size.
            let head = unsafe { std::slice::from_raw_parts(dacl as *const u8, 8) };
            let size = usize::from(u16::from_le_bytes([head[2], head[3]]));
            let raw = unsafe { std::slice::from_raw_parts(dacl as *const u8, size.max(8)) };
            Ok((who, Some(parse_acl(raw).ok_or_else(unreadable)?)))
        };
        let out = read();
        // SAFETY: Windows' allocation, freed once.
        unsafe { LocalFree(descriptor) };
        out
    }
}

// ── The stream ──────────────────────────────────────────────────────────────

fn failure(message: &str) -> CodexError {
    let said = brief(message, 200);
    let kind = if matches(&QUOTA, &said) {
        Kind::Quota
    } else if matches(&AUTH, &said) {
        Kind::Auth
    } else {
        Kind::Api
    };
    fail(kind, if said.is_empty() { "turn failed".into() } else { said })
}

/// str() of a value, as the launcher writes what it gets (`or ""` included).
fn text_of(v: Option<&Value>) -> String {
    match v {
        _ if !truthy(v) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Bool(_)) => "True".into(),
        Some(other) => other.to_string(),
        None => String::new(),
    }
}

/// codex exec --json, line by line (exec/src/exec_events.rs).
#[derive(Default)]
struct Watch {
    thread: Option<String>,
    turns: u32,
    answer: Option<String>,
    usage: Option<Map<String, Value>>,
    failed: Option<String>,
    init: Option<CodexError>,
    errors: Vec<String>,
}

impl Watch {
    /// False once the run must stop: Codex did not start, or did not behave, as required.
    fn line(&mut self, line: &str) -> bool {
        let v: Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(_) => {
                if strip(line).starts_with('{') {
                    self.init = Some(fail(Kind::Init, "a line of the stream the app cannot read"));
                }
                return self.ok();
            }
        };
        let Some(o) = v.as_object() else { return self.ok() };
        let kind = o.get("type").and_then(Value::as_str);
        if self.thread.is_none() {
            match (kind, o.get("thread_id").and_then(Value::as_str)) {
                (Some("thread.started"), Some(id)) => self.thread = Some(id.to_string()),
                _ => self.init = Some(fail(Kind::Init, format!("{} before thread.started", label_of(o.get("type"))))),
            }
            return self.ok();
        }
        match kind {
            Some("turn.started") => {
                self.turns += 1;
                if self.turns > 1 {
                    self.init = Some(fail(Kind::Init, "a second turn"));
                }
            }
            Some("item.started" | "item.updated" | "item.completed") => {
                let item = o.get("item").and_then(Value::as_object);
                let item_type = item.and_then(|i| i.get("type"));
                match item_type.and_then(Value::as_str) {
                    Some(t) if ITEMS.contains(&t) => {
                        if let (Some("agent_message"), Some("item.completed"), Some(text)) =
                            (Some(t), kind, item.and_then(|i| i.get("text")).and_then(Value::as_str))
                        {
                            self.answer = Some(text.to_string());
                        }
                    }
                    // A tool's item, or one this version brings that the kit does not know.
                    _ => self.init = Some(fail(Kind::Init, format!("tool used: {}", label_of(item_type)))),
                }
            }
            // "Reconnecting... 1/5": a warning, unless the turn fails.
            Some("error") => self.errors.push(text_of(o.get("message"))),
            Some("turn.failed") => {
                let message = text_of(o.get("error").and_then(Value::as_object).and_then(|e| e.get("message")));
                self.failed = Some(if message.is_empty() { "turn failed".into() } else { message });
            }
            Some("turn.completed") => {
                let usage = o.get("usage").and_then(Value::as_object);
                self.usage =
                    Some(usage.map(|u| u.iter().filter(|(_, n)| n.is_i64() || n.is_u64()).map(|(k, n)| (k.clone(), n.clone())).collect()).unwrap_or_default());
            }
            _ => self.init = Some(fail(Kind::Init, format!("unknown event {}", label_of(o.get("type"))))),
        }
        self.ok()
    }

    fn ok(&self) -> bool {
        self.init.is_none()
    }

    /// The answer, or why there is none. Fail closed: no thread, no single clean turn, no answer.
    fn conclude(self, ran: &Ran, cancelled: bool, answer_path: &Path, version: String) -> Result<Answer, CodexError> {
        if let Some(e) = self.init {
            return Err(e);
        }
        if cancelled {
            return Err(fail(Kind::Cancelled, "stopped"));
        }
        if ran.timed_out {
            return Err(fail(Kind::Timeout, "no answer in time"));
        }
        if self.thread.is_none() {
            let code = ran.code.map_or_else(|| "None".into(), |c| c.to_string());
            return Err(fail(Kind::Start, format!("Codex did not start (exit code {code})")));
        }
        if let Some(f) = &self.failed {
            return Err(failure(f));
        }
        let Some(usage) = self.usage else {
            let last = self.errors.last().map_or("", String::as_str);
            if !last.is_empty() && (matches(&QUOTA, last) || matches(&AUTH, last)) {
                return Err(failure(last));
            }
            let said = if last.is_empty() { String::new() } else { format!(" ({})", brief(last, 160)) };
            return Err(fail(Kind::Api, format!("no result{said}")));
        };
        let answer = self.answer.ok_or_else(|| fail(Kind::Invalid, "no answer in the stream"))?;
        let text = read_small(answer_path, ANSWER_CAP).map_err(|_| fail(Kind::Invalid, "no answer file"))?;
        if strip(&text) != strip(&answer) {
            return Err(fail(Kind::Invalid, "the answer file is not the streamed answer"));
        }
        let out: Value = serde_json::from_str(&text).map_err(|_| fail(Kind::Invalid, "the answer is not JSON"))?;
        let Value::Object(output) = out else { return Err(fail(Kind::Invalid, "the answer is not a JSON object")) };
        Ok(Answer { output, version, num_turns: self.turns, tokens: usage })
    }
}

// ── Asking, checking, looking ───────────────────────────────────────────────

/// One question: its prompt (stdin), the JSON schema of the answer, Codex's instructions.
pub struct Question<'a> {
    pub prompt: &'a str,
    pub schema: &'a Value,
    pub system: &'a str,
    pub model: Option<&'a str>,
    pub effort: Option<&'a str>,
}

/// Codex's answer, already parsed, and what it says of the run. No cost: Codex gives none.
#[derive(Debug, Serialize)]
#[cfg_attr(not(test), allow(dead_code))]
pub struct Answer {
    pub output: Map<String, Value>,
    pub version: String,
    pub num_turns: u32,
    /// turn.completed's usage: the tokens of each kind.
    pub tokens: Map<String, Value>,
}

fn write(path: &Path, text: &str) -> Result<(), CodexError> {
    fs::write(path, text).map_err(|e| fail(Kind::Start, format!("cannot write {}: {e}", file_name(path))))
}

/// One isolated question to Codex. Blocks until it answers, `limit`, or `cancel` (the caller's
/// « Arrêter », which this also sets to stop a run that misbehaves).
#[cfg_attr(not(test), allow(dead_code))] // the repair asks Codex from phase 9 on
pub fn ask(setup: &Setup, q: &Question, limit: Duration, cancel: &AtomicBool) -> Result<Answer, CodexError> {
    strict_schema(q.schema, "$")?;
    if strip(q.system).is_empty() {
        return Err(value("Codex refuses empty instructions"));
    }
    if let Some(m) = q.model.filter(|m| !matches(&MODEL_NAME, m)) {
        return Err(value(format!("not a model name: {}", py_repr(&brief(m, 80)))));
    }
    if let Some(e) = q.effort.filter(|e| !matches(&EFFORT, e)) {
        return Err(value(format!("not a reasoning effort: {}", py_repr(&brief(e, 40)))));
    }
    let codex = resolve(setup)?;
    let codex_home = codex_home(setup)?;
    let coupees = load_features(&setup.features)?;
    let cage = Cage::new(&codex, setup, &codex_home)?;
    write(&cage.schema, &q.schema.to_string())?;
    write(&cage.system, q.system)?;
    let p = Call { codex: &codex, cage: &cage, cancel };
    let (version, mcp_off) = preflight(&p, setup, &codex_home, &coupees, q.effort)?;
    // As it is when the question loads it, not only when the probes did.
    check_system_settings(&setup.layers)?;
    if cancel.load(Ordering::SeqCst) {
        return Err(fail(Kind::Cancelled, "stopped"));
    }
    let mut watch = Watch::default();
    let args = exec_argv(&cage, &coupees, &mcp_off, q.model, q.effort);
    let ran = run(&p, &args, &cage.env, q.prompt, limit, &mut |line| watch.line(line))?;
    // Stopped while the stream was as required: by the caller.
    let by_caller = ran.stopped && watch.ok();
    watch.conclude(&ran, by_caller, &cage.answer, version)
}

/// Whether this codex may be asked, without asking it anything: its version, or why not. The probes run as a question would.
#[cfg_attr(not(test), allow(dead_code))] // the screens look from phase 10 on
pub fn check(setup: &Setup) -> Result<String, CodexError> {
    let codex = resolve(setup)?;
    let codex_home = codex_home(setup)?;
    let coupees = load_features(&setup.features)?;
    let cage = Cage::new(&codex, setup, &codex_home)?;
    // Codex refuses an empty instructions file, and the probes read it.
    write(&cage.system, CHECK_INSTRUCTIONS)?;
    let never = AtomicBool::new(false);
    Ok(preflight(&Call { codex: &codex, cage: &cage, cancel: &never }, setup, &codex_home, &coupees, None)?.0)
}

/// How Codex is logged in, as the app tells the user (§4.3): never the line Codex printed.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Login {
    /// A ChatGPT account of a personal plan: what the app uses.
    Chatgpt,
    /// An API key or another billed way: the app does not use it, and never logs it out.
    Billed,
    /// A workspace account (Business, Edu, Enterprise), or settings of this system above the kit's.
    Managed,
    LoggedOut,
    /// Codex is missing, or did not answer.
    Unknown,
}

/// auth.json (its mode and plan, whether something refreshes it), then `codex login status` read
/// as a question would read it, every -c included. Windows: the system settings' folder first.
#[cfg_attr(not(test), allow(dead_code))] // the screens ask from phase 10b on
pub fn login(setup: &Setup) -> Login {
    match login_check(setup) {
        Ok(()) => Login::Chatgpt,
        Err(CodexError { kind: Kind::Auth, reason: Some(Reason::Billed), .. }) => Login::Billed,
        Err(CodexError { kind: Kind::Auth, .. }) => Login::LoggedOut,
        Err(CodexError { reason: Some(Reason::Managed), .. }) => Login::Managed,
        Err(_) => Login::Unknown,
    }
}

fn login_check(setup: &Setup) -> Result<(), CodexError> {
    check_system_settings(&setup.layers)?;
    let codex = resolve(setup)?;
    let codex_home = codex_home(setup)?;
    check_auth_file(&codex_home)?;
    let coupees = load_features(&setup.features)?;
    let cage = Cage::new(&codex, setup, &codex_home)?;
    write(&cage.system, CHECK_INSTRUCTIONS)?;
    let never = AtomicBool::new(false);
    check_login(&Call { codex: &codex, cage: &cage, cancel: &never }, &config_flags(&coupees, &[], Some(&cage.system), None))
}

// ── Python's text rules ─────────────────────────────────────────────────────

/// str.isspace(): Unicode's spaces, and the four separators below 0x20 Python counts too.
fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

fn strip(s: &str) -> &str {
    s.trim_matches(is_space)
}

fn split_space(s: &str) -> Vec<&str> {
    s.split(is_space).filter(|w| !w.is_empty()).collect()
}

/// str.splitlines(): \n, \r, \r\n, and the other line ends Python knows.
fn splitlines(s: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut chars = s.char_indices().peekable();
    while let Some((i, c)) = chars.next() {
        if matches!(c, '\n' | '\r' | '\u{b}' | '\u{c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}') {
            out.push(&s[start..i]);
            let mut end = i + c.len_utf8();
            if c == '\r' && chars.peek().is_some_and(|(_, n)| *n == '\n') {
                chars.next();
                end += 1;
            }
            start = end;
        }
    }
    if start < s.len() {
        out.push(&s[start..]);
    }
    out
}

/// str.isprintable(), as far as Rust knows the categories: not a control, a separator but the
/// space, a format character or a private use one.
fn printable(c: char) -> bool {
    if c == ' ' {
        return true;
    }
    let format = matches!(c as u32,
        0xAD | 0x600..=0x605 | 0x61C | 0x6DD | 0x70F | 0x890..=0x891 | 0x8E2 | 0x180E | 0x200B..=0x200F
        | 0x202A..=0x202E | 0x2060..=0x2064 | 0x2066..=0x206F | 0xFEFF | 0xFFF9..=0xFFFB | 0x110BD | 0x110CD
        | 0x13430..=0x1343F | 0x1BCA0..=0x1BCA3 | 0x1D173..=0x1D17A | 0xE0001 | 0xE0020..=0xE007F);
    let private = matches!(c as u32, 0xE000..=0xF8FF | 0xF0000..=0xFFFFD | 0x100000..=0x10FFFD);
    !(c.is_control() || is_space(c) || format || private)
}

/// A value for an error message: one line, printable, capped.
fn brief(value: &str, cap: usize) -> String {
    let flat: String = value.chars().map(|c| if printable(c) { c } else { ' ' }).collect();
    split_space(&flat).join(" ").chars().take(cap).collect()
}

/// A name for an error message when the model may have chosen it.
fn label(value: &str) -> String {
    let kept: String = value.chars().filter(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | ':' | '-')).take(64).collect();
    if kept.is_empty() {
        "?".into()
    } else {
        kept
    }
}

/// The same of what Python's str() writes of a JSON value.
fn label_of(v: Option<&Value>) -> String {
    match v {
        Some(Value::String(s)) => label(s),
        None | Some(Value::Null) => "None".into(),
        Some(Value::Bool(b)) => (if *b { "True" } else { "False" }).into(),
        Some(other) => label(&other.to_string()),
    }
}

/// Python's repr() of a string `brief` made: quoted, its quote and backslashes escaped.
fn py_repr(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::from(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if printable(c) => out.push(c),
            c if (c as u32) < 0x100 => out.push_str(&format!("\\x{:02x}", c as u32)),
            c if (c as u32) < 0x10000 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push_str(&format!("\\U{:08x}", c as u32)),
        }
    }
    out.push(quote);
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::io::Write;
    use std::process::{Command, Stdio};
    use std::sync::atomic::AtomicUsize;
    use std::sync::Mutex;

    use serde_json::json;

    /// What a scenario plants, and nothing the app or the launcher writes may carry: the fake API
    /// key `codex login status` shows, the caller's keys, the tokens of auth.json.
    const SECRETS: &[&str] = &["canari", "fake-refresh", "fake-access", "sk-exchanged", "sk-ant-"];

    fn repo() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
    }

    /// The Python the kit runs with on this system: the scenarios and the launcher are its own.
    fn python() -> String {
        let name = if cfg!(windows) { "python" } else { "python3" };
        let out = Command::new(name)
            .args(["-I", "-c", "import sys; print(sys.executable)"])
            .output()
            .unwrap_or_else(|e| panic!("the contract between the app and the kit's launcher needs {name}: {e}"));
        assert!(out.status.success(), "{name}: {}", String::from_utf8_lossy(&out.stderr));
        String::from_utf8(out.stdout).unwrap().trim().to_string()
    }

    /// engine/tests/codex_contract.py, its JSON answer.
    fn contract(python: &str, args: &[&str], stdin: &str) -> Value {
        let mut child = Command::new(python)
            .arg("-I")
            .arg(repo().join("engine").join("tests").join("codex_contract.py"))
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        let mut sin = child.stdin.take().unwrap();
        let data = stdin.to_string();
        let writer = std::thread::spawn(move || sin.write_all(data.as_bytes()));
        let out = child.wait_with_output().unwrap();
        writer.join().unwrap().unwrap();
        assert!(out.status.success(), "codex_contract.py {args:?}: {}", String::from_utf8_lossy(&out.stderr));
        serde_json::from_slice(&out.stdout).unwrap()
    }

    fn text(p: &Value, key: &str) -> String {
        p[key].as_str().unwrap_or_else(|| panic!("{key} in {p}")).to_string()
    }

    fn paths(p: &Value, key: &str) -> Vec<PathBuf> {
        p[key].as_array().unwrap().iter().map(|v| PathBuf::from(v.as_str().unwrap())).collect()
    }

    /// A scenario's world, as the launcher sees it: its home, CODEX_HOME, layers and environment
    /// (Windows reads a variable whatever its case, as the app's own environment).
    fn setup_of(p: &Value) -> Setup {
        let env: HashMap<String, String> = p["env"].as_object().unwrap().iter().map(|(k, v)| (key(k), v.as_str().unwrap().to_string())).collect();
        fn key(k: &str) -> String {
            if cfg!(windows) {
                k.to_uppercase()
            } else {
                k.to_string()
            }
        }
        Setup {
            home: PathBuf::from(text(p, "home")),
            codex_home: PathBuf::from(text(p, "codex_home")),
            work_root: PathBuf::from(text(p, "work_root")),
            features: PathBuf::from(text(p, "features_file")),
            layers: Layers { managed: Some(paths(p, "managed")), mdm: Some(paths(p, "mdm")), program_data: Some(PathBuf::from(text(p, "program_data"))) },
            vars: Box::new(move |k| env.get(&key(k)).cloned()),
            command: p["command"].as_array().map(|a| a.iter().map(|v| v.as_str().unwrap().to_string()).collect()),
        }
    }

    /// What codex.rs answers, written as the contract writes the launcher's.
    fn outcome(p: &Value) -> Value {
        let setup = setup_of(p);
        let done = match p["call"].as_str().unwrap() {
            "check" => check(&setup).map(|v| json!({ "version": v })),
            "login" => login_check(&setup).map(|()| json!({ "login": "ok" })),
            _ => {
                let q = Question {
                    prompt: &text(p, "prompt"),
                    schema: &p["schema"],
                    system: &text(p, "system"),
                    model: p["model"].as_str(),
                    effort: p["effort"].as_str(),
                };
                let never = AtomicBool::new(false);
                ask(&setup, &q, Duration::from_secs(p["timeout"].as_u64().unwrap()), &never)
                    .map(|a| json!({ "answer": a.output, "version": a.version, "num_turns": a.num_turns, "tokens": a.tokens }))
            }
        };
        done.unwrap_or_else(|e| json!({ "error": [e.kind, e.reason, e.detail] }))
    }

    /// Where two records part, for the message.
    fn first_difference(a: &Value, b: &Value, at: &str) -> Option<String> {
        match (a, b) {
            (Value::Object(x), Value::Object(y)) => {
                let keys: std::collections::BTreeSet<&String> = x.keys().chain(y.keys()).collect();
                keys.into_iter().find_map(|k| first_difference(x.get(k).unwrap_or(&Value::Null), y.get(k).unwrap_or(&Value::Null), &format!("{at}.{k}")))
            }
            (Value::Array(x), Value::Array(y)) if x.len() == y.len() => {
                x.iter().zip(y).enumerate().find_map(|(i, (u, v))| first_difference(u, v, &format!("{at}[{i}]")))
            }
            _ if a == b => None,
            _ => Some(format!("{at}:\n  app:      {a}\n  launcher: {b}")),
        }
    }

    /// The outcome is the expected one: the same answer, or the same kind and reason of refusal.
    fn as_expected(outcome: &Value, expect: &Value) -> bool {
        match expect.get("error") {
            Some(e) => outcome["error"].as_array().is_some_and(|o| o.len() == 3 && o[..2] == e.as_array().unwrap()[..]),
            None => expect.as_object().unwrap().iter().all(|(k, v)| &outcome[k] == v),
        }
    }

    /// Every scenario of engine/tests/codex_contract.py, run by this file and by the launcher, each
    /// in a world of its own: the same outcome (refusal, pause, answer, with the same words), and the
    /// fake Codex given the same probes, -c, call, environment, folder, stdin and schema, in the same
    /// order, nothing left in the work folder, nothing of what Codex started left running.
    #[test]
    fn the_app_and_the_launcher_answer_alike() {
        let python = python();
        let ours = tempfile::tempdir().unwrap();
        let theirs = tempfile::tempdir().unwrap();
        let ours_dir = ours.path().to_string_lossy().into_owned();
        let params = contract(&python, &["setup", &ours_dir, &python], "");
        let params = params.as_array().unwrap();
        assert!(params.len() >= 100, "{} scenarios", params.len());

        // Side by side, a few at a time: each scenario has its own folders.
        let next = AtomicUsize::new(0);
        let records = Mutex::new(vec![Value::Null; params.len()]);
        let logins = Mutex::new(Vec::new());
        let workers = std::thread::available_parallelism().map_or(4, |n| n.get().min(8));
        std::thread::scope(|s| {
            for _ in 0..workers {
                s.spawn(|| loop {
                    let i = next.fetch_add(1, Ordering::SeqCst);
                    let Some(p) = params.get(i) else { break };
                    let index = p["index"].to_string();
                    let record = contract(&python, &["record", &ours_dir, &index], &outcome(p).to_string());
                    records.lock().unwrap()[i] = record;
                    if let Some(state) = p["login"].as_str() {
                        logins.lock().unwrap().push((text(p, "name"), state.to_string(), login(&setup_of(p))));
                    }
                });
            }
        });
        let ours = records.into_inner().unwrap();
        let theirs_dir = theirs.path().to_string_lossy().into_owned();
        let launcher = contract(&python, &["run", &theirs_dir, &python], "");
        let launcher = launcher.as_array().unwrap();
        assert_eq!(launcher.len(), params.len(), "the launcher ran another set of scenarios");

        let mut wrong = Vec::new();
        for ((p, a), b) in params.iter().zip(&ours).zip(launcher) {
            let name = text(p, "name");
            if let Some(d) = first_difference(a, b, "") {
                wrong.push(format!("{name}: {d}"));
            }
            for (side, r) in [("app", a), ("launcher", b)] {
                if !p["expect"].is_null() && !as_expected(&r["outcome"], &p["expect"]) {
                    wrong.push(format!("{name}: the {side} answered {}, expected {}", r["outcome"], p["expect"]));
                }
                let said = r.to_string();
                if let Some(secret) = SECRETS.iter().find(|s| said.contains(*s)) {
                    wrong.push(format!("{name}: the {side}'s record carries {secret}"));
                }
            }
            // Not a comparison of two empty records: an answer was asked for.
            if a["outcome"].get("error").is_none() && a["calls"].as_array().is_none_or(Vec::is_empty) {
                wrong.push(format!("{name}: Codex was never run"));
            }
        }
        let calls: usize = ours.iter().map(|r| r["calls"].as_array().map_or(0, Vec::len)).sum();
        assert!(calls > 300, "only {calls} runs of Codex in all");
        for (name, expected, got) in logins.into_inner().unwrap() {
            if serde_json::to_value(got).unwrap() != expected {
                wrong.push(format!("{name}: the app shows the login as {got:?}, expected {expected}"));
            }
        }
        assert!(wrong.is_empty(), "{} of {} scenarios differ:\n{}", wrong.len(), params.len(), wrong.join("\n"));
    }

    /// Who may change a folder of Codex's system settings, from its owner and access list: the
    /// launcher's decision, on every system (Windows reads the real ones in the scenarios above).
    #[test]
    fn who_may_change_the_system_settings_is_decided_as_the_launcher_decides() {
        let cases = contract(&python(), &["acl"], "");
        let acl = cases["acl"].as_array().unwrap();
        assert!(acl.len() > 10_000, "{} cases", acl.len());
        let mut wrong = Vec::new();
        for c in acl {
            let aces: Option<Vec<Ace>> = c[1].as_array().map(|list| {
                list.iter()
                    .map(|a| Ace {
                        kind: a[0].as_u64().unwrap() as u8,
                        flags: a[1].as_u64().unwrap() as u8,
                        mask: a[2].as_u64().unwrap() as u32,
                        sid: a[3].as_str().map(String::from),
                    })
                    .collect()
            });
            let ours = who_may_change(c[0].as_str().unwrap(), aces.as_deref(), c[2].as_bool().unwrap());
            if ours.as_deref() != c[3].as_str() {
                wrong.push(format!("{c}: the app says {ours:?}"));
            }
        }
        for c in cases["sid"].as_array().unwrap() {
            let raw: Vec<u8> =
                (0..c[0].as_str().unwrap().len()).step_by(2).map(|i| u8::from_str_radix(&c[0].as_str().unwrap()[i..i + 2], 16).unwrap()).collect();
            if sid_string(&raw).as_deref() != c[1].as_str() {
                wrong.push(format!("{c}: the app says {:?}", sid_string(&raw)));
            }
        }
        assert!(wrong.is_empty(), "{} differ, the first ones:\n{}", wrong.len(), wrong[..wrong.len().min(10)].join("\n"));
    }

    /// An access list as GetNamedSecurityInfoW hands it: a header, then entries of their own size.
    #[test]
    fn an_access_list_is_read_from_its_bytes() {
        let sid = |subs: &[u32]| -> Vec<u8> {
            let mut b = vec![1, subs.len() as u8, 0, 0, 0, 0, 0, 5];
            b.extend(subs.iter().flat_map(|s| s.to_le_bytes()));
            b
        };
        let ace = |kind: u8, flags: u8, mask: u32, sid: &[u8]| -> Vec<u8> {
            let mut b = vec![kind, flags];
            b.extend(((8 + sid.len()) as u16).to_le_bytes());
            b.extend(mask.to_le_bytes());
            b.extend(sid);
            b
        };
        let body = [ace(0, 0x3, 0x1F01FF, &sid(&[32, 544])), ace(1, 0, 0x2, &sid(&[32, 545])), ace(5, 0, 0x1, &[0; 24])].concat();
        let mut raw = vec![2, 0];
        raw.extend(((8 + body.len()) as u16).to_le_bytes());
        raw.extend(3u16.to_le_bytes());
        raw.extend([0, 0]);
        raw.extend(&body);
        let aces = parse_acl(&raw).unwrap();
        assert_eq!(
            aces,
            [
                Ace { kind: 0, flags: 3, mask: 0x1F01FF, sid: Some("S-1-5-32-544".into()) },
                Ace { kind: 1, flags: 0, mask: 2, sid: Some("S-1-5-32-545".into()) },
                Ace { kind: 5, flags: 0, mask: 1, sid: None },
            ]
        );
        // An entry past the list's size, a size it does not have, an entry shorter than its header.
        assert_eq!(parse_acl(&raw[..raw.len() - 1]), None);
        let mut short = raw.clone();
        short[4] = 4;
        assert_eq!(parse_acl(&short), None);
        let mut tiny = raw.clone();
        tiny[10] = 4;
        assert_eq!(parse_acl(&tiny), None);
    }

    /// The official installer's place first, then the PATH and Homebrew's folders; only an .exe on
    /// Windows (npm's codex.cmd goes through cmd.exe).
    #[test]
    fn codex_is_found_where_the_installer_puts_it() {
        let root = tempfile::tempdir().unwrap();
        let home = root.path().join("home");
        let on_path = root.path().join("bin");
        let local = if cfg!(windows) { home.join("AppData/Local/Programs/OpenAI/Codex/bin/codex.exe") } else { home.join(".local/bin/codex") };
        let path_one = on_path.join(if cfg!(windows) { "codex.exe" } else { "codex" });
        for f in [&local, &path_one] {
            fs::create_dir_all(f.parent().unwrap()).unwrap();
            fs::write(f, "#!/bin/sh\n").unwrap();
            #[cfg(unix)]
            perms::set_mode(f, 0o755).unwrap();
        }
        let path = on_path.to_string_lossy().into_owned();
        let setup = |path: String| Setup {
            home: home.clone(),
            codex_home: home.join(".codex"),
            work_root: root.path().to_path_buf(),
            features: PathBuf::new(),
            layers: Layers::default(),
            vars: Box::new(move |k| (k == "PATH").then(|| path.clone())),
            command: None,
        };
        assert_eq!(find(&setup(path.clone())), Some(local.clone()));
        fs::remove_file(&local).unwrap();
        assert_eq!(find(&setup(path.clone())), Some(path_one.clone()));
        fs::remove_file(&path_one).unwrap();
        if cfg!(windows) {
            fs::write(on_path.join("codex.cmd"), "@echo off\r\n").unwrap();
            assert_eq!(find(&setup(path)), None);
            let cmd = Setup { command: Some(vec![on_path.join("codex.cmd").to_string_lossy().into_owned()]), ..setup(String::new()) };
            assert_eq!(resolve(&cmd).err().map(|e| e.kind), Some(Kind::Start));
        } else {
            fs::write(&path_one, "not a program").unwrap();
            assert_eq!(find(&setup(path)), None, "not executable");
        }
    }

    #[test]
    fn the_list_of_features_is_the_one_installed_next_to_the_launcher() {
        assert_eq!(installed_features(Path::new("/h")), Path::new("/h").join(".claude").join("hooks").join("brain").join("codex-features.json"));
        let user = Setup::for_user(Path::new("/c")).unwrap();
        assert_eq!((user.features, user.codex_home, user.command.is_none()), (installed_features(&sys::home()), PathBuf::from("/c"), true));
        // As the kit's engine/codex-features.json: the list the night's tests keep up to date.
        let names = load_features(&repo().join("engine").join(FEATURES_FILE)).unwrap();
        assert!(CORE.iter().all(|c| names.iter().any(|n| n == c)));
    }

    #[test]
    fn values_are_written_as_python_writes_them() {
        assert_eq!(toml_string("C:\\Users\\Zoé \"x\"\n🙂"), r#""C:\\Users\\Zo\u00e9 \"x\"\n\ud83d\ude42""#);
        assert_eq!(py_repr("x; rm -rf ~"), "'x; rm -rf ~'");
        assert_eq!(py_repr("it's"), "\"it's\"");
        assert_eq!(py_repr("a'b\"c\\"), r#"'a\'b"c\\'"#);
        assert_eq!(label_of(Some(&json!(true))), "True");
        assert_eq!(label_of(None), "None");
        assert_eq!(label_of(Some(&json!({"a": 1}))), "a:1");
        assert_eq!(splitlines("a\r\nb\rc\u{2028}d\n"), ["a", "b", "c", "d"]);
        assert_eq!(brief(" a\t\u{0}b\n\n c\u{200b}d ", 200), "a b c d");
    }
}
