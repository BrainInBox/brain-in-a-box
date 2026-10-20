//! « Importer ma mémoire »: what ChatGPT or Claude knows of the user, brought into the brain by the
//! kit's importer-memoire.py, the copy the install put next to the hooks, run with the kit's Python
//! and `--json` (one line per event, never any text of the export but `voir`'s, on purpose).
//!
//! The window never gives a path nor a command. A file comes from the system's own picker, opened
//! here (a regular file, not a link) and handed to the importer as its standard input; a pasted
//! answer goes there as text. The importer reads either as hostile data, masks the secrets, makes a
//! plan and says what would leave and the agreement code of each choice. The codes stay here: the
//! window says how many requests, and `lancer` gets the code the importer gave for that number, so
//! what leaves is exactly what the importer showed.
//!
//! One import at a time from the app (and the importer keeps its own lock). « Arrêter » kills the
//! importer and what it started in its group; once it writes the note, it is let finish.

use std::ffi::OsStr;
use std::fs::File;
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{LazyLock, Mutex};
use std::time::Duration;

use regex::Regex;
use serde::Serialize;
use serde_json::{Map, Value};

use crate::sys::{self, Fed, Input};
use crate::{agents, detect, redact};

/// Reading an export: a large one takes minutes in Python.
const READ_LIMIT: Duration = Duration::from_secs(30 * 60);
/// Each request: the importer stops one at 10 minutes.
const REQUEST_LIMIT: Duration = Duration::from_secs(12 * 60);
const SHORT_LIMIT: Duration = Duration::from_secs(60);
/// `voir` prints a request on one line: 100 000 characters, escaped.
const LINE_CAP: usize = 8 << 20;
const FILES_MAX: usize = 4;
/// The importer's own ceiling on a file.
const FILE_MAX: u64 = 8 << 30;
/// The importer's ceiling on a paste (COLLAGE_MAX characters), as UTF-8 at worst.
const PASTE_MAX: usize = 300_000 * 4;
const APPELS_MAX: u32 = 20;
/// Errors the importer gives for one file of several that the rest makes up for: already imported,
/// Claude's account data alone, Claude's file of links.
const SKIPPED_WITH_OTHERS: [&str; 3] = ["deja", "compte", "liens"];

static ID: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"\A[0-9a-f]{12}\z").ok());
static NOTE: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"\AImports/[0-9A-Za-z][0-9A-Za-z._-]{0,100}\.md\z").ok());

/// What the window shows when something stops: a word and a sentence in French.
#[derive(Serialize, Clone, Debug, PartialEq, Eq)]
pub struct ImportError {
    /// The importer's code ("deja", "pas_un_zip", "auth", "quota"…) or the app's: "a_jour" (the brain
    /// predates the import), "cerveau_absent", "python", "occupe", "arrete", "plan", "interne".
    pub code: String,
    pub message: String,
}

impl ImportError {
    fn new(code: &str, message: &str) -> ImportError {
        ImportError { code: code.into(), message: message.into() }
    }
}

/// The plan the importer made, as the window gets it: what was read and the choices, without their
/// agreement codes.
pub type PlanView = Value;
/// What the importer wrote: the note, the facts by category, what was set aside.
pub type Outcome = Value;

struct Plan {
    id: String,
    /// (requests, agreement code) of each choice the importer offered.
    choices: Vec<(u32, String)>,
}

#[derive(Default)]
struct Session {
    plan: Option<Plan>,
    /// The note of the last import, relative to the brain ("Imports/2026-10-20-import-chatgpt.md").
    note: Option<String>,
}

static SESSION: Mutex<Session> = Mutex::new(Session { plan: None, note: None });
static BUSY: AtomicBool = AtomicBool::new(false);
static CANCEL: AtomicBool = AtomicBool::new(false);
/// The importer is writing the note: stopping it now could leave half of it.
static WRITING: AtomicBool = AtomicBool::new(false);

fn session() -> std::sync::MutexGuard<'static, Session> {
    SESSION.lock().unwrap_or_else(|p| p.into_inner())
}

/// Held while the importer runs: a second click waits for nothing, it is told.
struct Busy;

impl Busy {
    fn take() -> Result<Busy, ImportError> {
        if BUSY.swap(true, Ordering::SeqCst) {
            return Err(ImportError::new("occupe", "Un import est déjà en cours : attends qu'il finisse."));
        }
        CANCEL.store(false, Ordering::SeqCst);
        WRITING.store(false, Ordering::SeqCst);
        Ok(Busy)
    }
}

