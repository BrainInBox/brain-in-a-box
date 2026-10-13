//! STUB — replaced by the real key check.
use crate::model::{KeyCheck, KeyStatus};

pub fn check_key(_key: &str) -> KeyCheck {
    KeyCheck { status: KeyStatus::Offline, message: "stub".into() }
}
