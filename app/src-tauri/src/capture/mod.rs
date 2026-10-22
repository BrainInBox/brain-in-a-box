//! The automatic capture (docs/proposals/guide.md §7.1): the window the user chose, when they ask
//! Bibo to look, never the screen. On Windows (phase 2a) by PrintWindow alone: the window draws
//! itself into Bibo's memory, so another program's window over it never gets into the image. When it
//! cannot (a DirectX surface, a program run as administrator, a protected window), the capture is
//! black and the user pastes one instead: never a copy of the screen in its place (R20). Under X11
//! (phase 2b) by XGetImage, and only while a compositing manager keeps each window's pixels apart
//! (R21). On macOS 14 and later (phase 2b), the user picks the window in the system's own picker,
//! then one image of it is streamed, without the screen recording permission. On Wayland and macOS
//! 13, the guide is in paste mode.

use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use serde::Serialize;

use crate::shot::Rgba;

#[cfg(target_os = "macos")]
mod mac;
#[cfg(windows)]
mod win;
#[cfg(target_os = "linux")]
mod x11;

/// How long Bibo waits for the click in the user's window (R4): time to find the program in the
/// taskbar. The guide's window counts it down (the status gives it).
pub const CLIC_SECONDES: u64 = 45;
const CLIC: Duration = Duration::from_secs(CLIC_SECONDES);
const POLL: Duration = Duration::from_millis(100);
/// G2: a title is cut there (a mail's subject, a document's name: no more of it than needed).
const TITRE_MAX: usize = 60;

/// A window kept for the session. Its id may go to another program's window once it is closed, so
/// its process and its program go with it, and all three are checked before each capture (R22).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Window {
    pub id: u64,
    pub pid: u32,
    /// The executable's path (Windows), WM_CLASS, its instance and class (X11), or the bundle id the
    /// picker says (macOS 15.2 and later; empty before, and the pid 0: the system's filter is the
    /// window there, Bibo's id only its number for the session).
    pub app: String,
}

/// « Je ne trouve plus ta fenêtre » unless the window found under the id is the one chosen.
pub fn same_window(chosen: &Window, found: Option<&Window>) -> bool {
    found.is_some_and(|w| w == chosen)
}

/// Another program's window, as the system shows it now.
#[derive(Clone, Debug, PartialEq)]
pub struct Found {
    pub window: Window,
    /// The program as people call it (« Microsoft Edge »), else its executable's name.
    pub nom: String,
    pub titre: String,
    pub reduite: bool,
    pub terminal: bool,
}

/// A window as the guide's window shows it: a line of the list, or « C'est bien … ? ».
#[derive(Serialize, Clone, Debug, PartialEq)]
pub struct Fenetre {
    pub id: u64,
    pub app: String,
    /// On one line, 60 characters at most.
    pub titre: String,
    /// « (réduite : ouvre-la d'abord) ».
    pub reduite: bool,
    /// « Je ne guide pas dans un terminal » (R6).
    pub terminal: bool,
}

impl From<&Found> for Fenetre {
    fn from(f: &Found) -> Self {
        Fenetre { id: f.window.id, app: f.nom.clone(), titre: short(&f.titre), reduite: f.reduite, terminal: f.terminal }
    }
}

/// Why there is no capture.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Rate {
    /// Not on this system (Wayland, X11 without a compositing manager, macOS 13, macOS refusing): paste
    /// mode.
    #[cfg_attr(windows, allow(dead_code))]
    Indisponible,
    /// Closed, or its id now another program's window (R22).
    Introuvable,
    /// Minimized or hidden: there is nothing to draw.
    Reduite,
    /// The window would not draw itself: black, never the screen instead (R20).
    Noire,
}

/// What came of the system's own picker (macOS).
#[derive(Clone, Debug, PartialEq)]
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub enum Pick {
    Choisie(Found),
    /// The user closed it: « Tu as fermé la fenêtre de choix ».
    Fermee,
    /// macOS would not show it, for this reason (its own words).
    Refus(String),
    /// Bibo stopped waiting (« Arrêter », the guide closed), or nobody chose for too long.
    Arrete,
}

