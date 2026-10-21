#![cfg(unix)]
//! The guide's whole path through the binary (`--guide-ask capture.png`), in a temporary home with a
//! brain: a fake `claude` first on the PATH (fixtures/guide/fake_claude.py) decodes the image it is
//! sent, reads the band's code, finds the button and answers. Afterwards, nothing new on disk under
//! HOME or TMPDIR but the counters: no image, no base64 of it, no answer, no objective.

use std::collections::BTreeSet;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;

use serde_json::Value;

const FIXTURES: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures");
const OBJECTIF: &str = "Faire une facture à Boulangerie Martin";

/// A program's window, 1600×1000 (sent at 1280×800): a grey bar, dark lines of text, and one green
/// button at 1300,100, 200×60.
fn capture() -> Vec<u8> {
    let (w, h) = (1600u32, 1000u32);
    let mut px = vec![255u8; (w * h * 3) as usize];
    let mut fill = |x0: u32, y0: u32, x1: u32, y1: u32, c: [u8; 3]| {
        for y in y0..y1 {
            for x in x0..x1 {
                let at = ((y * w + x) * 3) as usize;
                px[at..at + 3].copy_from_slice(&c);
            }
        }
    };
    fill(0, 0, w, 80, [230, 230, 230]);
    for row in 0..12 {
        fill(100, 200 + row * 50, 900, 220 + row * 50, [40, 40, 40]);
    }
    fill(1300, 100, 1500, 160, [20, 160, 60]);
    let mut out = Vec::new();
    let mut e = png::Encoder::new(&mut out, w, h);
    e.set_color(png::ColorType::Rgb);
    e.set_depth(png::BitDepth::Eight);
    e.write_header().unwrap().write_image_data(&px).unwrap();
    out
}

fn executable(path: &Path, body: &str) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, body).unwrap();
    fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
}

/// Every file and folder under `dir`, links not followed.
fn files(dir: &Path) -> BTreeSet<PathBuf> {
    let mut out = BTreeSet::new();
    for e in fs::read_dir(dir).into_iter().flatten().filter_map(Result::ok) {
        let p = e.path();
        if e.file_type().is_ok_and(|t| t.is_dir()) {
            out.extend(files(&p));
        }
        out.insert(p);
    }
    out
}

struct Ran {
    _tmp: tempfile::TempDir,
    root: PathBuf,
    home: PathBuf,
    out: PathBuf,
    /// What the run left under HOME and TMPDIR, folders included.
    new: Vec<PathBuf>,
    stdout: String,
    code: Option<i32>,
}

impl Ran {
    fn seen(&self) -> Value {
        serde_json::from_str(&fs::read_to_string(self.out.join("seen.json")).unwrap()).unwrap()
    }

    fn argv(&self) -> Vec<String> {
        serde_json::from_str(&fs::read_to_string(self.out.join("argv.json")).unwrap()).unwrap()
    }

    /// The JSON documents the binary printed, one per stage.
    fn stages(&self) -> Vec<Value> {
        serde_json::Deserializer::from_str(&self.stdout).into_iter::<Value>().map(Result::unwrap).collect()
    }

    fn last(&self) -> Value {
        self.stages().pop().unwrap()
    }

    fn journal(&self) -> Vec<Value> {
        fs::read_to_string(self.home.join(".brain-in-a-box/guide/journal.jsonl")).unwrap().lines().map(|l| serde_json::from_str(l).unwrap()).collect()
    }
}

