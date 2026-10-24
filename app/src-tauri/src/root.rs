//! What the app runs as root, on Linux only, through pkexec: the system's packages of PACKAGES,
//! and Obsidian's official package, the file whose SHA-256 is OBSIDIAN_DEB_SHA256. The system's
//! own window asks the user for their password. The commands are the two fixed scripts below:
//! whatever they are given reaches them as arguments ("$1"…), never as part of their text, and
//! each refuses what it was not written for (a package outside its list, a file of another sum).
//! All that root may do is in this file, the rest of the app cannot widen it.
//! (.github/CODEOWNERS: the maintainer reviews every change of this file.)

use std::path::Path;
use std::time::Duration;

use crate::sys;

/// The system's own way to ask the user for their password and run one command as root.
pub const PKEXEC: &str = "/usr/bin/pkexec";
/// Past this, apt is stuck rather than slow.
const LIMIT: Duration = Duration::from_secs(20 * 60);

/// The only packages root installs: what install.sh's missing_packages may ask for. APT_INSTALL
/// holds the same list, and refuses any other.
pub const PACKAGES: &[&str] = &["python3", "git", "curl", "procps", "unzip", "ca-certificates"];

/// Obsidian's official package, 1.14.4 for amd64, the one install.sh pins: the only file root
/// installs. APT_INSTALL_DEB compares the file with this sum, written in its text.
pub const OBSIDIAN_DEB_SHA256: &str = "85b10dcba6edfc1c0460a6d18260cf31c30447a444bd858a6440b9c9c8806d25";

// The system's password window shows the first 38 and the last 37 characters of the command
// (pkexec 124, Ubuntu 24.04): each script starts with a comment that names what it does, and its
// "$0" is a label long enough that the end shows the label and what is installed, never shell.
const PACKAGES_LABEL: &str = "Brain in a Box installe avec apt-get :";
const OBSIDIAN_LABEL: &str = "Brain in a Box installe Obsidian :";

/// Runs as root, through pkexec. pkexec gives it a clean environment of its own. It first makes
/// sure it is root (apt run as anyone else fails, and the step must say why), then that every
/// package is one of PACKAGES: an argument starting with « - » would be an option of apt's,
/// which can run a command. apt waits for another apt (unattended-upgrades, right after a first
/// start) up to 10 minutes.
const APT_INSTALL: &str = "# Brain in a Box installe :\n\
[ \"$(id -u)\" = 0 ] || { echo \"pas root (uid $(id -u))\" >&2; exit 4; }\n\
[ $# -gt 0 ] || { echo \"aucun paquet\" >&2; exit 5; }\n\
for p; do case \"$p\" in python3|git|curl|procps|unzip|ca-certificates) ;; *) echo \"paquet hors liste : $p\" >&2; exit 5;; esac; done\n\
export DEBIAN_FRONTEND=noninteractive\n\
apt-get update -q || true\n\
apt-get -o DPkg::Lock::Timeout=600 install -y -q -- \"$@\"\n";

/// The package the user downloaded is copied into a folder of root's first, its SHA-256
/// checked there against Obsidian's, and installed from there: nothing of the user's can swap it
/// between the check and apt.
const APT_INSTALL_DEB: &str = "# Brain in a Box : Obsidian\n\
set -eu\n\
[ \"$(id -u)\" = 0 ] || { echo \"pas root (uid $(id -u))\" >&2; exit 4; }\n\
[ $# = 1 ] || { echo \"un seul paquet\" >&2; exit 5; }\n\
d=$(mktemp -d)\n\
trap 'rm -rf \"$d\"' EXIT\n\
cp -- \"$1\" \"$d/paquet.deb\"\n\
if ! echo \"85b10dcba6edfc1c0460a6d18260cf31c30447a444bd858a6440b9c9c8806d25  $d/paquet.deb\" | sha256sum -c --status -; then\n\
  echo \"paquet.deb : somme SHA-256 inattendue, rien n'est installé\" >&2\n\
  exit 3\n\
fi\n\
chmod 755 \"$d\"\n\
chmod 644 \"$d/paquet.deb\"\n\
export DEBIAN_FRONTEND=noninteractive\n\
apt-get update -q || true\n\
apt-get -o DPkg::Lock::Timeout=600 install -y -q -- \"$d/paquet.deb\"\n";

