//! Linux under X11: other programs' windows as the window manager lists them (EWMH), the one in
//! front, and their capture by XGetImage, on `x11-dl`, already in the build (tao's), which loads
//! libX11 when asked: no other crate, no -dev package to build.
//!
//! Only while a compositing manager draws the screen (R21). It keeps each window's pixels in a pixmap
//! of its own, which XGetImage reads; without it, what lies over the window is « undefined » (Xlib's
//! manual), on a real Xorg another program's pixels, which no share of black would tell. So without a
//! compositor, nor under Wayland, there is no capture: the user pastes one.

use std::ffi::{c_int, c_uchar, c_uint, c_ulong};
use std::ptr::{null, null_mut};
use std::sync::atomic::{AtomicPtr, Ordering};
use std::sync::{Mutex, MutexGuard, OnceLock, PoisonError};

use x11_dl::xlib::{self, Atom, Display, XErrorEvent, XWindowAttributes, Xlib};

use super::{from_ximage, is_terminal, latin1, lay, on_screen, same_window, wm_class, Found, Rate, Window, XPixels};
use crate::shot::{Rgba, MAX_SIDE};

/// WM_STATE's IconicState: minimized, as ICCCM says it.
const ICONIC: c_ulong = 3;
/// How deep a dialog's own windows are climbed (WM_TRANSIENT_FOR), a loop broken.
const UP: usize = 32;

/// A C long of X's as a u64: the same type on 64-bit Linux, not on 32-bit.
#[allow(clippy::unnecessary_cast)]
fn wide(v: c_ulong) -> u64 {
    v as u64
}

type OnError = unsafe extern "C" fn(*mut Display, *mut XErrorEvent) -> c_int;

/// Bibo's own connection, apart from its windows' (GTK's): what goes wrong on it is read from what
/// each call returns, and never ends the app.
static OURS: AtomicPtr<Display> = AtomicPtr::new(null_mut());
/// The handler before Bibo's (GTK's, or Xlib's): every other connection's errors still go there.
static BEFORE: OnceLock<Option<OnError>> = OnceLock::new();

unsafe extern "C" fn on_error(d: *mut Display, e: *mut XErrorEvent) -> c_int {
    if d == OURS.load(Ordering::SeqCst) {
        return 0;
    }
    match BEFORE.get().copied().flatten() {
        // SAFETY: the handler Xlib had, called as Xlib would.
        Some(before) => unsafe { before(d, e) },
        None => 0,
    }
}

struct Atoms {
    stacking: Atom,
    clients: Atom,
    active: Atom,
    name: Atom,
    utf8: Atom,
    pid: Atom,
    state: Atom,
    hidden: Atom,
    skip_taskbar: Atom,
    kind: Atom,
    normal: Atom,
    dialog: Atom,
    wm_state: Atom,
    /// `_NET_WM_CM_S{screen}`: the compositing manager owns it while it runs.
    compositor: Atom,
}

struct Conn {
    x: Xlib,
    d: *mut Display,
    root: xlib::Window,
    screen: c_int,
    atoms: Atoms,
}

// SAFETY: the connection is used under CONN's lock only, by one thread at a time.
unsafe impl Send for Conn {}

static CONN: OnceLock<Option<Mutex<Conn>>> = OnceLock::new();

fn conn() -> Option<MutexGuard<'static, Conn>> {
    CONN.get_or_init(open).as_ref().map(|c| c.lock().unwrap_or_else(PoisonError::into_inner))
}

/// A Wayland session, as the environment says it now: checked at each use, not only once.
fn on_wayland() -> bool {
    super::wayland(std::env::var("XDG_SESSION_TYPE").ok().as_deref(), std::env::var("WAYLAND_DISPLAY").ok().as_deref())
}

fn open() -> Option<Mutex<Conn>> {
    if on_wayland() {
        return None;
    }
    std::env::var_os("DISPLAY").filter(|d| !d.is_empty())?;
    let x = Xlib::open().ok()?;
    // SAFETY: the display named by DISPLAY; ours for the app's life.
    let d = unsafe { (x.XOpenDisplay)(null()) };
    if d.is_null() {
        return None;
    }
    OURS.store(d, Ordering::SeqCst);
    // SAFETY: a handler that ignores this connection's errors and passes on every other one.
    let before = unsafe { (x.XSetErrorHandler)(Some(on_error)) };
    let _ = BEFORE.set(before);
    // SAFETY: an open display.
    let (screen, root) = unsafe {
        let screen = (x.XDefaultScreen)(d);
        (screen, (x.XRootWindow)(d, screen))
    };
    let atom = |name: &str| {
        let name = std::ffi::CString::new(name).expect("no NUL");
        // SAFETY: a NUL-terminated name; the atom is made if the server has none yet.
        unsafe { (x.XInternAtom)(d, name.as_ptr(), 0) }
    };
    let atoms = Atoms {
        stacking: atom("_NET_CLIENT_LIST_STACKING"),
        clients: atom("_NET_CLIENT_LIST"),
        active: atom("_NET_ACTIVE_WINDOW"),
        name: atom("_NET_WM_NAME"),
        utf8: atom("UTF8_STRING"),
        pid: atom("_NET_WM_PID"),
        state: atom("_NET_WM_STATE"),
        hidden: atom("_NET_WM_STATE_HIDDEN"),
        skip_taskbar: atom("_NET_WM_STATE_SKIP_TASKBAR"),
        kind: atom("_NET_WM_WINDOW_TYPE"),
        normal: atom("_NET_WM_WINDOW_TYPE_NORMAL"),
        dialog: atom("_NET_WM_WINDOW_TYPE_DIALOG"),
        wm_state: atom("WM_STATE"),
        compositor: atom(&format!("_NET_WM_CM_S{screen}")),
    };
    Some(Mutex::new(Conn { x, d, root, screen, atoms }))
}

