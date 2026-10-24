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
    CreateCompatibleDC, CreateDIBSection, DeleteDC, DeleteObject, GdiFlush, MonitorFromWindow, SelectObject, BITMAPINFO, BITMAPINFOHEADER, BI_RGB,
    DIB_RGB_COLORS, HBITMAP, HDC, HGDIOBJ, MONITOR_DEFAULTTONEAREST,
};
use windows::Win32::Storage::FileSystem::{GetFileVersionInfoSizeW, GetFileVersionInfoW, VerQueryValueW};
use windows::Win32::Storage::Xps::{PrintWindow, PRINT_WINDOW_FLAGS};
use windows::Win32::System::Threading::{OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION};
use windows::Win32::UI::HiDpi::{
    GetAwarenessFromDpiAwarenessContext, GetDpiForMonitor, GetDpiForWindow, GetThreadDpiAwarenessContext, GetWindowDpiAwarenessContext,
    SetThreadDpiAwarenessContext, DPI_AWARENESS_CONTEXT, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2, DPI_AWARENESS_PER_MONITOR_AWARE, MDT_EFFECTIVE_DPI,
};
use windows::Win32::UI::Input::KeyboardAndMouse::IsWindowEnabled;
use windows::Win32::UI::WindowsAndMessaging::{
    EnumWindows, GetClassNameW, GetForegroundWindow, GetLastActivePopup, GetWindow, GetWindowLongW, GetWindowRect, GetWindowTextW, GetWindowThreadProcessId,
    IsIconic, IsWindow, IsWindowVisible, GWL_EXSTYLE, GW_OWNER, PW_RENDERFULLCONTENT, WS_EX_APPWINDOW, WS_EX_TOOLWINDOW,
};

use super::{from_bgra, is_terminal, lay, ratio, resized, room, same_window, Found, Rate, Window};
use crate::shot::{Rgba, MAX_SIDE};

/// The shell's own windows: the desktop, the taskbar, and Start and its search (a store app's
/// window, alone at the top only for the system's panels: a store app's own sits in an
/// ApplicationFrameWindow). Opened while Bibo waits for the click, Start is never taken for the
/// user's program.
const SHELL: &[&str] = &["Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow"];

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

pub fn available() -> bool {
    true
}

/// Never asked: PrintWindow is there on every Windows the app runs on.
pub fn raison() -> &'static str {
    "indisponible"
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

/// While it lives, this thread counts in physical pixels, as PrintWindow draws a window aware of the
/// screen's scale: its frame and its image agree. One Windows stretches draws at its own (`image_ratio`,
/// which needs it).
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

/// Aware of each screen's scale (per monitor, v1 or v2), as this context says: a window's or a thread's.
fn per_monitor(context: DPI_AWARENESS_CONTEXT) -> bool {
    // SAFETY: any value is accepted; one that is no context reads as invalid.
    let awareness = unsafe { GetAwarenessFromDpiAwarenessContext(context) };
    awareness == DPI_AWARENESS_PER_MONITOR_AWARE
}

/// A capture's pixels to a pixel of the screen (`ratio`): the window's DPI and its screen's, that one
/// as this thread reads it, and whether it really counts in physical pixels (Windows may have refused
/// the token's context).
fn image_ratio(h: HWND, _: &Physical) -> f64 {
    let (mut screen, mut y) = (0, 0);
    // SAFETY: a window handle and plain values; two u32 for the call.
    let (own, read, window, thread) = unsafe {
        let monitor = MonitorFromWindow(h, MONITOR_DEFAULTTONEAREST);
        let read = GetDpiForMonitor(monitor, MDT_EFFECTIVE_DPI, &mut screen, &mut y);
        (GetDpiForWindow(h), read, GetWindowDpiAwarenessContext(h), GetThreadDpiAwarenessContext())
    };
    ratio(own, if read.is_ok() { screen } else { 0 }, per_monitor(window), per_monitor(thread))
}

/// A length on the screen in a capture's pixels.
fn in_image(v: i32, ratio: f64) -> i32 {
    (f64::from(v) * ratio).round() as i32
}

fn frame(h: HWND) -> Option<RECT> {
    let mut r = RECT::default();
    // SAFETY: a RECT for DWMWA_EXTENDED_FRAME_BOUNDS, its size given.
    unsafe { DwmGetWindowAttribute(h, DWMWA_EXTENDED_FRAME_BOUNDS, (&mut r as *mut RECT).cast(), size_of::<RECT>() as u32) }.ok()?;
    (r.right > r.left && r.bottom > r.top).then_some(r)
}

