//! Bibo's overlay (spec guide v2, §3 « Qui dessine », §10 W3): a window over the screen of the target,
//! transparent but for a ring and a bubble, always on top, never active, that clicks go through, and kept
//! out of every capture. Bibo never clicks: it only shows where.
//!
//! What W3 measured on Windows Server 2022 and 2025 is the order here: the window's display affinity
//! (WDA_EXCLUDEFROMCAPTURE) set before `set_ignore_cursor_events` (2022 refuses it afterwards, and on a
//! window drawn by UpdateLayeredWindow, never used), then read back once shown: anything but 0x11 and
//! nothing is drawn. The window goes back on top every 250 ms (a window always on top opened after it
//! would cover it). And should Windows still let it into a capture, the capture finds its colours and
//! is not sent (capture/ecran.rs).
//!
//! The page (calque.html) calls no command: its window has no capability. Rust gives it everything to
//! draw, in the screen's physical pixels; the text goes in as text. Windows only for now; elsewhere,
//! « non disponible ».
#![cfg_attr(
    not(test),
    allow(dead_code, reason = "the capture that searches for the overlay is wired into the guide with P3 (guide.rs and guide.js change in #76 first)")
)]

use std::sync::{LazyLock, Mutex};

use regex::Regex;
use serde::{Deserialize, Serialize};

use crate::capture::ecran::{Calque, Ecran, RectEcran, Trace};

pub const LABEL: &str = "calque";
/// WDA_EXCLUDEFROMCAPTURE, as GetWindowDisplayAffinity reads it back.
const EXCLUE: u32 = 0x11;
/// The text of a bubble at most, on one line (Claude's `consigne`, guide.rs).
const TEXTE_MAX: usize = 240;
/// A target this small is a guess, this large is not a place (the share of the screen: guide.rs's MAX_AIRE).
const COTE_MIN: u32 = 4;
const AIRE_MAX: f64 = 0.25;
/// In the overlay's own pixels at 100 % (CSS pixels): the ring 6 away from the target, a light band, a dark
/// one, a light one; the bubble, its gap, its largest box (5 lines).
const MARGE: f64 = 6.0;
const CLAIR: f64 = 2.0;
const FONCE: f64 = 4.0;
const BULLE: (f64, f64) = (360.0, 132.0);
const ECART: f64 = 10.0;

#[derive(Deserialize, Serialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Ton {
    Normal,
    /// A click that commits (`action_sensible`): amber, and the word.
    Attention,
    /// A suspect page (ClickFix): red, never a ring.
    Danger,
}

/// What the guide asks the overlay to show.
#[derive(Deserialize, Clone, Debug, PartialEq)]
pub struct Demande {
    /// The target, in the screen's physical pixels.
    pub cible: RectEcran,
    /// A ring around it: the guide asks for one only at the « plein » level.
    pub anneau: bool,
    pub texte: String,
    pub ton: Ton,
}

#[derive(Serialize, Clone, Copy, Debug, PartialEq, Eq)]
pub struct Montre {
    pub anneau: bool,
    pub bulle: bool,
}

#[derive(Serialize, Clone, Debug, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Refus {
    /// No overlay on this system (yet).
    Indisponible,
    /// The bubble's text holds a link, a number to call, a command or remote access (guide.rs §4.6), or
    /// nothing at all.
    Texte,
    /// The target lies on no screen whole, or is too small or too large to point at.
    Cible,
    Fenetre { message: String },
}

#[derive(Clone, Copy, Debug, PartialEq)]
struct Palette {
    fonce: [u8; 3],
    clair: [u8; 3],
    fond: [u8; 3],
    bord: [u8; 3],
}

