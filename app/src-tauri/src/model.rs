//! Types shared by the diagnosis, the install steps and the window (serialized to JSON).

use serde::Serialize;

#[derive(Serialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    /// Present and working.
    Ok,
    /// Not installed yet: an install step fixes it.
    Missing,
    /// Works, but something is off (old version, no key...). Never blocks.
    Warn,
    /// Blocks the install and no step can fix it (old macOS, no disk space...).
    Error,
}

#[derive(Serialize, Clone, Debug)]
pub struct Check {
    /// Stable id, e.g. "clt", "claude", "brain_hooks".
    pub id: String,
    /// What the user sees, in French, plain words.
    pub label: String,
    pub status: Status,
    /// One short sentence: the version found, or what is missing and why it matters.
    pub detail: String,
    /// Id of the install step that fixes it (see steps::ORDER), if any.
    pub fix: Option<String>,
    /// Where it was found, for `--diagnose` and its tests (which Python, of the Store's stub
    /// and the others). The window does not show it.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
}

#[derive(Serialize, Clone, Debug)]
pub struct Diagnosis {
    /// "macOS 26.3.1"
    pub os: String,
    /// "arm64" or "x86_64"
    pub arch: String,
    pub ram_gb: f64,
    pub disk_free_gb: f64,
    /// github.com and claude.ai answer.
    pub online: bool,
    pub checks: Vec<Check>,
    /// Step ids still to run, in steps::ORDER order. Empty when everything is installed.
    pub todo: Vec<String>,
    /// No check in Status::Error.
    pub can_install: bool,
    /// Which AI the brain should work with, from what is already on this computer (advice.rs).
    pub advice: Option<Advice>,
}

/// Bibo's advice, in the kit's words (engine/hooks/_conseil.py).
#[derive(Serialize, Clone, Debug, PartialEq, Eq)]
pub struct Advice {
    /// "claude", "codex" or "les_deux".
    pub conseil: String,
    /// One sentence for the user.
    pub text: String,
    /// One more when Hermes Agent is there: the kit does not install it.
    pub hermes: Option<String>,
}

#[derive(Serialize, Clone, Debug)]
pub struct StepResult {
    pub id: String,
    pub ok: bool,
    /// One sentence for the user (French). On failure: what happened and what to do.
    pub message: String,
}

/// Emitted as the "step-log" event while a step runs.
#[derive(Serialize, Clone, Debug)]
pub struct LogLine {
    pub step: String,
    pub line: String,
}

#[derive(Serialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum KeyStatus {
    /// Voyage accepted the key.
    Ok,
    /// 401/403: wrong key.
    Invalid,
    /// 429: the key works but Voyage throttles it (no card on the account).
    Limited,
    /// No answer: no network, VPN, proxy.
    Offline,
}

#[derive(Serialize, Clone, Debug)]
pub struct KeyCheck {
    pub status: KeyStatus,
    /// French, for the mascot to say.
    pub message: String,
}
