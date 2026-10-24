//! When to look again (spec guide v2, P3; W4 of §10 done again on the whole screen): on the PC, without
//! sending anything. A 160×90 thumbnail of the screen, compared every 0.5 s with the one last sent.
//! - What keeps moving (a clock with seconds, a spinner, a video, a caret) is looked past: a thumbnail
//!   pixel that moved in 3 of the last 6 looks counts for nothing.
//! - The taskbar, its clock and the notifications are looked past too (ecran::zones_du_systeme).
//! - Around the target the user was shown, a change weighs 4 times more: a dialog opening over the
//!   program, a menu under the button. Elsewhere it weighs as much as without a target.
//! - Without any input (a key, a click, the mouse moved) since the last image sent, only a change of a
//!   quarter of the screen counts.
//! - A change is sent once it holds still (looked at again 250 ms later), at most once every 5 s, and
//!   never the same image twice.
//!
//! Idleness comes from GetLastInputInfo: no keyboard or mouse hook.
#![cfg_attr(not(all(test, windows)), allow(dead_code, reason = "the guide v2's change detection, wired into the guide with P3 (guide.rs and guide.js change in #76 first): its desk tests use it until then"))]

use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use super::ecran::{self, Capture, Ecran, MotsDePasse, RectEcran, Refus};
use crate::shot::Rgba;

pub const VIGNETTE: (u32, u32) = (160, 90);
/// A thumbnail pixel changed: its grey moved by more than this.
pub const SEUIL_PIXEL: u8 = 12;
/// A change worth a look: this share of the screen, weighted.
pub const SEUIL_PART: f32 = 0.03;
/// Without any input since the last image sent: a change this large only.
pub const SANS_GESTE: f32 = 0.25;
/// What moved in AGITE of the last HISTOIRE looks keeps moving.
pub const AGITE: usize = 3;
pub const HISTOIRE: usize = 6;
/// Around the target: this share of the screen's width and height on each side of it.
pub const AUTOUR: f32 = 0.25;
pub const POIDS_CIBLE: u8 = 4;
pub const PAS: Duration = Duration::from_millis(500);
pub const CONFIRME: Duration = Duration::from_millis(250);
pub const ECART_MIN: Duration = Duration::from_secs(5);
/// No look after this long without a key or the mouse (P3): the user is away.
pub const INACTIF_PAUSE: Duration = Duration::from_secs(120);

/// No look while the screen is locked or on the secure desktop (`ouvert` false), nor after 2 min without any
/// input: the user is away.
fn pause(inactif: Duration, ouvert: bool) -> bool {
    inactif >= INACTIF_PAUSE || !ouvert
}

/// The image's grey, averaged over each thumbnail pixel's area.
pub fn vignette(img: &Rgba) -> Vec<u8> {
    let (tw, th) = VIGNETTE;
    let (w, h) = (img.w as usize, img.h as usize);
    let mut sums = vec![0u64; (tw * th) as usize];
    let mut counts = vec![0u64; (tw * th) as usize];
    let cols: Vec<usize> = (0..w).map(|x| (x * tw as usize / w.max(1)).min(tw as usize - 1)).collect();
    for y in 0..h {
        let ty = (y * th as usize / h.max(1)).min(th as usize - 1) * tw as usize;
        for (x, p) in img.px[y * w * 4..(y + 1) * w * 4].chunks_exact(4).enumerate() {
            let i = ty + cols[x];
            sums[i] += (u64::from(p[0]) * 299 + u64::from(p[1]) * 587 + u64::from(p[2]) * 114) / 1000;
            counts[i] += 1;
        }
    }
    sums.iter().zip(&counts).map(|(s, n)| s.checked_div(*n).unwrap_or(0) as u8).collect()
}

/// Thumbnail pixels: left, top, right, bottom (right and bottom out).
type ZoneV = (usize, usize, usize, usize);

/// The thumbnail pixels a rectangle of the screen covers, any part of them.
fn en_vignette(r: &RectEcran, ecran: &RectEcran) -> Option<ZoneV> {
    let z = r.inter(ecran)?;
    let (tw, th) = (VIGNETTE.0 as i64, VIGNETTE.1 as i64);
    let (w, h) = (i64::from(ecran.largeur).max(1), i64::from(ecran.hauteur).max(1));
    let (x0, y0) = (i64::from(z.x - ecran.x), i64::from(z.y - ecran.y));
    let (x1, y1) = (x0 + i64::from(z.largeur), y0 + i64::from(z.hauteur));
    let haut = |a: i64, b: i64| (a + b - 1) / b;
    Some(((x0 * tw / w) as usize, (y0 * th / h) as usize, haut(x1 * tw, w).min(tw) as usize, haut(y1 * th, h).min(th) as usize))
}