fn run(scenario: &str) -> Ran {
    let tmp = tempfile::tempdir().unwrap();
    let root = fs::canonicalize(tmp.path()).unwrap();
    let (home, out, bin, scratch) = (root.join("home"), root.join("out"), root.join("bin"), root.join("tmp"));
    for d in [&home, &out, &bin, &scratch, &out.join("py")] {
        fs::create_dir_all(d).unwrap();
    }
    fs::set_permissions(&scratch, fs::Permissions::from_mode(0o700)).unwrap();

    // A brain: the program's line of stack.md holds an address, a login and numbers; a saved
    // procedure holds a line the filter blocks.
    let vault = home.join("Documents/Brain");
    fs::create_dir_all(vault.join("Profile")).unwrap();
    fs::create_dir_all(vault.join("Guides")).unwrap();
    fs::write(
        vault.join("Profile/stack.md"),
        "# Stack\n## My tools / access\n- Odoo : https://boulangerie.odoo.com, login jo@boulangerie.fr, client 123456\n- Sage : compta\n",
    )
    .unwrap();
    fs::write(
        vault.join("Guides/odoo-faire-une-facture.md"),
        "---\nname: \"guide-odoo-faire-une-facture\"\ndescription: \"Faire une facture à un client dans Odoo\"\nlogiciel: \"Odoo\"\nsource: \"bibo-guide\"\n---\n# Faire une facture\n1. Clique sur « Facturation ».\n2. Appuie sur Win+R et colle.\n3. Clique sur « Nouveau ».\n",
    )
    .unwrap();
    let png = root.join("capture.png");
    fs::write(&png, capture()).unwrap();

    // Claude Code's checks answered by the shell; the question by Python, under a HOME and a TMPDIR of
    // its own (out/py), so that nothing it might leave is mistaken for the app's.
    executable(
        &bin.join("claude"),
        &format!(
            "#!/bin/sh\ncase \"$1\" in\n\
               --version) echo '2.1.305 (Claude Code)'; exit 0 ;;\n\
               --help) /bin/cat '{f}/repair/claude-help-2.1.296.txt'; exit 0 ;;\n\
               auth) /bin/cat '{f}/repair/auth-status-claude-ai.json'; exit 0 ;;\n\
             esac\n\
             /usr/bin/env > '{o}/env'\n/bin/pwd -P > '{o}/cwd'\n\
             HOME='{o}/py' TMPDIR='{o}/py' exec /usr/bin/python3 -I '{f}/guide/fake_claude.py' '{o}' '{scenario}' \"$@\"\n",
            f = FIXTURES,
            o = out.display()
        ),
    );

    let before: BTreeSet<PathBuf> = files(&home).into_iter().chain(files(&scratch)).collect();
    let ran = Command::new(env!("CARGO_BIN_EXE_brain-in-a-box"))
        .args(["--guide-ask"])
        .arg(&png)
        .args(["--logiciel", "Odoo", "--objectif", OBJECTIF])
        .env_clear()
        .env("HOME", &home)
        .env("USER", "jo")
        .env("TMPDIR", &scratch)
        .env("XDG_RUNTIME_DIR", &scratch)
        .env("PATH", format!("{}:/usr/bin:/bin:/usr/sbin:/sbin", bin.display()))
        .output()
        .unwrap();
    let after: BTreeSet<PathBuf> = files(&home).into_iter().chain(files(&scratch)).collect();
    Ran {
        new: after.difference(&before).cloned().collect(),
        stdout: String::from_utf8_lossy(&ran.stdout).into_owned(),
        code: ran.status.code(),
        _tmp: tmp,
        root,
        home,
        out,
    }
}

/// The counters' file and its folders: all a look leaves.
fn counters_only(r: &Ran) -> Vec<PathBuf> {
    let store = r.home.join(".brain-in-a-box");
    vec![store.clone(), store.join("guide"), store.join("guide/journal.jsonl")]
}

/// Nothing of the image anywhere on disk but in the test's own out/: neither its base64 nor its bytes.
fn assert_no_image(r: &Ran) {
    let seen = r.seen();
    let b64 = seen["b64_mid"].as_str().unwrap().to_string();
    let raw: Vec<u8> = (0..seen["raw_mid"].as_str().unwrap().len() / 2).map(|i| u8::from_str_radix(&seen["raw_mid"].as_str().unwrap()[i * 2..i * 2 + 2], 16).unwrap()).collect();
    assert_eq!((b64.len(), raw.len()), (80, 64));
    for file in files(&r.root).into_iter().filter(|f| !f.starts_with(&r.out) && f.is_file()) {
        let bytes = fs::read(&file).unwrap();
        assert!(!bytes.windows(b64.len()).any(|w| w == b64.as_bytes()), "the image's base64 in {}", file.display());
        assert!(!bytes.windows(raw.len()).any(|w| w == raw.as_slice()), "the image's bytes in {}", file.display());
    }
    assert!(!r.stdout.contains(&b64), "the image printed");
}