/// The system's windows, as the guide asks for them: this computer's, or a test's.
pub trait Screen {
    fn available(&self) -> bool;
    /// Why not, when not available: for the status and the tests, never shown (the window says « sur
    /// ce PC », never the name of a display server).
    fn raison(&self) -> &'static str {
        "indisponible"
    }
    /// The window is chosen in the system's own picker (macOS), not in Bibo's list nor by a click.
    fn picker(&self) -> bool {
        false
    }
    /// The system's picker, until the user chooses a window or closes it, or `cancel` is set.
    fn pick(&self, _cancel: &AtomicBool) -> Pick {
        Pick::Arrete
    }
    /// Other programs' windows, the top one first (G2's list).
    fn windows(&self) -> Vec<Found>;
    /// The window that comes to the front within 45 s, Bibo's left out (R4); None if none came or
    /// `cancel` was set.
    fn clicked(&self, cancel: &AtomicBool) -> Option<Found>;
    /// The window under this id now, if it is one the guide may capture.
    fn find(&self, id: u64) -> Option<Found>;
    /// The window, if it is still the one chosen (R22), as it draws itself, with the dialog it waits
    /// on drawn at its place (R11).
    fn grab(&self, chosen: &Window) -> Result<Rgba, Rate>;
    /// The guide closed: the window it was given forgotten, the system's picker put away (macOS: its
    /// sharing indicator goes off).
    fn forget(&self) {}
}

/// This computer's windows.
pub struct System;

impl Screen for System {
    fn available(&self) -> bool {
        os::available()
    }

    fn raison(&self) -> &'static str {
        os::raison()
    }

    #[cfg(target_os = "macos")]
    fn picker(&self) -> bool {
        os::available()
    }

    #[cfg(target_os = "macos")]
    fn pick(&self, cancel: &AtomicBool) -> Pick {
        os::pick(cancel)
    }

    #[cfg(target_os = "macos")]
    fn forget(&self) {
        os::forget()
    }

    fn windows(&self) -> Vec<Found> {
        os::list()
    }

    fn clicked(&self, cancel: &AtomicBool) -> Option<Found> {
        wait_for(&os::foreground, CLIC, POLL, cancel)
    }

    fn find(&self, id: u64) -> Option<Found> {
        os::describe(id)
    }

    fn grab(&self, chosen: &Window) -> Result<Rgba, Rate> {
        os::grab(chosen)
    }
}

#[cfg(target_os = "macos")]
use mac as os;
#[cfg(windows)]
use win as os;
#[cfg(target_os = "linux")]
use x11 as os;

#[cfg(not(any(windows, target_os = "linux", target_os = "macos")))]
mod os {
    use super::{Found, Rate, Window};
    use crate::shot::Rgba;

    pub fn available() -> bool {
        false
    }

    pub fn raison() -> &'static str {
        "indisponible"
    }

    pub fn list() -> Vec<Found> {
        Vec::new()
    }

    pub fn foreground() -> Option<Found> {
        None
    }

    pub fn describe(_id: u64) -> Option<Found> {
        None
    }

    pub fn grab(_chosen: &Window) -> Result<Rgba, Rate> {
        Err(Rate::Indisponible)
    }
}

/// G2's line: the title on one line, cut at 60 characters.
fn short(titre: &str) -> String {
    let line = titre.split_whitespace().collect::<Vec<_>>().join(" ");
    if line.chars().count() <= TITRE_MAX {
        return line;
    }
    let mut cut: String = line.chars().take(TITRE_MAX - 1).collect();
    cut.push('…');
    cut
}

/// R6: a terminal, by its program (its executable's name, a part of WM_CLASS, or its bundle id) or its
/// window's class.
fn is_terminal(app: &str, class: &str) -> bool {
    const PROGRAMS: &[&str] = &[
        "cmd",
        "conhost",
        "openconsole",
        "windowsterminal",
        "powershell",
        "powershell_ise",
        "pwsh",
        "wsl",
        "bash",
        "mintty",
        "conemu",
        "conemu64",
        "putty",
        "kitty",
        "alacritty",
        "wezterm-gui",
        "tabby",
        "hyper",
        "gnome-terminal-server",
        "gnome-terminal",
        "konsole",
        "xterm",
        "uxterm",
        "xfce4-terminal",
        "tilix",
        "terminator",
        "lxterminal",
        "com.apple.terminal",
        "com.googlecode.iterm2",
        "dev.warp.warp-stable",
        "net.kovidgoyal.kitty",
        "io.alacritty",
        "com.github.wez.wezterm",
        "co.zeit.hyper",
        "com.mitchellh.ghostty",
        "org.tabby",
    ];
    const CLASSES: &[&str] = &["consolewindowclass", "cascadia_hosting_window_class", "virtualconsoleclass", "mintty", "putty"];
    let file = app.rsplit(['\\', '/']).next().unwrap_or(app).to_lowercase();
    let program = file.strip_suffix(".exe").unwrap_or(&file);
    PROGRAMS.contains(&program) || CLASSES.contains(&class.to_lowercase().as_str())
}