/// `packages`, all of PACKAGES, installed as root. `what` names them for the user, `by_hand` is
/// the command they can type instead.
pub fn install_packages(pkexec: &Path, packages: &[&str], what: &str, by_hand: &str, log: &mut dyn FnMut(String)) -> Result<(), String> {
    if let Some(p) = packages.iter().find(|p| !PACKAGES.contains(p)) {
        return Err(format!("Je n'installe pas « {p} » : ce n'est pas un des outils de base. Envoie le rapport à la personne qui t'a conseillé Bibo."));
    }
    if packages.is_empty() {
        return Ok(());
    }
    run(pkexec, APT_INSTALL, PACKAGES_LABEL, packages, what, by_hand, log)
}

/// Obsidian's package, downloaded at `deb`, installed as root if it is the one OBSIDIAN_DEB_SHA256 names.
pub fn install_obsidian_deb(pkexec: &Path, deb: &Path, by_hand: &str, log: &mut dyn FnMut(String)) -> Result<(), String> {
    run(pkexec, APT_INSTALL_DEB, OBSIDIAN_LABEL, &[&deb.to_string_lossy()], "Obsidian", by_hand, log)
}

/// `script` as root through `pkexec`, with `label` as its "$0" and `args`.
fn run(pkexec: &Path, script: &str, label: &str, args: &[&str], what: &str, by_hand: &str, log: &mut dyn FnMut(String)) -> Result<(), String> {
    if !sys::is_executable(pkexec) {
        return Err(format!(
            "Pour installer {what}, il me faut ton mot de passe, mais ce système ne sait pas me le demander (pas de pkexec). \
             Dans un terminal, tape : {by_hand} puis relance l'étape."
        ));
    }
    let mut argv = vec!["/bin/sh", "-c", script, label];
    argv.extend(args);
    let (mut busy, mut outdated) = (false, false);
    // What stopped it, for the step's sentence: apt's own error (« E: … ») first, else the last line.
    let (mut error, mut last) = (String::new(), String::new());
    // In / : the app's own folder may be its AppImage's, mounted for this user only.
    let code = sys::run_streaming(pkexec, &argv, &[("VOYAGE_API_KEY", None)], Some(Path::new("/")), LIMIT, &mut |line| {
        let said: String = line.trim().chars().take(200).collect();
        // apt's error once it gave up waiting, not its « Waiting for cache lock: Could not get lock… ».
        busy |= said.starts_with("E:") && (said.contains("Could not get lock") || said.contains("is held by process"));
        // A library the package needs at a newer version than the system's, which apt may not upgrade alone.
        outdated |= line.contains("held broken packages") || line.contains("unmet dependencies");
        if said.starts_with("E:") && error.is_empty() {
            error.clone_from(&said);
        }
        if !said.is_empty() {
            last = said;
        }
        log(line);
    });
    let why = if error.is_empty() { last } else { error };
    match code {
        0 => Ok(()),
        // pkexec: 126, the window was closed; 127, no authorization (wrong password, an account
        // that may not administer the computer, a desktop with no window to ask in: no agent).
        126 => Err(format!("La demande de mot de passe a été fermée : je n'ai pas installé {what}. Relance l'étape, puis tape ton mot de passe.")),
        127 => Err(format!(
            "Ton mot de passe n'a pas été accepté, ou ce bureau ne sait pas me le demander, ou ton compte n'a pas le droit \
             d'installer des programmes : je n'ai pas installé {what}. Relance l'étape ; sinon, dans un terminal, tape : {by_hand} \
             puis relance l'étape. Si ton compte n'a pas ce droit, demande à la personne qui s'occupe de cet ordinateur."
        )),
        _ if busy => Err("Les mises à jour du système sont en cours. Attends qu'elles finissent, puis relance l'étape.".into()),
        _ if outdated => Err(format!(
            "Pour installer {what}, ton système doit d'abord être à jour (« {why} »). Ouvre « Mise à jour des logiciels », \
             installe les mises à jour, puis relance l'étape."
        )),
        code => Err(format!(
            "L'installation de {what} s'est arrêtée (code {code}) sur : « {why} ». Relance l'étape ; si ça recommence, \
             envoie le rapport à la personne qui t'a conseillé Bibo."
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;
    use std::path::PathBuf;

    use crate::config_guard;

    fn write_exec(path: &Path, content: &str) {
        fs::write(path, content).unwrap();
        fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
    }

    /// A pkexec that runs its command as this user, with a fake apt-get first on the PATH that
    /// writes what it was given and the package's SHA-256 as it reads it.
    struct Root {
        dir: tempfile::TempDir,
    }

    impl Root {
        fn new(pkexec_exit: Option<i32>) -> Self {
            let dir = tempfile::tempdir().unwrap();
            let bin = dir.path().join("bin");
            fs::create_dir_all(&bin).unwrap();
            let pkexec = match pkexec_exit {
                Some(code) => format!("#!/bin/sh\nexit {code}\n"),
                None => format!("#!/bin/sh\nPATH='{}':/usr/bin:/bin exec \"$@\"\n", bin.display()),
            };
            write_exec(&dir.path().join("pkexec"), &pkexec);
            // root, as pkexec makes the script
            write_exec(&bin.join("id"), "#!/bin/sh\necho 0\n");
            let seen = dir.path().join("apt-get.txt");
            write_exec(
                &bin.join("apt-get"),
                &format!(
                    "#!/bin/sh\nfor a in \"$@\"; do printf '%s\\n' \"$a\"; case \"$a\" in *.deb) sha256sum \"$a\" | cut -c1-64; dirname \"$a\";; esac; done >> '{}'\n",
                    seen.display()
                ),
            );
            Root { dir }
        }

        fn pkexec(&self) -> PathBuf {
            self.dir.path().join("pkexec")
        }

        fn seen(&self) -> String {
            fs::read_to_string(self.dir.path().join("apt-get.txt")).unwrap_or_default()
        }

        /// `run`, again when another test's fork still held the stand-in pkexec open for writing
        /// (Linux: « Text file busy »).
        fn run(&self, script: &str, label: &str, args: &[&str], by_hand: &str) -> Result<(), String> {
            let mut tries = 0;
            loop {
                let res = run(&self.pkexec(), script, label, args, "x", by_hand, &mut |_| {});
                match res {
                    Err(e) if e.contains("Text file busy") && tries < 5 => tries += 1,
                    res => return res,
                }
                std::thread::sleep(Duration::from_millis(50));
            }
        }
    }

    /// pkexec 124's line in the system's password window ($(cmdline_short)): argv joined by spaces,
    /// past 80 bytes its first 38, « ... » and its last 37.
    fn cmdline_short(argv: &[&str]) -> String {
        let line = argv.join(" ");
        let b = line.as_bytes();
        if b.len() <= 80 {
            return line;
        }
        format!("{} ... {}", String::from_utf8(b[..38].to_vec()).unwrap(), String::from_utf8(b[b.len() - 37..].to_vec()).unwrap())
    }

    #[test]
    fn a_package_goes_to_apt_only_from_roots_copy_with_obsidians_sum() {
        let work = tempfile::tempdir().unwrap();
        let deb = work.path().join("obsidian.deb");
        fs::write(&deb, "the package").unwrap();
        let sum = config_guard::sha256_file(&deb).unwrap();
        let deb_s = deb.to_string_lossy();
        // The script as it is, but for a stand-in's sum: the real package is 108 MB.
        let script = APT_INSTALL_DEB.replace(OBSIDIAN_DEB_SHA256, &sum);
        let root = Root::new(None);
        root.run(&script, OBSIDIAN_LABEL, &[&deb_s], "").unwrap();
        let seen = root.seen();
        let said: Vec<&str> = seen.lines().skip_while(|l| *l != "install").collect();
        assert_eq!(said[..4], ["install", "-y", "-q", "--"], "{seen}");
        assert!(said[4].ends_with("/paquet.deb") && !said[4].starts_with(&*work.path().to_string_lossy()), "{seen}");
        assert_eq!(said[5], sum, "apt read the checked bytes");
        assert!(!Path::new(said[6]).exists(), "root's copy is gone");
        assert!(seen.contains("-o\nDPkg::Lock::Timeout=600\ninstall\n"), "{seen}");

        // Changed after the app checked it: apt never sees it.
        let root = Root::new(None);
        fs::write(&deb, "something else").unwrap();
        let err = root.run(&script, OBSIDIAN_LABEL, &[&deb_s], "").unwrap_err();
        assert!(err.contains("code 3"), "{err}");
        assert_eq!(root.seen(), "");
        // The script itself: Obsidian's sum, in its own text, and one file only.
        fs::write(&deb, "the package").unwrap();
        let root = Root::new(None);
        let err = root.run(APT_INSTALL_DEB, OBSIDIAN_LABEL, &[&deb_s], "").unwrap_err();
        assert!(err.contains("code 3"), "{err}");
        assert_eq!(APT_INSTALL_DEB.matches(OBSIDIAN_DEB_SHA256).count(), 1);
        let err = root.run(&script, OBSIDIAN_LABEL, &[&deb_s, &deb_s], "").unwrap_err();
        assert!(err.contains("(code 5) sur : « un seul paquet »"), "{err}");
        assert_eq!(root.seen(), "");
    }

    #[test]
    fn packages_reach_apt_as_arguments_never_as_script_nor_as_options() {
        let root = Root::new(None);
        root.run(APT_INSTALL, PACKAGES_LABEL, &["curl", "git"], "").unwrap();
        assert!(root.seen().ends_with("-o\nDPkg::Lock::Timeout=600\ninstall\n-y\n-q\n--\ncurl\ngit\n"), "{}", root.seen());
        // Anything but PACKAGES is refused by the script before apt: shell, an option of apt's
        // (`-o DPkg::Pre-Invoke::=…` runs a command as root), another package, none at all.
        let odd = "git; touch /tmp/biab-pwned";
        for args in [&["curl", odd][..], &["unzip", "-oDPkg::Pre-Invoke::=id > /tmp/biab-pwned"], &["--allow-unauthenticated", "git"],
            &["openssh-server"], &[]]
        {
            let root = Root::new(None);
            let err = root.run(APT_INSTALL, PACKAGES_LABEL, args, "").unwrap_err();
            assert!(err.contains("(code 5)"), "{args:?}: {err}");
            assert_eq!(root.seen(), "", "{args:?}: apt never ran");
        }
        assert!(!Path::new("/tmp/biab-pwned").exists());
        // The app's side refuses them first, and never asks for the password for nothing.
        let root = Root::new(None);
        let err = install_packages(&root.pkexec(), &["git", "-oX=y"], "git", "", &mut |_| {}).unwrap_err();
        assert!(err.contains("« -oX=y »"), "{err}");
        install_packages(Path::new("/nonexistent/pkexec"), &[], "", "", &mut |_| {}).unwrap();
        assert_eq!(root.seen(), "");
    }

    /// Root goes through this file only: no other source of the app names pkexec's program.
    #[test]
    fn no_other_file_runs_pkexec() {
        let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
        for entry in fs::read_dir(&src).unwrap() {
            let path = entry.unwrap().path();
            if path.file_name().is_some_and(|n| n != "root.rs") && path.extension().is_some_and(|e| e == "rs") {
                let text = fs::read_to_string(&path).unwrap();
                assert!(!text.contains("bin/pkexec"), "{} names pkexec's program: root.rs's PKEXEC only", path.display());
            }
        }
    }

    #[test]
    fn the_scripts_list_is_packages() {
        let case = APT_INSTALL.split("case \"$p\" in ").nth(1).and_then(|r| r.split(')').next()).unwrap();
        assert_eq!(case.split('|').collect::<Vec<_>>(), PACKAGES);
    }

    #[test]
    fn the_password_window_names_what_is_installed() {
        // What Ubuntu 24.04's window shows: the app and what it installs, at both ends, and no shell
        // at the end (cut in a UTF-8 character, it would show garbage: cmdline_short unwraps).
        for pkgs in [&["git"][..], &["curl"], &["git", "curl"], PACKAGES] {
            let mut argv = vec!["/bin/sh", "-c", APT_INSTALL, PACKAGES_LABEL];
            argv.extend(pkgs);
            let shown = cmdline_short(&argv);
            let (head, end) = shown.split_once(" ... ").unwrap();
            assert_eq!(head, "/bin/sh -c # Brain in a Box installe :");
            assert!(format!("{PACKAGES_LABEL} {}", pkgs.join(" ")).ends_with(end), "{end}");
        }
        let shown = cmdline_short(&["/bin/sh", "-c", APT_INSTALL, PACKAGES_LABEL, "git", "curl"]);
        assert_eq!(shown, "/bin/sh -c # Brain in a Box installe : ...  Box installe avec apt-get : git curl");
        let deb = "/home/jo/.brain-in-a-box/.biab-0123456789abcdef/obsidian.deb";
        let shown = cmdline_short(&["/bin/sh", "-c", APT_INSTALL_DEB, OBSIDIAN_LABEL, deb]);
        assert_eq!(shown, "/bin/sh -c # Brain in a Box : Obsidian ... x/.biab-0123456789abcdef/obsidian.deb");
    }

    #[test]
    fn a_script_that_is_not_root_and_apt_s_own_error_are_said() {
        // pkexec that did not make it root: the script stops before apt, and says so.
        let root = Root::new(None);
        fs::remove_file(root.dir.path().join("bin/id")).unwrap();
        let err = root.run(APT_INSTALL, PACKAGES_LABEL, &["git"], "").unwrap_err();
        assert!(err.contains("(code 4) sur : « pas root (uid "), "{err}");
        assert_eq!(root.seen(), "", "apt never ran");
        // apt's first error is the one the sentence quotes (apt writes them on stderr: one pipe, in order).
        let root = Root::new(None);
        write_exec(
            &root.dir.path().join("bin/apt-get"),
            "#!/bin/sh\n[ \"$1\" = update ] && exit 0\necho 'Reading package lists...'\necho 'E: Unable to locate package curl' >&2\necho 'E: second' >&2\nexit 100\n",
        );
        let err = root.run(APT_INSTALL, PACKAGES_LABEL, &["curl"], "").unwrap_err();
        assert!(err.contains("(code 100) sur : « E: Unable to locate package curl »"), "{err}");
    }

    #[test]
    fn apt_that_waited_for_the_lock_is_not_busy_one_that_gave_up_is() {
        let root = Root::new(None);
        let waiting = "echo 'Waiting for cache lock: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 9 (unattended-upgr)...' >&2";
        write_exec(
            &root.dir.path().join("bin/apt-get"),
            &format!("#!/bin/sh\n[ \"$3\" = install ] || exit 0\n{waiting}\necho 'E: Unable to locate package git' >&2\nexit 100\n"),
        );
        let err = root.run(APT_INSTALL, PACKAGES_LABEL, &["git"], "").unwrap_err();
        assert!(err.contains("(code 100) sur : « E: Unable to locate package git »"), "{err}");
        write_exec(
            &root.dir.path().join("bin/apt-get"),
            &format!("#!/bin/sh\n[ \"$3\" = install ] || exit 0\n{waiting}\necho 'E: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 9 (unattended-upgr)' >&2\nexit 100\n"),
        );
        let err = root.run(APT_INSTALL, PACKAGES_LABEL, &["git"], "").unwrap_err();
        assert!(err.contains("Les mises à jour du système sont en cours"), "{err}");
    }

    #[test]
    fn a_system_to_update_first_is_said_so() {
        let root = Root::new(None);
        write_exec(
            &root.dir.path().join("bin/apt-get"),
            "#!/bin/sh\n[ \"$1\" = update ] && exit 0\necho 'The following packages have unmet dependencies:' >&2\n\
             echo ' curl : Depends: libcurl4t64 (= 8.5.0-2ubuntu10.16) but 8.5.0-2ubuntu10.15 is to be installed' >&2\n\
             echo 'E: Unable to correct problems, you have held broken packages.' >&2\nexit 100\n",
        );
        let err = root.run(APT_INSTALL, PACKAGES_LABEL, &["curl"], "").unwrap_err();
        assert!(err.contains("ton système doit d'abord être à jour") && err.contains("held broken packages"), "{err}");
        // pkexec starts it in /, not in the app's folder.
        let root = Root::new(None);
        write_exec(&root.dir.path().join("bin/apt-get"), &format!("#!/bin/sh\npwd >> '{}'\n", root.dir.path().join("cwd").display()));
        root.run(APT_INSTALL, PACKAGES_LABEL, &["git"], "").unwrap();
        assert_eq!(fs::read_to_string(root.dir.path().join("cwd")).unwrap(), "/\n/\n");
    }

    #[test]
    fn the_password_window_says_why_nothing_was_installed() {
        let by_hand = "sudo apt-get install -y git";
        for (code, words) in [(126, "a été fermée"), (127, "pas été accepté"), (1, "code 1")] {
            let root = Root::new(Some(code));
            let err = root.run(APT_INSTALL, PACKAGES_LABEL, &["git"], by_hand).unwrap_err();
            assert!(err.contains(words), "{code}: {err}");
            // No agent to ask (WSLg, ssh -X, a bare window manager) is 127 too: the command to type.
            assert_eq!(err.contains(by_hand), code == 127, "{code}: {err}");
        }
        let err = install_packages(Path::new("/nonexistent/pkexec"), &["git"], "git", by_hand, &mut |_| {}).unwrap_err();
        assert!(err.contains(by_hand), "{err}");
    }
}
