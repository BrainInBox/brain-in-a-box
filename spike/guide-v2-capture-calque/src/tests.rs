//! On a Windows VM of the CI only: `cargo test -- --ignored --test-threads=1 --nocapture`. Each measure is
//! printed as `MESURE <proof> <key>=<value>`, for the job's summary.

use std::cell::RefCell;
use std::fmt::Display;
use std::io::{BufRead, BufReader, Read, Write};
use std::mem::size_of;
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use windows::core::BOOL;
use windows::Win32::Foundation::{CloseHandle, HANDLE, HWND, LPARAM, POINT, RECT};
use windows::Win32::Security::{GetSidSubAuthority, GetSidSubAuthorityCount, GetTokenInformation, TokenIntegrityLevel, TOKEN_MANDATORY_LABEL, TOKEN_QUERY};
use windows::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, INPUT_MOUSE, KEYBDINPUT, KEYBD_EVENT_FLAGS, KEYEVENTF_KEYUP, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEEVENTF_MOVE,
    MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, MOUSEINPUT, MOUSE_EVENT_FLAGS, VIRTUAL_KEY, VK_CONTROL, VK_ESCAPE, VK_LWIN, VK_SHIFT,
};
use windows::Win32::UI::WindowsAndMessaging::{
    EnumChildWindows, GetAncestor, GetForegroundWindow, SetCursorPos, SetWindowPos, ShowWindow, WindowFromPoint, GA_ROOT, SWP_NOACTIVATE, SWP_NOZORDER,
    HWND_TOP, SWP_NOMOVE, SWP_NOSIZE, SW_SHOWMINNOACTIVE, WDA_EXCLUDEFROMCAPTURE, WDA_NONE, WINDOW_DISPLAY_AFFINITY,
};

use crate::calque::{anneau, Calque, Mode, ANNEAU, EPAISSEUR, MARGE};
use crate::compose::composer;
use crate::echelle;
use crate::navigateur::{hote, Navigateur, Refus};
use crate::prims::{class, frame, handle, id_of, pid_of, screen_copy, screen_rect, title, top_windows, visible, window_rect, Physical, Rgba, Zone};
use crate::veille::{ecart, inactif, vignette, Decision, Veille, ECART_MIN};

const BLUE: [u8; 3] = [0x1e, 0x88, 0xe5];
const MAGENTA: [u8; 3] = [0xff, 0x00, 0xff];
const JAUNE: [u8; 3] = [0xff, 0xeb, 0x3b];
const ORANGE: [u8; 3] = [0xff, 0x98, 0x00];
const TEAL: [u8; 3] = [0x00, 0x89, 0x7b];
const PURPLE: [u8; 3] = [0x8e, 0x24, 0xaa];
const GREEN: [u8; 3] = [0x2e, 0x7d, 0x32];
const WAIT: Duration = Duration::from_secs(30);

fn mesure(preuve: &str, cle: impl Display, valeur: impl Display) {
    eprintln!("MESURE {preuve} {cle}={valeur}");
}

fn ms(d: Duration) -> u128 {
    d.as_millis()
}

fn centile(v: &[f32], p: f32) -> f32 {
    let mut s = v.to_vec();
    s.sort_by(|a, b| a.partial_cmp(b).unwrap());
    s.get(((s.len().max(1) - 1) as f32 * p).round() as usize).copied().unwrap_or(f32::NAN)
}

/// The other site's colour (its page and its icon), magenta: what must never be in the image. Not red:
/// Edge paints a red badge of its own on its « Settings and more » button.
const BANQUE: [u8; 3] = [0xff, 0x00, 0xff];

fn banque_px(img: &Rgba, z: Option<Zone>) -> usize {
    img.count_in(z.unwrap_or((0, 0, img.w as i32, img.h as i32)), BANQUE)
}

/// Reds of any shade outside `hors`: how many, their box and mean colour (what Edge itself paints red).
fn autres_rouges(img: &Rgba, hors: Zone) -> Option<(usize, Zone, [u32; 3])> {
    let (mut n, mut b, mut sum): (usize, Option<Zone>, [u32; 3]) = (0, None, [0; 3]);
    for y in 0..img.h as i32 {
        for x in 0..img.w as i32 {
            if x >= hors.0 && x < hors.2 && y >= hors.1 && y < hors.3 {
                continue;
            }
            let p = &img.px[((y as u32 * img.w + x as u32) * 4) as usize..][..3];
            if p[0] > 160 && p[1] < 80 && p[2] < 80 {
                n += 1;
                (0..3).for_each(|i| sum[i] += u32::from(p[i]));
                b = Some(b.map_or((x, y, x + 1, y + 1), |(l, t, r, bt)| (l.min(x), t.min(y), r.max(x + 1), bt.max(y + 1))));
            }
        }
    }
    b.map(|b| (n, b, sum.map(|v| v / n as u32)))
}

fn bounds_in(img: &Rgba, z: Zone, c: [u8; 3]) -> Option<Zone> {
    let mut b: Option<Zone> = None;
    for y in z.1.max(0)..z.3.min(img.h as i32) {
        for x in z.0.max(0)..z.2.min(img.w as i32) {
            let p = &img.px[((y as u32 * img.w + x as u32) * 4) as usize..][..3];
            if (0..3).all(|i| p[i].abs_diff(c[i]) <= 3) {
                b = Some(b.map_or((x, y, x + 1, y + 1), |(l, t, r, bt)| (l.min(x), t.min(y), r.max(x + 1), bt.max(y + 1))));
            }
        }
    }
    b
}

fn area(r: RECT) -> i64 {
    i64::from(r.right - r.left) * i64::from(r.bottom - r.top)
}

fn zone_of(r: RECT, origin: RECT) -> Zone {
    (r.left - origin.left, r.top - origin.top, r.right - origin.left, r.bottom - origin.top)
}

struct Program(Child);

