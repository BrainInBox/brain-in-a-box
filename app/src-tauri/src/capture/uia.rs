//! Throwaway (spike/guide-v2-uia, proof W2 of the guide v2): the commands of the front app, read
//! from UI Automation, numbered with their boxes. Commands only, never data (C2): buttons, menu items,
//! tabs, links, check boxes, radio buttons, lists and fields by their label; never a value, never
//! anything inside a grid, a table, a list or a tree; in a browser, only the page (never the tabs).
//! Each name cut to 40 characters; what lies in a hidden zone left out; password fields given apart,
//! for the image to be blacked there.
//!
//! One cross-process call per window: the window's subtree, filtered on the types that matter, comes
//! back cached (ElementFromHandleBuildCache), then is walked here.

#![allow(dead_code, non_upper_case_globals)]

use std::cell::RefCell;

use windows::core::Interface;
use windows::Win32::Foundation::{HWND, LPARAM, RECT};
use windows::Win32::System::Com::{CoCreateInstance, CoInitializeEx, CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED};
use windows::Win32::System::Variant::VARIANT;
use windows::Win32::UI::Accessibility::*;
use windows::Win32::Graphics::Gdi::{MonitorFromWindow, MONITOR_DEFAULTTONEAREST};
use windows::Win32::UI::HiDpi::{GetDpiForMonitor, GetDpiForWindow, MDT_EFFECTIVE_DPI};
use windows::Win32::UI::WindowsAndMessaging::EnumWindows;

use super::{cloaked, frame, minimized, pid_of, visible, Physical};
use crate::shot::Rgba;

pub const NOM_MAX: usize = 40;

/// The programs whose window shows another computer's screen: no tree of what they show (C10).
const DISTANTS: [&str; 6] = ["mstsc.exe", "msrdc.exe", "wfica32.exe", "cdviewer.exe", "vmconnect.exe", "msrdcw.exe"];

#[derive(Clone, Debug)]
pub struct Element {
    pub kind: &'static str,
    pub name: String,
    /// On the screen, in physical pixels.
    pub rect: RECT,
    /// One of the window's own buttons (minimize, maximize, close), not the program's.
    pub titlebar: bool,
}

#[derive(Debug, Default)]
pub struct Liste {
    pub elements: Vec<Element>,
    pub passwords: Vec<RECT>,
    /// Nodes that came back from the other process (after the filter).
    pub nodes: usize,
    pub windows: usize,
    /// Windows or levels UIA did not give (a timeout, a window gone).
    pub errors: Vec<String>,
}

fn kind(t: UIA_CONTROLTYPE_ID) -> Option<&'static str> {
    Some(match t {
        UIA_ButtonControlTypeId | UIA_SplitButtonControlTypeId => "bouton",
        UIA_MenuItemControlTypeId => "menu",
        UIA_TabItemControlTypeId => "onglet",
        UIA_HyperlinkControlTypeId => "lien",
        UIA_CheckBoxControlTypeId => "case",
        UIA_RadioButtonControlTypeId => "option",
        UIA_ComboBoxControlTypeId => "liste",
        UIA_EditControlTypeId => "champ",
        _ => return None,
    })
}

/// Where rows of data live: nothing under them is a command (C2).
const DATA: [UIA_CONTROLTYPE_ID; 7] = [
    UIA_DataGridControlTypeId,
    UIA_TableControlTypeId,
    UIA_ListControlTypeId,
    UIA_TreeControlTypeId,
    UIA_DataItemControlTypeId,
    UIA_ListItemControlTypeId,
    UIA_TreeItemControlTypeId,
];

const KEPT: [UIA_CONTROLTYPE_ID; 9] = [
    UIA_ButtonControlTypeId,
    UIA_SplitButtonControlTypeId,
    UIA_MenuItemControlTypeId,
    UIA_TabItemControlTypeId,
    UIA_HyperlinkControlTypeId,
    UIA_CheckBoxControlTypeId,
    UIA_RadioButtonControlTypeId,
    UIA_ComboBoxControlTypeId,
    UIA_EditControlTypeId,
];

struct Uia {
    auto: IUIAutomation,
    /// The whole subtree in one call (filtered on the types that matter).
    cache: IUIAutomationCacheRequest,
    /// One level of the same filtered view, its elements live to go down: what lies under a command
    /// or a table is never walked, not even by the other process.
    kids: IUIAutomationCacheRequest,
}

thread_local! {
    static UIA: RefCell<Option<Uia>> = const { RefCell::new(None) };
}

fn uia() -> windows::core::Result<Uia> {
    // SAFETY: COM for this thread; already there in another mode is fine for these calls.
    let _ = unsafe { CoInitializeEx(None, COINIT_MULTITHREADED) };
    // SAFETY: the system's UIA client, in process.
    let auto: IUIAutomation = unsafe { CoCreateInstance(&CUIAutomation8, None, CLSCTX_INPROC_SERVER)? };
    if let Ok(a2) = auto.cast::<IUIAutomation2>() {
        // SAFETY: plain values. A program that hangs does not hang Bibo for long.
        unsafe {
            a2.SetConnectionTimeout(2000)?;
            a2.SetTransactionTimeout(2000)?;
        }
    }
    let mut filter: Option<IUIAutomationCondition> = None;
    for t in KEPT.iter().chain(DATA.iter()).chain([UIA_DocumentControlTypeId, UIA_TitleBarControlTypeId].iter()) {
        // SAFETY: a condition on a property UIA defines.
        let c = unsafe { auto.CreatePropertyCondition(UIA_ControlTypePropertyId, &VARIANT::from(t.0))? };
        filter = Some(match filter {
            None => c,
            Some(before) => unsafe { auto.CreateOrCondition(&before, &c)? },
        });
    }
    let request = |scope: TreeScope, mode: AutomationElementMode| -> windows::core::Result<IUIAutomationCacheRequest> {
        // SAFETY: a request of ours, filled before use.
        unsafe {
            let r = auto.CreateCacheRequest()?;
            for p in [UIA_ControlTypePropertyId, UIA_NamePropertyId, UIA_BoundingRectanglePropertyId, UIA_IsOffscreenPropertyId, UIA_IsPasswordPropertyId] {
                r.AddProperty(p)?;
            }
            r.SetTreeScope(scope)?;
            r.SetAutomationElementMode(mode)?;
            r.SetTreeFilter(filter.as_ref().unwrap())?;
            Ok(r)
        }
    };
    let cache = request(TreeScope_Subtree, AutomationElementMode_None)?;
    let kids = request(TreeScope(TreeScope_Element.0 | TreeScope_Children.0), AutomationElementMode_Full)?;
    Ok(Uia { auto, cache, kids })
}

