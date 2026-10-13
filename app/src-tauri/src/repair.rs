//! Repairing a failed install step with Claude.
//!
//! So far only `availability`: what the window can offer under a failed step.
//! The analysis is not built yet, so a Mac where Claude could help still gets
//! `Disabled`.

use std::thread;

use serde::Serialize;

use crate::detect::{self, Found, Version};
use crate::diagnose;

/// The first version checked with every flag the analysis relies on.
pub const MIN_CLAUDE: Version = (2, 1, 296);

#[derive(Serialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RepairState {
    /// No Claude to ask: the failed step is the one that installs it, or it does not answer.
    Absent,
    /// Too old, or its version cannot be read.
    Unsupported,
    Offline,
    NotLoggedIn,
    /// This build of the app does not repair.
    Disabled,
}

#[derive(Serialize, Clone, Copy, Debug)]
pub struct RepairStatus {
    pub state: RepairState,
}

struct Facts {
    claude: Found,
    online: bool,
    logged_in: bool,
}

pub fn availability(step: &str) -> RepairStatus {
    let state = if step == "claude" || detect::claude_bin().is_none() {
        RepairState::Absent
    } else if !cfg!(feature = "repair-in-app") {
        RepairState::Disabled
    } else {
        decide(&probe())
    };
    RepairStatus { state }
}

fn probe() -> Facts {
    thread::scope(|s| {
        let online = s.spawn(|| diagnose::answers("https://claude.ai"));
        let logged_in = s.spawn(detect::claude_logged_in);
        Facts {
            claude: detect::claude(),
            online: online.join().unwrap_or(false),
            logged_in: logged_in.join().unwrap_or(false),
        }
    })
}

fn decide(f: &Facts) -> RepairState {
    if !f.claude.answers() {
        RepairState::Absent
    } else if f.claude.version().is_none_or(|v| v < MIN_CLAUDE) {
        RepairState::Unsupported
    } else if !f.online {
        RepairState::Offline
    } else if !f.logged_in {
        RepairState::NotLoggedIn
    } else {
        RepairState::Disabled
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ready() -> Facts {
        Facts {
            claude: Found::Version("2.1.296 (Claude Code)".into()),
            online: true,
            logged_in: true,
        }
    }

    #[test]
    fn the_claude_step_never_asks_claude() {
        assert_eq!(availability("claude").state, RepairState::Absent);
    }

    #[test]
    fn a_claude_that_does_not_answer_is_absent() {
        for claude in [Found::Absent, Found::NoVersion] {
            assert_eq!(decide(&Facts { claude, ..ready() }), RepairState::Absent);
        }
    }

    #[test]
    fn old_or_unreadable_versions_are_unsupported() {
        for v in ["2.1.295 (Claude Code)", "1.9.999", "nightly"] {
            let f = Facts { claude: Found::Version(v.into()), ..ready() };
            assert_eq!(decide(&f), RepairState::Unsupported, "{v}");
        }
        let newer = Facts { claude: Found::Version("2.2.0".into()), ..ready() };
        assert_eq!(decide(&newer), RepairState::Disabled);
    }

    #[test]
    fn no_network_comes_before_the_login() {
        let f = Facts { online: false, logged_in: false, ..ready() };
        assert_eq!(decide(&f), RepairState::Offline);
        let f = Facts { logged_in: false, ..ready() };
        assert_eq!(decide(&f), RepairState::NotLoggedIn);
        assert_eq!(decide(&ready()), RepairState::Disabled);
    }

    #[test]
    fn the_window_reads_snake_case() {
        let json = serde_json::to_value(RepairStatus { state: RepairState::NotLoggedIn }).unwrap_or_default();
        assert_eq!(json, serde_json::json!({ "state": "not_logged_in" }));
    }
}
