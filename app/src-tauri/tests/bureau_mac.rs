//! The guide's capture on a macOS desktop of the CI's only (the desk job of phase 2b). This test is
//! built, put in an app bundle of Bibo's bundle id, signed ad hoc and opened by `open`: its own
//! responsible process, so without the screen recording permission the runner gives its tools. Then
//! tests/fixtures/guide/pilote_mac.py clicks the system's picker as a hand would (System Events),
//! which this program cannot: it asks for each gesture in its output and waits for it.
//!
//! As in the app: AppKit's run loop on the main thread, with « Bibo », an orange window always on
//! top over the window to pick (lowered while the picker is out, as `guide_choisir` does), and the
//! guide's capture (src/capture, the very files) on another thread, as a command runs. No image is
//! written: their pixels are counted in memory. Without `--bureau <folder>`, nothing runs.
//!
//! The files included here keep their unit tests, which only the library's own test build runs.
#![allow(dead_code, unused_imports)]

#[cfg(target_os = "macos")]
#[path = "../src/shot.rs"]
mod shot;

#[cfg(target_os = "macos")]
#[path = "../src/capture/mod.rs"]
mod capture;

fn main() {
    #[cfg(target_os = "macos")]
    desk::main();
}

#[cfg(target_os = "macos")]
mod desk {
    use std::cell::RefCell;
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    use std::time::{Duration, Instant};

    use dispatch2::DispatchQueue;
    use objc2::rc::Retained;
    use objc2::{MainThreadMarker, MainThreadOnly};
    use objc2_app_kit::{
        NSApplication, NSApplicationActivationPolicy, NSBackingStoreType, NSColor, NSFloatingWindowLevel, NSNormalWindowLevel, NSScreen, NSWindow,
        NSWindowStyleMask,
    };
    use objc2_foundation::{NSPoint, NSRect, NSSize, NSString};

    use crate::capture::{Pick, Rate, Screen, System, Window};
    use crate::shot::Rgba;

    /// The page the user's window shows (pilote_mac.py), then after a click on it; the window over it.
    const TEAL: [u8; 3] = [0x00, 0xaa, 0xc8];
    const GREEN: [u8; 3] = [0x2e, 0x7d, 0x32];
    const MAGENTA: [u8; 3] = [0xff, 0x00, 0xff];
    /// Bibo's window.
    const ORANGE: [u8; 3] = [0xff, 0x98, 0x00];

    #[link(name = "CoreGraphics", kind = "framework")]
    extern "C" {
        /// Whether this process may record the screen (TCC): it must stay false.
        fn CGPreflightScreenCaptureAccess() -> bool;
    }

    fn preflight() -> bool {
        // SAFETY: no argument, a plain answer.
        unsafe { CGPreflightScreenCaptureAccess() }
    }

    thread_local! {
        static BIBO: RefCell<Option<Retained<NSWindow>>> = const { RefCell::new(None) };
    }

    /// « Bibo »: orange, always on top, over the right part of where the user's window will be.
    fn bibo(mtm: MainThreadMarker) {
        let height = NSScreen::mainScreen(mtm).map_or(900.0, |s| s.frame().size.height);
        let frame = NSRect::new(NSPoint::new(860.0, height - 300.0 - 220.0), NSSize::new(300.0, 220.0));
        // SAFETY: a window of ours on the main thread, kept until the end.
        let w = unsafe {
            NSWindow::initWithContentRect_styleMask_backing_defer(NSWindow::alloc(mtm), frame, NSWindowStyleMask::Titled, NSBackingStoreType::Buffered, false)
        };
        // SAFETY: as above.
        unsafe { w.setReleasedWhenClosed(false) };
        w.setTitle(&NSString::from_str("Bibo (bureau)"));
        w.setBackgroundColor(Some(&NSColor::colorWithSRGBRed_green_blue_alpha(1.0, 152.0 / 255.0, 0.0, 1.0)));
        w.setLevel(NSFloatingWindowLevel);
        w.orderFrontRegardless();
        BIBO.with(|b| *b.borrow_mut() = Some(w));
    }

    fn bibo_level(on_top: bool) {
        let (tx, rx) = std::sync::mpsc::channel();
        DispatchQueue::main().exec_async(move || {
            BIBO.with(|b| {
                if let Some(w) = b.borrow().as_ref() {
                    w.setLevel(if on_top { NSFloatingWindowLevel } else { NSNormalWindowLevel });
                }
            });
            let _ = tx.send(());
        });
        let _ = rx.recv_timeout(Duration::from_secs(5));
    }