/// A name as sent: on one line, its spaces collapsed, cut to 40 characters.
pub fn short(name: &str) -> String {
    name.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(NOM_MAX).collect()
}

fn overlaps(a: &RECT, b: &RECT) -> bool {
    a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom
}

struct Walk<'a> {
    out: Vec<Element>,
    passwords: Vec<RECT>,
    nodes: usize,
    errors: usize,
    frame: RECT,
    browser: bool,
    hidden: &'a [RECT],
}

/// What one node gives: maybe a command, maybe a password field. Whether to go under it, and
/// what it is for the nodes under it: (in a table, in a page, in the title bar).
fn visit(el: &IUIAutomationElement, in_data: bool, in_doc: bool, in_bar: bool, w: &mut Walk) -> Option<(bool, bool, bool)> {
    w.nodes += 1;
    // SAFETY: cached properties of an element of ours.
    let t = unsafe { el.CachedControlType() }.unwrap_or_default();
    let rect = unsafe { el.CachedBoundingRectangle() }.unwrap_or_default();
    let offscreen = unsafe { el.CachedIsOffscreen() }.map(|b| b.as_bool()).unwrap_or(true);
    let on = rect.right > rect.left && rect.bottom > rect.top && overlaps(&rect, &w.frame);
    if t == UIA_EditControlTypeId && on && unsafe { el.CachedIsPassword() }.is_ok_and(|b| b.as_bool()) {
        w.passwords.push(rect);
    }
    let data = in_data || DATA.contains(&t);
    let doc = in_doc || t == UIA_DocumentControlTypeId;
    let bar = in_bar || t == UIA_TitleBarControlTypeId;
    if let Some(k) = kind(t) {
        if on && !offscreen && !data && (!w.browser || doc) && !w.hidden.iter().any(|h| overlaps(h, &rect)) {
            let name = unsafe { el.CachedName() }.map(|b| short(&b.to_string())).unwrap_or_default();
            w.out.push(Element { kind: k, name, rect, titlebar: bar });
        }
        if matches!(t, UIA_EditControlTypeId | UIA_ComboBoxControlTypeId) {
            return None;
        }
    }
    Some((data, doc, bar))
}

fn children(el: &IUIAutomationElement) -> Vec<IUIAutomationElement> {
    let Ok(all) = (unsafe { el.GetCachedChildren() }) else { return Vec::new() };
    (0..unsafe { all.Length() }.unwrap_or(0)).filter_map(|i| unsafe { all.GetElement(i) }.ok()).collect()
}

/// The subtree came back whole: walked here.
fn walk(el: &IUIAutomationElement, in_data: bool, in_doc: bool, in_bar: bool, w: &mut Walk) {
    if let Some((data, doc, bar)) = visit(el, in_data, in_doc, in_bar, w) {
        for c in children(el) {
            walk(&c, data, doc, bar, w);
        }
    }
}

/// One level at a time: a page or a title bar is asked for its own level; a command, a field, a
/// table or a list never is (nothing of a table's rows is read, nor walked by the other process).
fn walk_pruned(u: &Uia, el: &IUIAutomationElement, in_data: bool, in_doc: bool, in_bar: bool, w: &mut Walk) {
    if let Some((data, doc, bar)) = visit(el, in_data, in_doc, in_bar, w) {
        for c in children(el) {
            // SAFETY: cached property of an element of ours.
            let t = unsafe { c.CachedControlType() }.unwrap_or_default();
            if kind(t).is_some() || DATA.contains(&t) {
                visit(&c, data, doc, bar, w);
            } else if let Ok(c) = unsafe { c.BuildUpdatedCache(&u.kids) } {
                walk_pruned(u, &c, data, doc, bar, w);
            } else {
                w.errors += 1;
            }
        }
    }
}

/// The top-level windows of the process on screen, the one on top first: its main window, its
/// dialogs, its open menus.
pub fn windows_of(pid: u32) -> Vec<HWND> {
    unsafe extern "system" fn each(h: HWND, all: LPARAM) -> windows::core::BOOL {
        // SAFETY: the Vec lent below, alive for the whole call.
        unsafe { (*(all.0 as *mut Vec<HWND>)).push(h) };
        windows::core::BOOL(1)
    }
    let mut all: Vec<HWND> = Vec::new();
    // SAFETY: `each` only pushes to `all`, which outlives the call.
    let _ = unsafe { EnumWindows(Some(each), LPARAM(&mut all as *mut Vec<HWND> as isize)) };
    all.into_iter().filter(|&h| pid_of(h) == pid && visible(h) && !minimized(h) && !cloaked(h) && frame(h).is_some()).collect()
}

pub fn is_browser(app: &str) -> bool {
    let exe = app.rsplit(['\\', '/']).next().unwrap_or(app).to_lowercase();
    ["msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"].contains(&exe.as_str())
}

pub fn is_remote(app: &str) -> bool {
    let exe = app.rsplit(['\\', '/']).next().unwrap_or(app).to_lowercase();
    DISTANTS.contains(&exe.as_str())
}