impl Drop for Program {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

/// A process tree ended with the test (a browser outlives the program that launched it).
struct Tuer(u32);

impl Drop for Tuer {
    fn drop(&mut self) {
        let _ = Command::new("taskkill").args(["/F", "/T", "/PID", &self.0.to_string()]).output();
    }
}

fn dossier() -> &'static Path {
    static DIR: OnceLock<PathBuf> = OnceLock::new();
    DIR.get_or_init(|| {
        let dir = std::env::temp_dir().join(format!("spike-v2-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    })
}

fn atelier() -> &'static Path {
    static EXE: OnceLock<PathBuf> = OnceLock::new();
    EXE.get_or_init(|| {
        let windir = PathBuf::from(std::env::var("WINDIR").unwrap_or_else(|_| "C:\\Windows".into()));
        let csc = windir.join("Microsoft.NET").join("Framework64").join("v4.0.30319").join("csc.exe");
        let exe = dossier().join("atelier.exe");
        let source = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("atelier.cs");
        let out = Command::new(&csc)
            .args(["/nologo", "/target:winexe", "/optimize+", "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll"])
            .arg(format!("/out:{}", exe.display()))
            .arg(&source)
            .output()
            .unwrap();
        assert!(out.status.success() && exe.is_file(), "csc: {}", String::from_utf8_lossy(&out.stdout));
        exe
    })
}

struct Desk {
    nonce: String,
}

/// The runner's own console (hosted-compute-agent) lies on the desktop: minimized, so that no click of
/// the proofs lands in it (a click there puts it in « Select » mode, which stops the agent's writes).
fn consoles_reduites() {
    for h in top_windows() {
        if visible(h) && class(h) == "ConsoleWindowClass" {
            // SAFETY: another program's window, minimized, not activated.
            let _ = unsafe { ShowWindow(h, SW_SHOWMINNOACTIVE) };
        }
    }
}

fn desk() -> Desk {
    static N: AtomicUsize = AtomicUsize::new(0);
    consoles_reduites();
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
        let h = until_some(&format!("« {titre} » on screen"), || find_window(|h| pid_of(h) == pid && title(h) == titre));
        devant_tous(h);
        (p, h)
    }
}

/// A test window put on top of the others (not activated: Windows keeps that for the user's click). The
/// runner's desktop has windows of its own that come over ours (its console, a « System Properties »).
fn devant_tous(h: HWND) {
    for o in top_windows().into_iter().take_while(|&o| o != h).filter(|&o| visible(o) && pid_of(o) != pid_of(h)) {
        if frame(o).zip(frame(h)).is_some_and(|(a, b)| a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) {
            eprintln!("obstacle: {} « {} » over « {} »", class(o), title(o), title(h));
        }
    }
    // SAFETY: a window of ours, moved up in the order only.
    let _ = unsafe { SetWindowPos(h, Some(HWND_TOP), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE) };
}

fn find_window(pred: impl Fn(HWND) -> bool) -> Option<HWND> {
    top_windows().into_iter().find(|&h| visible(h) && pred(h))
}

fn enfants(parent: HWND) -> Vec<HWND> {
    unsafe extern "system" fn each(h: HWND, all: LPARAM) -> BOOL {
        // SAFETY: the Vec lent below.
        unsafe { (*(all.0 as *mut Vec<HWND>)).push(h) };
        BOOL(1)
    }
    let mut all: Vec<HWND> = Vec::new();
    // SAFETY: `each` only pushes to `all`, which outlives the call.
    let _ = unsafe { EnumChildWindows(Some(parent), Some(each), LPARAM(&mut all as *mut Vec<HWND> as isize)) };
    all
}

fn until(what: &str, done: impl Fn() -> bool) {
    let start = Instant::now();
    while !done() {
        assert!(start.elapsed() < WAIT, "{what}: still not, after {WAIT:?}");
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn until_some<T>(what: &str, mut get: impl FnMut() -> Option<T>) -> T {
    let start = Instant::now();
    loop {
        if let Some(v) = get() {
            return v;
        }
        assert!(start.elapsed() < WAIT, "{what}: still not, after {WAIT:?}");
        std::thread::sleep(Duration::from_millis(100));
    }
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
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn devant() -> HWND {
    // SAFETY: no argument.
    unsafe { GetForegroundWindow() }
}

/// The top-level window a click at this point would reach.
fn dessus_a((x, y): (i32, i32)) -> HWND {
    let _p = Physical::enter();
    // SAFETY: plain values.
    unsafe { GetAncestor(WindowFromPoint(POINT { x, y }), GA_ROOT) }
}

fn centre(r: RECT) -> (i32, i32) {
    ((r.left + r.right) / 2, (r.top + r.bottom) / 2)
}

fn send(inputs: &[INPUT]) {
    // SAFETY: the inputs live for the call.
    let n = unsafe { SendInput(inputs, size_of::<INPUT>() as i32) };
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
    let _p = Physical::enter();
    // SAFETY: plain values.
    unsafe { SetCursorPos(x, y) }.unwrap();
}

fn pas_une_console(at: (i32, i32)) {
    let h = dessus_a(at);
    assert_ne!(class(h), "ConsoleWindowClass", "a click about to land in a console « {} »", title(h));
}

fn clic(at: (i32, i32)) {
    pas_une_console(at);
    pointer(at);
    send(&[souris(MOUSEEVENTF_LEFTDOWN), souris(MOUSEEVENTF_LEFTUP)]);
}

fn clic_droit(at: (i32, i32)) {
    pas_une_console(at);
    pointer(at);
    send(&[souris(MOUSEEVENTF_RIGHTDOWN), souris(MOUSEEVENTF_RIGHTUP)]);
}

fn touche(vk: VIRTUAL_KEY) {
    send(&[clavier(vk, false), clavier(vk, true)]);
}

fn combo(mods: &[VIRTUAL_KEY], vk: VIRTUAL_KEY) {
    let mut all: Vec<INPUT> = mods.iter().map(|&m| clavier(m, false)).collect();
    all.push(clavier(vk, false));
    all.push(clavier(vk, true));
    all.extend(mods.iter().rev().map(|&m| clavier(m, true)));
    send(&all);
}

fn sleep(ms: u64) {
    std::thread::sleep(Duration::from_millis(ms));
}

fn z_index(h: HWND) -> Option<usize> {
    top_windows().iter().position(|&w| w == h)
}

fn rect_s(r: RECT) -> String {
    format!("{},{},{},{}", r.left, r.top, r.right, r.bottom)
}

/// The integrity level of this process (0x2000 Medium, 0x3000 High).
fn niveau() -> u32 {
    // SAFETY: our own token, closed below; a buffer large enough for the label.
    unsafe {
        let mut tok = HANDLE::default();
        OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut tok).unwrap();
        let mut buf = vec![0u8; 256];
        let mut len = 0u32;
        GetTokenInformation(tok, TokenIntegrityLevel, Some(buf.as_mut_ptr().cast()), buf.len() as u32, &mut len).unwrap();
        let _ = CloseHandle(tok);
        let label = &*(buf.as_ptr() as *const TOKEN_MANDATORY_LABEL);
        let sid = label.Label.Sid;
        *GetSidSubAuthority(sid, u32::from(*GetSidSubAuthorityCount(sid)) - 1)
    }
}

// ---------------------------------------------------------------------------------------------------
// W1: the app in front

/// Its main window, its palette, its modal dialog, its menus (the system's, and WinForms' strip) are in
/// the image; another program's window laid over them, and the one behind, never are; between them, black.
#[test]
#[ignore = "a Windows desktop (VM)"]
fn bureau_w1_appli_composee() {
    const T: &str = "w1.appli";
    let d = desk();
    let _p = Physical::enter();
    let (_fond, _) = d.open("fenetre", "Fond", (20, 20, 980, 700), JAUNE);
    let (_app, main) = d.open("appli", "Appli", (80, 80, 560, 400), BLUE);
    let pid = pid_of(main);
    let palette = until_some("its palette", || find_window(|h| pid_of(h) == pid && title(h) == "Palette"));
    let dialog = until_some("its dialog", || find_window(|h| pid_of(h) == pid && title(h) == "Nouvelle facture"));
    let (_dessus, dessus) = d.open("dessus", "Dessus", (360, 300, 420, 260), MAGENTA);
    until("the magenta window on top", || dessus_a((520, 420)) == dessus);
    let df = frame(dialog).unwrap();
    clic((df.left + 60, df.top + 12));
    until("the dialog in front", || devant() == dialog);
    sleep(300);

    let c = composer(pid, main).unwrap();
    let origin = RECT { left: c.left, top: c.top, right: c.left + c.img.w as i32, bottom: c.top + c.img.h as i32 };
    mesure(T, "fenetres", c.fenetres.len());
    mesure(T, "ratees", c.ratees);
    mesure(T, "image", format!("{}x{}", c.img.w, c.img.h));
    let temoin = screen_copy(origin).unwrap();
    mesure(T, "temoin_ecran_magenta", temoin.count(MAGENTA));
    mesure(T, "temoin_ecran_jaune", temoin.count(JAUNE));
    assert!(temoin.count(MAGENTA) > 10_000 && temoin.count(JAUNE) > 10_000, "witness: both other windows are on the screen there");

    mesure(T, "magenta", c.img.count(MAGENTA));
    mesure(T, "jaune", c.img.count(JAUNE));
    assert_eq!(c.img.count(MAGENTA), 0, "the window laid over the app");
    assert_eq!(c.img.count(JAUNE), 0, "the window behind the app");
    let zones: Vec<Zone> = c.fenetres.iter().map(|&(_, r)| c.zone(r)).collect();
    let (mut hors, mut hors_noir) = (0usize, 0usize);
    for y in 0..c.img.h as i32 {
        for x in 0..c.img.w as i32 {
            if zones.iter().any(|z| x >= z.0 && x < z.2 && y >= z.1 && y < z.3) {
                continue;
            }
            hors += 1;
            if c.img.px[((y as u32 * c.img.w + x as u32) * 4) as usize..][..3].iter().all(|&v| v <= 6) {
                hors_noir += 1;
            }
        }
    }
    mesure(T, "hors_fenetres", format!("{hors_noir}/{hors} noirs"));
    assert!(hors > 10_000 && hors_noir == hors, "outside the app's windows, black");
    let dz = c.zone(df);
    let pz = c.zone(frame(palette).unwrap());
    let (o, t) = (c.img.count_in(dz, ORANGE), c.img.count_in(pz, TEAL));
    mesure(T, "dialogue_orange", format!("{o}/{}", c.img.area(dz)));
    mesure(T, "palette_teal", format!("{t}/{}", c.img.area(pz)));
    assert!(o * 10 > c.img.area(dz) * 6, "the dialog");
    assert!(t * 10 > c.img.area(pz) * 6, "the palette");
    assert!(c.img.count(BLUE) * 10 > (560 * 400) * 3, "the main window");

    // The system's menu, on the dialog.
    let point = (df.left + 60, df.top + 110);
    clic_droit(point);
    let menu = until_some("the system's menu", || find_window(|h| pid_of(h) == pid && class(h) == "#32768"));
    sleep(400);
    let c = composer(pid, main).unwrap();
    let mr = frame(menu).or_else(|| window_rect(menu)).unwrap();
    let mz = c.zone(mr);
    let (a, noir, o) = (c.img.area(mz), c.img.black_in(mz), c.img.count_in(mz, ORANGE));
    mesure(T, "menu_systeme", format!("{} noir {noir}/{a} orange {o}", rect_s(mr)));
    assert!(c.fenetres.iter().any(|&(h, _)| h == menu), "the menu is one of the app's windows");
    assert!(noir * 10 < a && o * 10 < a, "the menu drawn over the dialog");
    assert_eq!(c.img.count(MAGENTA), 0);
    touche(VK_ESCAPE);
    until("the menu closed", || find_window(|h| pid_of(h) == pid && class(h) == "#32768").is_none());

    // WinForms' strip menu (a window of its own).
    clic(point);
    let known = [main, palette, dialog];
    let strip = until_some("the strip menu", || {
        find_window(|h| pid_of(h) == pid && !known.contains(&h) && class(h).starts_with("WindowsForms") && frame(h).is_some_and(|r| area(r) > 10_000))
    });
    sleep(400);
    let c = composer(pid, main).unwrap();
    let sr = frame(strip).unwrap();
    let sz = c.zone(sr);
    let pu = c.img.count_in(sz, PURPLE);
    mesure(T, "menu_bande", format!("{} violet {pu}/{}", rect_s(sr), c.img.area(sz)));
    mesure(T, "classes", c.fenetres.iter().map(|&(h, _)| class(h)).collect::<Vec<_>>().join("|"));
    assert!(pu * 10 > c.img.area(sz) * 6, "the strip menu");
    assert_eq!(c.img.count(MAGENTA), 0);
    touche(VK_ESCAPE);
}

/// Run by the workflow at Medium integrity (moyen.exe), beside « Atelier Admin » started as administrator
/// (High): its windows draw nothing for us; the image is black, and nothing of the screen replaces them.
#[test]
#[ignore = "under moyen.exe, beside a window run as administrator"]
fn sonde_admin() {
    const T: &str = "w1.admin";
    let lvl = niveau();
    mesure(T, "niveau_test", format!("{lvl:#x}"));
    assert_eq!(lvl, 0x2000, "the probe must run at Medium");
    let _p = Physical::enter();
    let h = until_some("the administrator's window", || find_window(|h| title(h) == "Atelier Admin"));
    let pid = pid_of(h);
    let temoin = screen_copy(frame(h).unwrap()).unwrap();
    mesure(T, "temoin_ecran_bleu", temoin.count(BLUE));
    match composer(pid, h) {
        None => mesure(T, "image", "aucune"),
        Some(c) => {
            mesure(T, "fenetres", c.fenetres.len());
            mesure(T, "ratees", c.ratees);
            mesure(T, "bleu", c.img.count(BLUE));
            mesure(T, "noir", format!("{:.3}", c.img.black_share()));
            assert_eq!(c.img.count(BLUE), 0, "never the window's pixels");
            assert!(c.img.black_share() > 0.99, "black: the guide goes to paste mode");
        }
    }
}

fn page(banque: bool) -> String {
    let (titre, fond) = if banque { ("Banque", "ff00ff") } else { ("Odoo Factures", "2e7d32") };
    format!(
        "<!doctype html><meta charset=utf-8><title>{titre}</title>\
         <link rel=icon href=\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' fill='%23{fond}'/%3E%3C/svg%3E\">\
         <style>html,body{{margin:0;height:100%;background:#{fond}}}</style><body></body>"
    )
}

fn repondre(mut s: TcpStream) {
    let _ = s.set_read_timeout(Some(Duration::from_secs(5)));
    let mut buf = vec![0u8; 8192];
    let mut n = 0;
    while n < buf.len() {
        match s.read(&mut buf[n..]) {
            Ok(0) | Err(_) => break,
            Ok(k) => {
                n += k;
                if buf[..n].windows(4).any(|w| w == b"\r\n\r\n") {
                    break;
                }
            }
        }
    }
    let req = String::from_utf8_lossy(&buf[..n]).to_ascii_lowercase();
    let banque = req.lines().any(|l| l.starts_with("host:") && l.contains("banque"));
    let (status, body) = if req.starts_with("get / ") { ("200 OK", page(banque)) } else { ("404 Not Found", String::new()) };
    let _ = write!(s, "HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
}

/// Two sites on this VM: odoo.localhost (green) and banque.localhost (red), which Chromium sends to 127.0.0.1.
fn serveur() -> u16 {
    let l = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = l.local_addr().unwrap().port();
    if let Ok(l6) = TcpListener::bind(("::1", port)) {
        std::thread::spawn(move || l6.incoming().flatten().for_each(|s| drop(std::thread::spawn(move || repondre(s)))));
    }
    std::thread::spawn(move || l.incoming().flatten().for_each(|s| drop(std::thread::spawn(move || repondre(s)))));
    port
}

/// C1: in Edge, the app is the browser and the site of its tab. The other tab's icon (magenta) is in the
/// tab strip, painted black; Edge's other window (magenta) is left out; another site is a pause in less than 1 s,
/// and an image taken while the site changes is never sent.
#[test]
#[ignore = "a Windows desktop (VM)"]
fn bureau_w1_edge() {
    const T: &str = "w1.edge";
    let d = desk();
    let _p = Physical::enter();
    let edge = PathBuf::from(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe");
    assert!(edge.is_file(), "Edge on the VM");
    let port = serveur();
    let profil = dossier().join(format!("edge-{}", d.nonce));
    let base = |c: &mut Command| {
        c.arg(format!("--user-data-dir={}", profil.display()));
        c.args(["--no-first-run", "--no-default-browser-check", "--disable-sync", "--hide-crash-restore-bubble", "--disable-features=msEdgeSidebarV2"]);
    };
    let (u1, u2) = (format!("http://odoo.localhost:{port}/"), format!("http://banque.localhost:{port}/"));
    let mut c1 = Command::new(&edge);
    base(&mut c1);
    c1.args(["--new-window", "--window-position=0,0", "--window-size=640,600", &u1, &u2]);
    let _e1 = Program(c1.spawn().unwrap());
    let w1 = until_some("Edge's window", || find_window(|h| class(h) == "Chrome_WidgetWin_1" && (title(h).contains("Odoo Factures") || title(h).contains("Banque"))));
    let pid = pid_of(w1);
    let _tuer = Tuer(pid);
    mesure(T, "titre1", title(w1));
    let mut c2 = Command::new(&edge);
    base(&mut c2);
    c2.args(["--new-window", "--window-position=660,60", "--window-size=360,420", &u2]);
    let _e2 = Program(c2.spawn().unwrap());
    let w2 = until_some("Edge's second window", || find_window(|h| h != w1 && class(h) == "Chrome_WidgetWin_1" && title(h).contains("Banque")));
    mesure(T, "meme_processus", pid == pid_of(w2));
    mesure(T, "fenetre2_avant", frame(w2).map(rect_s).unwrap_or_default());
    // Edge opens its second window over the first, whatever --window-position says: moved aside.
    // SAFETY: another program's window, moved, not activated.
    unsafe { SetWindowPos(w2, None, 660, 60, 360, 420, SWP_NOZORDER | SWP_NOACTIVATE) }.unwrap();
    until("Edge's second window aside", || frame(w2).is_some_and(|r| r.left >= 640));
    let f1 = frame(w1).unwrap();
    let point = (f1.left + 300, f1.bottom - 80);
    let decrire = |h: HWND| format!("{} « {} » pid {} {:?} owner {:?}", class(h), title(h), pid_of(h), frame(h).map(rect_s), crate::prims::owner(h).map(id_of));
    mesure(T, "fenetre1", decrire(w1));
    mesure(T, "sous_le_clic", decrire(dessus_a(point)));
    clic(point);
    if within(Duration::from_secs(10), || (devant() == w1).then_some(())).is_none() {
        let edges: Vec<String> = top_windows().into_iter().filter(|&h| visible(h) && pid_of(h) == pid).map(decrire).collect();
        panic!("Edge not in front: in front {}, Edge's windows {edges:?}", decrire(devant()));
    }
    combo(&[VK_CONTROL], VIRTUAL_KEY(0x31));

    let t = Instant::now();
    let mut nav = Navigateur::new(w1, pid).unwrap();
    let mut dernier = None;
    let premier = within(Duration::from_secs(20), || {
        dernier = nav.domaine();
        dernier.clone().filter(|s| s == "odoo.localhost")
    })
    .unwrap_or_else(|| {
        let edges: Vec<String> = top_windows().into_iter().filter(|&h| visible(h) && pid_of(h) == pid).map(|h| format!("{} « {} »", class(h), title(h))).collect();
        panic!("the site never read as odoo.localhost: last {dernier:?}, address bar {:?}, Edge's windows {edges:?}", nav.barre())
    });
    mesure(T, "uia_premiere_lecture_ms", ms(t.elapsed()));
    mesure(T, "domaine", &premier);
    mesure(T, "barre", format!("{:?}", nav.barre()));
    let bande = until_some("the tab strip", || nav.bande());
    mesure(T, "bande", rect_s(bande));
    sleep(1000);

    let brut = composer(pid, w1).unwrap();
    let bz = brut.zone(bande);
    let rouge_bande = banque_px(&brut.img, Some(bz));
    mesure(T, "temoin_icone_banque_dans_bande", rouge_bande);
    mesure(T, "banque_hors_bande_avant_masque", banque_px(&brut.img, None) - rouge_bande);
    match autres_rouges(&brut.img, bz) {
        None => mesure(T, "autres_rouges_hors_bande", "aucun"),
        Some((n, z, moy)) => {
            let at = (brut.left + (z.0 + z.2) / 2, brut.top + (z.1 + z.3) / 2);
            mesure(T, "autres_rouges_hors_bande", format!("{n} px, boîte {z:?}, moyenne {moy:?}, élément UIA au centre {:?}", nav.element_au(at)));
        }
    }
    assert!(rouge_bande >= 20, "witness: the other tab's icon is in the tab strip");
    mesure(T, "fenetres", brut.fenetres.len());
    assert!(brut.fenetres.iter().all(|&(h, _)| h != w2), "Edge's other window is left out");
    assert_eq!(banque_px(&brut.img, None), rouge_bande, "nothing of the other site but its icon");

    let c = nav.regarder("odoo.localhost").unwrap();
    let bz = c.zone(bande);
    mesure(T, "banque_apres_masque", banque_px(&c.img, None));
    mesure(T, "bande_noire", format!("{}/{}", c.img.black_in(bz), c.img.area(bz)));
    mesure(T, "vert", format!("{}/{}", c.img.count(GREEN), c.img.pixels()));
    assert_eq!(banque_px(&c.img, None), 0, "not a pixel of the other site");
    assert_eq!(c.img.black_in(bz), c.img.area(bz), "the tab strip black");
    assert!(c.img.count(GREEN) * 10 > c.img.pixels() * 4, "the page");

    // The tab changes while a look is under way, between its two reads of the site.
    let r = nav.regarder_avec("odoo.localhost", || {
        combo(&[VK_CONTROL], VIRTUAL_KEY(0x32));
        sleep(800);
    });
    mesure(T, "changement_pendant_regard", format!("{:?}", r.as_ref().err()));
    assert!(matches!(r, Err(Refus::Domaine(_))), "an image taken while the site changed is not sent");
    combo(&[VK_CONTROL], VIRTUAL_KEY(0x31));
    until_some("back on Odoo", || nav.domaine().filter(|s| s == "odoo.localhost"));
    sleep(500);

    let mut courant = "odoo.localhost".to_string();
    let (mut pauses, mut lectures) = (Vec::new(), Vec::new());
    for i in 0..6 {
        let (key, attendu) = if i % 2 == 0 { (0x32, "banque.localhost") } else { (0x31, "odoo.localhost") };
        let t0 = Instant::now();
        combo(&[VK_CONTROL], VIRTUAL_KEY(key));
        let vu = loop {
            let t = Instant::now();
            let s = nav.domaine();
            lectures.push(t.elapsed().as_secs_f32() * 1000.0);
            if s.as_deref() != Some(courant.as_str()) {
                break s;
            }
            assert!(t0.elapsed() < Duration::from_secs(5), "the switch never seen");
            sleep(250);
        };
        let l = t0.elapsed();
        pauses.push(l.as_secs_f32() * 1000.0);
        mesure(T, format!("pause_{i}"), format!("{} ms ({vu:?})", ms(l)));
        courant = until_some("the new site", || nav.domaine().filter(|s| s == attendu));
        let r = nav.regarder("odoo.localhost");
        if attendu == "banque.localhost" {
            assert!(matches!(r, Err(Refus::Domaine(_))), "another site: a pause");
        } else {
            assert_eq!(banque_px(&r.unwrap().img, None), 0);
        }
    }
    mesure(T, "pause_max_ms", centile(&pauses, 1.0));
    mesure(T, "lecture_uia_p50_ms", format!("{:.1}", centile(&lectures, 0.5)));
    mesure(T, "lecture_uia_p90_ms", format!("{:.1}", centile(&lectures, 0.9)));
    assert!(pauses.iter().all(|&p| p < 1000.0), "a pause in less than 1 s: {pauses:?}");
}

#[test]
fn hote_lu_dans_la_barre() {
    assert_eq!(hote("odoo.localhost:8765").as_deref(), Some("odoo.localhost"));
    assert_eq!(hote("https://mail.google.com/mail/u/0/#inbox").as_deref(), Some("mail.google.com"));
    assert_eq!(hote("http://user@odoo.example.com:8069/web#id=3").as_deref(), Some("odoo.example.com"));
    assert_eq!(hote("facture dupont"), None);
    assert_eq!(hote(""), None);
}

// ---------------------------------------------------------------------------------------------------
// W3: the overlay

fn check(ok: bool, what: impl Display) -> Result<(), String> {
    if ok {
        Ok(())
    } else {
        Err(what.to_string())
    }
}

/// The overlay under test: Win32 (calque.rs) or Tauri (calque-tauri, its own process).
trait Overlay {
    fn hwnd(&self) -> HWND;
    fn affinite(&self, a: WINDOW_DISPLAY_AFFINITY) -> bool;
}

impl Overlay for Calque {
    fn hwnd(&self) -> HWND {
        Calque::hwnd(self)
    }

    fn affinite(&self, a: WINDOW_DISPLAY_AFFINITY) -> bool {
        Calque::affinite(self, a)
    }
}

struct CalqueTauri {
    child: Child,
    io: RefCell<(ChildStdin, BufReader<ChildStdout>)>,
    id: u64,
}

impl CalqueTauri {
    fn exe() -> Option<PathBuf> {
        std::env::var_os("CALQUE_TAURI").map(PathBuf::from).filter(|p| p.is_file())
    }

    fn ouvrir(anneaux: Vec<RECT>, mode: &str) -> Self {
        let arg = anneaux.iter().map(|r| format!("{},{},{},{}", r.left, r.top, r.right - r.left, r.bottom - r.top)).collect::<Vec<_>>().join(";");
        let mut child = Command::new(Self::exe().expect("CALQUE_TAURI")).args([arg.as_str(), mode]).stdin(Stdio::piped()).stdout(Stdio::piped()).spawn().unwrap();
        let stdin = child.stdin.take().unwrap();
        let mut stdout = BufReader::new(child.stdout.take().unwrap());
        let mut line = String::new();
        stdout.read_line(&mut line).unwrap();
        eprintln!("calque tauri: {}", line.trim());
        let id = line.split_whitespace().nth(1).and_then(|v| v.parse().ok()).expect("hwnd <id>");
        CalqueTauri { child, io: RefCell::new((stdin, stdout)), id }
    }
}

impl Overlay for CalqueTauri {
    fn hwnd(&self) -> HWND {
        handle(self.id)
    }

    fn affinite(&self, a: WINDOW_DISPLAY_AFFINITY) -> bool {
        let mut io = self.io.borrow_mut();
        let (stdin, stdout) = &mut *io;
        let _ = writeln!(stdin, "{}", if a == WDA_NONE { "none" } else { "exclude" });
        let _ = stdin.flush();
        let mut line = String::new();
        stdout.read_line(&mut line).is_ok() && line.trim() == "ok true"
    }
}

impl Drop for CalqueTauri {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[derive(Clone, Copy, PartialEq)]
enum Sorte {
    Win32(Mode),
    /// calque-tauri's mode: the order of its calls.
    Tauri(&'static str),
}

/// The overlay the guide would take: the Win32 one by colour key, and Tauri's in this order.
const WIN32_RETENU: Sorte = Sorte::Win32(Mode::Cle);
const TAURI_RETENU: &str = "avant";
const TAURI_MODES: [&str; 3] = ["apres", "avant", "lwa"];

impl Sorte {
    fn ouvrir(self, anneaux: Vec<RECT>) -> Box<dyn Overlay> {
        match self {
            Sorte::Win32(m) => Box::new(Calque::ouvrir(anneaux, m)),
            Sorte::Tauri(m) => Box::new(CalqueTauri::ouvrir(anneaux, m)),
        }
    }

    fn nom(self) -> String {
        match self {
            Sorte::Win32(Mode::Cle) => "win32-cle".into(),
            Sorte::Win32(Mode::Ulw) => "win32-ulw".into(),
            Sorte::Tauri(m) => format!("tauri-{m}"),
        }
    }

    fn tauri(self) -> bool {
        matches!(self, Sorte::Tauri(_))
    }
}

/// Bibo's pastille, as in the guide: a small window of this process that the user clicks. Clicked, it gives
/// this process the right to put windows in front, as Bibo has it when the user just typed in its bar.
struct Pastille {
    id: u64,
    tid: u32,
    thread: Option<JoinHandle<()>>,
}

impl Pastille {
    fn ouvrir(r: RECT) -> Pastille {
        use std::sync::Once;
        use windows::core::w;
        use windows::Win32::Foundation::{LRESULT, WPARAM};
        use windows::Win32::System::LibraryLoader::GetModuleHandleW;
        use windows::Win32::System::Threading::GetCurrentThreadId;
        use windows::Win32::UI::WindowsAndMessaging::{
            CreateWindowExW, DefWindowProcW, DestroyWindow, DispatchMessageW, GetMessageW, RegisterClassW, TranslateMessage, MSG, WNDCLASSW, WS_BORDER,
            WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_POPUP, WS_VISIBLE,
        };
        unsafe extern "system" fn proc_(h: HWND, msg: u32, wp: WPARAM, lp: LPARAM) -> LRESULT {
            // SAFETY: the default behaviour of a plain window.
            unsafe { DefWindowProcW(h, msg, wp, lp) }
        }
        static CLASS: Once = Once::new();
        let (tx, rx) = std::sync::mpsc::channel();
        let thread = std::thread::spawn(move || {
            let _p = Physical::enter();
            // SAFETY: this module's instance and a class of static strings, registered once; the window
            // is destroyed on this thread.
            unsafe {
                let instance = GetModuleHandleW(None).unwrap();
                CLASS.call_once(|| {
                    RegisterClassW(&WNDCLASSW { lpfnWndProc: Some(proc_), hInstance: instance.into(), lpszClassName: w!("BiboPastille"), ..Default::default() });
                });
                let h = CreateWindowExW(
                    WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
                    w!("BiboPastille"),
                    w!("Bibo pastille"),
                    WS_POPUP | WS_VISIBLE | WS_BORDER,
                    r.left,
                    r.top,
                    r.right - r.left,
                    r.bottom - r.top,
                    None,
                    None,
                    Some(instance.into()),
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
        Pastille { id, tid, thread: Some(thread) }
    }
}

impl Drop for Pastille {
    fn drop(&mut self) {
        use windows::Win32::Foundation::WPARAM;
        use windows::Win32::UI::WindowsAndMessaging::{PostThreadMessageW, WM_QUIT};
        // SAFETY: the pastille's thread, told to end.
        let _ = unsafe { PostThreadMessageW(self.tid, WM_QUIT, WPARAM(0), LPARAM(0)) };
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

/// The user clicks Bibo's pastille, then Bibo puts the app back in front: this process (and the overlay's,
/// when it is another) may still put a window in front when the overlay shows.
fn bibo_vient_d_avoir_la_main(t: &str, s: RECT, tb: RECT, win: HWND) -> Result<Pastille, String> {
    use windows::Win32::UI::WindowsAndMessaging::{AllowSetForegroundWindow, SetForegroundWindow, ASFW_ANY};
    let pr = RECT { left: s.right - 240, top: tb.top - 90, right: s.right - 40, bottom: tb.top - 30 };
    let pastille = Pastille::ouvrir(pr);
    sleep(300);
    clic(centre(pr));
    let ph = handle(pastille.id);
    let eue = within(Duration::from_secs(5), || (devant() == ph).then_some(())).is_some();
    // SAFETY: plain values; this process is in front.
    let rendue = unsafe { AllowSetForegroundWindow(ASFW_ANY).is_ok() && SetForegroundWindow(win).as_bool() };
    let revenue = within(Duration::from_secs(5), || (devant() == win).then_some(())).is_some();
    mesure(t, "pastille", format!("devant {eue}, rendue {rendue}, appli revenue {revenue}"));
    check(eue && revenue, "Bibo never had the focus, or never gave it back")?;
    Ok(pastille)
}

fn ring_area(r: RECT) -> i64 {
    let (o, i) = anneau(r);
    area(o) - area(i)
}

/// The click count the window of mode « bouton » shows at the end of its title, and where the last one landed.
fn clics(win: HWND) -> (u32, String) {
    let t = title(win);
    let mut words = t.rsplit(' ');
    let n = words.next().and_then(|w| w.parse().ok()).unwrap_or(0);
    (n, words.next().unwrap_or("").to_string())
}

/// A click at `at`, and where the window of mode « bouton » says it landed (« clic-fond », « clic-bouton »),
/// or None when it never got it.
fn clic_recu(win: HWND, at: (i32, i32)) -> Option<String> {
    let (n0, _) = clics(win);
    clic(at);
    within(Duration::from_secs(3), || {
        let (n, ou) = clics(win);
        (n > n0).then_some(ou)
    })
}

/// The first click on a window just opened: tried again while it is not yet listening.
fn premier_clic(win: HWND, at: (i32, i32)) -> Option<String> {
    (0..5).find_map(|_| {
        if dessus_a(at) != win {
            devant_tous(win);
        }
        clic_recu(win, at)
    })
}

fn verifier_calque(d: &Desk, echelle: u32, sorte: Sorte) -> Result<(), String> {
    let t = format!("w3.{}.{echelle}", sorte.nom());
    let mut fautes: Vec<String> = Vec::new();
    macro_rules! verifier {
        ($ok:expr, $what:expr) => {
            if !$ok {
                fautes.push($what.to_string());
            }
        };
    }
    let _p = Physical::enter();
    let (_prog, win) = d.open("bouton", &format!("Calque{echelle}{}", sorte.nom()), (100, 100, 600, 400), BLUE);
    let tauri = sorte.tauri();
    let pid = pid_of(win);
    let bouton = until_some("its button", || enfants(win).into_iter().find(|&c| title(c) == "Nouveau"));
    let b = window_rect(bouton).unwrap();
    let f = frame(win).unwrap();
    mesure(&t, "fenetre", rect_s(f));
    mesure(&t, "bouton", rect_s(b));
    let fond = (f.right - 60, f.bottom - 60);
    premier_clic(win, fond).ok_or_else(|| format!("the window never clicked; in front {} « {} »", class(devant()), title(devant())))?;
    until("in front", || devant() == win);
    let tray = find_window(|h| class(h) == "Shell_TrayWnd").ok_or("no taskbar")?;
    let tb = window_rect(tray).unwrap();
    mesure(&t, "barre_des_taches", rect_s(tb));
    let o = MARGE + EPAISSEUR + 2;
    let tb_cible = RECT { left: tb.left + 40, top: tb.top + o, right: tb.left + 300, bottom: tb.bottom - o };
    let s = screen_rect();
    mesure(&t, "ecran", rect_s(s));
    let avant = screen_copy(s).unwrap();
    check(avant.count(ANNEAU) == 0, "the ring's colour on the screen before the overlay")?;
    let zf = zone_of(f, s);
    let bleu_avant = avant.count_in(zf, BLUE) as i64;

    let pastille = bibo_vient_d_avoir_la_main(&t, s, tb, win)?;
    let calque = sorte.ouvrir(vec![b, tb_cible]);
    // A web view draws its page a moment after the window shows.
    sleep(if tauri { 3000 } else { 500 });
    mesure(&t, "devant_apres_calque", class(devant()));
    verifier!(devant() == win, "the overlay took the focus");
    drop(pastille);
    mesure(&t, "z_calque_vs_barre_avant", format!("{:?} < {:?}", z_index(calque.hwnd()), z_index(tray)));

    let pw = composer(pid, win).unwrap();
    let ec = screen_copy(s).unwrap();
    mesure(&t, "anneau_printwindow", pw.img.count(ANNEAU));
    mesure(&t, "anneau_ecran_entier", ec.count(ANNEAU));
    let bleu_exclu = ec.count_in(zf, BLUE) as i64;
    mesure(&t, "bleu_capture_calque_exclu", format!("{bleu_exclu}/{bleu_avant}"));
    verifier!(pw.img.count(ANNEAU) == 0, "the ring in PrintWindow's image");
    verifier!(ec.count(ANNEAU) == 0, "the ring in a copy of the whole screen");
    verifier!(bleu_exclu * 100 >= bleu_avant * 95, "the app hidden in a capture of the screen");

    // The witness: shown to captures for a moment, the ring is on the screen, where it should be.
    let aff = calque.affinite(WDA_NONE);
    mesure(&t, "affinite_none", aff);
    let (ob, _) = anneau(b);
    let autour = zone_of(RECT { left: ob.left - 20, top: ob.top - 20, right: ob.right + 20, bottom: ob.bottom + 20 }, s);
    let part = if tauri { 80 } else { 95 };
    let vu = within(Duration::from_secs(10), || {
        let vu = screen_copy(s).unwrap();
        (vu.count_in(autour, ANNEAU) as i64 * 100 >= ring_area(b) * part).then_some(vu)
    })
    .unwrap_or_else(|| screen_copy(s).unwrap());
    let nb = vu.count_in(autour, ANNEAU) as i64;
    // The overlay is transparent but for its rings: the app under it is seen.
    let bleu_vu = vu.count_in(zf, BLUE) as i64;
    mesure(&t, "bleu_sous_calque", format!("{bleu_vu}/{bleu_avant}"));
    verifier!(bleu_vu * 100 >= bleu_avant * 90, "the app hidden by the overlay");
    let bornes = bounds_in(&vu, autour, ANNEAU);
    let attendu = zone_of(ob, s);
    let erreur = bornes.map_or(i32::MAX, |z| (z.0 - attendu.0).abs().max((z.1 - attendu.1).abs()).max((z.2 - attendu.2).abs()).max((z.3 - attendu.3).abs()));
    mesure(&t, "temoin_anneau_bouton", format!("{nb}/{}", ring_area(b)));
    mesure(&t, "ecart_position_px", erreur);
    let ztb = zone_of(tb, s);
    let ntb = vu.count_in(ztb, ANNEAU) as i64;
    mesure(&t, "temoin_anneau_sur_barre", format!("{ntb}/{}", ring_area(tb_cible)));
    let back = calque.affinite(WDA_EXCLUDEFROMCAPTURE);
    mesure(&t, "affinite_exclue", back);
    sleep(400);
    let encore = screen_copy(s).unwrap().count(ANNEAU);
    mesure(&t, "anneau_ecran_entier_apres_temoin", encore);
    verifier!(nb * 100 >= ring_area(b) * part, "witness: the ring on the screen");
    verifier!(erreur <= 4, format!("the ring {erreur} px off"));
    verifier!(ntb * 100 >= ring_area(tb_cible) * (part - 10), "the ring over the taskbar");
    verifier!(encore == 0, "the ring in a copy of the whole screen, kept out again");

    // Clicks go through: inside the ring (transparent) and on the ring itself (opaque).
    let r1 = clic_recu(win, centre(b));
    mesure(&t, "clic_dans_anneau", format!("{r1:?}"));
    verifier!(r1.as_deref() == Some("clic-bouton"), "the button not clicked through the overlay");
    let sur = (ob.left + EPAISSEUR / 2, (b.top + b.bottom) / 2);
    mesure(&t, "sous_anneau", class(dessus_a(sur)));
    verifier!(dessus_a(sur) == win, "the ring catches the point");
    let r2 = clic_recu(win, sur);
    mesure(&t, "clic_sur_anneau", format!("{r2:?}"));
    verifier!(r2.as_deref() == Some("clic-fond"), "the click on the ring never reached the app");
    verifier!(devant() == win, "the app no longer in front");

    // The taskbar used (Win+T): does it come over the overlay?
    combo(&[VK_LWIN], VIRTUAL_KEY(b'T' as u16));
    let active = within(Duration::from_secs(3), || (devant() == tray).then_some(())).is_some();
    mesure(&t, "barre_active", active);
    let mut dessus = 0;
    for _ in 0..10 {
        sleep(200);
        if z_index(calque.hwnd()) < z_index(tray) {
            dessus += 1;
        }
    }
    mesure(&t, "calque_au_dessus_barre_sur_10", dessus);
    calque.affinite(WDA_NONE);
    sleep(400);
    let ntb2 = screen_copy(tb).unwrap().count(ANNEAU) as i64;
    calque.affinite(WDA_EXCLUDEFROMCAPTURE);
    mesure(&t, "temoin_anneau_sur_barre_active", format!("{ntb2}/{}", ring_area(tb_cible)));
    touche(VK_ESCAPE);
    sleep(300);
    let r3 = clic_recu(win, fond);
    verifier!(r3.is_some(), "the app not clicked back");
    verifier!(dessus == 10, format!("the taskbar over the overlay {} times in 10", 10 - dessus));
    verifier!(ntb2 * 100 >= ring_area(tb_cible) * (part - 10), "the ring hidden by the taskbar in use");

    // Another window always on top, opened after the overlay, over the ring (a call's small window, a
    // notification): the overlay goes back over it.
    let zd = (ob.left - 30, ob.top - 30, ob.right - ob.left + 60, ob.bottom - ob.top + 60);
    let (_dessus, dw) = d.open("dessus", &format!("Dessus{echelle}{}", sorte.nom()), zd, MAGENTA);
    sleep(600);
    let mut au_dessus = 0;
    for _ in 0..10 {
        sleep(200);
        if z_index(calque.hwnd()) < z_index(dw) {
            au_dessus += 1;
        }
    }
    mesure(&t, "calque_au_dessus_fenetre_toujours_devant_sur_10", au_dessus);
    calque.affinite(WDA_NONE);
    sleep(400);
    let nd = screen_copy(s).unwrap().count_in(autour, ANNEAU) as i64;
    calque.affinite(WDA_EXCLUDEFROMCAPTURE);
    mesure(&t, "temoin_anneau_sur_fenetre_toujours_devant", format!("{nd}/{}", ring_area(b)));
    verifier!(au_dessus == 10, format!("a window always on top over the overlay {} times in 10", 10 - au_dessus));
    verifier!(nd * 100 >= ring_area(b) * part, "the ring hidden by a window always on top");
    drop(calque);
    if fautes.is_empty() {
        Ok(())
    } else {
        Err(fautes.join("; "))
    }
}

/// Which windows Windows lets out of captures (SetWindowDisplayAffinity), by kind and size.
#[test]
#[ignore = "a Windows desktop (VM)"]
fn sonde_affinite() {
    use windows::core::w;
    use windows::Win32::Foundation::{COLORREF, SIZE};
    use windows::Win32::Graphics::Gdi::{GetDC, ReleaseDC, AC_SRC_ALPHA, AC_SRC_OVER, BLENDFUNCTION};
    use windows::Win32::UI::WindowsAndMessaging::{
        CreateWindowExW, DestroyWindow, SetLayeredWindowAttributes, SetWindowDisplayAffinity, ShowWindow, UpdateLayeredWindow, LWA_ALPHA, LWA_COLORKEY,
        SW_SHOWNOACTIVATE, ULW_ALPHA, WDA_MONITOR, WINDOW_EX_STYLE, WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_NOREDIRECTIONBITMAP, WS_EX_TOOLWINDOW, WS_EX_TOPMOST,
        WS_EX_TRANSPARENT, WS_POPUP,
    };
    const T: &str = "w3.affinite";
    let _p = Physical::enter();
    let s = screen_rect();
    let (sw, sh) = (s.right - s.left, s.bottom - s.top);
    for (nom, w, h) in [("ecran", sw, sh), ("ecran_moins_1", sw - 1, sh), ("moitie", sw / 2, sh / 2), ("anneau", 260, 120)] {
        // « tao »: what Tauri makes for a transparent window that clicks go through (no back buffer, layered,
        // neither UpdateLayeredWindow nor SetLayeredWindowAttributes).
        for sorte in ["ulw", "lwa", "opaque", "ulw_transparent", "cle_transparent", "tao"] {
            let base = WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE;
            let ex = match sorte {
                "ulw" | "lwa" => WS_EX_LAYERED | base,
                "ulw_transparent" | "cle_transparent" => WS_EX_LAYERED | WS_EX_TRANSPARENT | base,
                "tao" => WS_EX_NOREDIRECTIONBITMAP | WS_EX_LAYERED | WS_EX_TRANSPARENT | base,
                _ => base,
            };
            // SAFETY: a window of a system class, destroyed below.
            let hw = unsafe { CreateWindowExW(WINDOW_EX_STYLE(ex.0), w!("STATIC"), w!("sonde"), WS_POPUP, s.left, s.top, w, h, None, None, None, None) }.unwrap();
            if sorte.starts_with("ulw") {
                let mut c = crate::prims::Canvas::new(w, h).unwrap();
                c.pixels_mut().fill(0);
                let blend = BLENDFUNCTION { BlendOp: AC_SRC_OVER as u8, BlendFlags: 0, SourceConstantAlpha: 255, AlphaFormat: AC_SRC_ALPHA as u8 };
                // SAFETY: as in calque.rs.
                unsafe {
                    let screen = GetDC(None);
                    let _ = UpdateLayeredWindow(hw, Some(screen), Some(&POINT { x: s.left, y: s.top }), Some(&SIZE { cx: w, cy: h }), Some(c.dc), Some(&POINT { x: 0, y: 0 }), COLORREF(0), Some(&blend), ULW_ALPHA);
                    ReleaseDC(None, screen);
                }
            } else if sorte == "lwa" {
                // SAFETY: our layered window.
                let _ = unsafe { SetLayeredWindowAttributes(hw, COLORREF(0), 1, LWA_ALPHA) };
            } else if sorte == "cle_transparent" {
                // SAFETY: as above.
                let _ = unsafe { SetLayeredWindowAttributes(hw, COLORREF(0), 0, LWA_COLORKEY) };
            }
            // SAFETY: our window.
            let _ = unsafe { ShowWindow(hw, SW_SHOWNOACTIVATE) };
            let ex_res = unsafe { SetWindowDisplayAffinity(hw, WDA_EXCLUDEFROMCAPTURE) };
            let mon = unsafe { SetWindowDisplayAffinity(hw, WDA_MONITOR) };
            let _ = unsafe { SetWindowDisplayAffinity(hw, WDA_NONE) };
            mesure(T, format!("{nom}_{w}x{h}_{sorte}"), format!("exclure {:?} moniteur {:?}", ex_res.map_err(|e| e.code()), mon.map_err(|e| e.code())));
            // SAFETY: our window.
            let _ = unsafe { DestroyWindow(hw) };
        }
    }
}

/// The Win32 overlay by colour key (retained); by per-pixel alpha, measured only.
#[test]
#[ignore = "a Windows desktop (VM)"]
fn bureau_w3_calque() {
    let d = desk();
    let ulw = verifier_calque(&d, 100, Sorte::Win32(Mode::Ulw));
    mesure("w3.win32-ulw.100", "verdict", format!("{ulw:?}"));
    let retenu = verifier_calque(&d, 100, WIN32_RETENU);
    mesure(&format!("w3.{}.100", WIN32_RETENU.nom()), "verdict", format!("{retenu:?}"));
    retenu.unwrap();
}

/// Each order of calque-tauri's calls, as the guide would make it (calque-tauri, built by the workflow).
fn verifier_tauri(d: &Desk, echelle: u32) -> Vec<(&'static str, Result<(), String>)> {
    TAURI_MODES
        .iter()
        .map(|&m| {
            let r = verifier_calque(d, echelle, Sorte::Tauri(m));
            mesure(&format!("w3.tauri-{m}.{echelle}"), "verdict", format!("{r:?}"));
            (m, r)
        })
        .collect()
}

#[test]
#[ignore = "a Windows desktop (VM), CALQUE_TAURI"]
fn bureau_w3_calque_tauri() {
    let d = desk();
    let r = verifier_tauri(&d, 100);
    let retenu = r.iter().find(|(m, _)| *m == TAURI_RETENU).map(|(_, r)| r.clone()).unwrap();
    retenu.unwrap();
}

/// At 125 and 150 %, when the VM lets its scale change: the overlay again, and how PrintWindow draws a
/// program that is not aware of the screen's scale.
#[test]
#[ignore = "a Windows desktop (VM); changes the screen's resolution and scale, put back after"]
fn bureau_w3_echelles() {
    const T: &str = "w3.ecran";
    let d = desk();
    let _p = Physical::enter();
    let (w0, h0) = echelle::resolution().unwrap();
    let modes = echelle::modes();
    mesure(T, "resolution", format!("{w0}x{h0}"));
    mesure(T, "modes", format!("{modes:?}"));
    mesure(T, "echelle_depart", format!("{:?}", echelle::lire()));
    struct Remet(u32, u32);
    impl Drop for Remet {
        fn drop(&mut self) {
            if let Some((rec, _, _)) = echelle::lire() {
                echelle::poser(rec);
            }
            echelle::poser_resolution(self.0, self.1);
        }
    }
    let _remet = Remet(w0, h0);
    if let Some(&(w, h)) = modes.iter().filter(|m| m.0 <= 1920 && m.1 <= 1080).max_by_key(|m| m.0 * m.1) {
        if w * h > w0 * h0 {
            mesure(T, "nouvelle_resolution", format!("{w}x{h} {}", echelle::poser_resolution(w, h)));
            sleep(3000);
        }
    }
    let info = echelle::lire();
    mesure(T, "echelles", format!("{info:?} (recommandée, actuelle, max)"));
    let max = info.map_or(100, |i| i.2);
    let mut fautes = Vec::new();
    for pct in [100, 125, 150] {
        if pct > max {
            mesure(T, format!("echelle_{pct}"), format!("impossible sur cette VM (max {max} %)"));
            fautes.push(format!("{pct} %: not offered"));
            continue;
        }
        if !echelle::poser(pct) {
            fautes.push(format!("{pct} %: refused"));
            continue;
        }
        let want = 96 * pct / 100;
        if within(Duration::from_secs(15), || (echelle::dpi() == want).then_some(())).is_none() {
            fautes.push(format!("{pct} %: dpi {}", echelle::dpi()));
            continue;
        }
        sleep(3000);
        mesure(T, format!("echelle_{pct}"), format!("dpi {}", echelle::dpi()));
        {
            // W1 at this scale: a program aware of the system's scale only (as WinForms ones are).
            let (_p, win) = d.open("fenetre", &format!("Echelle{pct}"), (80, 80, 500, 400), BLUE);
            sleep(500);
            let c = composer(pid_of(win), win).unwrap();
            let fr = frame(win).unwrap();
            mesure(&format!("w1.{pct}"), "cadre", rect_s(fr));
            mesure(&format!("w1.{pct}"), "image", format!("{}x{}", c.img.w, c.img.h));
            mesure(&format!("w1.{pct}"), "bleu", format!("{}/{}", c.img.count(BLUE), c.img.pixels()));
            mesure(&format!("w1.{pct}"), "bornes_bleu", format!("{:?}", c.img.bounds(BLUE)));
        }
        if let Err(e) = verifier_calque(&d, pct, WIN32_RETENU) {
            fautes.push(format!("{pct} % {}: {e}", WIN32_RETENU.nom()));
        }
        if CalqueTauri::exe().is_some() {
            for (m, r) in verifier_tauri(&d, pct) {
                if let (true, Err(e)) = (m == TAURI_RETENU, r) {
                    fautes.push(format!("{pct} % tauri-{m}: {e}"));
                }
            }
        }
    }
    assert!(fautes.is_empty(), "{fautes:?}");
}

// ---------------------------------------------------------------------------------------------------
// W4: change detection

struct Envoi {
    t: Instant,
    img: Rgba,
    left: i32,
    top: i32,
}

struct Veilleur {
    stop: Arc<AtomicBool>,
    envois: Arc<Mutex<Vec<Envoi>>>,
    thread: Option<JoinHandle<(Veille, Vec<f32>)>>,
}

fn veiller(pid: u32, front: HWND) -> Veilleur {
    let stop = Arc::new(AtomicBool::new(false));
    let envois = Arc::new(Mutex::new(Vec::new()));
    let (s, e, id) = (stop.clone(), envois.clone(), id_of(front));
    let thread = std::thread::spawn(move || {
        let front = handle(id);
        let mut v = Veille::depart(&composer(pid, front).unwrap().img, Instant::now());
        let mut durees = Vec::new();
        while !s.load(Ordering::SeqCst) {
            let t = Instant::now();
            let c = composer(pid, front);
            durees.push(t.elapsed().as_secs_f32() * 1000.0);
            if let Some(c) = c {
                if v.voir(Instant::now(), &c.img) == Decision::Envoyer {
                    e.lock().unwrap().push(Envoi { t: Instant::now(), img: c.img, left: c.left, top: c.top });
                }
            }
            std::thread::sleep(v.delai().saturating_sub(t.elapsed()));
        }
        (v, durees)
    });
    Veilleur { stop, envois, thread: Some(thread) }
}

impl Veilleur {
    fn n(&self) -> usize {
        self.envois.lock().unwrap().len()
    }

    fn arreter(&mut self) -> (Veille, Vec<f32>) {
        self.stop.store(true, Ordering::SeqCst);
        self.thread.take().unwrap().join().unwrap()
    }
}

/// 60 s of a clock, a spinner, a marquee bar, a blinking caret and the mouse moving: nothing sent.
#[test]
#[ignore = "a Windows desktop (VM)"]
fn bureau_w4_calme_60s() {
    const T: &str = "w4.calme";
    let d = desk();
    let _p = Physical::enter();
    let (_prog, win) = d.open("vivant", "Vivant", (80, 80, 800, 560), BLUE);
    let f = frame(win).unwrap();
    clic((f.left + 300, f.top + 12));
    until("in front", || devant() == win);
    sleep(1000);
    let mut v = veiller(pid_of(win), win);
    let debut = Instant::now();
    let mut k = 0;
    while debut.elapsed() < Duration::from_secs(60) {
        pointer((f.left + 380 + (k % 10) * 20, f.top + 260 + (k % 3) * 20));
        k += 1;
        sleep(1500);
    }
    let n = v.n();
    let (veille, durees) = v.arreter();
    mesure(T, "envois", n);
    mesure(T, "regards", veille.regards);
    mesure(T, "regards_ou_l_image_bouge", veille.bouge);
    mesure(T, "ecart_max_pct", format!("{:.2}", veille.ecart_max * 100.0));
    mesure(T, "composition_p50_ms", format!("{:.1}", centile(&durees, 0.5)));
    mesure(T, "composition_p90_ms", format!("{:.1}", centile(&durees, 0.9)));
    assert!(veille.bouge * 2 >= veille.regards, "witness: the screen moves in the images");
    assert_eq!(n, 0, "nothing sent");
}

/// A dialog that opens (white for 200 ms, then filled): 1 sending in less than 1.5 s, of the filled
/// dialog; it closes 2 s later: sent, but not before 5 s after the first.
#[test]
#[ignore = "a Windows desktop (VM)"]
fn bureau_w4_dialogue() {
    const T: &str = "w4.dialogue";
    let d = desk();
    let _p = Physical::enter();
    let (_prog, win) = d.open("vivant", "Vivant", (80, 80, 800, 560), BLUE);
    let pid = pid_of(win);
    let f = frame(win).unwrap();
    clic((f.left + 300, f.top + 12));
    until("in front", || devant() == win);
    let nouveau = until_some("its button", || enfants(win).into_iter().find(|&c| title(c) == "Nouveau"));
    let at = centre(window_rect(nouveau).unwrap());
    sleep(1000);
    let mut v = veiller(pid, win);
    sleep(ECART_MIN.as_millis() as u64 + 500);
    let (mut lat, mut fautes) = (Vec::new(), Vec::new());
    for i in 0..8 {
        let n0 = v.n();
        let t0 = Instant::now();
        clic(at);
        let ok = within(Duration::from_secs(3), || (v.n() > n0).then_some(()));
        if ok.is_none() {
            fautes.push(format!("{i}: no sending"));
            break;
        }
        let dialog = until_some("the dialog", || find_window(|h| pid_of(h) == pid && title(h) == "Nouvelle facture"));
        let (l, orange, aire) = {
            let e = v.envois.lock().unwrap();
            let s = &e[n0];
            let df = frame(dialog).unwrap();
            let z = (df.left - s.left, df.top - s.top, df.right - s.left, df.bottom - s.top);
            (s.t.duration_since(t0), s.img.count_in(z, ORANGE), s.img.area(z))
        };
        lat.push(l.as_secs_f32() * 1000.0);
        mesure(T, format!("ouverture_{i}"), format!("{} ms, orange {orange}/{aire}", ms(l)));
        if orange * 10 < aire * 6 {
            fautes.push(format!("{i}: the dialog sent before it was filled"));
        }
        sleep(2000);
        if v.n() != n0 + 1 {
            fautes.push(format!("{i}: {} sendings for one dialog", v.n() - n0));
        }
        let tf = Instant::now();
        touche(VK_ESCAPE);
        if within(Duration::from_secs(9), || (v.n() > n0 + 1).then_some(())).is_none() {
            fautes.push(format!("{i}: the closing never sent"));
            break;
        }
        let (ecart_envois, apres_fermeture) = {
            let e = v.envois.lock().unwrap();
            (e[n0 + 1].t.duration_since(e[n0].t), e[n0 + 1].t.duration_since(tf))
        };
        mesure(T, format!("fermeture_{i}"), format!("{} ms après, {} ms après l'envoi d'avant", ms(apres_fermeture), ms(ecart_envois)));
        if ecart_envois < ECART_MIN {
            fautes.push(format!("{i}: two sendings {} ms apart", ms(ecart_envois)));
        }
        sleep(ECART_MIN.as_millis() as u64 + 300);
    }
    let (veille, durees) = v.arreter();
    mesure(T, "latence_p50_ms", format!("{:.0}", centile(&lat, 0.5)));
    mesure(T, "latence_max_ms", format!("{:.0}", centile(&lat, 1.0)));
    mesure(T, "regards", veille.regards);
    mesure(T, "composition_p90_ms", format!("{:.1}", centile(&durees, 0.9)));
    if lat.iter().any(|&l| l > 1500.0) {
        fautes.push(format!("latency over 1.5 s: {lat:?}"));
    }
    assert!(fautes.is_empty(), "{fautes:?}");
}

/// Idleness, read from the session's last input (GetLastInputInfo): no hook.
#[test]
#[ignore = "a Windows desktop (VM)"]
fn bureau_w4_inactivite() {
    const T: &str = "w4.inactivite";
    send(&[souris(MOUSEEVENTF_MOVE)]);
    let a = inactif();
    sleep(3000);
    let b = inactif();
    touche(VK_SHIFT);
    let c = inactif();
    mesure(T, "apres_souris_ms", ms(a));
    mesure(T, "apres_3s_ms", ms(b));
    mesure(T, "apres_touche_ms", ms(c));
    assert!(a < Duration::from_millis(300) && b >= Duration::from_millis(2900) && c < Duration::from_millis(300));
}

/// No keyboard or mouse hook anywhere in what the guide would run.
#[test]
fn aucun_hook_clavier_souris() {
    let code = [include_str!("calque.rs"), include_str!("compose.rs"), include_str!("navigateur.rs"), include_str!("prims.rs"), include_str!("veille.rs")].concat();
    for interdit in ["SetWindowsHookEx", "WH_KEYBOARD", "WH_MOUSE", "RegisterRawInputDevices"] {
        assert!(!code.contains(interdit), "{interdit}");
    }
}

#[test]
fn vignette_et_ecart() {
    let mut img = Rgba::black(1600, 900);
    let a = vignette(&img);
    assert_eq!(a.len(), 160 * 90);
    img.fill((0, 0, 400, 900), [255, 255, 255]);
    let b = vignette(&img);
    assert!((ecart(&a, &b) - 0.25).abs() < 0.01);
}
