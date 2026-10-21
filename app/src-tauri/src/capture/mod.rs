//! The automatic capture (docs/proposals/guide.md §7.1): the window the user chose, when they ask
//! Bibo to look, never the screen. On Windows (phase 2a) by PrintWindow alone: the window draws
//! itself into Bibo's memory, so another program's window over it never gets into the image. When it
//! cannot (a DirectX surface, a program run as administrator, a protected window), the capture is
//! black and the user pastes one instead: never a copy of the screen in its place (R20). macOS (the
//! system's picker) and X11 come in phase 2b; until then, as on Wayland, the guide is in paste mode.

use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use serde::Serialize;

use crate::shot::Rgba;

#[cfg(windows)]
mod win;

/// How long Bibo waits for the click in the user's window (R4).
const CLIC: Duration = Duration::from_secs(15);
const POLL: Duration = Duration::from_millis(100);
/// G2: a title is cut there (a mail's subject, a document's name: no more of it than needed).
const TITRE_MAX: usize = 60;

/// A window kept for the session. Its id may go to another program's window once it is closed, so
/// its process and its program go with it, and all three are checked before each capture (R22).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Window {
    pub id: u64,
    pub pid: u32,
    /// The executable's path (Windows), or WM_CLASS (X11).
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
#[cfg_attr(not(windows), allow(dead_code))] // until macOS and X11 capture too (phase 2b)
pub enum Rate {
    /// Not on this system yet (macOS and Linux until phase 2b): paste mode.
    #[cfg_attr(windows, allow(dead_code))]
    Indisponible,
    /// Closed, or its id now another program's window (R22).
    Introuvable,
    /// Minimized or hidden: there is nothing to draw.
    Reduite,
    /// The window would not draw itself: black, never the screen instead (R20).
    Noire,
}

/// The system's windows, as the guide asks for them: this computer's, or a test's.
pub trait Screen {
    fn available(&self) -> bool;
    /// Other programs' windows, the top one first (G2's list).
    fn windows(&self) -> Vec<Found>;
    /// The window that comes to the front within 15 s, Bibo's left out (R4); None if none came or
    /// `cancel` was set.
    fn clicked(&self, cancel: &AtomicBool) -> Option<Found>;
    /// The window under this id now, if it is one the guide may capture.
    fn find(&self, id: u64) -> Option<Found>;
    /// The window, if it is still the one chosen (R22), as it draws itself, with the dialog it waits
    /// on drawn at its place (R11).
    fn grab(&self, chosen: &Window) -> Result<Rgba, Rate>;
}

/// This computer's windows.
pub struct System;

impl Screen for System {
    fn available(&self) -> bool {
        cfg!(windows)
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

#[cfg(windows)]
use win as os;

#[cfg(not(windows))]
mod os {
    use super::{Found, Rate, Window};
    use crate::shot::Rgba;

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

/// R6: a terminal, by its program (its executable's name, or WM_CLASS) or its window's class.
#[cfg_attr(not(windows), allow(dead_code))] // X11 (phase 2b) reads it too
fn is_terminal(app: &str, class: &str) -> bool {
    const PROGRAMS: &[&str] = &[
        "cmd", "conhost", "openconsole", "windowsterminal", "powershell", "powershell_ise", "pwsh", "wsl", "bash", "mintty",
        "conemu", "conemu64", "putty", "kitty", "alacritty", "wezterm-gui", "tabby", "hyper", "gnome-terminal-server",
        "gnome-terminal", "konsole", "xterm", "uxterm", "xfce4-terminal", "tilix", "terminator", "lxterminal",
    ];
    const CLASSES: &[&str] = &["consolewindowclass", "cascadia_hosting_window_class", "virtualconsoleclass", "mintty", "putty"];
    let file = app.rsplit(['\\', '/']).next().unwrap_or(app).to_lowercase();
    let program = file.strip_suffix(".exe").unwrap_or(&file);
    PROGRAMS.contains(&program) || CLASSES.contains(&class.to_lowercase().as_str())
}

/// The window that comes to the front. The one in front at the start is left out: Bibo's button was
/// clicked there, or the user's window already was, and only a click says which one they mean.
fn wait_for(front: &dyn Fn() -> Option<Found>, limit: Duration, step: Duration, cancel: &AtomicBool) -> Option<Found> {
    let start = Instant::now();
    let before = front().map(|f| f.window.id);
    loop {
        if cancel.load(Ordering::SeqCst) {
            return None;
        }
        if let Some(f) = front().filter(|f| Some(f.window.id) != before) {
            return Some(f);
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
#[cfg_attr(not(windows), allow(dead_code))]
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
        let orange = |img: &Rgba| (0..img.h).flat_map(|y| (0..img.w).map(move |x| (x, y))).filter(|&(x, y)| at(img, x, y) == [255, 128, 0, 255]).collect::<Vec<_>>();
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
}