impl Ton {
    /// Bibo's own colours (styles.css: --accent, --accent-soft, --ink, --warn, --error).
    fn palette(self) -> Palette {
        const ENCRE: [u8; 3] = [0x2e, 0x22, 0x20];
        match self {
            Ton::Normal => Palette { fonce: [0xc2, 0x3a, 0x6e], clair: [0xfb, 0xe3, 0xec], fond: ENCRE, bord: [0xc2, 0x3a, 0x6e] },
            Ton::Attention => Palette { fonce: [0xa3, 0x5f, 0x09], clair: [0xfd, 0xf0, 0xd5], fond: ENCRE, bord: [0xa3, 0x5f, 0x09] },
            Ton::Danger => Palette { fonce: [0xc0, 0x39, 0x2b], clair: [0xfd, 0xe2, 0xdf], fond: [0xc0, 0x39, 0x2b], bord: ENCRE },
        }
    }

    /// Amber and red carry a word too, not a colour alone (C11).
    fn mot(self) -> Option<&'static str> {
        (self != Ton::Normal).then_some("Attention")
    }
}

fn hex(c: [u8; 3]) -> String {
    format!("#{:02x}{:02x}{:02x}", c[0], c[1], c[2])
}

/// Where everything goes, in the screen's physical pixels.
#[derive(Clone, Debug, PartialEq)]
struct Plan {
    ecran: Ecran,
    /// The ring's outer box, and its bands' widths (light, dark).
    anneau: Option<(RectEcran, u32, u32)>,
    /// The bubble's largest box; drawn from its bottom up when it sits above the target.
    bulle: RectEcran,
    en_haut: bool,
    texte: String,
    ton: Ton,
}

static INVISIBLE: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"[\p{Cc}\p{Cf}]").ok());

/// The bubble's text on one line, without what does not show (bidi marks reorder a sentence); None when
/// the guide's filter refuses it or nothing is left.
fn texte_montre(texte: &str) -> Option<String> {
    let Some(re) = &*INVISIBLE else { return None };
    let mots: Vec<String> = texte.split_whitespace().map(|m| re.replace_all(m, "").into_owned()).filter(|m| !m.is_empty()).collect();
    let vu: String = mots.join(" ").chars().take(TEXTE_MAX).collect();
    (!vu.is_empty() && crate::guide::allowed(&vu)).then_some(vu)
}

fn plan(d: &Demande, ecran: Ecran) -> Result<Plan, Refus> {
    let texte = texte_montre(&d.texte).ok_or(Refus::Texte)?;
    let (c, s) = (d.cible, ecran.echelle.max(0.5));
    let aire = c.aire() as f64 / ecran.rect.aire().max(1) as f64;
    if !ecran.rect.contient(&c) || c.largeur < COTE_MIN || c.hauteur < COTE_MIN || aire > AIRE_MAX {
        return Err(Refus::Cible);
    }
    let px = |v: f64| (v * s).round().max(1.0) as u32;
    let (clair, fonce) = (px(CLAIR), px(FONCE).max(2));
    let tour = px(MARGE) + 2 * clair + fonce;
    let exterieur = c.agrandi(tour as i32, tour as i32);
    let anneau = (d.anneau && d.ton != Ton::Danger).then_some((exterieur, clair, fonce));
    let (bw, bh, ecart) = (px(BULLE.0).min(ecran.rect.largeur), px(BULLE.1).min(ecran.rect.hauteur), i64::from(px(ECART)));
    let (top, bottom) = (i64::from(ecran.rect.y), ecran.rect.bas());
    let dessous = exterieur.bas() + ecart;
    let dessus = i64::from(exterieur.y) - ecart - i64::from(bh);
    let (y, en_haut) = if dessous + i64::from(bh) <= bottom {
        (dessous, false)
    } else if dessus >= top {
        (dessus, true)
    } else {
        (bottom - i64::from(bh), true)
    };
    let x = i64::from(exterieur.x).clamp(i64::from(ecran.rect.x), ecran.rect.droite() - i64::from(bw));
    let bulle = RectEcran { x: x as i32, y: y as i32, largeur: bw, hauteur: bh };
    Ok(Plan { ecran, anneau, bulle, en_haut, texte, ton: d.ton })
}