fn pixels(zones: &[ZoneV]) -> impl Iterator<Item = usize> + '_ {
    zones.iter().flat_map(|&(x0, y0, x1, y1)| (y0..y1).flat_map(move |y| (x0..x1).map(move |x| y * VIGNETTE.0 as usize + x)))
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Decision {
    Rien,
    /// A change seen: looked at again soon.
    Attendre,
    Envoyer,
}

pub struct Veille {
    reference: Vec<u8>,
    precedente: Vec<u8>,
    /// Which thumbnail pixels moved, look by look, the latest last.
    histoire: VecDeque<Vec<bool>>,
    poids: Vec<u8>,
    /// When the reference was sent.
    depuis: Instant,
    attente: bool,
    pub regards: usize,
    /// Looks where the thumbnail moved at all (the witness that the screen lives).
    pub bouge: usize,
    pub ecart_max: f32,
}

impl Veille {
    /// The first image is the reference: the look the user asked for went with it. Around `cible` (the
    /// target the user was shown, on `ecran`), a change weighs more.
    pub fn depart(v: Vec<u8>, maintenant: Instant, ecran: &RectEcran, cible: Option<&RectEcran>) -> Self {
        let mut poids = vec![1u8; v.len()];
        if let Some(autour) = cible.map(|c| c.agrandi((ecran.largeur as f32 * AUTOUR) as i32, (ecran.hauteur as f32 * AUTOUR) as i32)) {
            for i in pixels(&en_vignette(&autour, ecran).into_iter().collect::<Vec<_>>()) {
                poids[i] = POIDS_CIBLE;
            }
        }
        Veille { reference: v.clone(), precedente: v, histoire: VecDeque::new(), poids, depuis: maintenant, attente: false, regards: 0, bouge: 0, ecart_max: 0.0 }
    }

