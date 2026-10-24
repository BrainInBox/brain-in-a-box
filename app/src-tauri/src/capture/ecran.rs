//! The guide v2's capture (spec guide v2, §3 and §11 D2 (b)): the whole screen the active window is on,
//! read by BitBlt, the only way the overlay's exclusion was measured (§10, W3). What never leaves is
//! blacked by Bibo itself before anything else is made of the image: its own windows (the pastille stays
//! on the screen, for whoever helps on Teams: R2) and the password fields. Nothing is read while the
//! screen is locked or on the secure desktop (UAC), and an image with a pixel of Bibo's overlay in it is
//! never sent. Windows only for now; elsewhere, « non disponible ».
#![cfg_attr(not(all(test, windows)), allow(dead_code, reason = "the guide v2's capture, wired into the guide with P3 (guide.rs and guide.js change in #76 first): its desk tests use it until then"))]

use std::sync::{Mutex, MutexGuard};

use serde::{Deserialize, Serialize};

use crate::shot::{self, Rgb, Rgba, Zone};

/// A rectangle on the screens, in physical pixels; a screen left of or above the main one has x or y < 0.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
pub struct RectEcran {
    pub x: i32,
    pub y: i32,
    pub largeur: u32,
    pub hauteur: u32,
}

impl RectEcran {
    pub fn droite(&self) -> i64 {
        i64::from(self.x) + i64::from(self.largeur)
    }

    pub fn bas(&self) -> i64 {
        i64::from(self.y) + i64::from(self.hauteur)
    }

    pub fn aire(&self) -> u64 {
        u64::from(self.largeur) * u64::from(self.hauteur)
    }

    fn de_bords(x0: i64, y0: i64, x1: i64, y1: i64) -> Option<RectEcran> {
        let fit = |v: i64| i32::try_from(v).ok();
        (x1 > x0 && y1 > y0).then_some(())?;
        Some(RectEcran { x: fit(x0)?, y: fit(y0)?, largeur: u32::try_from(x1 - x0).ok()?, hauteur: u32::try_from(y1 - y0).ok()? })
    }

    pub fn inter(&self, o: &RectEcran) -> Option<RectEcran> {
        let (x0, y0) = (i64::from(self.x).max(i64::from(o.x)), i64::from(self.y).max(i64::from(o.y)));
        RectEcran::de_bords(x0, y0, self.droite().min(o.droite()), self.bas().min(o.bas()))
    }

    /// `d` more on each side (less when negative), never upside down.
    pub fn agrandi(&self, dx: i32, dy: i32) -> RectEcran {
        let (dx, dy) = (i64::from(dx), i64::from(dy));
        let (x0, y0) = (i64::from(self.x) - dx, i64::from(self.y) - dy);
        let (x1, y1) = ((self.droite() + dx).max(x0 + 1), (self.bas() + dy).max(y0 + 1));
        RectEcran::de_bords(x0, y0, x1, y1).unwrap_or(*self)
    }

    pub fn contient(&self, o: &RectEcran) -> bool {
        o.x >= self.x && o.y >= self.y && o.droite() <= self.droite() && o.bas() <= self.bas()
    }

    /// Where it falls on an image of `ecran`, cut to it.
    pub fn sur(&self, ecran: &RectEcran) -> Option<Zone> {
        let r = self.inter(ecran)?;
        Some(Zone { x: (r.x - ecran.x) as u32, y: (r.y - ecran.y) as u32, largeur: r.largeur, hauteur: r.hauteur })
    }
}

/// One screen: where it lies among the others, and its scale (1.0 at 100 %, 1.5 at 150 %).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Ecran {
    pub rect: RectEcran,
    pub echelle: f64,
}

