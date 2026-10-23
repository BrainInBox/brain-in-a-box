//! What app/src-tauri/src/capture/win.rs already does (a window drawn by PrintWindow into Bibo's memory,
//! cut to its DWM frame), copied here so the spike stays outside the product.

use std::ffi::c_void;
use std::mem::size_of;
use std::ptr::null_mut;

use windows::core::{BOOL, PWSTR};
use windows::Win32::Foundation::{CloseHandle, HWND, LPARAM, RECT};
use windows::Win32::Graphics::Dwm::{DwmGetWindowAttribute, DWMWA_CLOAKED, DWMWA_EXTENDED_FRAME_BOUNDS};
use windows::Win32::Graphics::Gdi::{
    CreateCompatibleDC, CreateDIBSection, DeleteDC, DeleteObject, GdiFlush, SelectObject, BITMAPINFO, BITMAPINFOHEADER, BI_RGB, DIB_RGB_COLORS, HBITMAP, HDC,
    HGDIOBJ,
};
use windows::Win32::Storage::Xps::{PrintWindow, PRINT_WINDOW_FLAGS};
use windows::Win32::System::Threading::{OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION};
use windows::Win32::UI::HiDpi::{SetThreadDpiAwarenessContext, DPI_AWARENESS_CONTEXT, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2};
use windows::Win32::UI::WindowsAndMessaging::{
    EnumWindows, GetClassNameW, GetSystemMetrics, GetWindow, GetWindowRect, GetWindowTextW, GetWindowThreadProcessId, IsIconic, IsWindowVisible, GW_OWNER,
    PW_RENDERFULLCONTENT, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN,
};

const MAX_SIDE: i32 = 8000;

#[derive(Clone, Debug)]
pub struct Rgba {
    pub w: u32,
    pub h: u32,
    pub px: Vec<u8>,
}

/// (left, top, right, bottom), right and bottom out.
pub type Zone = (i32, i32, i32, i32);

impl Rgba {
    pub fn black(w: u32, h: u32) -> Self {
        let mut px = vec![0u8; w as usize * h as usize * 4];
        px.chunks_exact_mut(4).for_each(|p| p[3] = 255);
        Rgba { w, h, px }
    }

    pub fn pixels(&self) -> usize {
        self.px.len() / 4
    }

    fn at(&self, i: usize) -> &[u8] {
        &self.px[i * 4..i * 4 + 4]
    }

    fn inside(&self, z: Zone) -> impl Iterator<Item = usize> + '_ {
        let (x0, y0) = (z.0.clamp(0, self.w as i32) as usize, z.1.clamp(0, self.h as i32) as usize);
        let (x1, y1) = (z.2.clamp(0, self.w as i32) as usize, z.3.clamp(0, self.h as i32) as usize);
        (y0..y1).flat_map(move |y| (x0..x1).map(move |x| y * self.w as usize + x))
    }

    /// Pixels of that colour, give or take 3 on each channel.
    pub fn count(&self, c: [u8; 3]) -> usize {
        self.px.chunks_exact(4).filter(|p| near(p, c)).count()
    }

    pub fn count_in(&self, z: Zone, c: [u8; 3]) -> usize {
        self.inside(z).filter(|&i| near(self.at(i), c)).count()
    }

    pub fn black_in(&self, z: Zone) -> usize {
        self.inside(z).filter(|&i| self.at(i)[..3].iter().all(|&v| v <= 6)).count()
    }

    pub fn area(&self, z: Zone) -> usize {
        self.inside(z).count()
    }

    pub fn black_share(&self) -> f32 {
        let total = self.pixels().max(1);
        let black = self.px.chunks_exact(4).filter(|p| p[..3].iter().all(|&v| v <= 6)).count();
        black as f32 / total as f32
    }

    pub fn fill(&mut self, z: Zone, c: [u8; 3]) {
        let all: Vec<usize> = self.inside(z).collect();
        for i in all {
            self.px[i * 4..i * 4 + 3].copy_from_slice(&c);
        }
    }

    /// The smallest box around the pixels of that colour.
    pub fn bounds(&self, c: [u8; 3]) -> Option<Zone> {
        let mut b: Option<Zone> = None;
        for (i, p) in self.px.chunks_exact(4).enumerate() {
            if near(p, c) {
                let (x, y) = ((i as u32 % self.w) as i32, (i as u32 / self.w) as i32);
                b = Some(b.map_or((x, y, x + 1, y + 1), |(l, t, r, bt)| (l.min(x), t.min(y), r.max(x + 1), bt.max(y + 1))));
            }
        }
        b
    }
}

