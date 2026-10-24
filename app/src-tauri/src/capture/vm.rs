//! The guide v2 on a Windows desktop, a VM of the CI's (Server 2022 and 2025): the whole screen captured
//! (ecran.rs), Bibo's overlay (calque.rs) and the change detection (veille.rs). Windows of known colours
//! (tests/fixtures/guide/atelier.cs, compiled there by Windows' own csc), a window of this process as
//! Bibo's pastille, the overlay made by the app's own code in a Tauri app run by this test. Pixels are
//! counted in memory; nothing is written but that program, in a folder of the test's.
//!
//! One test per `cargo test` run (`-- --ignored --exact capture::vm::<name> --test-threads=1`): a process
//! has one Tauri event loop. `vm_verrouille` locks the session: the job's last step.

use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{mpsc, Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use windows::core::{w, BOOL};
use windows::Win32::Foundation::{COLORREF, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM};
use windows::Win32::Graphics::Gdi::{CreateSolidBrush, ChangeDisplaySettingsExW, EnumDisplaySettingsW, CDS_TYPE, DEVMODEW, DISP_CHANGE_SUCCESSFUL, DM_PELSHEIGHT, DM_PELSWIDTH, ENUM_CURRENT_SETTINGS, ENUM_DISPLAY_SETTINGS_MODE};
use windows::Win32::System::Threading::GetCurrentThreadId;
use windows::Win32::UI::Input::KeyboardAndMouse::{
    SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, INPUT_MOUSE, KEYBDINPUT, KEYBD_EVENT_FLAGS, KEYEVENTF_KEYUP, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_MOVE, MOUSEINPUT, MOUSE_EVENT_FLAGS, VIRTUAL_KEY, VK_ESCAPE, VK_LWIN, VK_SHIFT,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DefWindowProcW, DestroyWindow, DispatchMessageW, EnumChildWindows, EnumWindows, GetAncestor, GetForegroundWindow, GetMessageW,
    GetWindowDisplayAffinity, GetWindowLongW, GetWindowRect, GetWindowTextW, IsWindow, IsWindowVisible, PostThreadMessageW, RegisterClassW, SetCursorPos,
    SetForegroundWindow, SetWindowDisplayAffinity, SetWindowPos, ShowWindow, TranslateMessage, WindowFromPoint, ES_PASSWORD, GA_ROOT, GWL_STYLE, HWND_TOP,
    MSG, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, SW_SHOWMINNOACTIVE, WDA_EXCLUDEFROMCAPTURE, WDA_NONE, WINDOW_DISPLAY_AFFINITY, WM_QUIT, WNDCLASSW,
    WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_POPUP, WS_VISIBLE,
};

use super::bureau::{Program, BLUE, GREEN, GREY, MAGENTA, ORANGE};
use super::ecran::{self, actif, bureau_ouvert, capturer, inactif, ChampsClassiques, Ecran, RectEcran, Refus};
use super::veille::{Bilan, Veilleur, ECART_MIN};
use super::win::{class, frame, id_of, pid_of, visible, Physical};
use crate::calque::{self, Demande, Montre, Ton};
use crate::shot::Rgba;

const WAIT: Duration = Duration::from_secs(30);
/// Bibo's ring and bubble (calque.rs, Ton::Normal).
const FONCE: [u8; 3] = [0xc2, 0x3a, 0x6e];
const ENCRE: [u8; 3] = [0x2e, 0x22, 0x20];

fn mesure(test: &str, cle: impl std::fmt::Display, valeur: impl std::fmt::Display) {
    println!("MESURE {test} {cle}={valeur}");
}

fn sleep(ms: u64) {
    std::thread::sleep(Duration::from_millis(ms));
}

fn within<T>(limit: Duration, mut get: impl FnMut() -> Option<T>) -> Option<T> {
    let start = Instant::now();
    loop {
        if let Some(v) = get() {
            return Some(v);
        }
        if start.elapsed() > limit {
            return None;
        }
        sleep(50);
    }
}

fn until<T>(what: &str, get: impl FnMut() -> Option<T>) -> T {
    within(WAIT, get).unwrap_or_else(|| panic!("{what}: still not, after {WAIT:?}"))
}

fn centile(v: &[f32], p: f32) -> f32 {
    let mut v = v.to_vec();
    v.sort_by(|a, b| a.total_cmp(b));
    v.get(((v.len() as f32 - 1.0) * p).round().max(0.0) as usize).copied().unwrap_or(0.0)
}

fn rect(r: RECT) -> RectEcran {
    RectEcran { x: r.left, y: r.top, largeur: (r.right - r.left).max(0) as u32, hauteur: (r.bottom - r.top).max(0) as u32 }
}

fn centre(r: &RectEcran) -> (i32, i32) {
    (r.x + r.largeur as i32 / 2, r.y + r.hauteur as i32 / 2)
}

fn hwnd(id: u64) -> HWND {
    HWND(id as usize as *mut std::ffi::c_void)
}

fn window_rect(h: HWND) -> RectEcran {
    let mut r = RECT::default();
    // SAFETY: a RECT for the call.
    let _ = unsafe { GetWindowRect(h, &mut r) };
    rect(r)
}

fn title(h: HWND) -> String {
    let mut buf = [0u16; 512];
    // SAFETY: the buffer's length goes with it.
    let n = unsafe { GetWindowTextW(h, &mut buf) }.clamp(0, 512) as usize;
    String::from_utf16_lossy(&buf[..n])
}

fn top_windows() -> Vec<HWND> {
    unsafe extern "system" fn each(h: HWND, all: LPARAM) -> BOOL {
        // SAFETY: the Vec lent below, alive for the whole call.
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
    // SAFETY: as above.
    let _ = unsafe { EnumChildWindows(Some(parent), Some(each), LPARAM(&mut all as *mut Vec<HWND> as isize)) };
    all
}

fn devant() -> HWND {
    // SAFETY: no argument.
    unsafe { GetForegroundWindow() }
}

fn dessus_a((x, y): (i32, i32)) -> HWND {
    // SAFETY: plain values.
    unsafe { GetAncestor(WindowFromPoint(POINT { x, y }), GA_ROOT) }
}

fn z(h: HWND) -> Option<usize> {
    top_windows().iter().position(|&w| w == h)
}

fn send(inputs: &[INPUT]) {
    // SAFETY: the inputs live for the call.
    let n = unsafe { SendInput(inputs, std::mem::size_of::<INPUT>() as i32) };
    assert_eq!(n as usize, inputs.len(), "SendInput");
}

fn souris(flags: MOUSE_EVENT_FLAGS) -> INPUT {
    INPUT { r#type: INPUT_MOUSE, Anonymous: INPUT_0 { mi: MOUSEINPUT { dwFlags: flags, ..Default::default() } } }
}

fn clavier(vk: VIRTUAL_KEY, up: bool) -> INPUT {
    let flags = if up { KEYEVENTF_KEYUP } else { KEYBD_EVENT_FLAGS(0) };
    INPUT { r#type: INPUT_KEYBOARD, Anonymous: INPUT_0 { ki: KEYBDINPUT { wVk: vk, dwFlags: flags, ..Default::default() } } }
}

fn pointer((x, y): (i32, i32)) {
    // SAFETY: plain values.
    unsafe { SetCursorPos(x, y) }.unwrap();
}

fn clic(at: (i32, i32)) {
    assert_ne!(class(dessus_a(at)), "ConsoleWindowClass", "a click about to land in a console");
    pointer(at);
    send(&[souris(MOUSEEVENTF_LEFTDOWN), souris(MOUSEEVENTF_LEFTUP)]);
}

fn touche(vk: VIRTUAL_KEY) {
    send(&[clavier(vk, false), clavier(vk, true)]);
}

/// Pixels of `c` (give or take 3) in a zone of the screen, on an image of `ecran`.
fn compte(img: &Rgba, ecran: &RectEcran, zone: &RectEcran, c: [u8; 3]) -> u64 {
    let Some(z) = zone.sur(ecran) else { return 0 };
    let mut n = 0;
    for y in z.y..(z.y + z.hauteur).min(img.h) {
        for x in z.x..(z.x + z.largeur).min(img.w) {
            let p = &img.px[((y * img.w + x) * 4) as usize..][..3];
            if (0..3).all(|i| p[i].abs_diff(c[i]) <= 3) {
                n += 1;
            }
        }
    }
    n
}

/// The box around the pixels of `c` in a zone of the screen, in the screen's pixels.
fn bornes(img: &Rgba, ecran: &RectEcran, zone: &RectEcran, c: [u8; 3]) -> Option<RectEcran> {
    let z = zone.sur(ecran)?;
    let mut b: Option<(i64, i64, i64, i64)> = None;
    for y in z.y..(z.y + z.hauteur).min(img.h) {
        for x in z.x..(z.x + z.largeur).min(img.w) {
            let p = &img.px[((y * img.w + x) * 4) as usize..][..3];
            if (0..3).all(|i| p[i].abs_diff(c[i]) <= 3) {
                let (x, y) = (i64::from(x), i64::from(y));
                b = Some(b.map_or((x, y, x + 1, y + 1), |(l, t, r, bt)| (l.min(x), t.min(y), r.max(x + 1), bt.max(y + 1))));
            }
        }
    }
    b.map(|(l, t, r, bt)| RectEcran { x: ecran.x + l as i32, y: ecran.y + t as i32, largeur: (r - l) as u32, hauteur: (bt - t) as u32 })
}

fn ecart(a: &RectEcran, b: &RectEcran) -> i64 {
    [i64::from(a.x - b.x), i64::from(a.y - b.y), a.droite() - b.droite(), a.bas() - b.bas()].iter().map(|v| v.abs()).max().unwrap_or(0)
}

fn atelier() -> &'static PathBuf {
    static EXE: OnceLock<(tempfile::TempDir, PathBuf)> = OnceLock::new();
    &EXE.get_or_init(|| {
        let dir = tempfile::tempdir().unwrap();
        let windir = PathBuf::from(std::env::var("WINDIR").unwrap_or_else(|_| "C:\\Windows".into()));
        let csc = windir.join("Microsoft.NET").join("Framework64").join("v4.0.30319").join("csc.exe");
        let exe = dir.path().join("atelier.exe");
        let source = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests").join("fixtures").join("guide").join("atelier.cs");
        let out = Command::new(&csc)
            .args(["/nologo", "/target:winexe", "/optimize+", "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll"])
            .arg(format!("/out:{}", exe.display()))
            .arg(&source)
            .output()
            .unwrap();
        assert!(out.status.success() && exe.is_file(), "csc: {}", String::from_utf8_lossy(&out.stdout));
        (dir, exe)
    })
    .1
}

struct Desk {
    nonce: String,
}

/// The runner's own console lies on the desktop: minimized, so that no click lands in it.
fn desk() -> Desk {
    static N: AtomicUsize = AtomicUsize::new(0);
    for h in top_windows() {
        if visible(h) && class(h) == "ConsoleWindowClass" {
            // SAFETY: another program's window, minimized, not activated.
            let _ = unsafe { ShowWindow(h, SW_SHOWMINNOACTIVE) };
        }
    }
    atelier();
    Desk { nonce: format!("{}-{}", std::process::id(), N.fetch_add(1, Ordering::SeqCst)) }
}

impl Desk {
    fn open(&self, mode: &str, what: &str, (x, y, w, h): (i32, i32, i32, i32), color: [u8; 3]) -> (Program, HWND) {
        let titre = format!("Atelier {what} {}", self.nonce);
        let child = Command::new(atelier())
            .args([mode, &titre, &x.to_string(), &y.to_string(), &w.to_string(), &h.to_string()])
            .arg(format!("{:02x}{:02x}{:02x}", color[0], color[1], color[2]))
            .spawn()
            .unwrap();
        let pid = child.id();
        let p = Program(child);
        let win = until(&format!("« {titre} »"), || top_windows().into_iter().find(|&h| visible(h) && pid_of(h) == pid && title(h).starts_with(&titre)));
        // SAFETY: a window of ours, moved up in the order only.
        let _ = unsafe { SetWindowPos(win, Some(HWND_TOP), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE) };
        (p, win)
    }
}

/// Brought in front by a click on its background, tried again while it is not yet listening.
fn au_premier_plan(win: HWND, at: (i32, i32)) {
    for _ in 0..5 {
        clic(at);
        if within(Duration::from_secs(2), || (devant() == win).then_some(())).is_some() {
            return;
        }
    }
    panic!("never in front; in front {} « {} »", class(devant()), title(devant()));
}

/// Bibo's pastille, as in the guide: a small window of this process, of a known colour, that the user may
/// click. Not kept out of captures (R2): Bibo blacks it in its own image.
struct Pastille {
    id: u64,
    tid: u32,
    fil: Option<std::thread::JoinHandle<()>>,
}

impl Pastille {
    fn ouvrir(r: RectEcran, c: [u8; 3]) -> Pastille {
        unsafe extern "system" fn proc_(h: HWND, msg: u32, wp: WPARAM, lp: LPARAM) -> LRESULT {
            // SAFETY: a plain window's default behaviour.
            unsafe { DefWindowProcW(h, msg, wp, lp) }
        }
        let (tx, rx) = mpsc::channel();
        let fil = std::thread::spawn(move || {
            let _p = Physical::enter();
            // SAFETY: a class of its own colour, registered by this thread; the window is destroyed on it.
            unsafe {
                let fond = CreateSolidBrush(COLORREF(u32::from(c[0]) | (u32::from(c[1]) << 8) | (u32::from(c[2]) << 16)));
                RegisterClassW(&WNDCLASSW { lpfnWndProc: Some(proc_), hbrBackground: fond, lpszClassName: w!("BiboPastilleTest"), ..Default::default() });
                let h = CreateWindowExW(
                    WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
                    w!("BiboPastilleTest"),
                    w!("Bibo pastille"),
                    WS_POPUP | WS_VISIBLE,
                    r.x,
                    r.y,
                    r.largeur as i32,
                    r.hauteur as i32,
                    None,
                    None,
                    None,
                    None,
                )
                .unwrap();
                tx.send((id_of(h), GetCurrentThreadId())).unwrap();
                let mut msg = MSG::default();
                while GetMessageW(&mut msg, None, 0, 0).as_bool() {
                    let _ = TranslateMessage(&msg);
                    DispatchMessageW(&msg);
                }
                let _ = DestroyWindow(h);
            }
        });
        let (id, tid) = rx.recv().unwrap();
        Pastille { id, tid, fil: Some(fil) }
    }
}

impl Drop for Pastille {
    fn drop(&mut self) {
        // SAFETY: the pastille's thread, told to end.
        let _ = unsafe { PostThreadMessageW(self.tid, WM_QUIT, WPARAM(0), LPARAM(0)) };
        if let Some(f) = self.fil.take() {
            let _ = f.join();
        }
    }
}

fn barre_des_taches() -> RectEcran {
    let tray = until("the taskbar", || top_windows().into_iter().find(|&h| class(h) == "Shell_TrayWnd"));
    window_rect(tray)
}

/// The app's own Tauri, run by this test on a thread of its own (one per process), without the
/// installer's window: the overlay is made by calque.rs as in the app.
fn appli() -> tauri::AppHandle {
    static APP: OnceLock<tauri::AppHandle> = OnceLock::new();
    APP.get_or_init(|| {
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            let mut ctx = tauri::generate_context!();
            ctx.config_mut().app.windows.clear();
            let app = tauri::Builder::default().any_thread().build(ctx).expect("tauri");
            tx.send(app.handle().clone()).unwrap();
            app.run(|_, e| {
                if let tauri::RunEvent::ExitRequested { api, .. } = e {
                    api.prevent_exit();
                }
            });
        });
        rx.recv_timeout(Duration::from_secs(60)).expect("the app's Tauri")
    })
    .clone()
}

/// The witness only: the overlay shown to captures (WDA_NONE) or kept out again, on its own thread.
fn temoin(app: &tauri::AppHandle, a: WINDOW_DISPLAY_AFFINITY) -> bool {
    let id = ecran::calque().fenetre.expect("the overlay");
    let (tx, rx) = mpsc::channel();
    app.run_on_main_thread(move || {
        // SAFETY: our window, on its thread.
        let _ = tx.send(unsafe { SetWindowDisplayAffinity(hwnd(id), a) }.is_ok());
    })
    .unwrap();
    rx.recv_timeout(Duration::from_secs(5)).unwrap_or(false)
}

fn affinite(id: u64) -> u32 {
    let mut a = 0;
    // SAFETY: a u32 for the call.
    let _ = unsafe { GetWindowDisplayAffinity(hwnd(id), &mut a) };
    a
}

/// The ring's outer box and its dark band's, from what the overlay says it draws (its four bands).
fn anneau_dessine(echelle: f64) -> (RectEcran, RectEcran) {
    let t = ecran::calque().dessin.clone();
    let bandes: Vec<&RectEcran> = t.iter().filter(|t| t.couleurs[0] == FONCE).map(|t| &t.zone).collect();
    assert_eq!(bandes.len(), 4, "the ring's four bands");
    let (x0, y0) = (bandes.iter().map(|r| r.x).min().unwrap(), bandes.iter().map(|r| r.y).min().unwrap());
    let (x1, y1) = (bandes.iter().map(|r| r.droite()).max().unwrap(), bandes.iter().map(|r| r.bas()).max().unwrap());
    let ext = RectEcran { x: x0, y: y0, largeur: (x1 - i64::from(x0)) as u32, hauteur: (y1 - i64::from(y0)) as u32 };
    let clair = (2.0 * echelle).round().max(1.0) as i32;
    (ext, ext.agrandi(-clair, -clair))
}

fn bulle_dessinee() -> RectEcran {
    ecran::calque().dessin.iter().find(|t| t.couleurs[0] == ENCRE).map(|t| t.zone).expect("the bubble")
}

/// The click count the window of mode « bouton » shows at the end of its title, and where the last one landed.
fn clics(win: HWND) -> (u32, String) {
    let t = title(win);
    let mut words = t.rsplit(' ');
    let n = words.next().and_then(|w| w.parse().ok()).unwrap_or(0);
    (n, words.next().unwrap_or("").to_string())
}

fn clic_recu(win: HWND, at: (i32, i32)) -> Option<String> {
    let (n0, _) = clics(win);
    clic(at);
    within(Duration::from_secs(3), || {
        let (n, ou) = clics(win);
        (n > n0).then_some(ou)
    })
}

// ── The screen's resolution and scale, changed live as the Settings app does, put back after ──

fn devmode(i: ENUM_DISPLAY_SETTINGS_MODE) -> Option<DEVMODEW> {
    let mut d = DEVMODEW { dmSize: std::mem::size_of::<DEVMODEW>() as u16, ..Default::default() };
    // SAFETY: a DEVMODEW of its size.
    unsafe { EnumDisplaySettingsW(None, i, &mut d) }.as_bool().then_some(d)
}

fn poser_resolution(w: u32, h: u32) -> bool {
    let Some(mut d) = devmode(ENUM_CURRENT_SETTINGS) else { return false };
    d.dmPelsWidth = w;
    d.dmPelsHeight = h;
    d.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT;
    // SAFETY: a DEVMODEW read from the system, two fields changed; not saved in the registry.
    unsafe { ChangeDisplaySettingsExW(None, Some(&d), None, CDS_TYPE(0), None) == DISP_CHANGE_SUCCESSFUL }
}

/// The DisplayConfig packets -3 (get) and -4 (set) Windows' own settings use, undocumented (spike W3).
mod echelle {
    use std::mem::size_of;
    use windows::Win32::Devices::Display::{
        DisplayConfigGetDeviceInfo, DisplayConfigSetDeviceInfo, GetDisplayConfigBufferSizes, QueryDisplayConfig, DISPLAYCONFIG_DEVICE_INFO_HEADER,
        DISPLAYCONFIG_DEVICE_INFO_TYPE, DISPLAYCONFIG_MODE_INFO, DISPLAYCONFIG_PATH_INFO, QDC_ONLY_ACTIVE_PATHS,
    };
    use windows::Win32::Foundation::LUID;

    const PAS: [u32; 12] = [100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500];

    #[repr(C)]
    struct Lire {
        header: DISPLAYCONFIG_DEVICE_INFO_HEADER,
        min: i32,
        cur: i32,
        max: i32,
    }

    #[repr(C)]
    struct Poser {
        header: DISPLAYCONFIG_DEVICE_INFO_HEADER,
        rel: i32,
    }

    fn source() -> Option<(LUID, u32)> {
        let (mut np, mut nm) = (0u32, 0u32);
        // SAFETY: counts, then arrays of those sizes.
        unsafe { GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, &mut np, &mut nm) }.ok().ok()?;
        let mut paths = vec![DISPLAYCONFIG_PATH_INFO::default(); np as usize];
        let mut modes = vec![DISPLAYCONFIG_MODE_INFO::default(); nm as usize];
        unsafe { QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, &mut np, paths.as_mut_ptr(), &mut nm, modes.as_mut_ptr(), None) }.ok().ok()?;
        paths.first().map(|p| (p.sourceInfo.adapterId, p.sourceInfo.id))
    }

    /// (recommended, current, largest) scale of the first screen, in percent.
    pub fn lire() -> Option<(u32, u32, u32)> {
        let (adapter, id) = source()?;
        let mut get = Lire {
            header: DISPLAYCONFIG_DEVICE_INFO_HEADER { r#type: DISPLAYCONFIG_DEVICE_INFO_TYPE(-3), size: size_of::<Lire>() as u32, adapterId: adapter, id },
            min: 0,
            cur: 0,
            max: 0,
        };
        // SAFETY: the packet's size in its header.
        if unsafe { DisplayConfigGetDeviceInfo(&mut get.header) } != 0 {
            return None;
        }
        let rec = get.min.unsigned_abs() as usize;
        let at = |rel: i32| PAS.get((rec as i32 + rel).max(0) as usize).copied();
        Some((at(0)?, at(get.cur)?, at(get.max)?))
    }

    pub fn poser(pct: u32) -> bool {
        let (Some((adapter, id)), Some((rec, _, _))) = (source(), lire()) else { return false };
        let (Some(want), Some(base)) = (PAS.iter().position(|&p| p == pct), PAS.iter().position(|&p| p == rec)) else { return false };
        let set = Poser {
            header: DISPLAYCONFIG_DEVICE_INFO_HEADER { r#type: DISPLAYCONFIG_DEVICE_INFO_TYPE(-4), size: size_of::<Poser>() as u32, adapterId: adapter, id },
            rel: want as i32 - base as i32,
        };
        // SAFETY: as above.
        unsafe { DisplayConfigSetDeviceInfo(&set.header) == 0 }
    }
}

/// While it lives, the screen at 1920 × 1080 (the spec's measure: a dialog of 400 × 200 is 3.9 % of it),
/// when the VM offers it; and at the scale asked for. Both put back on drop.
struct Ecrans {
    avant: (u32, u32),
}

impl Ecrans {
    fn hd(test: &str) -> Ecrans {
        let d = devmode(ENUM_CURRENT_SETTINGS).unwrap();
        let avant = (d.dmPelsWidth, d.dmPelsHeight);
        let posee = avant == (1920, 1080) || poser_resolution(1920, 1080);
        mesure(test, "resolution", format!("{avant:?} -> 1920x1080 {posee}"));
        sleep(3000);
        Ecrans { avant }
    }

    fn echelle(&self, test: &str, pct: u32) -> bool {
        let ok = echelle::poser(pct) && within(Duration::from_secs(15), || (actif().map(|e| (e.echelle * 100.0).round() as u32) == Some(pct)).then_some(())).is_some();
        mesure(test, format!("echelle_{pct}"), format!("{ok} {:?}", echelle::lire()));
        sleep(3000);
        ok
    }
}

impl Drop for Ecrans {
    fn drop(&mut self) {
        if let Some((rec, _, _)) = echelle::lire() {
            echelle::poser(rec);
        }
        poser_resolution(self.avant.0, self.avant.1);
        sleep(2000);
    }
}

// ── The capture ──

/// The whole screen leaves, another program's window included (D2 b); Bibo's pastille and the password
/// field are black in it, and only them; Teams still sees both (the witness); 1 280 px sent of 1 920.
#[test]
#[ignore = "a Windows desktop (VM); changes the screen's resolution, put back after"]
fn vm_capture_ecran_entier() {
    const T: &str = "capture";
    let _p = Physical::enter();
    let _ecrans = Ecrans::hd(T);
    let d = desk();
    let (_a, appli) = d.open("fenetre", "Appli", (60, 60, 600, 400), BLUE);
    let (_b, autre) = d.open("fenetre", "Autre", (700, 100, 280, 240), MAGENTA);
    let (_c, secret) = d.open("secret", "Secret", (60, 480, 400, 200), BLUE);
    let mdp = until("the password field", || {
        children(secret).into_iter().find(|&c| {
            // SAFETY: read from the system.
            let style = unsafe { GetWindowLongW(c, GWL_STYLE) };
            style & ES_PASSWORD != 0
        })
    });
    let nom = until("the plain field", || children(secret).into_iter().find(|&c| c != mdp && class(c).to_ascii_lowercase().contains("edit")));
    let (champ, simple) = (window_rect(mdp), window_rect(nom));
    au_premier_plan(appli, (90, 120));
    let ecran: Ecran = actif().expect("the active window's screen");
    mesure(T, "ecran", format!("{:?}", ecran));
    let tb = barre_des_taches();
    let pr = RectEcran { x: ecran.rect.droite() as i32 - 260, y: tb.y - 90, largeur: 220, hauteur: 60 };
    let _pastille = Pastille::ouvrir(pr, GREEN);
    sleep(800);
    assert_eq!(actif(), Some(ecran), "the active window's screen, Bibo's pastille aside");

    let teams = ecran::lire_brut(&ecran.rect).unwrap();
    let autre_r = rect(frame(autre).unwrap());
    let (vert, orange) = (compte(&teams, &ecran.rect, &pr, GREEN), compte(&teams, &ecran.rect, &champ, ORANGE));
    let magenta_vu = compte(&teams, &ecran.rect, &autre_r, MAGENTA);
    mesure(T, "temoin_pastille", format!("{vert}/{}", pr.aire()));
    mesure(T, "temoin_mot_de_passe", format!("{orange}/{}", champ.aire()));
    mesure(T, "temoin_autre_programme", format!("{magenta_vu}/{}", autre_r.aire()));
    assert!(vert * 10 >= pr.aire() * 9, "witness: the pastille on the screen, as Teams sees it");
    assert!(orange * 10 >= champ.aire() * 6, "witness: the password field on the screen");
    assert!(magenta_vu * 2 >= autre_r.aire(), "witness: another program's window on the screen");

    let c = capturer(&ecran, &ChampsClassiques).expect("the screen");
    assert_eq!((c.image.w, c.image.h), (ecran.rect.largeur, ecran.rect.hauteur));
    let vert = super::bureau::count(&c.image, GREEN);
    let orange = super::bureau::count(&c.image, ORANGE);
    let gris = compte(&c.image, &ecran.rect, &simple, GREY);
    let magenta = compte(&c.image, &ecran.rect, &autre_r, MAGENTA);
    mesure(T, "pastille_dans_l_image", vert);
    mesure(T, "mot_de_passe_dans_l_image", orange);
    mesure(T, "champ_simple_dans_l_image", format!("{gris}/{}", simple.aire()));
    mesure(T, "autre_programme_dans_l_image", format!("{magenta}/{magenta_vu}"));
    assert_eq!(vert, 0, "Bibo's pastille in the image");
    assert_eq!(orange, 0, "the password field in the image");
    assert!(compte(&c.image, &ecran.rect, &champ, [0, 0, 0]) * 10 >= champ.aire() * 9, "the password field black");
    assert!(gris * 10 >= simple.aire() * 6, "a plain field blacked");
    assert!(magenta * 100 >= magenta_vu * 99, "the whole screen: another program's window in it, as Teams sees it");
    let sent = ecran::a_envoyer(&c);
    mesure(T, "envoyee", format!("{}x{}", sent.w, sent.h));
    assert!(sent.w.max(sent.h) <= crate::shot::MAX_SENT);
    if ecran.rect.largeur > crate::shot::MAX_SENT {
        assert_eq!(sent.w, crate::shot::MAX_SENT, "the whole screen, made smaller");
    }

    let mut durees = Vec::new();
    for _ in 0..20 {
        let t = Instant::now();
        capturer(&ecran, &ChampsClassiques).unwrap();
        durees.push(t.elapsed().as_secs_f32() * 1000.0);
    }
    mesure(T, "capture_p50_ms", format!("{:.1}", centile(&durees, 0.5)));
    mesure(T, "capture_p90_ms", format!("{:.1}", centile(&durees, 0.9)));
}

// ── The overlay ──

/// One scale: the ring and the bubble around the button, out of every capture, where they should be on the
/// screen (the witness), clicks through them, back on top of what opens after, the focus never taken.
fn verifier_calque(app: &tauri::AppHandle, d: &Desk, t: &str, fautes: &mut Vec<String>) {
    let mut faute = |ok: bool, what: String| {
        if !ok {
            println!("FAUTE {t} {what}");
            fautes.push(format!("{t}: {what}"));
        }
    };
    let (_prog, win) = d.open("bouton", &format!("Calque{t}"), (100, 100, 600, 400), BLUE);
    let bouton = until("its button", || children(win).into_iter().find(|&c| title(c) == "Nouveau"));
    let b = window_rect(bouton);
    let f = window_rect(win);
    let fond = (f.droite() as i32 - 60, f.bas() as i32 - 60);
    au_premier_plan(win, fond);
    let ecran = actif().unwrap();
    mesure(t, "ecran", format!("{ecran:?}"));
    mesure(t, "bouton", format!("{b:?}"));
    let tb = barre_des_taches();
    let pr = RectEcran { x: ecran.rect.droite() as i32 - 260, y: tb.y - 90, largeur: 220, hauteur: 60 };

    // Shown while Bibo has the focus (the user just asked), then Bibo gives it back.
    let pastille = Pastille::ouvrir(pr, GREEN);
    sleep(300);
    clic(centre(&pr));
    faute(within(Duration::from_secs(5), || (devant() == hwnd(pastille.id)).then_some(())).is_some(), "Bibo's pastille never in front".into());
    let montre = calque::montrer(app, &Demande { cible: b, anneau: true, texte: "Clique sur Nouveau.".into(), ton: Ton::Normal });
    mesure(t, "montre", format!("{montre:?}"));
    faute(montre == Ok(Montre { anneau: true, bulle: true }), format!("shown: {montre:?}"));
    let id = ecran::calque().fenetre.expect("the overlay's window");
    mesure(t, "affinite_lue", format!("{:#x}", affinite(id)));
    faute(affinite(id) == 0x11, "the affinity read back".into());
    sleep(2000);
    faute(devant() == hwnd(pastille.id), format!("the overlay took the focus: {}", class(devant())));
    // SAFETY: this process is in front.
    let _ = unsafe { SetForegroundWindow(win) };
    faute(within(Duration::from_secs(5), || (devant() == win).then_some(())).is_some(), "the app not back in front".into());
    drop(pastille);
    sleep(300);

    let (ext, sombre) = anneau_dessine(ecran.echelle);
    let bulle = bulle_dessinee();
    // Less than the bubble's gap around the ring: the bubble's border is of the ring's dark colour.
    let autour = ext.agrandi(8, 8);
    let bande = (2 * (u64::from(sombre.largeur) + u64::from(sombre.hauteur))) * (4.0 * ecran.echelle).round() as u64;
    let teams = ecran::lire_brut(&ecran.rect).unwrap();
    let (a0, b0) = (compte(&teams, &ecran.rect, &autour, FONCE), compte(&teams, &ecran.rect, &bulle, ENCRE));
    mesure(t, "anneau_capture_ecran", a0);
    mesure(t, "bulle_capture_ecran", b0);
    faute(a0 == 0 && b0 == 0, format!("the overlay in a copy of the screen: ring {a0}, bubble {b0}"));
    let c = capturer(&ecran, &ChampsClassiques);
    faute(c.is_ok(), format!("Bibo's capture refused: {:?}", c.as_ref().err()));
    let bleu = compte(&teams, &ecran.rect, &f.agrandi(-20, -40), BLUE);
    faute(bleu > 0, "the app under the overlay".into());

    // The witness: shown to captures for a moment, the ring and the bubble are there, at their place.
    faute(temoin(app, WDA_NONE), "witness: the affinity not set to none".into());
    let vu = within(Duration::from_secs(10), || {
        let img = ecran::lire_brut(&ecran.rect).ok()?;
        (compte(&img, &ecran.rect, &autour, FONCE) * 10 >= bande * 3).then_some(img)
    });
    let vu = vu.unwrap_or_else(|| ecran::lire_brut(&ecran.rect).unwrap());
    let (a1, b1) = (compte(&vu, &ecran.rect, &autour, FONCE), compte(&vu, &ecran.rect, &bulle, ENCRE));
    let vu_la = bornes(&vu, &ecran.rect, &autour, FONCE);
    let place = vu_la.map_or(i64::MAX, |r| ecart(&r, &sombre));
    // The ring's centre on the screen against the button's (P2b: 6 px at most, at 100, 125 and 150 %).
    let centre_px = vu_la.map_or(i64::MAX, |r| {
        let ((x0, y0), (x1, y1)) = (centre(&r), centre(&b));
        i64::from((x0 - x1).abs().max((y0 - y1).abs()))
    });
    mesure(t, "temoin_anneau", format!("{a1}/{bande}"));
    mesure(t, "temoin_bulle", format!("{b1}/{}", bulle.aire()));
    mesure(t, "anneau_ecart_px", place);
    mesure(t, "anneau_centre_bouton_px", centre_px);
    // Where the dark pixels around the ring lie, should the ring be off: inside its box, and around it.
    mesure(t, "anneau_attendu", format!("{ext:?}, bande sombre {sombre:?}"));
    mesure(t, "anneau_vu", format!("{vu_la:?}"));
    mesure(t, "fonce_hors_de_l_anneau", compte(&vu, &ecran.rect, &autour, FONCE) - compte(&vu, &ecran.rect, &ext, FONCE));
    mesure(t, "bulle_attendue", format!("{bulle:?}"));
    mesure(t, "bulle_vue", format!("{:?}", bornes(&vu, &ecran.rect, &bulle.agrandi(40, 40), ENCRE)));
    faute(a1 * 10 >= bande * 3, "witness: the ring on the screen".into());
    faute(b1 * 10 >= bulle.aire(), "witness: the bubble on the screen".into());
    faute(place <= 6, format!("the ring {place} px off"));
    faute(centre_px <= 6, format!("the ring's centre {centre_px} px from the button's"));
    let refusee = capturer(&ecran, &ChampsClassiques).err();
    mesure(t, "capture_avec_le_calque", format!("{refusee:?}"));
    faute(refusee == Some(Refus::Calque), "an image with the overlay in it not refused".into());
    faute(temoin(app, WDA_EXCLUDEFROMCAPTURE), "witness: the affinity not set back".into());
    sleep(500);
    let a2 = compte(&ecran::lire_brut(&ecran.rect).unwrap(), &ecran.rect, &autour, FONCE);
    faute(a2 == 0, format!("the ring in a copy of the screen, kept out again: {a2}"));

    // Clicks go through: inside the ring (transparent), and on the ring itself.
    let r1 = clic_recu(win, centre(&b));
    mesure(t, "clic_dans_l_anneau", format!("{r1:?}"));
    faute(r1.as_deref() == Some("clic-bouton"), "the button not clicked through the overlay".into());
    let sur = (sombre.x + 2, centre(&b).1);
    faute(dessus_a(sur) == win, format!("the ring catches the point: {}", class(dessus_a(sur))));
    let r2 = clic_recu(win, sur);
    mesure(t, "clic_sur_l_anneau", format!("{r2:?}"));
    faute(r2.as_deref() == Some("clic-fond"), "the click on the ring never reached the app".into());
    faute(devant() == win, "the app no longer in front".into());

    // A window always on top, opened after the overlay, over the ring: the overlay goes back over it.
    let zd = autour.agrandi(10, 10);
    let (_dessus, dw) = d.open("dessus", &format!("Dessus{t}"), (zd.x, zd.y, zd.largeur as i32, zd.hauteur as i32), MAGENTA);
    sleep(600);
    let mut au_dessus = 0;
    for _ in 0..10 {
        sleep(200);
        if z(hwnd(id)) < z(dw) {
            au_dessus += 1;
        }
    }
    mesure(t, "au_dessus_d_une_fenetre_toujours_devant_sur_10", au_dessus);
    faute(au_dessus == 10, format!("a window always on top over the overlay {} times in 10", 10 - au_dessus));
    temoin(app, WDA_NONE);
    sleep(500);
    let a3 = compte(&ecran::lire_brut(&ecran.rect).unwrap(), &ecran.rect, &autour, FONCE);
    temoin(app, WDA_EXCLUDEFROMCAPTURE);
    mesure(t, "temoin_anneau_sur_fenetre_toujours_devant", format!("{a3}/{bande}"));
    faute(a3 * 10 >= bande * 3, "the ring hidden by a window always on top".into());

    // Erased: nothing drawn, nothing searched.
    faute(calque::effacer(app).is_ok(), "erased".into());
    sleep(300);
    // SAFETY: a plain value.
    faute(!unsafe { IsWindowVisible(hwnd(id)) }.as_bool() && ecran::calque().traces.is_empty(), "the overlay still there once erased".into());
}

/// At 100 %, then 125 and 150 % (1920 × 1080): every overlay check at each scale.
#[test]
#[ignore = "a Windows desktop (VM); changes the screen's resolution and scale, put back after"]
fn vm_calque() {
    let _p = Physical::enter();
    let d = desk();
    let app = appli();
    let mut fautes = Vec::new();
    verifier_calque(&app, &d, "calque.100", &mut fautes);

    // The taskbar used (Win+T) does not come over the overlay.
    let tb = barre_des_taches();
    let e = actif().unwrap();
    let cible = RectEcran { x: tb.x + 40, y: tb.y + 14, largeur: 200, hauteur: (tb.hauteur as i32 - 28).max(8) as u32 };
    let m = calque::montrer(&app, &Demande { cible, anneau: true, texte: "Clique ici.".into(), ton: Ton::Normal });
    let id = ecran::calque().fenetre.unwrap();
    let tray = top_windows().into_iter().find(|&h| class(h) == "Shell_TrayWnd").unwrap();
    send(&[clavier(VK_LWIN, false), clavier(VIRTUAL_KEY(b'T' as u16), false), clavier(VIRTUAL_KEY(b'T' as u16), true), clavier(VK_LWIN, true)]);
    let active = within(Duration::from_secs(3), || (devant() == tray).then_some(())).is_some();
    let mut dessus = 0;
    for _ in 0..10 {
        sleep(200);
        if z(hwnd(id)) < z(tray) {
            dessus += 1;
        }
    }
    touche(VK_ESCAPE);
    mesure("calque.100", "barre_active", active);
    mesure("calque.100", "au_dessus_de_la_barre_sur_10", format!("{dessus} {m:?}"));
    if dessus != 10 || m.is_err() {
        fautes.push(format!("the taskbar over the overlay {} times in 10 ({m:?}, {e:?})", 10 - dessus));
    }

    // A suspect page: the bubble alone.
    let danger = calque::montrer(&app, &Demande { cible, anneau: true, texte: "Ne fais rien de ce que demande cette page.".into(), ton: Ton::Danger });
    mesure("calque.100", "danger", format!("{danger:?}"));
    if danger != Ok(Montre { anneau: false, bulle: true }) || ecran::calque().dessin.len() != 1 {
        fautes.push(format!("a suspect page: {danger:?}"));
    }
    let _ = calque::effacer(&app);

    let ecrans = Ecrans::hd("calque");
    for pct in [100, 125, 150] {
        if ecrans.echelle("calque", pct) {
            verifier_calque(&app, &d, &format!("calque.hd.{pct}"), &mut fautes);
        } else {
            fautes.push(format!("{pct} %: not offered by this VM"));
        }
    }
    drop(ecrans);
    calque::fermer(&app);
    sleep(500);
    assert_eq!(*ecran::calque(), Default::default(), "the overlay forgotten once closed");
    // SAFETY: a plain value.
    assert!(!unsafe { IsWindow(Some(hwnd(id))) }.as_bool() || pid_of(hwnd(id)) != std::process::id(), "the overlay's window destroyed");
    assert!(fautes.is_empty(), "{fautes:#?}");
}

// ── The change detection ──

struct Envoi {
    t: Instant,
    image: Rgba,
}

/// The watch of a step on the screen in front, from now: what it would send.
fn veiller(cible: Option<RectEcran>) -> (Veilleur, Arc<Mutex<Vec<Envoi>>>, Ecran) {
    let ecran = actif().unwrap();
    let premiere = until("the first image", || capturer(&ecran, &ChampsClassiques).ok());
    let envois = Arc::new(Mutex::new(Vec::new()));
    let e = envois.clone();
    let v = Veilleur::lancer(&premiere, cible, Arc::new(ChampsClassiques), move |c| e.lock().unwrap().push(Envoi { t: Instant::now(), image: c.image }));
    (v, envois, ecran)
}

fn bilan(test: &str, b: &Bilan) {
    mesure(test, "regards", b.regards);
    mesure(test, "regards_ou_l_ecran_bouge", b.bouge);
    mesure(test, "ecart_max_pct", format!("{:.2}", b.ecart_max * 100.0));
    mesure(test, "pauses", b.pauses);
    mesure(test, "calque", b.calque);
    mesure(test, "capture_p50_ms", format!("{:.1}", centile(&b.durees, 0.5)));
    mesure(test, "capture_p90_ms", format!("{:.1}", centile(&b.durees, 0.9)));
}

/// A real notification, as a program sends one (PowerShell's own app id): whether Windows shows it here.
fn notification() -> bool {
    let script = "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; \
        $x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); \
        $x.GetElementsByTagName('text').Item(0).AppendChild($x.CreateTextNode('Rappel')) > $null; \
        $x.GetElementsByTagName('text').Item(1).AppendChild($x.CreateTextNode('Une notification pendant le guide')) > $null; \
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show([Windows.UI.Notifications.ToastNotification]::new($x))";
    let lancee = Command::new("powershell.exe").args(["-NoProfile", "-NonInteractive", "-Command", script]).status().is_ok_and(|s| s.success());
    lancee && within(Duration::from_secs(5), || top_windows().into_iter().find(|&h| visible(h) && class(h) == "Windows.UI.Core.CoreWindow" && title(h).to_lowercase().contains("notification"))).is_some()
}

/// 60 s of a clock with seconds, a spinner, a moving bar, a caret, a video, the taskbar's clock, a
/// notification (a window always on top, the size of one, and a real one when Windows shows it), the mouse
/// moving all along: nothing sent.
#[test]
#[ignore = "a Windows desktop (VM); changes the screen's resolution, put back after"]
fn vm_veille_calme_60s() {
    const T: &str = "veille.calme";
    let _p = Physical::enter();
    let _ecrans = Ecrans::hd(T);
    let d = desk();
    let (_v, win) = d.open("vivant", "Vivant", (80, 80, 800, 560), BLUE);
    let (_video, _) = d.open("video", "Video", (1000, 120, 480, 270), GREY);
    let f = window_rect(win);
    au_premier_plan(win, (f.x + 300, f.y + 12));
    let bouton = until("its button", || children(win).into_iter().find(|&c| title(c) == "Nouveau"));
    sleep(1500);
    let (v, envois, ecran) = veiller(Some(window_rect(bouton)));
    let debut = Instant::now();
    let tb = barre_des_taches();
    let (mut k, mut toast, mut reelle) = (0, None, None);
    while debut.elapsed() < Duration::from_secs(60) {
        // Moved as a hand moves it (SendInput): GetLastInputInfo counts it, SetCursorPos alone it does not.
        pointer((f.x + 380 + (k % 10) * 20, f.y + 260 + (k % 3) * 20));
        send(&[souris(MOUSEEVENTF_MOVE)]);
        k += 1;
        let s = debut.elapsed().as_secs();
        if (20..26).contains(&s) && toast.is_none() {
            let r = (ecran.rect.droite() as i32 - 380, tb.y - 126, 364, 110);
            toast = Some(d.open("dessus", "Notification", r, GREY));
        } else if s >= 26 {
            toast = None;
        }
        if s >= 35 && reelle.is_none() {
            reelle = Some(notification());
        }
        sleep(1500);
    }
    let n = envois.lock().unwrap().len();
    let au_bout = inactif();
    let b = v.arreter();
    mesure(T, "envois", n);
    mesure(T, "notification_reelle_vue", format!("{reelle:?}"));
    mesure(T, "inactif_a_la_fin_ms", au_bout.as_millis());
    bilan(T, &b);
    assert!(au_bout < Duration::from_secs(3), "witness: the mouse moved as input all along");
    assert!(b.bouge * 2 >= b.regards, "witness: the screen moves in the images");
    assert_eq!(b.pauses, 0, "the user is there: no pause");
    assert_eq!(n, 0, "nothing sent");
}

/// A dialog of 400 × 200 that opens after a click on « Nouveau » (white for 200 ms, then filled), a video
/// playing elsewhere on the screen: 1 sending in less than 1.5 s, of the filled dialog; it closes 2 s later:
/// sent, but not before 5 s after the first.
#[test]
#[ignore = "a Windows desktop (VM); changes the screen's resolution, put back after"]
fn vm_veille_dialogue() {
    const T: &str = "veille.dialogue";
    let _p = Physical::enter();
    let _ecrans = Ecrans::hd(T);
    let d = desk();
    let (_prog, win) = d.open("vivant", "Vivant", (80, 80, 800, 560), BLUE);
    let (_video, _) = d.open("video", "Video", (1000, 120, 480, 270), GREY);
    let pid = pid_of(win);
    let f = window_rect(win);
    au_premier_plan(win, (f.x + 300, f.y + 12));
    let bouton = until("its button", || children(win).into_iter().find(|&c| title(c) == "Nouveau"));
    let at = centre(&window_rect(bouton));
    sleep(1500);
    let (v, envois, ecran) = veiller(Some(window_rect(bouton)));
    sleep(ECART_MIN.as_millis() as u64 + 500);
    let (mut lat, mut fautes) = (Vec::new(), Vec::new());
    for i in 0..8 {
        let n0 = envois.lock().unwrap().len();
        let t0 = Instant::now();
        clic(at);
        if within(Duration::from_secs(3), || (envois.lock().unwrap().len() > n0).then_some(())).is_none() {
            fautes.push(format!("{i}: no sending"));
            break;
        }
        let dialog = until("the dialog", || top_windows().into_iter().find(|&h| visible(h) && pid_of(h) == pid && title(h) == "Nouvelle facture"));
        let df = window_rect(dialog);
        let (l, orange) = {
            let e = envois.lock().unwrap();
            (e[n0].t.duration_since(t0), compte(&e[n0].image, &ecran.rect, &df, ORANGE))
        };
        lat.push(l.as_secs_f32() * 1000.0);
        mesure(T, format!("ouverture_{i}"), format!("{} ms, orange {orange}/{} ({}x{})", l.as_millis(), df.aire(), df.largeur, df.hauteur));
        if orange * 10 < df.aire() * 6 {
            fautes.push(format!("{i}: the dialog sent before it was filled"));
        }
        sleep(2000);
        let n1 = envois.lock().unwrap().len();
        if n1 != n0 + 1 {
            fautes.push(format!("{i}: {} sendings for one dialog", n1 - n0));
        }
        let tf = Instant::now();
        touche(VK_ESCAPE);
        if within(Duration::from_secs(9), || (envois.lock().unwrap().len() > n1).then_some(())).is_none() {
            fautes.push(format!("{i}: the closing never sent"));
            break;
        }
        let (entre, apres) = {
            let e = envois.lock().unwrap();
            (e[n1].t.duration_since(e[n1 - 1].t), e[n1].t.duration_since(tf))
        };
        mesure(T, format!("fermeture_{i}"), format!("{} ms après, {} ms après l'envoi d'avant", apres.as_millis(), entre.as_millis()));
        // 5 s between the looks that sent them; each image handed over a few ms after its look.
        if entre + Duration::from_millis(25) < ECART_MIN {
            fautes.push(format!("{i}: two sendings {} ms apart", entre.as_millis()));
        }
        sleep(ECART_MIN.as_millis() as u64 + 300);
    }
    let b = v.arreter();
    mesure(T, "latence_p50_ms", format!("{:.0}", centile(&lat, 0.5)));
    mesure(T, "latence_max_ms", format!("{:.0}", centile(&lat, 1.0)));
    bilan(T, &b);
    if lat.iter().any(|&l| l > 1500.0) {
        fautes.push(format!("latency over 1.5 s: {lat:?}"));
    }
    assert!(fautes.is_empty(), "{fautes:?}");
}

/// Idleness, read from the session's last input (GetLastInputInfo): no hook.
#[test]
#[ignore = "a Windows desktop (VM)"]
fn vm_inactivite() {
    const T: &str = "inactivite";
    send(&[souris(MOUSEEVENTF_MOVE)]);
    let a = inactif();
    sleep(3000);
    let b = inactif();
    touche(VK_SHIFT);
    let c = inactif();
    mesure(T, "apres_souris_ms", a.as_millis());
    mesure(T, "apres_3s_ms", b.as_millis());
    mesure(T, "apres_touche_ms", c.as_millis());
    assert!(a < Duration::from_millis(300) && b >= Duration::from_millis(2900) && c < Duration::from_millis(300));
}

/// The session's lock, as Windows says it (WTS): the witness of the lock screen.
fn session_verrouillee() -> Option<bool> {
    use windows::core::PWSTR;
    use windows::Win32::System::RemoteDesktop::{WTSFreeMemory, WTSQuerySessionInformationW, WTSSessionInfoEx, WTSINFOEXW, WTS_CURRENT_SESSION, WTS_SESSIONSTATE_LOCK};
    let mut buf = PWSTR::null();
    let mut len = 0u32;
    // SAFETY: Windows allocates the buffer, freed below; read only when it holds a WTSINFOEXW.
    unsafe {
        WTSQuerySessionInformationW(None, WTS_CURRENT_SESSION, WTSSessionInfoEx, &mut buf, &mut len).ok()?;
        let lock = (len as usize >= std::mem::size_of::<WTSINFOEXW>()).then(|| {
            let info = &*(buf.0 as *const WTSINFOEXW);
            info.Level == 1 && info.Data.WTSInfoExLevel1.SessionFlags as u32 == WTS_SESSIONSTATE_LOCK
        });
        WTSFreeMemory(buf.0.cast());
        lock
    }
}

/// The session locked (the job's last step: nobody unlocks it): nothing read any more, within a look.
#[test]
#[ignore = "a Windows desktop (VM); locks the session, for good"]
fn vm_verrouille() {
    const T: &str = "verrou";
    let _p = Physical::enter();
    let (v, envois, ecran) = veiller(None);
    sleep(2000);
    mesure(T, "avant", format!("verrou {:?}, bureau ouvert {}", session_verrouillee(), bureau_ouvert()));
    // SAFETY: no argument.
    let lock = unsafe { windows::Win32::System::Shutdown::LockWorkStation() };
    let t0 = Instant::now();
    let verrouillee = within(Duration::from_secs(10), || (session_verrouillee() == Some(true)).then_some(()));
    mesure(T, "verrouillage", format!("{lock:?}, vu par WTS {:?} en {} ms", verrouillee.is_some(), t0.elapsed().as_millis()));
    if verrouillee.is_none() {
        mesure(T, "verdict", "non mesurable : Windows ne verrouille pas cette session");
        drop(v);
        return;
    }
    let ferme = within(Duration::from_secs(3), || (!bureau_ouvert()).then_some(()));
    mesure(T, "pause_apres_ms", t0.elapsed().as_millis());
    let refus = capturer(&ecran, &ChampsClassiques).err();
    mesure(T, "capture_verrouillee", format!("{refus:?}"));
    sleep(3000);
    let n = envois.lock().unwrap().len();
    let b = v.arreter();
    bilan(T, &b);
    assert!(ferme.is_some(), "the lock screen not seen");
    assert_eq!(refus, Some(Refus::Pause));
    assert!(b.pauses >= 4, "the watch kept reading the locked screen: {} pauses", b.pauses);
    assert_eq!(n, 0);
}