/// The window that comes to the front. The one in front at the start is left out, as only a click
/// says which one the user means, until none is: Bibo's window took the focus (lib.rs gives it as the
/// wait starts), or the taskbar did. Then a click in the user's window, in front a moment ago, counts.
fn wait_for(front: &dyn Fn() -> Option<Found>, limit: Duration, step: Duration, cancel: &AtomicBool) -> Option<Found> {
    let start = Instant::now();
    let mut before = front().map(|f| f.window.id);
    loop {
        if cancel.load(Ordering::SeqCst) {
            return None;
        }
        match front() {
            Some(f) if Some(f.window.id) != before => return Some(f),
            Some(_) => {}
            None => before = None,
        }
        if start.elapsed() >= limit {
            return None;
        }
        std::thread::sleep(step);
    }
}

/// Pixels as Windows draws them (blue, green, red, unused; top row first), into RGBA, cut to `keep`
/// (left, top, right, bottom: the window's visible frame within the bitmap). Every pixel is opaque:
/// the fourth byte means nothing there, and a transparent pixel would count as black.
#[cfg_attr(not(windows), allow(dead_code))]
fn from_bgra(raw: &[u8], w: u32, h: u32, keep: (i32, i32, i32, i32)) -> Rgba {
    let clamp = |v: i32, max: u32| v.clamp(0, max as i32) as u32;
    let (mut x0, mut y0, mut x1, mut y1) = (clamp(keep.0, w), clamp(keep.1, h), clamp(keep.2, w), clamp(keep.3, h));
    if x0 >= x1 || y0 >= y1 {
        (x0, y0, x1, y1) = (0, 0, w, h);
    }
    let mut px = Vec::with_capacity((x1 - x0) as usize * (y1 - y0) as usize * 4);
    for y in y0..y1 {
        let row = &raw[(y * w + x0) as usize * 4..(y * w + x1) as usize * 4];
        for p in row.chunks_exact(4) {
            px.extend_from_slice(&[p[2], p[1], p[0], 255]);
        }
    }
    Rgba { w: x1 - x0, h: y1 - y0, px }
}

/// The dialog drawn over its window at its place (`dx`, `dy` from the window's corner), cut to the
/// window: the image keeps the window's size.
#[cfg_attr(not(any(windows, target_os = "linux")), allow(dead_code))]
fn lay(base: &mut Rgba, dialog: &Rgba, dx: i64, dy: i64) {
    let span = |d: i64, size: u32, room: u32| (d.max(0), (d + i64::from(size)).min(i64::from(room)));
    let ((x0, x1), (y0, y1)) = (span(dx, dialog.w, base.w), span(dy, dialog.h, base.h));
    if x0 >= x1 {
        return;
    }
    for y in y0..y1 {
        let to = ((y * i64::from(base.w) + x0) * 4) as usize;
        let from = (((y - dy) * i64::from(dialog.w) + (x0 - dx)) * 4) as usize;
        let len = ((x1 - x0) * 4) as usize;
        base.px[to..to + len].copy_from_slice(&dialog.px[from..from + len]);
    }
}

/// A frame as ScreenCaptureKit gives it (blue, green, red, alpha; `stride` bytes a row, padded) into
/// RGBA, its alpha kept: a window's rounded corners are transparent, put on white before it is sent.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn from_bgra_rows(raw: &[u8], w: u32, h: u32, stride: usize) -> Option<Rgba> {
    let row = w as usize * 4;
    if w == 0 || h == 0 || stride < row || raw.len() < stride * (h as usize - 1) + row {
        return None;
    }
    let mut px = Vec::with_capacity(row * h as usize);
    for y in 0..h as usize {
        for p in raw[y * stride..][..row].chunks_exact(4) {
            px.extend_from_slice(&[p[2], p[1], p[0], p[3]]);
        }
    }
    Some(Rgba { w, h, px })
}