impl Drop for Busy {
    fn drop(&mut self) {
        WRITING.store(false, Ordering::SeqCst);
        BUSY.store(false, Ordering::SeqCst);
    }
}

/// The importer as the install put it, and where it works.
pub struct Kit {
    python: PathBuf,
    script: PathBuf,
    brain: PathBuf,
    /// Added to the app's environment (the tests' home and stand-ins).
    env: Vec<(String, std::ffi::OsString)>,
}

impl Kit {
    /// The user's: the installed importer, the kit's Python, the brain the install made.
    pub fn for_user() -> Result<Kit, ImportError> {
        let home = sys::home();
        let script = home.join(".claude").join("hooks").join("brain").join("importer-memoire.py");
        let brain = detect::vault(&home).ok_or_else(|| ImportError::new("cerveau_absent", "Ton cerveau n'est pas encore installé."))?;
        if !script.is_file() {
            return Err(ImportError::new("a_jour", "Ton cerveau a été installé avant l'import de mémoire : je dois d'abord le mettre à jour."));
        }
        let python = agents::kit_python().ok_or_else(|| ImportError::new("python", "Python n'est pas là : relance l'installation de ton cerveau."))?;
        Ok(Kit { python, script, brain, env: Vec::new() })
    }

    /// Runs `importer-memoire <args> --brain B --json`. Progress lines go to `progress`, the others
    /// are returned; an `erreur` line is the error.
    fn run(&self, args: &[&str], input: Input, limit: Duration, progress: &mut dyn FnMut(Value)) -> Result<Vec<Value>, ImportError> {
        let brain = self.brain.to_string_lossy();
        let script = self.script.to_string_lossy();
        let mut argv = vec!["-I", "-B", script.as_ref()];
        argv.extend_from_slice(args);
        argv.extend(["--brain", brain.as_ref(), "--json"]);
        let env: Vec<(&str, &OsStr)> = self.env.iter().map(|(k, v)| (k.as_str(), v.as_os_str())).collect();
        let fed = Fed { env: &env, limit, cancel: &CANCEL, line_cap: LINE_CAP };
        let mut events = Vec::new();
        let code = sys::run_fed(&self.python, &argv, &fed, input, &mut |line| {
            let Ok(Value::Object(ev)) = serde_json::from_str::<Value>(&line) else { return };
            if ev.get("etape").and_then(Value::as_str) == Some("ecriture") {
                WRITING.store(true, Ordering::SeqCst);
            }
            if ev.contains_key("etape") {
                progress(Value::Object(ev));
            } else {
                events.push(Value::Object(ev));
            }
        })
        .map_err(|e| ImportError { code: "interne".into(), message: format!("Je n'ai pas pu lancer l'import ({e}).") })?;
        if let Some(e) = events.iter().find_map(|e| e.get("erreur")) {
            return Err(engine_error(e));
        }
        if CANCEL.load(Ordering::SeqCst) && !matches!(code, Some(0 | 5)) {
            return Err(ImportError::new("arrete", "Arrêté : rien n'a été écrit dans ton cerveau."));
        }
        match code {
            None => Err(ImportError::new("interne", "L'import a pris trop de temps : je l'ai arrêté. Rien n'a été écrit.")),
            // 5: partial, written: the result says so.
            Some(0 | 5) => Ok(events),
            Some(n) => Err(ImportError { code: "interne".into(), message: format!("L'import s'est arrêté sans dire pourquoi (code {n}). Rien d'autre n'a changé.") }),
        }
    }
}

/// The importer's error, its sentence masked and on one line (it may name a path).
fn engine_error(e: &Value) -> ImportError {
    let code = e.get("code").and_then(Value::as_str).filter(|c| c.len() <= 40 && c.bytes().all(|b| b.is_ascii_lowercase() || b == b'_'));
    let message = e.get("message").and_then(Value::as_str).map(|m| one_line(m, 400)).filter(|m| !m.is_empty());
    ImportError {
        code: code.unwrap_or("interne").into(),
        message: message.unwrap_or_else(|| "L'import s'est arrêté sur une erreur.".into()),
    }
}

