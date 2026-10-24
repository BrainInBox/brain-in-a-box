//! Windows: the guide v2's elements from UI Automation (uia.rs holds the rules). The system's UIA
//! client, in process, on the reader's thread. The program's tree is read one level at a time, in a
//! view filtered on the kinds the rules need: a command, a table or a list is never asked for what
//! lies under it, so their rows are not even walked by the other process (P0: the whole tree took 166
//! to 405 ms at p90, one level at a time 17 to 63 ms).

use std::mem::size_of;

use windows::core::Interface;
use windows::Win32::Foundation::{HWND, LPARAM, POINT, RECT};
use windows::Win32::Graphics::Gdi::{GetMonitorInfoW, MonitorFromWindow, MONITORINFO, MONITOR_DEFAULTTONEAREST};
use windows::Win32::System::Com::{CoCreateInstance, CoInitializeEx, CoUninitialize, CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED};
use windows::Win32::System::Variant::VARIANT;
use windows::Win32::UI::Accessibility::*;
use windows::Win32::UI::Input::KeyboardAndMouse::IsWindowEnabled;
use windows::Win32::UI::WindowsAndMessaging::{EnumWindows, GetAncestor, WindowFromPoint, GA_ROOT};

use super::uia::{attend_un_choix, court, descendre, dialogue, distant, garde, navigateur, visible, Cadre, Contexte, Element, Genre, Lire, Liste, Sorte};
use super::win::{class, cloaked, foreground, frame, handle, identity, list, minimized, owner, pid_of, visible as shown, Physical};
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
        // SAFETY: a request of ours, filled before use.
        let vue = unsafe {
            let r = auto.CreateCacheRequest().map_err(fail)?;
            for p in [
                UIA_ControlTypePropertyId,
                UIA_NamePropertyId,
                UIA_BoundingRectanglePropertyId,
                UIA_IsOffscreenPropertyId,
                UIA_IsEnabledPropertyId,
                UIA_IsPasswordPropertyId,
                UIA_WindowIsModalPropertyId,
            ] {
                r.AddProperty(p).map_err(fail)?;
            }
            let _ = r.AddProperty(UIA_LandmarkTypePropertyId);
            r.SetTreeScope(TreeScope(TreeScope_Element.0 | TreeScope_Children.0)).map_err(fail)?;
            r.SetAutomationElementMode(AutomationElementMode_Full).map_err(fail)?;
            r.SetTreeFilter(filtre.as_ref().ok_or(Rate::Indisponible)?).map_err(fail)?;
            r
        };
        Ok(Lecteur { auto, vue, _com: com })
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
    /// Every command of the window, seen or not, unnamed: what the guard of the dialog counts (a
    /// hidden zone over « Non » leaves a dialog that waits on a choice all the same).
    toutes: Vec<Element>,
    secrets: Vec<Cadre>,
}