/// The screen as it will leave: one pixel of the image per physical pixel of the screen.
pub struct Capture {
    pub image: Rgba,
    pub ecran: Ecran,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Refus {
    /// Not on this system (yet).
    Indisponible,
    /// The screen is locked, or Windows shows its secure desktop (UAC): nothing is read.
    Pause,
    /// BitBlt failed, or gave a black screen.
    Noire,
    /// Bibo's ring or bubble is in the image: Windows did not keep its overlay out of it.
    Calque,
}

/// Black past this share: a locked screen, a session gone, nothing to guide in.
const NOIRE: f32 = 0.99;
/// Windows only, for now (P6: macOS, then X11).
pub const DISPONIBLE: bool = cfg!(windows);

/// The password fields on a screen, in its physical pixels: Bibo blacks them before anything is made of
/// the image. Here the classic ones (`ChampsClassiques`); UI Automation's IsPassword (guide v2, P2a) gives
/// the others (a web page's, WPF's).
pub trait MotsDePasse: Send + Sync {
    fn champs(&self, ecran: &Ecran) -> Vec<RectEcran>;
}

/// Edit controls marked ES_PASSWORD: Win32, WinForms, Delphi (its PasswordChar sets the style), read from
/// the system without a message to their program.
pub struct ChampsClassiques;

impl MotsDePasse for ChampsClassiques {
    fn champs(&self, ecran: &Ecran) -> Vec<RectEcran> {
        os::champs_classiques(ecran)
    }
}

/// One zone Bibo's overlay draws, and its two colours: what every capture is searched for.
#[derive(Clone, Debug, PartialEq)]
pub struct Trace {
    pub zone: RectEcran,
    pub couleurs: [[u8; 3]; 2],
}

/// Bibo's overlay now, as calque.rs keeps it: its window (left out of Bibo's windows to black: it is kept
/// out of captures, and blacking it would black the whole screen), what its page was last asked to draw,
/// and what captures are searched for: that, and what it drew before until it is erased (its page draws
/// a moment after it is asked).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Calque {
    pub fenetre: Option<u64>,
    pub dessin: Vec<Trace>,
    pub traces: Vec<Trace>,
}

static CALQUE: Mutex<Calque> = Mutex::new(Calque { fenetre: None, dessin: Vec::new(), traces: Vec::new() });

pub fn calque() -> MutexGuard<'static, Calque> {
    CALQUE.lock().unwrap_or_else(|p| p.into_inner())
}

/// A capture with both colours of one of the overlay's zones on this share of it has the overlay in it.
const TRACE_MIN: f64 = 0.02;

/// Whether the overlay is in an image of `ecran`: both colours of one of its zones on 2 % of it, or more.
/// A program painted in Bibo's own two colours exactly, around the very target, would be refused too: no
/// image leaves then, which is the safe way to be wrong.
pub fn present_dans(img: &Rgba, ecran: &RectEcran, traces: &[Trace]) -> bool {
    traces.iter().any(|t| {
        let Some(z) = t.zone.sur(ecran) else { return false };
        let (x1, y1) = ((z.x + z.largeur).min(img.w), (z.y + z.hauteur).min(img.h));
        let mut n = [0u64; 2];
        for y in z.y..y1 {
            let row = &img.px[(y * img.w + z.x) as usize * 4..(y * img.w + x1) as usize * 4];
            for p in row.chunks_exact(4) {
                for (k, c) in t.couleurs.iter().enumerate() {
                    if (0..3).all(|i| p[i].abs_diff(c[i]) <= 3) {
                        n[k] += 1;
                    }
                }
            }
        }
        let aire = (u64::from(x1.saturating_sub(z.x)) * u64::from(y1.saturating_sub(z.y))).max(1) as f64;
        n.iter().all(|&k| k as f64 / aire >= TRACE_MIN)
    })
}

/// The zones painted black, opaque, on an image of `ecran`.
pub fn noircir(img: &mut Rgba, ecran: &RectEcran, zones: &[RectEcran]) {
    let zones: Vec<Zone> = zones.iter().filter_map(|z| z.sur(ecran)).collect();
    shot::paint(img, &zones);
}