/// The commands of the program's windows on screen, in physical pixels.
pub fn commands(pid: u32, app: &str, hidden: &[RECT]) -> Result<Liste, String> {
    commands_by(pid, app, hidden, true)
}

/// `pruned`: one level at a time (the default); else the whole subtree in one call.
pub fn commands_by(pid: u32, app: &str, hidden: &[RECT], pruned: bool) -> Result<Liste, String> {
    let _physical = Physical::enter();
    UIA.with(|cell| {
        let mut slot = cell.borrow_mut();
        if slot.is_none() {
            *slot = Some(uia().map_err(|e| format!("UIA: {e}"))?);
        }
        let u = slot.as_ref().unwrap();
        let mut liste = Liste::default();
        for h in windows_of(pid) {
            let Some(f) = frame(h) else { continue };
            // SAFETY: a window handle; its subtree, or its first level, comes back cached.
            let root = match unsafe { u.auto.ElementFromHandleBuildCache(h, if pruned { &u.kids } else { &u.cache }) } {
                Ok(r) => r,
                Err(e) => {
                    liste.errors.push(format!("{:?}: {e}", h.0));
                    continue;
                }
            };
            let mut w = Walk { out: Vec::new(), passwords: Vec::new(), nodes: 0, errors: 0, frame: f, browser: is_browser(app), hidden };
            if pruned {
                walk_pruned(u, &root, false, false, false, &mut w);
            } else {
                walk(&root, false, false, false, &mut w);
            }
            if w.errors > 0 {
                liste.errors.push(format!("{:?}: {} levels not given", h.0, w.errors));
            }
            liste.elements.extend(w.out);
            liste.passwords.extend(w.passwords);
            liste.nodes += w.nodes;
            liste.windows += 1;
        }
        Ok(liste)
    })
}

/// Bibo points by pixels, as in v1, when the tree has nothing (a remote desktop, an app without
/// accessibility): no command but the window's own buttons, or the program shows another computer.
pub fn repli_pixels(app: &str, liste: &Liste) -> bool {
    liste.elements.iter().all(|e| e.titlebar) || is_remote(app)
}

/// Image pixels to a screen pixel for this window's capture. PrintWindow draws a window Windows
/// stretches (a program unaware of the scale) at its own size, not the screen's: 96 / 144 at 150 %.
pub fn image_ratio(h: HWND) -> f64 {
    let _physical = Physical::enter();
    // SAFETY: a window handle, plain values.
    unsafe {
        let own = GetDpiForWindow(h);
        let (mut x, mut y) = (0, 0);
        let screen = GetDpiForMonitor(MonitorFromWindow(h, MONITOR_DEFAULTTONEAREST), MDT_EFFECTIVE_DPI, &mut x, &mut y).map(|_| x).unwrap_or(own);
        if own == 0 || screen == 0 {
            1.0
        } else {
            own as f64 / screen as f64
        }
    }
}

/// A screen box in the image of a window whose frame starts at `origin`, `ratio` image pixels to a
/// screen pixel: (x, y, w, h).
pub fn in_image(r: &RECT, origin: (i32, i32), ratio: f64) -> (i32, i32, i32, i32) {
    let x = ((r.left - origin.0) as f64 * ratio).round() as i32;
    let y = ((r.top - origin.1) as f64 * ratio).round() as i32;
    let x2 = ((r.right - origin.0) as f64 * ratio).round() as i32;
    let y2 = ((r.bottom - origin.1) as f64 * ratio).round() as i32;
    (x, y, x2 - x, y2 - y)
}

/// The lines the engine would get: `#7 bouton "Nouveau" [x,y,l,h]`, boxes in the image.
pub fn lignes(liste: &Liste, origin: (i32, i32), ratio: f64) -> Vec<String> {
    liste
        .elements
        .iter()
        .enumerate()
        .map(|(i, e)| {
            let (x, y, w, h) = in_image(&e.rect, origin, ratio);
            format!("#{} {} \"{}\" [{x},{y},{w},{h}]", i + 1, e.kind, e.name)
        })
        .collect()
}

/// Password fields blacked in the image, opaque.
pub fn noircir(img: &mut Rgba, rects: &[RECT], origin: (i32, i32), ratio: f64) {
    for r in rects {
        let (x, y, w, h) = in_image(r, origin, ratio);
        let (x0, y0) = (x.max(0) as u32, y.max(0) as u32);
        let (x1, y1) = (((x + w).max(0) as u32).min(img.w), ((y + h).max(0) as u32).min(img.h));
        for yy in y0..y1 {
            for xx in x0..x1 {
                let i = ((yy * img.w + xx) * 4) as usize;
                img.px[i..i + 4].copy_from_slice(&[0, 0, 0, 255]);
            }
        }
    }
}

/// On a Windows desktop only, a VM of the CI's: `cargo test --lib -- --ignored uia_ --test-threads=1`.
#[cfg(test)]
mod tests {
    use super::super::{class, grab, handle, id_of, identity, title};
    use super::*;
    use crate::capture::bureau::*;
    use crate::capture::Found;
    use std::mem::size_of;
    use std::path::{Path, PathBuf};
    use std::process::Command;
    use std::time::{Duration, Instant};
    use windows::Win32::Foundation::POINT;
    use windows::Win32::Graphics::Gdi::{MonitorFromPoint, MONITOR_DEFAULTTOPRIMARY};
    use windows::Win32::UI::HiDpi::{GetAwarenessFromDpiAwarenessContext, GetWindowDpiAwarenessContext};
    use windows::Win32::UI::Input::KeyboardAndMouse::{SendInput, INPUT, INPUT_0, INPUT_MOUSE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEINPUT, MOUSE_EVENT_FLAGS};
    use windows::Win32::UI::WindowsAndMessaging::SetCursorPos;