fn one_line(text: &str, max: usize) -> String {
    let flat = redact::redact(text, &[]).split_whitespace().collect::<Vec<_>>().join(" ");
    if flat.chars().count() <= max {
        return flat;
    }
    flat.chars().take(max - 1).chain(['…']).collect()
}

/// The sentence to paste in ChatGPT or Claude, the importer's own.
pub fn phrase(kit: &Kit) -> Result<String, ImportError> {
    let _busy = Busy::take()?;
    let events = kit.run(&["phrase"], Input::Text(String::new()), SHORT_LIMIT, &mut |_| {})?;
    events
        .iter()
        .find_map(|e| e.get("phrase").and_then(Value::as_str))
        .map(|p| one_line(p, 2000))
        .ok_or_else(|| ImportError::new("interne", "Je n'ai pas pu préparer la phrase."))
}

/// A file the picker gave, opened for reading as the importer opens one: a regular file, not a link,
/// at most 8 GiB. The user picked it: the check before the open is enough of one.
fn open_regular(path: &Path) -> Result<File, ImportError> {
    let refused = || ImportError::new("lien", "Ce fichier est un lien ou n'est pas un fichier ordinaire : je ne le lis pas.");
    if std::fs::symlink_metadata(path).map_err(|_| ImportError::new("introuvable", "Je ne trouve pas ce fichier."))?.file_type().is_symlink() {
        return Err(refused());
    }
    let mut options = std::fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        // A named pipe does not hang the open: it is refused below.
        options.custom_flags(O_NONBLOCK);
    }
    let file = options.open(path).map_err(|_| refused())?;
    let meta = file.metadata().map_err(|_| refused())?;
    if !meta.is_file() {
        return Err(refused());
    }
    if meta.len() > FILE_MAX {
        return Err(ImportError::new("trop_gros", "Cette archive est trop grosse pour être lue sans risque."));
    }
    Ok(file)
}

#[cfg(target_os = "macos")]
const O_NONBLOCK: i32 = 0x0004;
#[cfg(target_os = "linux")]
const O_NONBLOCK: i32 = 0o4000;

/// Reads the export's files (1 to 4, Claude's come split) into one plan: one `preparer --zip-stdin`
/// each, the first making the plan, the next adding to it. A plan not run yet is thrown away first.
pub fn read_files(kit: &Kit, paths: &[PathBuf], progress: &mut dyn FnMut(Value)) -> Result<PlanView, ImportError> {
    if paths.is_empty() || paths.len() > FILES_MAX {
        return Err(ImportError::new("usage", "Choisis de 1 à 4 fichiers de ton export."));
    }
    let _busy = Busy::take()?;
    forget_plan(kit);
    let files = paths.iter().map(|p| open_regular(p)).collect::<Result<Vec<_>, _>>()?;
    let (n, mut plan, mut first_error) = (files.len(), None::<Value>, None);
    for (i, file) in files.into_iter().enumerate() {
        let id = plan.as_ref().and_then(|p| p.get("id")).and_then(Value::as_str).map(String::from);
        let mut args = vec!["preparer", "--zip-stdin"];
        if let Some(id) = id.as_deref() {
            args.extend(["--plan", id]);
        }
        let mut each = |mut ev: Value| {
            if let Value::Object(m) = &mut ev {
                m.insert("fichier".into(), (i + 1).into());
                m.insert("fichiers".into(), n.into());
            }
            progress(ev);
        };
        // Which file, even for one too small for the importer to say how far it is.
        each(serde_json::json!({"etape": "lecture", "fait": 0, "total": 0}));
        match kit.run(&args, Input::File(file), READ_LIMIT, &mut each).and_then(|ev| last_plan(&ev)) {
            Ok(p) => plan = Some(p),
            Err(e) if n > 1 && SKIPPED_WITH_OTHERS.contains(&e.code.as_str()) => {
                first_error.get_or_insert(e);
            }
            Err(e) => {
                if let Some(id) = id {
                    // Stopped by « Arrêter » too: the plan begun goes all the same.
                    CANCEL.store(false, Ordering::SeqCst);
                    annuler(kit, &id);
                }
                return Err(e);
            }
        }
    }
    let plan = plan.ok_or_else(|| first_error.unwrap_or_else(|| ImportError::new("pas_un_export", "Je ne trouve rien à importer dans ces fichiers.")))?;
    keep(&plan)
}

