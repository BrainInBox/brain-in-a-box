//! Windows: other programs' windows, the one in front, and their capture by PrintWindow alone, on the
//! `windows` crate already in the build (Tauri's): no other crate, and no BitBlt, ever (R20). The window
//! draws itself into Bibo's memory, so what lies over it on the screen is never in the image; when it
//! will not draw, the capture is black and the user pastes one instead.

use std::ffi::c_void;
use std::mem::size_of;
use std::path::Path;
use std::ptr::null_mut;

use windows::core::{BOOL, PCWSTR, PWSTR};
use windows::Win32::Foundation::{CloseHandle, HWND, LPARAM, RECT};
use windows::Win32::Graphics::Dwm::{DwmGetWindowAttribute, DWMWA_CLOAKED, DWMWA_EXTENDED_FRAME_BOUNDS};
use windows::Win32::Graphics::Gdi::{
    CreateCompatibleDC, CreateDIBSection, DeleteDC, DeleteObject, GdiFlush, SelectObject, BITMAPINFO, BITMAPINFOHEADER, BI_RGB, DIB_RGB_COLORS, HBITMAP, HDC,
    HGDIOBJ,
};
use windows::Win32::Storage::FileSystem::{GetFileVersionInfoSizeW, GetFileVersionInfoW, VerQueryValueW};
use windows::Win32::Storage::Xps::{PrintWindow, PRINT_WINDOW_FLAGS};
use windows::Win32::System::Threading::{OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION};
use windows::Win32::UI::HiDpi::{SetThreadDpiAwarenessContext, DPI_AWARENESS_CONTEXT, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2};
use windows::Win32::UI::Input::KeyboardAndMouse::IsWindowEnabled;
use windows::Win32::UI::WindowsAndMessaging::{
    EnumWindows, GetClassNameW, GetForegroundWindow, GetLastActivePopup, GetWindow, GetWindowLongW, GetWindowRect, GetWindowTextW, GetWindowThreadProcessId, IsIconic,
    IsWindow, IsWindowVisible, GWL_EXSTYLE, GW_OWNER, PW_RENDERFULLCONTENT, WS_EX_APPWINDOW, WS_EX_TOOLWINDOW,
};

use super::{from_bgra, is_terminal, lay, same_window, Found, Rate, Window};
use crate::shot::{Rgba, MAX_SIDE};

/// The shell's own windows: the desktop and the taskbar.
const SHELL: &[&str] = &["Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"];

fn handle(id: u64) -> HWND {
    HWND(id as usize as *mut c_void)
}

fn id_of(h: HWND) -> u64 {
    h.0 as usize as u64
}

fn visible(h: HWND) -> bool {
    // SAFETY: any value is accepted; a handle that is no window is « not visible ».
    unsafe { IsWindowVisible(h) }.as_bool()
}

fn minimized(h: HWND) -> bool {
    // SAFETY: as above.
    unsafe { IsIconic(h) }.as_bool()
}

fn owner(h: HWND) -> Option<HWND> {
    // SAFETY: as above; no owner is an error.
    unsafe { GetWindow(h, GW_OWNER) }.ok().filter(|o| !o.is_invalid())
}

fn pid_of(h: HWND) -> u32 {
    let mut pid = 0;
    // SAFETY: `pid` lives for the call.
    unsafe { GetWindowThreadProcessId(h, Some(&mut pid)) };
    pid
}

fn wide_text(read: impl FnOnce(&mut [u16]) -> i32) -> String {
    let mut buf = [0u16; 512];
    let n = read(&mut buf).clamp(0, buf.len() as i32) as usize;
    String::from_utf16_lossy(&buf[..n])
}

/// Another process's window title is read from the system, without a message to that window.
fn title(h: HWND) -> String {
    // SAFETY: the buffer's length goes with it.
    wide_text(|b| unsafe { GetWindowTextW(h, b) })
}

fn class(h: HWND) -> String {
    // SAFETY: as above.
    wide_text(|b| unsafe { GetClassNameW(h, b) })
}

/// The executable's path: what the window's id is checked against, with its process (R22).
fn image_path(pid: u32) -> Option<String> {
    // SAFETY: a query-only handle, closed below; the buffer's length goes with it.
    let process = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) }.ok()?;
    let mut buf = [0u16; 1024];
    let mut len = buf.len() as u32;
    let read = unsafe { QueryFullProcessImageNameW(process, PROCESS_NAME_WIN32, PWSTR(buf.as_mut_ptr()), &mut len) };
    // SAFETY: our handle, closed once.
    let _ = unsafe { CloseHandle(process) };
    read.ok()?;
    Some(String::from_utf16_lossy(&buf[..(len as usize).min(buf.len())])).filter(|p| !p.is_empty())
}