    /// One look: `v` its thumbnail, `geste` when the last input was (a key, a click, the mouse), `ignorer` the system's own
    /// windows on `ecran` now.
    pub fn voir(&mut self, maintenant: Instant, v: Vec<u8>, geste: Option<Instant>, ecran: &RectEcran, ignorer: &[RectEcran]) -> Decision {
        self.regards += 1;
        if v.len() != self.reference.len() {
            return Decision::Rien;
        }
        let moved: Vec<bool> = v.iter().zip(&self.precedente).map(|(a, b)| a.abs_diff(*b) > SEUIL_PIXEL).collect();
        if moved.iter().any(|&m| m) {
            self.bouge += 1;
        }
        self.histoire.push_back(moved);
        if self.histoire.len() > HISTOIRE {
            self.histoire.pop_front();
        }
        let mut compte = vec![true; v.len()];
        for i in pixels(&ignorer.iter().filter_map(|z| en_vignette(z, ecran)).collect::<Vec<_>>()) {
            compte[i] = false;
        }
        // Shares of the pixels looked at; near the target a pixel weighs more, elsewhere it weighs as much
        // as without a target (the weight adds, never dilutes).
        let (mut total, mut change, mut brut, mut bouges) = (0u64, 0u64, 0u64, 0u64);
        let latest = self.histoire.back().cloned().unwrap_or_default();
        for i in 0..v.len() {
            let agite = self.histoire.iter().filter(|h| h[i]).count() >= AGITE;
            if !compte[i] || agite {
                continue;
            }
            let w = u64::from(self.poids[i]);
            total += 1;
            if v[i].abs_diff(self.reference[i]) > SEUIL_PIXEL {
                change += w;
                brut += 1;
            }
            if latest[i] {
                bouges += w;
            }
        }
        let share = |n: u64| if total == 0 { 0.0 } else { n as f32 / total as f32 };
        // Weighted alike: a change that counts near the target is still settling while it moves.
        let (part, stable) = (share(change), total > 0 && share(bouges) < SEUIL_PART);
        self.ecart_max = self.ecart_max.max(part);
        self.precedente = v;
        let gestes = geste.is_some_and(|g| g > self.depuis);
        self.attente = false;
        if part < SEUIL_PART || (!gestes && share(brut) < SANS_GESTE) {
            return Decision::Rien;
        }
        if !stable || maintenant.duration_since(self.depuis) < ECART_MIN {
            self.attente = true;
            return Decision::Attendre;
        }
        self.reference = self.precedente.clone();
        self.depuis = maintenant;
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

/// What a watch saw, for the tests and the status.
#[derive(Debug, Default, Clone)]
pub struct Bilan {
    pub regards: usize,
    pub bouge: usize,
    pub ecart_max: f32,
    /// Looks skipped: the screen locked, the secure desktop, the user away.
    pub pauses: usize,
    /// Images refused because the overlay was in them.
    pub calque: usize,
    /// How long each capture took, in ms.
    pub durees: Vec<f32>,
}

/// The watch of one step: the screen read every 0.5 s on a thread of its own; `envoi` gets each image
/// to send. Stopped when dropped.
pub struct Veilleur {
    arret: Arc<AtomicBool>,
    fil: Option<JoinHandle<Bilan>>,
}

impl Veilleur {
    /// `premiere`: the image the step began with (already sent), the reference.
    pub fn lancer(
        premiere: &Capture,
        cible: Option<RectEcran>,
        mots_de_passe: Arc<dyn MotsDePasse>,
        mut envoi: impl FnMut(Capture) + Send + 'static,
    ) -> Veilleur {
        let arret = Arc::new(AtomicBool::new(false));
        let stop = arret.clone();
        let ecran: Ecran = premiere.ecran;
        let depart = vignette(&premiere.image);
        let fil = std::thread::spawn(move || {
            let mut veille = Veille::depart(depart, Instant::now(), &ecran.rect, cible.as_ref());
            let mut bilan = Bilan::default();
            while !stop.load(Ordering::SeqCst) {
                let t = Instant::now();
                let inactif = ecran::inactif();
                let pas = if pause(inactif, ecran::bureau_ouvert()) {
                    bilan.pauses += 1;
                    PAS
                } else {
                    match ecran::capturer(&ecran, mots_de_passe.as_ref()) {
                        Ok(c) => {
                            bilan.durees.push(t.elapsed().as_secs_f32() * 1000.0);
                            let geste = Instant::now().checked_sub(inactif);
                            let (ignorer, v) = (ecran::zones_du_systeme(&ecran), vignette(&c.image));
                            // The look's time, as close to the sending as can be: 5 s apart, sent 5 s apart.
                            if veille.voir(Instant::now(), v, geste, &ecran.rect, &ignorer) == Decision::Envoyer {
                                envoi(c);
                            }
                            veille.delai()
                        }
                        Err(Refus::Pause) => {
                            bilan.pauses += 1;
                            PAS
                        }
                        Err(Refus::Calque) => {
                            bilan.calque += 1;
                            PAS
                        }
                        Err(_) => PAS,
                    }
                };
                std::thread::sleep(pas.saturating_sub(t.elapsed()));
            }
            Bilan { regards: veille.regards, bouge: veille.bouge, ecart_max: veille.ecart_max, ..bilan }
        });
        Veilleur { arret, fil: Some(fil) }
    }

    pub fn arreter(mut self) -> Bilan {
        self.arret.store(true, Ordering::SeqCst);
        self.fil.take().and_then(|f| f.join().ok()).unwrap_or_default()
    }
}

impl Drop for Veilleur {
    fn drop(&mut self) {
        self.arret.store(true, Ordering::SeqCst);
        if let Some(f) = self.fil.take() {
            let _ = f.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const N: usize = (VIGNETTE.0 * VIGNETTE.1) as usize;
    const ECRAN: RectEcran = RectEcran { x: 0, y: 0, largeur: 1920, hauteur: 1080 };

    /// A screen of thumbnails: grey 100, with rectangles (in thumbnail pixels) of other greys.
    fn ecran(zones: &[(ZoneV, u8)]) -> Vec<u8> {
        let mut v = vec![100u8; N];
        for &(z, g) in zones {
            for i in pixels(&[z]) {
                v[i] = g;
            }
        }
        v
    }

    /// Looks every 0.5 s from `t0`, with these thumbnails; the decisions.
    struct Film {
        veille: Veille,
        t: Instant,
    }

    impl Film {
        fn new(cible: Option<RectEcran>) -> Film {
            let t = Instant::now();
            Film { veille: Veille::depart(ecran(&[]), t, &ECRAN, cible.as_ref()), t }
        }

        fn voir(&mut self, v: Vec<u8>, geste: bool, ignorer: &[RectEcran]) -> Decision {
            self.t += PAS;
            let g = geste.then_some(self.t - Duration::from_millis(100));
            self.veille.voir(self.t, v, g, &ECRAN, ignorer)
        }
    }

    #[test]
    fn the_thumbnail_averages_the_screen() {
        let mut img = crate::shot::tests::rgba(1920, 1080, [0, 0, 0, 255]);
        for y in 0..1080 {
            for x in 0..480 {
                img.px[(y * 1920 + x) * 4..][..3].copy_from_slice(&[255, 255, 255]);
            }
        }
        let v = vignette(&img);
        assert_eq!(v.len(), N);
        assert_eq!(v.iter().filter(|&&g| g == 255).count(), N / 4);
        assert_eq!(en_vignette(&RectEcran { x: 1900, y: 1070, largeur: 100, hauteur: 100 }, &ECRAN), Some((158, 89, 160, 90)));
    }

    /// A dialog of 400×200 on a 1920×1080 screen (3.9 %, near the button the user was shown), after a click:
    /// once it holds still, one sending; then nothing more while it stays.
    #[test]
    fn a_dialog_after_a_click_is_sent_once_it_holds_still() {
        let bouton = RectEcran { x: 300, y: 200, largeur: 160, hauteur: 40 };
        let mut f = Film::new(Some(bouton));
        f.t += ECART_MIN;
        // 400×200 at (500, 300): 34×17 thumbnail pixels.
        let dialogue = |g| ecran(&[((42, 25, 75, 42), g)]);
        assert_eq!(f.voir(dialogue(255), true, &[]), Decision::Attendre, "white for 200 ms: it moves");
        assert_eq!(f.veille.delai(), CONFIRME);
        assert_eq!(f.voir(dialogue(200), true, &[]), Decision::Attendre, "filled: it moved again");
        assert_eq!(f.voir(dialogue(200), true, &[]), Decision::Envoyer);
        for _ in 0..20 {
            assert_eq!(f.voir(dialogue(200), true, &[]), Decision::Rien, "the same image never twice");
        }
    }

    /// A clock, a spinner, a caret, a video: what keeps moving never sends, the mouse moving all along.
    #[test]
    fn what_keeps_moving_is_never_sent() {
        let mut f = Film::new(None);
        f.t += ECART_MIN;
        for k in 0..120u8 {
            // A video of 640×360 (53×30 pixels: 11 % of the screen), a spinner, the clock's seconds.
            let g: u8 = if k % 2 == 0 { 30 } else { 220 };
            let v = ecran(&[((10, 10, 63, 40), g.wrapping_add(k)), ((140, 5, 143, 8), g), ((150, 86, 158, 89), k.wrapping_mul(7))]);
            assert_ne!(f.voir(v, true, &[]), Decision::Envoyer, "look {k}");
        }
        assert!(f.veille.ecart_max >= SEUIL_PART, "witness: the video alone is past the threshold before it is known to move");
    }

    /// A notification over the taskbar's corner, the clock's minute: below the threshold, or the system's
    /// own windows, looked past.
    #[test]
    fn a_notification_and_the_clock_are_looked_past() {
        // A large notification, 400×180 (3.5 % of the screen, past the threshold): as the system's own
        // window, it is looked past whatever its size.
        let toast = RectEcran { x: 1500, y: 840, largeur: 400, hauteur: 180 };
        let avec = ecran(&[((125, 70, 159, 85), 240)]);
        let mut f = Film::new(None);
        f.t += ECART_MIN;
        assert_eq!(f.voir(avec.clone(), true, &[toast]), Decision::Rien);
        assert_eq!(f.voir(avec.clone(), true, &[toast]), Decision::Rien);
        // The clock's minute, as the notification goes: 0.2 %.
        assert_eq!(f.voir(ecran(&[((150, 86, 158, 89), 240)]), true, &[toast]), Decision::Rien);
        // The same notification, not known as the system's: the witness that it would count.
        let mut f = Film::new(None);
        f.t += ECART_MIN;
        assert_eq!(f.voir(avec.clone(), true, &[]), Decision::Attendre);
        assert_eq!(f.voir(avec, true, &[]), Decision::Envoyer);
    }

    /// Without any input since the last image sent, a dialog that opens alone (an update, a reminder)
    /// is not sent; a whole new screen is.
    #[test]
    fn without_a_gesture_only_a_new_screen_counts() {
        let mut f = Film::new(None);
        f.t += ECART_MIN;
        let dialogue = ecran(&[((40, 20, 100, 60), 230)]);
        for _ in 0..10 {
            assert_eq!(f.voir(dialogue.clone(), false, &[]), Decision::Rien);
        }
        assert_eq!(f.voir(dialogue.clone(), true, &[]), Decision::Envoyer, "the same, once the user clicked");
        f.t += ECART_MIN;
        let nouvel = ecran(&[((0, 0, 160, 50), 10)]);
        assert_eq!(f.voir(nouvel.clone(), false, &[]), Decision::Attendre);
        assert_eq!(f.voir(nouvel, false, &[]), Decision::Envoyer, "more than a quarter of the screen");
    }

    /// Near the target, a small change counts (a menu under the button: 1.5 % of the screen); the same far
    /// from it does not.
    #[test]
    fn a_change_near_the_target_weighs_more() {
        let bouton = RectEcran { x: 300, y: 200, largeur: 160, hauteur: 40 };
        let menu = ecran(&[((25, 15, 45, 34), 230)]);
        let loin = ecran(&[((130, 60, 150, 79), 230)]);
        let mut f = Film::new(Some(bouton));
        f.t += ECART_MIN;
        assert_eq!(f.voir(menu.clone(), true, &[]), Decision::Attendre);
        assert_eq!(f.voir(menu, true, &[]), Decision::Envoyer);
        let mut f = Film::new(Some(bouton));
        f.t += ECART_MIN;
        assert_eq!(f.voir(loin.clone(), true, &[]), Decision::Rien);
        assert_eq!(f.voir(loin, true, &[]), Decision::Rien);
        let mut f = Film::new(None);
        f.t += ECART_MIN;
        assert_eq!(f.voir(ecran(&[((25, 15, 45, 34), 230)]), true, &[]), Decision::Rien, "no target: 1.5 % is too little");
    }

    /// The weight around the target adds, it takes nothing away: a dialog of 400×200 in the middle of the
    /// screen, far from a button of the toolbar, counts as it would without a target.
    #[test]
    fn a_dialog_far_from_the_target_still_counts() {
        let bouton = RectEcran { x: 40, y: 90, largeur: 120, hauteur: 40 };
        // 400×200 at (760, 440): 34×17 thumbnail pixels, out of the zone around the button.
        let dialogue = ecran(&[((63, 36, 97, 53), 230)]);
        let mut f = Film::new(Some(bouton));
        f.t += ECART_MIN;
        assert_eq!(f.voir(dialogue.clone(), true, &[]), Decision::Attendre);
        assert_eq!(f.voir(dialogue, true, &[]), Decision::Envoyer);
    }

    /// A video keeps playing while the user works: what it moves is looked past, and the dialog that opens
    /// near the button is sent once it holds still.
    #[test]
    fn a_dialog_while_a_video_plays_is_sent() {
        let bouton = RectEcran { x: 300, y: 200, largeur: 160, hauteur: 40 };
        let video = |k: u8| ((100, 10, 150, 40), if k % 2 == 0 { 30 } else { 220 });
        let mut f = Film::new(Some(bouton));
        f.t += ECART_MIN;
        for k in 0..6 {
            assert_ne!(f.voir(ecran(&[video(k)]), true, &[]), Decision::Envoyer, "look {k}");
        }
        let envoye = (6..12).find(|&k| f.voir(ecran(&[video(k), ((42, 25, 75, 42), 230)]), true, &[]) == Decision::Envoyer);
        assert_eq!(envoye, Some(7), "the dialog, the second look it is there");
    }

    /// Nothing is looked at while the screen is locked or on UAC's desktop, nor after 2 min without a key or
    /// the mouse.
    #[test]
    fn no_look_while_locked_or_away() {
        assert!(!pause(Duration::ZERO, true) && !pause(INACTIF_PAUSE - Duration::from_millis(1), true));
        assert!(pause(INACTIF_PAUSE, true) && pause(Duration::ZERO, false));
    }

    /// Two sendings 5 s apart at least: a dialog that closes 2 s after it was sent waits.
    #[test]
    fn two_sendings_are_5_s_apart() {
        let mut f = Film::new(None);
        f.t += ECART_MIN;
        let dialogue = ecran(&[((40, 20, 100, 60), 230)]);
        assert_eq!(f.voir(dialogue.clone(), true, &[]), Decision::Attendre);
        assert_eq!(f.voir(dialogue, true, &[]), Decision::Envoyer);
        let envoi = f.t;
        let mut n = 0;
        loop {
            n += 1;
            match f.voir(ecran(&[]), true, &[]) {
                Decision::Envoyer => break,
                _ => assert!(n < 30),
            }
        }
        assert!(f.t.duration_since(envoi) >= ECART_MIN, "{:?}", f.t.duration_since(envoi));
    }
}