fn near(p: &[u8], c: [u8; 3]) -> bool {
    (0..3).all(|i| p[i].abs_diff(c[i]) <= 3)
}

pub fn handle(id: u64) -> HWND {
    HWND(id as usize as *mut c_void)
}

pub fn id_of(h: HWND) -> u64 {
    h.0 as usize as u64
}

pub fn visible(h: HWND) -> bool {
    // SAFETY: any value is accepted.
    unsafe { IsWindowVisible(h) }.as_bool()
}

pub fn minimized(h: HWND) -> bool {
    // SAFETY: as above.
    unsafe { IsIconic(h) }.as_bool()
}

pub fn owner(h: HWND) -> Option<HWND> {
    // SAFETY: as above; no owner is an error.
    unsafe { GetWindow(h, GW_OWNER) }.ok().filter(|o| !o.is_invalid())
}

pub fn pid_of(h: HWND) -> u32 {
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

pub fn title(h: HWND) -> String {
    // SAFETY: the buffer's length goes with it.
    wide_text(|b| unsafe { GetWindowTextW(h, b) })
}

pub fn class(h: HWND) -> String {
    // SAFETY: as above.
    wide_text(|b| unsafe { GetClassNameW(h, b) })
}

pub fn image_path(pid: u32) -> Option<String> {
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

pub fn cloaked(h: HWND) -> bool {
    let mut value = 0u32;
    // SAFETY: a u32 for DWMWA_CLOAKED, its size given.
    let read = unsafe { DwmGetWindowAttribute(h, DWMWA_CLOAKED, (&mut value as *mut u32).cast(), size_of::<u32>() as u32) };
    read.is_ok() && value != 0
}

/// Every top-level window, the one on top first.
pub fn top_windows() -> Vec<HWND> {
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

/// While it lives, this thread counts in physical pixels.
pub struct Physical(DPI_AWARENESS_CONTEXT);

impl Physical {
    pub fn enter() -> Self {
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

/// All the screens, in physical pixels when the thread is.
pub fn screen_rect() -> RECT {
    // SAFETY: plain values.
    unsafe {
        let (x, y) = (GetSystemMetrics(SM_XVIRTUALSCREEN), GetSystemMetrics(SM_YVIRTUALSCREEN));
        RECT { left: x, top: y, right: x + GetSystemMetrics(SM_CXVIRTUALSCREEN), bottom: y + GetSystemMetrics(SM_CYVIRTUALSCREEN) }
    }
}

/// A 32-bit top-down bitmap of ours.
pub struct Canvas {
    pub dc: HDC,
    bitmap: HBITMAP,
    before: HGDIOBJ,
    bits: *mut u8,
    len: usize,
}

impl Canvas {
    pub fn new(w: i32, h: i32) -> Option<Self> {
        let info = BITMAPINFO {
            bmiHeader: BITMAPINFOHEADER {
                biSize: size_of::<BITMAPINFOHEADER>() as u32,
                biWidth: w,
                biHeight: -h,
                biPlanes: 1,
                biBitCount: 32,
                biCompression: BI_RGB.0,
                ..Default::default()
            },
            ..Default::default()
        };
        // SAFETY: a memory DC of our own, deleted on drop.
        let dc = unsafe { CreateCompatibleDC(None) };
        if dc.is_invalid() {
            return None;
        }
        let mut bits = null_mut();
        // SAFETY: `info` describes a 32-bit top-down DIB.
        let bitmap = match unsafe { CreateDIBSection(Some(dc), &info, DIB_RGB_COLORS, &mut bits, None, 0) } {
            Ok(b) if !bits.is_null() => b,
            _ => {
                // SAFETY: our DC, deleted once.
                let _ = unsafe { DeleteDC(dc) };
                return None;
            }
        };
        // SAFETY: our bitmap into our DC.
        let before = unsafe { SelectObject(dc, bitmap.into()) };
        Some(Canvas { dc, bitmap, before, bits: bits as *mut u8, len: w as usize * h as usize * 4 })
    }

    pub fn pixels(&self) -> &[u8] {
        // SAFETY: GDI's pending drawing flushed; the DIB lives as long as `self`.
        unsafe {
            let _ = GdiFlush();
            std::slice::from_raw_parts(self.bits, self.len)
        }
    }

    pub fn pixels_mut(&mut self) -> &mut [u8] {
        // SAFETY: as above, and `self` borrowed mutably.
        unsafe {
            let _ = GdiFlush();
            std::slice::from_raw_parts_mut(self.bits, self.len)
        }
    }
}

impl Drop for Canvas {
    fn drop(&mut self) {
        // SAFETY: our objects, released once.
        unsafe {
            SelectObject(self.dc, self.before);
            let _ = DeleteObject(self.bitmap.into());
            let _ = DeleteDC(self.dc);
        }
    }
}

pub fn frame(h: HWND) -> Option<RECT> {
    let mut r = RECT::default();
    // SAFETY: a RECT for DWMWA_EXTENDED_FRAME_BOUNDS, its size given.
    unsafe { DwmGetWindowAttribute(h, DWMWA_EXTENDED_FRAME_BOUNDS, (&mut r as *mut RECT).cast(), size_of::<RECT>() as u32) }.ok()?;
    (r.right > r.left && r.bottom > r.top).then_some(r)
}

pub fn window_rect(h: HWND) -> Option<RECT> {
    let mut r = RECT::default();
    // SAFETY: a RECT for the call.
    unsafe { GetWindowRect(h, &mut r) }.ok()?;
    (r.right > r.left && r.bottom > r.top).then_some(r)
}

/// BGRA rows to RGBA, opaque, cut to `keep`.
fn from_bgra(raw: &[u8], w: u32, h: u32, keep: Zone) -> Rgba {
    let clamp = |v: i32, max: u32| v.clamp(0, max as i32) as u32;
    let (mut x0, mut y0, mut x1, mut y1) = (clamp(keep.0, w), clamp(keep.1, h), clamp(keep.2, w), clamp(keep.3, h));
    if x0 >= x1 || y0 >= y1 {
        (x0, y0, x1, y1) = (0, 0, w, h);
    }
    let mut px = Vec::with_capacity((x1 - x0) as usize * (y1 - y0) as usize * 4);
    for y in y0..y1 {
        for p in raw[(y * w + x0) as usize * 4..(y * w + x1) as usize * 4].chunks_exact(4) {
            px.extend_from_slice(&[p[2], p[1], p[0], 255]);
        }
    }
    Rgba { w: x1 - x0, h: y1 - y0, px }
}

/// The window drawn by itself into a bitmap of ours, cut to its visible frame, with that frame on the
/// screen; None when it would not draw. Nothing is read from the screen.
pub fn print(h: HWND) -> Option<(Rgba, RECT)> {
    let outer = window_rect(h)?;
    let (w, ht) = (outer.right - outer.left, outer.bottom - outer.top);
    if w > MAX_SIDE || ht > MAX_SIDE {
        return None;
    }
    let shown = frame(h).unwrap_or(outer);
    let canvas = Canvas::new(w, ht)?;
    // SAFETY: the window draws itself into our DC.
    if !unsafe { PrintWindow(h, canvas.dc, PRINT_WINDOW_FLAGS(PW_RENDERFULLCONTENT)) }.as_bool() {
        return None;
    }
    let keep = (shown.left - outer.left, shown.top - outer.top, shown.right - outer.left, shown.bottom - outer.top);
    Some((from_bgra(canvas.pixels(), w as u32, ht as u32, keep), shown))
}

/// `pic` laid on `base` with its top-left corner at (dx, dy), cut to `base`.
pub fn lay(base: &mut Rgba, pic: &Rgba, dx: i64, dy: i64) {
    let span = |d: i64, size: u32, room: u32| (d.max(0), (d + i64::from(size)).min(i64::from(room)));
    let ((x0, x1), (y0, y1)) = (span(dx, pic.w, base.w), span(dy, pic.h, base.h));
    if x0 >= x1 {
        return;
    }
    for y in y0..y1 {
        let to = ((y * i64::from(base.w) + x0) * 4) as usize;
        let from = (((y - dy) * i64::from(pic.w) + (x0 - dx)) * 4) as usize;
        let len = ((x1 - x0) * 4) as usize;
        base.px[to..to + len].copy_from_slice(&pic.px[from..from + len]);
    }
}

/// The screen as Teams or a screenshot tool sees it (BitBlt of the desktop). The proofs' witness only:
/// what the composite must never contain, and the overlay's exclusion from such captures.
pub fn screen_copy(r: RECT) -> Option<Rgba> {
    use windows::Win32::Graphics::Gdi::{BitBlt, GetDC, ReleaseDC, CAPTUREBLT, SRCCOPY};
    let (w, h) = (r.right - r.left, r.bottom - r.top);
    let canvas = Canvas::new(w, h)?;
    // SAFETY: the screen's DC, released below; our canvas.
    unsafe {
        let screen = GetDC(None);
        let ok = BitBlt(canvas.dc, 0, 0, w, h, Some(screen), r.left, r.top, SRCCOPY | CAPTUREBLT);
        ReleaseDC(None, screen);
        ok.ok()?;
    }
    Some(from_bgra(canvas.pixels(), w as u32, h as u32, (0, 0, w, h)))
}
