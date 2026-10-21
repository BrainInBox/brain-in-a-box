//! Hermes connected to the AI the user already pays for, with no key and no question
//! (docs/proposals/hermes.md, the decisions of 20/10): Claude's Pro or Max subscription
//! through the official claude-subscription-directsdk plugin, or ChatGPT through Hermes's own
//! login by code. The step ends on Hermes's first answer, not on "connected".
//!
//! What `hermes model` writes, written here with `hermes config set` (Hermes 0.22.2):
//! a plugin's provider gets model.provider, model.default, model.base_url and model.api_mode
//! (hermes_cli/model_setup_flows.py, _model_flow_plugin_provider → _persist_model), ChatGPT
//! gets model.provider, model.default and model.base_url (_model_flow_openai_codex →
//! _activate_provider_model). model.provider goes first: setting it clears a stale base_url.
//!
//! Never borrowed: Codex's or Claude Code's own login. Hermes adopts them out of the box
//! (auth.adopt_external_logins, true by default), and their refresh tokens are single-use: once
//! two programs hold one, whichever refreshes first logs the other out (Hermes's docs, « Borrowed
//! CLI logins »). The step turns adoption off before anything else, and Hermes's ChatGPT login,
//! its input closed, declines to import Codex's (its question defaults to no). The plugin runs
//! the claude program, which keeps its own login. No token is read, copied or shown here.
//!
//! A provider the user chose is kept: the step only connects a Hermes that has none.
//!
//! The outputs read here are real ones, captured with a throwaway HOME and HERMES_HOME
//! (tests/fixtures/hermes-connexion, the user's folder renamed /Users/someone, the code masked).

use std::path::Path;
use std::sync::{LazyLock, Mutex, MutexGuard};
use std::time::Duration;

use regex::Regex;
use serde::Serialize;

use crate::agents::{self, Engine};
use crate::detect::{self, fmt_version, Version};
use crate::{hermes, sys};

/// The official plugin, as `hermes plugins install` takes it, and the name it installs under.
const PLUGIN: &str = "claude-subscription-directsdk";
const PLUGIN_NAME: &str = "claude-subscription-directsdk-experimental";
const CLAUDE_PROVIDER: &str = "claude-subscription-directsdk-experimental";
/// The plugin's profile (its __init__.py): its endpoint is the claude program itself.
const CLAUDE_BASE_URL: &str = "process://claude-subscription-directsdk-experimental";
const CLAUDE_API_MODE: &str = "chat_completions";
/// Claude Code's own alias, the model the plugin checks the login with (directsdk_setup.py):
/// it follows the subscription's current Sonnet.
const CLAUDE_MODEL: &str = "sonnet";
const CODEX_PROVIDER: &str = "openai-codex";
/// hermes_cli/auth_constants.py, DEFAULT_CODEX_BASE_URL.
const CODEX_BASE_URL: &str = "https://chatgpt.com/backend-api/codex";
/// The first of Hermes's own list (hermes_cli/codex_models.py, DEFAULT_CODEX_MODELS).
const CODEX_MODEL: &str = "gpt-6-sol";
/// advice.rs's SUBSCRIBED: the claude logins of a Pro or Max subscription.
const SUBSCRIBED: [&str; 2] = ["claude.ai", "oauth_token"];
/// The only page the login by code may open.
const DEVICE_PAGE: &str = "https://auth.openai.com/";

/// The one message of the step: a short question, a short answer.
pub const FIRST_MESSAGE: &str = "Réponds seulement : Bonjour, je suis prêt.";

/// config set: Python's start, and the plugin's own `claude auth status` at load.
const SHORT: Duration = Duration::from_secs(60);
/// The plugin is cloned from GitHub, its environment prepared.
const INSTALL: Duration = Duration::from_secs(10 * 60);
/// The user reads the code, opens the page, logs in.
const LOGIN: Duration = Duration::from_secs(15 * 60);
const ANSWER: Duration = Duration::from_secs(3 * 60);
/// Plain text, and lines wide enough that a sentence is not cut in two.
const ENV: [(&str, &str); 2] = [("NO_COLOR", "1"), ("COLUMNS", "200")];
const ENV_STREAM: [(&str, Option<&str>); 2] = [("NO_COLOR", Some("1")), ("COLUMNS", Some("200"))];