/// What is sent: 1 280 pixels on the longer side at most (shot.rs, as in the v1).
pub fn a_envoyer(c: &Capture) -> Rgb {
    shot::reduce(&c.image)
}

/// A zone on the image sent (`envoye`: its size, band left out) as pixels of the screen it was taken on:
/// every physical pixel it touches, whatever the screen's scale.
pub fn vers_ecran(z: Zone, envoye: (u32, u32), ecran: &Ecran) -> RectEcran {
    let c = shot::to_capture(z, envoye, (ecran.rect.largeur, ecran.rect.hauteur));
    RectEcran { x: ecran.rect.x + c.x as i32, y: ecran.rect.y + c.y as i32, largeur: c.largeur, hauteur: c.hauteur }
}

/// The input desktop by its name: Windows' own, « Default », is the only one Bibo reads; the lock screen
/// and UAC's prompt are « Winlogon », and a desktop Bibo may not open is no better.
fn bureau_ouvert_si(nom: Option<&str>) -> bool {
    nom.is_some_and(|n| n.eq_ignore_ascii_case("Default"))
}

/// The screen the user works on: the one the active window is on (Bibo's own windows left aside).
pub fn actif() -> Option<Ecran> {
    os::actif()
}

/// The screen a rectangle lies on (its larger part); None when it lies on none.
pub fn de_rect(r: &RectEcran) -> Option<Ecran> {
    os::de_rect(r)
}

/// False while the screen is locked or on the secure desktop: no capture then.
pub fn bureau_ouvert() -> bool {
    os::bureau_ouvert()
}

/// How long since the last key or mouse input anywhere in the session (GetLastInputInfo, no hook).
pub fn inactif() -> std::time::Duration {
    os::inactif()
}

/// The screen now, its password fields and Bibo's windows black. Refused while it is locked (checked
/// again once read: a screen locked meanwhile gives nothing), black, or with Bibo's overlay in it.
pub fn capturer(ecran: &Ecran, mots_de_passe: &dyn MotsDePasse) -> Result<Capture, Refus> {
    if !DISPONIBLE {
        return Err(Refus::Indisponible);
    }
    if !os::bureau_ouvert() {
        return Err(Refus::Pause);
    }
    let brute = os::lire(&ecran.rect)?;
    if !os::bureau_ouvert() {
        return Err(Refus::Pause);
    }
    if shot::black_share(&brute) >= NOIRE {
        return Err(Refus::Noire);
    }
    let traces = calque().traces.clone();
    if present_dans(&brute, &ecran.rect, &traces) {
        return Err(Refus::Calque);
    }
    let mut image = brute;
    noircir(&mut image, &ecran.rect, &os::fenetres_de_bibo(ecran));
    noircir(&mut image, &ecran.rect, &mots_de_passe.champs(ecran));
    Ok(Capture { image, ecran: *ecran })
}

/// The screen as Teams or a screenshot tool reads it, nothing blacked: the desk tests' witness.
#[cfg(all(test, windows))]
pub(super) fn lire_brut(r: &RectEcran) -> Result<Rgba, Refus> {
    os::lire(r)
}

/// Windows that are not the user's work, for the change detection (veille.rs) to look past: the taskbar,
/// its clock, the notifications, Start.
pub fn zones_du_systeme(ecran: &Ecran) -> Vec<RectEcran> {
    os::zones_du_systeme(ecran)
}

#[cfg(windows)]
mod os {
    use std::mem::size_of;
    use std::time::Duration;

