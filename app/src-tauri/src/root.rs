//! What the app runs as root, on Linux only: the system's packages (git, python3, curl) and
//! Obsidian's official package, through pkexec. The system's own window asks the user for
//! their password and names the command; the commands are these two fixed scripts, whatever
//! they are given reaches them as arguments ("$1"…), never as part of their text.
//! (.github/CODEOWNERS: the maintainer reviews every change of this file.)

use std::path::Path;
use std::time::Duration;

use crate::sys;

/// The system's own way to ask the user for their password and run one command as root.
pub const PKEXEC: &str = "/usr/bin/pkexec";
/// Past this, apt is stuck rather than slow.
const LIMIT: Duration = Duration::from_secs(20 * 60);

/// Runs as root, through pkexec: the system's own window asks for the user's password and
/// names the command. The script is a constant of this file; `args` reach it as "$1"…, never
/// through its text. pkexec gives it a clean environment of its own. Each script first makes
/// sure it is root: apt run as anyone else fails, and the step must say why.
pub const APT_INSTALL: &str = "[ \"$(id -u)\" = 0 ] || { echo \"pas root (uid $(id -u))\" >&2; exit 4; }\n\
export DEBIAN_FRONTEND=noninteractive\n\
apt-get update -q || true\n\
apt-get install -y -q \"$@\"\n";

/// The package the user downloaded is copied into a folder of root's first, its SHA-256
/// checked there, and installed from there: nothing of the user's can swap it between the
/// check and apt.
pub const APT_INSTALL_DEB: &str = "set -eu\n\
[ \"$(id -u)\" = 0 ] || { echo \"pas root (uid $(id -u))\" >&2; exit 4; }\n\
d=$(mktemp -d)\n\
trap 'rm -rf \"$d\"' EXIT\n\
cp -- \"$1\" \"$d/paquet.deb\"\n\
if ! printf '%s  %s\\n' \"$2\" \"$d/paquet.deb\" | sha256sum -c --status -; then\n\
  echo \"paquet.deb : somme SHA-256 inattendue, rien n'est installé\" >&2\n\
  exit 3\n\
fi\n\
chmod 755 \"$d\"\n\
chmod 644 \"$d/paquet.deb\"\n\
export DEBIAN_FRONTEND=noninteractive\n\
apt-get update -q || true\n\
apt-get install -y -q \"$d/paquet.deb\"\n";

