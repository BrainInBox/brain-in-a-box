//! Hermes, Nous Research's agent: the app installs it, connects it to the user's AI, links
//! it to the brain, to WhatsApp and Telegram, and walks the user through the computer's
//! permissions (docs/proposals/hermes.md).
//!
//! Every Hermes feature finds the same `hermes` command here.

use std::path::PathBuf;
use std::time::Duration;

use crate::sys;

/// Long enough for a `hermes` subcommand that only reads its config.
pub const TIMEOUT: Duration = Duration::from_secs(20);

/// The `hermes` command. The official installer puts it in ~/.local/bin, which is on
/// sys::tool_path on a Mac and on Linux.
pub fn bin() -> Option<PathBuf> {
    sys::find_tool("hermes")
}

/// `hermes <args>`, or None when Hermes is not installed.
pub fn run(args: &[&str], timeout: Duration) -> Option<sys::Output> {
    bin().map(|b| sys::run(&b, args, timeout))
}
