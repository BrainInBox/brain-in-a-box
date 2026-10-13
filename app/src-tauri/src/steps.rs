//! STUB — replaced by the real install steps.
use crate::model::StepResult;

pub const ORDER: &[&str] = &["clt", "bun", "claude", "obsidian", "brain"];

pub fn run(id: &str, _voyage_key: Option<&str>, _log: &mut dyn FnMut(String)) -> StepResult {
    StepResult { id: id.to_string(), ok: false, message: "stub".into() }
}
pub fn claude_logged_in() -> bool { false }
pub fn open_claude_login() -> Result<(), String> { Err("stub".into()) }
pub fn start_onboarding() -> Result<(), String> { Err("stub".into()) }
pub fn open_vault() -> Result<(), String> { Err("stub".into()) }
pub fn open_link(_url: &str) -> Result<(), String> { Err("stub".into()) }