fn identity(h: HWND) -> Option<Window> {
    // SAFETY: any value is accepted.
    if !unsafe { IsWindow(Some(h)) }.as_bool() {
        return None;
    }
    let pid = pid_of(h);
    (pid != 0).then_some(())?;
    Some(Window { id: id_of(h), pid, app: image_path(pid)? })
}

fn cloaked(h: HWND) -> bool {
    let mut value = 0u32;
    // SAFETY: a u32 for DWMWA_CLOAKED, its size given.
    let read = unsafe { DwmGetWindowAttribute(h, DWMWA_CLOAKED, (&mut value as *mut u32).cast(), size_of::<u32>() as u32) };
    read.is_ok() && value != 0
}

/// A window of the taskbar's kind: on screen (not on another desktop, not a store app's hidden
/// one), not a tool's palette, and its own owner unless that one is hidden (some programs hang all
/// their windows on a hidden one). A dialog comes with its window (`modal`).
fn listed(h: HWND) -> bool {
    if !visible(h) || cloaked(h) {
        return false;
    }
    // SAFETY: any value is accepted.
    let ex = unsafe { GetWindowLongW(h, GWL_EXSTYLE) } as u32;
    if ex & WS_EX_APPWINDOW.0 != 0 {
        return true;
    }
    ex & WS_EX_TOOLWINDOW.0 == 0 && owner(h).is_none_or(|o| !visible(o))
}

/// The program as people call it: its version resource's description (« Microsoft Edge »).
fn description(path: &str) -> Option<String> {
    let wide = |s: &str| s.encode_utf16().chain(Some(0)).collect::<Vec<u16>>();
    let file = wide(path);
    // SAFETY: a NUL-terminated path.
    let size = unsafe { GetFileVersionInfoSizeW(PCWSTR(file.as_ptr()), None) } as usize;
    if size == 0 || size > 1 << 20 {
        return None;
    }
    // u32s: what VerQueryValueW points at in it is aligned for u16.
    let mut data = vec![0u32; size.div_ceil(4)];
    // SAFETY: a buffer of at least `size` bytes.
    unsafe { GetFileVersionInfoW(PCWSTR(file.as_ptr()), None, size as u32, data.as_mut_ptr().cast()) }.ok()?;
    let (base, end) = (data.as_ptr() as usize, data.as_ptr() as usize + size);
    // What Windows points at in `data`, its length as it says, and the u16s left there.
    let query = |sub: &str| -> Option<(*const u16, usize, usize)> {
        let sub = wide(sub);
        let (mut at, mut len) = (null_mut::<c_void>(), 0u32);
        // SAFETY: `data` holds the block GetFileVersionInfoW filled; Windows points into it.
        unsafe { VerQueryValueW(data.as_ptr().cast(), PCWSTR(sub.as_ptr()), &mut at, &mut len) }.as_bool().then_some(())?;
        let p = at as usize;
        (p >= base && p % 2 == 0 && p < end).then_some((at as *const u16, len as usize, (end - p) / 2))
    };
    let (langs, bytes, room) = query("\\VarFileInfo\\Translation")?;
    (bytes >= 4 && room >= 2).then_some(())?;
    // SAFETY: two u16 inside `data` (checked above).
    let (lang, page) = unsafe { (*langs, *langs.add(1)) };
    let (text, units, room) = query(&format!("\\StringFileInfo\\{lang:04x}{page:04x}\\FileDescription"))?;
    // SAFETY: at most the u16s left in `data`.
    let text = String::from_utf16_lossy(unsafe { std::slice::from_raw_parts(text, units.min(room)) });
    Some(text.trim_end_matches('\0').trim().to_string()).filter(|t| !t.is_empty())
}

fn name(path: &str) -> String {
    description(path).unwrap_or_else(|| Path::new(path).file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default())
}

/// The window under this id, if it is another program's the guide may capture.
pub fn describe(id: u64) -> Option<Found> {
    let h = handle(id);
    if !listed(h) {
        return None;
    }
    let window = identity(h)?;
    // Bibo's own windows before their title is asked for: never a message to one of them.
    if window.pid == std::process::id() {
        return None;
    }
    let (titre, class) = (title(h), class(h));
    if titre.trim().is_empty() || SHELL.contains(&class.as_str()) {
        return None;
    }
    Some(Found { nom: name(&window.app), terminal: is_terminal(&window.app, &class), reduite: minimized(h), titre, window })
}

pub fn list() -> Vec<Found> {
    unsafe extern "system" fn each(h: HWND, all: LPARAM) -> BOOL {
        // SAFETY: the Vec lent to EnumWindows below, alive for its whole call.
        unsafe { (*(all.0 as *mut Vec<HWND>)).push(h) };
        BOOL(1)
    }
    let mut all: Vec<HWND> = Vec::new();
    // SAFETY: `each` only pushes to `all`, which outlives the call.
    let _ = unsafe { EnumWindows(Some(each), LPARAM(&mut all as *mut Vec<HWND> as isize)) };
    all.into_iter().filter_map(|h| describe(id_of(h))).collect()
}