impl Plan {
    /// What a capture is searched for: the ring's four bands, the bubble's box.
    fn traces(&self) -> Vec<Trace> {
        let p = self.ton.palette();
        let mut out = Vec::new();
        if let Some((r, clair, fonce)) = self.anneau {
            let b = 2 * clair + fonce;
            let bandes = [
                RectEcran { hauteur: b, ..r },
                RectEcran { y: (r.bas() - i64::from(b)) as i32, hauteur: b, ..r },
                RectEcran { largeur: b, ..r },
                RectEcran { x: (r.droite() - i64::from(b)) as i32, largeur: b, ..r },
            ];
            out.extend(bandes.into_iter().map(|zone| Trace { zone, couleurs: [p.fonce, p.clair] }));
        }
        out.push(Trace { zone: self.bulle, couleurs: [p.fond, p.bord] });
        out
    }

    /// The page's call (calque.js), its coordinates from the overlay's corner.
    fn script(&self) -> String {
        let (ox, oy) = (self.ecran.rect.x, self.ecran.rect.y);
        let boite = |r: &RectEcran| serde_json::json!({ "x": r.x - ox, "y": r.y - oy, "largeur": r.largeur, "hauteur": r.hauteur });
        let p = self.ton.palette();
        let d = serde_json::json!({
            "anneau": self.anneau.map(|(r, clair, fonce)| serde_json::json!({ "boite": boite(&r), "clair": clair, "fonce": fonce })),
            "bulle": { "boite": boite(&self.bulle), "en_haut": self.en_haut, "bord": (self.ecran.echelle * 2.0).round().max(1.0) },
            "texte": self.texte,
            "mot": self.ton.mot(),
            "couleurs": { "fonce": hex(p.fonce), "clair": hex(p.clair), "fond": hex(p.fond), "bord": hex(p.bord) },
        });
        format!("window.calqueDessiner && window.calqueDessiner({d})")
    }
}

/// Only an affinity read back as WDA_EXCLUDEFROMCAPTURE lets a drawing show.
fn exclue(lue: u32) -> bool {
    lue == EXCLUE
}

/// What captures are searched for once the page is asked to draw `nouveau`: that, and what it drew before,
/// which may still show until the page has drawn anew.
fn dessiner(e: &mut Calque, nouveau: Vec<Trace>) {
    e.traces = std::mem::replace(&mut e.dessin, nouveau.clone()).into_iter().chain(nouveau).collect();
}

/// One showing or hiding at a time (never taken by `fermer`, which may run on the main thread they wait on).
static SERIE: Mutex<()> = Mutex::new(());

/// The ring and the bubble at the target, or nothing at all when Windows would let them into a capture.
/// Never from the main thread (it waits on it).
pub fn montrer<R: tauri::Runtime>(app: &tauri::AppHandle<R>, d: &Demande) -> Result<Montre, Refus> {
    let _une_a_la_fois = SERIE.lock().unwrap_or_else(|p| p.into_inner());
    if !cfg!(windows) {
        return Err(Refus::Indisponible);
    }
    let ecran = crate::capture::ecran::de_rect(&d.cible).ok_or(Refus::Cible)?;
    let plan = plan(d, ecran)?;
    os::montrer(app, &plan)
}

/// The ring and the bubble gone; the overlay hidden.
pub fn effacer<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> Result<(), Refus> {
    let _une_a_la_fois = SERIE.lock().unwrap_or_else(|p| p.into_inner());
    os::effacer(app)
}

/// The guide closed: the overlay with it. From any thread, the main one included.
pub fn fermer<R: tauri::Runtime>(app: &tauri::AppHandle<R>) {
    os::fermer(app);
}

#[cfg(windows)]
mod os {
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{mpsc, Arc, Mutex};
    use std::time::Duration;

