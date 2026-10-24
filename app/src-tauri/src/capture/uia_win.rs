//! Windows: the guide v2's elements from UI Automation (uia.rs holds the rules). The system's UIA
//! client, in process, on the reader's thread. The program's tree is read one level at a time, in a
//! view filtered on the kinds the rules need: a command, a table or a list is never asked for what
//! lies under it, so their rows are not even walked by the other process (P0: the whole tree took 166
//! to 405 ms at p90, one level at a time 17 to 63 ms). The password fields, by one search of each
//! window's whole tree, done by its own process, which gives back their boxes only.

use std::mem::size_of;

use windows::core::Interface;
use windows::Win32::Foundation::{HWND, LPARAM, POINT, RECT};
use windows::Win32::Graphics::Gdi::{GetMonitorInfoW, MonitorFromWindow, MONITORINFO, MONITOR_DEFAULTTONEAREST};
use windows::Win32::System::Com::{CoCreateInstance, CoInitializeEx, CoUninitialize, CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED};
use windows::Win32::System::Variant::VARIANT;
use windows::Win32::UI::Accessibility::*;
use windows::Win32::UI::Input::KeyboardAndMouse::IsWindowEnabled;
use windows::Win32::UI::WindowsAndMessaging::{
    EnumChildWindows, EnumWindows, GetAncestor, GetWindowLongW, GetWindowThreadProcessId, WindowFromPoint, GA_ROOT, GWL_STYLE, WS_CAPTION, WS_POPUP,
};

use super::uia::{
    attend_un_choix, compte, court, coupe, descendre, dialogue, distant, garde, navigateur, tant_que, visible, Cadre, Contexte, Element, Genre, Lire, Liste,
    Secrets, Sorte, ELEMENTS_MAX,
};
use super::win::{class, cloaked, foreground, frame, handle, identity, list, minimized, owner, pid_of, visible as shown, Physical, SHELL};
use super::{same_window, Rate, Window};

/// What each control type is to the rules; the view holds these kinds only. Text, images, groups,
/// headers and the like are never in it.
const SORTES: [(UIA_CONTROLTYPE_ID, Sorte); 21] = [
    (UIA_ButtonControlTypeId, Sorte::Commande(Genre::Bouton)),
    (UIA_SplitButtonControlTypeId, Sorte::Commande(Genre::Bouton)),
    (UIA_MenuItemControlTypeId, Sorte::Commande(Genre::Menu)),
    (UIA_TabItemControlTypeId, Sorte::Commande(Genre::Onglet)),
    (UIA_HyperlinkControlTypeId, Sorte::Commande(Genre::Lien)),
    (UIA_CheckBoxControlTypeId, Sorte::Commande(Genre::Case)),
    (UIA_RadioButtonControlTypeId, Sorte::Commande(Genre::BoutonRadio)),
    (UIA_ComboBoxControlTypeId, Sorte::Commande(Genre::Liste)),
    (UIA_EditControlTypeId, Sorte::Commande(Genre::Champ)),
    (UIA_DataGridControlTypeId, Sorte::Donnees),
    (UIA_TableControlTypeId, Sorte::Donnees),
    (UIA_ListControlTypeId, Sorte::Donnees),
    (UIA_TreeControlTypeId, Sorte::Donnees),
    (UIA_DataItemControlTypeId, Sorte::Donnees),
    (UIA_ListItemControlTypeId, Sorte::Donnees),
    (UIA_TreeItemControlTypeId, Sorte::Donnees),
    (UIA_DocumentControlTypeId, Sorte::Page),
    (UIA_MenuBarControlTypeId, Sorte::Barre),
    (UIA_MenuControlTypeId, Sorte::Barre),
    (UIA_ToolBarControlTypeId, Sorte::Barre),
    (UIA_TitleBarControlTypeId, Sorte::BarreTitre),
];

/// Deeper than this in the view, nothing is read: a page cannot make the reader walk forever, nor
/// overflow its stack. A program's commands lie a few levels down.
const PROFONDEUR_MAX: usize = 24;

/// A control type and a landmark (a page's `<nav>`) as the rules see them.
fn sorte(t: UIA_CONTROLTYPE_ID, repere: i32) -> Sorte {
    match SORTES.iter().find(|(k, _)| *k == t) {
        Some((_, s)) => *s,
        None if repere == UIA_NavigationLandmarkTypeId.0 => Sorte::Barre,
        None => Sorte::Autre,
    }
}

fn cadre(r: RECT) -> Cadre {
    Cadre { x: r.left, y: r.top, largeur: r.right - r.left, hauteur: r.bottom - r.top }
}

/// COM for the reader's thread, released after the reader's interfaces (it is their last field).
struct Com(bool);

impl Drop for Com {
    fn drop(&mut self) {
        if self.0 {
            // SAFETY: paired with this thread's CoInitializeEx that succeeded.
            unsafe { CoUninitialize() };
        }
    }
}

pub(super) struct Lecteur {
    auto: IUIAutomation,
    /// A node, its properties and its children in the filtered view, alive to go further down.
    vue: IUIAutomationCacheRequest,
    /// The password fields anywhere under a window, and its pages.
    cherches: IUIAutomationCondition,
    /// Their boxes and what they are, nothing else.
    leurs_cadres: IUIAutomationCacheRequest,
    _com: Com,
}

impl Lecteur {
    pub(super) fn nouveau() -> Result<Lecteur, Rate> {
        // SAFETY: COM for this thread, released by Com's drop when it was taken.
        let com = Com(unsafe { CoInitializeEx(None, COINIT_MULTITHREADED) }.is_ok());
        let fail = |_| Rate::Indisponible;
        // SAFETY: the system's UIA client, in process.
        let auto: IUIAutomation = unsafe { CoCreateInstance(&CUIAutomation8, None, CLSCTX_INPROC_SERVER) }.map_err(fail)?;
        if let Ok(a2) = auto.cast::<IUIAutomation2>() {
            // SAFETY: plain values. A program that hangs does not hold the reader long.
            unsafe {
                let _ = a2.SetConnectionTimeout(2000);
                let _ = a2.SetTransactionTimeout(2000);
            }
        }
        let condition = |p: UIA_PROPERTY_ID, v: VARIANT| {
            // SAFETY: a condition on a property UIA defines.
            unsafe { auto.CreatePropertyCondition(p, &v) }
        };
        let mut filtre = None;
        for (t, _) in SORTES {
            filtre = Some(ou(&auto, filtre, condition(UIA_ControlTypePropertyId, VARIANT::from(t.0)).map_err(fail)?).map_err(fail)?);
        }
        // Landmarks: Windows 10 1703 and later. Without them, no link of a page's menu.
        if let Ok(nav) = condition(UIA_LandmarkTypePropertyId, VARIANT::from(UIA_NavigationLandmarkTypeId.0)) {
            filtre = Some(ou(&auto, filtre, nav).map_err(fail)?);
        }
        // SAFETY: requests of ours, filled before use.
        let (vue, leurs_cadres) = unsafe {
            let r = auto.CreateCacheRequest().map_err(fail)?;
            for p in [
                UIA_ControlTypePropertyId,
                UIA_NamePropertyId,
                UIA_BoundingRectanglePropertyId,
                UIA_IsOffscreenPropertyId,
                UIA_IsEnabledPropertyId,
                UIA_ValueIsReadOnlyPropertyId,
                UIA_WindowIsModalPropertyId,
            ] {
                r.AddProperty(p).map_err(fail)?;
            }
            let _ = r.AddProperty(UIA_LandmarkTypePropertyId);
            r.SetTreeScope(TreeScope(TreeScope_Element.0 | TreeScope_Children.0)).map_err(fail)?;
            r.SetAutomationElementMode(AutomationElementMode_Full).map_err(fail)?;
            r.SetTreeFilter(filtre.as_ref().ok_or(Rate::Indisponible)?).map_err(fail)?;
            let c = auto.CreateCacheRequest().map_err(fail)?;
            for p in [UIA_BoundingRectanglePropertyId, UIA_ControlTypePropertyId, UIA_IsPasswordPropertyId] {
                c.AddProperty(p).map_err(fail)?;
            }
            c.SetAutomationElementMode(AutomationElementMode_None).map_err(fail)?;
            (r, c)
        };
        let mots_de_passe = condition(UIA_IsPasswordPropertyId, VARIANT::from(true)).map_err(fail)?;
        let pages = condition(UIA_ControlTypePropertyId, VARIANT::from(UIA_DocumentControlTypeId.0)).map_err(fail)?;
        let cherches = ou(&auto, Some(mots_de_passe), pages).map_err(fail)?;
        Ok(Lecteur { auto, vue, cherches, leurs_cadres, _com: com })
    }

    /// The level under `el`, then under each of its children that is neither a command nor data.
    fn parcourir(&self, el: &IUIAutomationElement, ctx: Contexte, profondeur: usize, p: &mut Parcours) {
        if profondeur > PROFONDEUR_MAX {
            return;
        }
        for c in enfants(el) {
            // SAFETY: cached properties of an element of ours.
            let t = unsafe { c.CachedControlType() }.unwrap_or_default();
            let repere = unsafe { c.GetCachedPropertyValue(UIA_LandmarkTypePropertyId) }.ok().and_then(|v| i32::try_from(&v).ok()).unwrap_or(0);
            let s = sorte(t, repere);
            if let Sorte::Commande(g) = s {
                p.commande(&c, g, ctx);
            }
            if descendre(s) {
                // SAFETY: an element of ours, its next level asked for.
                if let Ok(c) = unsafe { c.BuildUpdatedCache(&self.vue) } {
                    self.parcourir(&c, ctx.sous(s), profondeur + 1, p);
                }
            }
        }
    }