/// The window in front: a dialog's own window, up its owners still on screen.
pub fn foreground() -> Option<Found> {
    // SAFETY: no argument.
    let mut h = unsafe { GetForegroundWindow() };
    if h.is_invalid() {
        return None;
    }
    for _ in 0..32 {
        match owner(h).filter(|&o| visible(o)) {
            Some(o) => h = o,
            None => break,
        }
    }
    describe(id_of(h))
}

/// While it lives, this thread counts in physical pixels, as PrintWindow draws: a window's frame and
/// its image agree whatever the screen's scale.
struct Physical(DPI_AWARENESS_CONTEXT);

impl Physical {
    fn enter() -> Self {
        // SAFETY: a context Windows defines; the one before is put back on drop.
        Physical(unsafe { SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) })
    }
}

impl Drop for Physical {
    fn drop(&mut self) {
        if !self.0 .0.is_null() {
            // SAFETY: the context this thread had.
            unsafe { SetThreadDpiAwarenessContext(self.0) };
        }
    }
}

/// A bitmap in Bibo's memory, for a window to draw itself in.
struct Canvas {
    dc: HDC,
    bitmap: HBITMAP,
    before: HGDIOBJ,
    bits: *const u8,
    len: usize,
}

impl Canvas {
    fn new(w: i32, h: i32) -> Option<Self> {
        let info = BITMAPINFO {
            bmiHeader: BITMAPINFOHEADER {
                biSize: size_of::<BITMAPINFOHEADER>() as u32,
                biWidth: w,
                // Top row first.
                biHeight: -h,
                biPlanes: 1,
                biBitCount: 32,
                biCompression: BI_RGB.0,
                ..Default::default()
            },
            ..Default::default()
        };
        // SAFETY: a memory DC of our own, deleted on drop (or here when the bitmap fails).
        let dc = unsafe { CreateCompatibleDC(None) };
        if dc.is_invalid() {
            return None;
        }
        let mut bits = null_mut();
        // SAFETY: `info` describes a 32-bit top-down DIB; Windows allocates it and gives its pixels.
        let bitmap = match unsafe { CreateDIBSection(Some(dc), &info, DIB_RGB_COLORS, &mut bits, None, 0) } {
            Ok(b) if !bits.is_null() => b,
            _ => {
                // SAFETY: our DC, deleted once.
                let _ = unsafe { DeleteDC(dc) };
                return None;
            }
        };
        // SAFETY: our bitmap into our DC; the object before is put back on drop.
        let before = unsafe { SelectObject(dc, bitmap.into()) };
        Some(Canvas { dc, bitmap, before, bits: bits as *const u8, len: w as usize * h as usize * 4 })
    }

    fn pixels(&self) -> &[u8] {
        // SAFETY: what GDI still has to draw is drawn first; the DIB's pixels live as long as `self`.
        unsafe {
            let _ = GdiFlush();
            std::slice::from_raw_parts(self.bits, self.len)
        }
    }
}

impl Drop for Canvas {
    fn drop(&mut self) {
        // SAFETY: our objects, released once, the bitmap after it left the DC.
        unsafe {
            SelectObject(self.dc, self.before);
            let _ = DeleteObject(self.bitmap.into());
            let _ = DeleteDC(self.dc);
        }
    }
}

fn frame(h: HWND) -> Option<RECT> {
    let mut r = RECT::default();
    // SAFETY: a RECT for DWMWA_EXTENDED_FRAME_BOUNDS, its size given.
    unsafe { DwmGetWindowAttribute(h, DWMWA_EXTENDED_FRAME_BOUNDS, (&mut r as *mut RECT).cast(), size_of::<RECT>() as u32) }.ok()?;
    (r.right > r.left && r.bottom > r.top).then_some(r)
}

/// The window drawn by itself into a bitmap of ours, cut to its visible frame (without the invisible
/// borders of Windows 10 and 11), with that frame on the screen; None when it would not draw.
fn print(h: HWND) -> Option<(Rgba, RECT)> {
    let mut outer = RECT::default();
    // SAFETY: a RECT for the call.
    unsafe { GetWindowRect(h, &mut outer) }.ok()?;
    let (w, ht) = (outer.right - outer.left, outer.bottom - outer.top);
    if w <= 0 || ht <= 0 || w as u32 > MAX_SIDE || ht as u32 > MAX_SIDE {
        return None;
    }
    let shown = frame(h).unwrap_or(outer);
    let canvas = Canvas::new(w, ht)?;
    // SAFETY: the window draws itself into our DC; nothing is read from the screen.
    if !unsafe { PrintWindow(h, canvas.dc, PRINT_WINDOW_FLAGS(PW_RENDERFULLCONTENT)) }.as_bool() {
        return None;
    }
    let keep = (shown.left - outer.left, shown.top - outer.top, shown.right - outer.left, shown.bottom - outer.top);
    Some((from_bgra(canvas.pixels(), w as u32, ht as u32, keep), shown))
}