    use tauri::webview::PageLoadEvent;
    use tauri::{AppHandle, Manager, PhysicalPosition, PhysicalSize, Runtime, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
    use windows::Win32::Foundation::HWND;
    use windows::Win32::UI::WindowsAndMessaging::{
        GetWindowDisplayAffinity, IsWindowVisible, SetWindowDisplayAffinity, SetWindowPos, HWND_TOPMOST, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOOWNERZORDER,
        SWP_NOSIZE, WDA_EXCLUDEFROMCAPTURE,
    };

    use super::{exclue, Montre, Plan, Refus, LABEL};
    use crate::capture::ecran::calque as etat;

    const EFFACER: &str = "window.calqueEffacer && window.calqueEffacer()";

    /// How often the overlay goes back on top (W3).
    const REHAUSSE: Duration = Duration::from_millis(250);
    const CHARGEMENT: Duration = Duration::from_secs(15);

    static ARRET: Mutex<Option<Arc<AtomicBool>>> = Mutex::new(None);

    fn erreur(e: impl std::fmt::Display) -> Refus {
        Refus::Fenetre { message: e.to_string() }
    }

    fn hwnd(id: u64) -> HWND {
        HWND(id as usize as *mut std::ffi::c_void)
    }

    /// `f` on the main thread, its result awaited.
    fn sur_le_fil<R: Runtime, T: Send + 'static>(app: &AppHandle<R>, f: impl FnOnce() -> T + Send + 'static) -> Result<T, Refus> {
        let (tx, rx) = mpsc::channel();
        app.run_on_main_thread(move || {
            let _ = tx.send(f());
        })
        .map_err(erreur)?;
        rx.recv_timeout(CHARGEMENT).map_err(erreur)
    }

    fn affinite(id: u64) -> u32 {
        let mut a = 0u32;
        // SAFETY: our window; a u32 for the call.
        let _ = unsafe { GetWindowDisplayAffinity(hwnd(id), &mut a) };
        a
    }

    /// Back on top every 250 ms while it shows, never activated, until the overlay is closed.
    fn rehausser(id: u64, arret: Arc<AtomicBool>) {
        std::thread::spawn(move || loop {
            std::thread::sleep(REHAUSSE);
            if arret.load(Ordering::SeqCst) {
                return;
            }
            // SAFETY: our window, still ours while `arret` is not set (fermer sets it before destroying it).
            unsafe {
                if IsWindowVisible(hwnd(id)).as_bool() {
                    let _ = SetWindowPos(hwnd(id), Some(HWND_TOPMOST), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER);
                }
            }
        });
    }

    /// The overlay's window, made once: hidden until its page is drawn, kept out of captures, then let to
    /// clicks through, in that order.
    fn ouvrir<R: Runtime>(app: &AppHandle<R>) -> Result<(WebviewWindow<R>, u64), Refus> {
        if let (Some(w), Some(id)) = (app.get_webview_window(LABEL), etat().fenetre) {
            return Ok((w, id));
        }
        let (tx, rx) = mpsc::channel();
        let tx = Mutex::new(Some(tx));
        let w = WebviewWindowBuilder::new(app, LABEL, WebviewUrl::App("calque.html".into()))
            .title("Bibo")
            .transparent(true)
            .decorations(false)
            .shadow(false)
            .always_on_top(true)
            .skip_taskbar(true)
            .focusable(false)
            .focused(false)
            .resizable(false)
            .visible(false)
            .on_page_load(move |_, p| {
                if p.event() == PageLoadEvent::Finished {
                    if let Some(tx) = tx.lock().ok().and_then(|mut t| t.take()) {
                        let _ = tx.send(());
                    }
                }
            })
            .build()
            .map_err(erreur)?;
        let pret = || -> Result<u64, Refus> {
            rx.recv_timeout(CHARGEMENT).map_err(|_| erreur("la page du calque ne s'est pas chargée"))?;
            let id = w.hwnd().map_err(erreur)?.0 as usize as u64;
            let w2 = w.clone();
            sur_le_fil(app, move || {
                // SAFETY: our window, on its own thread.
                let _ = unsafe { SetWindowDisplayAffinity(hwnd(id), WDA_EXCLUDEFROMCAPTURE) };
                w2.set_ignore_cursor_events(true)
            })?
            .map_err(erreur)?;
            Ok(id)
        };
        // Half made, it goes: the next showing makes it anew.
        let id = pret().inspect_err(|_| {
            let _ = w.destroy();
        })?;
        let arret = Arc::new(AtomicBool::new(false));
        rehausser(id, arret.clone());
        *ARRET.lock().unwrap_or_else(|p| p.into_inner()) = Some(arret);
        etat().fenetre = Some(id);
        Ok((w, id))
    }