/// The window drawn by itself into a bitmap of ours, cut to its visible frame (without the invisible
/// borders of Windows 10 and 11) at the size it drew, with that frame on the screen and the ratio it
/// drew at (`image_ratio`); None when it would not draw.
fn print(h: HWND, physical: &Physical) -> Option<(Rgba, RECT, f64)> {
    let mut outer = RECT::default();
    // SAFETY: a RECT for the call.
    unsafe { GetWindowRect(h, &mut outer) }.ok()?;
    // Read once, before it draws: the image and its cut at the same scale. Aware of a bigger scale than
    // its screen's, it draws bigger than its frame: room for all of it.
    let (w, ht, r) = room(outer.right - outer.left, outer.bottom - outer.top, image_ratio(h, physical))?;
    let shown = frame(h).unwrap_or(outer);
    let canvas = Canvas::new(w, ht)?;
    // SAFETY: the window draws itself into our DC; nothing is read from the screen.
    if !unsafe { PrintWindow(h, canvas.dc, PRINT_WINDOW_FLAGS(PW_RENDERFULLCONTENT)) }.as_bool() {
        return None;
    }
    let mine = |v: i32| in_image(v, r);
    let keep = (mine(shown.left - outer.left), mine(shown.top - outer.top), mine(shown.right - outer.left), mine(shown.bottom - outer.top));
    Some((from_bgra(canvas.pixels(), w as u32, ht as u32, keep), shown, r))
}

/// The dialog a disabled window waits on (R11): its last active popup, or its hidden owner's, of
/// the same process only.
fn modal(h: HWND, pid: u32) -> Option<HWND> {
    // SAFETY: any value is accepted.
    if unsafe { IsWindowEnabled(h) }.as_bool() {
        return None;
    }
    let up = owner(h);
    [Some(h), up]
        .into_iter()
        .flatten()
        .map(|o| unsafe { GetLastActivePopup(o) })
        .find(|&p| !p.is_invalid() && p != h && Some(p) != up && visible(p) && !minimized(p) && pid_of(p) == pid)
}

/// The window, if it is still the one chosen, as it draws itself, with the dialog it waits on
/// drawn at its place. Checked again afterwards: the image is that window's, not another that took
/// its id meanwhile. Its pixels are the window's own: `image_ratio` of one of the screen (1 for a
/// program aware of the scale, 2/3 for one Windows stretches to 150 %); a point of the screen lies in
/// it at (point - its frame's corner) x that ratio.
pub fn grab(chosen: &Window) -> Result<Rgba, Rate> {
    let physical = Physical::enter();
    let h = handle(chosen.id);
    let still = || same_window(chosen, identity(h).as_ref());
    if !still() {
        return Err(Rate::Introuvable);
    }
    if minimized(h) || !visible(h) {
        return Err(Rate::Reduite);
    }
    let (mut image, at, r) = print(h, &physical).ok_or(Rate::Noire)?;
    if let Some((mut dialog, its, own)) = modal(h, chosen.pid).and_then(|d| print(d, &physical)) {
        // A dialog of another scale than its window (a program of mixed parts), at the window's.
        let (w, ht) = (in_image(its.right - its.left, r) as u32, in_image(its.bottom - its.top, r) as u32);
        if own != r && w <= MAX_SIDE && ht <= MAX_SIDE {
            dialog = resized(&dialog, w, ht).unwrap_or(dialog);
        }
        lay(&mut image, &dialog, in_image(its.left - at.left, r).into(), in_image(its.top - at.top, r).into());
    }
    if !still() {
        return Err(Rate::Introuvable);
    }
    Ok(image)
}