/// The dialog a disabled window waits on (R11): its last active popup, or its hidden owner's, of
/// the same process only.
fn modal(h: HWND, pid: u32) -> Option<HWND> {
    // SAFETY: any value is accepted.
    if unsafe { IsWindowEnabled(h) }.as_bool() {
        return None;
    }
    let up = owner(h);
    [Some(h), up].into_iter().flatten().map(|o| unsafe { GetLastActivePopup(o) }).find(|&p| {
        !p.is_invalid() && p != h && Some(p) != up && visible(p) && !minimized(p) && pid_of(p) == pid
    })
}

/// The window, if it is still the one chosen, as it draws itself, with the dialog it waits on
/// drawn at its place. Checked again afterwards: the image is that window's, not another that took
/// its id meanwhile.
pub fn grab(chosen: &Window) -> Result<Rgba, Rate> {
    let _physical = Physical::enter();
    let h = handle(chosen.id);
    let still = || same_window(chosen, identity(h).as_ref());
    if !still() {
        return Err(Rate::Introuvable);
    }
    if minimized(h) || !visible(h) {
        return Err(Rate::Reduite);
    }
    let (mut image, at) = print(h).ok_or(Rate::Noire)?;
    if let Some((dialog, its)) = modal(h, chosen.pid).and_then(print) {
        lay(&mut image, &dialog, i64::from(its.left - at.left), i64::from(its.top - at.top));
    }
    if !still() {
        return Err(Rate::Introuvable);
    }
    Ok(image)
}

