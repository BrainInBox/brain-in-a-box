//! The report of an install that got stuck, on the user's Desktop (rapport-bibo.txt), for the person
//! who advised Bibo: the window says where it is, the user sends the file. What the window built goes
//! through the same mask as Claude's dossier first: no key, no token, the user's name written ~.

#[cfg(any(target_os = "linux", test))]
use std::fs;
use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::{repair, steps, sys};

pub const FILE: &str = "rapport-bibo.txt";
/// A report is a few dozen lines: anything bigger is not one.
const MAX_BYTES: usize = 1_000_000;

/// Where the report went, and the window's sentence for it.
#[derive(Serialize, Debug)]
pub struct Saved {
    pub path: String,
    pub message: String,
}

/// Writes `text`, masked, after a header that says what the file is, to the Desktop (the home folder when
/// there is none, or when the system keeps the app out of it) and says where.
pub fn save(text: &str) -> Result<Saved, String> {
    let home = sys::home();
    let mask = repair::masker();
    let desk = desktop(&home);
    let file = save_in(&[desk.clone(), home.clone()], &format!("{}{}", header(repair::now_secs()), mask(text)))?;
    Ok(Saved { message: saved_message(&file, file.parent() == Some(desk.as_path()), &home), path: file.display().to_string() })
}

/// The window's sentence: « sur ton Bureau » only when it is there.
fn saved_message(file: &Path, on_desktop: bool, home: &Path) -> String {
    let send = "Envoie-le à la personne qui t'a conseillé Bibo.";
    if on_desktop {
        return format!("Le rapport est sur ton Bureau. {send}");
    }
    let shown = file.strip_prefix(home).map_or_else(|_| file.display().to_string(), |rel| rel.display().to_string());
    format!("Le rapport est dans ton dossier personnel ({shown}). {send}")
}

/// What the file is, for whoever opens it: when, which app, which system; that it holds no secret.
fn header(secs: u64) -> String {
    let (y, mo, d, h, mi, _) = repair::civil(secs);
    let system = match std::env::consts::OS {
        "windows" => "Windows",
        "macos" => "macOS",
        _ => "Linux",
    };
    format!(
        "Rapport d'installation de Bibo, le {y:04}-{mo:02}-{d:02} à {h:02}:{mi:02} (UTC), version {}, {system}.\n\
         Ni mot de passe ni clé dedans : envoie-le à la personne qui t'a conseillé Bibo.\n\n",
        env!("CARGO_PKG_VERSION")
    )
}

/// The first of `dirs` that takes the file. The text is already masked.
fn save_in(dirs: &[PathBuf], text: &str) -> Result<PathBuf, String> {
    if text.len() > MAX_BYTES {
        return Err("Ce rapport est bien trop long : je ne l'enregistre pas.".into());
    }
    let body = if cfg!(windows) { text.replace("\r\n", "\n").replace('\n', "\r\n") } else { text.to_string() };
    let mut why = String::new();
    for dir in dirs.iter().filter(|d| d.is_dir()) {
        let file = dir.join(FILE);
        match steps::write_atomic(&file, body.as_bytes(), 0o600) {
            Ok(()) => return Ok(file),
            Err(e) => why = e.to_string(),
        }
    }
    Err(format!("Je n'ai pas pu enregistrer le rapport ({why})."))
}

/// The Desktop as the system names it: Windows' known folder (OneDrive may hold it), the folder of
/// ~/.config/user-dirs.dirs on Linux (« Bureau » in French), ~/Desktop on a Mac.
#[cfg(target_os = "macos")]
fn desktop(home: &Path) -> PathBuf {
    home.join("Desktop")
}

#[cfg(target_os = "linux")]
fn desktop(home: &Path) -> PathBuf {
    let config = std::env::var_os("XDG_CONFIG_HOME").map(PathBuf::from).filter(|p| p.is_absolute()).unwrap_or_else(|| home.join(".config"));
    fs::read_to_string(config.join("user-dirs.dirs")).ok().and_then(|t| xdg_desktop(&t, home)).unwrap_or_else(|| home.join("Desktop"))
}

/// XDG_DESKTOP_DIR="$HOME/Bureau", as xdg-user-dirs writes it: the home folder or an absolute path.
#[cfg(any(target_os = "linux", all(test, unix)))]
fn xdg_desktop(dirs: &str, home: &Path) -> Option<PathBuf> {
    let value = dirs.lines().find_map(|l| l.trim().strip_prefix("XDG_DESKTOP_DIR="))?.trim().trim_matches('"');
    let path = match value.strip_prefix("$HOME") {
        Some(rest) => home.join(rest.trim_start_matches('/')),
        None => PathBuf::from(value),
    };
    (path.is_absolute() && path != home && !value.contains("..")).then_some(path)
}

#[cfg(windows)]
fn desktop(home: &Path) -> PathBuf {
    known::desktop().unwrap_or_else(|| home.join("Desktop"))
}

#[cfg(windows)]
mod known {
    //! FOLDERID_Desktop, where Explorer shows the Desktop: OneDrive may have moved it.
    use std::ffi::{c_void, OsString};
    use std::os::windows::ffi::OsStringExt;
    use std::path::PathBuf;
    use std::ptr::null_mut;

    #[repr(C)]
    struct Guid {
        data1: u32,
        data2: u16,
        data3: u16,
        data4: [u8; 8],
    }

    /// B4BFCC3A-DB2C-424C-B029-7FE99A87C641.
    const DESKTOP: Guid = Guid { data1: 0xB4BF_CC3A, data2: 0xDB2C, data3: 0x424C, data4: [0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41] };