    pub fn main() {
        let args: Vec<String> = std::env::args().collect();
        let Some(dir) = args.windows(2).find(|a| a[0] == "--bureau").map(|a| PathBuf::from(&a[1])) else {
            println!("bureau_mac: for the macOS desk job only (--bureau <folder>)");
            return;
        };
        // A scenario that panics ends the program, which would otherwise wait on AppKit's loop.
        std::panic::set_hook(Box::new(|info| {
            println!("{}", serde_json::json!({"panique": info.to_string()}));
            std::process::exit(2);
        }));
        let mtm = MainThreadMarker::new().expect("the main thread");
        let app = NSApplication::sharedApplication(mtm);
        app.setActivationPolicy(NSApplicationActivationPolicy::Regular);
        bibo(mtm);
        std::thread::spawn(move || {
            let code = scenarios(&dir);
            std::process::exit(code);
        });
        app.run();
    }

    /// What the scenarios say, one JSON line each, and the gestures they ask of the driver.
    struct Report {
        dir: PathBuf,
        asked: AtomicUsize,
        failed: Vec<String>,
    }

    impl Report {
        fn line(&self, v: serde_json::Value) {
            println!("{v}");
        }

        /// A gesture of the driver's; with `wait`, its end (the driver writes `fait-<n>`).
        fn ask(&self, gesture: &str, wait: bool) {
            let n = self.asked.fetch_add(1, Ordering::SeqCst) + 1;
            self.line(serde_json::json!({"pilote": gesture, "n": n}));
            if wait {
                let done = self.dir.join(format!("fait-{n}"));
                let start = Instant::now();
                while !done.exists() && start.elapsed() < Duration::from_secs(60) {
                    std::thread::sleep(Duration::from_millis(200));
                }
                assert!(done.exists(), "the driver never did « {gesture} »");
            }
        }

        fn scenario(&mut self, name: &str, ok: bool, detail: serde_json::Value) {
            self.line(serde_json::json!({"scenario": name, "ok": ok, "detail": detail}));
            if !ok {
                self.failed.push(name.to_string());
            }
        }
    }

    fn count(img: &Rgba, c: [u8; 3]) -> usize {
        img.px.chunks_exact(4).filter(|p| p[3] > 200 && (0..3).all(|i| p[i].abs_diff(c[i]) <= 12)).count()
    }

    fn share(img: &Rgba, c: [u8; 3]) -> f64 {
        count(img, c) as f64 / (img.px.len() / 4).max(1) as f64
    }

    fn stats(img: &Rgba) -> serde_json::Value {
        serde_json::json!({
            "w": img.w, "h": img.h, "square": square(img),
            "teal": share(img, TEAL), "green": share(img, GREEN), "magenta": count(img, MAGENTA), "orange": count(img, ORANGE),
            "black": crate::shot::black_share(img),
        })
    }

    fn outcome(r: &Result<Rgba, Rate>) -> serde_json::Value {
        match r {
            Ok(img) => stats(img),
            Err(e) => serde_json::json!({"rate": format!("{e:?}")}),
        }
    }

    /// The picker, Bibo lowered meanwhile as `guide_choisir` lowers it.
    fn pick(cancel: &AtomicBool) -> Pick {
        bibo_level(false);
        let picked = System.pick(cancel);
        bibo_level(true);
        picked
    }

    /// The page's white square, its box: (left, top, right, bottom). Rows mostly white (a light title
    /// bar) are not the page's.
    fn square(img: &Rgba) -> Option<(u32, u32, u32, u32)> {
        let white = |x: u32, y: u32| {
            let p = &img.px[((y * img.w + x) * 4) as usize..][..4];
            p[..3].iter().all(|&v| v >= 245) && p[3] > 200
        };
        let mut b: Option<(u32, u32, u32, u32)> = None;
        for y in 0..img.h {
            let row: Vec<u32> = (0..img.w).filter(|&x| white(x, y)).collect();
            if row.is_empty() || row.len() as u32 * 2 > img.w {
                continue;
            }
            for x in row {
                b = Some(b.map_or((x, y, x + 1, y + 1), |(l, t, r, bt)| (l.min(x), t.min(y), r.max(x + 1), bt.max(y + 1))));
            }
        }
        b
    }

    /// The user's window, only its own pixels: its page's colour over most of it, its white square
    /// square, not a pixel of the window over it nor of Bibo's.
    fn its_own(img: &Rgba, colour: [u8; 3]) -> bool {
        let square = square(img).is_some_and(|(l, t, r, b)| (r - l).abs_diff(b - t) <= 2 && r - l >= 50);
        share(img, colour) > 0.7 && square && count(img, MAGENTA) == 0 && count(img, ORANGE) == 0 && crate::shot::black_share(img) < 0.1
    }

    fn grab_until(window: &Window, colour: [u8; 3]) -> Result<Rgba, Rate> {
        let start = Instant::now();
        loop {
            let r = System.grab(window);
            if r.as_ref().is_ok_and(|img| share(img, colour) > 0.7) || start.elapsed() > Duration::from_secs(15) {
                return r;
            }
            std::thread::sleep(Duration::from_millis(500));
        }
    }

