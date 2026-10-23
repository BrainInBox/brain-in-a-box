//! W3: Bibo's overlay. One window over all the screens, transparent but for its rings, always on top,
//! never active, that clicks go through (WS_EX_LAYERED | WS_EX_TRANSPARENT), kept out of every capture
//! (WDA_EXCLUDEFROMCAPTURE). Win32 here; Tauri sets the same styles through tao (set_ignore_cursor_events,
//! focusable(false), always_on_top), and the affinity on its HWND.
//!
//! Two ways to make it transparent: per-pixel alpha (UpdateLayeredWindow), or a key colour painted as
//! nothing (SetLayeredWindowAttributes, LWA_COLORKEY). Server 2022 refuses the affinity to the first.

use std::cell::RefCell;
use std::sync::mpsc;
use std::sync::Once;
use std::thread::JoinHandle;

use windows::core::w;
use windows::Win32::Foundation::{COLORREF, HWND, LPARAM, LRESULT, POINT, RECT, SIZE, WPARAM};
use windows::Win32::Graphics::Gdi::{
    BeginPaint, CreateSolidBrush, DeleteObject, EndPaint, FillRect, GetDC, InvalidateRect, ReleaseDC, AC_SRC_ALPHA, AC_SRC_OVER, BLENDFUNCTION, PAINTSTRUCT,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::Threading::GetCurrentThreadId;
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DefWindowProcW, DestroyWindow, DispatchMessageW, GetMessageW, GetWindowDisplayAffinity, PostThreadMessageW, RegisterClassW, SendMessageW,
    SetLayeredWindowAttributes, SetTimer, SetWindowDisplayAffinity, SetWindowPos, ShowWindow, TranslateMessage, UpdateLayeredWindow, HWND_TOPMOST, LWA_COLORKEY,
    MSG, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOOWNERZORDER, SWP_NOSIZE, SW_SHOWNOACTIVATE, ULW_ALPHA, WDA_EXCLUDEFROMCAPTURE, WINDOW_DISPLAY_AFFINITY, WM_APP,
    WM_PAINT, WM_QUIT, WM_TIMER, WNDCLASSW, WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_POPUP,
};

use crate::prims::{id_of, handle, screen_rect, Canvas, Physical};

/// The ring's colour (BGR order is handled below): a green nothing else on the VM's screen has.
pub const ANNEAU: [u8; 3] = [0x00, 0xe6, 0x76];
pub const EPAISSEUR: i32 = 6;
pub const MARGE: i32 = 6;
/// How often the overlay puts itself back on top of the topmost band (the taskbar comes up when used).
pub const REHAUSSE_MS: u32 = 250;

/// The key colour: painted as nothing, clicked through (COLORREF, 0x00BBGGRR). Never the ring's.
pub const CLE: u32 = 0x0000_0000;

const AFFINITE: u32 = WM_APP + 1;

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Mode {
    /// Per-pixel alpha, UpdateLayeredWindow.
    Ulw,
    /// LWA_COLORKEY: the window paints the key colour and its rings (WM_PAINT).
    Cle,
}

thread_local! {
    /// The overlay's rings, on its own thread, for WM_PAINT (screen and window coordinates are the same).
    static PEINT: RefCell<(RECT, Vec<RECT>)> = RefCell::new((RECT::default(), Vec::new()));
}

pub struct Calque {
    pub id: u64,
    tid: u32,
    thread: Option<JoinHandle<()>>,
    /// Where the overlay lies (all the screens), physical pixels.
    pub ecran: RECT,
}

/// The ring around `r`: its outer and inner boxes.
pub fn anneau(r: RECT) -> (RECT, RECT) {
    let o = MARGE + EPAISSEUR;
    (
        RECT { left: r.left - o, top: r.top - o, right: r.right + o, bottom: r.bottom + o },
        RECT { left: r.left - MARGE, top: r.top - MARGE, right: r.right + MARGE, bottom: r.bottom + MARGE },
    )
}