    #[link(name = "shell32")]
    extern "system" {
        fn SHGetKnownFolderPath(id: *const Guid, flags: u32, token: *mut c_void, path: *mut *mut u16) -> i32;
    }
    #[link(name = "ole32")]
    extern "system" {
        fn CoTaskMemFree(mem: *mut c_void);
    }

    pub fn desktop() -> Option<PathBuf> {
        let mut found: *mut u16 = null_mut();
        // SAFETY: a GUID that lives for the call; Windows hands back a string we free once, even on failure.
        let hr = unsafe { SHGetKnownFolderPath(&DESKTOP, 0, null_mut(), &mut found) };
        let path = (hr == 0 && !found.is_null()).then(|| {
            // SAFETY: a NUL-terminated wide string Windows allocated.
            let len = (0..).take_while(|&i| unsafe { *found.add(i) } != 0).count();
            PathBuf::from(OsString::from_wide(unsafe { std::slice::from_raw_parts(found, len) }))
        });
        // SAFETY: allocated by SHGetKnownFolderPath (null is allowed).
        unsafe { CoTaskMemFree(found.cast()) };
        path
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The report lands whole, masked as the window built it, on the first folder that takes it.
    #[test]
    fn the_report_lands_on_the_desktop_masked() {
        let dir = tempfile::tempdir().unwrap();
        let desk = dir.path().join("Bureau");
        fs::create_dir(&desk).unwrap();
        let text = "Échec à l'étape « brain »\nclé pa-abcdefghijklmnopqrstuvwxyz0123 /Users/jo.doe/Documents/Brain sk-ant-api03-secret-value\n";
        let masked = crate::redact::redact(text, &[]);
        let file = save_in(&[desk.clone(), dir.path().to_path_buf()], &masked).unwrap();
        assert_eq!(file, desk.join(FILE));
        let written = fs::read_to_string(&file).unwrap();
        for secret in ["pa-abcdefghijklmnopqrstuvwxyz0123", "sk-ant-api03", "jo.doe"] {
            assert!(!written.contains(secret), "{secret} in {written}");
        }
        assert!(written.contains("Échec à l'étape « brain »") && written.contains("~/Documents/Brain"), "{written}");
        assert_eq!(written.contains("\r\n"), cfg!(windows));
        // Again: replaced, not a second file.
        save_in(std::slice::from_ref(&desk), "deux").unwrap();
        assert_eq!(fs::read_to_string(desk.join(FILE)).unwrap(), "deux");
        assert_eq!(fs::read_dir(&desk).unwrap().count(), 1);
    }

    /// No Desktop: the home folder; nowhere to write: said, in French.
    #[test]
    fn without_a_desktop_the_home_folder() {
        let dir = tempfile::tempdir().unwrap();
        let file = save_in(&[dir.path().join("absent"), dir.path().to_path_buf()], "x").unwrap();
        assert_eq!(file, dir.path().join(FILE));
        let err = save_in(&[dir.path().join("absent")], "x").unwrap_err();
        assert!(err.starts_with("Je n'ai pas pu enregistrer le rapport"), "{err}");
        assert!(save_in(&[dir.path().to_path_buf()], &"x".repeat(MAX_BYTES + 1)).is_err());
    }

    /// The file says what it is to whoever opens it; the window says where it is: the Desktop, or the home
    /// folder when there was none (never « sur ton Bureau » then).
    #[test]
    fn the_report_says_what_it_is_and_where() {
        let h = header(0);
        assert!(h.starts_with("Rapport d'installation de Bibo, le 1970-01-01 à 00:00 (UTC), version "), "{h}");
        assert!(h.contains("Ni mot de passe ni clé") && h.contains("conseillé Bibo") && h.ends_with("\n\n"), "{h}");
        let home = Path::new("/home/jo");
        assert_eq!(saved_message(&home.join("Bureau").join(FILE), true, home), "Le rapport est sur ton Bureau. Envoie-le à la personne qui t'a conseillé Bibo.");
        let m = saved_message(&home.join(FILE), false, home);
        assert!(m.starts_with("Le rapport est dans ton dossier personnel") && m.contains(FILE) && !m.contains("Bureau"), "{m}");
    }

    #[cfg(unix)]
    #[test]
    fn the_linux_desktop_is_the_one_user_dirs_names() {
        let home = Path::new("/home/jo");
        let dirs = "# written by xdg-user-dirs-update\nXDG_DOWNLOAD_DIR=\"$HOME/Téléchargements\"\nXDG_DESKTOP_DIR=\"$HOME/Bureau\"\n";
        assert_eq!(xdg_desktop(dirs, home), Some(home.join("Bureau")));
        assert_eq!(xdg_desktop("XDG_DESKTOP_DIR=\"/data/desk\"\n", home), Some(PathBuf::from("/data/desk")));
        for odd in ["", "XDG_DESKTOP_DIR=\"$HOME/\"\n", "XDG_DESKTOP_DIR=\"desk\"\n", "XDG_DESKTOP_DIR=\"$HOME/../x\"\n"] {
            assert_eq!(xdg_desktop(odd, home), None, "{odd:?}");
        }
    }

    /// The real Desktop, where Explorer shows it (OneDrive may have moved it).
    #[cfg(windows)]
    #[test]
    fn the_windows_desktop_is_explorers() {
        let d = known::desktop().expect("Windows names its Desktop");
        assert!(d.is_absolute(), "{}", d.display());
        assert_eq!(desktop(&sys::home()), d);
    }
}
