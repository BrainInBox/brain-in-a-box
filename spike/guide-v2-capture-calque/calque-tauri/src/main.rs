//! W3 with Tauri: the overlay as `WebviewWindowBuilder` makes it (transparent, no frame, always on top,
//! not focusable, out of the taskbar), clicks let through by `set_ignore_cursor_events`, kept out of
//! captures by WDA_EXCLUDEFROMCAPTURE on its HWND. It prints « hwnd <id> … » once shown.
//!
//! `calque-tauri.exe <rings> <mode>`, the order of the calls (Server 2022 refuses the affinity to the
//! layered window tao makes):
//!   apres        set_ignore_cursor_events, then the affinity
//!   avant        the affinity first (no WS_EX_LAYERED yet), then set_ignore_cursor_events
//!   lwa          set_ignore_cursor_events, SetLayeredWindowAttributes(255, LWA_ALPHA), then the affinity
#![windows_subsystem = "windows"]

use std::io::{BufRead, Write};
use std::sync::mpsc;
use std::time::Duration;

use tauri::{PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindowBuilder};
use windows::Win32::Foundation::HWND;
use windows::Win32::Foundation::COLORREF;
use windows::Win32::UI::WindowsAndMessaging::{
    GetSystemMetrics, GetWindowDisplayAffinity, GetWindowLongPtrW, SetLayeredWindowAttributes, SetWindowDisplayAffinity, SetWindowPos, GWL_EXSTYLE, HWND_TOPMOST,
    LWA_ALPHA, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOOWNERZORDER, SWP_NOSIZE,
    WDA_EXCLUDEFROMCAPTURE, WDA_NONE, WINDOW_DISPLAY_AFFINITY,
};

fn main() {
    let rects = std::env::args().nth(1).unwrap_or_default();
    let mode = std::env::args().nth(2).unwrap_or_else(|| "apres".into());
    tauri::Builder::default()
        .setup(move |app| {
            // SAFETY: plain values; the app is per-monitor aware (Tauri's manifest): physical pixels.
            let (x, y, w, h) = unsafe {
                (GetSystemMetrics(SM_XVIRTUALSCREEN), GetSystemMetrics(SM_YVIRTUALSCREEN), GetSystemMetrics(SM_CXVIRTUALSCREEN), GetSystemMetrics(SM_CYVIRTUALSCREEN))
            };
            let win = WebviewWindowBuilder::new(app, "calque", WebviewUrl::App("index.html".into()))
                .initialization_script(format!("window.__ANNEAUX = {rects:?};"))
                .title("Bibo calque")
                .transparent(true)
                .decorations(false)
                .shadow(false)
                .always_on_top(true)
                .skip_taskbar(true)
                .focusable(false)
                .focused(false)
                .resizable(false)
                .visible(false)
                .build()?;
            win.set_position(PhysicalPosition::new(x, y))?;
            win.set_size(PhysicalSize::new(w as u32, h as u32))?;
            let hwnd = win.hwnd()?;
            // SAFETY: our window, on the main thread (setup).
            let exclure = || unsafe { SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE) }.map_err(|e| e.code());
            let mut lwa = None;
            let excluded = match mode.as_str() {
                "avant" => {
                    let r = exclure();
                    win.set_ignore_cursor_events(true)?;
                    r
                }
                "lwa" => {
                    win.set_ignore_cursor_events(true)?;
                    // SAFETY: as above; the whole window drawn as it is (alpha 255).
                    lwa = Some(unsafe { SetLayeredWindowAttributes(hwnd, COLORREF(0), 255, LWA_ALPHA) }.map_err(|e| e.code()));
                    exclure()
                }
                _ => {
                    win.set_ignore_cursor_events(true)?;
                    exclure()
                }
            };
            win.show()?;
            let mut lue = 0u32;
            // SAFETY: as above.
            let _ = unsafe { GetWindowDisplayAffinity(hwnd, &mut lue) };
            let ex = unsafe { GetWindowLongPtrW(hwnd, GWL_EXSTYLE) };
            let id = hwnd.0 as usize;
            println!("hwnd {id} mode {mode} excluded {} ({excluded:?}) affinity {lue:#x} exstyle {ex:#x} lwa {lwa:?}", excluded.is_ok());
            let _ = std::io::stdout().flush();
            // Back on top of the topmost band, as calque.rs does.
            std::thread::spawn(move || loop {
                std::thread::sleep(Duration::from_millis(250));
                // SAFETY: our window; never activated.
                let _ = unsafe { SetWindowPos(HWND(id as _), Some(HWND_TOPMOST), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER) };
            });
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                for line in std::io::stdin().lock().lines() {
                    let Ok(line) = line else { break };
                    let a: WINDOW_DISPLAY_AFFINITY = if line.trim() == "none" { WDA_NONE } else { WDA_EXCLUDEFROMCAPTURE };
                    let (tx, rx) = mpsc::channel();
                    let _ = handle.run_on_main_thread(move || {
                        // SAFETY: our window, on its thread.
                        let _ = tx.send(unsafe { SetWindowDisplayAffinity(HWND(id as _), a) }.is_ok());
                    });
                    let ok = rx.recv_timeout(Duration::from_secs(5)).unwrap_or(false);
                    println!("ok {ok}");
                    let _ = std::io::stdout().flush();
                }
                std::process::exit(0);
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("the overlay");
}