    use windows::core::BOOL;
    use windows::Win32::Foundation::{HANDLE, HWND, LPARAM, POINT, RECT};
    use windows::Win32::Graphics::Gdi::{
        BitBlt, GetDC, GetMonitorInfoW, MonitorFromPoint, MonitorFromRect, MonitorFromWindow, ReleaseDC, CAPTUREBLT, HMONITOR, MONITORINFO, MONITOR_DEFAULTTONEAREST,
        MONITOR_DEFAULTTONULL, MONITOR_DEFAULTTOPRIMARY, SRCCOPY,
    };
    use windows::Win32::System::StationsAndDesktops::{CloseDesktop, GetUserObjectInformationW, OpenInputDesktop, DESKTOP_CONTROL_FLAGS, DESKTOP_READOBJECTS, UOI_NAME};
    use windows::Win32::System::SystemInformation::GetTickCount;
    use windows::Win32::UI::HiDpi::{GetDpiForMonitor, MDT_EFFECTIVE_DPI};
    use windows::Win32::UI::Input::KeyboardAndMouse::{GetLastInputInfo, LASTINPUTINFO};
    use windows::Win32::UI::WindowsAndMessaging::{
        EnumChildWindows, EnumWindows, GetForegroundWindow, GetWindowDisplayAffinity, GetWindowLongW, GetWindowRect, ES_PASSWORD, GWL_STYLE,
        WDA_EXCLUDEFROMCAPTURE,
    };

    use super::super::win::{class, cloaked, frame, id_of, minimized, owner, pid_of, visible, Canvas, Physical, SHELL};
    use super::{Ecran, RectEcran, Refus};
    use crate::shot::{Rgba, MAX_SIDE};

    /// The shell's own windows: the taskbars (and their clock), the notifications, Start and its search.
    const SYSTEME: &[&str] = &["Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow", "NotifyIconOverflowWindow", "Xaml_WindowedPopupClass"];

    fn rect_de(r: RECT) -> Option<RectEcran> {
        RectEcran::de_bords(i64::from(r.left), i64::from(r.top), i64::from(r.right), i64::from(r.bottom))
    }

    fn top_windows() -> Vec<HWND> {
        unsafe extern "system" fn each(h: HWND, all: LPARAM) -> BOOL {
            // SAFETY: the Vec lent to EnumWindows below, alive for its whole call.
            unsafe { (*(all.0 as *mut Vec<HWND>)).push(h) };
            BOOL(1)
        }
        let mut all: Vec<HWND> = Vec::new();
        // SAFETY: `each` only pushes to `all`, which outlives the call.
        let _ = unsafe { EnumWindows(Some(each), LPARAM(&mut all as *mut Vec<HWND> as isize)) };
        all
    }

    fn children(parent: HWND) -> Vec<HWND> {
        unsafe extern "system" fn each(h: HWND, all: LPARAM) -> BOOL {
            // SAFETY: as above.
            unsafe { (*(all.0 as *mut Vec<HWND>)).push(h) };
            BOOL(1)
        }
        let mut all: Vec<HWND> = Vec::new();
        // SAFETY: as above; every descendant, not only the first level.
        let _ = unsafe { EnumChildWindows(Some(parent), Some(each), LPARAM(&mut all as *mut Vec<HWND> as isize)) };
        all
    }

    fn shown(h: HWND) -> bool {
        visible(h) && !cloaked(h) && !minimized(h)
    }

    fn window_rect(h: HWND) -> Option<RectEcran> {
        let mut r = RECT::default();
        // SAFETY: a RECT for the call.
        unsafe { GetWindowRect(h, &mut r) }.ok()?;
        rect_de(r)
    }

    fn ecran_de(m: HMONITOR) -> Option<Ecran> {
        let mut info = MONITORINFO { cbSize: size_of::<MONITORINFO>() as u32, ..Default::default() };
        // SAFETY: a MONITORINFO of its size.
        unsafe { GetMonitorInfoW(m, &mut info) }.ok().ok()?;
        let (mut x, mut y) = (96u32, 96u32);
        // SAFETY: plain values.
        let dpi = if unsafe { GetDpiForMonitor(m, MDT_EFFECTIVE_DPI, &mut x, &mut y) }.is_ok() { x } else { 96 };
        Some(Ecran { rect: rect_de(info.rcMonitor)?, echelle: f64::from(dpi.max(1)) / 96.0 })
    }

