//! W1: the app in front, as one image. Every window of its process on screen (its main window, its
//! dialogs, its menus, its palettes), each drawn by itself (PrintWindow), laid bottom first in the
//! screen's order, on black. What lies between or over them (another program, the desktop) is never
//! read: it stays black. A browser is the window in front and what it owns only: its other windows
//! (another site) are left out.

use std::path::Path;

use windows::Win32::Foundation::{HWND, RECT};

use crate::prims::{class, cloaked, frame, image_path, lay, minimized, owner, pid_of, print, screen_rect, top_windows, visible, Physical, Rgba};

pub const NAVIGATEURS: &[&str] = &["msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"];

/// Shadows the system draws beside a menu or a dialog: a window of the same process, of no content.
const OMBRES: &[&str] = &["SysShadow"];

pub struct Composite {
    pub img: Rgba,
    /// Where the image's top-left corner lies on the screen (physical pixels).
    pub left: i32,
    pub top: i32,
    /// The windows drawn, the one on top first, and those that would not draw (left black).
    pub fenetres: Vec<(HWND, RECT)>,
    pub ratees: usize,
}

impl Composite {
    /// A rectangle of the screen, in the image's coordinates.
    pub fn zone(&self, r: RECT) -> (i32, i32, i32, i32) {
        (r.left - self.left, r.top - self.top, r.right - self.left, r.bottom - self.top)
    }
}

pub fn navigateur(pid: u32) -> bool {
    image_path(pid).and_then(|p| Path::new(&p).file_name().map(|n| n.to_string_lossy().to_ascii_lowercase())).is_some_and(|n| NAVIGATEURS.contains(&n.as_str()))
}

fn owned_by(h: HWND, front: HWND) -> bool {
    let mut o = owner(h);
    for _ in 0..32 {
        match o {
            Some(x) if x == front => return true,
            Some(x) => o = owner(x),
            None => return false,
        }
    }
    false
}

/// The windows that make the app in front, the one on top first.
pub fn fenetres(pid: u32, front: HWND) -> Vec<HWND> {
    let nav = navigateur(pid);
    top_windows()
        .into_iter()
        .filter(|&h| visible(h) && !cloaked(h) && !minimized(h) && pid_of(h) == pid)
        .filter(|&h| !OMBRES.contains(&class(h).as_str()))
        .filter(|&h| !nav || h == front || owned_by(h, front))
        .collect()
}

/// The app of process `pid` as one image, cut to the box around its windows (and to the screens).
pub fn composer(pid: u32, front: HWND) -> Option<Composite> {
    let _physical = Physical::enter();
    let mut placed = Vec::new();
    for h in fenetres(pid, front) {
        if let Some(r) = frame(h) {
            placed.push((h, r));
        }
    }
    let s = screen_rect();
    let u = placed.iter().map(|&(_, r)| r).reduce(|a, b| RECT { left: a.left.min(b.left), top: a.top.min(b.top), right: a.right.max(b.right), bottom: a.bottom.max(b.bottom) })?;
    let b = RECT { left: u.left.max(s.left), top: u.top.max(s.top), right: u.right.min(s.right), bottom: u.bottom.min(s.bottom) };
    if b.right <= b.left || b.bottom <= b.top {
        return None;
    }
    let mut img = Rgba::black((b.right - b.left) as u32, (b.bottom - b.top) as u32);
    let mut ratees = 0;
    for &(h, _) in placed.iter().rev() {
        match print(h) {
            Some((pic, at)) => lay(&mut img, &pic, i64::from(at.left - b.left), i64::from(at.top - b.top)),
            None => ratees += 1,
        }
    }
    Some(Composite { img, left: b.left, top: b.top, fenetres: placed, ratees })
}