    const RED: [u8; 3] = [0xe5, 0x39, 0x35];
    const PURPLE: [u8; 3] = [0x8e, 0x24, 0xaa];
    const CLIENTS: [&str; 30] = [
        "Boulangerie Martin", "Garage Dupont", "Pharmacie Leroy", "Plomberie Moreau", "Cafe Lefebvre",
        "Fleuriste Garnier", "Librairie Fournier", "Menuiserie Girard", "Coiffure Bonnet", "Epicerie Lambert",
        "Optique Fontaine", "Traiteur Rousseau", "Cordonnerie Vincent", "Pressing Muller", "Quincaillerie Faure",
        "Boucherie Andre", "Fromagerie Mercier", "Auto-ecole Blanc", "Imprimerie Guerin", "Serrurerie Boyer",
        "Electricite Chevalier", "Toiture Francois", "Jardinerie Legrand", "Cave Gauthier", "Patisserie Perrin",
        "Bijouterie Robin", "Opticien Clement", "Tabac Morin", "Brasserie Nicolas", "Atelier Henry",
    ];

    fn mesure(test: &str, what: &str, value: impl std::fmt::Display) {
        println!("MESURE {test} {what}={value}");
    }

    fn scale() -> u32 {
        let _p = Physical::enter();
        let (mut x, mut y) = (0, 0);
        // SAFETY: plain values.
        unsafe {
            let m = MonitorFromPoint(POINT { x: 0, y: 0 }, MONITOR_DEFAULTTOPRIMARY);
            let _ = GetDpiForMonitor(m, MDT_EFFECTIVE_DPI, &mut x, &mut y);
        }
        x * 100 / 96
    }