    /// One of Bibo's windows, or one hung on them (a web view's popup).
    fn de_bibo(h: HWND) -> bool {
        let me = std::process::id();
        let mut w = Some(h);
        for _ in 0..8 {
            match w {
                Some(x) if pid_of(x) == me => return true,
                Some(x) => w = owner(x),
                None => return false,
            }
        }
        false
    }

    pub fn actif() -> Option<Ecran> {
        let _p = Physical::enter();
        // SAFETY: no argument.
        let devant = unsafe { GetForegroundWindow() };
        let h = if !devant.is_invalid() && !de_bibo(devant) {
            Some(devant)
        } else {
            top_windows().into_iter().find(|&h| shown(h) && !de_bibo(h) && !SHELL.contains(&class(h).as_str()) && frame(h).is_some())
        };
        // SAFETY: plain values.
        let m = match h {
            Some(h) => unsafe { MonitorFromWindow(h, MONITOR_DEFAULTTONEAREST) },
            None => unsafe { MonitorFromPoint(POINT::default(), MONITOR_DEFAULTTOPRIMARY) },
        };
        ecran_de(m)
    }

    pub fn de_rect(r: &RectEcran) -> Option<Ecran> {
        let _p = Physical::enter();
        let rc = RECT { left: r.x, top: r.y, right: r.droite() as i32, bottom: r.bas() as i32 };
        // SAFETY: a RECT for the call.
        let m = unsafe { MonitorFromRect(&rc, MONITOR_DEFAULTTONULL) };
        (!m.is_invalid()).then_some(())?;
        ecran_de(m)
    }

    pub fn bureau_ouvert() -> bool {
        // SAFETY: the desktop handle, closed below; the buffer's length in bytes goes with it.
        unsafe {
            let Ok(d) = OpenInputDesktop(DESKTOP_CONTROL_FLAGS(0), false, DESKTOP_READOBJECTS) else { return false };
            let mut buf = [0u16; 64];
            let mut needed = 0u32;
            let read = GetUserObjectInformationW(HANDLE(d.0), UOI_NAME, Some(buf.as_mut_ptr().cast()), (buf.len() * 2) as u32, Some(&mut needed)).is_ok();
            let _ = CloseDesktop(d);
            let n = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
            super::bureau_ouvert_si(read.then(|| String::from_utf16_lossy(&buf[..n])).as_deref())
        }
    }

    pub fn inactif() -> Duration {
        let mut info = LASTINPUTINFO { cbSize: size_of::<LASTINPUTINFO>() as u32, dwTime: 0 };
        // SAFETY: a struct of its size.
        if !unsafe { GetLastInputInfo(&mut info) }.as_bool() {
            return Duration::ZERO;
        }
        // SAFETY: no argument; both counters wrap after 49 days, together.
        let now = unsafe { GetTickCount() };
        Duration::from_millis(u64::from(now.wrapping_sub(info.dwTime)))
    }

    /// The screen's pixels as Teams or a screenshot tool reads them (the overlay kept out by Windows).
    pub fn lire(r: &RectEcran) -> Result<Rgba, Refus> {
        let _p = Physical::enter();
        let (w, h) = (r.largeur, r.hauteur);
        if w == 0 || h == 0 || w > MAX_SIDE || h > MAX_SIDE {
            return Err(Refus::Noire);
        }
        let canvas = Canvas::new(w as i32, h as i32).ok_or(Refus::Noire)?;
        // SAFETY: the screen's DC, released below; our canvas, of that size.
        let copie = unsafe {
            let screen = GetDC(None);
            let ok = BitBlt(canvas.dc, 0, 0, w as i32, h as i32, Some(screen), r.x, r.y, SRCCOPY | CAPTUREBLT);
            ReleaseDC(None, screen);
            ok
        };
        copie.map_err(|_| Refus::Noire)?;
        Ok(super::super::from_bgra(canvas.pixels(), w, h, (0, 0, w as i32, h as i32)))
    }