/// A property as the server keeps it: bytes (format 8) or 32-bit items (format 32).
enum Prop {
    Bytes(Atom, Vec<u8>),
    Items(Vec<c_ulong>),
}

impl Conn {
    fn prop(&self, w: xlib::Window, name: Atom, kind: Atom) -> Option<Prop> {
        let (mut actual, mut format, mut n, mut after, mut data) = (0, 0, 0, 0, null_mut::<c_uchar>());
        // SAFETY: out-parameters for the call; 64 Ki items at most, enough for a list or a title.
        let status = unsafe { (self.x.XGetWindowProperty)(self.d, w, name, 0, 1 << 16, 0, kind, &mut actual, &mut format, &mut n, &mut after, &mut data) };
        if data.is_null() {
            return None;
        }
        let n = n as usize;
        // SAFETY: Xlib's buffer of `n` items of `format` bits (a C long each for 32), freed once below.
        let read = (status == c_int::from(xlib::Success)).then(|| unsafe {
            match format {
                8 => Some(Prop::Bytes(actual, std::slice::from_raw_parts(data, n).to_vec())),
                32 => Some(Prop::Items(std::slice::from_raw_parts(data.cast::<c_ulong>(), n).to_vec())),
                _ => None,
            }
        });
        // SAFETY: Xlib's buffer, freed once.
        unsafe { (self.x.XFree)(data.cast()) };
        read.flatten()
    }

    fn items(&self, w: xlib::Window, name: Atom, kind: Atom) -> Vec<c_ulong> {
        match self.prop(w, name, kind) {
            Some(Prop::Items(v)) => v,
            _ => Vec::new(),
        }
    }

    fn bytes(&self, w: xlib::Window, name: Atom, kind: Atom) -> Option<(Atom, Vec<u8>)> {
        match self.prop(w, name, kind) {
            Some(Prop::Bytes(actual, v)) => Some((actual, v)),
            _ => None,
        }
    }

    fn compositor(&self) -> bool {
        // SAFETY: an open display and an atom of it.
        unsafe { (self.x.XGetSelectionOwner)(self.d, self.atoms.compositor) != 0 }
    }

    /// The window manager's windows, the bottom one first.
    fn clients(&self) -> Vec<xlib::Window> {
        let stacked = self.items(self.root, self.atoms.stacking, xlib::XA_WINDOW);
        if stacked.is_empty() {
            self.items(self.root, self.atoms.clients, xlib::XA_WINDOW)
        } else {
            stacked
        }
    }

    fn attributes(&self, w: xlib::Window) -> Option<XWindowAttributes> {
        // SAFETY: plain C data, filled by the call when it succeeds.
        let mut a: XWindowAttributes = unsafe { std::mem::zeroed() };
        // SAFETY: as above.
        (unsafe { (self.x.XGetWindowAttributes)(self.d, w, &mut a) } != 0).then_some(a)
    }

    /// The window's top left corner on the screen.
    fn corner(&self, w: xlib::Window) -> Option<(i32, i32)> {
        let (mut x, mut y, mut child) = (0, 0, 0);
        // SAFETY: out-parameters for the call.
        (unsafe { (self.x.XTranslateCoordinates)(self.d, w, self.root, 0, 0, &mut x, &mut y, &mut child) } != 0).then_some((x, y))
    }

    fn transient_for(&self, w: xlib::Window) -> Option<xlib::Window> {
        let mut parent = 0;
        // SAFETY: an out-parameter for the call.
        (unsafe { (self.x.XGetTransientForHint)(self.d, w, &mut parent) } != 0 && parent != 0).then_some(parent)
    }

    fn class(&self, w: xlib::Window) -> (String, String) {
        self.bytes(w, xlib::XA_WM_CLASS, xlib::XA_STRING).map(|(_, raw)| wm_class(&raw)).unwrap_or_default()
    }

    fn title(&self, w: xlib::Window) -> String {
        if let Some((_, raw)) = self.bytes(w, self.atoms.name, self.atoms.utf8) {
            return String::from_utf8_lossy(&raw).into_owned();
        }
        match self.bytes(w, xlib::XA_WM_NAME, xlib::AnyPropertyType as Atom) {
            Some((kind, raw)) if kind == xlib::XA_STRING => latin1(&raw),
            Some((_, raw)) => String::from_utf8_lossy(&raw).into_owned(),
            None => String::new(),
        }
    }