    fn scenarios(dir: &Path) -> i32 {
        let mut r = Report { dir: dir.to_path_buf(), asked: AtomicUsize::new(0), failed: Vec::new() };
        let never = AtomicBool::new(false);
        r.line(serde_json::json!({"macos": std::process::Command::new("sw_vers").arg("-productVersion").output().map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string()).unwrap_or_default(), "preflight": preflight(), "available": System.available(), "picker": System.picker()}));
        r.scenario("sans_permission_au_depart", !preflight(), serde_json::json!({}));
        if !System.available() {
            r.scenario("selecteur_disponible", false, serde_json::json!({}));
            return 1;
        }

        // The user picks their window in the system's picker.
        r.ask("choisir", false);
        let picked = pick(&never);
        let Pick::Choisie(found) = picked.clone() else {
            r.scenario("le_selecteur_donne_la_fenetre", false, serde_json::json!({"pick": format!("{picked:?}")}));
            return 1;
        };
        r.scenario(
            "le_selecteur_donne_la_fenetre",
            !found.terminal && System.find(found.window.id).as_ref() == Some(&found),
            serde_json::json!({"nom": found.nom, "titre": found.titre, "app": found.window.app, "pid": found.window.pid}),
        );

        // One image, under the other window and Bibo's, without the permission.
        r.ask("couvrir", true);
        let first = System.grab(&found.window);
        r.scenario("une_image_sans_permission", first.as_ref().is_ok_and(|img| its_own(img, TEAL)) && !preflight(), outcome(&first));

        // « C'est fait »: the same filter, a new stream, 4 s later.
        std::thread::sleep(Duration::from_secs(4));
        let again = System.grab(&found.window);
        r.scenario("cest_fait_le_meme_filtre", again.as_ref().is_ok_and(|img| its_own(img, TEAL)), outcome(&again));

        // The page changes: the image is the window now, not an image kept.
        r.ask("clic_page", true);
        let changed = grab_until(&found.window, GREEN);
        r.scenario("la_fenetre_telle_quelle_est", changed.as_ref().is_ok_and(|img| its_own(img, GREEN)), outcome(&changed));

        // Minimized: the stream says it has nothing to show, no image.
        r.ask("reduire", true);
        std::thread::sleep(Duration::from_secs(2));
        let minimized = System.grab(&found.window);
        r.scenario("reduite", minimized.as_ref().err() == Some(&Rate::Reduite), outcome(&minimized));
        r.ask("rouvrir", true);
        let back = grab_until(&found.window, GREEN);
        r.scenario("rouverte", back.as_ref().is_ok_and(|img| its_own(img, GREEN)), outcome(&back));

        // « Arrêter » while the picker is out: it goes away, the window picked before stays.
        let stop = AtomicBool::new(false);
        let stopped = std::thread::scope(|s| {
            s.spawn(|| {
                std::thread::sleep(Duration::from_secs(3));
                stop.store(true, Ordering::SeqCst);
            });
            let start = Instant::now();
            let p = pick(&stop);
            (p, start.elapsed())
        });
        r.scenario(
            "arreter_pendant_le_choix",
            stopped.0 == Pick::Arrete && stopped.1 < Duration::from_secs(5),
            serde_json::json!({"pick": format!("{:?}", stopped.0), "secs": stopped.1.as_secs_f64()}),
        );
        // The picker off, its window goes with it: a stream of it no longer shows that window.
        let kept = System.grab(&found.window);
        r.scenario("apres_arreter_la_fenetre_oubliee", kept.as_ref().err() == Some(&Rate::Introuvable), outcome(&kept));

        // The user closes the picker without a window.
        r.ask("fermer_le_selecteur", false);
        let closed = pick(&never);
        r.scenario("selecteur_ferme", closed == Pick::Fermee, serde_json::json!({"pick": format!("{closed:?}")}));

        // Picked again, then the window closed: no image, never another one's.
        r.ask("choisir", false);
        let Pick::Choisie(found) = pick(&never) else {
            r.scenario("choisie_de_nouveau", false, serde_json::json!({}));
            return 1;
        };
        let before = System.grab(&found.window);
        r.scenario("choisie_de_nouveau", before.is_ok(), outcome(&before));
        r.ask("fermer_la_fenetre", true);
        std::thread::sleep(Duration::from_secs(2));
        let gone = System.grab(&found.window);
        r.scenario("fenetre_fermee", gone.as_ref().err() == Some(&Rate::Introuvable), outcome(&gone));

        // The guide closed: the window forgotten.
        System.forget();
        r.scenario(
            "oubliee_a_la_fin",
            System.find(found.window.id).is_none() && System.grab(&found.window).err() == Some(Rate::Introuvable),
            serde_json::json!({}),
        );
        std::thread::sleep(Duration::from_secs(2));
        r.scenario("sans_permission_a_la_fin", !preflight(), serde_json::json!({}));
        r.line(serde_json::json!({"fin": r.failed.is_empty(), "rates": r.failed}));
        i32::from(!r.failed.is_empty())
    }
}