const ADOPT_OFF: [&str; 4] = ["config", "set", "auth.adopt_external_logins", "false"];
const INSTALL_PLUGIN: [&str; 5] = ["plugins", "install", PLUGIN, "--enable", "--yes-deps"];
const ENABLE_PLUGIN: [&str; 3] = ["plugins", "enable", PLUGIN_NAME];
/// The page is opened by the app, which only opens auth.openai.com.
const CODEX_LOGIN: [&str; 6] = ["auth", "add", CODEX_PROVIDER, "--type", "oauth", "--no-browser"];
const AUTH_LIST: [&str; 2] = ["auth", "list"];
const ANSWER_ARGS: [&str; 5] = ["chat", "-Q", "--oneshot", "-q", FIRST_MESSAGE];

const NOT_INSTALLED: &str = "Hermes ne répond pas sur ton ordinateur : je ne peux pas encore le brancher sur ton IA. Réessaie dans un moment.";
const CLAUDE_LOGGED_OUT: &str = "Claude n'est pas encore connecté. Connecte ton compte Claude Pro ou Max, puis réessaie : Hermes passera par lui.";
const CLAUDE_NOT_SUBSCRIBED: &str = "Claude est connecté avec une clé payante, pas avec un abonnement Pro ou Max. Reconnecte Claude avec ton compte claude.ai, puis réessaie : Hermes passera par lui.";
const CLAUDE_SENTENCE: &str = "Il passe par ton abonnement Claude : ce que tu lui demandes compte sur ton forfait, comme avec Claude.";
const CHATGPT_SENTENCE: &str = "Il passe par ton compte ChatGPT : ce que tu lui demandes compte sur ton forfait, comme avec ChatGPT.";

static ANSI: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"\x1b\[[0-9;?]*[A-Za-z]").ok());

/// Which subscription Hermes answers with.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Route {
    Claude,
    Chatgpt,
}

/// What the step decides before it changes anything.
#[derive(Debug, PartialEq, Eq)]
pub enum Plan {
    Go(Route),
    /// Hermes already answers through a provider: it is kept, in the user's words.
    Keep(String),
    /// Nothing done: why, and what to do, for the user.
    Stop(String),
}

/// What the decision looks at.
pub struct Facts<'a> {
    /// `hermes --version`'s; None when Hermes is missing or did not answer.
    pub version: Option<Version>,
    /// The provider Hermes has (hermes::parse_provider), None when it has none.
    pub provider: Option<&'a str>,
    /// `claude auth status`'s authMethod (detect::claude_auth_method).
    pub claude_method: Option<&'a str>,
    /// agents.json's order, the preferred one first.
    pub engines: &'a [Engine],
}

/// The ChatGPT code Hermes waits for, and its page: what the window shows while the step runs.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Device {
    pub url: String,
    pub code: String,
}

static DEVICE: Mutex<Option<Device>> = Mutex::new(None);

fn device_mut() -> MutexGuard<'static, Option<Device>> {
    DEVICE.lock().unwrap_or_else(|p| p.into_inner())
}

/// What the window asks every second while the step runs.
pub fn device() -> Option<Device> {
    device_mut().clone()
}

/// The login page again, if the user closed it. Only auth.openai.com.
pub fn open_device_page() -> Result<(), String> {
    let url = device().map(|d| d.url).ok_or("Il n'y a pas de code à saisir pour l'instant.")?;
    if !url.starts_with(DEVICE_PAGE) || !sys::open_with_system(&url) {
        return Err(format!("Je n'ai pas pu ouvrir la page. Ouvre-la toi-même : {url}"));
    }
    Ok(())
}

fn provider_of(route: Route) -> &'static str {
    match route {
        Route::Claude => CLAUDE_PROVIDER,
        Route::Chatgpt => CODEX_PROVIDER,
    }
}

fn keep(provider: &str) -> Plan {
    Plan::Keep(format!("Hermes est déjà branché. {} Je n'y touche pas.", hermes::provider_said(provider)))
}