/// Wayland: its session, or its socket in the environment. No program's window can be captured there
/// without the desktop's portal (after the v1), even through XWayland: the guide is in paste mode.
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
fn wayland(session: Option<&str>, socket: Option<&str>) -> bool {
    session.is_some_and(|s| s.eq_ignore_ascii_case("wayland")) || socket.is_some_and(|s| !s.is_empty())
}

/// An image as Xlib reads it from a window (ZPixmap).
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
struct XPixels<'a> {
    data: &'a [u8],
    w: u32,
    h: u32,
    /// Bytes from one row to the next.
    stride: usize,
    bits: u32,
    lsb_first: bool,
    /// Red, green, blue.
    masks: [u64; 3],
}

/// Into opaque RGBA: 24 or 32 bits a pixel, each colour 8 bits in a row of its mask. Any other layout
/// gives None, and the capture is black: never guessed.
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
fn from_ximage(x: &XPixels) -> Option<Rgba> {
    let bytes = match x.bits {
        24 => 3,
        32 => 4,
        _ => return None,
    };
    let shift = |m: u64| {
        let s = m.trailing_zeros();
        (m != 0 && m >> s == 0xff && s + 8 <= bytes * 8).then_some(s)
    };
    let shifts = [shift(x.masks[0])?, shift(x.masks[1])?, shift(x.masks[2])?];
    let (w, h, row) = (x.w as usize, x.h as usize, x.w as usize * bytes as usize);
    if w == 0 || h == 0 || x.stride < row || x.data.len() < x.stride * (h - 1) + row {
        return None;
    }
    let mut px = Vec::with_capacity(w * h * 4);
    for y in 0..h {
        for p in x.data[y * x.stride..][..row].chunks_exact(bytes as usize) {
            let v = p.iter().enumerate().fold(0u32, |v, (i, &b)| v | (u32::from(b) << (8 * if x.lsb_first { i } else { p.len() - 1 - i })));
            px.extend_from_slice(&[(v >> shifts[0]) as u8, (v >> shifts[1]) as u8, (v >> shifts[2]) as u8, 255]);
        }
    }
    Some(Rgba { w: x.w, h: x.h, px })
}

/// The part of a window on the screen, in the window's own pixels (left, top, right, bottom): X reads
/// none of a window off the screen. None when none of it is there.
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
fn on_screen((x, y): (i32, i32), (w, h): (i32, i32), (sw, sh): (i32, i32)) -> Option<(i32, i32, i32, i32)> {
    let (x0, y0) = (x.saturating_neg().max(0), y.saturating_neg().max(0));
    let (x1, y1) = (w.min(sw.saturating_sub(x)), h.min(sh.saturating_sub(y)));
    (x0 < x1 && y0 < y1).then_some((x0, y0, x1, y1))
}

/// WM_CLASS: its instance and its class, each ended by a NUL, in Latin-1.
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
fn wm_class(raw: &[u8]) -> (String, String) {
    let mut parts = raw.split(|&b| b == 0).map(latin1);
    (parts.next().unwrap_or_default(), parts.next().unwrap_or_default())
}

/// A property of type STRING: Latin-1, a character a byte.
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
fn latin1(raw: &[u8]) -> String {
    raw.iter().map(|&b| char::from(b)).collect()
}

/// What the desktop tests of Windows and X11 share (`bureau_*`, on a VM): programs of known colours,
/// waits, and pixels counted in memory.
#[cfg(all(test, any(windows, target_os = "linux")))]
pub(crate) mod bureau {
    use super::{Found, Screen, System};
    use crate::shot::Rgba;
    use std::process::Child;
    use std::time::{Duration, Instant};

    pub const BLUE: [u8; 3] = [0x1e, 0x88, 0xe5];
    pub const MAGENTA: [u8; 3] = [0xff, 0x00, 0xff];
    pub const ORANGE: [u8; 3] = [0xff, 0x98, 0x00];
    pub const GREY: [u8; 3] = [0x78, 0x78, 0x78];
    pub const GREEN: [u8; 3] = [0x2e, 0x7d, 0x32];
    pub const WAIT: Duration = Duration::from_secs(30);

