//! The screen's scale (100, 125, 150 %), changed live on the VM, as the Settings app does: the
//! DisplayConfig packets -3 (get) and -4 (set) Windows' own settings use, undocumented. A resolution
//! large enough first: Windows offers no 150 % on a small screen.

use std::mem::size_of;

use windows::Win32::Devices::Display::{
    DisplayConfigGetDeviceInfo, DisplayConfigSetDeviceInfo, GetDisplayConfigBufferSizes, QueryDisplayConfig, DISPLAYCONFIG_DEVICE_INFO_HEADER,
    DISPLAYCONFIG_DEVICE_INFO_TYPE, DISPLAYCONFIG_MODE_INFO, DISPLAYCONFIG_PATH_INFO, QDC_ONLY_ACTIVE_PATHS,
};
use windows::Win32::Foundation::{LUID, POINT};
use windows::Win32::Graphics::Gdi::{
    ChangeDisplaySettingsExW, EnumDisplaySettingsW, MonitorFromPoint, CDS_TYPE, DEVMODEW, DISP_CHANGE_SUCCESSFUL, DM_PELSHEIGHT, DM_PELSWIDTH, ENUM_CURRENT_SETTINGS,
    ENUM_DISPLAY_SETTINGS_MODE, MONITOR_DEFAULTTOPRIMARY,
};
use windows::Win32::UI::HiDpi::{GetDpiForMonitor, MDT_EFFECTIVE_DPI};

const PAS: [u32; 12] = [100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500];

#[repr(C)]
struct Lire {
    header: DISPLAYCONFIG_DEVICE_INFO_HEADER,
    min: i32,
    cur: i32,
    max: i32,
}

#[repr(C)]
struct Poser {
    header: DISPLAYCONFIG_DEVICE_INFO_HEADER,
    rel: i32,
}

fn source() -> Option<(LUID, u32)> {
    let (mut np, mut nm) = (0u32, 0u32);
    // SAFETY: counts, then arrays of those sizes.
    unsafe { GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, &mut np, &mut nm) }.ok().ok()?;
    let mut paths = vec![DISPLAYCONFIG_PATH_INFO::default(); np as usize];
    let mut modes = vec![DISPLAYCONFIG_MODE_INFO::default(); nm as usize];
    unsafe { QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, &mut np, paths.as_mut_ptr(), &mut nm, modes.as_mut_ptr(), None) }.ok().ok()?;
    paths.first().map(|p| (p.sourceInfo.adapterId, p.sourceInfo.id))
}

/// (recommended, current, largest) scale of the first screen, in percent.
pub fn lire() -> Option<(u32, u32, u32)> {
    let (adapter, id) = source()?;
    let mut get = Lire {
        header: DISPLAYCONFIG_DEVICE_INFO_HEADER { r#type: DISPLAYCONFIG_DEVICE_INFO_TYPE(-3), size: size_of::<Lire>() as u32, adapterId: adapter, id },
        min: 0,
        cur: 0,
        max: 0,
    };
    // SAFETY: the packet's size in its header.
    if unsafe { DisplayConfigGetDeviceInfo(&mut get.header) } != 0 {
        return None;
    }
    let rec = get.min.unsigned_abs() as usize;
    let at = |rel: i32| PAS.get((rec as i32 + rel).max(0) as usize).copied();
    Some((at(0)?, at(get.cur)?, at(get.max)?))
}

pub fn poser(pct: u32) -> bool {
    let (Some((adapter, id)), Some((rec, _, _))) = (source(), lire()) else { return false };
    let (Some(want), Some(base)) = (PAS.iter().position(|&p| p == pct), PAS.iter().position(|&p| p == rec)) else { return false };
    let set = Poser {
        header: DISPLAYCONFIG_DEVICE_INFO_HEADER { r#type: DISPLAYCONFIG_DEVICE_INFO_TYPE(-4), size: size_of::<Poser>() as u32, adapterId: adapter, id },
        rel: want as i32 - base as i32,
    };
    // SAFETY: as above.
    unsafe { DisplayConfigSetDeviceInfo(&set.header) == 0 }
}

/// The primary screen's DPI as programs aware of it see it (96 = 100 %).
pub fn dpi() -> u32 {
    let (mut x, mut y) = (0u32, 0u32);
    // SAFETY: plain values.
    let m = unsafe { MonitorFromPoint(POINT { x: 0, y: 0 }, MONITOR_DEFAULTTOPRIMARY) };
    let _ = unsafe { GetDpiForMonitor(m, MDT_EFFECTIVE_DPI, &mut x, &mut y) };
    x
}

fn devmode(i: ENUM_DISPLAY_SETTINGS_MODE) -> Option<DEVMODEW> {
    let mut d = DEVMODEW { dmSize: size_of::<DEVMODEW>() as u16, ..Default::default() };
    // SAFETY: a DEVMODEW of its size.
    unsafe { EnumDisplaySettingsW(None, i, &mut d) }.as_bool().then_some(d)
}

pub fn resolution() -> Option<(u32, u32)> {
    devmode(ENUM_CURRENT_SETTINGS).map(|d| (d.dmPelsWidth, d.dmPelsHeight))
}

pub fn modes() -> Vec<(u32, u32)> {
    let mut all: Vec<(u32, u32)> = (0..512).map_while(|i| devmode(ENUM_DISPLAY_SETTINGS_MODE(i))).map(|d| (d.dmPelsWidth, d.dmPelsHeight)).collect();
    all.sort();
    all.dedup();
    all
}

pub fn poser_resolution(w: u32, h: u32) -> bool {
    let Some(mut d) = devmode(ENUM_CURRENT_SETTINGS) else { return false };
    d.dmPelsWidth = w;
    d.dmPelsHeight = h;
    d.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT;
    // SAFETY: a DEVMODEW read from the system, two fields changed; not saved in the registry.
    unsafe { ChangeDisplaySettingsExW(None, Some(&d), None, CDS_TYPE(0), None) == DISP_CHANGE_SUCCESSFUL }
}
