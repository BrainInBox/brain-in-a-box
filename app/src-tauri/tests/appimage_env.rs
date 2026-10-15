#![cfg(target_os = "linux")]
//! The app run from an AppImage: its AppRun gives the app the AppImage's own Python home,
//! libraries and GTK modules. install.sh, which the brain step runs, must get the session's
//! environment, not those: with the AppImage's PYTHONHOME the system's python3 does not start,
//! and the brain step stopped on « python3 is , the kit needs 3.9 or newer » (terrain, 2026-10-15).

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use std::process::Command;

fn executable(path: &Path, body: &str) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, body).unwrap();
    fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
}

#[test]
fn install_sh_gets_none_of_the_appimages_environment() {
    let tools = ["python3", "git", "curl", "pgrep", "unzip"];
    if !tools.iter().all(|t| ["/usr/bin", "/bin"].iter().any(|d| Path::new(d).join(t).is_file())) {
        eprintln!("the system's tools are not all there: skipped");
        return;
    }
    let tmp = tempfile::tempdir().unwrap();
    let root = fs::canonicalize(tmp.path()).unwrap();
    let (home, out, run) = (root.join("home"), root.join("out"), root.join("run"));
    for d in [&home, &out, &run] {
        fs::create_dir_all(d).unwrap();
    }
    fs::set_permissions(&run, fs::Permissions::from_mode(0o700)).unwrap();
    executable(&home.join(".bun/bin/bun"), "#!/bin/sh\necho 1.4.3\n");

    // What AppImageKit's AppRun and linuxdeploy's GTK hook set, a python3 of the AppImage's own first on the PATH.
    let appdir = root.join("Brain in a Box.AppDir");
    executable(&appdir.join("AppRun"), "#!/bin/sh\n");
    executable(&appdir.join("usr/bin/python3"), "#!/bin/sh\necho 'the AppImage python3' >&2; exit 9\n");
    fs::create_dir_all(appdir.join("usr/lib")).unwrap();
    let a = appdir.display().to_string();

    // install.sh as a stand-in: what it gets, and the system's python3 starting.
    let src = root.join("archive/brain-in-a-box-master");
    fs::create_dir_all(src.join("vault-skeleton")).unwrap();
    executable(
        &src.join("install.sh"),
        &format!(
            "#!/bin/bash\n/usr/bin/env > '{o}/env'\ncommand -v python3 > '{o}/which'\n\
             python3 -c 'import encodings, sys; print(sys.version_info >= (3, 9))' > '{o}/python3' 2>&1\nexit 1\n",
            o = out.display()
        ),
    );
    let zip = root.join("brain.zip");
    let made = Command::new("/usr/bin/python3").args(["-I", "-m", "zipfile", "-c"]).arg(&zip).arg(&src).status().unwrap();
    assert!(made.success());

    let run_step = Command::new(env!("CARGO_BIN_EXE_brain-in-a-box"))
        .args(["--run-step", "brain"])
        .env_clear()
        .env("HOME", &home)
        .env("USER", "jo")
        .env("XDG_RUNTIME_DIR", &run)
        .env("BIAB_ARCHIVE", &zip)
        .env("APPDIR", &appdir)
        .env("APPIMAGE", root.join("Brain in a Box.AppImage"))
        .env("PATH", format!("{a}/usr/bin/:{a}/usr/sbin/:/usr/bin:/bin"))
        .env("PYTHONHOME", format!("{a}/usr/"))
        .env("PYTHONPATH", format!("{a}/usr/share/pyshared/:"))
        .env("LD_LIBRARY_PATH", format!("{a}/usr/lib/:/opt/mine/lib"))
        .env("XDG_DATA_DIRS", format!("{a}/usr/share:/usr/share"))
        .env("GDK_PIXBUF_MODULE_FILE", format!("{a}//usr/lib/gdk-pixbuf-2.0/2.10.0/loaders.cache"))
        .env("GTK_THEME", "Adwaita:light")
        .output()
        .unwrap();
    let said = String::from_utf8_lossy(&run_step.stdout);
    let read = |name: &str| fs::read_to_string(out.join(name)).unwrap_or_else(|_| panic!("install.sh did not run: {said}"));
    let env = read("env");
    assert!(!env.contains(&a), "the AppImage's folder reached install.sh:\n{env}");
    for gone in ["APPDIR=", "APPIMAGE=", "PYTHONHOME=", "PYTHONPATH=", "GDK_PIXBUF_MODULE_FILE=", "GTK_THEME="] {
        assert!(!env.lines().any(|l| l.starts_with(gone)), "{gone}\n{env}");
    }
    assert!(env.lines().any(|l| l == "LD_LIBRARY_PATH=/opt/mine/lib"), "the user's own entries stay:\n{env}");
    assert!(env.lines().any(|l| l == "XDG_DATA_DIRS=/usr/share"), "{env}");
    assert_eq!(read("which").trim(), "/usr/bin/python3");
    assert_eq!(read("python3").trim(), "True", "the system's python3 did not start");
}
