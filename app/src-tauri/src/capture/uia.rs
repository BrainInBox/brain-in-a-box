//! The guide v2's elements (guide v2, §3 and P2a): what can be clicked in the program in front, read
//! from UI Automation on Windows (uia_win.rs), numbered, each with its box on the screen. The engine
//! names one by its number; Bibo points at it from its own list, never from the answer, and only when
//! its name is the label the engine gives.
//!
//! Commands only, never data (C2): buttons, menu items, tabs, check boxes, radio buttons, lists and
//! fields by their label. A link only in a bar of navigation, menus or tools: elsewhere its name is
//! often a customer (on a quotation's form, the customer is a link). Never a value, never anything
//! inside a table, a list or a tree. In a browser, the page only, never its tabs. Each name on one
//! line, 40 characters at most. What lies in a hidden zone is left out; password fields come apart,
//! to be blacked on the image. A dialog that waits on the person's choice is said: Bibo puts a bubble
//! there, never a ring (M1).
//!
//! The reader lives on a thread of its own, created with a first read when the guide opens: that one
//! takes 0.3 to 1.2 s, and gets a browser to build its tree. The next ones take tens of milliseconds.
//! macOS and Linux: not available (P6).

use std::sync::mpsc::{self, Sender};
use std::sync::{Mutex, MutexGuard, PoisonError};
use std::time::{Duration, Instant};

use super::{Rate, Window};
use crate::shot::Zone;

pub const NOM_MAX: usize = 40;
/// How long a list is waited for; past it, Bibo points by pixels as in v1. UIA's own timeouts keep a
/// program that hangs from holding the reader much longer.
#[cfg_attr(not(windows), allow(dead_code))]
const DELAI: Duration = Duration::from_secs(3);
/// The programs whose window shows another computer's screen: their tree has nothing of it (C10).
#[cfg_attr(not(windows), allow(dead_code))]
const DISTANTS: [&str; 6] = ["mstsc.exe", "msrdc.exe", "msrdcw.exe", "wfica32.exe", "cdviewer.exe", "vmconnect.exe"];
#[cfg_attr(not(windows), allow(dead_code))]
const NAVIGATEURS: [&str; 6] = ["msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"];

/// A box on the screen, in physical pixels.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Cadre {
    pub x: i32,
    pub y: i32,
    pub largeur: i32,
    pub hauteur: i32,
}

impl Cadre {
    fn vide(&self) -> bool {
        self.largeur <= 0 || self.hauteur <= 0
    }