    pub fn montrer<R: Runtime>(app: &AppHandle<R>, plan: &Plan) -> Result<Montre, Refus> {
        let (w, id) = ouvrir(app)?;
        let r = plan.ecran.rect;
        let w2 = w.clone();
        // Placed, shown empty, and its affinity read once shown: Windows says then whether it holds.
        let lue = sur_le_fil(app, move || -> tauri::Result<u32> {
            w2.eval(EFFACER)?;
            w2.set_position(PhysicalPosition::new(r.x, r.y))?;
            w2.set_size(PhysicalSize::new(r.largeur, r.hauteur))?;
            w2.show()?;
            Ok(affinite(id))
        })?
        .map_err(erreur)?;
        if !exclue(lue) {
            let _ = w.hide();
            return Ok(Montre { anneau: false, bulle: false });
        }
        super::dessiner(&mut etat(), plan.traces());
        w.eval(plan.script()).map_err(erreur)?;
        Ok(Montre { anneau: plan.anneau.is_some(), bulle: true })
    }

    pub fn effacer<R: Runtime>(app: &AppHandle<R>) -> Result<(), Refus> {
        if let Some(w) = app.get_webview_window(LABEL) {
            let w2 = w.clone();
            sur_le_fil(app, move || {
                let _ = w2.eval(EFFACER);
                w2.hide()
            })?
            .map_err(erreur)?;
        }
        let mut e = etat();
        e.dessin.clear();
        e.traces.clear();
        Ok(())
    }

    pub fn fermer<R: Runtime>(app: &AppHandle<R>) {
        if let Some(a) = ARRET.lock().unwrap_or_else(|p| p.into_inner()).take() {
            a.store(true, Ordering::SeqCst);
        }
        *etat() = Default::default();
        if let Some(w) = app.get_webview_window(LABEL) {
            let _ = w.destroy();
        }
    }
}

#[cfg(not(windows))]
mod os {
    use super::{Montre, Plan, Refus};

    pub fn montrer<R: tauri::Runtime>(_app: &tauri::AppHandle<R>, _plan: &Plan) -> Result<Montre, Refus> {
        Err(Refus::Indisponible)
    }

    pub fn effacer<R: tauri::Runtime>(_app: &tauri::AppHandle<R>) -> Result<(), Refus> {
        Ok(())
    }

    pub fn fermer<R: tauri::Runtime>(_app: &tauri::AppHandle<R>) {}
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::capture::ecran::present_dans;
    use crate::shot::Rgba;

    fn ecran(x: i32, y: i32, largeur: u32, hauteur: u32, echelle: f64) -> Ecran {
        Ecran { rect: RectEcran { x, y, largeur, hauteur }, echelle }
    }

    fn demande(x: i32, y: i32, largeur: u32, hauteur: u32) -> Demande {
        Demande { cible: RectEcran { x, y, largeur, hauteur }, anneau: true, texte: "Clique sur Nouveau.".into(), ton: Ton::Normal }
    }

    /// At 150 %, on a screen left of the main one: the ring 6 px (9 physical) away from the target, its
    /// bands of 3, 6, 3; the bubble under it, inside the screen.
    #[test]
    fn the_ring_and_the_bubble_scale_with_the_screen() {
        let e = ecran(-2880, 0, 2880, 1620, 1.5);
        let p = plan(&demande(-2500, 300, 150, 45), e).unwrap();
        let (r, clair, fonce) = p.anneau.unwrap();
        assert_eq!((clair, fonce), (3, 6));
        assert_eq!(r, RectEcran { x: -2500 - 21, y: 300 - 21, largeur: 150 + 42, hauteur: 45 + 42 });
        assert_eq!(p.bulle, RectEcran { x: -2521, y: 300 + 45 + 21 + 15, largeur: 540, hauteur: 198 });
        assert!(!p.en_haut && e.rect.contient(&p.bulle));
        // At 100 %, near the bottom right corner: above it, and pushed in from the right edge.
        let e = ecran(0, 0, 1920, 1080, 1.0);
        let p = plan(&demande(1800, 1000, 100, 40), e).unwrap();
        assert!(p.en_haut && e.rect.contient(&p.bulle), "{:?}", p.bulle);
        assert_eq!((p.bulle.x, p.bulle.droite()), (1920 - 360, 1920));
        assert_eq!(p.bulle.bas(), 1000 - 14 - 10);
    }