/// The route, or why not now. A provider already there is kept. Claude first when it is
/// preferred and logged in with a subscription; ChatGPT when it is preferred, or when Claude
/// cannot serve. Nothing chosen: Claude's route, the one the install already prepares.
pub fn decide(f: &Facts) -> Plan {
    match f.version {
        None => return Plan::Stop(NOT_INSTALLED.into()),
        Some(v) if v < hermes::MIN_HERMES => {
            return Plan::Stop(format!(
                "Hermes {} est trop ancien pour passer par ton abonnement (il faut {} ou plus). Mets Hermes à jour, puis réessaie.",
                fmt_version(v),
                fmt_version(hermes::MIN_HERMES)
            ))
        }
        Some(_) => {}
    }
    if let Some(p) = f.provider.filter(|p| ![CLAUDE_PROVIDER, CODEX_PROVIDER].contains(p)) {
        return keep(p);
    }
    let subscribed = f.claude_method.is_some_and(|m| SUBSCRIBED.contains(&m));
    let engines: &[Engine] = if f.engines.is_empty() { &[Engine::Claude] } else { f.engines };
    let route = engines.iter().find_map(|e| match e {
        Engine::Claude if subscribed => Some(Route::Claude),
        Engine::Claude => None,
        Engine::Codex => Some(Route::Chatgpt),
    });
    match (route, f.provider) {
        // Connected before to the other subscription: that one stays.
        (Some(route), Some(p)) if p != provider_of(route) => keep(p),
        (Some(route), _) => Plan::Go(route),
        (None, _) => match f.claude_method {
            Some(m) if m != "none" => Plan::Stop(CLAUDE_NOT_SUBSCRIBED.into()),
            _ => Plan::Stop(CLAUDE_LOGGED_OUT.into()),
        },
    }
}

/// The settings `hermes model` would write, in its order, provider first.
fn settings(route: Route) -> &'static [(&'static str, &'static str)] {
    match route {
        Route::Claude => &[
            ("model.provider", CLAUDE_PROVIDER),
            ("model.default", CLAUDE_MODEL),
            ("model.base_url", CLAUDE_BASE_URL),
            ("model.api_mode", CLAUDE_API_MODE),
        ],
        Route::Chatgpt => &[("model.provider", CODEX_PROVIDER), ("model.default", CODEX_MODEL), ("model.base_url", CODEX_BASE_URL)],
    }
}

fn config_set<'a>(key: &'a str, value: &'a str) -> [&'a str; 4] {
    ["config", "set", key, value]
}