    fn click_at(r: &RECT) {
        let _p = Physical::enter();
        // SAFETY: plain values; the inputs live for the call.
        unsafe { SetCursorPos((r.left + r.right) / 2, (r.top + r.bottom) / 2) }.unwrap();
        let input = |flags: MOUSE_EVENT_FLAGS| INPUT { r#type: INPUT_MOUSE, Anonymous: INPUT_0 { mi: MOUSEINPUT { dwFlags: flags, ..Default::default() } } };
        let sent = unsafe { SendInput(&[input(MOUSEEVENTF_LEFTDOWN), input(MOUSEEVENTF_LEFTUP)], size_of::<INPUT>() as i32) };
        assert_eq!(sent, 2, "SendInput");
    }

    fn timed(pid: u32, app: &str) -> (Liste, f64) {
        let t = Instant::now();
        let l = commands(pid, app, &[]).unwrap();
        (l, t.elapsed().as_secs_f64() * 1000.0)
    }

    /// p50, p90 and max of 20 lists in a row, one level at a time (Bibo's way), then the whole
    /// subtree in one call (to compare): the p90 of the first. Both give the same list.
    fn latency(test: &str, pid: u32, app: &str) -> f64 {
        let mut p90 = 0.0;
        for (pruned, tag) in [(true, ""), (false, "_sous_arbre")] {
            let mut ms: Vec<f64> = (0..20)
                .map(|_| {
                    let t = Instant::now();
                    commands_by(pid, app, &[], pruned).unwrap();
                    t.elapsed().as_secs_f64() * 1000.0
                })
                .collect();
            ms.sort_by(|a, b| a.partial_cmp(b).unwrap());
            mesure(test, &format!("ms_p50{tag}"), format!("{:.1}", ms[9]));
            mesure(test, &format!("ms_p90{tag}"), format!("{:.1}", ms[17]));
            mesure(test, &format!("ms_max{tag}"), format!("{:.1}", ms[19]));
            if pruned {
                p90 = ms[17];
            }
        }
        let a = commands_by(pid, app, &[], true).unwrap();
        let b = commands_by(pid, app, &[], false).unwrap();
        let names = |l: &Liste| l.elements.iter().map(|e| format!("{} {}", e.kind, e.name)).collect::<Vec<_>>();
        mesure(test, "noeuds_par_niveau_sous_arbre", format!("{} {}", a.nodes, b.nodes));
        mesure(test, "meme_liste", names(&a) == names(&b));
        if names(&a) != names(&b) {
            println!("DIFF {test} par niveau {:?}\nDIFF {test} sous-arbre {:?}", names(&a), names(&b));
        }
        p90
    }

    /// Every node of the window's tree (test only: the oracle of what is data), with whether it lies
    /// in a table or a list: (type, name, box, in a table).
    fn raw(h: HWND) -> Vec<(i32, String, RECT, bool)> {
        let _p = Physical::enter();
        // SAFETY: COM for this thread, then the system's client and a request of ours.
        unsafe {
            let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
            let auto: IUIAutomation = CoCreateInstance(&CUIAutomation8, None, CLSCTX_INPROC_SERVER).unwrap();
            let cache = auto.CreateCacheRequest().unwrap();
            for p in [UIA_ControlTypePropertyId, UIA_NamePropertyId, UIA_BoundingRectanglePropertyId] {
                cache.AddProperty(p).unwrap();
            }
            cache.SetTreeScope(TreeScope_Subtree).unwrap();
            cache.SetAutomationElementMode(AutomationElementMode_None).unwrap();
            cache.SetTreeFilter(&auto.CreateTrueCondition().unwrap()).unwrap();
            let root = auto.ElementFromHandleBuildCache(h, &cache).unwrap();
            fn go(el: &IUIAutomationElement, table: bool, out: &mut Vec<(i32, String, RECT, bool)>) {
                let t = unsafe { el.CachedControlType() }.unwrap_or_default();
                let name = unsafe { el.CachedName() }.map(|b| b.to_string()).unwrap_or_default();
                let rect = unsafe { el.CachedBoundingRectangle() }.unwrap_or_default();
                let table = table || t == UIA_TableControlTypeId || t == UIA_DataGridControlTypeId;
                out.push((t.0, name, rect, table));
                if let Ok(c) = unsafe { el.GetCachedChildren() } {
                    for i in 0..unsafe { c.Length() }.unwrap_or(0) {
                        if let Ok(e) = unsafe { c.GetElement(i) } {
                            go(&e, table, out);
                        }
                    }
                }
            }
            let mut out = Vec::new();
            go(&root, false, &mut out);
            out
        }
    }

    /// Client names found in the list (any of them, anywhere in a name, case ignored).
    fn leaks<'a>(l: &Liste, names: &[&'a str]) -> Vec<(&'static str, &'a str)> {
        let mut out = Vec::new();
        for e in &l.elements {
            let n = e.name.to_lowercase();
            for c in names {
                if n.contains(&c.to_lowercase()) {
                    out.push((e.kind, *c));
                }
            }
        }
        out
    }

    fn named<'a>(l: &'a Liste, kind: &str, name: &str) -> Option<&'a Element> {
        l.elements.iter().find(|e| e.kind == kind && e.name == name)
    }

    /// The box of a colour in the image against the element's box mapped into it: the largest gap of
    /// the four sides, in image pixels.
    fn gap(test: &str, img: &Rgba, colour: [u8; 3], e: &Element, origin: (i32, i32), ratio: f64) -> i32 {
        let (l, t, r, b) = bounds(img, colour).unwrap_or_else(|| panic!("{test}: no pixel of {colour:?} for {}", e.name));
        let (x, y, w, h) = in_image(&e.rect, origin, ratio);
        let g = [(x - l).abs(), (y - t).abs(), (x + w - r).abs(), (y + h - b).abs()].into_iter().max().unwrap();
        mesure(test, &format!("ecart_px[{}]", e.name), format!("{g} (image {l},{t},{r},{b} ; uia {x},{y},{},{})", x + w, y + h));
        g
    }

    fn origin_ratio(h: HWND, _img: &Rgba) -> ((i32, i32), f64) {
        let f = frame(h).unwrap();
        ((f.left, f.top), image_ratio(h))
    }

    /// cua-driver (Hermes's screen driver), the witness, on the same window, when the job gives it.
    fn temoin(test: &str, pid: u32, h: HWND, names: &[&str]) {
        let Ok(cua) = std::env::var("CUA_DRIVER") else { return };
        let arg = format!("{{\"pid\":{pid},\"window_id\":{},\"include_screenshot\":false}}", id_of(h));
        let t = Instant::now();
        let out = Command::new(&cua).args(["call", "get_window_state", &arg]).env("CUA_DRIVER_RS_TELEMETRY_ENABLED", "0").output().unwrap();
        let ms = t.elapsed().as_secs_f64() * 1000.0;
        let text = String::from_utf8_lossy(&out.stdout).to_string();
        let start = text.find('{').unwrap_or(0);
        let Ok(json) = serde_json::from_str::<serde_json::Value>(&text[start..]) else {
            mesure(test, "temoin", format!("illisible (exit {:?}, {} octets)", out.status.code(), text.len()));
            return;
        };
        fn find<'v>(v: &'v serde_json::Value) -> Option<&'v Vec<serde_json::Value>> {
            match v {
                serde_json::Value::Object(m) => m.get("elements").and_then(|e| e.as_array()).or_else(|| m.values().find_map(find)),
                serde_json::Value::Array(a) => a.iter().find_map(find),
                _ => None,
            }
        }
        let elements = find(&json).cloned().unwrap_or_default();
        let field = |e: &serde_json::Value, k: &str| e.get(k).and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
        let hits = |k: &str| elements.iter().filter(|e| names.iter().any(|c| field(e, k).contains(&c.to_lowercase()))).count();
        mesure(test, "temoin_ms", format!("{ms:.0}"));
        mesure(test, "temoin_elements", elements.len());
        mesure(test, "temoin_noms_clients_dans_label", hits("label"));
        mesure(test, "temoin_noms_clients_dans_value", hits("value"));
    }

    fn csc(source: &str, dir: &Path) -> PathBuf {
        let windir = PathBuf::from(std::env::var("WINDIR").unwrap_or_else(|_| "C:\\Windows".into()));
        let csc = windir.join("Microsoft.NET").join("Framework64").join("v4.0.30319").join("csc.exe");
        let exe = dir.join(source.replace(".cs", ".exe"));
        let src = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests").join("fixtures").join("guide").join(source);
        let out = Command::new(&csc)
            .args(["/nologo", "/target:winexe", "/optimize+", "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll"])
            .arg(format!("/out:{}", exe.display()))
            .arg(&src)
            .output()
            .unwrap();
        assert!(out.status.success(), "csc: {}", String::from_utf8_lossy(&out.stdout));
        exe
    }

    fn edge() -> &'static str {
        ["C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe", "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe"]
            .into_iter()
            .find(|b| Path::new(b).is_file())
            .expect("Edge")
    }

    fn until_list(test: &str, pid: u32, app: &str, wait: Duration, done: impl Fn(&Liste) -> bool) -> Liste {
        let start = Instant::now();
        loop {
            let l = commands(pid, app, &[]).unwrap();
            if done(&l) {
                mesure(test, "s_jusqu_a_la_liste", format!("{:.1}", start.elapsed().as_secs_f64()));
                return l;
            }
            assert!(
                start.elapsed() < wait,
                "{test}: never there; last: {:?}, {} nodes, {} windows, errors {:?}",
                l.elements.iter().map(|e| (e.kind, e.name.clone())).collect::<Vec<_>>(),
                l.nodes,
                l.windows,
                l.errors
            );
            std::thread::sleep(Duration::from_millis(250));
        }
    }

    fn print_list(test: &str, l: &Liste, origin: (i32, i32), ratio: f64) {
        mesure(test, "elements", l.elements.len());
        mesure(test, "noeuds_lus", l.nodes);
        for line in lignes(l, origin, ratio) {
            println!("LISTE {test} {line}");
        }
    }

    /// The ERP-like window, as an unaware, a system-aware or a per-monitor program: time, boxes
    /// against its own image, the password field blacked, no client, names cut, a hidden zone.
    fn atelier(dpi: &str) {
        let dir = tempfile::tempdir().unwrap();
        let exe = csc("atelier_uia.cs", dir.path());
        let test = format!("atelier_{dpi}_{}", scale());
        let titre = format!("Atelier UIA {dpi} {}", std::process::id());
        let program = Program(Command::new(&exe).args([titre.as_str(), "60", "60", "900", "640", dpi]).spawn().unwrap());
        let win: Found = listed_as(|f| f.titre == titre);
        let (pid, app) = (program.0.id(), win.window.app.clone());
        let h = handle(win.window.id);
        // SAFETY: a window handle.
        let awareness = unsafe { GetAwarenessFromDpiAwarenessContext(GetWindowDpiAwarenessContext(h)) };
        mesure(&test, "dpi_fenetre", format!("{} (awareness {})", unsafe { GetDpiForWindow(h) }, awareness.0));
        until("drawn", || grab(&win.window).is_ok_and(|i| count(&i, GREEN) > 1000));
        let (first, ms) = timed(pid, &app);
        mesure(&test, "ms_premier_appel", format!("{ms:.1}"));
        mesure(&test, "elements_premier_appel", first.elements.len());
        let l = until_list(&test, pid, &app, WAIT, |l| named(l, "bouton", "Nouveau").is_some());
        let p90 = latency(&test, pid, &app);

        let mut img = grab(&win.window).unwrap();
        let (origin, ratio) = origin_ratio(h, &img);
        mesure(&test, "image", format!("{}x{} ratio {ratio:.3}, noir {:.2}", img.w, img.h, crate::shot::black_share(&img)));
        print_list(&test, &l, origin, ratio);

        let mut worst = 0;
        for (colour, kind, name) in [(GREEN, "bouton", "Nouveau"), (PURPLE, "bouton", "Enregistrer"), (RED, "bouton", "Supprimer")] {
            let e = named(&l, kind, name).unwrap_or_else(|| panic!("{test}: {name}"));
            worst = worst.max(gap(&test, &img, colour, e, origin, ratio));
        }
        assert_eq!(l.passwords.len(), 1, "{test}: the password field");
        let field = Element { kind: "champ", name: "mot de passe".into(), rect: l.passwords[0], titlebar: false };
        worst = worst.max(gap(&test, &img, ORANGE, &field, origin, ratio));
        mesure(&test, "ecart_max_px", worst);
        let before = count(&img, ORANGE);
        noircir(&mut img, &l.passwords, origin, ratio);
        mesure(&test, "orange_avant_apres", format!("{before} -> {}", count(&img, ORANGE)));

        let found = leaks(&l, &CLIENTS);
        mesure(&test, "noms_clients", format!("{} {:?}", found.len(), found));
        let zone = named(&l, "bouton", "Supprimer").unwrap().rect;
        let hid = commands(pid, &app, &[zone]).unwrap();
        temoin(&test, pid, h, &CLIENTS);

        assert!(before > 500 && count(&img, ORANGE) == 0, "{test}: the password field blacked");
        for want in ["Fichier", "Edition", "Aide", "Factures", "Clients", "Payee", "HT", "TTC", "Ouvrir la fiche"] {
            assert!(l.elements.iter().any(|e| e.name == want), "{test}: {want} listed");
        }
        assert!(found.is_empty(), "{test}: no client in the list");
        assert!(l.elements.iter().all(|e| e.name.chars().count() <= NOM_MAX), "{test}: names cut");
        assert!(l.elements.iter().any(|e| e.name == "Exporter toutes les factures du trimestr"), "{test}: the long one, cut");
        assert!(l.elements.iter().all(|e| e.name != "Start" && e.name != "Démarrer"), "{test}: nothing of another program");
        assert!(!repli_pixels(&app, &l), "{test}: a tree, no pixels");
        assert!(named(&hid, "bouton", "Supprimer").is_none() && named(&hid, "bouton", "Nouveau").is_some(), "{test}: the hidden zone");
        assert!(worst <= 4, "{test}: boxes within 4 px of the image ({worst})");
        assert!(p90 < 300.0, "{test}: p90 {p90:.1} ms");
    }

    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_atelier_unaware() {
        atelier("unaware");
    }

    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_atelier_system() {
        atelier("system");
    }

    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_atelier_pmv2() {
        atelier("pmv2");
    }

    /// A window that draws itself without a control (v1's window of a colour, as a canvas or an app
    /// without accessibility would): nothing but its title bar's buttons, so pixels.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_sans_arbre() {
        let test = format!("sans_arbre_{}", scale());
        let dir = tempfile::tempdir().unwrap();
        let exe = csc("atelier.cs", dir.path());
        let titre = format!("Atelier toile {}", std::process::id());
        let program = Program(Command::new(&exe).args(["fenetre", titre.as_str(), "80", "80", "640", "480", "1e88e5"]).spawn().unwrap());
        let win = listed_as(|f| f.titre == titre);
        let l = until_list(&test, program.0.id(), &win.window.app, WAIT, |l| !l.elements.is_empty());
        mesure(&test, "elements", l.elements.len());
        for e in &l.elements {
            println!("LISTE {test} {} \"{}\" barre={}", e.kind, e.name, e.titlebar);
        }
        assert!(repli_pixels(&win.window.app, &l), "{test}: pixels");
    }

    /// An Odoo-like page in Edge, with a tab of mail behind it: time (cold, then warm), boxes, the
    /// password field blacked, no client from the table, no other tab's title, nothing out of view.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_edge_imitation() {
        let test = format!("edge_imitation_{}", scale());
        let dir = tempfile::tempdir().unwrap();
        let page = dir.path().join("odoo.html");
        let html = std::fs::read_to_string(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../spike/guide-v2-uia/odoo.html")).unwrap();
        std::fs::write(&page, html.replace("Garage Dupont</a></div>", "Bijouterie Robin</a></div>")).unwrap();
        let mail = dir.path().join("mail.html");
        std::fs::write(&mail, "<!doctype html><meta charset=utf-8><title>Boite de reception - Garage Dupont</title><p>Courrier</p>").unwrap();
        let url = |p: &Path| format!("file:///{}", p.display().to_string().replace('\\', "/"));
        let profile = format!("--user-data-dir={}", dir.path().join("profil").display());
        let flags = ["--no-first-run", "--no-default-browser-check", "--disable-sync", "--window-position=40,40", "--window-size=1280,860"];
        let browser = Program(Command::new(edge()).arg(&profile).args(flags).arg(url(&mail)).spawn().unwrap());
        listed_as(|f| f.window.pid == browser.0.id());
        let _second = Command::new(edge()).arg(&profile).args(flags).arg(url(&page)).spawn().unwrap();
        let win = listed_as(|f| f.window.pid == browser.0.id() && f.titre.contains("Devis - Odoo"));
        let (pid, app) = (win.window.pid, win.window.app.clone());
        assert!(is_browser(&app), "{app}");
        let (first, ms) = timed(pid, &app);
        mesure(&test, "ms_premier_appel", format!("{ms:.1}"));
        mesure(&test, "elements_premier_appel", first.elements.len());
        let l = until_list(&test, pid, &app, Duration::from_secs(60), |l| named(l, "bouton", "Nouveau").is_some() && l.elements.len() > 15);
        let p90 = latency(&test, pid, &app);

        let h = handle(win.window.id);
        let mut img = grab(&win.window).unwrap();
        let (origin, ratio) = origin_ratio(h, &img);
        mesure(&test, "image", format!("{}x{} ratio {ratio:.3}", img.w, img.h));
        print_list(&test, &l, origin, ratio);
        let mut worst = 0;
        for (colour, name) in [(GREEN, "Nouveau"), (PURPLE, "Enregistrer"), (RED, "Supprimer")] {
            let e = named(&l, "bouton", name).unwrap_or_else(|| panic!("{test}: {name}"));
            worst = worst.max(gap(&test, &img, colour, e, origin, ratio));
        }
        assert_eq!(l.passwords.len(), 1, "{test}: the password field");
        let field = Element { kind: "champ", name: "mot de passe".into(), rect: l.passwords[0], titlebar: false };
        worst = worst.max(gap(&test, &img, ORANGE, &field, origin, ratio));
        mesure(&test, "ecart_max_px", worst);
        let before = count(&img, ORANGE);
        noircir(&mut img, &l.passwords, origin, ratio);
        mesure(&test, "orange_avant_apres", format!("{before} -> {}", count(&img, ORANGE)));

        let table = &CLIENTS[..20];
        let found = leaks(&l, table);
        mesure(&test, "noms_clients_du_tableau", format!("{} {:?}", found.len(), found));
        let cells = raw(h).into_iter().filter(|n| n.3 && n.1.len() >= 4).count();
        mesure(&test, "oracle_noeuds_du_tableau", cells);
        let out_of_table = leaks(&l, &["Bijouterie Robin"]);
        mesure(&test, "lien_client_hors_tableau", out_of_table.len());
        temoin(&test, pid, h, table);

        assert!(before > 500 && count(&img, ORANGE) == 0, "{test}: the password field blacked");
        assert!(found.is_empty(), "{test}: no client of the table");
        assert!(l.elements.iter().all(|e| e.kind != "onglet" && !e.name.contains("Boite de reception")), "{test}: no other tab");
        assert!(named(&l, "bouton", "Bas de page").is_none() && named(&l, "bouton", "Cache derriere").is_none(), "{test}: nothing out of view");
        assert!(l.elements.iter().all(|e| e.name.chars().count() <= NOM_MAX), "{test}: names cut");
        for want in ["Ventes", "Commandes", "Menu des applications", "Rechercher", "Suivant", "Planifier une activite"] {
            assert!(l.elements.iter().any(|e| e.name == want), "{test}: {want} listed");
        }
        assert!(worst <= 4, "{test}: boxes within 4 px of the image ({worst})");
        assert!(p90 < 300.0, "{test}: p90 {p90:.1} ms");
    }

    /// The public Odoo demo in Edge (its quotations, a table of known clients). Not reachable: said,
    /// not failed.
    #[test]
    #[ignore = "a Windows desktop (VM) and the internet"]
    fn uia_odoo_demo() {
        const DEMO: [&str; 16] = [
            "Acme Corporation", "Azure Interior", "Gemini Furniture", "Lumber Inc", "Ready Mat", "The Jackson Group", "Wood Corner", "Deco Addict",
            "Joel Willis", "Brandon Freeman", "Colleen Diaz", "Nicole Ford", "Addison Olson", "Douglas Fletcher", "Floyd Steward", "Edwin Hansen",
        ];
        let test = format!("odoo_demo_{}", scale());
        let dir = tempfile::tempdir().unwrap();
        let profile = format!("--user-data-dir={}", dir.path().join("profil").display());
        let browser = Program(
            Command::new(edge())
                .arg(&profile)
                .args(["--no-first-run", "--no-default-browser-check", "--disable-sync", "--window-position=40,40", "--window-size=1280,860", "https://demo.odoo.com/"])
                .spawn()
                .unwrap(),
        );
        let win = listed_as(|f| f.window.pid == browser.0.id());
        let (pid, app) = (win.window.pid, win.window.app.clone());
        let h = handle(win.window.id);
        let start = Instant::now();
        // The home menu: Odoo's apps are options of a list box (role="option"), so not in Bibo's list;
        // found here in the whole tree, the oracle's.
        let sales = loop {
            if let Some(n) = raw(h).into_iter().find(|n| n.1 == "Sales" || n.1 == "Ventes") {
                break n;
            }
            if start.elapsed() > Duration::from_secs(120) {
                mesure(&test, "odoo_demo", "injoignable ou pas d'accueil en 120 s");
                return;
            }
            std::thread::sleep(Duration::from_millis(500));
        };
        mesure(&test, "s_jusqu_a_l_accueil", format!("{:.1}", start.elapsed().as_secs_f64()));
        std::thread::sleep(Duration::from_secs(2));
        let (home, ms) = timed(pid, &app);
        let tiles = raw(h).into_iter().filter(|n| n.0 == UIA_ListItemControlTypeId.0 && n.1.len() > 2).count();
        mesure(&test, "accueil_ms", format!("{ms:.1}"));
        mesure(&test, "accueil_elements", home.elements.len());
        mesure(&test, "accueil_tuiles_d_applis_listitem", tiles);
        mesure(&test, "accueil_sales_dans_la_liste", home.elements.iter().any(|e| e.name == "Sales"));
        print_list(&format!("{test}_accueil"), &home, (0, 0), 1.0);
        click_at(&sales.2);
        until_list(&test, pid, &app, Duration::from_secs(60), |l| l.elements.iter().any(|e| e.name == "New" || e.name == "Nouveau"));
        until("the quotations' table", || raw(h).iter().filter(|n| n.3 && DEMO.iter().any(|c| n.1 == *c)).count() >= 5);
        std::thread::sleep(Duration::from_secs(2));
        let p90 = latency(&test, pid, &app);
        let (l, _) = timed(pid, &app);
        let img = grab(&win.window).unwrap();
        let (origin, ratio) = origin_ratio(h, &img);
        print_list(&test, &l, origin, ratio);
        let nodes = raw(h);
        let cells: Vec<String> = nodes.iter().filter(|n| n.3 && n.1.chars().filter(|c| c.is_alphabetic()).count() >= 4).map(|n| n.1.to_lowercase()).collect();
        mesure(&test, "oracle_cellules", cells.len());
        let found = leaks(&l, &DEMO);
        let equal: Vec<&str> = l.elements.iter().filter(|e| cells.contains(&e.name.to_lowercase())).map(|e| e.name.as_str()).collect();
        mesure(&test, "noms_clients", format!("{} {:?}", found.len(), found));
        mesure(&test, "egal_a_une_cellule", format!("{} {:?}", equal.len(), equal));
        temoin(&test, pid, h, &DEMO);

        // A quotation's form: the client is a field's value there, not a table's cell.
        let row = nodes.iter().find(|n| n.3 && DEMO.iter().any(|c| n.1 == *c)).map(|n| n.2).unwrap();
        click_at(&row);
        let start = Instant::now();
        while start.elapsed() < Duration::from_secs(30) && !raw(h).iter().any(|n| n.1 == "Customer" || n.1 == "Confirm" || n.1 == "Send by Email") {
            std::thread::sleep(Duration::from_millis(500));
        }
        std::thread::sleep(Duration::from_secs(2));
        let (form, ms) = timed(pid, &app);
        mesure(&test, "fiche_ms", format!("{ms:.1}"));
        print_list(&format!("{test}_fiche"), &form, origin, ratio);
        let in_form = leaks(&form, &DEMO);
        mesure(&test, "fiche_noms_clients", format!("{} {:?}", in_form.len(), in_form));
        temoin(&format!("{test}_fiche"), pid, h, &DEMO);

        assert!(found.is_empty() && equal.is_empty(), "{test}: no client of the demo's table");
        assert!(l.elements.iter().all(|e| e.name.chars().count() <= NOM_MAX));
        assert!(p90 < 300.0, "{test}: p90 {p90:.1} ms");
    }

    /// A remote desktop's windows (mstsc, opened by the job, its pid in BIBO_LOIN_PID): their tree has
    /// nothing of the other computer, so Bibo points by pixels; their image is not black. Against
    /// the same program run here, whose menus are listed.
    #[test]
    #[ignore = "a Windows desktop (VM) with mstsc open"]
    fn uia_bureau_distance() {
        let test = format!("bureau_distance_{}", scale());
        let pid: u32 = std::env::var("BIBO_LOIN_PID").expect("BIBO_LOIN_PID").parse().unwrap();
        let wins = windows_of(pid);
        assert!(!wins.is_empty(), "{test}: no mstsc window");
        let app = identity(wins[0]).unwrap().app;
        let (l, ms) = timed(pid, &app);
        mesure(&test, "fenetres", wins.iter().map(|&h| format!("{}|{}", class(h), short(&title(h)))).collect::<Vec<_>>().join(" ; "));
        mesure(&test, "ms", format!("{ms:.1}"));
        mesure(&test, "elements", l.elements.len());
        mesure(&test, "noeuds_lus", l.nodes);
        for e in &l.elements {
            println!("LISTE {test} {} \"{}\"", e.kind, e.name);
        }
        for &h in &wins {
            let w = identity(h).unwrap();
            if let Ok(img) = grab(&w) {
                mesure(&test, &format!("noir[{}]", short(&title(h))), format!("{:.3}", crate::shot::black_share(&img)));
            }
        }
        let remote = ["File", "Edit", "Format", "View", "Help"];
        assert!(l.elements.iter().all(|e| !remote.contains(&e.name.as_str())), "{test}: nothing of the remote app");
        assert!(repli_pixels(&app, &l), "{test}: pixels");

        let here = Program(Command::new("notepad.exe").spawn().unwrap());
        let mine = listed_as(|f| f.window.pid == here.0.id());
        let local = until_list(&test, mine.window.pid, &mine.window.app, WAIT, |l| l.elements.iter().any(|e| e.kind == "menu"));
        for e in &local.elements {
            println!("LISTE {test}_local {} \"{}\"", e.kind, e.name);
        }
        mesure(&test, "bloc_notes_local_elements", local.elements.len());
        assert!(!repli_pixels(&mine.window.app, &local), "{test}: the same program here has its tree");
    }
}
