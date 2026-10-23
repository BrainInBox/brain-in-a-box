//! W4: when to look again, on the PC, without sending anything. A 160×90 thumbnail of the app in front,
//! compared every 0.5 s with the last image sent: a clock, a caret, a spinner change too little of it;
//! a dialog that opens changes a lot. A change is sent once it holds still (a second look 250 ms later),
//! at most once every 5 s. Idleness from GetLastInputInfo: no keyboard or mouse hook.

use std::time::{Duration, Instant};

use windows::Win32::System::SystemInformation::GetTickCount;
use windows::Win32::UI::Input::KeyboardAndMouse::{GetLastInputInfo, LASTINPUTINFO};

use crate::prims::Rgba;

pub const VIGNETTE: (u32, u32) = (160, 90);
/// A thumbnail pixel changed: its grey moved by more than this.
pub const SEUIL_PIXEL: u8 = 12;
/// A change worth a look: this share of the thumbnail's pixels changed.
pub const SEUIL_PART: f32 = 0.03;
pub const PAS: Duration = Duration::from_millis(500);
pub const CONFIRME: Duration = Duration::from_millis(250);
pub const ECART_MIN: Duration = Duration::from_secs(5);

/// The image's grey, averaged over each thumbnail pixel's area.
pub fn vignette(img: &Rgba) -> Vec<u8> {
    let (tw, th) = VIGNETTE;
    let mut out = Vec::with_capacity((tw * th) as usize);
    for ty in 0..th {
        let (y0, y1) = (ty * img.h / th, ((ty + 1) * img.h / th).max(ty * img.h / th + 1).min(img.h));
        for tx in 0..tw {
            let (x0, x1) = (tx * img.w / tw, ((tx + 1) * img.w / tw).max(tx * img.w / tw + 1).min(img.w));
            let (mut sum, mut n) = (0u64, 0u64);
            for y in y0..y1 {
                for x in x0..x1 {
                    let p = &img.px[((y * img.w + x) * 4) as usize..][..3];
                    sum += (u64::from(p[0]) * 299 + u64::from(p[1]) * 587 + u64::from(p[2]) * 114) / 1000;
                    n += 1;
                }
            }
            out.push(sum.checked_div(n).unwrap_or(0) as u8);
        }
    }
    out
}

/// The share of the thumbnail's pixels that changed.
pub fn ecart(a: &[u8], b: &[u8]) -> f32 {
    if a.len() != b.len() || a.is_empty() {
        return 1.0;
    }
    a.iter().zip(b).filter(|(x, y)| x.abs_diff(**y) > SEUIL_PIXEL).count() as f32 / a.len() as f32
}

#[derive(Debug, PartialEq)]
pub enum Decision {
    Rien,
    /// A change seen; looked at again soon.
    Attendre,
    Envoyer,
}

#[derive(Default)]
pub struct Veille {
    reference: Option<Vec<u8>>,
    precedente: Option<Vec<u8>>,
    dernier_envoi: Option<Instant>,
    attente: bool,
    pub ecart_max: f32,
    pub regards: usize,
    /// Looks where the thumbnail moved at all from the one before (the witness that the screen lives).
    pub bouge: usize,
}

impl Veille {
    /// The first image is the reference: the look the user asked for was sent with it.
    pub fn depart(img: &Rgba, maintenant: Instant) -> Self {
        let v = vignette(img);
        Veille { reference: Some(v.clone()), precedente: Some(v), dernier_envoi: Some(maintenant), ..Default::default() }
    }

    pub fn voir(&mut self, maintenant: Instant, img: &Rgba) -> Decision {
        let v = vignette(img);
        self.regards += 1;
        let change = self.reference.as_ref().map_or(1.0, |r| ecart(&v, r));
        self.ecart_max = self.ecart_max.max(change);
        let depuis = self.precedente.as_ref().map_or(1.0, |p| ecart(&v, p));
        if self.precedente.as_ref().is_some_and(|p| p != &v) {
            self.bouge += 1;
        }
        let stable = depuis < SEUIL_PART;
        self.precedente = Some(v.clone());
        if change < SEUIL_PART {
            self.attente = false;
            return Decision::Rien;
        }
        if !stable {
            self.attente = true;
            return Decision::Attendre;
        }
        if self.dernier_envoi.is_some_and(|t| maintenant.duration_since(t) < ECART_MIN) {
            self.attente = true;
            return Decision::Attendre;
        }
        self.reference = Some(v);
        self.dernier_envoi = Some(maintenant);
        self.attente = false;
        Decision::Envoyer
    }

    pub fn delai(&self) -> Duration {
        if self.attente {
            CONFIRME
        } else {
            PAS
        }
    }
}

/// How long since the last key or mouse input, anywhere in the session (no hook).
pub fn inactif() -> Duration {
    let mut info = LASTINPUTINFO { cbSize: std::mem::size_of::<LASTINPUTINFO>() as u32, dwTime: 0 };
    // SAFETY: a struct of its size.
    if !unsafe { GetLastInputInfo(&mut info) }.as_bool() {
        return Duration::ZERO;
    }
    // SAFETY: no argument.
    let now = unsafe { GetTickCount() };
    Duration::from_millis(u64::from(now.wrapping_sub(info.dwTime)))
}