/// The answer pasted from ChatGPT or Claude, into a plan of its own.
pub fn read_paste(kit: &Kit, text: &str) -> Result<PlanView, ImportError> {
    if text.trim().is_empty() {
        return Err(ImportError::new("vide", "Il n'y a rien à importer dans ce texte."));
    }
    if text.len() > PASTE_MAX {
        return Err(ImportError::new("collage_long", "C'est trop long pour un copier-coller : passe plutôt par le fichier d'export."));
    }
    let _busy = Busy::take()?;
    forget_plan(kit);
    let events = kit.run(&["preparer", "--fichier", "-"], Input::Text(text.to_string()), SHORT_LIMIT * 5, &mut |_| {})?;
    keep(&last_plan(&events)?)
}

fn last_plan(events: &[Value]) -> Result<Value, ImportError> {
    events.iter().rev().find_map(|e| e.get("plan")).cloned().ok_or_else(|| ImportError::new("interne", "L'import n'a pas fait de plan."))
}

/// Keeps the plan's id and agreement codes here; the window gets the rest.
fn keep(plan: &Value) -> Result<PlanView, ImportError> {
    let bad = || ImportError::new("interne", "Le plan de l'import est illisible.");
    let id = plan.get("id").and_then(Value::as_str).filter(|i| is_id(i)).ok_or_else(bad)?;
    let mut choices = Vec::new();
    let mut view = plan.as_object().cloned().ok_or_else(bad)?;
    let mut shown = Vec::new();
    for c in plan.get("choix").and_then(Value::as_array).ok_or_else(bad)? {
        let appels = c.get("appels").and_then(Value::as_u64).filter(|n| (1..=u64::from(APPELS_MAX)).contains(n)).ok_or_else(bad)? as u32;
        let accord = c.get("accord").and_then(Value::as_str).filter(|a| is_id(a)).ok_or_else(bad)?;
        choices.push((appels, accord.to_string()));
        let mut c = c.as_object().cloned().unwrap_or_default();
        c.remove("accord");
        shown.push(Value::Object(c));
    }
    if choices.is_empty() {
        return Err(bad());
    }
    view.insert("choix".into(), Value::Array(shown));
    session().plan = Some(Plan { id: id.to_string(), choices });
    Ok(Value::Object(view))
}

fn is_id(s: &str) -> bool {
    ID.as_ref().is_some_and(|re| re.is_match(s))
}

/// The plan's id and the agreement code for `appels`, one of the choices the importer gave.
fn agreed(appels: u32) -> Result<(String, String), ImportError> {
    let s = session();
    let plan = s.plan.as_ref().ok_or_else(|| ImportError::new("plan", "Il n'y a pas d'import préparé : recommence."))?;
    let accord = plan.choices.iter().find(|(n, _)| *n == appels).map(|(_, a)| a.clone());
    let accord = accord.ok_or_else(|| ImportError::new("plan", "Ce nombre de demandes n'a pas été proposé."))?;
    Ok((plan.id.clone(), accord))
}

/// Exactly what would leave for `appels` requests, as the importer prints it (`voir`), masked: the
/// instructions given to the AI, then each request.
pub fn preview(kit: &Kit, appels: u32) -> Result<String, ImportError> {
    let (id, accord) = agreed(appels)?;
    let _busy = Busy::take()?;
    let n = appels.to_string();
    let events = kit.run(&["voir", &id, "--appels", &n], Input::Text(String::new()), SHORT_LIMIT * 5, &mut |_| {})?;
    // The same text as the one agreed to: the code of what `voir` printed is the one kept.
    if events.iter().find_map(|e| e.get("accord")).and_then(Value::as_str) != Some(accord.as_str()) {
        return Err(ImportError::new("plan", "Ce plan a changé depuis : recommence l'import."));
    }
    let mut out = String::new();
    for e in &events {
        if let Some(s) = e.get("systeme").and_then(Value::as_str) {
            out.push_str(&format!("=== Consignes données à l'IA (les mêmes à chaque demande) ===\n{s}\n\n"));
        }
        if let (Some(k), Some(t)) = (e.get("demande").and_then(Value::as_u64), e.get("texte").and_then(Value::as_str)) {
            let sur = e.get("sur").and_then(Value::as_u64).unwrap_or(u64::from(appels));
            out.push_str(&format!("=== Demande {k} sur {sur} ({} caractères) ===\n{t}\n\n", t.chars().count()));
        }
    }
    Ok(out.trim_end().to_string())
}