    /// The commands of `h`, its dialogs and its menus on its screen, the window on top first, and the
    /// dialogs that wait on a choice wherever they are. In physical pixels: the caller has entered them.
    fn arbre(&self, h: HWND, navigateur: bool, cachees: &[Cadre]) -> Result<Liste, Rate> {
        let ecran = ecran_de(h).ok_or(Rate::Introuvable)?;
        let mut liste = Liste { attend: !active(h), ..Liste::default() };
        for w in famille(h) {
            let Some(f) = frame(w).map(cadre) else { continue };
            // A window that waits on a dialog: nothing of it can be clicked.
            if !active(w) {
                continue;
            }
            let attend = owner(w).is_some_and(|o| !active(o));
            // On another screen, nothing of it is listed; a dialog there waits on the person all the same.
            if !f.touche(&ecran) && !dialogue(&class(w), attend, false) {
                continue;
            }
            // SAFETY: a window handle; its first level comes back cached.
            let Ok(racine) = (unsafe { self.auto.ElementFromHandleBuildCache(w, &self.vue) }) else { continue };
            let mut p = Parcours { hwnd: w, fenetre: f, ecran, navigateur, cachees, elements: Vec::new(), toutes: Vec::new() };
            self.parcourir(&racine, Contexte::default(), 0, &mut p);
            // SAFETY: a cached property of an element of ours.
            let modale = unsafe { racine.GetCachedPropertyValue(UIA_WindowIsModalPropertyId) }.ok().and_then(|v| bool::try_from(&v).ok()).unwrap_or(false);
            if attend_un_choix(dialogue(&class(w), attend, modale), &p.toutes) {
                p.elements.iter_mut().for_each(|e| e.choix = true);
                liste.choix.push(f);
            }
            liste.elements.extend(p.elements);
        }
        liste.elements.truncate(ELEMENTS_MAX);
        Ok(liste)
    }

    /// The boxes of a window's password fields: one search of its whole tree, done by its own process,
    /// whatever lies above them (a table, a list, a window waiting on a dialog), whatever their type.
    /// None: not known, the tree did not answer, or a web page's is not built yet.
    fn champs(&self, w: HWND) -> Option<Vec<Cadre>> {
        // SAFETY: a window handle, a condition and a request of ours; what is found comes back cached.
        unsafe {
            let racine = self.auto.ElementFromHandle(w).ok()?;
            let trouves = racine.FindAllBuildCache(TreeScope_Descendants, &self.cherches, &self.leurs_cadres).ok()?;
            let trouves: Vec<IUIAutomationElement> = (0..trouves.Length().ok()?).filter_map(|i| trouves.GetElement(i).ok()).collect();
            if !trouves.iter().any(|e| e.CachedControlType().is_ok_and(|t| t == UIA_DocumentControlTypeId)) && montre_une_page(w) {
                return None;
            }
            Some(trouves.iter().filter(|e| e.CachedIsPassword().is_ok_and(|b| b.as_bool())).filter_map(|e| e.CachedBoundingRectangle().ok()).map(cadre).collect())
        }
    }
}

fn ou(auto: &IUIAutomation, avant: Option<IUIAutomationCondition>, c: IUIAutomationCondition) -> windows::core::Result<IUIAutomationCondition> {
    match avant {
        None => Ok(c),
        // SAFETY: two conditions of ours.
        Some(a) => unsafe { auto.CreateOrCondition(&a, &c) },
    }
}

fn enfants(el: &IUIAutomationElement) -> Vec<IUIAutomationElement> {
    // SAFETY: the children the request cached.
    let Ok(all) = (unsafe { el.GetCachedChildren() }) else { return Vec::new() };
    (0..unsafe { all.Length() }.unwrap_or(0)).filter_map(|i| unsafe { all.GetElement(i) }.ok()).collect()
}

/// One window's walk.
struct Parcours<'a> {
    hwnd: HWND,
    fenetre: Cadre,
    ecran: Cadre,
    navigateur: bool,
    cachees: &'a [Cadre],
    elements: Vec<Element>,
    /// What the guard of the dialog counts, unnamed (`compte`).
    toutes: Vec<Element>,
}

impl Parcours<'_> {
    fn commande(&mut self, el: &IUIAutomationElement, genre: Genre, ctx: Contexte) {
        // SAFETY: cached properties of an element of ours.
        let c = cadre(unsafe { el.CachedBoundingRectangle() }.unwrap_or_default());
        let barre_titre = ctx.titre;
        let hors_vue = unsafe { el.CachedIsOffscreen() }.map_or(true, |b| b.as_bool());
        let actif = unsafe { el.CachedIsEnabled() }.is_ok_and(|b| b.as_bool());
        // Only whether one can type in it, never its value.
        let lecture_seule = unsafe { el.GetCachedPropertyValue(UIA_ValueIsReadOnlyPropertyId) }.ok().and_then(|v| bool::try_from(&v).ok()).unwrap_or(false);
        let element = |nom: String, coupe: bool| Element { genre, nom, coupe, cadre: c, barre_titre, choix: false };
        if compte(genre, hors_vue, actif, lecture_seule) {
            self.toutes.push(element(String::new(), false));
        }
        if garde(genre, ctx, self.navigateur) && visible(&c, &self.fenetre, &self.ecran, hors_vue, actif, self.cachees) && montre(self.hwnd, &c) {
            let brut = unsafe { el.CachedName() }.map(|b| b.to_string()).unwrap_or_default();
            self.elements.push(element(court(&brut), coupe(&brut)));
        }
    }
}

/// Whether the window shows at the middle of the box: another window over it (another program's, a
/// browser's bubble, Bibo's pill) hides the command. A window the clicks go through (Bibo's ring)
/// hides nothing.
fn montre(w: HWND, c: &Cadre) -> bool {
    let p = POINT { x: c.x + c.largeur / 2, y: c.y + c.hauteur / 2 };
    // SAFETY: plain values; no window there is a null handle, never `w`.
    let racine = unsafe { GetAncestor(WindowFromPoint(p), GA_ROOT) };
    racine == w
}

/// The desktop's windows, the one on top first.
fn en_ordre() -> Vec<HWND> {
    unsafe extern "system" fn each(h: HWND, all: LPARAM) -> windows::core::BOOL {
        // SAFETY: the Vec lent below, alive for the whole call.
        unsafe { (*(all.0 as *mut Vec<HWND>)).push(h) };
        windows::core::BOOL(1)
    }
    let mut all: Vec<HWND> = Vec::new();
    // SAFETY: `each` only pushes to `all`, which outlives the call.
    let _ = unsafe { EnumWindows(Some(each), LPARAM(&mut all as *mut Vec<HWND> as isize)) };
    all
}

/// Shown on the desktop: not hidden, minimized, nor cloaked (another virtual desktop, a suspended app).
fn en_vue(h: HWND) -> bool {
    shown(h) && !minimized(h) && !cloaked(h)
}

/// A window that shows a web page (a browser, an Electron app, a WebView2): Chromium builds its tree
/// at the first question, so that tree without its page is not read yet (P0: Edge's first list empty).
fn montre_une_page(w: HWND) -> bool {
    unsafe extern "system" fn each(h: HWND, trouve: LPARAM) -> windows::core::BOOL {
        let page = class(h) == "Chrome_RenderWidgetHostHWND" && shown(h);
        // SAFETY: the bool lent below, alive for the whole call.
        unsafe { *(trouve.0 as *mut bool) |= page };
        windows::core::BOOL(i32::from(!page))
    }
    let mut trouve = false;
    // SAFETY: `each` only writes `trouve`, which outlives the call.
    let _ = unsafe { EnumChildWindows(Some(w), Some(each), LPARAM(&mut trouve as *mut bool as isize)) };
    trouve
}

/// The end of a window's chain of owners. By `GW_OWNER`: `GA_ROOTOWNER` stops at a dialog that is no
/// popup (a WinForms one), and a program may hang all its windows on a hidden one (Delphi's).
fn racine_de(h: HWND) -> HWND {
    let mut r = h;
    for _ in 0..32 {
        match owner(r) {
            Some(o) => r = o,
            None => break,
        }
    }
    r
}

fn fil_de(h: HWND) -> u32 {
    // SAFETY: any value is accepted.
    unsafe { GetWindowThreadProcessId(h, None) }
}

fn coquille(h: HWND) -> bool {
    SHELL.contains(&class(h).as_str())
}

/// A menu of a thread: the system's (`#32768`), or a toolkit's popup without a title bar, owned by
/// none or by a hidden window (WinForms hangs its menus on one).
fn menu(w: HWND) -> bool {
    // SAFETY: any value is accepted.
    let style = unsafe { GetWindowLongW(w, GWL_STYLE) } as u32;
    let popup = style & WS_POPUP.0 != 0 && style & WS_CAPTION.0 != WS_CAPTION.0;
    class(w) == "#32768" || (popup && (racine_de(w) == w || !en_vue(racine_de(w))))
}