/// WDA_EXCLUDEFROMCAPTURE on the overlay.
fn exclure(h: HWND) -> windows::core::Result<()> {
    // SAFETY: our window.
    unsafe { SetWindowDisplayAffinity(h, WDA_EXCLUDEFROMCAPTURE) }
}

unsafe extern "system" fn proc_(h: HWND, msg: u32, wp: WPARAM, lp: LPARAM) -> LRESULT {
    match msg {
        AFFINITE => {
            // SAFETY: our window, on its own thread.
            let ok = unsafe { SetWindowDisplayAffinity(h, WINDOW_DISPLAY_AFFINITY(wp.0 as u32)) }.is_ok();
            LRESULT(ok as isize)
        }
        WM_PAINT => {
            let mut ps = PAINTSTRUCT::default();
            // SAFETY: our window, painted on its thread; the brushes are ours, deleted below.
            unsafe {
                let dc = BeginPaint(h, &mut ps);
                let fond = CreateSolidBrush(COLORREF(CLE));
                let vert = CreateSolidBrush(COLORREF(u32::from(ANNEAU[0]) | (u32::from(ANNEAU[1]) << 8) | (u32::from(ANNEAU[2]) << 16)));
                PEINT.with(|p| {
                    let (ecran, anneaux) = &*p.borrow();
                    let local = |r: RECT| RECT { left: r.left - ecran.left, top: r.top - ecran.top, right: r.right - ecran.left, bottom: r.bottom - ecran.top };
                    FillRect(dc, &ps.rcPaint, fond);
                    for &r in anneaux {
                        let (o, i) = anneau(r);
                        FillRect(dc, &local(o), vert);
                        FillRect(dc, &local(i), fond);
                    }
                });
                let _ = DeleteObject(fond.into());
                let _ = DeleteObject(vert.into());
                let _ = EndPaint(h, &ps);
            }
            LRESULT(0)
        }
        WM_TIMER => {
            // SAFETY: as above; never activated.
            let _ = unsafe { SetWindowPos(h, Some(HWND_TOPMOST), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER) };
            LRESULT(0)
        }
        // SAFETY: the default for everything else.
        _ => unsafe { DefWindowProcW(h, msg, wp, lp) },
    }
}

fn dessiner(h: HWND, ecran: RECT, anneaux: &[RECT]) -> bool {
    let (w, ht) = (ecran.right - ecran.left, ecran.bottom - ecran.top);
    let Some(mut canvas) = Canvas::new(w, ht) else { return false };
    let px = canvas.pixels_mut();
    px.fill(0);
    for &r in anneaux {
        let (o, i) = anneau(r);
        for y in o.top.max(ecran.top)..o.bottom.min(ecran.bottom) {
            for x in o.left.max(ecran.left)..o.right.min(ecran.right) {
                if x >= i.left && x < i.right && y >= i.top && y < i.bottom {
                    continue;
                }
                let at = (((y - ecran.top) * w + (x - ecran.left)) * 4) as usize;
                // Premultiplied BGRA, opaque.
                px[at..at + 4].copy_from_slice(&[ANNEAU[2], ANNEAU[1], ANNEAU[0], 255]);
            }
        }
    }
    let blend = BLENDFUNCTION { BlendOp: AC_SRC_OVER as u8, BlendFlags: 0, SourceConstantAlpha: 255, AlphaFormat: AC_SRC_ALPHA as u8 };
    let (pos, size, src) = (POINT { x: ecran.left, y: ecran.top }, SIZE { cx: w, cy: ht }, POINT { x: 0, y: 0 });
    // SAFETY: the screen's DC, released below; our window and canvas.
    unsafe {
        let screen = GetDC(None);
        let ok = UpdateLayeredWindow(h, Some(screen), Some(&pos), Some(&size), Some(canvas.dc), Some(&src), COLORREF(0), Some(&blend), ULW_ALPHA);
        ReleaseDC(None, screen);
        ok.is_ok()
    }
}

