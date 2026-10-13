//! STUB — replaced by the real diagnosis.
use crate::model::Diagnosis;

pub fn run() -> Diagnosis {
    Diagnosis { os: String::new(), arch: String::new(), ram_gb: 0.0, disk_free_gb: 0.0, online: false, checks: vec![], todo: vec![], can_install: false }
}