    /// Some pixel in common.
    pub fn touche(&self, o: &Cadre) -> bool {
        !self.vide() && !o.vide() && self.x < o.x + o.largeur && o.x < self.x + self.largeur && self.y < o.y + o.hauteur && o.y < self.y + self.hauteur
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[cfg_attr(not(windows), allow(dead_code))]
pub enum Genre {
    Bouton,
    Menu,
    Onglet,
    Lien,
    Case,
    BoutonRadio,
    Liste,
    Champ,
}

impl Genre {
    pub fn mot(self) -> &'static str {
        match self {
            Genre::Bouton => "bouton",
            Genre::Menu => "menu",
            Genre::Onglet => "onglet",
            Genre::Lien => "lien",
            Genre::Case => "case",
            Genre::BoutonRadio => "option",
            Genre::Liste => "liste",
            Genre::Champ => "champ",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Element {
    pub genre: Genre,
    /// On one line, 40 characters at most.
    pub nom: String,
    pub cadre: Cadre,
    /// One of the window's own buttons (minimize, maximize, close), not the program's.
    pub barre_titre: bool,
    /// In a dialog that waits on the person's choice: a bubble there, never a ring.
    pub choix: bool,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Liste {
    /// Numbered from 1, in this order: the window on top first.
    pub elements: Vec<Element>,
    /// Password fields, blacked on the image before anything else is made of it.
    pub secrets: Vec<Cadre>,
    /// The dialogs that wait on the person's choice.
    pub choix: Vec<Cadre>,
}

/// What a node of the tree is, as the rules see it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[cfg_attr(not(windows), allow(dead_code))]
pub(super) enum Sorte {
    Commande(Genre),
    /// A table, a list, a tree or one of their rows: never listed, never walked.
    Donnees,
    /// A browser's page.
    Page,
    /// A bar of navigation, menus or tools: its links keep their names.
    Barre,
    /// The window's title bar.
    BarreTitre,
    Autre,
}

/// What the nodes under a node inherit.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
#[cfg_attr(not(windows), allow(dead_code))]
pub(super) struct Contexte {
    pub page: bool,
    pub barre: bool,
    pub titre: bool,
}

#[cfg_attr(not(windows), allow(dead_code))]
impl Contexte {
    pub(super) fn sous(self, s: Sorte) -> Contexte {
        Contexte { page: self.page || s == Sorte::Page, barre: self.barre || s == Sorte::Barre, titre: self.titre || s == Sorte::BarreTitre }
    }
}

/// A command and a table's rows are never walked: nothing under them is read, not even by the other
/// process.
#[cfg_attr(not(windows), allow(dead_code))]
pub(super) fn descendre(s: Sorte) -> bool {
    !matches!(s, Sorte::Commande(_) | Sorte::Donnees)
}

/// Whether a command, at this place of the tree, may be listed: in a browser, only from the page
/// (never its tabs nor its bar); a link, only in a bar.
#[cfg_attr(not(windows), allow(dead_code))]
pub(super) fn garde(genre: Genre, ctx: Contexte, navigateur: bool) -> bool {
    (!navigateur || ctx.page) && (genre != Genre::Lien || ctx.barre)
}

/// Whether the person sees the command and can click it: on its window and on the screen, not out of
/// view, enabled, outside every hidden zone.
#[cfg_attr(not(windows), allow(dead_code))]
pub(super) fn visible(c: &Cadre, fenetre: &Cadre, ecran: &Cadre, hors_vue: bool, actif: bool, cachees: &[Cadre]) -> bool {
    c.touche(fenetre) && c.touche(ecran) && !hors_vue && actif && !cachees.iter().any(|z| z.touche(c))
}

/// A dialog: the system's (`#32770`), a window its owner waits on, or one UIA says modal.
#[cfg_attr(not(windows), allow(dead_code))]
pub(super) fn dialogue(classe: &str, proprietaire_attend: bool, modale: bool) -> bool {
    classe == "#32770" || proprietaire_attend || modale
}

/// A dialog that waits on the person's choice (M1): two buttons or more, and nothing to type nor to
/// pick from a list. A form in a dialog (Sage's invoice) is not one.
#[cfg_attr(not(windows), allow(dead_code))]
pub(super) fn attend_un_choix(dialogue: bool, elements: &[Element]) -> bool {
    let boutons = elements.iter().filter(|e| e.genre == Genre::Bouton && !e.barre_titre).count();
    dialogue && boutons >= 2 && !elements.iter().any(|e| matches!(e.genre, Genre::Champ | Genre::Liste))
}

fn exe(app: &str) -> String {
    app.rsplit(['\\', '/']).next().unwrap_or(app).to_lowercase()
}

#[cfg_attr(not(windows), allow(dead_code))]
pub(super) fn distant(app: &str) -> bool {
    DISTANTS.contains(&exe(app).as_str())
}

#[cfg_attr(not(windows), allow(dead_code))]
pub(super) fn navigateur(app: &str) -> bool {
    NAVIGATEURS.contains(&exe(app).as_str())
}

/// A name as it goes in the list: on one line, no quote that would end it, no invisible or bidi mark,
/// 40 characters at most.
#[cfg_attr(not(windows), allow(dead_code))]
pub(super) fn court(nom: &str) -> String {
    let invisible = |c: char| c.is_control() || matches!(c, '\u{200b}'..='\u{200f}' | '\u{202a}'..='\u{202e}' | '\u{2060}'..='\u{2069}' | '\u{feff}');
    let line: String = nom.chars().filter(|&c| !invisible(c) || c.is_whitespace()).map(|c| if c == '"' { '\'' } else { c }).collect();
    line.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(NOM_MAX).collect()
}

/// Bibo points by pixels, as in v1, when the tree gives nothing to point at: nothing but the title
/// bar's buttons (a program without accessibility, a canvas), or nothing at all (a remote desktop).
#[cfg_attr(not(test), allow(dead_code))]
pub fn repli_pixels(liste: &Liste) -> bool {
    liste.elements.iter().all(|e| e.barre_titre)
}

/// A letter without its accent, in lower case. A pair this misses only costs a ring: no match, a
/// bubble.
fn sans_accent(c: char) -> char {
    match c {
        'à' | 'â' | 'ä' | 'á' | 'ã' | 'å' => 'a',
        'é' | 'è' | 'ê' | 'ë' => 'e',
        'î' | 'ï' | 'í' | 'ì' => 'i',
        'ô' | 'ö' | 'ó' | 'ò' | 'õ' => 'o',
        'ù' | 'û' | 'ü' | 'ú' => 'u',
        'ç' => 'c',
        'ÿ' | 'ý' => 'y',
        'ñ' => 'n',
        c => c,
    }
}

/// The words of a label as they are compared: lower case, without accents, punctuation nor an
/// accelerator's `&`, without icon words (a ligature's name, « arrow_drop_down », or a glyph of an
/// icon font).
fn mots(s: &str) -> Vec<String> {
    let kept: String = s.chars().flat_map(char::to_lowercase).map(sans_accent).map(|c| if c.is_alphanumeric() || c == '_' { c } else { ' ' }).collect();
    kept.split_whitespace().filter(|w| !w.contains('_')).map(str::to_string).collect()
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Accord {
    Egal,
    Prefixe,
}

/// The engine's label against a name of the list: the same words; or the name says more after them
/// (« New add »); or the name was cut at 40 characters and the label goes on.
fn accord(libelle: &str, nom: &str) -> Option<Accord> {
    let (a, b) = (mots(libelle), mots(nom));
    if a.is_empty() || b.is_empty() {
        return None;
    }
    if a == b {
        return Some(Accord::Egal);
    }
    let coupe = nom.chars().count() >= NOM_MAX && a.join(" ").starts_with(&b.join(" "));
    (b.starts_with(&a) || coupe).then_some(Accord::Prefixe)
}

/// The element the engine names by its number, if its name is the label the engine gives (C3): the
/// number comes from the model and may be wrong. Never a title bar's button, never in a dialog that
/// waits on the person's choice. A name that only begins with the label loses to another element
/// whose name is the label: the number was likely the wrong one.
#[cfg_attr(not(test), allow(dead_code))]
pub fn designe<'a>(liste: &'a Liste, numero: usize, libelle: &str) -> Option<&'a Element> {
    let e = liste.elements.get(numero.checked_sub(1)?)?;
    if e.barre_titre || e.choix {
        return None;
    }
    match accord(libelle, &e.nom)? {
        Accord::Egal => Some(e),
        Accord::Prefixe => (!liste.elements.iter().any(|o| !o.barre_titre && accord(libelle, &o.nom) == Some(Accord::Egal))).then_some(e),
    }
}

/// The lines the engine gets, `#7 bouton "Nouveau" [x,y,l,h]`: each box in the image's pixels, the
/// image being the screen's capture from `origine` (its top left corner), `ratio` image pixels to a
/// screen pixel.
#[cfg_attr(not(test), allow(dead_code))]
pub fn lignes(liste: &Liste, origine: (i32, i32), ratio: f64) -> Vec<String> {
    let at = |v: i32, o: i32| (f64::from(v - o) * ratio).round() as i64;
    liste
        .elements
        .iter()
        .enumerate()
        .map(|(i, e)| {
            let c = &e.cadre;
            let (x, y) = (at(c.x, origine.0), at(c.y, origine.1));
            let (w, h) = (at(c.x + c.largeur, origine.0) - x, at(c.y + c.hauteur, origine.1) - y);
            format!("#{} {} \"{}\" [{x},{y},{w},{h}]", i + 1, e.genre.mot(), e.nom)
        })
        .collect()
}

/// The password fields as zones of the screen's capture taken from `origine`, for `shot::paint` to
/// black them before anything else is made of the image.
#[cfg_attr(not(test), allow(dead_code))]
pub fn zones_secretes(liste: &Liste, origine: (i32, i32)) -> Vec<Zone> {
    let at = |v: i32, o: i32| v.saturating_sub(o).max(0) as u32;
    liste
        .secrets
        .iter()
        .map(|c| {
            let (x, y) = (at(c.x, origine.0), at(c.y, origine.1));
            let (x1, y1) = (at(c.x.saturating_add(c.largeur), origine.0), at(c.y.saturating_add(c.hauteur), origine.1));
            Zone { x, y, largeur: x1 - x, hauteur: y1 - y }
        })
        .collect()
}

/// Who reads the tree: UIA on Windows, a fake in the tests. `None`: the window in front, Bibo's left
/// out (the read that warms the reader up).
#[cfg_attr(not(windows), allow(dead_code))]
pub(super) trait Lire {
    fn lire(&mut self, cible: Option<&Window>, cachees: &[Cadre]) -> Result<Liste, Rate>;
}

#[cfg_attr(not(windows), allow(dead_code))]
struct Travail {
    cible: Option<Window>,
    cachees: Vec<Cadre>,
    reponse: Option<Sender<Result<Liste, Rate>>>,
    /// Past it, nobody waits for the list any more: it is not read. A program that hangs never piles
    /// up the lists asked meanwhile.
    echeance: Option<Instant>,
}

/// The reader's thread: it makes its reader there (UIA's client stays on the thread that made it),
/// reads once the window in front, then each list asked, in turn. It ends with its last sender.
#[cfg_attr(not(windows), allow(dead_code))]
struct Travailleur {
    envoi: Sender<Travail>,
}

#[cfg_attr(not(windows), allow(dead_code))]
impl Travailleur {
    fn demarrer<L: Lire>(creer: impl FnOnce() -> Result<L, Rate> + Send + 'static) -> Travailleur {
        let (envoi, recu) = mpsc::channel::<Travail>();
        let _ = std::thread::Builder::new().name("bibo-uia".into()).spawn(move || {
            let mut lecteur = creer();
            for t in recu {
                if t.echeance.is_some_and(|e| Instant::now() >= e) {
                    continue;
                }
                let liste = match &mut lecteur {
                    Ok(l) => l.lire(t.cible.as_ref(), &t.cachees),
                    Err(e) => Err(*e),
                };
                if let Some(r) = t.reponse {
                    let _ = r.send(liste);
                }
            }
        });
        let _ = envoi.send(Travail { cible: None, cachees: Vec::new(), reponse: None, echeance: None });
        Travailleur { envoi }
    }
}

/// A list asked of the reader's thread, waited for `delai` at most.
#[cfg_attr(not(windows), allow(dead_code))]
fn demander(envoi: &Sender<Travail>, cible: &Window, cachees: &[Cadre], delai: Duration) -> Result<Liste, Rate> {
    let (reponse, recu) = mpsc::channel();
    let echeance = Some(Instant::now() + delai);
    envoi.send(Travail { cible: Some(cible.clone()), cachees: cachees.to_vec(), reponse: Some(reponse), echeance }).map_err(|_| Rate::Indisponible)?;
    recu.recv_timeout(delai).map_err(|_| Rate::Indisponible)?
}

static LECTEUR: Mutex<Option<Travailleur>> = Mutex::new(None);

fn lecteur() -> MutexGuard<'static, Option<Travailleur>> {
    LECTEUR.lock().unwrap_or_else(PoisonError::into_inner)
}

/// The guide opened: the reader is made, and reads the program in front once, for nothing.
pub fn ouvrir() {
    #[cfg(windows)]
    {
        let mut slot = lecteur();
        if slot.is_none() {
            *slot = Some(Travailleur::demarrer(super::uia_win::Lecteur::nouveau));
        }
    }
}

/// The guide closed: the reader goes, its thread with it.
pub fn fermer() {
    lecteur().take();
}

/// The commands of `fenetre`'s program on the screen it is on, the hidden zones' left out.
#[cfg_attr(not(test), allow(dead_code))]
pub fn elements(fenetre: &Window, cachees: &[Cadre]) -> Result<Liste, Rate> {
    #[cfg(windows)]
    {
        ouvrir();
        let envoi = lecteur().as_ref().map(|t| t.envoi.clone()).ok_or(Rate::Indisponible)?;
        demander(&envoi, fenetre, cachees, DELAI)
    }
    #[cfg(not(windows))]
    {
        let _ = (fenetre, cachees);
        Err(Rate::Indisponible)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    fn el(genre: Genre, nom: &str) -> Element {
        Element { genre, nom: court(nom), cadre: Cadre { x: 10, y: 10, largeur: 80, hauteur: 30 }, barre_titre: false, choix: false }
    }

    fn liste(elements: Vec<Element>) -> Liste {
        Liste { elements, ..Liste::default() }
    }

    #[test]
    fn a_name_is_one_line_of_forty_characters_without_a_quote_or_a_hidden_mark() {
        assert_eq!(court("  Nouveau\r\n\tdevis  "), "Nouveau devis");
        assert_eq!(court("Exporter toutes les factures du trimestre vers le cabinet comptable"), "Exporter toutes les factures du trimestr");
        assert_eq!(court(&"é".repeat(60)).chars().count(), NOM_MAX);
        assert_eq!(court("Dire \"oui\""), "Dire 'oui'", "a quote would end the line's name");
        assert_eq!(court("Pa\u{202e}yer\u{200b}\u{7}"), "Payer", "bidi, zero-width and control marks");
    }

    #[test]
    fn commands_only_never_data() {
        for s in [Sorte::Commande(Genre::Bouton), Sorte::Commande(Genre::Champ), Sorte::Commande(Genre::Liste), Sorte::Donnees] {
            assert!(!descendre(s), "{s:?}: nothing under it is read");
        }
        for s in [Sorte::Page, Sorte::Barre, Sorte::BarreTitre, Sorte::Autre] {
            assert!(descendre(s), "{s:?}");
        }
    }

    #[test]
    fn a_link_keeps_its_place_only_in_a_bar() {
        let racine = Contexte::default();
        assert!(!garde(Genre::Lien, racine, false), "a link on a form: often a customer");
        assert!(!garde(Genre::Lien, racine.sous(Sorte::Page), true));
        assert!(garde(Genre::Lien, racine.sous(Sorte::Barre), false));
        assert!(garde(Genre::Lien, racine.sous(Sorte::Page).sous(Sorte::Barre), true), "a page's menu");
        assert!(garde(Genre::Lien, racine.sous(Sorte::Barre).sous(Sorte::Page), true), "a page in a bar's place");
        assert!(garde(Genre::Bouton, racine, false));
    }

    #[test]
    fn in_a_browser_only_the_page() {
        let racine = Contexte::default();
        assert!(!garde(Genre::Onglet, racine, true), "the browser's tabs: other sites' titles");
        assert!(!garde(Genre::Bouton, racine.sous(Sorte::Barre), true), "its own bar");
        assert!(garde(Genre::Bouton, racine.sous(Sorte::Page), true));
        assert!(garde(Genre::Onglet, racine, false), "a program's own tabs");
        let page = racine.sous(Sorte::Page).sous(Sorte::Autre).sous(Sorte::Barre);
        assert_eq!((page.page, page.barre, page.titre), (true, true, false), "what is above stays");
        assert!(racine.sous(Sorte::BarreTitre).titre);
    }

    #[test]
    fn seen_and_clickable_only() {
        let fenetre = Cadre { x: 100, y: 100, largeur: 800, hauteur: 600 };
        let ecran = Cadre { x: 0, y: 0, largeur: 1920, hauteur: 1080 };
        let b = Cadre { x: 120, y: 140, largeur: 100, hauteur: 30 };
        assert!(visible(&b, &fenetre, &ecran, false, true, &[]));
        assert!(!visible(&b, &fenetre, &ecran, true, true, &[]), "out of view");
        assert!(!visible(&b, &fenetre, &ecran, false, false, &[]), "disabled");
        assert!(!visible(&Cadre { x: 950, ..b }, &fenetre, &ecran, false, true, &[]), "outside its window");
        let ailleurs = Cadre { x: 1920, y: 0, largeur: 1920, hauteur: 1080 };
        assert!(!visible(&b, &fenetre, &ailleurs, false, true, &[]), "on another screen");
        assert!(!visible(&Cadre { largeur: 0, ..b }, &fenetre, &ecran, false, true, &[]), "no size");
        let zone = Cadre { x: 200, y: 160, largeur: 50, hauteur: 50 };
        assert!(!visible(&b, &fenetre, &ecran, false, true, &[zone]), "in a hidden zone, even in part");
        assert!(visible(&b, &fenetre, &ecran, false, true, &[Cadre { x: 400, ..zone }]));
    }

    #[test]
    fn a_dialog_that_waits_on_a_choice() {
        assert!(dialogue("#32770", false, false), "the system's message box");
        assert!(dialogue("WindowsForms10.Window.8.app.0.1", true, false), "a window its owner waits on");
        assert!(dialogue("WindowsForms10.Window.8.app.0.1", false, true), "UIA says modal");
        assert!(!dialogue("WindowsForms10.Window.8.app.0.1", false, false));

        let oui_non = [el(Genre::Bouton, "Oui"), el(Genre::Bouton, "Non")];
        assert!(attend_un_choix(true, &oui_non));
        assert!(!attend_un_choix(false, &oui_non), "a main window");
        assert!(!attend_un_choix(true, &oui_non[..1]), "one button: nothing to choose");
        let fermer = Element { barre_titre: true, ..el(Genre::Bouton, "Fermer") };
        assert!(!attend_un_choix(true, &[oui_non[0].clone(), fermer]), "the title bar's close is no choice");
        let mut saisie = oui_non.to_vec();
        saisie.push(el(Genre::Champ, "Montant"));
        assert!(!attend_un_choix(true, &saisie), "a form in a dialog");
        saisie.pop();
        saisie.push(el(Genre::Liste, "Client"));
        assert!(!attend_un_choix(true, &saisie));
        saisie.pop();
        saisie.push(el(Genre::Case, "Ne plus demander"));
        assert!(attend_un_choix(true, &saisie), "a box to tick does not make it a form");
    }

    #[test]
    fn programs_by_their_executable() {
        assert!(distant("C:\\Windows\\System32\\mstsc.exe") && distant("C:\\Program Files\\Citrix\\ICA Client\\WFICA32.EXE"));
        assert!(distant("C:\\x\\msrdc.exe") && distant("C:\\x\\vmconnect.exe"));
        assert!(!distant("C:\\Sage\\Sage100.exe"));
        assert!(navigateur("C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe") && navigateur("C:\\x\\chrome.exe"));
        assert!(!navigateur("C:\\x\\EXCEL.EXE"));
    }

    #[test]
    fn pixels_when_the_tree_gives_nothing() {
        assert!(repli_pixels(&Liste::default()), "a remote desktop");
        let barre = |n: &str| Element { barre_titre: true, ..el(Genre::Bouton, n) };
        assert!(repli_pixels(&liste(vec![barre("Réduire"), barre("Agrandir"), barre("Fermer")])), "a canvas");
        assert!(!repli_pixels(&liste(vec![barre("Fermer"), el(Genre::Bouton, "Nouveau")])));
    }

    #[test]
    fn the_label_against_the_name() {
        let l = liste(vec![el(Genre::Bouton, "Nouveau"), el(Genre::Bouton, "New arrow_drop_down"), el(Genre::Menu, "&Édition")]);
        assert_eq!(designe(&l, 1, "Nouveau").map(|e| e.nom.as_str()), Some("Nouveau"));
        assert!(designe(&l, 1, "  NOUVEAU ").is_some(), "case and spaces");
        assert!(designe(&l, 1, "Enregistrer").is_none(), "another name: the number was wrong");
        assert!(designe(&l, 2, "New").is_some(), "an icon's word after it");
        assert!(designe(&l, 3, "Edition").is_some(), "accents and the accelerator");
        assert!(designe(&l, 0, "Nouveau").is_none() && designe(&l, 4, "Nouveau").is_none(), "no such number");
        assert!(designe(&l, 1, "").is_none() && designe(&l, 1, "▾").is_none(), "no label, nothing to check");

        // Words after it: the name begins with the label.
        let l = liste(vec![el(Genre::Bouton, "Nouveau add"), el(Genre::Bouton, "Supprimer")]);
        assert!(designe(&l, 1, "Nouveau").is_some());
        assert!(designe(&l, 1, "Nouveau devis").is_none(), "the label says more than the name");
        assert!(designe(&l, 1, "Nouv").is_none(), "words, not letters");

        // Cut at 40: the label goes on.
        let l = liste(vec![el(Genre::Bouton, "Exporter toutes les factures du trimestre vers le cabinet comptable")]);
        assert!(designe(&l, 1, "Exporter toutes les factures du trimestre vers le cabinet").is_some());
        assert!(designe(&l, 1, "Exporter toutes les factures du mois").is_none());
        let l = liste(vec![el(Genre::Bouton, "Exporter")]);
        assert!(designe(&l, 1, "Exporter toutes les factures").is_none(), "a short name was not cut");
    }

    #[test]
    fn a_name_that_only_begins_with_the_label_loses_to_the_label_itself() {
        let l = liste(vec![el(Genre::Bouton, "Valider"), el(Genre::Bouton, "Valider et envoyer")]);
        assert!(designe(&l, 2, "Valider").is_none(), "Valider is #1");
        assert!(designe(&l, 1, "Valider").is_some());
        assert!(designe(&l, 2, "Valider et envoyer").is_some());
        let l = liste(vec![el(Genre::Bouton, "Valider et envoyer"), Element { barre_titre: true, ..el(Genre::Bouton, "Valider") }]);
        assert!(designe(&l, 1, "Valider").is_some(), "a title bar's button is no rival");
    }

    #[test]
    fn never_a_title_bars_button_nor_in_a_dialog_that_waits_on_a_choice() {
        let l = liste(vec![
            Element { barre_titre: true, ..el(Genre::Bouton, "Fermer") },
            Element { choix: true, ..el(Genre::Bouton, "Oui") },
            el(Genre::Bouton, "Oui"),
        ]);
        assert!(designe(&l, 1, "Fermer").is_none());
        assert!(designe(&l, 2, "Oui").is_none(), "the person's choice: a bubble only");
        assert!(designe(&l, 3, "Oui").is_some());
    }

    #[test]
    fn the_engines_lines_in_the_images_pixels() {
        let mut l = liste(vec![el(Genre::Bouton, "Nouveau"), el(Genre::BoutonRadio, "TTC"), el(Genre::Champ, "Mot de passe")]);
        l.elements[1].cadre = Cadre { x: 1930, y: 20, largeur: 31, hauteur: 11 };
        assert_eq!(lignes(&l, (0, 0), 1.0)[0], "#1 bouton \"Nouveau\" [10,10,80,30]");
        assert_eq!(lignes(&l, (1920, 0), 0.5)[1], "#2 option \"TTC\" [5,10,16,6]", "the second screen, the image halved");
        assert_eq!(lignes(&l, (0, 0), 1.0).len(), 3);
    }

    #[test]
    fn password_fields_become_zones_of_the_capture() {
        let l = Liste { secrets: vec![Cadre { x: 2000, y: 300, largeur: 160, hauteur: 24 }, Cadre { x: 1900, y: -10, largeur: 40, hauteur: 30 }], ..Liste::default() };
        assert_eq!(
            zones_secretes(&l, (1920, 0)),
            vec![Zone { x: 80, y: 300, largeur: 160, hauteur: 24 }, Zone { x: 0, y: 0, largeur: 20, hauteur: 20 }],
            "on the capture from its corner, what lies before it cut"
        );
        let mut img = crate::shot::tests::rgba(300, 400, [255, 152, 0, 255]);
        crate::shot::paint(&mut img, &zones_secretes(&l, (1920, 0)));
        assert_eq!(crate::shot::black_share(&img), (160.0 * 24.0 + 400.0) / (300.0 * 400.0));
    }

    /// The windows read, in turn (None: the window in front).
    type Lus = Arc<Mutex<Vec<Option<u64>>>>;

    /// A reader of the tests: the first read slow, as UIA's first is.
    struct Faux {
        lus: Lus,
        premier: Duration,
        ensuite: Duration,
        parti: Arc<Mutex<bool>>,
    }

    impl Lire for Faux {
        fn lire(&mut self, cible: Option<&Window>, _cachees: &[Cadre]) -> Result<Liste, Rate> {
            let premier = self.lus.lock().unwrap().is_empty();
            std::thread::sleep(if premier { self.premier } else { self.ensuite });
            self.lus.lock().unwrap().push(cible.map(|w| w.id));
            Ok(liste(vec![el(Genre::Bouton, "Nouveau")]))
        }
    }

    impl Drop for Faux {
        fn drop(&mut self) {
            *self.parti.lock().unwrap() = true;
        }
    }

    fn faux(premier: u64, ensuite: u64) -> (Travailleur, Lus, Arc<Mutex<bool>>) {
        let (lus, parti) = (Arc::new(Mutex::new(Vec::new())), Arc::new(Mutex::new(false)));
        let (l, p) = (lus.clone(), parti.clone());
        let t = Travailleur::demarrer(move || {
            Ok(Faux { lus: l, premier: Duration::from_millis(premier), ensuite: Duration::from_millis(ensuite), parti: p })
        });
        (t, lus, parti)
    }

    fn sage() -> Window {
        Window { id: 42, pid: 7, app: "C:\\Sage\\Sage100.exe".into() }
    }

    fn until(what: &str, done: impl Fn() -> bool) {
        let start = Instant::now();
        while !done() {
            assert!(start.elapsed() < Duration::from_secs(5), "{what}");
            std::thread::sleep(Duration::from_millis(5));
        }
    }

    #[test]
    fn the_reader_reads_the_window_in_front_as_the_guide_opens_so_the_first_list_is_quick() {
        let (t, lus, _) = faux(600, 5);
        until("the read that warms it up", || lus.lock().unwrap().len() == 1);
        assert_eq!(*lus.lock().unwrap(), vec![None], "the window in front, before any list is asked");
        let start = Instant::now();
        let l = demander(&t.envoi, &sage(), &[], Duration::from_secs(3)).unwrap();
        assert!(start.elapsed() < Duration::from_millis(300), "{:?}", start.elapsed());
        assert_eq!((l.elements.len(), lus.lock().unwrap().clone()), (1, vec![None, Some(42)]));
    }

    #[test]
    fn a_list_too_slow_is_none_and_pixels_take_over() {
        let (t, _, _) = faux(0, 2000);
        let start = Instant::now();
        assert_eq!(demander(&t.envoi, &sage(), &[], Duration::from_millis(150)), Err(Rate::Indisponible));
        assert!(start.elapsed() < Duration::from_secs(1));
    }

    #[test]
    fn the_lists_nobody_waits_for_any_more_are_not_read() {
        let (t, lus, _) = faux(0, 300);
        until("warmed up", || lus.lock().unwrap().len() == 1);
        for _ in 0..5 {
            assert_eq!(demander(&t.envoi, &sage(), &[], Duration::from_millis(20)), Err(Rate::Indisponible));
        }
        let start = Instant::now();
        assert!(demander(&t.envoi, &sage(), &[], Duration::from_secs(5)).is_ok());
        assert!(start.elapsed() < Duration::from_millis(1000), "the four given up were read: {:?}", start.elapsed());
        assert_eq!(lus.lock().unwrap().len(), 3, "the warm-up, the one under way, this one");
    }

    #[test]
    fn a_reader_that_cannot_be_made_lists_nothing() {
        let t = Travailleur::demarrer(|| -> Result<Faux, Rate> { Err(Rate::Indisponible) });
        assert_eq!(demander(&t.envoi, &sage(), &[], Duration::from_secs(1)), Err(Rate::Indisponible));
    }

    #[test]
    fn the_reader_goes_with_the_guide() {
        let (t, lus, parti) = faux(0, 0);
        until("warmed up", || lus.lock().unwrap().len() == 1);
        drop(t);
        until("its reader gone with its thread", || *parti.lock().unwrap());
    }

    #[test]
    #[cfg(not(windows))]
    fn elsewhere_than_windows_not_available() {
        ouvrir();
        assert_eq!(elements(&sage(), &[]), Err(Rate::Indisponible));
        assert!(lecteur().is_none(), "no reader made");
        fermer();
    }
}