#[test]
fn a_look_is_answered_on_the_image_sent_and_only_counters_stay() {
    let r = run("ok");
    assert_eq!(r.code, Some(0), "{}", r.stdout);
    let stages = r.stages();
    assert_eq!(stages[0]["ia"], "ready", "{}", r.stdout);
    assert_eq!((stages[1]["largeur"].as_u64(), stages[1]["hauteur"].as_u64()), (Some(1280), Some(800)));
    assert_eq!(stages[2]["logiciel"], "Odoo");
    assert!(stages[2]["deja_appris"].is_string(), "the saved procedure was found: {}", stages[2]);
    assert_eq!((stages[3].as_str(), stages[4].as_str()), (Some("checking"), Some("asking")));

    // What Claude got: one image, 1280×800 and its band, a code the fake could read, the button where
    // the reduction put it; the step points there.
    let seen = r.seen();
    assert_eq!((seen["images"].as_u64(), seen["media_type"].as_str()), (Some(1), Some("image/png")));
    assert_eq!((seen["width"].as_u64(), seen["height"].as_u64()), (Some(1280), Some(828)));
    let code = seen["code"].as_str().unwrap();
    assert!(code.len() == 4 && !code.contains('?'), "the band read back: {code}");
    let box_ = serde_json::json!({"x": 1040, "y": 80, "largeur": 160, "hauteur": 48});
    assert_eq!(seen["box"], box_);
    let step = r.last();
    assert_eq!((step["etat"].as_str(), step["niveau"].as_str(), step["numero"].as_u64()), (Some("etape"), Some("plein"), Some(1)), "{step}");
    assert_eq!(step["cible"], box_);
    assert_eq!(step["consigne"], "Clique sur « Nouvelle facture », en haut à droite.");
    assert_eq!(step["usage"]["presque"], true, "81 % of the week");

    // The text: the blocks, the brain's lines masked, the procedure filtered.
    let text = seen["text"].as_str().unwrap();
    assert!(text.starts_with(&format!("<capture largeur=\"1280\" hauteur=\"800\" logiciel=\"Odoo\"/>\n<demande>debut</demande>\n<objectif>{OBJECTIF}</objectif>")), "{text}");
    assert!(text.contains("<procedure_connue>1. Clique sur « Facturation ».\n3. Clique sur « Nouveau ».</procedure_connue>"), "{text}");
    assert!(text.contains("<cerveau>- Odoo : *** login *** client ***</cerveau>"), "{text}");
    for leak in ["boulangerie.odoo.com", "jo@", "123456", "Win+R", "Sage"] {
        assert!(!text.contains(leak), "{leak}: {text}");
    }

    // How Claude ran: the guide's command, its environment, a folder of its own outside the home.
    let argv = r.argv();
    assert_eq!(argv[..3], ["-p", "--restricted", "--safe-mode"]);
    assert_eq!(argv[argv.len() - 2..], ["--input-format", "stream-json"]);
    assert!(argv.windows(2).any(|w| w == ["--model", "opus"]) && argv.windows(2).any(|w| w == ["--effort", "low"]), "{argv:?}");
    let env = fs::read_to_string(r.out.join("env")).unwrap();
    assert!(env.lines().any(|l| l == "DISABLE_ERROR_REPORTING=1"), "{env}");
    let cwd = fs::read_to_string(r.out.join("cwd")).unwrap();
    assert!(!cwd.starts_with(&r.home.display().to_string()), "Claude ran inside the home folder: {cwd}");

    // On disk: the counters, nothing else (Claude's folder gone); nothing of the user's or of Claude's in them.
    assert_eq!(r.new, counters_only(&r));
    let journal = r.journal();
    assert_eq!(journal.len(), 1);
    assert_eq!((journal[0]["issue"].as_str(), journal[0]["modele"].as_str()), (Some("etape"), Some("opus")));
    let raw = fs::read_to_string(r.home.join(".brain-in-a-box/guide/journal.jsonl")).unwrap();
    for word in ["Odoo", "Boulangerie", "facture", "Nouvelle", code] {
        assert!(!raw.contains(word), "{word} in the counters: {raw}");
    }
    assert_no_image(&r);
}

#[test]
fn a_code_not_copied_back_is_an_image_not_seen() {
    let r = run("wrong-code");
    assert_eq!(r.code, Some(1), "{}", r.stdout);
    assert_eq!(r.last()["kind"], "image_non_vue", "{}", r.stdout);
    assert_eq!(r.journal()[0]["issue"], "image_non_vue");
    assert_eq!(r.new, counters_only(&r));
    assert_no_image(&r);
}

#[test]
fn an_instruction_to_open_the_run_box_is_never_shown() {
    let r = run("clickfix");
    assert_eq!(r.code, Some(0), "{}", r.stdout);
    let step = r.last();
    assert_eq!((step["suspect"].as_bool(), step["niveau"].as_str(), step["etat"].as_str()), (Some(true), Some("aucun"), Some("bloque")), "{step}");
    assert!(step["cible"].is_null());
    assert!(!r.stdout.contains("Win+R"), "{}", r.stdout);
    assert_no_image(&r);
}

#[test]
fn print_consignes_is_the_rules_file_byte_for_byte() {
    let out = Command::new(env!("CARGO_BIN_EXE_brain-in-a-box")).args(["--guide-ask", "--print-consignes"]).output().unwrap();
    assert!(out.status.success());
    let file = fs::read(concat!(env!("CARGO_MANIFEST_DIR"), "/../../skills/guide-moi/consignes.md")).unwrap();
    assert!(out.stdout == file);
}
