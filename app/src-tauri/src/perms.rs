//! Private files and folders, their modes and owners.
//!
//! On a Mac these are Unix permissions: 0700 folders, 0600 files, the owner's uid.
//! Windows has neither modes nor uids. Everything the app writes lives in the user's
//! profile (its work folders included: sys::work_root, never a TEMP that may be shared),
//! whose access list keeps the other accounts out and passes on to what is created in it. There a folder is private by being where
//! it is, every file counts as the user's own, and a mode only says read-only or not.

use std::fs::{self, File, Metadata, OpenOptions};
use std::io;
use std::path::Path;

#[cfg(unix)]
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt, PermissionsExt};

/// The owner of every file on Windows (see the module's note).
#[cfg(windows)]
const PROFILE_OWNER: u32 = 0;

/// A folder only the user can open (0700). Its parent must exist.
pub fn create_dir(path: &Path) -> io::Result<()> {
    private_builder().create(path)
}

/// The same, missing parents included.
pub fn create_dir_all(path: &Path) -> io::Result<()> {
    private_builder().recursive(true).create(path)
}

fn private_builder() -> fs::DirBuilder {
    #[cfg_attr(not(unix), allow(unused_mut))]
    let mut b = fs::DirBuilder::new();
    #[cfg(unix)]
    b.mode(0o700);
    b
}

/// A new file (never an existing one), created with `mode`.
pub fn create_new(path: &Path, mode: u32) -> io::Result<File> {
    let mut o = OpenOptions::new();
    o.write(true).create_new(true);
    with_mode(&mut o, mode);
    o.open(path)
}

/// A file to append to, created with `mode` if missing; an existing one gets `mode` back.
pub fn append(path: &Path, mode: u32) -> io::Result<File> {
    let mut o = OpenOptions::new();
    o.append(true).create(true);
    with_mode(&mut o, mode);
    let f = o.open(path)?;
    set_mode(path, mode)?;
    Ok(f)
}

#[cfg(unix)]
fn with_mode(o: &mut OpenOptions, mode: u32) {
    o.mode(mode);
}

#[cfg(windows)]
fn with_mode(_: &mut OpenOptions, _: u32) {}

/// Unix permission bits. On Windows only "not writable" means something: the read-only flag.
#[cfg(unix)]
pub fn set_mode(path: &Path, mode: u32) -> io::Result<()> {
    fs::set_permissions(path, fs::Permissions::from_mode(mode))
}

#[cfg(windows)]
pub fn set_mode(path: &Path, mode: u32) -> io::Result<()> {
    let mut p = fs::metadata(path)?.permissions();
    p.set_readonly(mode & 0o200 == 0);
    fs::set_permissions(path, p)
}

/// The permission bits, as `set_mode` takes them. On Windows: 0o400 read-only, else 0o600.
#[cfg(unix)]
pub fn mode(m: &Metadata) -> u32 {
    m.permissions().mode() & 0o7777
}

#[cfg(windows)]
pub fn mode(m: &Metadata) -> u32 {
    if m.permissions().readonly() {
        0o400
    } else {
        0o600
    }
}

/// The owner's uid on a Mac; on Windows, the same for every file (see the module's note).
#[cfg(unix)]
pub fn owner(m: &Metadata) -> u32 {
    m.uid()
}

#[cfg(windows)]
pub fn owner(_: &Metadata) -> u32 {
    PROFILE_OWNER
}

/// A symbolic link at `link` to `target`.
#[cfg(unix)]
pub fn symlink(target: &Path, link: &Path) -> io::Result<()> {
    std::os::unix::fs::symlink(target, link)
}

/// Windows has two kinds of links, for a folder and for a file: a target that is gone
/// gets a file link.
#[cfg(windows)]
pub fn symlink(target: &Path, link: &Path) -> io::Result<()> {
    let resolved = link.parent().map_or_else(|| target.to_path_buf(), |p| p.join(target));
    if resolved.is_dir() {
        std::os::windows::fs::symlink_dir(target, link)
    } else {
        std::os::windows::fs::symlink_file(target, link)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn private_files_and_folders() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path().join("a/b");
        assert!(create_dir(&dir).is_err(), "no parent");
        create_dir_all(&dir).unwrap();
        create_dir(&dir.join("c")).unwrap();
        let file = dir.join("f");
        create_new(&file, 0o600).unwrap().write_all(b"x").unwrap();
        assert!(create_new(&file, 0o600).is_err(), "never an existing file");
        let m = fs::metadata(&file).unwrap();
        assert_eq!(mode(&m), 0o600);
        assert_eq!(owner(&m), owner(&fs::metadata(tmp.path()).unwrap()));
        set_mode(&file, 0o400).unwrap();
        assert_eq!(mode(&fs::metadata(&file).unwrap()), 0o400);
        set_mode(&file, 0o600).unwrap();
        let mut log = append(&dir.join("log"), 0o600).unwrap();
        writeln!(log, "one").unwrap();
        drop(log);
        writeln!(append(&dir.join("log"), 0o600).unwrap(), "two").unwrap();
        assert_eq!(fs::read_to_string(dir.join("log")).unwrap(), "one\ntwo\n");
        #[cfg(unix)]
        for d in [&dir, &dir.join("c")] {
            assert_eq!(mode(&fs::metadata(d).unwrap()), 0o700);
        }
    }
}