/// On a Windows desktop only, a VM of the CI's: `cargo test -- --ignored bureau --test-threads=1`.
/// Windows of known colours (tests/fixtures/guide/atelier.cs, compiled there by Windows' own csc)
/// and a page in the browser; their pixels are counted in memory. Nothing is written but that program
/// and that page, in a folder of the test's.
#[cfg(test)]
mod tests {
    use super::*;
    use crate::capture::{Screen, System};
    use crate::guide::{self, GuideError};
    use std::path::PathBuf;
    use std::process::{Child, Command};
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::time::{Duration, Instant};
    use windows::core::w;
    use windows::Win32::Foundation::POINT;
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        EnableWindow, SendInput, INPUT, INPUT_0, INPUT_MOUSE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEINPUT, MOUSE_EVENT_FLAGS,
    };
    use windows::Win32::UI::WindowsAndMessaging::{
        CreateWindowExW, DestroyWindow, GetAncestor, GetWindowDisplayAffinity, SetCursorPos, ShowWindowAsync, WindowFromPoint, GA_ROOT, SW_MINIMIZE, SW_RESTORE,
        WINDOW_EX_STYLE, WS_OVERLAPPEDWINDOW, WS_VISIBLE,
    };

    const BLUE: [u8; 3] = [0x1e, 0x88, 0xe5];
    const MAGENTA: [u8; 3] = [0xff, 0x00, 0xff];
    const ORANGE: [u8; 3] = [0xff, 0x98, 0x00];
    const GREY: [u8; 3] = [0x78, 0x78, 0x78];
    const GREEN: [u8; 3] = [0x2e, 0x7d, 0x32];
    const WAIT: Duration = Duration::from_secs(30);

    /// A program the test started, closed with it.
    struct Program(Child);

    impl Drop for Program {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }

    /// The test's folder, with the program of known colours compiled in it.
    struct Desk {
        dir: tempfile::TempDir,
        exe: PathBuf,
        nonce: String,
    }

    fn desk() -> Desk {
        static N: AtomicUsize = AtomicUsize::new(0);
        guide::fin();
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
            .unwrap_or_else(|e| panic!("{}: {e}", csc.display()));
        assert!(out.status.success() && exe.is_file(), "csc: {}", String::from_utf8_lossy(&out.stdout));
        Desk { dir, exe, nonce: format!("{}-{}", std::process::id(), N.fetch_add(1, Ordering::SeqCst)) }
    }

    impl Desk {
        fn open(&self, mode: &str, what: &str, (x, y, w, h): (i32, i32, i32, i32), colors: &[[u8; 3]]) -> (Program, Found) {
            let titre = format!("Atelier {what} {}", self.nonce);
            let mut cmd = Command::new(&self.exe);
            cmd.args([mode, &titre, &x.to_string(), &y.to_string(), &w.to_string(), &h.to_string()]);
            cmd.args(colors.iter().map(|c| format!("{:02x}{:02x}{:02x}", c[0], c[1], c[2])));
            let program = Program(cmd.spawn().unwrap());
            let found = listed_as(|f| f.titre == titre);
            assert_eq!(found.window.pid, program.0.id(), "{titre}");
            (program, found)
        }
    }

    /// The window as the list shows it, once it is there.
    fn listed_as(which: impl Fn(&Found) -> bool) -> Found {
        let start = Instant::now();
        loop {
            if let Some(f) = System.windows().into_iter().find(&which) {
                return f;
            }
            assert!(start.elapsed() < WAIT, "never in the list: {:?}", System.windows().iter().map(|f| f.titre.clone()).collect::<Vec<_>>());
            std::thread::sleep(Duration::from_millis(200));
        }
    }

    fn until(what: &str, done: impl Fn() -> bool) {
        let start = Instant::now();
        while !done() {
            assert!(start.elapsed() < WAIT, "{what}: still not, after {WAIT:?}");
            std::thread::sleep(Duration::from_millis(100));
        }
    }

    /// Pixels of that colour, give or take 3 on each channel.
    fn count(img: &Rgba, c: [u8; 3]) -> usize {
        img.px.chunks_exact(4).filter(|p| (0..3).all(|i| p[i].abs_diff(c[i]) <= 3)).count()
    }

    fn pixels(img: &Rgba) -> usize {
        img.px.len() / 4
    }

    /// The smallest box around the pixels of that colour: (left, top, right, bottom), right and bottom out.
    fn bounds(img: &Rgba, c: [u8; 3]) -> Option<(i32, i32, i32, i32)> {
        let mut b: Option<(i32, i32, i32, i32)> = None;
        for (i, p) in img.px.chunks_exact(4).enumerate() {
            if (0..3).all(|k| p[k].abs_diff(c[k]) <= 3) {
                let (x, y) = ((i as u32 % img.w) as i32, (i as u32 / img.w) as i32);
                b = Some(b.map_or((x, y, x + 1, y + 1), |(l, t, r, bt)| (l.min(x), t.min(y), r.max(x + 1), bt.max(y + 1))));
            }
        }
        b
    }

    /// How many lines are mostly black from each side (left, right, top, bottom): the invisible
    /// borders of Windows 10 and 11 left in, or a window drawn off its place, show as several; the
    /// 1 px line Windows 11 draws around a window itself (not the window) as one.
    fn black_margins(img: &Rgba) -> [u32; 4] {
        let black = |x: u32, y: u32| img.px[((y * img.w + x) * 4) as usize..][..3].iter().all(|&v| v <= 6);
        let col = |x: u32| (0..img.h).filter(|&y| black(x, y)).count() * 2 > img.h as usize;
        let row = |y: u32| (0..img.w).filter(|&x| black(x, y)).count() * 2 > img.w as usize;
        let run = |n: u32, line: &dyn Fn(u32) -> bool| (0..n).take_while(|&i| line(i)).count() as u32;
        [run(img.w, &col), run(img.w, &|i| col(img.w - 1 - i)), run(img.h, &row), run(img.h, &|i| row(img.h - 1 - i))]
    }

    fn center(f: &Found) -> (i32, i32) {
        let r = frame(handle(f.window.id)).unwrap();
        ((r.left + r.right) / 2, (r.top + r.bottom) / 2)
    }

    /// The window on top at this point of the screen.
    fn on_top((x, y): (i32, i32)) -> u64 {
        // SAFETY: plain values.
        id_of(unsafe { GetAncestor(WindowFromPoint(POINT { x, y }), GA_ROOT) })
    }

    /// A click as a hand makes it.
    fn click((x, y): (i32, i32)) {
        // SAFETY: plain values; the inputs live for the call.
        unsafe { SetCursorPos(x, y) }.unwrap();
        let input = |flags: MOUSE_EVENT_FLAGS| INPUT { r#type: INPUT_MOUSE, Anonymous: INPUT_0 { mi: MOUSEINPUT { dwFlags: flags, ..Default::default() } } };
        let sent = unsafe { SendInput(&[input(MOUSEEVENTF_LEFTDOWN), input(MOUSEEVENTF_LEFTUP)], size_of::<INPUT>() as i32) };
        assert_eq!(sent, 2, "SendInput");
    }

    /// Any top-level window with this title, owned ones included (the list leaves those out).
    fn any_titled(titre: &str) -> Option<HWND> {
        unsafe extern "system" fn each(h: HWND, all: LPARAM) -> BOOL {
            // SAFETY: the Vec lent below, alive for the whole call.
            unsafe { (*(all.0 as *mut Vec<HWND>)).push(h) };
            BOOL(1)
        }
        let mut all: Vec<HWND> = Vec::new();
        // SAFETY: `each` only pushes to `all`, which outlives the call.
        let _ = unsafe { EnumWindows(Some(each), LPARAM(&mut all as *mut Vec<HWND> as isize)) };
        all.into_iter().find(|&h| visible(h) && title(h) == titre)
    }

    fn front() -> u64 {
        // SAFETY: no argument.
        id_of(unsafe { GetForegroundWindow() })
    }

    /// R20: a window under another one is captured as it draws itself, without a pixel of the one over
    /// it; as the guide does it too (the list, the choice, the look).
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn bureau_a_window_under_another_has_none_of_it_in_its_capture() {
        let d = desk();
        let (_p, win) = d.open("fenetre", "Factures", (80, 80, 640, 480), &[BLUE]);
        assert_eq!((win.nom.as_str(), win.reduite, win.terminal), ("Atelier Demo", false, false), "its name from its version resource");
        let alone = grab(&win.window).unwrap();
        let shown = frame(handle(win.window.id)).unwrap();
        assert_eq!((alone.w as i32, alone.h as i32), (shown.right - shown.left, shown.bottom - shown.top), "its frame, the invisible borders left out");
        let margins = black_margins(&alone);
        assert!(margins.iter().all(|&m| m <= 1), "no invisible border kept: {margins:?}");
        let blue = count(&alone, BLUE);
        assert!(blue > pixels(&alone) * 8 / 10, "its colour: {blue} of {}", pixels(&alone));

        let (_cover, cover) = d.open("dessus", "Cache", (40, 40, 720, 560), &[MAGENTA]);
        until("the magenta window over it", || on_top(center(&win)) == cover.window.id);
        let under = grab(&win.window).unwrap();
        assert_eq!(count(&under, MAGENTA), 0, "not a pixel of the window over it");
        assert!(count(&under, BLUE).abs_diff(blue) <= blue / 100, "the same image as uncovered: {} and {blue}", count(&under, BLUE));

        assert!(guide::fenetres().unwrap().iter().any(|f| f.id == win.window.id && f.app == "Atelier Demo"));
        guide::choisir(Some(win.window.id)).unwrap();
        let a = guide::regarder().unwrap();
        assert_eq!((a.largeur, a.hauteur, a.noir), (under.w, under.h, false));
        guide::fin();
    }

    /// R20, in a browser: a page drawn by the GPU's compositor, under another window.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn bureau_a_page_in_the_browser_under_another_window() {
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
        let browsers = ["C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe", "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"];
        let browser = browsers.iter().find(|b| Path::new(b).is_file()).expect("Edge or Chrome on this PC");
        let url = format!("file:///{}", page.display().to_string().replace('\\', "/"));
        let _b = Program(
            Command::new(browser)
                .arg(format!("--user-data-dir={}", d.dir.path().join("profil").display()))
                .args(["--no-first-run", "--no-default-browser-check", "--window-position=80,80", "--window-size=800,600"])
                .arg(format!("--app={url}"))
                .spawn()
                .unwrap(),
        );
        let win = listed_as(|f| f.titre.contains(&titre));
        eprintln!("browser: {browser}, its name: {}", win.nom);
        until("the page drawn", || grab(&win.window).is_ok_and(|img| count(&img, GREEN) > 100_000));
        let alone = grab(&win.window).unwrap();
        let green = count(&alone, GREEN);

        let (_cover, cover) = d.open("dessus", "Cache", (40, 40, 880, 680), &[MAGENTA]);
        until("the magenta window over it", || on_top(center(&win)) == cover.window.id);
        let under = grab(&win.window).unwrap();
        assert_eq!(count(&under, MAGENTA), 0, "not a pixel of the window over it");
        assert!(count(&under, GREEN).abs_diff(green) <= green / 50, "the page as uncovered: {} and {green}", count(&under, GREEN));
        assert!(crate::shot::black_share(&under) < 0.1, "drawn, not black: {}", crate::shot::black_share(&under));
    }

    /// R11: a window waiting on a dialog of its own (ERPs open theirs in a window of their own): the
    /// dialog is drawn at its place on the window's capture; it is no line of the list.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn bureau_the_dialog_a_window_waits_on_is_in_its_capture() {
        let d = desk();
        let (_p, win) = d.open("modal", "Saisie", (80, 80, 640, 480), &[BLUE, ORANGE]);
        let h = handle(win.window.id);
        until("its dialog open", || modal(h, win.window.pid).is_some());
        let dialog = modal(h, win.window.pid).unwrap();
        assert!(System.windows().iter().all(|f| f.window.id != id_of(dialog)), "the dialog is not in the list, its window is");
        let img = grab(&win.window).unwrap();
        let at = frame(h).unwrap();
        let its = frame(dialog).unwrap();
        let place = (its.left - at.left, its.top - at.top, its.right - at.left, its.bottom - at.top);
        let orange = bounds(&img, ORANGE).expect("the dialog's colour in the capture");
        assert!(
            orange.0 >= place.0 && orange.1 >= place.1 && orange.2 <= place.2 && orange.3 <= place.3,
            "at its place: {orange:?} within {place:?}"
        );
        let area = (place.2 - place.0) * (place.3 - place.1);
        assert!(count(&img, ORANGE) as i32 > area * 6 / 10, "its colour over most of it: {} of {area}", count(&img, ORANGE));
        assert!(count(&img, BLUE) > pixels(&img) / 3, "and the window around it");

        // A click in the dialog says its window (R4).
        let (_o, other) = d.open("fenetre", "Autre", (740, 440, 260, 220), &[GREY]);
        click(center(&other));
        until("the other window in front", || front() == other.window.id);
        let waiting = std::thread::spawn(guide::attendre_clic);
        std::thread::sleep(Duration::from_millis(600));
        let r = frame(dialog).unwrap();
        click(((r.left + r.right) / 2, (r.top + r.bottom) / 2));
        assert_eq!(waiting.join().unwrap().map(|f| f.id), Ok(win.window.id));
        guide::fin();
    }

    /// R11: a window of another program that makes itself a popup of the chosen one, while that one
    /// waits (disabled), is never drawn on its capture: the same process only.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn bureau_another_programs_popup_is_never_drawn() {
        let d = desk();
        let (_p, win) = d.open("fenetre", "Compta", (80, 80, 640, 480), &[BLUE]);
        let h = handle(win.window.id);
        let mut cmd = Command::new(&d.exe);
        let titre = format!("Atelier Intrus {}", d.nonce);
        cmd.args(["intrus", &titre, "240", "200", "320", "220", "ff00ff", &win.window.id.to_string()]);
        let _intrus = Program(cmd.spawn().unwrap());
        until("the other program's window", || any_titled(&titre).is_some());
        let popup = any_titled(&titre).unwrap();
        assert_eq!(owner(popup).map(id_of), Some(win.window.id), "owned by the chosen window");
        assert_ne!(pid_of(popup), win.window.pid);
        // Clicked once, it is the chosen window's last active popup.
        let r = frame(popup).unwrap();
        let middle = ((r.left + r.right) / 2, (r.top + r.bottom) / 2);
        until("the other program's window over the chosen one", || on_top(middle) == id_of(popup));
        click(middle);
        // SAFETY: any value is accepted.
        until("its last active popup", || unsafe { GetLastActivePopup(h) } == popup);
        // SAFETY: a window of the test's program, enabled again below.
        let _ = unsafe { EnableWindow(h, false) };
        let img = grab(&win.window).unwrap();
        assert_eq!(count(&img, MAGENTA), 0, "not a pixel of the other program's window");
        assert!(count(&img, BLUE) > pixels(&img) * 8 / 10);
        // SAFETY: as above.
        let _ = unsafe { EnableWindow(h, true) };
    }

    /// The system's own message box (a #32770 dialog), as many programs ask « Enregistrer ? ».
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn bureau_a_message_box_is_in_its_windows_capture() {
        let d = desk();
        let (_p, win) = d.open("message", "Question", (80, 80, 640, 480), &[BLUE]);
        let h = handle(win.window.id);
        until("the message box open", || modal(h, win.window.pid).is_some());
        let dialog = modal(h, win.window.pid).unwrap();
        assert_eq!(class(dialog), "#32770");
        let (alone, _) = print(dialog).unwrap();
        let margins = black_margins(&alone);
        eprintln!("message box {}x{}, black margins {margins:?}", alone.w, alone.h);
        assert!(margins.iter().all(|&m| m <= 1), "drawn at its place in its own image: {margins:?}");
        let img = grab(&win.window).unwrap();
        let white = count(&img, [255, 255, 255]);
        assert!(white > pixels(&alone) / 4, "its white in the window's capture: {white}");
        assert!(count(&img, BLUE) > pixels(&img) / 3);
    }

    /// A minimized window is marked in the list and never captured; once opened again, it is.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn bureau_a_minimized_window() {
        let d = desk();
        let (_p, win) = d.open("fenetre", "Reduite", (80, 80, 500, 400), &[BLUE]);
        guide::fenetres().unwrap();
        guide::choisir(Some(win.window.id)).unwrap();
        let h = handle(win.window.id);
        // SAFETY: a window of the test's program.
        let _ = unsafe { ShowWindowAsync(h, SW_MINIMIZE) };
        until("minimized", || minimized(h));
        assert!(System.windows().iter().any(|f| f.window.id == win.window.id && f.reduite), "marked in the list");
        assert_eq!(grab(&win.window).err(), Some(Rate::Reduite));
        assert_eq!(guide::regarder(), Err(GuideError::FenetreReduite));
        // SAFETY: as above.
        let _ = unsafe { ShowWindowAsync(h, SW_RESTORE) };
        until("open again", || !minimized(h));
        until("drawn again", || grab(&win.window).is_ok_and(|img| count(&img, BLUE) > pixels(&img) * 8 / 10));
        assert!(!guide::regarder().unwrap().noir);
        guide::fin();
    }

    /// A window Windows keeps out of captures (a protected one, as a DirectX surface or a program
    /// run as administrator would give): black, and nothing leaves; the user pastes a capture.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn bureau_a_protected_window_is_black_and_never_leaves() {
        let d = desk();
        let (_p, win) = d.open("protege", "Protegee", (80, 80, 500, 400), &[BLUE]);
        let h = handle(win.window.id);
        until("kept out of captures", || {
            let mut affinity = 0u32;
            // SAFETY: a u32 for the call.
            unsafe { GetWindowDisplayAffinity(h, &mut affinity) }.is_ok() && affinity != 0
        });
        match grab(&win.window) {
            Err(Rate::Noire) => eprintln!("protected: PrintWindow refused"),
            Ok(img) => {
                eprintln!("protected: drawn {}x{}, black {}", img.w, img.h, crate::shot::black_share(&img));
                assert_eq!(count(&img, BLUE), 0, "never its pixels");
            }
            Err(e) => panic!("{e:?}"),
        }
        guide::fenetres().unwrap();
        guide::choisir(Some(win.window.id)).unwrap();
        assert_eq!(guide::regarder(), Err(GuideError::CaptureNoire));
        guide::fin();
    }

    /// R4: the window clicked in is the one chosen; Bibo's own windows are never offered.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn bureau_the_click_says_the_window() {
        let d = desk();
        let (_a, other) = d.open("fenetre", "Autre", (640, 420, 300, 260), &[GREY]);
        let (_b, win) = d.open("fenetre", "Clic", (60, 60, 500, 400), &[BLUE]);
        click(center(&other));
        until("the other window in front", || front() == other.window.id);
        let waiting = std::thread::spawn(guide::attendre_clic);
        std::thread::sleep(Duration::from_millis(600));
        click(center(&win));
        let clicked = waiting.join().unwrap().unwrap();
        assert_eq!((clicked.id, clicked.app.as_str(), clicked.terminal), (win.window.id, "Atelier Demo", false));
        guide::choisir(Some(clicked.id)).unwrap();
        let a = guide::regarder().unwrap();
        assert!(!a.noir);
        assert_eq!(guide::choisir(Some(other.window.id)), Err(GuideError::FenetreIntrouvable), "only the window clicked");

        // A window of this process, as Bibo's are: never in the list, never described.
        // SAFETY: a plain window of a system class, destroyed below.
        let own = unsafe {
            CreateWindowExW(WINDOW_EX_STYLE(0), w!("STATIC"), w!("Bibo, ton guide"), WS_OVERLAPPEDWINDOW | WS_VISIBLE, 300, 300, 300, 200, None, None, None, None)
        }
        .unwrap();
        assert!(visible(own));
        assert!(describe(id_of(own)).is_none());
        assert!(System.windows().iter().all(|f| f.window.pid != std::process::id()));
        // SAFETY: our window.
        let _ = unsafe { DestroyWindow(own) };
        guide::fin();
    }

    /// R6: a terminal is marked, and the guide will not take it.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn bureau_a_terminal_is_marked_and_refused() {
        use std::os::windows::process::CommandExt;
        let d = desk();
        let titre = format!("Terminal {}", d.nonce);
        // CREATE_NEW_CONSOLE: a console window of its own, as a user opens one, kept open without reading
        // anything (the test's input is no keyboard).
        let _t = Program(
            Command::new("cmd.exe")
                .raw_arg(format!("/c title {titre}& ping -n 600 127.0.0.1 >nul"))
                .creation_flags(0x10)
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::null())
                .spawn()
                .unwrap(),
        );
        let term = listed_as(|f| f.titre.contains(&titre));
        eprintln!("terminal: {} ({}), class {}", term.nom, term.window.app, class(handle(term.window.id)));
        assert!(term.terminal);
        assert!(guide::fenetres().unwrap().iter().any(|f| f.id == term.window.id && f.terminal));
        assert_eq!(guide::choisir(Some(term.window.id)), Err(GuideError::Terminal));
        guide::fin();
    }

    /// R22: a window closed is not found again, its id checked before each look.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn bureau_a_closed_window_is_not_found() {
        let d = desk();
        let (p, win) = d.open("fenetre", "Fermee", (80, 80, 500, 400), &[BLUE]);
        guide::fenetres().unwrap();
        guide::choisir(Some(win.window.id)).unwrap();
        assert!(guide::regarder().is_ok());
        drop(p);
        // SAFETY: any value is accepted.
        until("closed", || !unsafe { IsWindow(Some(handle(win.window.id))) }.as_bool());
        assert_eq!(grab(&win.window).err(), Some(Rate::Introuvable));
        assert_eq!(guide::regarder(), Err(GuideError::FenetreIntrouvable));
        guide::fin();
    }
}