/// `script` as root through `pkexec`, with `args`. `what` names what it installs, `by_hand`
/// is the command the user can type instead, for a system without pkexec.
pub fn run(pkexec: &Path, script: &str, args: &[&str], what: &str, by_hand: &str, log: &mut dyn FnMut(String)) -> Result<(), String> {
    if !sys::is_executable(pkexec) {
        return Err(format!(
            "Pour installer {what}, il me faut ton mot de passe, mais ce système ne sait pas me le demander (pas de pkexec). \
             Dans un terminal, tape : {by_hand} puis relance l'étape."
        ));
    }
    let mut argv = vec!["/bin/sh", "-c", script, "sh"];
    argv.extend(args);
    let mut busy = false;
    // What stopped it, for the step's sentence: apt's own error (« E: … ») first, else the last line.
    let (mut error, mut last) = (String::new(), String::new());
    let code = sys::run_streaming(pkexec, &argv, &[("VOYAGE_API_KEY", None)], None, LIMIT, &mut |line| {
        busy |= line.contains("Could not get lock") || line.contains("is held by process");
        let said: String = line.trim().chars().take(200).collect();
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
        // pkexec: 126, the window was closed; 127, no authorization (wrong password, an
        // account that may not administer the computer, no window to ask in).
        126 => Err(format!("La demande de mot de passe a été fermée : je n'ai pas installé {what}. Relance l'étape, puis tape ton mot de passe.")),
        127 => Err(format!(
            "Ton mot de passe n'a pas été accepté, ou ton compte n'a pas le droit d'installer des programmes : je n'ai pas installé {what}. \
             Relance l'étape ; sinon, demande à la personne qui s'occupe de cet ordinateur."
        )),
        _ if busy => Err("Les mises à jour du système sont en cours. Attends qu'elles finissent, puis relance l'étape.".into()),
        code => Err(format!(
            "L'installation de {what} s'est arrêtée (code {code}) sur : « {why} ». Relance l'étape ; si ça recommence, \
             copie le rapport et envoie-le à l'équipe."
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
    }

    #[test]
    fn a_package_goes_to_apt_only_from_roots_copy_with_the_pinned_sum() {
        let root = Root::new(None);
        let work = tempfile::tempdir().unwrap();
        let deb = work.path().join("obsidian.deb");
        fs::write(&deb, "the package").unwrap();
        let sum = config_guard::sha256_file(&deb).unwrap();
        let mut lines = Vec::new();
        let deb_s = deb.to_string_lossy();
        run(&root.pkexec(), APT_INSTALL_DEB, &[&deb_s, &sum], "Obsidian", "", &mut |l| lines.push(l)).unwrap();
        let seen = root.seen();
        let said: Vec<&str> = seen.lines().skip_while(|l| *l != "install").collect();
        assert_eq!(said[..2], ["install", "-y"], "{seen}");
        assert!(said[3].ends_with("/paquet.deb") && !said[3].starts_with(&*work.path().to_string_lossy()), "{seen}");
        assert_eq!(said[4], sum, "apt read the checked bytes");
        assert!(!Path::new(said[5]).exists(), "root's copy is gone");

        // Changed after the app checked it: apt never sees it.
        let root = Root::new(None);
        fs::write(&deb, "something else").unwrap();
        let err = run(&root.pkexec(), APT_INSTALL_DEB, &[&deb_s, &sum], "Obsidian", "", &mut |l| lines.push(l)).unwrap_err();
        assert!(err.contains("code 3"), "{err}");
        assert_eq!(root.seen(), "");
    }

    #[test]
    fn packages_reach_apt_as_arguments_never_as_script() {
        let root = Root::new(None);
        let odd = "git; touch /tmp/biab-pwned";
        run(&root.pkexec(), APT_INSTALL, &["curl", odd], "curl", "", &mut |_| {}).unwrap();
        let seen = root.seen();
        assert!(seen.contains("install\n-y\n-q\ncurl\ngit; touch /tmp/biab-pwned\n"), "{seen}");
        assert!(!Path::new("/tmp/biab-pwned").exists());
    }

    #[test]
    fn a_script_that_is_not_root_and_apt_s_own_error_are_said() {
        // pkexec that did not make it root: the script stops before apt, and says so.
        let root = Root::new(None);
        fs::remove_file(root.dir.path().join("bin/id")).unwrap();
        let err = run(&root.pkexec(), APT_INSTALL, &["git"], "git", "", &mut |_| {}).unwrap_err();
        assert!(err.contains("(code 4) sur : « pas root (uid "), "{err}");
        assert_eq!(root.seen(), "", "apt never ran");
        // apt's first error is the one the sentence quotes (apt writes them on stderr: one pipe, in order).
        let root = Root::new(None);
        write_exec(
            &root.dir.path().join("bin/apt-get"),
            "#!/bin/sh\n[ \"$1\" = update ] && exit 0\necho 'Reading package lists...'\necho 'E: Unable to locate package gitt' >&2\necho 'E: second' >&2\nexit 100\n",
        );
        let err = run(&root.pkexec(), APT_INSTALL, &["gitt"], "gitt", "", &mut |_| {}).unwrap_err();
        assert!(err.contains("(code 100) sur : « E: Unable to locate package gitt »"), "{err}");
    }

    #[test]
    fn the_password_window_says_why_nothing_was_installed() {
        for (code, words) in [(126, "a été fermée"), (127, "pas été accepté"), (1, "code 1")] {
            let root = Root::new(Some(code));
            let err = run(&root.pkexec(), APT_INSTALL, &["git"], "git", "", &mut |_| {}).unwrap_err();
            assert!(err.contains(words), "{code}: {err}");
        }
        let err = run(Path::new("/nonexistent/pkexec"), APT_INSTALL, &["git"], "git", "sudo apt-get install -y git", &mut |_| {})
            .unwrap_err();
        assert!(err.contains("sudo apt-get install -y git"), "{err}");
    }

}