    /// A program the test started, closed with it.
    pub struct Program(pub Child);

    impl Drop for Program {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }

    /// The window as the list shows it, once it is there.
    pub fn listed_as(which: impl Fn(&Found) -> bool) -> Found {
        let start = Instant::now();
        loop {
            if let Some(f) = System.windows().into_iter().find(&which) {
                return f;
            }
            assert!(start.elapsed() < WAIT, "never in the list: {:?}", System.windows().iter().map(|f| f.titre.clone()).collect::<Vec<_>>());
            std::thread::sleep(Duration::from_millis(200));
        }
    }

    pub fn until(what: &str, done: impl Fn() -> bool) {
        let start = Instant::now();
        while !done() {
            assert!(start.elapsed() < WAIT, "{what}: still not, after {WAIT:?}");
            std::thread::sleep(Duration::from_millis(100));
        }
    }

    /// Pixels of that colour, give or take 3 on each channel.
    pub fn count(img: &Rgba, c: [u8; 3]) -> usize {
        img.px.chunks_exact(4).filter(|p| (0..3).all(|i| p[i].abs_diff(c[i]) <= 3)).count()
    }

    pub fn pixels(img: &Rgba) -> usize {
        img.px.len() / 4
    }

    /// The smallest box around the pixels of that colour: (left, top, right, bottom), right and bottom out.
    pub fn bounds(img: &Rgba, c: [u8; 3]) -> Option<(i32, i32, i32, i32)> {
        let mut b: Option<(i32, i32, i32, i32)> = None;
        for (i, p) in img.px.chunks_exact(4).enumerate() {
            if (0..3).all(|k| p[k].abs_diff(c[k]) <= 3) {
                let (x, y) = ((i as u32 % img.w) as i32, (i as u32 / img.w) as i32);
                b = Some(b.map_or((x, y, x + 1, y + 1), |(l, t, r, bt)| (l.min(x), t.min(y), r.max(x + 1), bt.max(y + 1))));
            }
        }
        b
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;

    fn found(id: u64) -> Found {
        Found { window: Window { id, pid: 7, app: "C:\\Odoo\\odoo.exe".into() }, nom: "Odoo".into(), titre: "Odoo".into(), reduite: false, terminal: false }
    }

    #[test]
    fn the_same_id_under_another_process_or_program_is_not_the_window() {
        let chosen = Window { id: 42, pid: 100, app: "chrome.exe".into() };
        assert!(same_window(&chosen, Some(&chosen.clone())));
        assert!(!same_window(&chosen, Some(&Window { pid: 101, ..chosen.clone() })));
        assert!(!same_window(&chosen, Some(&Window { app: "evil.exe".into(), ..chosen.clone() })));
        assert!(!same_window(&chosen, Some(&Window { id: 43, ..chosen.clone() })));
        assert!(!same_window(&chosen, None));
    }

    #[test]
    fn a_title_is_one_line_of_sixty_characters_at_most() {
        assert_eq!(short("Factures - Odoo"), "Factures - Odoo");
        assert_eq!(short("  Ligne 1\r\n\tLigne 2  "), "Ligne 1 Ligne 2");
        let long = "é".repeat(80);
        let cut = short(&long);
        assert_eq!(cut.chars().count(), TITRE_MAX);
        assert!(cut.ends_with('…') && cut.starts_with("éé"));
        assert_eq!(short(&"a".repeat(TITRE_MAX)), "a".repeat(TITRE_MAX), "60 exactly stays whole");
        let f = Fenetre::from(&Found { titre: long, ..found(1) });
        assert_eq!((f.id, f.app.as_str(), f.titre.chars().count()), (1, "Odoo", TITRE_MAX));
    }

    #[test]
    fn terminals_by_program_or_window_class() {
        for (app, class) in [
            ("C:\\Windows\\System32\\cmd.exe", ""),
            ("C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe", ""),
            ("C:\\Program Files\\PowerShell\\7\\pwsh.exe", ""),
            ("C:\\Program Files\\WindowsApps\\Microsoft.WindowsTerminal_1.21\\WindowsTerminal.exe", ""),
            ("C:\\Windows\\System32\\conhost.exe", ""),
            ("C:\\x\\anything.exe", "ConsoleWindowClass"),
            ("C:\\x\\anything.exe", "CASCADIA_HOSTING_WINDOW_CLASS"),
            ("C:\\Program Files\\Git\\usr\\bin\\mintty.exe", ""),
            ("C:\\Program Files\\PuTTY\\putty.exe", ""),
            ("gnome-terminal-server", ""),
            ("konsole", ""),
            ("xterm", ""),
        ] {
            assert!(is_terminal(app, class), "{app} {class}");
        }
        for (app, class) in [
            ("C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe", "Chrome_WidgetWin_1"),
            ("C:\\Program Files\\Sage\\Sage100.exe", "TApplication"),
            ("C:\\Program Files\\Microsoft Office\\root\\Office16\\EXCEL.EXE", "XLMAIN"),
            ("C:\\x\\terminal-de-paiement.exe", "TForm"),
            ("C:\\x\\cmdline-erp.exe", ""),
        ] {
            assert!(!is_terminal(app, class), "{app} {class}");
        }
    }

    #[test]
    fn the_click_is_the_window_that_comes_to_the_front() {
        let never = AtomicBool::new(false);
        let step = Duration::from_millis(1);
        // Bibo in front (none), then the user's window.
        let calls = Cell::new(0);
        let front = || {
            calls.set(calls.get() + 1);
            (calls.get() > 3).then(|| found(9))
        };
        assert_eq!(wait_for(&front, Duration::from_secs(5), step, &never).map(|f| f.window.id), Some(9));

        // The window already in front is not a click: the next one is.
        let calls = Cell::new(0);
        let front = || {
            calls.set(calls.get() + 1);
            Some(found(if calls.get() > 5 { 2 } else { 1 }))
        };
        assert_eq!(wait_for(&front, Duration::from_secs(5), step, &never).map(|f| f.window.id), Some(2));

        // The user's window already in front (clicked while Bibo got ready): Bibo takes the focus, then
        // the click in that same window counts.
        let calls = Cell::new(0);
        let front = || {
            calls.set(calls.get() + 1);
            (!(4..=6).contains(&calls.get())).then(|| found(1))
        };
        assert_eq!(wait_for(&front, Duration::from_secs(1), step, &never).map(|f| f.window.id), Some(1));

        // Nobody clicks: given up at the limit, not before.
        let start = Instant::now();
        assert_eq!(wait_for(&|| Some(found(1)), Duration::from_millis(60), step, &never), None);
        assert!(start.elapsed() >= Duration::from_millis(60));

        // « Arrêter »: given up at once.
        let cancel = AtomicBool::new(true);
        let start = Instant::now();
        assert_eq!(wait_for(&|| None, Duration::from_secs(5), step, &cancel), None);
        assert!(start.elapsed() < Duration::from_secs(1));
    }

    /// A bitmap as PrintWindow fills it: each pixel (b, g, r, junk), the value its position.
    fn bgra(w: u32, h: u32) -> Vec<u8> {
        (0..w * h).flat_map(|i| [i as u8, (i >> 8) as u8, 200, 0]).collect()
    }

    fn at(img: &Rgba, x: u32, y: u32) -> [u8; 4] {
        let i = ((y * img.w + x) * 4) as usize;
        [img.px[i], img.px[i + 1], img.px[i + 2], img.px[i + 3]]
    }

    #[test]
    fn windows_pixels_become_opaque_rgba_cut_to_the_frame() {
        let raw = bgra(10, 6);
        let img = from_bgra(&raw, 10, 6, (0, 0, 10, 6));
        assert_eq!((img.w, img.h, img.px.len()), (10, 6, 240));
        assert_eq!(at(&img, 3, 2), [200, 0, 23, 255], "red and blue swapped, opaque");

        // The invisible borders of Windows 10 left out: 2 px on each side, 1 at the top.
        let img = from_bgra(&raw, 10, 6, (2, 1, 8, 4));
        assert_eq!((img.w, img.h), (6, 3));
        assert_eq!(at(&img, 0, 0), [200, 0, 12, 255], "(2, 1) of the bitmap");
        assert_eq!(at(&img, 5, 2), [200, 0, 37, 255], "(7, 3)");

        // A frame that goes past the bitmap is cut to it; an empty one keeps the whole bitmap.
        let img = from_bgra(&raw, 10, 6, (-3, -3, 40, 40));
        assert_eq!((img.w, img.h), (10, 6));
        let img = from_bgra(&raw, 10, 6, (5, 5, 5, 9));
        assert_eq!((img.w, img.h), (10, 6));
        assert_eq!(crate::shot::black_share(&img), 0.0, "the unused byte never makes a pixel transparent");
    }

    #[test]
    fn the_dialog_lies_at_its_place_cut_to_the_window() {
        let mut base = crate::shot::tests::rgba(8, 6, [0, 0, 255, 255]);
        let dialog = crate::shot::tests::rgba(3, 2, [255, 128, 0, 255]);
        lay(&mut base, &dialog, 2, 3);
        let orange =
            |img: &Rgba| (0..img.h).flat_map(|y| (0..img.w).map(move |x| (x, y))).filter(|&(x, y)| at(img, x, y) == [255, 128, 0, 255]).collect::<Vec<_>>();
        assert_eq!(orange(&base), vec![(2, 3), (3, 3), (4, 3), (2, 4), (3, 4), (4, 4)]);

        // Past the right and bottom edges, or the top and left ones: what lies outside is dropped.
        let mut base = crate::shot::tests::rgba(8, 6, [0, 0, 255, 255]);
        lay(&mut base, &dialog, 6, 5);
        assert_eq!(orange(&base), vec![(6, 5), (7, 5)]);
        let mut base = crate::shot::tests::rgba(8, 6, [0, 0, 255, 255]);
        lay(&mut base, &dialog, -2, -1);
        assert_eq!(orange(&base), vec![(0, 0)]);
        let mut base = crate::shot::tests::rgba(8, 6, [0, 0, 255, 255]);
        lay(&mut base, &dialog, 50, -50);
        assert_eq!((orange(&base), base.w, base.h), (vec![], 8, 6));
    }

    #[test]
    fn a_macos_frame_becomes_rgba_its_rows_unpadded_its_alpha_kept() {
        // 2×2 pixels, rows of 12 bytes (4 of padding), each (b, g, r, a).
        let raw = [1, 2, 3, 255, 4, 5, 6, 128, 9, 9, 9, 9, 7, 8, 9, 0, 10, 11, 12, 255, 9, 9, 9, 9];
        let img = from_bgra_rows(&raw, 2, 2, 12).unwrap();
        assert_eq!((img.w, img.h), (2, 2));
        assert_eq!(img.px, [3, 2, 1, 255, 6, 5, 4, 128, 9, 8, 7, 0, 12, 11, 10, 255]);
        assert!(from_bgra_rows(&raw, 2, 2, 7).is_none(), "rows shorter than a row");
        assert!(from_bgra_rows(&raw[..19], 2, 2, 12).is_none(), "fewer bytes than its size says");
        assert!(from_bgra_rows(&raw, 0, 2, 12).is_none());
        // The last row needs no padding.
        assert!(from_bgra_rows(&raw[..20], 2, 2, 12).is_some());
    }

    #[test]
    fn macos_terminals_by_their_bundle_id() {
        for app in ["com.apple.Terminal", "com.googlecode.iterm2", "dev.warp.Warp-Stable", "com.mitchellh.ghostty"] {
            assert!(is_terminal(app, ""), "{app}");
        }
        for app in ["com.google.Chrome", "com.apple.Safari", "com.microsoft.Excel", ""] {
            assert!(!is_terminal(app, ""), "{app}");
        }
    }

    #[test]
    fn wayland_by_its_session_or_its_socket() {
        assert!(wayland(Some("wayland"), None));
        assert!(wayland(Some("Wayland"), Some("")));
        assert!(wayland(Some("x11"), Some("wayland-0")), "XWayland: still Wayland");
        assert!(wayland(None, Some("wayland-0")));
        assert!(!wayland(Some("x11"), None));
        assert!(!wayland(Some("x11"), Some("")));
        assert!(!wayland(None, None));
        assert!(!wayland(Some("tty"), None));
    }

    /// An X image of 3×2 pixels, each (r, g, b) known, laid out as the server says.
    fn ximage(bits: u32, lsb_first: bool, masks: [u64; 3], stride: usize) -> (Vec<u8>, XPixels<'static>) {
        let colors = [[10u8, 20, 30], [40, 50, 60], [70, 80, 90], [100, 110, 120], [130, 140, 150], [250, 1, 128]];
        let bytes = bits as usize / 8;
        let mut data = vec![0xAAu8; stride * 2];
        for (i, c) in colors.iter().enumerate() {
            let shift = |m: u64| m.trailing_zeros();
            let v = (u32::from(c[0]) << shift(masks[0])) | (u32::from(c[1]) << shift(masks[1])) | (u32::from(c[2]) << shift(masks[2]));
            let at = (i / 3) * stride + (i % 3) * bytes;
            for k in 0..bytes {
                data[at + k] = (v >> (8 * if lsb_first { k } else { bytes - 1 - k })) as u8;
            }
        }
        let x = XPixels { data: &[], w: 3, h: 2, stride, bits, lsb_first, masks };
        (data, x)
    }

    #[test]
    fn an_x_image_becomes_opaque_rgba_whatever_its_layout() {
        let want = [10u8, 20, 30, 255, 40, 50, 60, 255, 70, 80, 90, 255, 100, 110, 120, 255, 130, 140, 150, 255, 250, 1, 128, 255];
        let rgb = [0xff0000, 0xff00, 0xff];
        for (bits, lsb, masks, stride) in [
            (32, true, rgb, 12),
            (32, true, rgb, 16),
            (32, false, rgb, 12),
            (24, true, rgb, 9),
            (24, false, rgb, 12),
            (32, true, [0xff, 0xff00, 0xff0000], 12),
            (32, true, [0xff00_0000, 0xff_0000, 0xff00], 12),
        ] {
            let (data, x) = ximage(bits, lsb, masks, stride);
            let img = from_ximage(&XPixels { data: &data, ..x }).unwrap();
            assert_eq!((img.w, img.h, img.px.as_slice()), (3, 2, want.as_slice()), "{bits} bits, lsb {lsb}, masks {masks:x?}, stride {stride}");
        }
        // A layout it does not know is no image, never a guess.
        for (bits, masks) in [(16, [0xf800, 0x7e0, 0x1f]), (32, [0x3ff0_0000, 0xffc00, 0x3ff]), (32, [0, 0xff00, 0xff]), (8, rgb)] {
            let (data, x) = ximage(32, true, [0xff0000, 0xff00, 0xff], 12);
            assert!(from_ximage(&XPixels { data: &data, bits, masks, ..x }).is_none(), "{bits} {masks:x?}");
        }
        let (data, x) = ximage(32, true, rgb, 12);
        assert!(from_ximage(&XPixels { data: &data[..20], ..x }).is_none(), "fewer bytes than its size says");
        assert!(from_ximage(&XPixels { data: &data, stride: 8, ..x }).is_none(), "rows shorter than a row");
        assert!(from_ximage(&XPixels { data: &data, w: 0, ..x }).is_none());
    }

    #[test]
    fn only_the_part_of_a_window_on_the_screen_is_read() {
        let screen = (1280, 800);
        assert_eq!(on_screen((100, 50), (640, 480), screen), Some((0, 0, 640, 480)));
        assert_eq!(on_screen((-40, -10), (640, 480), screen), Some((40, 10, 640, 480)), "past the top left corner");
        assert_eq!(on_screen((1000, 600), (640, 480), screen), Some((0, 0, 280, 200)), "past the bottom right one");
        assert_eq!(on_screen((1280, 0), (640, 480), screen), None, "beside the screen");
        assert_eq!(on_screen((-640, 0), (640, 480), screen), None);
        assert_eq!(on_screen((i32::MIN, i32::MAX), (640, 480), screen), None);
    }

    #[test]
    fn wm_class_and_latin1() {
        assert_eq!(wm_class(b"gnome-terminal-server\0Gnome-terminal\0"), ("gnome-terminal-server".into(), "Gnome-terminal".into()));
        assert_eq!(wm_class(b"soffice\0libreoffice-calc"), ("soffice".into(), "libreoffice-calc".into()));
        assert_eq!(wm_class(b""), (String::new(), String::new()));
        assert_eq!(latin1(b"Caf\xe9 cr\xe8me"), "Café crème");
        let (instance, class) = wm_class(b"xterm\0XTerm\0");
        assert!(is_terminal(&instance, "") && is_terminal(&class, ""));
    }
}
