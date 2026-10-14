#![cfg(unix)]
//! The whole repair path through the binary (`--repair-step brain --yes`), in a
//! temporary home: a brain archive whose install.sh fails, a fake `claude` first on
//! the PATH that replays a recorded answer. Nothing of the real home is touched.

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use std::process::Command;

const CANARY: &str = "pa-CANARI0123456789abcdef";
const FIXTURES: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/repair");

fn executable(path: &Path, body: &str) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, body).unwrap();
    fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
}

/// install.sh runs git and python3: the brain step stops before it without Apple's tools.
fn apple_tools() -> bool {
    Command::new("/usr/bin/xcode-select")
        .arg("-p")
        .output()
        .ok()
        .filter(|o| o.status.success())
        .is_some_and(|o| Path::new(String::from_utf8_lossy(&o.stdout).trim()).join("usr/bin/git").is_file())
}

#[test]
fn a_failed_brain_step_is_analyzed_repaired_and_nothing_leaks() {
    if !apple_tools() {
        eprintln!("no Apple command line tools: skipped");
        return;
    }
    let tmp = tempfile::tempdir().unwrap();
    let root = fs::canonicalize(tmp.path()).unwrap();
    let (home, out, bin, scratch) = (root.join("home"), root.join("out"), root.join("bin"), root.join("tmp"));
    for d in [&home, &out, &bin, &scratch] {
        fs::create_dir_all(d).unwrap();
    }
    executable(&home.join(".bun/bin/bun"), "#!/bin/sh\necho 1.4.2\n");

    // The archive: an install.sh that prints what a careless installer could, then fails.
    let src = root.join("archive/brain-in-a-box-main");
    fs::create_dir_all(src.join("vault-skeleton")).unwrap();
    executable(
        &src.join("install.sh"),
        "#!/bin/bash\necho '==> bun install'\n\
         echo 'error: GET https://registry.npmjs.org/pglite - ConnectionRefused'\n\
         echo \"+ export VOYAGE_API_KEY=$VOYAGE_API_KEY\"\n\
         echo 'remote: token ghp_0123456789abcdefghijABCDEF'\n\
         echo \"Cloning into '/Users/jo/DEV/gbrain'...\"\n\
         exit 1\n",
    );
    let zip = root.join("brain.zip");
    let made = Command::new("/usr/bin/ditto")
        .args(["-c", "-k", "--keepParent"])
        .arg(&src)
        .arg(&zip)
        .status()
        .unwrap();
    assert!(made.success());

    executable(
        &bin.join("claude"),
        &format!(
            "#!/bin/sh\nout='{out}'\n\
             case \"$1\" in\n\
               --version) echo '2.1.296 (Claude Code)'; exit 0 ;;\n\
               --help) /bin/cat '{FIXTURES}/claude-help-2.1.296.txt'; exit 0 ;;\n\
               auth) /bin/cat '{FIXTURES}/auth-status-claude-ai.json'; exit 0 ;;\n\
             esac\n\
             printf '%s\\n' \"$@\" > \"$out/argv\"\n/usr/bin/env > \"$out/env\"\n/bin/pwd -P > \"$out/cwd\"\n\
             /bin/cat > \"$out/stdin\"\n/bin/cat '{FIXTURES}/plan-ok.jsonl'\n",
            out = out.display()
        ),
    );

    let run = Command::new(env!("CARGO_BIN_EXE_brain-in-a-box"))
        .args(["--repair-step", "brain", "--yes"])
        .env_clear()
        .env("HOME", &home)
        .env("USER", "jo")
        .env("TMPDIR", &scratch)
        .env("PATH", format!("{}:/usr/bin:/bin:/usr/sbin:/sbin", bin.display()))
        .env("BIAB_ARCHIVE", &zip)
        .env("VOYAGE_API_KEY", CANARY)
        .output()
        .unwrap();
    let stdout = String::from_utf8_lossy(&run.stdout).into_owned();
    if stdout.contains("\"offline\"") {
        eprintln!("no network to claude.ai: skipped\n{stdout}");
        return;
    }

    // Analyzed, the plan applied with --yes, the step relaunched: it still fails.
    assert!(stdout.contains("\"plan_id\"") && stdout.contains("\"cause_id\": \"reseau\""), "{stdout}");
    assert!(stdout.contains("\"outcome\": \"still_failing\""), "{stdout}");
    assert_eq!(run.status.code(), Some(1));
    assert!(stdout.contains("ConnectionRefused") && stdout.contains("~/DEV/gbrain"), "{stdout}");

    let read = |name: &str| fs::read_to_string(out.join(name)).unwrap_or_default();
    let (stdin, argv, env) = (read("stdin"), read("argv"), read("env"));
    assert!(stdin.contains("<journal>") && stdin.contains("ConnectionRefused"), "{stdin}");
    assert!(argv.starts_with("-p\n--restricted\n--safe-mode\n") && !argv.contains("ConnectionRefused"));
    assert!(!read("cwd").starts_with(&home.display().to_string()), "Claude ran inside the home folder");

    let mut seen = vec![("stdout", stdout), ("stdin", stdin), ("argv", argv), ("env", env)];
    let journal = fs::read_to_string(home.join(".brain-in-a-box/reparations/journal.jsonl")).unwrap();
    seen.push(("journal", journal));
    for (name, text) in &seen {
        for secret in ["CANARI", "ghp_0123456789", "/Users/jo"] {
            assert!(!text.contains(secret), "{secret} leaked into {name}:\n{text}");
        }
    }
}