/// On a Windows desktop only, a VM of the CI's: `cargo test -- --ignored bureau --test-threads=1`. Those
/// named echelle_baissee change the screen's scale: in the CI's job bureau-echelle only.
/// Windows of known colours (tests/fixtures/guide/atelier.cs, compiled there by Windows' own csc)
/// and a page in the browser; their pixels are counted in memory. Nothing is written but that program
/// and that page, in a folder of the test's.
#[cfg(test)]
mod tests {
    use super::*;
    use crate::capture::bureau::*;
    use crate::capture::{Screen, System};
    use crate::guide::{self, GuideError};
    use std::cell::Cell;
    use std::path::PathBuf;
    use std::process::Command;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::time::{Duration, Instant};
    use windows::core::w;
    use windows::Win32::Foundation::POINT;
    use windows::Win32::UI::HiDpi::{
        AreDpiAwarenessContextsEqual, GetDpiFromDpiAwarenessContext, DPI_AWARENESS_CONTEXT_UNAWARE, DPI_AWARENESS_CONTEXT_UNAWARE_GDISCALED,
    };
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        EnableWindow, SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, INPUT_MOUSE, KEYBDINPUT, KEYBD_EVENT_FLAGS, KEYEVENTF_KEYUP, MOUSEEVENTF_LEFTDOWN,
        MOUSEEVENTF_LEFTUP, MOUSEINPUT, MOUSE_EVENT_FLAGS, VIRTUAL_KEY, VK_ESCAPE, VK_LWIN,
    };
    use windows::Win32::UI::WindowsAndMessaging::{
        CreateWindowExW, DestroyWindow, GetAncestor, GetWindowDisplayAffinity, SetCursorPos, ShowWindowAsync, WindowFromPoint, GA_ROOT, SW_MINIMIZE,
        SW_RESTORE, WINDOW_EX_STYLE, WS_OVERLAPPEDWINDOW, WS_VISIBLE,
    };

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

    /// The window on top at this point of the screen (its pixels, as DWM's frames count).
    fn on_top((x, y): (i32, i32)) -> u64 {
        let _physical = Physical::enter();
        // SAFETY: plain values.
        id_of(unsafe { GetAncestor(WindowFromPoint(POINT { x, y }), GA_ROOT) })
    }

    /// A click as a hand makes it, at this point of the screen (its pixels).
    fn click((x, y): (i32, i32)) {
        let _physical = Physical::enter();
        // SAFETY: plain values; the inputs live for the call.
        unsafe { SetCursorPos(x, y) }.unwrap();
        let input = |flags: MOUSE_EVENT_FLAGS| INPUT { r#type: INPUT_MOUSE, Anonymous: INPUT_0 { mi: MOUSEINPUT { dwFlags: flags, ..Default::default() } } };
        let sent = unsafe { SendInput(&[input(MOUSEEVENTF_LEFTDOWN), input(MOUSEEVENTF_LEFTUP)], size_of::<INPUT>() as i32) };
        assert_eq!(sent, 2, "SendInput");
    }

    /// A key pressed and let go, as a hand does.
    fn key(vk: VIRTUAL_KEY) {
        let input = |flags: KEYBD_EVENT_FLAGS| INPUT { r#type: INPUT_KEYBOARD, Anonymous: INPUT_0 { ki: KEYBDINPUT { wVk: vk, dwFlags: flags, ..Default::default() } } };
        // SAFETY: plain values; the inputs live for the call.
        let sent = unsafe { SendInput(&[input(KEYBD_EVENT_FLAGS(0)), input(KEYEVENTF_KEYUP)], size_of::<INPUT>() as i32) };
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

    /// A window as a failed wait says it: its title, class, process and program.
    fn who(id: u64) -> String {
        let h = handle(id);
        format!("« {} » ({}, pid {}, {})", title(h), class(h), pid_of(h), image_path(pid_of(h)).unwrap_or_default())
    }

    /// Until `now` gives that window (the one on top at a point, the one in front); if never, which
    /// window it gave instead, and the one in front.
    fn until_is(what: &str, now: impl Fn() -> u64, want: u64) {
        let start = Instant::now();
        loop {
            let got = now();
            if got == want {
                return;
            }
            assert!(start.elapsed() < WAIT, "{what}: still not, after {WAIT:?}: {} instead of {}; in front {}", who(got), who(want), who(front()));
            std::thread::sleep(Duration::from_millis(100));
        }
    }

    /// R20: a window under another one is captured as it draws itself, without a pixel of the one over
    /// it; as the guide does it too (the list, the choice, the look). At its own size, and stretched by
    /// Windows (a program unaware of the scale, at 125 and 150 % in the job bureau-echelle).
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn bureau_a_window_under_another_has_none_of_it_in_its_capture() {
        let d = desk();
        for (mode, s) in [("fenetre", 1.0), ("fenetre-etiree", 100.0 / f64::from(echelle()))] {
            let (_p, win) = d.open(mode, &format!("Factures {mode}"), (80, 80, 640, 480), &[BLUE]);
            assert_eq!((win.nom.as_str(), win.reduite, win.terminal), ("Atelier Demo", false, false), "its name from its version resource");
            let alone = grab(&win.window).unwrap();
            let shown = frame(handle(win.window.id)).unwrap();
            let want = |v: i32| (f64::from(v) * s).round() as i32;
            let (w, h) = (want(shown.right - shown.left), want(shown.bottom - shown.top));
            let slack = if s == 1.0 { 0 } else { 2 };
            assert!(
                (alone.w as i32).abs_diff(w) <= slack && (alone.h as i32).abs_diff(h) <= slack,
                "{mode}: its frame at the size it draws, the invisible borders left out: {}x{}, not {w}x{h}",
                alone.w,
                alone.h
            );
            let margins = black_margins(&alone);
            assert!(margins.iter().all(|&m| m <= 1), "{mode}: no invisible border kept: {margins:?}");
            let under = covered_keeps_none_of_it(&d, &win, mode);

            assert!(guide::fenetres().unwrap().iter().any(|f| f.id == win.window.id && f.app == "Atelier Demo"));
            guide::choisir(Some(win.window.id)).unwrap();
            let a = guide::regarder().unwrap();
            assert_eq!((a.largeur, a.hauteur, a.noir), (under.w, under.h, false));
            guide::fin();
        }
    }

    /// The window captured alone, then under a magenta one always on top: not a pixel of it, the same
    /// image. The image under it.
    fn covered_keeps_none_of_it(d: &Desk, win: &Found, what: &str) -> Rgba {
        let alone = grab(&win.window).unwrap();
        let blue = count(&alone, BLUE);
        assert!(blue > pixels(&alone) * 8 / 10, "{what}: its colour: {blue} of {}", pixels(&alone));
        let (_cover, cover) = d.open("dessus", &format!("Cache {what}"), (40, 40, 720, 560), &[MAGENTA]);
        until_is("the magenta window over it", || on_top(center(win)), cover.window.id);
        let under = grab(&win.window).unwrap();
        assert_eq!(count(&under, MAGENTA), 0, "{what}: not a pixel of the window over it");
        assert!(count(&under, BLUE).abs_diff(blue) <= blue / 100, "{what}: the same image as uncovered: {} and {blue}", count(&under, BLUE));
        under
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
        until_is("the magenta window over it", || on_top(center(&win)), cover.window.id);
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
        assert!(orange.0 >= place.0 && orange.1 >= place.1 && orange.2 <= place.2 && orange.3 <= place.3, "at its place: {orange:?} within {place:?}");
        let area = (place.2 - place.0) * (place.3 - place.1);
        assert!(count(&img, ORANGE) as i32 > area * 6 / 10, "its colour over most of it: {} of {area}", count(&img, ORANGE));
        assert!(count(&img, BLUE) > pixels(&img) / 3, "and the window around it");

        // A click in the dialog says its window (R4).
        let (_o, other) = d.open("fenetre", "Autre", (740, 440, 260, 220), &[GREY]);
        click(center(&other));
        until_is("the other window in front", front, other.window.id);
        let waiting = std::thread::spawn(guide::attendre_clic);
        std::thread::sleep(Duration::from_millis(600));
        let r = frame(dialog).unwrap();
        click(((r.left + r.right) / 2, (r.top + r.bottom) / 2));
        assert_eq!(waiting.join().unwrap().map(|f| f.id), Ok(win.window.id));
        guide::fin();
    }

    /// The frame of a window as its program counts it: Windows gives a program unaware of the screen's
    /// scale (and a thread unaware too) 96 dpi coordinates, and stretches its windows on the screen.
    fn its_own(h: HWND) -> RECT {
        let mut r = RECT::default();
        // SAFETY: a context Windows defines, the one before put back; a RECT for the call.
        unsafe {
            let before = SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_UNAWARE);
            let read = GetWindowRect(h, &mut r);
            SetThreadDpiAwarenessContext(before);
            read.unwrap();
        }
        r
    }

    /// The screen's scale: the job bureau-echelle's (test.yml), or 100 %.
    fn echelle() -> u32 {
        std::env::var("BIBO_ECHELLE").map_or(100, |e| e.parse().unwrap())
    }

    fn dpi(h: HWND) -> u32 {
        // SAFETY: a window handle.
        unsafe { GetDpiForWindow(h) }
    }

    /// The window of one of the atelier's scale modes and its dialog, once open.
    fn open_with_dialog(d: &Desk, mode: &str) -> (Program, Found, HWND, HWND) {
        let (p, win) = d.open(mode, mode, (80, 80, 640, 480), &[BLUE, ORANGE]);
        let h = handle(win.window.id);
        until("its dialog open", || modal(h, win.window.pid).is_some());
        let dialog = modal(h, win.window.pid).unwrap();
        (p, win, h, dialog)
    }

    /// A window of the atelier's scale modes captured whole: the image `s` of its frame on the screen
    /// (a capture's pixels to one of the screen), nothing left black but the line Windows 11 draws around
    /// a window (1 px at 96 dpi, 2 at 144) and what rounding leaves when it is drawn bigger than its
    /// frame, its green band along the right and bottom edges (a capture cut short loses it); its dialog,
    /// `sd` of its own frame alone, laid at its place at the window's scale: where it shows alone, to 3
    /// pixels.
    fn captured_whole(win: &Found, dialog: HWND, s: f64, sd: f64) {
        let h = handle(win.window.id);
        // DWM's frames are the screen's pixels, whatever the thread counts in.
        let (at, its) = (frame(h).unwrap(), frame(dialog).unwrap());
        let img = grab(&win.window).unwrap();
        let alone = print(dialog, &Physical::enter()).unwrap().0;
        let scaled = |v: i32, k: f64| (f64::from(v) * k).round() as i32;
        let size = (scaled(at.right - at.left, s), scaled(at.bottom - at.top, s));
        let alone_size = (scaled(its.right - its.left, sd), scaled(its.bottom - its.top, sd));
        let green = |x: u32, y: u32| img.px[((y * img.w + x) * 4) as usize..][..3].iter().zip(GREEN).all(|(&v, c)| v.abs_diff(c) <= 3);
        let right = (0..img.h).filter(|&y| green(img.w - 4, y)).count() * 2 > img.h as usize;
        let bottom = (0..img.w).filter(|&x| green(x, img.h - 4)).count() * 2 > img.w as usize;
        let (k, dx, dy) = (s / sd, scaled(its.left - at.left, s), scaled(its.top - at.top, s));
        let o = bounds(&alone, ORANGE).expect("the dialog's colour in its own image");
        let place = (dx + scaled(o.0, k), dy + scaled(o.1, k), dx + scaled(o.2, k), dy + scaled(o.3, k));
        let orange = bounds(&img, ORANGE).expect("the dialog's colour in the capture");
        let (margins, alone_margins) = (black_margins(&img), black_margins(&alone));
        // The line, then, drawn bigger than its frame (k > 1), what the rounding leaves: its frame and its
        // rect on the screen each rounded to a pixel of the screen, k of the image's (3 and 4 px at 150 %).
        let edge = |w: HWND, k: f64| dpi(w).div_ceil(96) + if k > 1.0 { (k + 0.5).floor() as u32 } else { 0 };
        eprintln!(
            "{} at {} %, window {s:.3} dialog {sd:.3}: image {}x{} for {size:?}, black margins {margins:?}, green band right {right} bottom {bottom}; dialog alone {}x{} for {alone_size:?}, black margins {alone_margins:?}, its colour {orange:?} for {place:?}",
            win.titre,
            echelle(),
            img.w,
            img.h,
            alone.w,
            alone.h
        );
        assert!(margins.iter().all(|&m| m <= edge(h, s)), "nothing left black: {margins:?}");
        assert!(right && bottom, "its green band along the right and bottom edges: right {right}, bottom {bottom}");
        assert!((img.w as i32).abs_diff(size.0) <= 2 && (img.h as i32).abs_diff(size.1) <= 2, "its size: {}x{}, not {size:?}", img.w, img.h);
        assert!(alone_margins.iter().all(|&m| m <= edge(dialog, sd)), "the dialog alone, nothing black: {alone_margins:?}");
        assert!(
            (alone.w as i32).abs_diff(alone_size.0) <= 2 && (alone.h as i32).abs_diff(alone_size.1) <= 2,
            "the dialog alone: {}x{}, not {alone_size:?}",
            alone.w,
            alone.h
        );
        let off = [orange.0 - place.0, orange.1 - place.1, orange.2 - place.2, orange.3 - place.3];
        assert!(off.iter().all(|d| d.abs() <= 3), "the dialog at its place and its size: {orange:?}, not {place:?}");
        // Over a third of what the dialog leaves (stretched to 150 %, the one of mixed parts covers 42 %).
        let left = pixels(&img).saturating_sub(((place.2 - place.0) * (place.3 - place.1)) as usize);
        assert!(count(&img, BLUE) > left / 3, "and the window around it: {} blue of {left}", count(&img, BLUE));
    }

    /// A program unaware of the screen's scale (many an old ERP): at 125 or 150 %, Windows stretches its
    /// window and its dialog on the screen, while PrintWindow draws them at their own size. The capture
    /// is that image, whole: no black left from the screen's size, the dialog where the program put it.
    /// At 100 % with the others; at 125 and 150 % in the job bureau-echelle (test.yml), which says the
    /// scale in BIBO_ECHELLE.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn bureau_echelle_a_window_windows_stretches_is_captured_whole() {
        let d = desk();
        let (_p, win, h, dialog) = open_with_dialog(&d, "etiree");
        assert_eq!((dpi(h), dpi(dialog)), (96, 96), "a program unaware of the scale");
        let own = its_own(h);
        let outer = {
            let _physical = Physical::enter();
            let mut outer = RECT::default();
            // SAFETY: a RECT for the call.
            unsafe { GetWindowRect(h, &mut outer) }.unwrap();
            outer
        };
        // The program's pixels to a pixel of the screen, as Windows stretched its window.
        let s = f64::from(own.right - own.left) / f64::from(outer.right - outer.left);
        let want = 100.0 / f64::from(echelle());
        assert!((s - want).abs() < 0.01, "the screen at {} %: the window stretched {s:.3}, not {want:.3}", echelle());
        captured_whole(&win, dialog, s, s);
    }

    /// A program aware of each screen's scale (per monitor v2), as most are today (Edge, Office, the
    /// Explorer): drawn at the screen's pixels, captured as it is on the screen.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn bureau_echelle_a_window_aware_of_the_scale_is_captured_whole() {
        let d = desk();
        let (_p, win, h, dialog) = open_with_dialog(&d, "conscient");
        let screen = 96 * echelle() / 100;
        assert_eq!((dpi(h), dpi(dialog)), (screen, screen), "aware of the screen's scale");
        captured_whole(&win, dialog, 1.0, 1.0);
    }

    /// The ratio as a thread counting in physical pixels reads it, and never for a program aware of each
    /// screen's scale (never stretched by Windows, whatever its screen's DPI reads: astride two screens,
    /// or its program not yet caught up with a change of scale). The screen read by a thread counting in
    /// 96 dpi points, as one Windows refused the physical pixels would (Physical's token lying): 1, cut as
    /// it is on the screen, never a ratio of two scales.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn bureau_echelle_a_ratio_read_in_physical_pixels_only() {
        let d = desk();
        let stretched = 100.0 / f64::from(echelle());
        for (mode, aware, want) in [("conscient", true, 1.0), ("mixte", true, 1.0), ("systeme", false, 1.0), ("etiree", false, stretched)] {
            let (_p, _, h, dialog) = open_with_dialog(&d, mode);
            // SAFETY: window handles.
            let (window, its_dialog) = unsafe { (per_monitor(GetWindowDpiAwarenessContext(h)), per_monitor(GetWindowDpiAwarenessContext(dialog))) };
            assert_eq!((window, its_dialog), (aware, aware && mode != "mixte"), "{mode}: aware of each screen's scale, the window and its dialog");
            let physical = Physical::enter();
            // SAFETY: no argument.
            assert!(per_monitor(unsafe { GetThreadDpiAwarenessContext() }), "the token's thread counts in physical pixels");
            let r = image_ratio(h, &physical);
            drop(physical);
            // SAFETY: a context Windows defines, the one before put back when the token drops.
            let unaware = Physical(unsafe { SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_UNAWARE) });
            let mut read = (0, 0);
            // SAFETY: a window handle; two u32 for the call.
            let _ = unsafe { GetDpiForMonitor(MonitorFromWindow(h, MONITOR_DEFAULTTONEAREST), MDT_EFFECTIVE_DPI, &mut read.0, &mut read.1) };
            let lied = image_ratio(h, &unaware);
            drop(unaware);
            eprintln!("{mode} at {} %: dpi {}, ratio {r}; its screen read in points at {} dpi, ratio {lied}", echelle(), dpi(h), read.0);
            assert!((r - want).abs() < 1e-9, "{mode}: ratio {r}, not {want}");
            assert_eq!(read.0, 96, "{mode}: the screen read in points");
            assert_eq!(lied, 1.0, "{mode}: read in points, cut as it is on the screen");
        }
    }

    /// A dialog unaware of the scale over a window aware of it (a program made of old and new parts):
    /// Windows stretches the dialog alone. In the window's capture it is at the window's scale.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn bureau_echelle_a_dialog_of_another_scale_than_its_window() {
        let d = desk();
        let (_p, win, h, dialog) = open_with_dialog(&d, "mixte");
        let screen = 96 * echelle() / 100;
        assert_eq!((dpi(h), dpi(dialog)), (screen, 96), "the window aware of the scale, its dialog not");
        captured_whole(&win, dialog, 1.0, 96.0 / f64::from(screen));
    }

    /// A program unaware of the scale whose drawing GDI scales: « Système (amélioré) » in its
    /// Compatibility tab, ticked against an old ERP's blur. Sharp on the screen, PrintWindow draws it at
    /// its own size all the same, as one Windows stretches (the CI's Windows at 125 and 150 %).
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn bureau_echelle_a_window_gdi_scales_is_captured_whole() {
        let d = desk();
        let (_p, win, h, dialog) = open_with_dialog(&d, "gdi");
        // SAFETY: a window handle; a context Windows defines.
        let gdi = unsafe { AreDpiAwarenessContextsEqual(GetWindowDpiAwarenessContext(h), DPI_AWARENESS_CONTEXT_UNAWARE_GDISCALED) }.as_bool();
        assert_eq!((dpi(h), dpi(dialog), gdi), (96, 96, true), "unaware of the scale, its drawing scaled by GDI");
        let s = 100.0 / f64::from(echelle());
        captured_whole(&win, dialog, s, s);
    }

    /// The scale the job bureau-echelle set (BIBO_ECHELLE), the tests that lower it being on the CI's
    /// runner only: never a developer's screen changed.
    fn runner_scale(var: impl Fn(&str) -> Option<String>) -> Result<u32, String> {
        var("BIBO_ECHELLE").and_then(|e| e.parse().ok()).ok_or_else(|| "the job bureau-echelle's scale, 125 or 150 (BIBO_ECHELLE)".to_string())
    }

    #[test]
    fn the_scale_is_lowered_on_the_cis_runner_only() {
        fn env(vars: &'static [(&'static str, &'static str)]) -> impl Fn(&str) -> Option<String> {
            move |k| vars.iter().find(|(n, _)| *n == k).map(|(_, v)| v.to_string())
        }
        assert_eq!(runner_scale(env(&[("GITHUB_ACTIONS", "true"), ("BIBO_ECHELLE", "150")])), Ok(150));
        assert!(runner_scale(env(&[("BIBO_ECHELLE", "125")])).is_err(), "a developer's PC, the scale exported");
        assert!(runner_scale(env(&[("GITHUB_ACTIONS", "true")])).is_err());
        assert!(runner_scale(env(&[("GITHUB_ACTIONS", "true"), ("BIBO_ECHELLE", "100")])).is_err(), "at 100 %, nothing to lower");
    }

    /// echelle.cs (.github/ci), compiled in the test's folder: the screen's size and scale, read, and set
    /// as Settings sets them.
    fn scale_tool(d: &Desk) -> PathBuf {
        let csc = PathBuf::from(std::env::var("WINDIR").unwrap()).join("Microsoft.NET").join("Framework64").join("v4.0.30319").join("csc.exe");
        let exe = d.dir.path().join("echelle.exe");
        let source = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..").join(".github").join("ci").join("echelle.cs");
        let out = Command::new(&csc).args(["/nologo", "/target:exe"]).arg(format!("/out:{}", exe.display())).arg(&source).output().unwrap();
        assert!(out.status.success() && exe.is_file(), "csc: {}", String::from_utf8_lossy(&out.stdout));
        exe
    }

    /// The screen as it is: width, height and scale (%).
    fn screen_now(tool: &Path) -> (u32, u32, u32) {
        let out = Command::new(tool).output().unwrap();
        let said = String::from_utf8_lossy(&out.stdout).trim().to_string();
        let v: Vec<u32> = said.split_whitespace().filter_map(|n| n.parse().ok()).collect();
        assert!(out.status.success() && v.len() == 3, "echelle.exe: {said}");
        (v[0], v[1], v[2])
    }

    fn set_screen(tool: &Path, (w, h, percent): (u32, u32, u32)) -> Result<(), String> {
        let out = Command::new(tool).args([w, h, percent].map(|v| v.to_string())).output().map_err(|e| e.to_string())?;
        let said = String::from_utf8_lossy(&out.stdout).trim().replace('\n', " / ");
        eprintln!("echelle.exe {w}x{h} {percent} %: {said}");
        out.status.success().then_some(()).ok_or(said)
    }

    /// The screen put back as it was when it drops.
    struct Back(PathBuf, (u32, u32, u32));

    impl Drop for Back {
        fn drop(&mut self) {
            if let Err(e) = set_screen(&self.0, self.1) {
                eprintln!("the screen not put back to {:?}: {e}", self.1);
            }
        }
    }

    /// The screen's scale lowered to 100 % without signing out (put back as it was read when the guard
    /// drops), once the windows `still` stand still at it: Windows moves the windows once the scale has
    /// changed (a dialog once caught 63 px from where its frame said, at 125 %).
    fn lowered(tool: &Path, still: &[HWND]) -> Back {
        let before = screen_now(tool);
        let back = Back(tool.to_path_buf(), before);
        set_screen(tool, (before.0, before.1, 100)).unwrap();
        let last = Cell::new((Vec::new(), 0));
        until("the windows still at the new scale", || {
            let now: Vec<Option<RECT>> = still.iter().map(|&h| frame(h)).collect();
            let (before, n) = last.take();
            let n = if now.iter().all(Option::is_some) && before == now { n + 1 } else { 0 };
            last.set((now, n));
            n >= 5
        });
        back
    }

    /// A program aware of the scale the system had when it opened (SetProcessDPIAware, as many an ERP),
    /// then the screen's scale lowered without signing out, as a laptop's at 150 % beside a screen at
    /// 100 % where the window goes: Windows shrinks it on the screen, while PrintWindow draws it bigger
    /// than its frame there. Captured whole all the same. It changes the screen's scale (put back after):
    /// on a VM of the CI's only, the job bureau-echelle, never with the tests named bureau.
    #[test]
    #[ignore = "changes the screen's scale: the CI's job bureau-echelle"]
    fn echelle_baissee_a_window_drawn_bigger_than_its_frame_is_captured_whole() {
        let echelle = runner_scale(|k| std::env::var(k).ok()).unwrap();
        let d = desk();
        let tool = scale_tool(&d);
        let (_p, win, h, dialog) = open_with_dialog(&d, "systeme");
        let opened = dpi(h);
        // SAFETY: a window handle.
        let its_system = unsafe { GetDpiFromDpiAwarenessContext(GetWindowDpiAwarenessContext(h)) };
        eprintln!("systeme opened at {echelle} %: dpi {opened} (dialog {}), its system's {its_system}", dpi(dialog));
        assert_eq!(opened, 96 * echelle / 100, "opened at {echelle} %, it takes the screen's scale as its system's");
        let back = lowered(&tool, &[h, dialog]);
        let before = back.1;
        assert_eq!((dpi(h), dpi(dialog)), (opened, opened), "its scale kept, the screen's lowered");
        let s = f64::from(opened) / 96.0;
        captured_whole(&win, dialog, s, s);
        drop(back);
        assert_eq!(screen_now(&tool), before, "the screen put back as it was");
    }

    /// The same, drawn bigger than its frame: a window over it, another program's popup, and a window kept
    /// out of captures (on the screen, or out of every capture) stay out of the image.
    #[test]
    #[ignore = "changes the screen's scale: the CI's job bureau-echelle"]
    fn echelle_baissee_nothing_of_another_window_in_one_drawn_bigger_than_its_frame() {
        let echelle = runner_scale(|k| std::env::var(k).ok()).unwrap();
        let d = desk();
        let tool = scale_tool(&d);
        let (_w, win) = d.open("fenetre", "Factures", (80, 80, 640, 480), &[BLUE]);
        let (_k, kept) = d.open("protege", "Protegee", (1100, 80, 400, 300), &[BLUE]);
        let (_x, out) = d.open("exclu", "Exclue", (1100, 420, 400, 300), &[BLUE]);
        let windows = [&win, &kept, &out].map(|f| handle(f.window.id));
        let _back = lowered(&tool, &windows);
        let opened = 96 * echelle / 100;
        assert_eq!(windows.map(dpi), [opened; 3], "drawn bigger than their frames, the screen at 100 %");
        covered_keeps_none_of_it(&d, &win, "fenetre");
        others_popup_stays_out(&d, &win);
        protected_stays_out(&kept);
        protected_stays_out(&out);
    }

    /// R11: a window of another program that makes itself a popup of the chosen one, while that one
    /// waits (disabled), is never drawn on its capture: the same process only.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn bureau_another_programs_popup_is_never_drawn() {
        let d = desk();
        for mode in ["fenetre", "fenetre-etiree"] {
            let (_p, win) = d.open(mode, &format!("Compta {mode}"), (80, 80, 640, 480), &[BLUE]);
            others_popup_stays_out(&d, &win);
        }
    }

    fn others_popup_stays_out(d: &Desk, win: &Found) {
        let h = handle(win.window.id);
        let mut cmd = Command::new(&d.exe);
        let titre = format!("Atelier Intrus {} {}", d.nonce, win.window.id);
        cmd.args(["intrus", &titre, "240", "200", "320", "220", "ff00ff", &win.window.id.to_string()]);
        let _intrus = Program(cmd.spawn().unwrap());
        until("the other program's window", || any_titled(&titre).is_some());
        let popup = any_titled(&titre).unwrap();
        assert_eq!(owner(popup).map(id_of), Some(win.window.id), "owned by the chosen window");
        assert_ne!(pid_of(popup), win.window.pid);
        // Clicked once, it is the chosen window's last active popup.
        let r = frame(popup).unwrap();
        let middle = ((r.left + r.right) / 2, (r.top + r.bottom) / 2);
        until_is("the other program's window over the chosen one", || on_top(middle), id_of(popup));
        click(middle);
        // SAFETY: any value is accepted.
        until("its last active popup", || unsafe { GetLastActivePopup(h) } == popup);
        // SAFETY: a window of the test's program, enabled again below.
        let _ = unsafe { EnableWindow(h, false) };
        let img = grab(&win.window).unwrap();
        assert_eq!(count(&img, MAGENTA), 0, "{}: not a pixel of the other program's window", win.titre);
        assert!(count(&img, BLUE) > pixels(&img) * 8 / 10, "{}: its colour", win.titre);
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
        // Windows sizes and paints the box after it shows (once caught all black, 202x128): drawn, it is
        // at its place in its own image.
        let physical = Physical::enter();
        until("the message box drawn at its place in its own image", || print(dialog, &physical).is_some_and(|(a, ..)| black_margins(&a).iter().all(|&m| m <= 1)));
        let (alone, ..) = print(dialog, &physical).unwrap();
        eprintln!("message box {}x{}, black margins {:?}", alone.w, alone.h, black_margins(&alone));
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
    /// run as administrator would give): black, and nothing leaves; the user pastes a capture. Kept out
    /// on the screen only (WDA_MONITOR) or out of every capture (WDA_EXCLUDEFROMCAPTURE), at its own size
    /// or stretched by Windows.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn bureau_a_protected_window_is_black_and_never_leaves() {
        let d = desk();
        for mode in ["protege", "exclu", "protege-etiree", "exclu-etiree"] {
            let (_p, win) = d.open(mode, mode, (80, 80, 500, 400), &[BLUE]);
            protected_stays_out(&win);
        }
    }

    fn protected_stays_out(win: &Found) {
        let h = handle(win.window.id);
        until("kept out of captures", || {
            let mut affinity = 0u32;
            // SAFETY: a u32 for the call.
            unsafe { GetWindowDisplayAffinity(h, &mut affinity) }.is_ok() && affinity != 0
        });
        match grab(&win.window) {
            Err(Rate::Noire) => eprintln!("{}: PrintWindow refused", win.titre),
            Ok(img) => {
                eprintln!("{}: drawn {}x{}, black {}", win.titre, img.w, img.h, crate::shot::black_share(&img));
                assert_eq!(count(&img, BLUE), 0, "{}: never its pixels", win.titre);
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
        until_is("the other window in front", front, other.window.id);
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
            CreateWindowExW(
                WINDOW_EX_STYLE(0),
                w!("STATIC"),
                w!("Bibo, ton guide"),
                WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                300,
                300,
                300,
                200,
                None,
                None,
                None,
                None,
            )
        }
        .unwrap();
        assert!(visible(own));
        assert!(describe(id_of(own)).is_none());
        assert!(System.windows().iter().all(|f| f.window.pid != std::process::id()));
        // SAFETY: our window.
        let _ = unsafe { DestroyWindow(own) };
        guide::fin();
    }

    /// Start opened while Bibo waits for the click (to open the program from it): Start is no window of
    /// the user's, nor what it covers. The window back in front after it is the one that counts.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn bureau_windows_start_is_never_the_window() {
        struct Escape;
        impl Drop for Escape {
            fn drop(&mut self) {
                key(VK_ESCAPE);
            }
        }
        let d = desk();
        let (_p, win) = d.open("fenetre", "Avant Demarrer", (80, 80, 420, 320), &[BLUE]);
        click(center(&win));
        until_is("the window in front", front, win.window.id);
        let waiting = std::thread::spawn(guide::attendre_clic);
        std::thread::sleep(Duration::from_millis(600));
        let _closes = Escape;
        key(VK_LWIN);
        until("Start in front", || class(handle(front())) == "Windows.UI.Core.CoreWindow");
        let start = handle(front());
        eprintln!("Start: « {} », class {}, program {:?}", title(start), class(start), identity(start).map(|w| w.app));
        assert!(listed(start) && identity(start).is_some_and(|w| w.pid != std::process::id()) && !title(start).trim().is_empty(), "only its class keeps Start out");
        assert!(foreground().is_none() && describe(id_of(start)).is_none(), "Start is no window of the user's");
        std::thread::sleep(Duration::from_millis(600));
        key(VK_ESCAPE);
        let clicked = waiting.join().unwrap().unwrap();
        assert_eq!(clicked.id, win.window.id, "the window back in front after Start, never Start");
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