/// The windows of what the person works in, the one on top first: `h`, the windows hung on the same
/// owner as it (its dialogs, its palettes), and the menus its thread opens. Not its program's other
/// windows: Edge's other window is another site. Never the shell's: Explorer's window may live in
/// the taskbar's process, the shell's Run box hangs on it, and the taskbar names every window open
/// and its notification area says what each program says.
fn famille(h: HWND) -> Vec<HWND> {
    let (racine, fil) = (racine_de(h), fil_de(h));
    let racine = (!coquille(racine)).then_some(racine);
    en_ordre().into_iter().filter(|&w| en_vue(w) && (w == h || (!coquille(w) && (racine.is_some_and(|r| racine_de(w) == r) || (fil_de(w) == fil && menu(w)))))).collect()
}

fn ecran_de(h: HWND) -> Option<Cadre> {
    let mut info = MONITORINFO { cbSize: size_of::<MONITORINFO>() as u32, ..Default::default() };
    // SAFETY: the screen nearest the window, its info into ours, its size given.
    unsafe { GetMonitorInfoW(MonitorFromWindow(h, MONITOR_DEFAULTTONEAREST), &mut info) }.as_bool().then(|| cadre(info.rcMonitor))
}

fn active(h: HWND) -> bool {
    // SAFETY: any value is accepted.
    unsafe { IsWindowEnabled(h) }.as_bool()
}

/// The window in front, Bibo's left out; else the top one of another program.
fn devant() -> Option<Window> {
    foreground().or_else(|| list().into_iter().next()).map(|f| f.window)
}

impl Lire for Lecteur {
    fn lire(&mut self, cible: Option<&Window>, cachees: &[Cadre]) -> Result<Liste, Rate> {
        let _physical = Physical::enter();
        let cible = match cible {
            Some(w) => w.clone(),
            None => devant().ok_or(Rate::Introuvable)?,
        };
        if cible.pid == std::process::id() {
            return Err(Rate::Introuvable);
        }
        let h = handle(cible.id);
        tant_que(
            || same_window(&cible, identity(h).as_ref()),
            || {
                if minimized(h) || !shown(h) {
                    return Err(Rate::Reduite);
                }
                // A remote desktop's windows show another computer: whatever its tree says, nothing (C10).
                if distant(&cible.app) {
                    return Ok(Liste::default());
                }
                self.arbre(h, navigateur(&cible.app), cachees)
            },
        )
    }

    fn secrets(&mut self, ecran: &Cadre) -> Result<Secrets, Rate> {
        let _physical = Physical::enter();
        let mut s = Secrets::default();
        for w in en_ordre() {
            // Bibo's own windows: blacked whole by the capture.
            if !en_vue(w) || pid_of(w) == std::process::id() {
                continue;
            }
            let Some(f) = frame(w).map(cadre).filter(|f| f.touche(ecran)) else { continue };
            // A remote desktop shows another computer's fields; a tree that does not answer or a page not
            // built yet, none known.
            let lu = match identity(w) {
                Some(i) if !distant(&i.app) => self.champs(w),
                _ => None,
            };
            match lu {
                Some(champs) => s.champs.extend(champs.into_iter().filter(|c| c.touche(&f) && c.touche(ecran))),
                None => s.opaques.push(f),
            }
        }
        Ok(s)
    }
}