    /// A window of the window manager's, with its process (`_NET_WM_PID`, 0 for the old programs
    /// that do not say it) and its program (WM_CLASS): what is checked before each capture (R22).
    fn identity(&self, w: xlib::Window, clients: &[xlib::Window]) -> Option<Window> {
        if w == 0 || !clients.contains(&w) {
            return None;
        }
        let pid = self.items(w, self.atoms.pid, xlib::XA_CARDINAL).first().map_or(0, |&p| p as u32);
        let (instance, class) = self.class(w);
        Some(Window { id: wide(w), pid, app: format!("{instance}.{class}") })
    }

    /// Minimized, on another desktop, or not shown.
    fn reduced(&self, w: xlib::Window, a: &XWindowAttributes) -> bool {
        a.map_state != xlib::IsViewable
            || self.items(w, self.atoms.state, xlib::XA_ATOM).contains(&self.atoms.hidden)
            || self.items(w, self.atoms.wm_state, self.atoms.wm_state).first() == Some(&ICONIC)
    }

    /// A window of the taskbar's kind: a plain window or a dialog of its own, not a panel, not a
    /// desktop, not one that keeps out of the taskbar; a dialog of a listed window comes with it.
    fn found(&self, w: xlib::Window, clients: &[xlib::Window]) -> Option<Found> {
        let window = self.identity(w, clients)?;
        if window.pid == std::process::id() {
            return None;
        }
        let kinds = self.items(w, self.atoms.kind, xlib::XA_ATOM);
        if !kinds.is_empty() && !kinds.iter().any(|&k| k == self.atoms.normal || k == self.atoms.dialog) {
            return None;
        }
        if self.items(w, self.atoms.state, xlib::XA_ATOM).contains(&self.atoms.skip_taskbar) {
            return None;
        }
        if self.transient_for(w).is_some_and(|p| clients.contains(&p)) {
            return None;
        }
        let titre = self.title(w);
        if titre.trim().is_empty() {
            return None;
        }
        let attributes = self.attributes(w)?;
        let (instance, class) = self.class(w);
        let nom = if class.is_empty() { instance.clone() } else { class.clone() };
        Some(Found { reduite: self.reduced(w, &attributes), terminal: is_terminal(&instance, "") || is_terminal(&class, ""), nom, titre, window })
    }

    /// The window as it is drawn now, the part of it on the screen, and where that part starts on
    /// the screen; `Noire` when X gives no image Bibo can read.
    fn image(&self, w: xlib::Window, a: &XWindowAttributes) -> Result<(Rgba, (i32, i32)), Rate> {
        let corner = self.corner(w).ok_or(Rate::Introuvable)?;
        // SAFETY: an open display and its screen.
        let size = unsafe { ((self.x.XDisplayWidth)(self.d, self.screen), (self.x.XDisplayHeight)(self.d, self.screen)) };
        let (x0, y0, x1, y1) = on_screen(corner, (a.width, a.height), size).ok_or(Rate::Noire)?;
        let (w_, h_) = ((x1 - x0) as c_uint, (y1 - y0) as c_uint);
        if w_ > MAX_SIDE || h_ > MAX_SIDE {
            return Err(Rate::Noire);
        }
        // SAFETY: a rectangle of the window on the screen; every plane; NULL when X refuses.
        let image = unsafe { (self.x.XGetImage)(self.d, w, x0, y0, w_, h_, c_ulong::MAX, xlib::ZPixmap) };
        if image.is_null() {
            return Err(Rate::Noire);
        }
        // SAFETY: the image Xlib made, read within its rows, destroyed once.
        let rgba = unsafe {
            let i = &*image;
            let len = (i.bytes_per_line.max(0) as usize).saturating_mul(i.height.max(0) as usize);
            let read = (!i.data.is_null()).then(|| {
                from_ximage(&XPixels {
                    data: std::slice::from_raw_parts(i.data.cast::<u8>(), len),
                    w: i.width.max(0) as u32,
                    h: i.height.max(0) as u32,
                    stride: i.bytes_per_line.max(0) as usize,
                    bits: i.bits_per_pixel.max(0) as u32,
                    lsb_first: i.byte_order == xlib::LSBFirst,
                    masks: [wide(i.red_mask), wide(i.green_mask), wide(i.blue_mask)],
                })
            });
            (self.x.XDestroyImage)(image);
            read.flatten()
        };
        rgba.map(|img| (img, (corner.0 + x0, corner.1 + y0))).ok_or(Rate::Noire)
    }

    /// The dialog of the same process over the window (WM_TRANSIENT_FOR), the top one (R11).
    fn dialog(&self, w: xlib::Window, pid: u32, clients: &[xlib::Window]) -> Option<(xlib::Window, XWindowAttributes)> {
        if pid == 0 {
            return None;
        }
        clients.iter().rev().filter(|&&c| c != w && self.transient_for(c) == Some(w)).find_map(|&c| {
            let a = self.attributes(c)?;
            (self.identity(c, clients)?.pid == pid && !self.reduced(c, &a)).then_some((c, a))
        })
    }
}