/// Sends the plan's requests, as many as agreed, and writes the note: the importer's result.
pub fn run(kit: &Kit, appels: u32, progress: &mut dyn FnMut(Value)) -> Result<Outcome, ImportError> {
    let (id, accord) = agreed(appels)?;
    let _busy = Busy::take()?;
    let n = appels.to_string();
    let limit = REQUEST_LIMIT * appels + SHORT_LIMIT;
    let events = kit.run(&["lancer", &id, "--appels", &n, "--accord", &accord], Input::Text(String::new()), limit, progress)?;
    let res = events.iter().find_map(|e| e.get("resultat")).and_then(Value::as_object).cloned();
    let res = res.ok_or_else(|| ImportError::new("interne", "L'import n'a pas dit ce qu'il a écrit."))?;
    let note = res.get("note").and_then(Value::as_str).filter(|n| NOTE.as_ref().is_some_and(|re| re.is_match(n))).map(String::from);
    let mut s = session();
    s.plan = None;
    s.note = note.clone();
    Ok(outcome(res, note))
}

/// The importer's result, its warning on one line and masked, the note only as checked.
fn outcome(mut res: Map<String, Value>, note: Option<String>) -> Outcome {
    if let Some(avis) = res.get("avis").and_then(Value::as_str).map(|a| one_line(a, 400)) {
        res.insert("avis".into(), Value::String(avis));
    }
    res.insert("note".into(), note.map_or(Value::Null, Value::String));
    Value::Object(res)
}

/// « Arrêter »: the importer and what it started are killed, unless it is writing the note.
pub fn cancel() {
    if BUSY.load(Ordering::SeqCst) && !WRITING.load(Ordering::SeqCst) {
        CANCEL.store(true, Ordering::SeqCst);
    }
}

/// The plan not run is thrown away (`annuler`): nothing left.
pub fn forget(kit: &Kit) {
    if let Ok(_busy) = Busy::take() {
        forget_plan(kit);
    }
}

fn forget_plan(kit: &Kit) {
    let plan = session().plan.take();
    if let Some(p) = plan {
        annuler(kit, &p.id);
    }
}

fn annuler(kit: &Kit, id: &str) {
    let _ = kit.run(&["annuler", id], Input::Text(String::new()), SHORT_LIMIT, &mut |_| {});
}

