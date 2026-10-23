//! W1 in a browser (C1): the app is the browser AND the site of its active tab. The site is read in the
//! address bar by UI Automation, on the PC, before and after each capture; another site is a pause. The
//! tab strip (the other tabs' titles and icons) is painted black.

use std::mem::ManuallyDrop;

use windows::core::Result;
use windows::Win32::Foundation::{HWND, RECT};
use windows::Win32::System::Com::{CoCreateInstance, CoInitializeEx, CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED};
use windows::Win32::System::Variant::{VARIANT, VARIANT_0, VARIANT_0_0, VARIANT_0_0_0, VT_I4};
use windows::Win32::UI::Accessibility::{
    CUIAutomation, IUIAutomation, IUIAutomationElement, IUIAutomationValuePattern, TreeScope_Descendants, UIA_CONTROLTYPE_ID, UIA_ControlTypePropertyId,
    UIA_EditControlTypeId, UIA_TabControlTypeId, UIA_ValuePatternId,
};

use crate::compose::{composer, Composite};
use crate::prims::{frame, Physical};

pub struct Navigateur {
    uia: IUIAutomation,
    fenetre: IUIAutomationElement,
    barre: Option<IUIAutomationElement>,
    pub front: HWND,
    pub pid: u32,
}

fn int(v: i32) -> VARIANT {
    VARIANT { Anonymous: VARIANT_0 { Anonymous: ManuallyDrop::new(VARIANT_0_0 { vt: VT_I4, Anonymous: VARIANT_0_0_0 { lVal: v }, ..Default::default() }) } }
}

/// The site in what the address bar shows (« odoo.example.com/web#… », with or without its scheme).
pub fn hote(url: &str) -> Option<String> {
    let s = url.trim();
    let s = s.split_once("://").map_or(s, |(_, r)| r);
    let s = s.split(['/', '?', '#']).next()?;
    let s = s.rsplit_once('@').map_or(s, |(_, h)| h);
    let host = s.split(':').next()?.to_ascii_lowercase();
    (!host.is_empty() && host.contains('.') && host.chars().all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-')).then_some(host)
}

#[derive(Debug, PartialEq)]
pub enum Refus {
    /// Another site, or none read: a pause.
    Domaine(Option<String>),
    /// The tab strip not found: nothing is sent rather than the other tabs.
    Bande,
    Image,
}

impl Navigateur {
    pub fn new(front: HWND, pid: u32) -> Result<Self> {
        // SAFETY: COM for this thread, in the multithreaded apartment UI Automation prefers.
        let _ = unsafe { CoInitializeEx(None, COINIT_MULTITHREADED) };
        let uia: IUIAutomation = unsafe { CoCreateInstance(&CUIAutomation, None, CLSCTX_INPROC_SERVER)? };
        let fenetre = unsafe { uia.ElementFromHandle(front)? };
        Ok(Navigateur { uia, fenetre, barre: None, front, pid })
    }

    fn premier(&self, kind: UIA_CONTROLTYPE_ID) -> Result<IUIAutomationElement> {
        // SAFETY: COM calls on objects of this thread.
        unsafe {
            let cond = self.uia.CreatePropertyCondition(UIA_ControlTypePropertyId, &int(kind.0))?;
            self.fenetre.FindFirst(TreeScope_Descendants, &cond)
        }
    }

    /// The address bar's element (the first edit field of the window: the toolbar comes before the page).
    pub fn barre(&mut self) -> Option<(String, String)> {
        if self.barre.is_none() {
            self.barre = self.premier(UIA_EditControlTypeId).ok();
        }
        let b = self.barre.as_ref()?;
        // SAFETY: COM calls on an element of this thread.
        unsafe { Some((b.CurrentName().ok()?.to_string(), b.CurrentClassName().ok()?.to_string())) }
    }

    /// The site of the active tab, read now; None when it cannot be read.
    pub fn domaine(&mut self) -> Option<String> {
        if self.barre.is_none() {
            self.barre = self.premier(UIA_EditControlTypeId).ok();
        }
        let b = self.barre.as_ref()?;
        // SAFETY: as above.
        let read = unsafe { b.GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId).and_then(|v| v.CurrentValue()) };
        match read {
            Ok(url) => hote(&url.to_string()),
            Err(_) => {
                // Found again at the next look.
                self.barre = None;
                None
            }
        }
    }

    /// The element at a point of the screen: its name, class and control type (the proofs' diagnostics).
    pub fn element_au(&self, (x, y): (i32, i32)) -> Option<(String, String, i32)> {
        let _physical = Physical::enter();
        // SAFETY: COM calls on objects of this thread.
        unsafe {
            let e = self.uia.ElementFromPoint(windows::Win32::Foundation::POINT { x, y }).ok()?;
            Some((e.CurrentName().ok()?.to_string(), e.CurrentClassName().ok()?.to_string(), e.CurrentControlType().ok()?.0))
        }
    }

    /// The tab strip, on the screen (physical pixels).
    pub fn bande(&self) -> Option<RECT> {
        let _physical = Physical::enter();
        let tab = self.premier(UIA_TabControlTypeId).ok()?;
        // SAFETY: as above.
        let r = unsafe { tab.CurrentBoundingRectangle() }.ok()?;
        (r.right > r.left && r.bottom > r.top).then_some(r)
    }

    /// One look at the browser: the site checked before and after the capture, the tab strip black.
    pub fn regarder(&mut self, permis: &str) -> std::result::Result<Composite, Refus> {
        self.regarder_avec(permis, || {})
    }

    /// The same, with `entre` run between the first read of the site and the capture (the proofs: the
    /// tab changes there).
    pub fn regarder_avec(&mut self, permis: &str, entre: impl FnOnce()) -> std::result::Result<Composite, Refus> {
        let avant = self.domaine();
        if avant.as_deref() != Some(permis) {
            return Err(Refus::Domaine(avant));
        }
        entre();
        let bande = self.bande().ok_or(Refus::Bande)?;
        let mut c = composer(self.pid, self.front).ok_or(Refus::Image)?;
        masquer_bande(&mut c, bande, frame(self.front));
        let apres = self.domaine();
        if apres.as_deref() != Some(permis) {
            return Err(Refus::Domaine(apres));
        }
        Ok(c)
    }
}

/// The tab strip's band, across the whole window.
pub fn masquer_bande(c: &mut Composite, bande: RECT, fenetre: Option<RECT>) {
    let (l, r) = fenetre.map_or((bande.left, bande.right), |f| (f.left.min(bande.left), f.right.max(bande.right)));
    let z = c.zone(RECT { left: l, top: bande.top, right: r, bottom: bande.bottom });
    c.img.fill(z, [0, 0, 0]);
}