impl Calque {
    /// The overlay, with a ring around each rectangle (screen, physical pixels).
    pub fn ouvrir(anneaux: Vec<RECT>, mode: Mode) -> Calque {
        static CLASS: Once = Once::new();
        let (tx, rx) = mpsc::channel();
        let thread = std::thread::spawn(move || {
            // Before the window: it takes the thread's awareness, physical pixels.
            let _physical = Physical::enter();
            // SAFETY: this module's own instance and class, registered once.
            let instance = unsafe { GetModuleHandleW(None) }.unwrap();
            CLASS.call_once(|| {
                let wc = WNDCLASSW { lpfnWndProc: Some(proc_), hInstance: instance.into(), lpszClassName: w!("BiboCalque"), ..Default::default() };
                // SAFETY: a class of static strings and a static procedure.
                unsafe { RegisterClassW(&wc) };
            });
            let ecran = screen_rect();
            let ex = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE;
            // SAFETY: our class; the window is destroyed on this thread below.
            let h = unsafe {
                CreateWindowExW(ex, w!("BiboCalque"), w!("Bibo calque"), WS_POPUP, ecran.left, ecran.top, ecran.right - ecran.left, ecran.bottom - ecran.top, None, None, Some(instance.into()), None)
            }
            .unwrap();
            let drawn = match mode {
                Mode::Ulw => dessiner(h, ecran, &anneaux),
                Mode::Cle => {
                    PEINT.with(|p| *p.borrow_mut() = (ecran, anneaux.clone()));
                    // SAFETY: our layered window; WM_PAINT draws it.
                    let ok = unsafe { SetLayeredWindowAttributes(h, COLORREF(CLE), 0, LWA_COLORKEY) }.is_ok();
                    let _ = unsafe { InvalidateRect(Some(h), None, true) };
                    ok
                }
            };
            let avant = exclure(h);
            // SAFETY: our window.
            unsafe {
                let _ = ShowWindow(h, SW_SHOWNOACTIVATE);
                SetTimer(Some(h), 1, REHAUSSE_MS, None);
            }
            // Asked again once shown, when Windows refused it before.
            let apres = avant.is_err().then(|| exclure(h));
            let mut lue = 0u32;
            // SAFETY: a u32 for the call.
            let _ = unsafe { GetWindowDisplayAffinity(h, &mut lue) };
            eprintln!("calque {mode:?}: drawn {drawn}, excluded before showing {avant:?}, after {apres:?}, affinity read {lue:#x}");
            tx.send((id_of(h), unsafe { GetCurrentThreadId() }, ecran)).unwrap();
            let mut msg = MSG::default();
            // SAFETY: this thread's queue, until WM_QUIT.
            while unsafe { GetMessageW(&mut msg, None, 0, 0) }.as_bool() {
                unsafe {
                    let _ = TranslateMessage(&msg);
                    DispatchMessageW(&msg);
                }
            }
            let _ = unsafe { DestroyWindow(h) };
        });
        let (id, tid, ecran) = rx.recv().unwrap();
        Calque { id, tid, thread: Some(thread), ecran }
    }

    pub fn hwnd(&self) -> HWND {
        handle(self.id)
    }

    /// The witness only: the overlay shown to captures (WDA_NONE), then kept out again.
    pub fn affinite(&self, a: WINDOW_DISPLAY_AFFINITY) -> bool {
        // SAFETY: our window; its thread answers.
        unsafe { SendMessageW(self.hwnd(), AFFINITE, Some(WPARAM(a.0 as usize)), Some(LPARAM(0))) }.0 != 0
    }
}

impl Drop for Calque {
    fn drop(&mut self) {
        // SAFETY: the overlay's thread, told to end.
        let _ = unsafe { PostThreadMessageW(self.tid, WM_QUIT, WPARAM(0), LPARAM(0)) };
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}