    fn exclue(h: HWND) -> bool {
        let mut a = 0u32;
        // SAFETY: a u32 for the call.
        unsafe { GetWindowDisplayAffinity(h, &mut a) }.is_ok() && a == WDA_EXCLUDEFROMCAPTURE.0
    }

    /// Bibo's windows on that screen, the overlay aside: it is kept out of captures, and blacking it would
    /// black the whole screen.
    pub fn fenetres_de_bibo(ecran: &Ecran) -> Vec<RectEcran> {
        let _p = Physical::enter();
        let calque = super::calque().fenetre;
        top_windows()
            .into_iter()
            .filter(|&h| Some(id_of(h)) != calque && shown(h) && de_bibo(h) && !exclue(h))
            .filter_map(|h| frame(h).and_then(rect_de).or_else(|| window_rect(h)))
            .filter_map(|r| r.inter(&ecran.rect))
            .collect()
    }

    pub fn champs_classiques(ecran: &Ecran) -> Vec<RectEcran> {
        let _p = Physical::enter();
        let mut out = Vec::new();
        for top in top_windows().into_iter().filter(|&h| shown(h) && window_rect(h).is_some_and(|r| r.inter(&ecran.rect).is_some())) {
            for c in children(top) {
                // SAFETY: any value is accepted; read from the system, no message to the window.
                let style = unsafe { GetWindowLongW(c, GWL_STYLE) };
                if visible(c) && style & ES_PASSWORD != 0 && class(c).to_ascii_lowercase().contains("edit") {
                    out.extend(window_rect(c).and_then(|r| r.inter(&ecran.rect)));
                }
            }
        }
        out
    }

    pub fn zones_du_systeme(ecran: &Ecran) -> Vec<RectEcran> {
        let _p = Physical::enter();
        top_windows()
            .into_iter()
            .filter(|&h| visible(h) && !cloaked(h) && SYSTEME.contains(&class(h).as_str()))
            .filter_map(|h| window_rect(h).and_then(|r| r.inter(&ecran.rect)))
            .collect()
    }
}

#[cfg(not(windows))]
mod os {
    use super::{Ecran, RectEcran, Refus};
    use crate::shot::Rgba;
    use std::time::Duration;

    pub fn actif() -> Option<Ecran> {
        None
    }

    pub fn de_rect(_r: &RectEcran) -> Option<Ecran> {
        None
    }

    pub fn bureau_ouvert() -> bool {
        false
    }

    pub fn inactif() -> Duration {
        Duration::ZERO
    }

    pub fn lire(_r: &RectEcran) -> Result<Rgba, Refus> {
        Err(Refus::Indisponible)
    }

    pub fn fenetres_de_bibo(_e: &Ecran) -> Vec<RectEcran> {
        Vec::new()
    }

    pub fn champs_classiques(_e: &Ecran) -> Vec<RectEcran> {
        Vec::new()
    }