#[cfg(test)]
mod tests {
    use super::super::uia::{designe, elements, fermer, lignes, ouvrir, repli_pixels, secrets, zones_secretes, NOM_MAX};
    use super::super::win::title;
    use super::*;
    use crate::capture::bureau::*;
    use crate::capture::win::Canvas;
    use crate::capture::{from_bgra, Found};
    use crate::shot::Rgba;
    use std::path::{Path, PathBuf};
    use std::process::Command;
    use std::time::{Duration, Instant};
    use windows::Win32::Foundation::{POINT, WPARAM};
    use windows::Win32::Graphics::Gdi::{BitBlt, GetDC, MonitorFromPoint, ReleaseDC, MONITOR_DEFAULTTOPRIMARY, SRCCOPY};
    use windows::Win32::UI::HiDpi::{GetDpiForMonitor, MDT_EFFECTIVE_DPI};
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, INPUT_MOUSE, KEYBDINPUT, KEYBD_EVENT_FLAGS, KEYEVENTF_KEYUP, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEINPUT,
        MOUSE_EVENT_FLAGS, VIRTUAL_KEY, VK_ESCAPE, VK_LWIN, VK_MENU, VK_R, VK_SPACE,
    };
    use windows::Win32::UI::WindowsAndMessaging::{PostMessageW, SetCursorPos, WM_CLOSE};

    #[test]
    fn each_control_type_to_the_rules() {
        assert_eq!(sorte(UIA_ButtonControlTypeId, 0), Sorte::Commande(Genre::Bouton));
        assert_eq!(sorte(UIA_EditControlTypeId, 0), Sorte::Commande(Genre::Champ));
        assert_eq!(sorte(UIA_HyperlinkControlTypeId, 0), Sorte::Commande(Genre::Lien));
        for t in [UIA_DataGridControlTypeId, UIA_TableControlTypeId, UIA_ListControlTypeId, UIA_ListItemControlTypeId, UIA_DataItemControlTypeId, UIA_TreeItemControlTypeId] {
            assert_eq!(sorte(t, 0), Sorte::Donnees, "{t:?}: data");
        }
        for t in [UIA_TextControlTypeId, UIA_HeaderItemControlTypeId, UIA_ImageControlTypeId, UIA_ListItemControlTypeId] {
            assert!(!matches!(sorte(t, 0), Sorte::Commande(_)), "{t:?}: never a command");
        }
        assert_eq!(sorte(UIA_GroupControlTypeId, UIA_NavigationLandmarkTypeId.0), Sorte::Barre, "a page's <nav>");
        assert_eq!(sorte(UIA_GroupControlTypeId, 0), Sorte::Autre);
        assert_eq!(sorte(UIA_ToolBarControlTypeId, 0), Sorte::Barre);
        assert_eq!(sorte(UIA_DocumentControlTypeId, 0), Sorte::Page);
        assert_eq!(sorte(UIA_TitleBarControlTypeId, 0), Sorte::BarreTitre);
    }

    // ── On a Windows desktop only, a VM of the CI's: `cargo test --lib -- --ignored uia_ --test-threads=1`.
    // Windows of an ERP-like program (tests/fixtures/guide/atelier_uia.cs, compiled there by Windows' own
    // csc) and an Odoo-like page in Edge (tests/fixtures/guide/odoo.html), made-up customers only.

    const RED: [u8; 3] = [0xe5, 0x39, 0x35];
    const PURPLE: [u8; 3] = [0x8e, 0x24, 0xaa];
    /// The Odoo-like page's two other password fields: in a list's row, in a table's cell.
    const CYAN: [u8; 3] = [0x00, 0xac, 0xc1];
    const LIME: [u8; 3] = [0xc0, 0xca, 0x33];
    /// The made-up customers of both fixtures: none may be in a list.
    const CLIENTS: [&str; 31] = [
        "Boulangerie Martin", "Garage Dupont", "Pharmacie Leroy", "Plomberie Moreau", "Cafe Lefebvre", "Fleuriste Garnier", "Librairie Fournier",
        "Menuiserie Girard", "Coiffure Bonnet", "Epicerie Lambert", "Optique Fontaine", "Traiteur Rousseau", "Cordonnerie Vincent", "Pressing Muller",
        "Quincaillerie Faure", "Boucherie Andre", "Fromagerie Mercier", "Auto-ecole Blanc", "Imprimerie Guerin", "Serrurerie Boyer", "Electricite Chevalier",
        "Toiture Francois", "Jardinerie Legrand", "Cave Gauthier", "Patisserie Perrin", "Bijouterie Robin", "Opticien Clement", "Tabac Morin",
        "Brasserie Nicolas", "Atelier Henry", "Vendeur",
    ];

    fn mesure(test: &str, what: &str, value: impl std::fmt::Display) {
        println!("MESURE {test} {what}={value}");
    }

    fn echelle() -> u32 {
        let _p = Physical::enter();
        let (mut x, mut y) = (0, 0);
        // SAFETY: plain values.
        unsafe { GetDpiForMonitor(MonitorFromPoint(POINT { x: 0, y: 0 }, MONITOR_DEFAULTTOPRIMARY), MDT_EFFECTIVE_DPI, &mut x, &mut y) }.unwrap();
        x * 100 / 96
    }

    fn fixture(name: &str) -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests").join("fixtures").join("guide").join(name)
    }

    fn csc(source: &str, dir: &Path) -> PathBuf {
        csc_avec(source, dir, &["System.Windows.Forms.dll", "System.Drawing.dll"])
    }

    /// Compiled against those assemblies of the .NET Framework (a path under its folder, or a name).
    fn csc_avec(source: &str, dir: &Path, refs: &[&str]) -> PathBuf {
        let windir = PathBuf::from(std::env::var("WINDIR").unwrap_or_else(|_| "C:\\Windows".into()));
        let framework = windir.join("Microsoft.NET").join("Framework64").join("v4.0.30319");
        let csc = framework.join("csc.exe");
        let exe = dir.join(source.replace(".cs", ".exe"));
        let out = Command::new(&csc)
            .args(["/nologo", "/target:winexe", "/optimize+"])
            .args(refs.iter().map(|r| format!("/r:{}", if r.contains('\\') { framework.join(r).display().to_string() } else { r.to_string() })))
            .arg(format!("/out:{}", exe.display()))
            .arg(fixture(source))
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

    fn cadre_de(f: &Found) -> Cadre {
        let _p = Physical::enter();
        cadre(frame(handle(f.window.id)).unwrap())
    }

    /// A click as a hand makes it, at a point of the screen in physical pixels.
    fn clic(x: i32, y: i32) {
        let _p = Physical::enter();
        // SAFETY: plain values; the inputs live for the call.
        unsafe { SetCursorPos(x, y) }.unwrap();
        let input = |flags: MOUSE_EVENT_FLAGS| INPUT { r#type: INPUT_MOUSE, Anonymous: INPUT_0 { mi: MOUSEINPUT { dwFlags: flags, ..Default::default() } } };
        let sent = unsafe { SendInput(&[input(MOUSEEVENTF_LEFTDOWN), input(MOUSEEVENTF_LEFTUP)], size_of::<INPUT>() as i32) };
        assert_eq!(sent, 2, "SendInput");
    }

    /// Keys as a hand presses them: each down in turn, then up the other way round.
    fn touches(keys: &[VIRTUAL_KEY]) {
        let key = |k: VIRTUAL_KEY, f: KEYBD_EVENT_FLAGS| INPUT { r#type: INPUT_KEYBOARD, Anonymous: INPUT_0 { ki: KEYBDINPUT { wVk: k, dwFlags: f, ..Default::default() } } };
        let inputs: Vec<INPUT> = keys.iter().map(|&k| key(k, KEYBD_EVENT_FLAGS(0))).chain(keys.iter().rev().map(|&k| key(k, KEYEVENTF_KEYUP))).collect();
        // SAFETY: the inputs live for the call.
        let sent = unsafe { SendInput(&inputs, size_of::<INPUT>() as i32) };
        assert_eq!(sent as usize, inputs.len(), "SendInput");
        std::thread::sleep(Duration::from_millis(300));
    }

    /// The window brought in front by a click on its title bar.
    fn devant_par_clic(f: &Found) {
        let c = cadre_de(f);
        clic(c.x + c.largeur / 2, c.y + 8);
        std::thread::sleep(Duration::from_millis(300));
    }

    /// The test's own check of what the screen shows there (never Bibo's capture, which is not this
    /// PR's), in physical pixels.
    fn ecran(c: Cadre) -> Rgba {
        let _p = Physical::enter();
        let canvas = Canvas::new(c.largeur, c.hauteur).unwrap();
        // SAFETY: the screen's DC, released below; drawn into our bitmap.
        unsafe {
            let screen = GetDC(None);
            BitBlt(canvas.dc, 0, 0, c.largeur, c.hauteur, Some(screen), c.x, c.y, SRCCOPY).unwrap();
            ReleaseDC(None, screen);
        }
        from_bgra(canvas.pixels(), c.largeur as u32, c.hauteur as u32, (0, 0, c.largeur, c.hauteur))
    }

    fn nomme<'a>(l: &'a Liste, genre: Genre, nom: &str) -> Option<&'a Element> {
        l.elements.iter().find(|e| e.genre == genre && e.nom == nom)
    }

    fn noms(l: &Liste) -> Vec<String> {
        l.elements.iter().map(|e| format!("{} {}", e.genre.mot(), e.nom)).collect()
    }

    /// Customers found in the list's names, case ignored.
    fn fuites(l: &Liste) -> Vec<String> {
        l.elements.iter().filter(|e| CLIENTS.iter().any(|c| e.nom.to_lowercase().contains(&c.to_lowercase()))).map(|e| e.nom.clone()).collect()
    }

    fn jusqu_a(test: &str, w: &Window, attente: Duration, done: impl Fn(&Liste) -> bool) -> Liste {
        let start = Instant::now();
        loop {
            let l = elements(w, &[]);
            if let Ok(l) = &l {
                if done(l) {
                    mesure(test, "s_jusqu_a_la_liste", format!("{:.1}", start.elapsed().as_secs_f64()));
                    return l.clone();
                }
            }
            if start.elapsed() > attente {
                let f = frame(handle(w.id)).map(cadre);
                panic!("{test}: never there; last: {:?}; over the window: {:?}", l.map(|l| noms(&l)), f.map(|f| dessus(&f)));
            }
            std::thread::sleep(Duration::from_millis(250));
        }
    }

    /// p50, p90 and max of 20 lists in a row: the p90.
    fn latence(test: &str, w: &Window) -> f64 {
        let mut ms: Vec<f64> = (0..20)
            .map(|_| {
                let t = Instant::now();
                elements(w, &[]).unwrap();
                t.elapsed().as_secs_f64() * 1000.0
            })
            .collect();
        ms.sort_by(|a, b| a.partial_cmp(b).unwrap());
        mesure(test, "ms_p50", format!("{:.1}", ms[9]));
        mesure(test, "ms_p90", format!("{:.1}", ms[17]));
        mesure(test, "ms_max", format!("{:.1}", ms[19]));
        ms[17]
    }

    /// The windows over a box of the screen, the one on top first: said when a colour is not found
    /// where its element is.
    fn dessus(c: &Cadre) -> Vec<String> {
        let _p = Physical::enter();
        en_ordre()
            .into_iter()
            .filter(|&h| shown(h) && !cloaked(h))
            .filter_map(|h| Some((h, frame(h).map(cadre)?)))
            .filter(|(_, f)| f.touche(c))
            .map(|(h, f)| format!("{} {:?} (pid {}) [{},{},{},{}]", class(h), court(&title(h)), pid_of(h), f.x, f.y, f.largeur, f.hauteur))
            .collect()
    }

    /// The box of a colour on the screen against the element's box: the largest gap of the four sides.
    fn ecart(test: &str, img: &Rgba, origine: (i32, i32), couleur: [u8; 3], nom: &str, c: &Cadre) -> i32 {
        let (l, t, r, b) = bounds(img, couleur).unwrap_or_else(|| panic!("{test}: no pixel of {couleur:?} for {nom}; over it: {:?}", dessus(c)));
        let (x, y) = (c.x - origine.0, c.y - origine.1);
        let g = [(x - l).abs(), (y - t).abs(), (x + c.largeur - r).abs(), (y + c.hauteur - b).abs()].into_iter().max().unwrap();
        mesure(test, &format!("ecart_px[{nom}]"), format!("{g} (ecran {l},{t},{r},{b} ; uia {x},{y},{},{})", x + c.largeur, y + c.hauteur));
        g
    }

    fn imprime(test: &str, l: &Liste, origine: (i32, i32)) {
        mesure(test, "elements", l.elements.len());
        for line in lignes(l, origine, 1.0) {
            println!("LISTE {test} {line}");
        }
    }

    /// The screen a window is on, in physical pixels.
    fn ecran_de_la(w: &Window) -> Cadre {
        let _p = Physical::enter();
        ecran_de(handle(w.id)).unwrap()
    }

    /// The windows over or at a box, said: what an opaque window is.
    fn quoi(c: &[Cadre]) -> Vec<Vec<String>> {
        c.iter().map(dessus).collect()
    }

    /// The opaque windows that are not a remote desktop's (at 100 %, the job's RemoteApp is on the screen).
    fn inconnues(s: &Secrets) -> Vec<Vec<String>> {
        let _p = Physical::enter();
        let distants: Vec<Cadre> = en_ordre().into_iter().filter(|&h| en_vue(h) && identity(h).is_some_and(|i| distant(&i.app))).filter_map(|h| frame(h).map(cadre)).collect();
        quoi(&s.opaques.iter().filter(|c| !distants.contains(c)).copied().collect::<Vec<_>>())
    }

    /// The password fields of the screen, read once more until `done`, and measured: p50, p90 and max of
    /// 10 reads in a row.
    fn secrets_jusqu_a(test: &str, ecran: &Cadre, done: impl Fn(&Secrets) -> bool) -> Secrets {
        let start = Instant::now();
        let s = loop {
            let s = secrets(ecran);
            if let Ok(s) = &s {
                if done(s) {
                    break s.clone();
                }
            }
            assert!(start.elapsed() < WAIT, "{test}: the password fields never as expected; last: {s:?}");
            std::thread::sleep(Duration::from_millis(250));
        };
        let mut ms: Vec<f64> = (0..10)
            .map(|_| {
                let t = Instant::now();
                secrets(ecran).unwrap();
                t.elapsed().as_secs_f64() * 1000.0
            })
            .collect();
        ms.sort_by(|a, b| a.partial_cmp(b).unwrap());
        mesure(test, "secrets_ms_p50", format!("{:.1}", ms[4]));
        mesure(test, "secrets_ms_p90", format!("{:.1}", ms[8]));
        mesure(test, "secrets_ms_max", format!("{:.1}", ms[9]));
        mesure(test, "secrets", format!("{} champs, opaques {:?}", s.champs.len(), quoi(&s.opaques)));
        s
    }

    /// Each colour's box against the password field over it, the largest gap; then all blacked: none of
    /// the colour left.
    fn noircis(test: &str, img: &mut Rgba, origine: (i32, i32), s: &Secrets, couleurs: &[[u8; 3]]) -> i32 {
        let mut pire = 0;
        for &couleur in couleurs {
            let (l, t, r, b) = bounds(img, couleur).unwrap_or_else(|| panic!("{test}: no pixel of {couleur:?}"));
            let vu = Cadre { x: origine.0 + l, y: origine.1 + t, largeur: r - l, hauteur: b - t };
            let c = s.champs.iter().find(|c| c.touche(&vu)).unwrap_or_else(|| panic!("{test}: no password field over {couleur:?} at {vu:?}: {:?}", s.champs));
            pire = pire.max(ecart(test, img, origine, couleur, &format!("mot de passe {couleur:?}"), c));
        }
        let avant: Vec<usize> = couleurs.iter().map(|&c| count(img, c)).collect();
        crate::shot::paint(img, &zones_secretes(&s.a_noircir(), origine));
        let apres: Vec<usize> = couleurs.iter().map(|&c| count(img, c)).collect();
        mesure(test, "mots_de_passe_avant_apres", format!("{avant:?} -> {apres:?}"));
        assert!(avant.iter().all(|&n| n > 300) && apres.iter().all(|&n| n == 0), "{test}: the password fields blacked: {avant:?} -> {apres:?}");
        pire
    }

    /// The ERP-like window as a program unaware of the scale, aware of it, or per monitor: its commands
    /// and their boxes against the screen, the password field given and blacked, no customer, names
    /// cut, a link out of a bar and a disabled button left out, a hidden zone.
    fn atelier(dpi: &str) {
        let test = format!("atelier_{dpi}_{}", echelle());
        let dir = tempfile::tempdir().unwrap();
        let exe = csc("atelier_uia.cs", dir.path());
        let titre = format!("Atelier UIA {dpi} {}", std::process::id());
        let _p = Program(Command::new(&exe).args(["erp", titre.as_str(), "60", "60", "900", "640", dpi]).spawn().unwrap());
        let win = listed_as(|f| f.titre == titre);
        devant_par_clic(&win);
        fermer();
        let t = Instant::now();
        ouvrir();
        let premier = elements(&win.window, &[]);
        mesure(&test, "ms_premier_appel", format!("{:.1} ({} elements)", t.elapsed().as_secs_f64() * 1000.0, premier.map_or(0, |l| l.elements.len())));
        let l = jusqu_a(&test, &win.window, WAIT, |l| nomme(l, Genre::Bouton, "Nouveau").is_some());
        let p90 = latence(&test, &win.window);

        let f = cadre_de(&win);
        let origine = (f.x, f.y);
        let mut img = ecran(f);
        imprime(&test, &l, origine);
        let mut pire = 0;
        for (couleur, nom) in [(GREEN, "Nouveau"), (PURPLE, "Enregistrer"), (RED, "Supprimer")] {
            let e = nomme(&l, Genre::Bouton, nom).unwrap_or_else(|| panic!("{test}: {nom}"));
            pire = pire.max(ecart(&test, &img, origine, couleur, nom, &e.cadre));
        }
        let s = secrets_jusqu_a(&test, &ecran_de_la(&win.window), |s| s.champs.len() == 1);
        pire = pire.max(noircis(&test, &mut img, origine, &s, &[ORANGE]));
        mesure(&test, "ecart_max_px", pire);
        mesure(&test, "noms_clients", format!("{:?}", fuites(&l)));
        let supprimer = nomme(&l, Genre::Bouton, "Supprimer").unwrap().cadre;
        let cache = elements(&win.window, &[supprimer]).unwrap();

        assert!(fuites(&l).is_empty(), "{test}: no customer");
        for (genre, nom) in [
            (Genre::Menu, "Fichier"),
            (Genre::Menu, "Edition"),
            (Genre::Onglet, "Factures"),
            (Genre::Onglet, "Clients"),
            (Genre::Case, "Payee"),
            (Genre::BoutonRadio, "HT"),
            (Genre::BoutonRadio, "TTC"),
            (Genre::Bouton, "Exporter toutes les factures du trimestr"),
        ] {
            assert!(nomme(&l, genre, nom).is_some(), "{test}: {} {nom} listed: {:?}", genre.mot(), noms(&l));
        }
        assert!(l.elements.iter().all(|e| e.nom.chars().count() <= NOM_MAX), "{test}: names cut");
        assert!(l.elements.iter().all(|e| e.nom != "Ouvrir la fiche"), "{test}: a link out of a bar");
        assert!(nomme(&l, Genre::Bouton, "Archiver").is_none(), "{test}: a disabled button");
        assert!(l.elements.iter().all(|e| e.nom != "Start" && e.nom != "Démarrer"), "{test}: nothing of another program");
        assert!(l.elements.iter().filter(|e| e.barre_titre).count() >= 2, "{test}: the title bar's buttons, marked");
        assert!(!repli_pixels(&l) && l.choix.is_empty() && !l.attend, "{test}: a tree, no dialog");
        assert!(inconnues(&s).is_empty(), "{test}: every window's fields known: {:?}", inconnues(&s));
        let numero = |nom: &str| l.elements.iter().position(|e| e.nom == nom).unwrap() + 1;
        assert!(designe(&l, numero("Nouveau"), "Nouveau").is_some());
        assert!(designe(&l, numero("Nouveau"), "Nouveau devis").is_none(), "{test}: a short name was not cut");
        let exporter = "Exporter toutes les factures du trimestre vers le cabinet comptable";
        assert!(designe(&l, numero("Exporter toutes les factures du trimestr"), exporter).is_some(), "{test}: a name cut, the label going on");
        assert!(nomme(&cache, Genre::Bouton, "Supprimer").is_none() && nomme(&cache, Genre::Bouton, "Nouveau").is_some(), "{test}: the hidden zone");
        assert!(pire <= 4, "{test}: boxes within 4 px of the screen ({pire})");
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

    /// A window of another program over a button, always on top and never active, as Bibo's pill: that
    /// button is not listed, the others are; the window gone, it is back.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_couvert() {
        let test = format!("couvert_{}", echelle());
        let dir = tempfile::tempdir().unwrap();
        let exe = csc("atelier_uia.cs", dir.path());
        let titre = format!("Atelier couvert {}", std::process::id());
        let _p = Program(Command::new(&exe).args(["erp", titre.as_str(), "60", "60", "900", "640", "pmv2"]).spawn().unwrap());
        let win = listed_as(|f| f.titre == titre);
        ouvrir();
        let l = jusqu_a(&test, &win.window, WAIT, |l| nomme(l, Genre::Bouton, "Supprimer").is_some());
        let s = nomme(&l, Genre::Bouton, "Supprimer").unwrap().cadre;
        let milieu = Cadre { x: s.x + s.largeur / 2, y: s.y + s.hauteur / 2, largeur: 1, hauteur: 1 };

        let toile = csc("atelier.cs", dir.path());
        let pastille = format!("Pastille {}", std::process::id());
        let (x, y) = ((milieu.x - 60).to_string(), (milieu.y - 30).to_string());
        let d = Program(Command::new(&toile).args(["dessus", pastille.as_str(), x.as_str(), y.as_str(), "120", "60", "1e88e5"]).spawn().unwrap());
        let over = listed_as(|f| f.titre == pastille);
        assert!(cadre_de(&over).touche(&milieu), "{test}: the window lies over Supprimer's middle");
        let l = jusqu_a(&test, &win.window, WAIT, |l| nomme(l, Genre::Bouton, "Supprimer").is_none());
        imprime(&test, &l, (0, 0));
        for nom in ["Nouveau", "Enregistrer"] {
            assert!(nomme(&l, Genre::Bouton, nom).is_some(), "{test}: {nom}, not covered: {:?}", noms(&l));
        }
        drop(d);
        jusqu_a(&test, &win.window, WAIT, |l| nomme(l, Genre::Bouton, "Supprimer").is_some());
    }

    /// A window that draws itself without a control (a canvas, a program without accessibility):
    /// nothing but its title bar's buttons, so pixels. Closed, it is not found (R22).
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_sans_arbre() {
        let test = format!("sans_arbre_{}", echelle());
        let dir = tempfile::tempdir().unwrap();
        let exe = csc("atelier.cs", dir.path());
        let titre = format!("Atelier toile {}", std::process::id());
        let p = Program(Command::new(&exe).args(["fenetre", titre.as_str(), "80", "80", "640", "480", "1e88e5"]).spawn().unwrap());
        let win = listed_as(|f| f.titre == titre);
        ouvrir();
        let l = jusqu_a(&test, &win.window, WAIT, |l| !l.elements.is_empty());
        imprime(&test, &l, (0, 0));
        assert!(repli_pixels(&l), "{test}: pixels");
        drop(p);
        until("closed", || identity(handle(win.window.id)).is_none());
        assert_eq!(elements(&win.window, &[]), Err(Rate::Introuvable), "{test}: a window closed");
    }

    fn edge_ouvre(test: &str, dir: &Path, profil: &str) -> (Program, Found) {
        let page = dir.join("odoo.html");
        std::fs::copy(fixture("odoo.html"), &page).unwrap();
        let mail = dir.join("mail.html");
        std::fs::write(&mail, "<!doctype html><meta charset=utf-8><title>Boite de reception - Garage Dupont</title><p>Courrier</p>").unwrap();
        let url = |p: &Path| format!("file:///{}", p.display().to_string().replace('\\', "/"));
        let profile = format!("--user-data-dir={}", dir.join(profil).display());
        let flags = ["--no-first-run", "--no-default-browser-check", "--disable-sync", "--window-position=40,40", "--window-size=1280,700"];
        let browser = Program(Command::new(edge()).arg(&profile).args(flags).arg(url(&mail)).spawn().unwrap());
        listed_as(|f| f.window.pid == browser.0.id());
        let _second = Program(Command::new(edge()).arg(&profile).args(flags).arg(url(&page)).spawn().unwrap());
        let win = listed_as(|f| f.window.pid == browser.0.id() && f.titre.contains("Devis - Odoo"));
        assert!(navigateur(&win.window.app), "{test}: {}", win.window.app);
        (browser, win)
    }

    /// The Odoo-like page in Edge, a tab of mail behind it: the page's commands and their boxes, its
    /// menu's links and a toolbar's, the password field given and blacked; no customer of the table,
    /// nor the one linked beside it; no other tab's title; nothing out of view; nothing too deep.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_edge_page() {
        let test = format!("edge_page_{}", echelle());
        let dir = tempfile::tempdir().unwrap();
        let (_b, win) = edge_ouvre(&test, dir.path(), "profil");
        devant_par_clic(&win);
        ouvrir();
        let l = jusqu_a(&test, &win.window, Duration::from_secs(60), |l| nomme(l, Genre::Bouton, "Nouveau").is_some() && l.elements.len() > 15);
        let p90 = latence(&test, &win.window);

        let f = cadre_de(&win);
        let origine = (f.x, f.y);
        let mut img = ecran(f);
        imprime(&test, &l, origine);
        let mut pire = 0;
        for (couleur, nom) in [(GREEN, "Nouveau"), (PURPLE, "Enregistrer"), (RED, "Supprimer")] {
            let e = nomme(&l, Genre::Bouton, nom).unwrap_or_else(|| panic!("{test}: {nom}"));
            pire = pire.max(ecart(&test, &img, origine, couleur, nom, &e.cadre));
        }
        // Its three: the panel's, a list's row's, a table's cell's (data the walk never enters).
        let s = secrets_jusqu_a(&test, &ecran_de_la(&win.window), |s| s.champs.len() == 3);
        pire = pire.max(noircis(&test, &mut img, origine, &s, &[ORANGE, CYAN, LIME]));
        mesure(&test, "ecart_max_px", pire);
        mesure(&test, "noms_clients", format!("{:?}", fuites(&l)));

        assert!(fuites(&l).is_empty(), "{test}: no customer, in the table or linked beside it");
        assert!(inconnues(&s).is_empty(), "{test}: every window's fields known: {:?}", inconnues(&s));
        for (genre, nom) in [
            (Genre::Lien, "Ventes"),
            (Genre::Lien, "Commandes"),
            (Genre::Lien, "Imprimer"),
            (Genre::Bouton, "Menu des applications"),
            (Genre::Bouton, "Planifier une activite"),
            (Genre::Bouton, "Suivant"),
            (Genre::Champ, "Rechercher"),
        ] {
            assert!(nomme(&l, genre, nom).is_some(), "{test}: {} {nom} listed: {:?}", genre.mot(), noms(&l));
        }
        assert!(l.elements.iter().all(|e| e.nom != "Conditions generales"), "{test}: a link out of a bar");
        assert!(l.elements.iter().all(|e| e.genre != Genre::Onglet && !e.nom.contains("Boite de reception")), "{test}: no tab");
        for nom in ["Bas de page", "Cache derriere", "Tres profond"] {
            assert!(l.elements.iter().all(|e| e.nom != nom), "{test}: {nom}");
        }
        assert!(l.elements.iter().all(|e| e.nom.chars().count() <= NOM_MAX), "{test}: names cut");
        assert!(pire <= 4, "{test}: boxes within 4 px of the screen ({pire})");
        assert!(p90 < 300.0, "{test}: p90 {p90:.1} ms");
    }

    /// Edge's first list is empty: the reader made and its first read done as the guide opens, the
    /// first list asked has the page, quickly.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_edge_chaud() {
        let test = format!("edge_chaud_{}", echelle());
        let froid = tempfile::tempdir().unwrap();
        {
            let (_b, win) = edge_ouvre(&test, froid.path(), "froid");
            devant_par_clic(&win);
            std::thread::sleep(Duration::from_secs(5));
            fermer();
            let t = Instant::now();
            ouvrir();
            let l = elements(&win.window, &[]).unwrap();
            mesure(&test, "sans_ouverture", format!("{:.1} ms, {} elements", t.elapsed().as_secs_f64() * 1000.0, l.elements.len()));
        }
        let dir = tempfile::tempdir().unwrap();
        let (_b, win) = edge_ouvre(&test, dir.path(), "chaud");
        devant_par_clic(&win);
        std::thread::sleep(Duration::from_secs(5));
        fermer();
        ouvrir();
        std::thread::sleep(Duration::from_secs(5));
        let t = Instant::now();
        let l = elements(&win.window, &[]).unwrap();
        let ms = t.elapsed().as_secs_f64() * 1000.0;
        mesure(&test, "apres_ouverture", format!("{ms:.1} ms, {} elements", l.elements.len()));
        assert!(nomme(&l, Genre::Bouton, "Nouveau").is_some(), "{test}: the page at the first list: {:?}", noms(&l));
        assert!(ms < 300.0, "{test}: {ms:.1} ms");
    }

    /// A dialog of the program open: the window it waits on gives nothing; its buttons are listed and
    /// said to wait on the person's choice, or not when it is a form.
    fn dialogue_de(mode: &str) -> (Liste, Window, Program) {
        let test = format!("dialogue_{mode}_{}", echelle());
        let dir = tempfile::tempdir().unwrap();
        let exe = csc("atelier_uia.cs", dir.path());
        let titre = format!("Atelier {mode} {}", std::process::id());
        let p = Program(Command::new(&exe).args([mode, titre.as_str(), "80", "80", "700", "500"]).spawn().unwrap());
        let win = listed_as(|f| f.titre == titre);
        ouvrir();
        let l = jusqu_a(&test, &win.window, WAIT, |l| l.elements.iter().filter(|e| e.genre == Genre::Bouton && !e.barre_titre).count() >= 2);
        imprime(&test, &l, (0, 0));
        mesure(&test, "choix", l.choix.len());
        assert!(nomme(&l, Genre::Bouton, "Nouveau").is_none(), "{test}: the window behind waits: {:?}", noms(&l));
        assert!(l.attend && !repli_pixels(&l), "{test}: the window asked waits on its dialog");
        // The window behind, disabled, has a password field: blacked all the same.
        let f = cadre_de(&win);
        let mut img = ecran(f);
        let s = secrets_jusqu_a(&test, &ecran_de_la(&win.window), |s| !s.champs.is_empty());
        noircis(&test, &mut img, (f.x, f.y), &s, &[ORANGE]);
        (l, win.window, p)
    }

    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_dialogue_message() {
        let (l, w, _p) = dialogue_de("message");
        assert_eq!(l.choix.len(), 1, "the system's message box waits on a choice");
        let oui = l.elements.iter().position(|e| e.nom == "Yes" || e.nom == "Oui").expect("Yes");
        assert!(l.elements[oui].choix && designe(&l, oui + 1, &l.elements[oui].nom).is_none(), "a bubble only");
        // Its other buttons in a hidden zone: still the person's choice.
        let autres: Vec<Cadre> = l.elements.iter().enumerate().filter(|&(i, e)| i != oui && !e.barre_titre).map(|(_, e)| e.cadre).collect();
        let l = elements(&w, &autres).unwrap();
        let reste: Vec<usize> = (0..l.elements.len()).filter(|&i| !l.elements[i].barre_titre).collect();
        assert_eq!((l.choix.len(), reste.len()), (1, 1), "{:?}", noms(&l));
        assert!(designe(&l, reste[0] + 1, &l.elements[reste[0]].nom).is_none(), "a bubble only, all the same");
    }

    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_dialogue_question() {
        let (l, _, _p) = dialogue_de("question");
        assert_eq!(l.choix.len(), 1, "a modal window of Oui, Non, Annuler waits on a choice");
        let oui = l.elements.iter().position(|e| e.nom == "Oui").expect("Oui");
        assert!(designe(&l, oui + 1, "Oui").is_none());
    }

    /// An error as .NET shows it: its details in a box read only, three buttons. The person's choice: a
    /// box one cannot type in makes no form of it.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_dialogue_erreur() {
        let (l, _, _p) = dialogue_de("erreur");
        assert_eq!(l.choix.len(), 1, "an error's details make no form: {:?}", noms(&l));
        let quitter = l.elements.iter().position(|e| e.nom == "Quitter").expect("Quitter");
        assert!(designe(&l, quitter + 1, "Quitter").is_none());
    }

    /// A dialog that waits on a choice, out of the screen of the window it blocks: nothing of it listed,
    /// still the person's choice, and no ring by pixels on the window behind.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_dialogue_ailleurs() {
        let test = format!("dialogue_ailleurs_{}", echelle());
        let dir = tempfile::tempdir().unwrap();
        let exe = csc("atelier_uia.cs", dir.path());
        let titre = format!("Atelier ailleurs {}", std::process::id());
        let _p = Program(Command::new(&exe).args(["ailleurs", titre.as_str(), "80", "80", "700", "500"]).spawn().unwrap());
        let win = listed_as(|f| f.titre == titre);
        ouvrir();
        let l = jusqu_a(&test, &win.window, WAIT, |l| !l.choix.is_empty());
        imprime(&test, &l, (0, 0));
        assert_eq!(l.choix.len(), 1, "{test}");
        assert!(l.choix[0].x < -1000 && !l.choix[0].touche(&ecran_de_la(&win.window)), "{test}: out of the screen: {:?}", l.choix);
        assert!(l.elements.iter().all(|e| e.barre_titre), "{test}: nothing of it listed: {:?}", noms(&l));
        assert!(l.attend && !repli_pixels(&l), "{test}: no ring by pixels");
    }

    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_dialogue_saisie() {
        let (l, _, _p) = dialogue_de("saisie");
        assert!(l.choix.is_empty(), "a form in a dialog is no choice");
        let ok = l.elements.iter().position(|e| e.nom == "OK").expect("OK");
        assert!(designe(&l, ok + 1, "OK").is_some());
        assert!(l.elements.iter().any(|e| e.genre == Genre::Champ), "its field");
    }

    /// A program named as a remote desktop's client: an empty list, even when its tree has commands (a
    /// client that would expose the other computer's). The ERP-like window run as `mstsc.exe`.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_distant_par_son_nom() {
        let test = format!("distant_par_son_nom_{}", echelle());
        let dir = tempfile::tempdir().unwrap();
        let exe = dir.path().join("mstsc.exe");
        std::fs::rename(csc("atelier_uia.cs", dir.path()), &exe).unwrap();
        let titre = format!("Atelier distant {}", std::process::id());
        let _p = Program(Command::new(&exe).args(["erp", titre.as_str(), "60", "60", "900", "640", "pmv2"]).spawn().unwrap());
        let win = listed_as(|f| f.titre == titre);
        devant_par_clic(&win);
        assert!(distant(&win.window.app), "{test}: {}", win.window.app);

        let lecteur = Lecteur::nouveau().unwrap();
        let _physical = Physical::enter();
        let arbre = || lecteur.arbre(handle(win.window.id), false, &[]).unwrap_or_default();
        let start = Instant::now();
        while nomme(&arbre(), Genre::Bouton, "Nouveau").is_none() {
            assert!(start.elapsed() < WAIT, "{test}: its tree never had its commands");
            std::thread::sleep(Duration::from_millis(250));
        }
        ouvrir();
        let l = elements(&win.window, &[]).unwrap();
        imprime(&test, &l, (0, 0));
        assert!(l.elements.is_empty(), "{test}: {:?}", noms(&l));
        assert!(repli_pixels(&l), "{test}: pixels");
        // Its password field, the other computer's: not known, the window said opaque.
        let f = cadre_de(&win);
        let s = secrets(&ecran_de_la(&win.window)).unwrap();
        mesure(&test, "secrets", format!("{} champs, opaques {:?}", s.champs.len(), quoi(&s.opaques)));
        assert!(s.opaques.contains(&f) && !s.champs.iter().any(|c| c.touche(&f)), "{test}: {s:?}");
    }

    /// A RemoteApp's windows (mstsc, opened by the job, its pid in BIBO_LOIN_PID): an empty list, so
    /// pixels. The same program run here has its menus.
    #[test]
    #[ignore = "a Windows desktop (VM) with mstsc open"]
    fn uia_remoteapp() {
        let test = format!("remoteapp_{}", echelle());
        let pid: u32 = std::env::var("BIBO_LOIN_PID").expect("BIBO_LOIN_PID").parse().unwrap();
        let loin = en_ordre().into_iter().filter(|&h| pid_of(h) == pid && en_vue(h)).find_map(identity).expect("a window of mstsc");
        mesure(&test, "fenetre", class(handle(loin.id)));
        ouvrir();
        let l = elements(&loin, &[]).unwrap();
        imprime(&test, &l, (0, 0));
        assert!(l.elements.is_empty(), "{test}: nothing of the other computer");
        assert!(repli_pixels(&l), "{test}: pixels");
        let f = {
            let _p = Physical::enter();
            cadre(frame(handle(loin.id)).unwrap())
        };
        let s = secrets(&ecran_de_la(&loin)).unwrap();
        mesure(&test, "secrets", format!("{} champs, opaques {:?}", s.champs.len(), quoi(&s.opaques)));
        assert!(s.opaques.contains(&f), "{test}: the other computer's fields unknown: {s:?}");

        let ici = Program(Command::new("notepad.exe").spawn().unwrap());
        let local = listed_as(|f| f.window.pid == ici.0.id() || (f.nom.contains("Notepad") && f.window.pid != pid));
        let l = jusqu_a(&test, &local.window, WAIT, |l| l.elements.iter().any(|e| e.genre == Genre::Menu));
        imprime(&format!("{test}_local"), &l, (0, 0));
        assert!(!repli_pixels(&l), "{test}: the same program here has its tree");
    }

    /// A window of a program the test did not start (the shell's), asked to close.
    struct Fermer(HWND);

    impl Drop for Fermer {
        fn drop(&mut self) {
            // SAFETY: a message to a window; gone already, nothing happens.
            let _ = unsafe { PostMessageW(Some(self.0), WM_CLOSE, WPARAM(0), LPARAM(0)) };
        }
    }

    /// The shell's Run box lives in the taskbar's process, as Explorer's windows may: the taskbar's
    /// buttons (every window's title) and its notification area (what each program says) are none of
    /// its commands.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_executer() {
        let test = format!("executer_{}", echelle());
        let barre = en_ordre().into_iter().find(|&h| class(h) == "Shell_TrayWnd").expect("the taskbar");
        let siens: Vec<(String, Cadre)> = {
            let lecteur = Lecteur::nouveau().unwrap();
            let _physical = Physical::enter();
            let l = lecteur.arbre(barre, false, &[]).unwrap_or_default();
            l.elements.into_iter().filter(|e| !e.barre_titre && !e.nom.is_empty()).map(|e| (e.nom, e.cadre)).collect()
        };
        mesure(&test, "barre_des_taches", format!("{:?}", siens.iter().map(|(n, _)| n).collect::<Vec<_>>()));
        assert!(!siens.is_empty(), "{test}: the taskbar's own commands, to look for");

        touches(&[VK_LWIN, VK_R]);
        let start = Instant::now();
        let boite = loop {
            if let Some(h) = en_ordre().into_iter().find(|&h| en_vue(h) && class(h) == "#32770" && pid_of(h) == pid_of(barre)) {
                break h;
            }
            assert!(start.elapsed() < WAIT, "{test}: no Run box in the taskbar's process");
            std::thread::sleep(Duration::from_millis(200));
        };
        let _ferme = Fermer(boite);
        let fil = fil_de(boite) == fil_de(barre);
        mesure(&test, "boite", format!("{:?} ; proprietaire {:?} ; fil de la barre {fil}", title(boite), owner(boite).map(class)));
        let cible = identity(boite).unwrap();
        ouvrir();
        let l = jusqu_a(&test, &cible, WAIT, |l| l.elements.iter().any(|e| e.genre == Genre::Bouton && !e.barre_titre));
        imprime(&test, &l, (0, 0));
        let de_la_barre: Vec<&String> = l.elements.iter().filter(|e| siens.iter().any(|(n, c)| *n == e.nom && *c == e.cadre)).map(|e| &e.nom).collect();
        assert!(de_la_barre.is_empty(), "{test}: the taskbar's in the Run box's list: {de_la_barre:?}");
    }

    fn atelier_en(mode: &str, titre: &str, x: i32, y: i32, l: i32, h: i32, dir: &Path) -> (Program, Found) {
        let exe = dir.join("atelier_uia.exe");
        let exe = if exe.is_file() { exe } else { csc("atelier_uia.cs", dir) };
        let (x, y, l, h) = (x.to_string(), y.to_string(), l.to_string(), h.to_string());
        let p = Program(Command::new(&exe).args([mode, titre, x.as_str(), y.as_str(), l.as_str(), h.as_str()]).spawn().unwrap());
        let win = listed_as(|f| f.titre == titre);
        (p, win)
    }

    /// The menus open on the window: the program's own (a toolkit's window, owned by it) and the system's
    /// (`#32768`, its thread's): theirs are its commands too.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_menus() {
        let test = format!("menus_{}", echelle());
        let dir = tempfile::tempdir().unwrap();
        let exe = csc("atelier_uia.cs", dir.path());
        let titre = format!("Atelier menus {}", std::process::id());
        let _p = Program(Command::new(&exe).args(["erp", titre.as_str(), "60", "60", "900", "640", "pmv2"]).spawn().unwrap());
        let win = listed_as(|f| f.titre == titre);
        devant_par_clic(&win);
        ouvrir();
        let l = jusqu_a(&test, &win.window, WAIT, |l| nomme(l, Genre::Menu, "Fichier").is_some());
        let fichier = nomme(&l, Genre::Menu, "Fichier").unwrap().cadre;
        clic(fichier.x + fichier.largeur / 2, fichier.y + fichier.hauteur / 2);
        let l = jusqu_a(&test, &win.window, WAIT, |l| nomme(l, Genre::Menu, "Imprimer la facture").is_some());
        imprime(&format!("{test}_fichier"), &l, (0, 0));
        touches(&[VK_ESCAPE]);
        touches(&[VK_ESCAPE]);
        devant_par_clic(&win);
        touches(&[VK_MENU, VK_SPACE]);
        let l = jusqu_a(&test, &win.window, WAIT, |l| l.elements.iter().any(|e| e.genre == Genre::Menu && !e.barre_titre && (e.nom.starts_with("Close") || e.nom.starts_with("Fermer"))));
        imprime(&format!("{test}_systeme"), &l, (0, 0));
        touches(&[VK_ESCAPE]);
    }

    /// WPF puts a tab's content under its tab: the selected one's commands are listed, its password
    /// box found.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_wpf() {
        let test = format!("wpf_{}", echelle());
        let dir = tempfile::tempdir().unwrap();
        let wpf = ["WPF\\PresentationFramework.dll", "WPF\\PresentationCore.dll", "WPF\\WindowsBase.dll", "System.Xaml.dll"];
        let exe = csc_avec("atelier_wpf.cs", dir.path(), &wpf);
        let titre = format!("Atelier WPF {}", std::process::id());
        let _p = Program(Command::new(&exe).args([titre.as_str(), "60", "60", "640", "420"]).spawn().unwrap());
        let win = listed_as(|f| f.titre == titre);
        devant_par_clic(&win);
        ouvrir();
        let l = jusqu_a(&test, &win.window, WAIT, |l| nomme(l, Genre::Onglet, "Clients").is_some());
        imprime(&test, &l, (0, 0));
        assert!(nomme(&l, Genre::Bouton, "Valider la facture").is_some(), "{test}: the selected tab's content: {:?}", noms(&l));
        let f = cadre_de(&win);
        let s = secrets_jusqu_a(&test, &ecran_de_la(&win.window), |s| s.champs.iter().any(|c| c.touche(&f)));
        noircis(&test, &mut ecran(f), (f.x, f.y), &s, &[ORANGE]);
    }

    /// Another window of the program, owned by none (as Edge's second window, another site): nothing of
    /// it in the list of the window asked.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_deux_fenetres() {
        let test = format!("deux_fenetres_{}", echelle());
        let dir = tempfile::tempdir().unwrap();
        let titre = format!("Atelier deux {}", std::process::id());
        let (_p, win) = atelier_en("deux", &titre, 60, 60, 700, 500, dir.path());
        let autre = listed_as(|f| f.titre == format!("{titre} (autre)"));
        assert_eq!(autre.window.pid, win.window.pid);
        ouvrir();
        // Its own list has them: they are there to be left out.
        jusqu_a(&test, &autre.window, WAIT, |l| nomme(l, Genre::Bouton, "Autre fenetre").is_some());
        let l = jusqu_a(&test, &win.window, WAIT, |l| nomme(l, Genre::Bouton, "Nouveau").is_some());
        imprime(&test, &l, (0, 0));
        assert!(nomme(&l, Genre::Bouton, "Autre fenetre").is_none() && fuites(&l).is_empty(), "{test}: {:?}", noms(&l));
    }

    /// Three hundred buttons in sight: the first 150 listed, no more.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_foule() {
        let test = format!("foule_{}", echelle());
        let dir = tempfile::tempdir().unwrap();
        let titre = format!("Atelier foule {}", std::process::id());
        let (_p, win) = atelier_en("foule", &titre, 40, 40, 1000, 700, dir.path());
        devant_par_clic(&win);
        ouvrir();
        let l = jusqu_a(&test, &win.window, WAIT, |l| nomme(l, Genre::Bouton, "B001").is_some());
        mesure(&test, "elements", l.elements.len());
        assert_eq!(l.elements.len(), ELEMENTS_MAX, "{test}: {:?}", noms(&l).last());
        assert!(nomme(&l, Genre::Bouton, "Nouveau").is_some(), "{test}: the first ones kept");
    }

    /// Another program's password field on the same screen: blacked too, the whole screen leaves; the
    /// list of the program asked has nothing of it.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_mots_de_passe_ailleurs() {
        let test = format!("mots_de_passe_ailleurs_{}", echelle());
        let dir = tempfile::tempdir().unwrap();
        let (a, b) = (format!("Atelier ici {}", std::process::id()), format!("Atelier la {}", std::process::id()));
        let (_pa, ici) = atelier_en("fige", &a, 40, 60, 600, 400, dir.path());
        let (_pb, la) = atelier_en("fige", &b, 700, 60, 600, 400, dir.path());
        devant_par_clic(&ici);
        ouvrir();
        let l = jusqu_a(&test, &ici.window, WAIT, |l| nomme(l, Genre::Bouton, "Nouveau").is_some());
        assert_eq!(l.elements.iter().filter(|e| e.nom == "Nouveau").count(), 1, "{test}: the other program's not listed");
        let s = secrets_jusqu_a(&test, &ecran_de_la(&ici.window), |s| s.champs.len() == 2);
        for w in [&ici, &la] {
            let f = cadre_de(w);
            noircis(&test, &mut ecran(f), (f.x, f.y), &s, &[ORANGE]);
        }
    }

    /// A program that hangs (15 s): its list not waited for past 3 s; the reader free again long before
    /// it wakes (UIA's timeouts), another window's list given; its password field never taken for none.
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_fige() {
        let test = format!("fige_{}", echelle());
        let dir = tempfile::tempdir().unwrap();
        let (a, b) = (format!("Atelier fige {}", std::process::id()), format!("Atelier vivant {}", std::process::id()));
        let (_pa, fige) = atelier_en("fige", &a, 40, 60, 600, 400, dir.path());
        let (_pb, vivant) = atelier_en("fige", &b, 700, 60, 600, 400, dir.path());
        ouvrir();
        let l = jusqu_a(&test, &fige.window, WAIT, |l| nomme(l, Genre::Bouton, "Figer").is_some());
        jusqu_a(&test, &vivant.window, WAIT, |l| nomme(l, Genre::Bouton, "Figer").is_some());
        let (ecran_, f) = (ecran_de_la(&fige.window), cadre_de(&fige));
        let figer = nomme(&l, Genre::Bouton, "Figer").unwrap().cadre;
        clic(figer.x + figer.largeur / 2, figer.y + figer.hauteur / 2);
        let t0 = Instant::now();
        std::thread::sleep(Duration::from_millis(500));

        let r = elements(&fige.window, &[]);
        mesure(&test, "liste_figee", format!("{:?} a {:.1} s", r.as_ref().map(noms), t0.elapsed().as_secs_f64()));
        assert!(t0.elapsed() < Duration::from_millis(4500), "{test}: waited past its 3 s");
        let libre = loop {
            if elements(&vivant.window, &[]).is_ok_and(|l| nomme(&l, Genre::Bouton, "Figer").is_some()) {
                break t0.elapsed();
            }
            assert!(t0.elapsed() < Duration::from_secs(40), "{test}: the reader never free again");
            std::thread::sleep(Duration::from_millis(100));
        };
        mesure(&test, "s_lecteur_libre", format!("{:.1}", libre.as_secs_f64()));
        let s = secrets(&ecran_);
        mesure(&test, "secrets_fige", format!("{:?} a {:.1} s", s.as_ref().map(|s| (s.champs.len(), quoi(&s.opaques))), t0.elapsed().as_secs_f64()));
        assert!(t0.elapsed() < Duration::from_secs(14), "{test}: measured while it still hangs");
        if let Ok(s) = &s {
            assert!(s.opaques.contains(&f) || s.champs.iter().any(|c| c.touche(&f)), "{test}: its password field taken for none: {s:?}");
        }
        assert!(libre < Duration::from_secs(12), "{test}: the reader held {libre:?} by a program that hangs 15 s");
    }

    /// Edge met first by the password search, another program in front: its page's three fields found,
    /// or the window said opaque, never taken for a window without one (Chromium builds its tree at the
    /// first question: P0's first list was empty).
    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_edge_premier_contact() {
        let test = format!("edge_premier_contact_{}", echelle());
        let dir = tempfile::tempdir().unwrap();
        fermer();
        let (_b, edge_) = edge_ouvre(&test, dir.path(), "premier");
        let f = cadre_de(&edge_);
        until("the page painted", || count(&ecran(f), ORANGE) > 300);
        let titre = format!("Atelier devant {}", std::process::id());
        let (_p, devant_) = atelier_en("fige", &titre, f.x + f.largeur - 420, f.y + f.hauteur - 300, 400, 260, dir.path());
        devant_par_clic(&devant_);
        let ecran_ = ecran_de_la(&edge_.window);
        ouvrir();
        let t = Instant::now();
        let s = secrets(&ecran_);
        // Edge's, not the one of the window over it.
        let dessus_ = cadre_de(&devant_);
        let connus = |s: &Secrets| s.champs.iter().filter(|c| c.touche(&f) && !c.touche(&dessus_)).count();
        mesure(&test, "premier", format!("{:?} en {:.1} ms", s.as_ref().map(|s| (connus(s), quoi(&s.opaques))), t.elapsed().as_secs_f64() * 1000.0));
        if let Ok(s) = &s {
            assert!(connus(s) == 3 || s.opaques.contains(&f), "{test}: Edge's fields neither found ({}) nor said unknown: {s:?}", connus(s));
        }
        secrets_jusqu_a(&test, &ecran_, |s| connus(s) == 3);
    }
}