pub fn available() -> bool {
    !on_wayland() && conn().is_some_and(|c| c.compositor())
}

/// The window under this id, if it is another program's the guide may capture.
pub fn describe(id: u64) -> Option<Found> {
    let c = conn()?;
    c.found(id as xlib::Window, &c.clients())
}

pub fn list() -> Vec<Found> {
    let Some(c) = conn() else { return Vec::new() };
    let clients = c.clients();
    clients.iter().rev().filter_map(|&w| c.found(w, &clients)).collect()
}

/// The window in front: a dialog's own window, up the windows it is the dialog of.
pub fn foreground() -> Option<Found> {
    let c = conn()?;
    let mut w = *c.items(c.root, c.atoms.active, xlib::XA_WINDOW).first()?;
    let clients = c.clients();
    for _ in 0..UP {
        match c.transient_for(w).filter(|p| clients.contains(p)) {
            Some(p) => w = p,
            None => break,
        }
    }
    c.found(w, &clients)
}

/// The window, if it is still the one chosen and a compositor still draws the screen, as it is drawn
/// in its own pixmap, with its dialog drawn at its place. Checked again afterwards: the image is that
/// window's, not another that took its id meanwhile.
pub fn grab(chosen: &Window) -> Result<Rgba, Rate> {
    if on_wayland() {
        return Err(Rate::Indisponible);
    }
    let c = conn().ok_or(Rate::Indisponible)?;
    if !c.compositor() {
        return Err(Rate::Indisponible);
    }
    let w = chosen.id as xlib::Window;
    let still = || same_window(chosen, c.identity(w, &c.clients()).as_ref());
    if !still() {
        return Err(Rate::Introuvable);
    }
    let a = c.attributes(w).ok_or(Rate::Introuvable)?;
    if c.reduced(w, &a) {
        return Err(Rate::Reduite);
    }
    let (mut image, at) = c.image(w, &a)?;
    if let Some((d, da)) = c.dialog(w, chosen.pid, &c.clients()) {
        if let Ok((dialog, its)) = c.image(d, &da) {
            lay(&mut image, &dialog, i64::from(its.0 - at.0), i64::from(its.1 - at.1));
        }
    }
    if !still() || !c.compositor() {
        return Err(Rate::Introuvable);
    }
    Ok(image)
}

/// On an X11 desktop only, a VM of the CI's or a container: Xvfb, a window manager (EWMH) and a
/// compositing manager, then `cargo test -- --ignored bureau --skip sans_compositeur --test-threads=1`;
/// without the compositor, `cargo test -- --ignored sans_compositeur --test-threads=1`. Windows of known
/// colours (tests/fixtures/guide/atelier_x11.py, Xlib through Python's ctypes) and a page in the
/// browser; their pixels are counted in memory. Clicks by xdotool, as a hand makes them.
#[cfg(test)]
mod tests {
    use super::*;
    use crate::capture::bureau::*;
    use crate::capture::{Screen, System};
    use crate::guide::{self, GuideError};
    use std::path::{Path, PathBuf};
    use std::process::{Command, Stdio};
    use std::sync::atomic::AtomicUsize;
    use std::time::Duration;

    struct Desk {
        dir: tempfile::TempDir,
        nonce: String,
    }

    fn desk() -> Desk {
        static N: AtomicUsize = AtomicUsize::new(0);
        guide::fin();
        Desk { dir: tempfile::tempdir().unwrap(), nonce: format!("{}-{}", std::process::id(), N.fetch_add(1, Ordering::SeqCst)) }
    }

