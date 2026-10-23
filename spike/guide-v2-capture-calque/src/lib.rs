//! Throwaway: the guide v2's P0 proofs W1 (the app in front), W3 (the overlay), W4 (change detection),
//! on the Windows VMs of the CI only. Windows of known colours, pages of known colours on 127.0.0.1;
//! pixels counted in memory, nothing written but the test programs.
#![cfg(windows)]

pub mod calque;
pub mod compose;
pub mod echelle;
pub mod navigateur;
pub mod prims;
pub mod veille;

#[cfg(test)]
mod tests;