/// The note of the last import, in the brain, for « Voir la note ».
pub fn note(brain: &Path) -> Option<PathBuf> {
    let rel = session().note.clone()?;
    let path = brain.join(&rel);
    path.strip_prefix(brain).ok()?.components().all(|c| matches!(c, Component::Normal(_))).then_some(path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::tests::python;
    use std::sync::Mutex as StdMutex;

    /// The tests share the session: one at a time.
    static SERIAL: StdMutex<()> = StdMutex::new(());

    fn repo() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
    }

    struct Bench {
        _tmp: tempfile::TempDir,
        root: PathBuf,
        home: PathBuf,
        kit: Kit,
    }

    /// The sources' importer with a fake Claude (engine/tests/fake_claude.py) in a throwaway home.
    fn bench() -> Bench {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().to_path_buf();
        let home = root.join("home");
        let brain = home.join("Documents").join("Brain");
        std::fs::create_dir_all(brain.join("Profile")).unwrap();
        std::fs::write(brain.join("Profile").join("memory.md"), "# Memory\n\n## Facts\n").unwrap();
        let fake = home.join(".claude").join("fake");
        std::fs::create_dir_all(fake.join("answers")).unwrap();
        std::fs::create_dir_all(fake.join("scenarios")).unwrap();
        std::fs::write(fake.join("fixtures"), repo().join("app/src-tauri/tests/fixtures/repair").to_string_lossy().as_bytes()).unwrap();
        let py = python();
        // The importer takes only an .exe for Claude on Windows: there, a name that is nothing, so that
        // no request can ever reach a real Claude; the requests are tested on macOS and Linux.
        let claude = root.join("claude");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let fake = repo().join("engine").join("tests").join("fake_claude.py");
            std::fs::write(&claude, format!("#!/bin/sh\nexec '{}' -I '{}' \"$@\"\n", py.display(), fake.display())).unwrap();
            std::fs::set_permissions(&claude, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        let env = vec![
            ("HOME".to_string(), home.clone().into_os_string()),
            ("USERPROFILE".to_string(), home.clone().into_os_string()),
            ("CLAUDE_BIN".to_string(), claude.into_os_string()),
            ("BRAIN_DIR".to_string(), brain.clone().into_os_string()),
        ];
        let kit = Kit { python: py, script: repo().join("engine/hooks/importer-memoire.py"), brain, env };
        *session() = Session::default();
        Bench { _tmp: tmp, root, home, kit }
    }

    /// A ChatGPT export made by the tests' own fixtures (engine/tests/import_fixtures.py), as a file.
    fn export(b: &Bench, name: &str, conversations: usize) -> PathBuf {
        let script = "import json, sys\nsys.path.insert(0, sys.argv[1])\nimport import_fixtures as F\n\
                      n = int(sys.argv[3])\n\
                      convs = [F.chatgpt_conversation('c-%03d' % i, 'Sujet %d' % i, F.T0 + i * 3600,\n\
                      \x20   ['Je fais du pain au levain le dimanche, message %d.' % k + ' farine' * 400 for k in range(14)]) for i in range(n)]\n\
                      open(sys.argv[2], 'wb').write(F.zip_bytes([('conversations.json', json.dumps(convs))]))";
        let path = b.root.join(name);
        let out = std::process::Command::new(python())
            .args(["-I", "-c", script, &repo().join("engine/tests").to_string_lossy(), &path.to_string_lossy(), &conversations.to_string()])
            .output()
            .unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        path
    }

    fn answer(b: &Bench, n: usize, facts: &Value) {
        let p = b.home.join(".claude/fake/answers").join(format!("{n}.json"));
        std::fs::write(p, facts.to_string()).unwrap();
    }

    fn calls(b: &Bench) -> usize {
        std::fs::read_to_string(b.home.join(".claude/fake/calls")).map_or(0, |s| s.matches("-p").count())
    }

    #[test]
    fn an_export_read_through_stdin_and_what_would_leave() {
        let _one = SERIAL.lock().unwrap_or_else(|p| p.into_inner());
        let b = bench();
        let zip = export(&b, "export.zip", 3);
        let mut seen = Vec::new();
        let plan = read_files(&b.kit, &[zip], &mut |ev| seen.push(ev)).unwrap();
        assert_eq!(plan["source"], "chatgpt");
        assert_eq!(plan["conversations"], 3);
        assert!(seen.iter().any(|e| e["etape"] == "lecture" && e["fichiers"] == 1), "{seen:?}");
        // The window never sees an agreement code; the app keeps them.
        assert!(!plan.to_string().contains("accord"), "{plan}");
        let appels = plan["defaut"].as_u64().unwrap() as u32;
        assert!(session().plan.as_ref().is_some_and(|p| p.choices.iter().any(|(n, _)| *n == appels)));

        // What would leave, word for word: the importer's own text, with the conversations' words.
        let text = preview(&b.kit, appels).unwrap();
        assert!(text.starts_with("=== Consignes données à l'IA"), "{}", &text[..80.min(text.len())]);
        assert!(text.contains("levain"));
        assert_eq!(calls(&b), 0, "nothing leaves before the run");
    }

    #[cfg(unix)]
    #[test]
    fn the_requests_agreed_then_the_note() {
        let _one = SERIAL.lock().unwrap_or_else(|p| p.into_inner());
        let b = bench();
        let plan = read_files(&b.kit, &[export(&b, "export.zip", 3)], &mut |_| {}).unwrap();
        let appels = plan["defaut"].as_u64().unwrap() as u32;
        answer(&b, 1, &serde_json::json!({"faits": [
            {"categorie": "preference", "texte": "Fait du pain au levain le dimanche.", "sources": ["c1"], "date": "", "confiance": "sur"},
            {"categorie": "outil", "texte": "Utilise un four à pain.", "sources": ["c2"], "date": "", "confiance": "probable"}
        ], "suspect": false}));
        let mut steps = Vec::new();
        let res = run(&b.kit, appels, &mut |ev| steps.push(ev)).unwrap();
        assert_eq!(calls(&b), appels as usize);
        assert!(steps.iter().any(|e| e["etape"] == "tri"), "{steps:?}");
        assert!(steps.iter().any(|e| e["etape"] == "ecriture"), "{steps:?}");
        assert_eq!(res["faits"], 2, "{res}");
        let note = note(&b.kit.brain).expect("the note of this import");
        assert!(note.is_file() && note.starts_with(b.kit.brain.join("Imports")), "{}", note.display());
        assert!(std::fs::read_to_string(&note).unwrap().contains("levain"));
        // The plan is spent: running it again is refused here, nothing leaves.
        assert_eq!(run(&b.kit, appels, &mut |_| {}).unwrap_err().code, "plan");
        assert_eq!(calls(&b), appels as usize);

        // The same export again: already imported, no plan.
        let zip = b.root.join("export.zip");
        assert_eq!(read_files(&b.kit, &[zip], &mut |_| {}).unwrap_err().code, "deja");
        assert!(session().plan.is_none());
    }

    #[test]
    fn only_a_choice_the_importer_offered_runs() {
        let _one = SERIAL.lock().unwrap_or_else(|p| p.into_inner());
        let b = bench();
        let plan = read_files(&b.kit, &[export(&b, "e.zip", 2)], &mut |_| {}).unwrap();
        let offered: Vec<u64> = plan["choix"].as_array().unwrap().iter().map(|c| c["appels"].as_u64().unwrap()).collect();
        let other = (1..=APPELS_MAX).find(|n| !offered.contains(&u64::from(*n))).unwrap();
        assert_eq!(run(&b.kit, other, &mut |_| {}).unwrap_err().code, "plan");
        assert_eq!(preview(&b.kit, other).unwrap_err().code, "plan");
        assert_eq!(calls(&b), 0);
        // A forgotten plan is gone for the importer too.
        let id = session().plan.as_ref().unwrap().id.clone();
        forget(&b.kit);
        assert!(session().plan.is_none());
        assert!(!b.home.join(".brain-in-a-box/import/plans").join(&id).exists());
    }

    #[test]
    fn a_paste_goes_through_stdin() {
        let _one = SERIAL.lock().unwrap_or_else(|p| p.into_inner());
        let b = bench();
        let plan = read_paste(&b.kit, "```\n[2026-03-02] - Habite à Lyon.\n- Préfère des réponses courtes.\n```\nLa liste est complète.").unwrap();
        assert_eq!(plan["source"], "collage");
        assert_eq!(plan["choix"].as_array().unwrap().len(), 1);
        assert_eq!(read_paste(&b.kit, "  \n").unwrap_err().code, "vide");
        assert_eq!(read_paste(&b.kit, &"x".repeat(PASTE_MAX + 1)).unwrap_err().code, "collage_long");
        // The sentence to paste, the importer's.
        assert!(phrase(&b.kit).unwrap().starts_with("Je change d'outil"));
    }

    #[test]
    fn what_is_not_an_export_says_so() {
        let _one = SERIAL.lock().unwrap_or_else(|p| p.into_inner());
        let b = bench();
        let not_zip = b.root.join("notes.zip");
        std::fs::write(&not_zip, "not a zip at all").unwrap();
        let e = read_files(&b.kit, std::slice::from_ref(&not_zip), &mut |_| {}).unwrap_err();
        // The importer's own word and sentence, not the app's.
        assert_eq!(e.code, "pas_un_export", "{e:?}");
        assert!(e.message.starts_with("Je ne trouve ni conversation"), "{e:?}");
        assert_eq!(read_files(&b.kit, &[], &mut |_| {}).unwrap_err().code, "usage");
        let five: Vec<PathBuf> = (0..5).map(|_| not_zip.clone()).collect();
        assert_eq!(read_files(&b.kit, &five, &mut |_| {}).unwrap_err().code, "usage");
    }

    #[cfg(unix)]
    #[test]
    fn of_several_files_one_already_imported_is_left_aside() {
        let _one = SERIAL.lock().unwrap_or_else(|p| p.into_inner());
        let b = bench();
        let first = export(&b, "first.zip", 1);
        read_files(&b.kit, std::slice::from_ref(&first), &mut |_| {}).unwrap();
        answer(&b, 1, &serde_json::json!({"faits": [], "suspect": false}));
        run(&b.kit, 1, &mut |_| {}).unwrap();
        let second = export(&b, "second.zip", 2);
        let plan = read_files(&b.kit, &[first, second], &mut |_| {}).unwrap();
        assert_eq!(plan["conversations"], 2, "{plan}");
    }

    #[cfg(unix)]
    #[test]
    fn a_link_is_not_read() {
        let _one = SERIAL.lock().unwrap_or_else(|p| p.into_inner());
        let b = bench();
        let zip = export(&b, "real.zip", 1);
        let link = b.root.join("link.zip");
        std::os::unix::fs::symlink(&zip, &link).unwrap();
        assert_eq!(read_files(&b.kit, &[link], &mut |_| {}).unwrap_err().code, "lien");
        let fifo = b.root.join("fifo.zip");
        assert!(std::process::Command::new("mkfifo").arg(&fifo).status().unwrap().success());
        assert_eq!(read_files(&b.kit, &[fifo], &mut |_| {}).unwrap_err().code, "lien");
    }

    /// « Arrêter » during the requests: the importer is killed, nothing is written, the plan stays.
    #[cfg(unix)]
    #[test]
    fn stopping_writes_nothing() {
        let _one = SERIAL.lock().unwrap_or_else(|p| p.into_inner());
        let b = bench();
        let plan = read_files(&b.kit, &[export(&b, "e.zip", 2)], &mut |_| {}).unwrap();
        std::fs::write(b.home.join(".claude/fake/scenarios/1"), "hang").unwrap();
        let appels = plan["choix"][0]["appels"].as_u64().unwrap() as u32;
        // Once Claude is there, waiting on its request.
        let started = b.home.join(".claude/fake/grandchild.pid");
        let stopper = std::thread::spawn(move || {
            for _ in 0..300 {
                std::thread::sleep(Duration::from_millis(100));
                if std::fs::read_to_string(&started).is_ok_and(|p| !p.trim().is_empty()) {
                    cancel();
                    return;
                }
            }
        });
        let e = run(&b.kit, appels, &mut |_| {}).unwrap_err();
        stopper.join().unwrap();
        assert_eq!(e.code, "arrete", "{e:?}");
        assert!(!b.kit.brain.join("Imports").exists());
        // Claude, which the importer started in a session of its own, did not outlive it.
        let pid = std::fs::read_to_string(b.home.join(".claude/fake/grandchild.pid")).unwrap();
        let alive = || std::process::Command::new("/bin/kill").args(["-0", pid.trim()]).stderr(std::process::Stdio::null()).status().is_ok_and(|s| s.success());
        for _ in 0..100 {
            if !alive() {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        assert!(!alive(), "what Claude started outlived « Arrêter »");
        assert!(session().plan.is_some(), "the plan stays: it can be run later");
    }

    #[test]
    fn the_note_stays_in_the_brain() {
        let _one = SERIAL.lock().unwrap_or_else(|p| p.into_inner());
        let brain = Path::new("/b");
        for bad in ["../x.md", "/etc/x.md", "Imports/../../x.md", "Imports/a/b.md", "Notes/x.md", "Imports/x.txt"] {
            let res = outcome(Map::from_iter([("note".to_string(), Value::String(bad.into()))]), None);
            assert!(res["note"].is_null());
            assert!(!NOTE.as_ref().unwrap().is_match(bad), "{bad}");
        }
        session().note = Some("Imports/2026-10-20-import-chatgpt.md".into());
        assert_eq!(note(brain), Some(brain.join("Imports/2026-10-20-import-chatgpt.md")));
        session().note = None;
    }

    /// The picker is opened from Rust: the window gets no dialog of its own, nor any plugin's command.
    #[test]
    fn the_window_can_open_no_dialog() {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR"));
        let caps: Value = serde_json::from_str(&std::fs::read_to_string(dir.join("capabilities/default.json")).unwrap()).unwrap();
        assert_eq!(caps["permissions"], serde_json::json!(["core:default"]));
        let cargo = std::fs::read_to_string(dir.join("Cargo.toml")).unwrap();
        assert!(!cargo.contains("tauri-plugin"), "a Tauri plugin in Cargo.toml");
    }

    #[test]
    fn the_importers_errors_are_masked_and_short() {
        let e = engine_error(&serde_json::json!({"code": "cerveau_absent", "message": "Je ne trouve pas ton cerveau (/Users/jo/Brain) sk-ant-api03-SECRETSECRETSECRET\nici"}));
        assert_eq!(e.code, "cerveau_absent");
        assert!(!e.message.contains("SECRET") && !e.message.contains("/Users/jo") && !e.message.contains('\n'), "{}", e.message);
        assert_eq!(engine_error(&serde_json::json!({"code": "<script>", "message": 3})).code, "interne");
    }
}