    fn fixture() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests").join("fixtures").join("guide").join("atelier_x11.py")
    }

    fn hex(c: [u8; 3]) -> String {
        format!("{:02x}{:02x}{:02x}", c[0], c[1], c[2])
    }

    impl Desk {
        /// The program of known colours in that mode; its first window as the list shows it, if it
        /// is listed (a dialog of another window is not).
        fn start(&self, mode: &str, what: &str, (x, y, w, h): (i32, i32, i32, i32), extra: &[String]) -> (Program, String) {
            let titre = format!("Atelier {what} {}", self.nonce);
            let mut cmd = Command::new("python3");
            cmd.arg("-I").arg(fixture()).args([mode, &titre, &x.to_string(), &y.to_string(), &w.to_string(), &h.to_string()]).args(extra);
            (Program(cmd.stdout(Stdio::null()).spawn().unwrap()), titre)
        }

        fn open(&self, mode: &str, what: &str, at: (i32, i32, i32, i32), colors: &[[u8; 3]]) -> (Program, Found) {
            let (program, titre) = self.start(mode, what, at, &colors.iter().map(|&c| hex(c)).collect::<Vec<_>>());
            let found = listed_as(|f| f.titre == titre);
            assert_eq!(found.window.pid, program.0.id(), "{titre}");
            (program, found)
        }
    }

    fn xdotool(args: &[&str]) {
        let out = Command::new("xdotool").args(args).output().expect("xdotool on this desktop");
        assert!(out.status.success(), "xdotool {args:?}: {}", String::from_utf8_lossy(&out.stderr));
    }

    fn frame(id: u64) -> (i32, i32, i32, i32) {
        let c = conn().unwrap();
        let a = c.attributes(id as xlib::Window).unwrap();
        let (x, y) = c.corner(id as xlib::Window).unwrap();
        (x, y, x + a.width, y + a.height)
    }

    fn center(id: u64) -> (i32, i32) {
        let (l, t, r, b) = frame(id);
        ((l + r) / 2, (t + b) / 2)
    }

    /// A click as a hand makes it: the pointer moved there, the left button.
    fn click((x, y): (i32, i32)) {
        xdotool(&["mousemove", "--sync", &x.to_string(), &y.to_string(), "click", "1"]);
    }

    /// Above the other in the window manager's stacking order.
    fn above(top: u64, under: u64) -> bool {
        let clients = conn().unwrap().clients();
        let at = |id: u64| clients.iter().position(|&w| wide(w) == id);
        matches!((at(top), at(under)), (Some(t), Some(u)) if t > u)
    }

    fn any_titled(titre: &str) -> Option<u64> {
        let c = conn()?;
        c.clients().into_iter().find(|&w| c.title(w) == titre).map(wide)
    }

    fn compositing() {
        assert!(System.available(), "these tests want a compositing manager (_NET_WM_CM_S0 owned)");
    }

    /// R21: with a compositor, a window under another one is captured as it draws itself, without a
    /// pixel of the one over it; as the guide does it too (the list, the choice, the look).
    #[test]
    #[ignore = "an X11 desktop with a compositor (VM)"]
    fn bureau_a_window_under_another_has_none_of_it_in_its_capture() {
        compositing();
        let d = desk();
        let (_p, win) = d.open("fenetre", "Factures", (80, 80, 640, 480), &[BLUE]);
        assert_eq!((win.nom.as_str(), win.reduite, win.terminal), ("Atelier", false, false), "its name from WM_CLASS");
        assert!(win.window.app.ends_with(".Atelier"), "{}", win.window.app);
        let alone = grab(&win.window).unwrap();
        assert_eq!((alone.w, alone.h), (640, 480), "the window, its frame left to the window manager");
        let blue = count(&alone, BLUE);
        assert!(blue > pixels(&alone) * 95 / 100, "its colour: {blue} of {}", pixels(&alone));

        let (_cover, cover) = d.open("dessus", "Cache", (40, 40, 720, 560), &[MAGENTA]);
        until("the magenta window over it", || above(cover.window.id, win.window.id));
        std::thread::sleep(Duration::from_millis(500));
        let under = grab(&win.window).unwrap();
        assert_eq!(count(&under, MAGENTA), 0, "not a pixel of the window over it");
        assert_eq!(count(&under, BLUE), blue, "the same image as uncovered");

        assert!(guide::fenetres().unwrap().iter().any(|f| f.id == win.window.id && f.app == "Atelier"));
        guide::choisir(Some(win.window.id)).unwrap();
        let a = guide::regarder().unwrap();
        assert_eq!((a.largeur, a.hauteur, a.noir), (640, 480, false));
        guide::fin();
    }

    /// R21, in a browser: a page drawn by the browser, under another window.
    #[test]
    #[ignore = "an X11 desktop with a compositor (VM)"]
    fn bureau_a_page_in_the_browser_under_another_window() {
        compositing();
        let d = desk();
        let titre = format!("Atelier Demo {}", d.nonce);
        let page = d.dir.path().join("atelier.html");
        std::fs::write(
            &page,
            format!(
                "<!doctype html><meta charset=utf-8><title>{titre}</title><style>html,body{{margin:0;height:100%;background:#fff;font:16px sans-serif}}\
                 header{{height:160px;background:#2e7d32}}button{{margin:24px;width:240px;height:80px;background:#2e7d32;color:#fff;border:0}}</style>\
                 <header></header><button>Nouveau</button><p>Factures (inventées)</p>"
            ),
        )
        .unwrap();
        let browsers = ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser"];
        let browser = browsers.iter().find(|b| Path::new(b).is_file()).expect("Chrome or Chromium on this desktop");
        let _b = Program(
            Command::new(browser)
                .arg(format!("--user-data-dir={}", d.dir.path().join("profil").display()))
                .args(["--no-first-run", "--no-default-browser-check", "--ozone-platform=x11", "--window-position=80,80", "--window-size=800,600"])
                .arg(format!("--app=file://{}", page.display()))
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .unwrap(),
        );
        let win = listed_as(|f| f.titre.contains(&titre));
        eprintln!("browser: {browser}, its name: {}, its program: {}", win.nom, win.window.app);
        until("the page drawn", || grab(&win.window).is_ok_and(|img| count(&img, GREEN) > 100_000));
        let alone = grab(&win.window).unwrap();
        let green = count(&alone, GREEN);

        let (_cover, cover) = d.open("dessus", "Cache", (40, 40, 880, 680), &[MAGENTA]);
        until("the magenta window over it", || above(cover.window.id, win.window.id));
        std::thread::sleep(Duration::from_millis(500));
        let under = grab(&win.window).unwrap();
        assert_eq!(count(&under, MAGENTA), 0, "not a pixel of the window over it");
        assert!(count(&under, GREEN).abs_diff(green) <= green / 50, "the page as uncovered: {} and {green}", count(&under, GREEN));
        assert!(crate::shot::black_share(&under) < 0.1, "drawn, not black: {}", crate::shot::black_share(&under));
    }

    /// R11: a window with a dialog of its own over it (WM_TRANSIENT_FOR): the dialog is drawn at its
    /// place on the window's capture; it is no line of the list; a click in it says its window.
    #[test]
    #[ignore = "an X11 desktop with a compositor (VM)"]
    fn bureau_the_dialog_over_a_window_is_in_its_capture() {
        compositing();
        let d = desk();
        let (_p, win) = d.open("modal", "Saisie", (80, 80, 640, 480), &[BLUE, ORANGE]);
        let dialog_title = format!("{} (dialogue)", win.titre);
        until("its dialog shown", || any_titled(&dialog_title).is_some());
        let dialog = any_titled(&dialog_title).unwrap();
        until("its dialog drawn", || grab(&win.window).is_ok_and(|img| count(&img, ORANGE) > 0));
        assert!(System.windows().iter().all(|f| f.window.id != dialog), "the dialog is not in the list, its window is");
        let img = grab(&win.window).unwrap();
        let (at, its) = (frame(win.window.id), frame(dialog));
        let place = (its.0 - at.0, its.1 - at.1, its.2 - at.0, its.3 - at.1);
        let orange = bounds(&img, ORANGE).expect("the dialog's colour in the capture");
        assert_eq!(orange, (place.0.max(0), place.1.max(0), place.2.min(640), place.3.min(480)), "at its place");
        assert!(count(&img, BLUE) > pixels(&img) / 3, "and the window around it");

        // A click in the dialog says its window (R4).
        let (_o, other) = d.open("fenetre", "Autre", (900, 500, 260, 220), &[GREY]);
        click(center(other.window.id));
        let waiting = std::thread::spawn(guide::attendre_clic);
        std::thread::sleep(Duration::from_millis(600));
        click(center(dialog));
        assert_eq!(waiting.join().unwrap().map(|f| f.id), Ok(win.window.id));
        guide::fin();
    }

    /// R11: another program's window that makes itself a dialog of the chosen one is never drawn on
    /// its capture: the same process only.
    #[test]
    #[ignore = "an X11 desktop with a compositor (VM)"]
    fn bureau_another_programs_dialog_is_never_drawn() {
        compositing();
        let d = desk();
        let (_p, win) = d.open("fenetre", "Compta", (80, 80, 640, 480), &[BLUE]);
        let (_i, titre) = d.start("intrus", "Intrus", (240, 200, 320, 220), &[hex(MAGENTA), win.window.id.to_string()]);
        until("the other program's dialog over it", || any_titled(&titre).is_some_and(|i| above(i, win.window.id)));
        std::thread::sleep(Duration::from_millis(500));
        let img = grab(&win.window).unwrap();
        assert_eq!(count(&img, MAGENTA), 0, "not a pixel of the other program's window");
        assert!(count(&img, BLUE) > pixels(&img) * 95 / 100);
    }

    /// A minimized window is marked in the list and never captured; once opened again, it is.
    #[test]
    #[ignore = "an X11 desktop with a compositor (VM)"]
    fn bureau_a_minimized_window() {
        compositing();
        let d = desk();
        let (_p, win) = d.open("fenetre", "Reduite", (80, 80, 500, 400), &[BLUE]);
        guide::fenetres().unwrap();
        guide::choisir(Some(win.window.id)).unwrap();
        xdotool(&["windowminimize", "--sync", &win.window.id.to_string()]);
        until("minimized", || System.find(win.window.id).is_some_and(|f| f.reduite));
        assert!(System.windows().iter().any(|f| f.window.id == win.window.id && f.reduite), "marked in the list");
        assert_eq!(grab(&win.window).err(), Some(Rate::Reduite));
        assert_eq!(guide::regarder(), Err(GuideError::FenetreReduite));
        xdotool(&["windowactivate", "--sync", &win.window.id.to_string()]);
        until("open again", || System.find(win.window.id).is_some_and(|f| !f.reduite));
        until("drawn again", || grab(&win.window).is_ok_and(|img| count(&img, BLUE) > pixels(&img) * 95 / 100));
        assert!(!guide::regarder().unwrap().noir);
        guide::fin();
    }

    /// A window partly off the screen: X reads only what is on it, never a pixel beside it.
    #[test]
    #[ignore = "an X11 desktop with a compositor (VM)"]
    fn bureau_a_window_partly_off_the_screen() {
        compositing();
        let d = desk();
        let (_p, win) = d.open("fenetre", "Bord", (80, 80, 500, 400), &[BLUE]);
        let (sw, sh) = {
            let c = conn().unwrap();
            // SAFETY: an open display and its screen.
            unsafe { ((c.x.XDisplayWidth)(c.d, c.screen), (c.x.XDisplayHeight)(c.d, c.screen)) }
        };
        // Past the bottom right corner: window managers keep a window's top left on the screen.
        xdotool(&["windowmove", &win.window.id.to_string(), &(sw - 220).to_string(), &(sh - 160).to_string()]);
        until("moved past the corner", || {
            let (_, _, r, b) = frame(win.window.id);
            r > sw && b > sh
        });
        let (l, t, r, b) = frame(win.window.id);
        eprintln!("frame after the move: {:?}, screen {sw}x{sh}", (l, t, r, b));
        let img = grab(&win.window).unwrap();
        assert_eq!((img.w as i32, img.h as i32), (r.min(sw) - l.max(0), b.min(sh) - t.max(0)), "the part on the screen");
        assert_eq!(count(&img, BLUE), pixels(&img), "all of it the window's");
    }

    /// R4: the window clicked in is the one chosen; Bibo's own windows are never offered.
    #[test]
    #[ignore = "an X11 desktop with a compositor (VM)"]
    fn bureau_the_click_says_the_window() {
        compositing();
        let d = desk();
        let (_a, other) = d.open("fenetre", "Autre", (700, 420, 300, 260), &[GREY]);
        let (_b, win) = d.open("fenetre", "Clic", (60, 60, 500, 400), &[BLUE]);
        click(center(other.window.id));
        until("the other window in front", || foreground().is_some_and(|f| f.window.id == other.window.id));
        let waiting = std::thread::spawn(guide::attendre_clic);
        std::thread::sleep(Duration::from_millis(600));
        click(center(win.window.id));
        let clicked = waiting.join().unwrap().unwrap();
        assert_eq!((clicked.id, clicked.app.as_str(), clicked.terminal), (win.window.id, "Atelier", false));
        guide::choisir(Some(clicked.id)).unwrap();
        assert!(!guide::regarder().unwrap().noir);
        assert_eq!(guide::choisir(Some(other.window.id)), Err(GuideError::FenetreIntrouvable), "only the window clicked");

        // A window of this process, as Bibo's are: never in the list, never described.
        let own = {
            let c = conn().unwrap();
            // SAFETY: a plain window of the test's own, its pid set as GTK sets Bibo's, destroyed below.
            unsafe {
                let w = (c.x.XCreateSimpleWindow)(c.d, c.root, 300, 300, 300, 200, 0, 0, 0x1e88e5);
                let pid: [c_ulong; 1] = [c_ulong::from(std::process::id())];
                (c.x.XChangeProperty)(c.d, w, c.atoms.pid, xlib::XA_CARDINAL, 32, xlib::PropModeReplace, pid.as_ptr().cast(), 1);
                let name = b"Bibo, ton guide";
                (c.x.XChangeProperty)(c.d, w, c.atoms.name, c.atoms.utf8, 8, xlib::PropModeReplace, name.as_ptr(), name.len() as c_int);
                (c.x.XMapWindow)(c.d, w);
                (c.x.XSync)(c.d, 0);
                w
            }
        };
        until("the window manager has it", || conn().unwrap().clients().contains(&own));
        assert!(describe(wide(own)).is_none());
        assert!(System.windows().iter().all(|f| f.window.pid != std::process::id()));
        {
            let c = conn().unwrap();
            // SAFETY: our window.
            unsafe {
                (c.x.XDestroyWindow)(c.d, own);
                (c.x.XSync)(c.d, 0);
            }
        }
        guide::fin();
    }

    /// R6: a terminal is marked, and the guide will not take it.
    #[test]
    #[ignore = "an X11 desktop with a compositor (VM)"]
    fn bureau_a_terminal_is_marked_and_refused() {
        compositing();
        let d = desk();
        let titre = format!("Terminal {}", d.nonce);
        let _t = Program(Command::new("xterm").args(["-T", &titre, "-e", "sleep", "600"]).spawn().expect("xterm on this desktop"));
        let term = listed_as(|f| f.titre == titre);
        eprintln!("terminal: {} ({})", term.nom, term.window.app);
        assert!(term.terminal);
        assert!(guide::fenetres().unwrap().iter().any(|f| f.id == term.window.id && f.terminal));
        assert_eq!(guide::choisir(Some(term.window.id)), Err(GuideError::Terminal));
        guide::fin();
    }

    /// R22: a window closed is not found again, its id checked before each look.
    #[test]
    #[ignore = "an X11 desktop with a compositor (VM)"]
    fn bureau_a_closed_window_is_not_found() {
        compositing();
        let d = desk();
        let (p, win) = d.open("fenetre", "Fermee", (80, 80, 500, 400), &[BLUE]);
        guide::fenetres().unwrap();
        guide::choisir(Some(win.window.id)).unwrap();
        assert!(guide::regarder().is_ok());
        // The same id under another process or another program is not the window chosen (R22).
        assert_eq!(grab(&Window { pid: win.window.pid + 1, ..win.window.clone() }).err(), Some(Rate::Introuvable));
        assert_eq!(grab(&Window { app: "piege.Piege".into(), ..win.window.clone() }).err(), Some(Rate::Introuvable));
        drop(p);
        until("closed", || !conn().unwrap().clients().contains(&(win.window.id as xlib::Window)));
        assert_eq!(grab(&win.window).err(), Some(Rate::Introuvable));
        assert_eq!(guide::regarder(), Err(GuideError::FenetreIntrouvable));
        guide::fin();
    }

    /// A window drawn all black (as one a program keeps from captures would be): it never leaves.
    #[test]
    #[ignore = "an X11 desktop with a compositor (VM)"]
    fn bureau_a_black_window_never_leaves() {
        compositing();
        let d = desk();
        let (_p, win) = d.open("fenetre", "Noire", (80, 80, 500, 400), &[[0, 0, 0]]);
        until("drawn", || grab(&win.window).is_ok());
        guide::fenetres().unwrap();
        guide::choisir(Some(win.window.id)).unwrap();
        assert_eq!(guide::regarder(), Err(GuideError::CaptureNoire));
        guide::fin();
    }

    /// Wayland, as the session says it (here, its socket appearing): no capture, the user pastes one.
    #[test]
    #[ignore = "an X11 desktop with a compositor (VM)"]
    fn bureau_wayland_means_paste_mode() {
        compositing();
        let d = desk();
        let (_p, win) = d.open("fenetre", "Wayland", (80, 80, 500, 400), &[BLUE]);
        // SAFETY (edition 2021: safe): these tests run one at a time (--test-threads=1).
        std::env::set_var("WAYLAND_DISPLAY", "wayland-0");
        let (none, list, look) = (System.available(), guide::fenetres(), grab(&win.window).err());
        std::env::remove_var("WAYLAND_DISPLAY");
        assert!(!none);
        assert_eq!(list, Err(GuideError::CaptureIndisponible));
        assert_eq!(look, Some(Rate::Indisponible));
        assert!(System.available(), "X11 again");
    }

    /// R21: no compositing manager, no capture, whatever is asked: the user pastes one. What X gives
    /// instead, read here only to show it: the server blanks what another window covers (black under
    /// Xvfb, never that window's pixels), so a window under Bibo's would leave with a hole.
    #[test]
    #[ignore = "an X11 desktop without a compositor (VM)"]
    fn bureau_sans_compositeur_no_capture() {
        assert!(!System.available(), "this test wants no compositing manager");
        let d = desk();
        let (_p, titre) = d.start("fenetre", "Factures", (80, 80, 640, 480), &[hex(BLUE)]);
        until("shown", || any_titled(&titre).is_some());
        let win = System.find(any_titled(&titre).unwrap()).expect("listed all the same");
        assert_eq!(guide::fenetres(), Err(GuideError::CaptureIndisponible));
        assert_eq!(guide::attendre_clic(), Err(GuideError::CaptureIndisponible));
        assert_eq!(guide::choisir(Some(win.window.id)), Err(GuideError::CaptureIndisponible));
        assert_eq!(guide::regarder(), Err(GuideError::CaptureIndisponible));
        assert_eq!(grab(&win.window).err(), Some(Rate::Indisponible));

        let w = win.window.id as xlib::Window;
        let raw = || {
            let c = conn().unwrap();
            c.image(w, &c.attributes(w).unwrap()).unwrap().0
        };
        let alone = raw();
        assert_eq!(count(&alone, BLUE), pixels(&alone), "uncovered, X reads it whole");
        let (_cover, cover) = d.start("dessus", "Cache", (40, 40, 400, 300), &[hex(MAGENTA)]);
        until("the magenta window over it", || any_titled(&cover).is_some_and(|c| above(c, win.window.id)));
        std::thread::sleep(Duration::from_millis(500));
        let under = raw();
        let black = under.px.chunks_exact(4).filter(|p| p[..3] == [0, 0, 0]).count();
        eprintln!(
            "without a compositor, under another window: {} magenta, {black} black, {} blue of {}",
            count(&under, MAGENTA),
            count(&under, BLUE),
            pixels(&under)
        );
        assert_eq!(count(&under, MAGENTA), 0, "never the other window's pixels");
        assert!(black > pixels(&under) / 4, "a hole where it is covered: {black}");
    }

    /// R21: the compositor gone during the session, the next look captures nothing.
    #[test]
    #[ignore = "an X11 desktop without a compositor (VM), picom installed"]
    fn bureau_sans_compositeur_one_that_leaves() {
        assert!(!System.available(), "this test starts its own compositing manager");
        let d = desk();
        let mut picom = Program(Command::new("picom").args(["--backend", "xrender"]).stdout(Stdio::null()).stderr(Stdio::null()).spawn().expect("picom"));
        until("the compositor", || System.available());
        let (_p, win) = d.open("fenetre", "Factures", (80, 80, 640, 480), &[BLUE]);
        guide::fenetres().unwrap();
        guide::choisir(Some(win.window.id)).unwrap();
        assert!(!guide::regarder().unwrap().noir);
        let _ = picom.0.kill();
        let _ = picom.0.wait();
        until("the compositor gone", || !System.available());
        assert_eq!(grab(&win.window).err(), Some(Rate::Indisponible));
        assert_eq!(guide::regarder(), Err(GuideError::CaptureIndisponible));
        guide::fin();
    }
}