    pub fn zones_du_systeme(_e: &Ecran) -> Vec<RectEcran> {
        Vec::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn r(x: i32, y: i32, largeur: u32, hauteur: u32) -> RectEcran {
        RectEcran { x, y, largeur, hauteur }
    }

    #[test]
    fn rectangles_meet_grow_and_fall_on_a_screen_left_of_the_main_one() {
        assert_eq!(r(0, 0, 100, 100).inter(&r(50, 60, 100, 100)), Some(r(50, 60, 50, 40)));
        assert_eq!(r(0, 0, 100, 100).inter(&r(100, 0, 10, 10)), None);
        assert_eq!(r(10, 10, 20, 20).agrandi(5, 2), r(5, 8, 30, 24));
        assert_eq!(r(10, 10, 20, 20).agrandi(-30, 0).largeur, 1, "never upside down");
        assert!(r(0, 0, 100, 100).contient(&r(10, 10, 90, 90)) && !r(0, 0, 100, 100).contient(&r(10, 10, 91, 10)));
        // A screen at the left of the main one, and a window across both.
        let gauche = r(-1920, 0, 1920, 1080);
        assert_eq!(r(-100, 50, 300, 20).sur(&gauche), Some(Zone { x: 1820, y: 50, largeur: 100, hauteur: 20 }));
        assert_eq!(r(10, 50, 300, 20).sur(&gauche), None);
    }

    /// Bibo's windows and the password fields: black on the image, whatever part of them lies on that
    /// screen, and nothing else.
    #[test]
    fn what_never_leaves_is_painted_black_on_the_screen_it_lies_on() {
        let ecran = r(-1920, 0, 1920, 1080);
        let mut img = crate::shot::tests::rgba(1920, 1080, [200, 200, 200, 255]);
        noircir(&mut img, &ecran, &[r(-2000, -10, 100, 30), r(-1000, 500, 40, 10), r(5, 5, 50, 50)]);
        let black = |x: u32, y: u32| img.px[((y * 1920 + x) * 4) as usize..][..3] == [0, 0, 0];
        assert!(black(0, 0) && black(19, 19) && !black(20, 0) && !black(0, 20), "the part on this screen only");
        assert!(black(920, 500) && black(959, 509) && !black(960, 509) && !black(920, 510));
        assert_eq!(crate::shot::black_share(&img), (20.0 * 20.0 + 40.0 * 10.0) / (1920.0 * 1080.0));
    }

    /// ≤ 1 280 px sent, and a zone of the image sent goes back to the screen's own pixels, on a screen at
    /// 150 % left of the main one: every physical pixel it touches.
    #[test]
    fn the_image_sent_is_1280_wide_at_most_and_its_zones_go_back_to_the_screen() {
        let ecran = Ecran { rect: r(-2880, 0, 2880, 1620), echelle: 1.5 };
        let c = Capture { image: crate::shot::tests::rgba(2880, 1620, [10, 20, 30, 255]), ecran };
        let sent = a_envoyer(&c);
        assert_eq!((sent.w, sent.h), (1280, 720));
        let z = vers_ecran(Zone { x: 640, y: 360, largeur: 10, hauteur: 5 }, (sent.w, sent.h), &ecran);
        assert_eq!(z, r(-2880 + 1440, 810, 23, 12));
        assert!(ecran.rect.contient(&z));
    }

    #[test]
    fn only_windows_own_desktop_is_read() {
        assert!(bureau_ouvert_si(Some("Default")) && bureau_ouvert_si(Some("default")));
        for n in [Some("Winlogon"), Some("Screen-saver"), Some(""), None] {
            assert!(!bureau_ouvert_si(n), "{n:?}");
        }
    }

    #[cfg(not(windows))]
    #[test]
    fn elsewhere_nothing_is_captured() {
        let e = Ecran { rect: r(0, 0, 10, 10), echelle: 1.0 };
        assert!(matches!(capturer(&e, &ChampsClassiques), Err(Refus::Indisponible)));
        assert_eq!(actif(), None);
        assert!(!bureau_ouvert());
    }

    /// What the guide v2 runs on the PC listens to no key nor the mouse (§3, §7: antivirus), never clicks
    /// nor types for the user, and never draws its overlay by UpdateLayeredWindow (§10 W3: Windows would let
    /// it into captures).
    #[test]
    fn no_hook_no_click_no_layered_update() {
        let code = [include_str!("ecran.rs"), include_str!("veille.rs"), include_str!("../calque.rs"), include_str!("../../../src/calque.js")].concat();
        let interdits = ["SetWindows", "HookEx", "WH_KEYBOARD", "WH_MOUSE", "RegisterRawInput", "SendInput", "mouse_event", "keybd_event", "UpdateLayeredWindow", "__TAURI__", "invoke("];
        // The code, not what its comments say.
        let lignes: Vec<&str> = code.lines().filter(|l| !l.trim_start().starts_with("//") && !l.contains("interdits")).collect();
        assert!(lignes.len() > 500, "{}", lignes.len());
        for i in interdits {
            assert!(!lignes.iter().any(|l| l.contains(i)), "{i}");
        }
    }
}