/// Without colors, and on one line however the terminal would wrap it.
fn plain(text: &str) -> String {
    let text = match ANSI.as_ref() {
        Some(re) => re.replace_all(text, "").into_owned(),
        None => text.to_string(),
    };
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

#[derive(Debug, PartialEq, Eq)]
enum Installed {
    Now,
    /// Installed before: it may have been turned off since.
    Already,
    Failed,
}

fn read_install(code: i32, stdout: &str) -> Installed {
    if code == 0 {
        Installed::Now
    } else if plain(stdout).contains(&format!("Plugin '{PLUGIN_NAME}' already exists")) {
        Installed::Already
    } else {
        Installed::Failed
    }
}

/// "✓ Set model.provider = … in …/config.yaml".
fn read_set(code: i32, stdout: &str, key: &str) -> bool {
    code == 0 && plain(stdout).contains(&format!("Set {key} = "))
}

/// Whether Hermes already holds its own ChatGPT login (`hermes auth list`): it is kept.
fn has_chatgpt_login(code: i32, stdout: &str) -> bool {
    code == 0 && stdout.lines().any(|l| plain(l).contains(CODEX_PROVIDER))
}

/// Reads `hermes auth add openai-codex` line by line: the page, then the code on the line
/// after "Enter this code:".
#[derive(Default)]
struct DeviceReader {
    url: Option<String>,
    code: Option<String>,
    code_next: bool,
    published: bool,
}

impl DeviceReader {
    /// True when the line is the code: it stays out of the log.
    fn feed(&mut self, line: &str) -> bool {
        let text = plain(line);
        if text.starts_with(DEVICE_PAGE) && !text.contains(' ') {
            self.url = Some(text);
            return false;
        }
        if text.contains("Enter this code") {
            self.code_next = true;
            return false;
        }
        if self.code_next && !text.is_empty() {
            self.code_next = false;
            let looks_like_a_code = (4..=24).contains(&text.len()) && text.chars().all(|c| c.is_ascii_alphanumeric() || c == '-');
            if looks_like_a_code {
                self.code = Some(text);
                return true;
            }
        }
        false
    }

    /// The code and its page, the first time both are known.
    fn take_new(&mut self) -> Option<Device> {
        if self.published {
            return None;
        }
        let device = Device { url: self.url.clone()?, code: self.code.clone()? };
        self.published = true;
        Some(device)
    }
}

fn read_login(code: i32) -> Result<(), String> {
    match code {
        0 => Ok(()),
        -1 => Err("Je n'ai pas vu ta connexion à ChatGPT dans le temps imparti. Réessaie quand tu es prêt : je te donnerai un nouveau code.".into()),
        _ => Err("La connexion à ChatGPT n'a pas abouti. Réessaie : je te donnerai un nouveau code.".into()),
    }
}

/// Hermes's answer (`chat -Q` prints only the answer; its session id goes to stderr), or what
/// went wrong for the user, without Hermes's own words.
fn read_answer(code: i32, stdout: &str, stderr: &str) -> Result<String, String> {
    let answer = plain(stdout);
    if code == 0 && !answer.is_empty() {
        return Ok(shorten(&answer, 400));
    }
    if code == -1 {
        return Err("Hermes met trop de temps à répondre. Réessaie dans un moment.".into());
    }
    let said = format!("{} {}", plain(stdout), plain(stderr)).to_lowercase();
    let has = |words: &[&str]| words.iter().any(|w| said.contains(w));
    Err(if has(&["claude code is not installed"]) {
        "Hermes ne trouve pas Claude sur ton ordinateur. Vérifie que Claude est bien installé, puis réessaie."
    } else if has(&["rejected your sign-in", "no usable login", "not logged in"]) {
        "Hermes n'arrive pas à passer par ta connexion à Claude. Reconnecte ton compte Claude Pro ou Max, puis réessaie."
    } else if has(&["does not support this model"]) {
        "Claude est trop ancien pour répondre à Hermes. Mets Claude à jour, puis réessaie."
    } else if has(&["rate limit", "usage limit", "limit reached", "429"]) {
        "Ton abonnement a atteint sa limite pour le moment. Réessaie plus tard : elle se renouvelle toute seule."
    } else if has(&["401", "unauthorized", "invalid_grant", "revoked"]) {
        "Hermes n'arrive pas à passer par ta connexion. Réessaie : je te reconnecterai."
    } else {
        "Hermes n'a pas pu répondre. Réessaie dans un moment."
    }
    .to_string())
}

fn shorten(text: &str, max: usize) -> String {
    match text.char_indices().nth(max) {
        Some((cut, _)) => format!("{}…", &text[..cut]),
        None => text.to_string(),
    }
}

/// The step's last word: Hermes's answer, then whose subscription it counts on.
fn done_message(route: Route, answer: &str) -> String {
    let sentence = match route {
        Route::Claude => CLAUDE_SENTENCE,
        Route::Chatgpt => CHATGPT_SENTENCE,
    };
    format!("Hermes t'a répondu : « {answer} » {sentence}")
}

/// The hermes_ia step.
pub fn step(log: &mut dyn FnMut(String)) -> Result<String, String> {
    *device_mut() = None;
    let bin = hermes::bin().ok_or(NOT_INSTALLED)?;
    let h = hermes::probe();
    let engines = agents::current(&sys::home()).order;
    let method = detect::claude_auth_method();
    let facts = Facts { version: h.found.version(), provider: h.provider.as_deref(), claude_method: method.as_deref(), engines: &engines };
    let route = match decide(&facts) {
        Plan::Go(route) => route,
        Plan::Keep(said) => return Ok(said),
        Plan::Stop(why) => return Err(why),
    };
    log(match route {
        Route::Claude => "Hermes va passer par ton abonnement Claude.".into(),
        Route::Chatgpt => "Hermes va passer par ton compte ChatGPT.".into(),
    });
    // Before any login: Hermes must never take Codex's or Claude Code's own.
    set(&bin, ADOPT_OFF[2], ADOPT_OFF[3], log)?;
    match route {
        Route::Claude => install_plugin(&bin, log)?,
        Route::Chatgpt => login_chatgpt(&bin, log)?,
    }
    let answer = settle(&bin, route, h.provider.is_none(), log)?;
    Ok(done_message(route, &answer))
}

/// Hermes set on `route`, then its first answer. Connected means Hermes answered once: found with
/// no provider, it is left with none otherwise, so the next check finds it still to connect.
fn settle(bin: &Path, route: Route, fresh: bool, log: &mut dyn FnMut(String)) -> Result<String, String> {
    let answer = settings(route).iter().try_for_each(|&(key, value)| set(bin, key, value, log)).and_then(|()| greet(bin, log));
    if answer.is_err() && fresh {
        for (key, _) in settings(route) {
            let args = ["config", "unset", key];
            log(format!("$ hermes {}", args.join(" ")));
            let _ = sys::run_with(bin, &args, &ENV, None, SHORT);
        }
    }
    answer
}

fn greet(bin: &Path, log: &mut dyn FnMut(String)) -> Result<String, String> {
    log("Je dis bonjour à Hermes.".into());
    let out = sys::run_with(bin, &ANSWER_ARGS, &ENV, None, ANSWER);
    // The user reads read_answer's sentence; the report for the team keeps Hermes's own words.
    let answer = read_answer(out.code, &out.stdout, &out.stderr).inspect_err(|_| log(plain(&format!("{}\n{}", out.stdout, out.stderr))))?;
    log(format!("Hermes : {answer}"));
    Ok(answer)
}

fn set(bin: &Path, key: &str, value: &str, log: &mut dyn FnMut(String)) -> Result<(), String> {
    let args = config_set(key, value);
    log(format!("$ hermes {}", args.join(" ")));
    let out = sys::run_with(bin, &args, &ENV, None, SHORT);
    if read_set(out.code, &out.stdout, key) {
        return Ok(());
    }
    log(plain(&out.stderr));
    Err("Je n'ai pas pu régler Hermes. Réessaie dans un moment.".into())
}

/// `hermes <args>`, its lines in the log as they come, but the ones `hide` keeps out.
fn stream(bin: &Path, args: &[&str], limit: Duration, log: &mut dyn FnMut(String), hide: &mut dyn FnMut(&str) -> bool) -> (i32, String) {
    log(format!("$ hermes {}", args.join(" ")));
    let mut seen = String::new();
    let code = sys::run_streaming(bin, args, &ENV_STREAM, None, limit, &mut |line| {
        if hide(&line) {
            log("  (le code est affiché en grand dans la fenêtre)".into());
        } else {
            seen.push_str(&line);
            seen.push('\n');
            log(line);
        }
    });
    (code, seen)
}

fn install_plugin(bin: &Path, log: &mut dyn FnMut(String)) -> Result<(), String> {
    let (code, seen) = stream(bin, &INSTALL_PLUGIN, INSTALL, log, &mut |_| false);
    match read_install(code, &seen) {
        Installed::Now => Ok(()),
        Installed::Already => {
            let (code, _) = stream(bin, &ENABLE_PLUGIN, SHORT, log, &mut |_| false);
            if code == 0 {
                Ok(())
            } else {
                Err("Je n'ai pas pu activer le module Claude d'Hermes. Réessaie dans un moment.".into())
            }
        }
        Installed::Failed => Err("Je n'ai pas pu ajouter à Hermes le module qui passe par ton abonnement Claude. Vérifie ta connexion à Internet, puis réessaie.".into()),
    }
}

fn login_chatgpt(bin: &Path, log: &mut dyn FnMut(String)) -> Result<(), String> {
    let out = sys::run_with(bin, &AUTH_LIST, &ENV, None, SHORT);
    if has_chatgpt_login(out.code, &out.stdout) {
        log("Hermes a déjà sa propre connexion à ChatGPT.".into());
        return Ok(());
    }
    let mut reader = DeviceReader::default();
    let (code, _) = stream(bin, &CODEX_LOGIN, LOGIN, log, &mut |line| {
        let is_code = reader.feed(line);
        if let Some(device) = reader.take_new() {
            sys::open_with_system(&device.url);
            *device_mut() = Some(device);
        }
        is_code
    });
    *device_mut() = None;
    read_login(code)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(name: &str) -> String {
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/hermes-connexion").join(name);
        std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()))
    }

    fn facts<'a>(version: Option<Version>, method: Option<&'a str>, engines: &'a [Engine]) -> Facts<'a> {
        Facts { version, provider: None, claude_method: method, engines }
    }

    const NOW: Option<Version> = Some((0, 22, 2));

    #[test]
    fn claude_with_a_subscription_takes_the_plugin() {
        for method in SUBSCRIBED {
            assert_eq!(decide(&facts(NOW, Some(method), &[Engine::Claude])), Plan::Go(Route::Claude), "{method}");
            assert_eq!(decide(&facts(NOW, Some(method), &[Engine::Claude, Engine::Codex])), Plan::Go(Route::Claude), "{method}");
        }
    }

    #[test]
    fn chatgpt_preferred_takes_hermes_own_login() {
        assert_eq!(decide(&facts(NOW, Some("claude.ai"), &[Engine::Codex, Engine::Claude])), Plan::Go(Route::Chatgpt));
        assert_eq!(decide(&facts(NOW, None, &[Engine::Codex])), Plan::Go(Route::Chatgpt));
    }

    /// Claude cannot serve Hermes without a subscription: ChatGPT does when it is listed.
    #[test]
    fn claude_without_a_subscription_hands_over_to_chatgpt() {
        assert_eq!(decide(&facts(NOW, Some("none"), &[Engine::Claude, Engine::Codex])), Plan::Go(Route::Chatgpt));
        assert_eq!(decide(&facts(NOW, Some("api_key"), &[Engine::Claude, Engine::Codex])), Plan::Go(Route::Chatgpt));
    }

    /// Nothing chosen: the install prepared Claude, the step takes Claude's route.
    #[test]
    fn nothing_chosen_takes_claude() {
        assert_eq!(decide(&facts(NOW, Some("claude.ai"), &[])), Plan::Go(Route::Claude));
        assert_eq!(decide(&facts(NOW, None, &[])), Plan::Stop(CLAUDE_LOGGED_OUT.into()));
    }

    #[test]
    fn claude_not_ready_stops_with_what_to_do() {
        assert_eq!(decide(&facts(NOW, None, &[Engine::Claude])), Plan::Stop(CLAUDE_LOGGED_OUT.into()));
        assert_eq!(decide(&facts(NOW, Some("none"), &[Engine::Claude])), Plan::Stop(CLAUDE_LOGGED_OUT.into()));
        assert_eq!(decide(&facts(NOW, Some("api_key"), &[Engine::Claude])), Plan::Stop(CLAUDE_NOT_SUBSCRIBED.into()));
    }

    /// Hermes missing or too old: nothing is changed, the user learns why.
    #[test]
    fn hermes_missing_or_too_old_stops() {
        assert_eq!(decide(&facts(None, Some("claude.ai"), &[Engine::Claude])), Plan::Stop(NOT_INSTALLED.into()));
        let Plan::Stop(why) = decide(&facts(Some((0, 21, 3)), Some("claude.ai"), &[Engine::Claude])) else { panic!("too old") };
        assert!(why.contains("0.21.3") && why.contains("0.21.4"), "{why}");
        assert_eq!(decide(&facts(Some((0, 21, 4)), Some("claude.ai"), &[Engine::Claude])), Plan::Go(Route::Claude));
        assert_eq!(decide(&facts(Some((1, 0, 0)), None, &[Engine::Codex])), Plan::Go(Route::Chatgpt));
    }

    /// The user's own choice stays, even when Claude is logged out; ours again is done again.
    #[test]
    fn a_provider_already_there_is_kept() {
        let with = |provider, method, engines| decide(&Facts { provider: Some(provider), ..facts(NOW, method, engines) });
        let Plan::Keep(said) = with("anthropic", None, &[Engine::Claude]) else { panic!("kept") };
        assert_eq!(said, "Hermes est déjà branché. Il passe par Claude. Je n'y touche pas.");
        assert!(matches!(with("openrouter", Some("claude.ai"), &[Engine::Claude]), Plan::Keep(_)));
        assert!(matches!(with("openai-codex", Some("claude.ai"), &[Engine::Claude]), Plan::Keep(_)), "connected to ChatGPT before");
        assert_eq!(with(CLAUDE_PROVIDER, Some("claude.ai"), &[Engine::Claude]), Plan::Go(Route::Claude), "a retry after a failed hello");
        assert_eq!(with(CODEX_PROVIDER, None, &[Engine::Codex]), Plan::Go(Route::Chatgpt));
    }

    /// What `hermes model` writes, provider first; the commands never ask anything.
    #[test]
    fn the_commands_are_hermes_model_without_questions() {
        assert_eq!(ADOPT_OFF, ["config", "set", "auth.adopt_external_logins", "false"]);
        assert_eq!(INSTALL_PLUGIN, ["plugins", "install", "claude-subscription-directsdk", "--enable", "--yes-deps"]);
        assert_eq!(ENABLE_PLUGIN, ["plugins", "enable", "claude-subscription-directsdk-experimental"]);
        assert_eq!(
            settings(Route::Claude).iter().map(|(k, v)| config_set(k, v)).collect::<Vec<_>>(),
            [
                ["config", "set", "model.provider", "claude-subscription-directsdk-experimental"],
                ["config", "set", "model.default", "sonnet"],
                ["config", "set", "model.base_url", "process://claude-subscription-directsdk-experimental"],
                ["config", "set", "model.api_mode", "chat_completions"],
            ]
        );
        assert_eq!(
            settings(Route::Chatgpt).iter().map(|(k, v)| config_set(k, v)).collect::<Vec<_>>(),
            [
                ["config", "set", "model.provider", "openai-codex"],
                ["config", "set", "model.default", "gpt-6-sol"],
                ["config", "set", "model.base_url", "https://chatgpt.com/backend-api/codex"],
            ]
        );
        assert_eq!(CODEX_LOGIN, ["auth", "add", "openai-codex", "--type", "oauth", "--no-browser"]);
        assert_eq!(ANSWER_ARGS, ["chat", "-Q", "--oneshot", "-q", FIRST_MESSAGE]);
    }

    /// No command borrows a login or touches a token.
    #[test]
    fn no_command_borrows_a_login() {
        let mut all: Vec<Vec<&str>> = vec![ADOPT_OFF.to_vec(), INSTALL_PLUGIN.to_vec(), ENABLE_PLUGIN.to_vec(), CODEX_LOGIN.to_vec(), AUTH_LIST.to_vec(), ANSWER_ARGS.to_vec()];
        for route in [Route::Claude, Route::Chatgpt] {
            all.extend(settings(route).iter().map(|(k, v)| config_set(k, v).to_vec()));
        }
        for args in &all {
            let line = args.join(" ").to_lowercase();
            for word in ["token", "auth.json", ".codex", "credentials", "claude_config_dir", "adopt"] {
                assert!(!line.contains(word) || line == "config set auth.adopt_external_logins false", "{line}");
            }
        }
    }

    #[test]
    fn reads_the_plugin_install() {
        assert_eq!(read_install(0, &fixture("plugins-install.stdout.txt")), Installed::Now);
        assert_eq!(read_install(1, &fixture("plugins-install-again.stdout.txt")), Installed::Already, "the second install says so, wrapped at 80 columns");
        assert_eq!(read_install(1, "Error: could not clone https://github.com/NousResearch/hermes-official-plugins"), Installed::Failed);
        assert_eq!(read_install(-1, ""), Installed::Failed);
        assert!(plain(&fixture("plugins-enable-again.stdout.txt")).contains("is already enabled"));
    }

    #[test]
    fn reads_config_set() {
        let provider = fixture("config-set-provider.stdout.txt");
        assert!(read_set(0, &provider, "model.provider"));
        assert!(read_set(0, &fixture("config-set-default.stdout.txt"), "model.default"));
        assert!(read_set(0, &fixture("config-set-adopt.stdout.txt"), "auth.adopt_external_logins"));
        assert!(!read_set(0, &provider, "model.default"));
        assert!(!read_set(1, &provider, "model.provider"));
    }

    /// The real output of `hermes auth add openai-codex`, its code masked.
    #[test]
    fn reads_the_code_and_its_page() {
        let mut reader = DeviceReader::default();
        let out = fixture("codex-device.stdout.txt");
        let hidden: Vec<&str> = out.lines().filter(|l| reader.feed(l)).collect();
        assert_eq!(hidden.len(), 1, "only the code's line stays out of the log");
        assert_eq!(reader.take_new(), Some(Device { url: "https://auth.openai.com/codex/device".into(), code: "XXXX-XXXXX".into() }));
        assert_eq!(reader.take_new(), None, "published once");

        let mut other = DeviceReader::default();
        for l in ["  1. Open this URL in your browser:", "     https://evil.example/codex/device", "  2. Enter this code:", "     ABCD-12345"] {
            other.feed(l);
        }
        assert_eq!(other.take_new(), None, "a page elsewhere than auth.openai.com is never opened");
    }

    #[test]
    fn knows_hermes_own_chatgpt_login() {
        assert!(!has_chatgpt_login(0, &fixture("auth-list-empty.stdout.txt")));
        assert!(has_chatgpt_login(0, "openai-codex (1 credentials):\n  #1  chatgpt  oauth\n"));
        assert!(!has_chatgpt_login(1, "openai-codex"));
    }

    #[test]
    fn the_login_ends_with_a_way_out() {
        assert_eq!(read_login(0), Ok(()));
        for code in [-1, 1, 130] {
            let why = read_login(code).expect_err("a failed login");
            assert!(why.contains("Réessaie"), "{why}");
        }
    }

    #[test]
    fn reads_the_first_answer() {
        assert_eq!(read_answer(0, "Bonjour, je suis prêt.\n", "\nsession_id: 20260101_000000_abc123\n"), Ok("Bonjour, je suis prêt.".into()));
        assert_eq!(read_answer(0, "\x1b[1mBonjour,\x1b[0m\n  je suis prêt.\n", ""), Ok("Bonjour, je suis prêt.".into()));
        assert!(read_answer(0, "", "").is_err(), "no answer is no success");
    }

    /// The real refusal of the plugin when claude has no login where Hermes runs it.
    #[test]
    fn a_refusal_says_what_to_do_in_plain_french() {
        let why = read_answer(1, &fixture("chat-claude-not-logged-in.stdout.txt"), &fixture("chat-claude-not-logged-in.stderr.txt")).expect_err("refused");
        assert!(why.contains("connexion à Claude") && why.contains("réessaie"), "{why}");
        for english in ["401", "HTTP", "Provider", "hermes auth", "claude auth"] {
            assert!(!why.contains(english), "{why}");
        }
        let err = |out: &str, code| read_answer(code, out, "").expect_err("an error");
        assert!(err("Error: HTTP 429: usage limit reached", 1).contains("limite"));
        assert!(err("400 Claude Code 2.1.200 does not support this model", 1).contains("à jour"));
        assert!(err("", -1).contains("trop de temps"));
        assert!(err("Traceback (most recent call last):", 1).contains("Réessaie"));
    }

    #[test]
    fn the_last_word_is_the_answer_and_whose_subscription() {
        let m = done_message(Route::Claude, "Bonjour, je suis prêt.");
        assert_eq!(m, "Hermes t'a répondu : « Bonjour, je suis prêt. » Il passe par ton abonnement Claude : ce que tu lui demandes compte sur ton forfait, comme avec Claude.");
        assert!(done_message(Route::Chatgpt, "Salut").contains("ton compte ChatGPT"));
        assert_eq!(shorten("abcdef", 3), "abc…");
        assert_eq!(shorten("é", 3), "é");
    }

    /// Every message the user may read is French, says Hermes without an accent, and leaves a way out.
    #[test]
    fn every_stop_leaves_a_way_out() {
        for why in [NOT_INSTALLED, CLAUDE_LOGGED_OUT, CLAUDE_NOT_SUBSCRIBED] {
            assert!(why.to_lowercase().contains("réessaie"), "{why}");
        }
        let source = include_str!("hermes_connect.rs");
        assert!(!source.contains(concat!("Herm", "\u{e8}s")), "Hermes, not Herm\u{e8}s");
        assert!(!source.contains(concat!(" de ", "Hermes")), "d'Hermes");
    }

    /// Found with no provider, a Hermes that is not set or does not answer is left with none: the
    /// next check finds it still to connect. Connected before, its settings stay.
    #[cfg(unix)]
    #[test]
    fn no_answer_takes_the_settings_back_on_a_fresh_hermes() {
        use std::os::unix::fs::PermissionsExt;
        let tmp = tempfile::tempdir().unwrap();
        let root = std::fs::canonicalize(tmp.path()).unwrap();
        let argv = root.join("argv");
        // `config set` and `config unset` say what Hermes says, but for `refused`; the chat what a
        // subscription at its limit says.
        let hermes = |refused: &str| {
            let bin = root.join(format!("hermes-{}", refused.replace('.', "-")));
            let script = format!(
                "#!/bin/sh\necho \"$*\" >> '{a}'\ncase \"$1 $2\" in\n'config set') if [ \"$3\" = '{refused}' ]; then echo 'Permission denied' >&2; exit 1; fi; echo \"✓ Set $3 = $4 in config.yaml\"; exit 0;;\n'config unset') echo \"✓ Unset $3\"; exit 0;;\nesac\necho 'Error: rate limit reached for this subscription' >&2\nexit 1\n",
                a = argv.display()
            );
            std::fs::write(&bin, script).unwrap();
            std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap();
            bin
        };
        let ran = || {
            let lines = std::fs::read_to_string(&argv).unwrap();
            std::fs::remove_file(&argv).unwrap();
            lines
        };
        let after = |lines: &str, what: &str| -> Vec<String> {
            lines.lines().filter_map(|l| l.strip_prefix(what)).map(|k| k.split(' ').next().unwrap().to_string()).collect()
        };
        let keys: Vec<String> = settings(Route::Claude).iter().map(|(k, _)| k.to_string()).collect();

        let mut lines = Vec::new();
        let why = settle(&hermes("none"), Route::Claude, true, &mut |l| lines.push(l)).expect_err("the limit");
        assert!(why.starts_with("Ton abonnement a atteint sa limite"), "{why}");
        let r = ran();
        assert_eq!(after(&r, "config set "), keys, "set, then the chat");
        assert_eq!(after(&r, "config unset "), keys, "each setting taken back");
        assert!(lines.iter().any(|l| l.contains("rate limit")), "Hermes's own words, for the report");

        let why = settle(&hermes("model.default"), Route::Claude, true, &mut |_| {}).expect_err("not set");
        assert!(why.starts_with("Je n'ai pas pu régler Hermes"), "{why}");
        let r = ran();
        assert!(!r.lines().any(|l| l.starts_with("chat")), "no chat once a setting failed: {r}");
        assert_eq!(after(&r, "config unset "), keys, "the provider set before it taken back too");

        settle(&hermes("none"), Route::Chatgpt, false, &mut |_| {}).expect_err("the limit");
        let r = ran();
        assert!(!r.contains("unset"), "connected before, kept: {r}");
    }
}