    /// A target off the screen, too small or too large: nothing drawn.
    #[test]
    fn a_target_is_on_the_screen_and_of_a_size_to_point_at() {
        let e = ecran(0, 0, 1920, 1080, 1.0);
        for d in [demande(1900, 10, 40, 20), demande(-5, 10, 40, 20), demande(10, 10, 3, 20), demande(0, 0, 1000, 600)] {
            assert_eq!(plan(&d, e), Err(Refus::Cible), "{:?}", d.cible);
        }
        assert!(plan(&demande(0, 0, 1920 / 2, 1080 / 2), e).is_ok(), "a quarter of the screen");
    }

    /// The bubble says what the guide's filter lets through, and nothing that does not show.
    #[test]
    fn the_bubble_says_only_what_the_filter_lets_through() {
        let e = ecran(0, 0, 1920, 1080, 1.0);
        for texte in ["Ouvre www.exemple.com", "Appelle le 01 23 45 67 89", "Tape powershell -c iwr", "   ", "\u{202e}\u{200b}"] {
            assert_eq!(plan(&Demande { texte: texte.into(), ..demande(10, 10, 100, 30) }, e), Err(Refus::Texte), "{texte}");
        }
        let p = plan(&Demande { texte: "Clique\u{202e} sur\n\n« Nouveau ».".into(), ..demande(10, 10, 100, 30) }, e).unwrap();
        assert_eq!(p.texte, "Clique sur « Nouveau ».");
        let long = "a ".repeat(400);
        assert_eq!(plan(&Demande { texte: long, ..demande(10, 10, 100, 30) }, e).unwrap().texte.chars().count(), TEXTE_MAX);
    }