impl Parcours<'_> {
    fn commande(&mut self, el: &IUIAutomationElement, genre: Genre, ctx: Contexte) {
        // SAFETY: cached properties of an element of ours.
        let c = cadre(unsafe { el.CachedBoundingRectangle() }.unwrap_or_default());
        if genre == Genre::Champ && c.touche(&self.fenetre) && unsafe { el.CachedIsPassword() }.is_ok_and(|b| b.as_bool()) {
            self.secrets.push(c);
        }
        let barre_titre = ctx.titre;
        self.toutes.push(Element { genre, nom: String::new(), cadre: c, barre_titre, choix: false });
        let hors_vue = unsafe { el.CachedIsOffscreen() }.map_or(true, |b| b.as_bool());
        let actif = unsafe { el.CachedIsEnabled() }.is_ok_and(|b| b.as_bool());
        if garde(genre, ctx, self.navigateur) && visible(&c, &self.fenetre, &self.ecran, hors_vue, actif, self.cachees) && montre(self.hwnd, &c) {
            let nom = unsafe { el.CachedName() }.map(|b| court(&b.to_string())).unwrap_or_default();
            self.elements.push(Element { genre, nom, cadre: c, barre_titre, choix: false });
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

/// The process's windows on the screen, the one on top first: its main window, its dialogs, its
/// open menus.
fn fenetres(pid: u32) -> Vec<HWND> {
    en_ordre().into_iter().filter(|&h| pid_of(h) == pid && shown(h) && !minimized(h) && !cloaked(h)).collect()
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
        let h = handle(cible.id);
        let toujours = || same_window(&cible, identity(h).as_ref());
        if cible.pid == std::process::id() || !toujours() {
            return Err(Rate::Introuvable);
        }
        if minimized(h) || !shown(h) {
            return Err(Rate::Reduite);
        }
        if distant(&cible.app) {
            return Ok(Liste::default());
        }
        let ecran = ecran_de(h).ok_or(Rate::Introuvable)?;
        let mut liste = Liste::default();
        for w in fenetres(cible.pid) {
            let Some(f) = frame(w).map(cadre).filter(|f| f.touche(&ecran)) else { continue };
            // A window that waits on a dialog: nothing of it can be clicked.
            if !active(w) {
                continue;
            }
            // SAFETY: a window handle; its first level comes back cached.
            let Ok(racine) = (unsafe { self.auto.ElementFromHandleBuildCache(w, &self.vue) }) else { continue };
            let mut p = Parcours { hwnd: w, fenetre: f, ecran, navigateur: navigateur(&cible.app), cachees, elements: Vec::new(), toutes: Vec::new(), secrets: Vec::new() };
            self.parcourir(&racine, Contexte::default(), 0, &mut p);
            // SAFETY: a cached property of an element of ours.
            let modale = unsafe { racine.GetCachedPropertyValue(UIA_WindowIsModalPropertyId) }.ok().and_then(|v| bool::try_from(&v).ok()).unwrap_or(false);
            let attend = owner(w).is_some_and(|o| !active(o));
            if attend_un_choix(dialogue(&class(w), attend, modale), &p.toutes) {
                p.elements.iter_mut().for_each(|e| e.choix = true);
                liste.choix.push(f);
            }
            liste.elements.extend(p.elements);
            liste.secrets.extend(p.secrets);
        }
        if !toujours() {
            return Err(Rate::Introuvable);
        }
        Ok(liste)
    }
}

#[cfg(test)]
mod tests {
    use super::super::uia::{designe, elements, fermer, lignes, ouvrir, repli_pixels, zones_secretes, NOM_MAX};
    use super::super::win::title;
    use super::*;
    use crate::capture::bureau::*;
    use crate::capture::win::Canvas;
    use crate::capture::{from_bgra, Found};
    use crate::shot::Rgba;
    use std::path::{Path, PathBuf};
    use std::process::Command;
    use std::time::{Duration, Instant};
    use windows::Win32::Foundation::POINT;
    use windows::Win32::Graphics::Gdi::{BitBlt, GetDC, MonitorFromPoint, ReleaseDC, MONITOR_DEFAULTTOPRIMARY, SRCCOPY};
    use windows::Win32::UI::HiDpi::{GetDpiForMonitor, MDT_EFFECTIVE_DPI};
    use windows::Win32::UI::Input::KeyboardAndMouse::{SendInput, INPUT, INPUT_0, INPUT_MOUSE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEINPUT, MOUSE_EVENT_FLAGS};
    use windows::Win32::UI::WindowsAndMessaging::SetCursorPos;

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
        let windir = PathBuf::from(std::env::var("WINDIR").unwrap_or_else(|_| "C:\\Windows".into()));
        let csc = windir.join("Microsoft.NET").join("Framework64").join("v4.0.30319").join("csc.exe");
        let exe = dir.join(source.replace(".cs", ".exe"));
        let out = Command::new(&csc)
            .args(["/nologo", "/target:winexe", "/optimize+", "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll"])
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

    /// The window on the screen once each colour of its commands shows, 3 s at most: a run of the CI
    /// once found no pixel of Edge's password field (3 jobs of 6), nothing said what lay over it. Each
    /// capture taken again says the windows over the window then.
    fn ecran_montre(test: &str, f: Cadre, couleurs: &[[u8; 3]]) -> Rgba {
        let start = Instant::now();
        loop {
            let img = ecran(f);
            let absentes: Vec<_> = couleurs.iter().filter(|&&c| count(&img, c) == 0).collect();
            if absentes.is_empty() || start.elapsed() > Duration::from_secs(3) {
                return img;
            }
            mesure(test, "capture_reprise", format!("{absentes:?} absent; over the window: {:?}", dessus(&f)));
            std::thread::sleep(Duration::from_millis(250));
        }
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
        let premier = elements(&win.window, &[]);
        mesure(&test, "ms_premier_appel", format!("{:.1} ({} elements)", t.elapsed().as_secs_f64() * 1000.0, premier.map_or(0, |l| l.elements.len())));
        let l = jusqu_a(&test, &win.window, WAIT, |l| nomme(l, Genre::Bouton, "Nouveau").is_some());
        let p90 = latence(&test, &win.window);

        let f = cadre_de(&win);
        let origine = (f.x, f.y);
        let mut img = ecran_montre(&test, f, &[GREEN, PURPLE, RED, ORANGE]);
        imprime(&test, &l, origine);
        let mut pire = 0;
        for (couleur, nom) in [(GREEN, "Nouveau"), (PURPLE, "Enregistrer"), (RED, "Supprimer")] {
            let e = nomme(&l, Genre::Bouton, nom).unwrap_or_else(|| panic!("{test}: {nom}"));
            pire = pire.max(ecart(&test, &img, origine, couleur, nom, &e.cadre));
        }
        assert_eq!(l.secrets.len(), 1, "{test}: the password field");
        pire = pire.max(ecart(&test, &img, origine, ORANGE, "mot de passe", &l.secrets[0]));
        mesure(&test, "ecart_max_px", pire);
        let avant = count(&img, ORANGE);
        crate::shot::paint(&mut img, &zones_secretes(&l, origine));
        mesure(&test, "orange_avant_apres", format!("{avant} -> {}", count(&img, ORANGE)));
        mesure(&test, "noms_clients", format!("{:?}", fuites(&l)));
        let supprimer = nomme(&l, Genre::Bouton, "Supprimer").unwrap().cadre;
        let cache = elements(&win.window, &[supprimer]).unwrap();

        assert!(avant > 500 && count(&img, ORANGE) == 0, "{test}: the password field blacked");
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
        assert!(!repli_pixels(&l) && l.choix.is_empty(), "{test}: a tree, no dialog");
        assert!(designe(&l, l.elements.iter().position(|e| e.nom == "Nouveau").unwrap() + 1, "Nouveau").is_some());
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
        let l = jusqu_a(&test, &win.window, Duration::from_secs(60), |l| nomme(l, Genre::Bouton, "Nouveau").is_some() && l.elements.len() > 15);
        let p90 = latence(&test, &win.window);

        let f = cadre_de(&win);
        let origine = (f.x, f.y);
        let mut img = ecran_montre(&test, f, &[GREEN, PURPLE, RED, ORANGE]);
        imprime(&test, &l, origine);
        let mut pire = 0;
        for (couleur, nom) in [(GREEN, "Nouveau"), (PURPLE, "Enregistrer"), (RED, "Supprimer")] {
            let e = nomme(&l, Genre::Bouton, nom).unwrap_or_else(|| panic!("{test}: {nom}"));
            pire = pire.max(ecart(&test, &img, origine, couleur, nom, &e.cadre));
        }
        assert_eq!(l.secrets.len(), 1, "{test}: the password field");
        mesure(&test, "fenetres_sur_le_mot_de_passe", format!("{:?}", dessus(&l.secrets[0])));
        pire = pire.max(ecart(&test, &img, origine, ORANGE, "mot de passe", &l.secrets[0]));
        mesure(&test, "ecart_max_px", pire);
        let avant = count(&img, ORANGE);
        crate::shot::paint(&mut img, &zones_secretes(&l, origine));
        mesure(&test, "orange_avant_apres", format!("{avant} -> {}", count(&img, ORANGE)));
        mesure(&test, "noms_clients", format!("{:?}", fuites(&l)));

        assert!(avant > 500 && count(&img, ORANGE) == 0, "{test}: the password field blacked");
        assert!(fuites(&l).is_empty(), "{test}: no customer, in the table or linked beside it");
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
        let l = jusqu_a(&test, &win.window, WAIT, |l| l.elements.iter().filter(|e| e.genre == Genre::Bouton && !e.barre_titre).count() >= 2);
        imprime(&test, &l, (0, 0));
        mesure(&test, "choix", l.choix.len());
        assert!(nomme(&l, Genre::Bouton, "Nouveau").is_none(), "{test}: the window behind waits: {:?}", noms(&l));
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

    #[test]
    #[ignore = "a Windows desktop (VM)"]
    fn uia_dialogue_saisie() {
        let (l, _, _p) = dialogue_de("saisie");
        assert!(l.choix.is_empty(), "a form in a dialog is no choice");
        let ok = l.elements.iter().position(|e| e.nom == "OK").expect("OK");
        assert!(designe(&l, ok + 1, "OK").is_some());
        assert!(l.elements.iter().any(|e| e.genre == Genre::Champ), "its field");
    }

    /// A RemoteApp's windows (mstsc, opened by the job, its pid in BIBO_LOIN_PID): an empty list, so
    /// pixels. The same program run here has its menus.
    #[test]
    #[ignore = "a Windows desktop (VM) with mstsc open"]
    fn uia_remoteapp() {
        let test = format!("remoteapp_{}", echelle());
        let pid: u32 = std::env::var("BIBO_LOIN_PID").expect("BIBO_LOIN_PID").parse().unwrap();
        let loin = fenetres(pid).into_iter().find_map(identity).expect("a window of mstsc");
        mesure(&test, "fenetre", class(handle(loin.id)));
        let l = elements(&loin, &[]).unwrap();
        imprime(&test, &l, (0, 0));
        assert!(l.elements.is_empty() && l.secrets.is_empty(), "{test}: nothing of the other computer");
        assert!(repli_pixels(&l), "{test}: pixels");

        let ici = Program(Command::new("notepad.exe").spawn().unwrap());
        let local = listed_as(|f| f.window.pid == ici.0.id() || (f.nom.contains("Notepad") && f.window.pid != pid));
        let l = jusqu_a(&test, &local.window, WAIT, |l| l.elements.iter().any(|e| e.genre == Genre::Menu));
        imprime(&format!("{test}_local"), &l, (0, 0));
        assert!(!repli_pixels(&l), "{test}: the same program here has its tree");
    }
}