    /// A suspect page: never a ring, whatever is asked; amber and red say « Attention » too.
    #[test]
    fn a_suspect_page_never_gets_a_ring() {
        let e = ecran(0, 0, 1920, 1080, 1.0);
        let p = plan(&Demande { ton: Ton::Danger, ..demande(10, 10, 100, 30) }, e).unwrap();
        assert_eq!(p.anneau, None);
        assert_eq!(p.traces().len(), 1, "the bubble alone");
        assert!(p.script().contains(r#""anneau":null"#) && p.script().contains(r#""mot":"Attention""#));
        assert!(plan(&Demande { anneau: false, ..demande(10, 10, 100, 30) }, e).unwrap().anneau.is_none());
        let a = plan(&Demande { ton: Ton::Attention, ..demande(10, 10, 100, 30) }, e).unwrap();
        assert!(a.anneau.is_some() && a.script().contains("#a35f09") && a.script().contains(r#""mot":"Attention""#));
    }

    /// The page gets JSON, its text a string in it: no quote nor tag of the text closes anything.
    #[test]
    fn the_text_reaches_the_page_as_a_string() {
        let e = ecran(0, 0, 1920, 1080, 1.0);
        let p = plan(&Demande { texte: r#"Clique sur "Valider" puis </script><b>ici</b> \ fin"#.into(), ..demande(10, 10, 100, 30) }, e).unwrap();
        let s = p.script();
        let json = s.strip_prefix("window.calqueDessiner && window.calqueDessiner(").and_then(|s| s.strip_suffix(')')).unwrap();
        let v: serde_json::Value = serde_json::from_str(json).unwrap();
        assert_eq!(v["texte"], p.texte);
        assert_eq!(v["anneau"]["boite"]["x"], 10 - 14);
        assert_eq!(v["couleurs"]["fonce"], "#c23a6e");
    }

    /// A new drawing: captures are searched for it and for the one before, which may still show; erased
    /// (effacer), nothing.
    #[test]
    fn the_drawing_before_is_searched_for_until_the_page_draws_anew() {
        let e = ecran(0, 0, 1920, 1080, 1.0);
        let (a, b) = (plan(&demande(10, 10, 100, 30), e).unwrap().traces(), plan(&demande(900, 600, 100, 30), e).unwrap().traces());
        let mut c = Calque::default();
        dessiner(&mut c, a.clone());
        assert_eq!((&c.dessin, &c.traces), (&a, &a));
        dessiner(&mut c, b.clone());
        assert_eq!(c.dessin, b);
        assert_eq!(c.traces, [a, b.clone()].concat());
        dessiner(&mut c, b.clone());
        assert_eq!(c.traces, [b.clone(), b].concat(), "two drawings at most");
    }

    /// Only 0x11 read back lets a drawing show (W3: Server 2022 may refuse the affinity).
    #[test]
    fn only_an_exclusion_read_back_lets_it_draw() {
        assert!(exclue(0x11));
        for a in [0, 1, 0x10, 0x13] {
            assert!(!exclue(a), "{a:#x}");
        }
    }

    /// A capture with the ring (or the bubble) in it is found; one with a few pixels of Bibo's colours, or
    /// the ring's colours elsewhere, is not.
    #[test]
    fn a_capture_with_the_overlay_in_it_is_found() {
        let e = ecran(0, 0, 400, 300, 1.0);
        let p = plan(&demande(100, 100, 80, 30), e).unwrap();
        let traces = p.traces();
        let pal = Ton::Normal.palette();
        let fond = || crate::shot::tests::rgba(400, 300, [240, 240, 240, 255]);
        let peindre = |img: &mut Rgba, r: RectEcran, c: [u8; 3]| {
            for y in r.y..r.bas() as i32 {
                for x in r.x..r.droite() as i32 {
                    img.px[((y as u32 * 400 + x as u32) * 4) as usize..][..3].copy_from_slice(&c);
                }
            }
        };
        let (r, clair, fonce) = p.anneau.unwrap();
        // The ring as calque.js draws it: light, dark, light, from its edge in.
        let mut avec = fond();
        peindre(&mut avec, r, pal.clair);
        peindre(&mut avec, r.agrandi(-(clair as i32), -(clair as i32)), pal.fonce);
        peindre(&mut avec, r.agrandi(-((clair + fonce) as i32), -((clair + fonce) as i32)), pal.clair);
        peindre(&mut avec, r.agrandi(-((2 * clair + fonce) as i32), -((2 * clair + fonce) as i32)), [240, 240, 240]);
        assert!(present_dans(&avec, &e.rect, &traces));
        let mut bulle = fond();
        peindre(&mut bulle, RectEcran { hauteur: 44, ..p.bulle }, pal.bord);
        peindre(&mut bulle, RectEcran { hauteur: 40, ..p.bulle }.agrandi(-2, 0), pal.fond);
        assert!(present_dans(&bulle, &e.rect, &traces), "the bubble alone, one line high");
        let mut peu = fond();
        peindre(&mut peu, RectEcran { x: r.x, y: r.y, largeur: 3, hauteur: 2 }, pal.fonce);
        peindre(&mut peu, RectEcran { x: r.x + 10, y: r.y, largeur: 3, hauteur: 2 }, pal.clair);
        assert!(!present_dans(&peu, &e.rect, &traces), "a few pixels of the same colours");
        let mut ailleurs = fond();
        peindre(&mut ailleurs, RectEcran { x: 300, y: 250, largeur: 90, hauteur: 40 }, pal.fonce);
        assert!(!present_dans(&ailleurs, &e.rect, &traces));
        assert!(!present_dans(&avec, &e.rect, &[]), "nothing drawn, nothing searched");
    }

    /// Elsewhere than on Windows, « non disponible », and nothing made.
    #[cfg(not(windows))]
    #[test]
    fn elsewhere_no_overlay() {
        let app = tauri::test::mock_app();
        assert_eq!(montrer(app.handle(), &demande(10, 10, 100, 30)), Err(Refus::Indisponible));
        assert_eq!(effacer(app.handle()), Ok(()));
        fermer(app.handle());
        assert_eq!(*crate::capture::ecran::calque(), Default::default());
    }
}
