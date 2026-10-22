mod advice;
mod agents;
mod claude;
mod codex;
mod config_guard;
mod detect;
mod diagnose;
mod guide;
mod hermes;
mod hermes_connect;
mod hermes_brain;
mod import;
mod model;
mod perms;
mod redact;
mod remedies;
mod repair;
#[cfg(target_os = "linux")]
mod root;
mod shot;
mod steps;
mod sys;
mod voyage;

use std::path::{Path, PathBuf};

use tauri::{Emitter, Manager};

#[tauri::command]
async fn diagnose_machine() -> model::Diagnosis {
    tauri::async_runtime::spawn_blocking(diagnose::run).await.unwrap_or_else(|e| diagnose::failed(&e.to_string()))
}

/// Runs one install step; its output arrives as "step-log" events. The repair keeps
/// the same lines and the result: they are what Claude reads if the step fails.
#[tauri::command]
async fn run_step(app: tauri::AppHandle, id: String, voyage_key: Option<String>) -> model::StepResult {
    let step = id.clone();
    let res = tauri::async_runtime::spawn_blocking(move || {
        run_traced(&id, voyage_key.as_deref(), &mut |line| {
            let _ = app.emit("step-log", model::LogLine { step: id.clone(), line });
        })
    })
    .await;
    res.unwrap_or_else(|e| model::StepResult { id: step, ok: false, message: format!("Erreur interne : {e}") })
}

/// What leaves for the window (and the copied report) is masked like Claude's dossier;
/// the trace keeps the raw lines, masked when a dossier is built from them.
fn run_traced(id: &str, voyage_key: Option<&str>, emit: &mut dyn FnMut(String)) -> model::StepResult {
    repair::remember_key(voyage_key);
    repair::trace_start(id);
    let mask = repair::masker();
    let r = steps::run(id, voyage_key, &mut |line| {
        repair::record_line(id, &line);
        emit(mask(&line));
    });
    repair::record_result(&r);
    model::StepResult { message: mask(&r.message), ..r }
}

#[tauri::command]
async fn check_voyage_key(key: String) -> model::KeyCheck {
    tauri::async_runtime::spawn_blocking(move || voyage::check_key(&key)).await.unwrap_or_else(|e| model::KeyCheck {
        status: model::KeyStatus::Offline,
        message: format!("Je n'ai pas pu vérifier la clé (erreur interne : {e}). Réessaie."),
    })
}

#[tauri::command]
async fn claude_logged_in() -> bool {
    tauri::async_runtime::spawn_blocking(detect::claude_logged_in).await.unwrap_or(false)
}

/// Whether the window asks who the brain works with (only where the kit's gate lets Codex be
/// chosen), and what it preselects.
#[tauri::command]
async fn agents_offer() -> agents::Offer {
    tauri::async_runtime::spawn_blocking(|| agents::offer(&sys::home())).await.unwrap_or(agents::Offer { open: false, agents: Vec::new() })
}

/// The user's pick, preferred first ("claude", "codex"): held for the session until the brain step
/// has the installer record it.
#[tauri::command]
async fn choose_agents(agents: Vec<String>) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || agents::choose(&sys::home(), &agents).map(|_| ()))
        .await
        .unwrap_or_else(|e| Err(format!("Erreur interne : {e}")))
}

/// How Codex is logged in: "chatgpt", "billed", "managed", "logged_out" or "unknown".
#[tauri::command]
async fn codex_login() -> codex::Login {
    tauri::async_runtime::spawn_blocking(steps::codex_login).await.unwrap_or(codex::Login::Unknown)
}

/// What the window can offer under a failed step.
#[tauri::command]
async fn repair_status(step: String) -> repair::RepairStatus {
    tauri::async_runtime::spawn_blocking(move || repair::availability(&step))
        .await
        .unwrap_or(repair::RepairStatus { state: repair::RepairState::Disabled, terminal_fallback: false, engine: None })
}

/// Exactly what Claude would read, for « Voir exactement ce que j'envoie ».
#[tauri::command]
async fn repair_preview(step: String) -> String {
    tauri::async_runtime::spawn_blocking(move || repair::preview(&step)).await.unwrap_or_default()
}

/// One analysis; its stages arrive as "repair-progress" events.
#[tauri::command]
async fn repair_analyze(app: tauri::AppHandle, step: String) -> Result<repair::RepairPlan, repair::RepairError> {
    tauri::async_runtime::spawn_blocking(move || {
        repair::analyze(&step, &mut |stage| {
            let _ = app.emit("repair-progress", stage);
        })
    })
    .await
    .unwrap_or_else(|e| Err(repair::RepairError::Internal { message: e.to_string() }))
}

#[tauri::command]
fn repair_cancel() {
    repair::cancel();
}

/// Applies a plan from `repair_analyze`: the window sends only its id. The relaunched
/// step's output arrives as "step-log" events.
#[tauri::command]
async fn repair_apply(app: tauri::AppHandle, plan_id: String, voyage_key: Option<String>) -> repair::RepairOutcome {
    tauri::async_runtime::spawn_blocking(move || {
        repair::apply(&plan_id, voyage_key.as_deref(), &mut |step, line| {
            let _ = app.emit("step-log", model::LogLine { step: step.to_string(), line });
        })
    })
    .await
    .unwrap_or_else(repair::RepairOutcome::internal)
}

/// « Remettre comme avant ».
#[tauri::command]
async fn repair_undo(plan_id: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || repair::undo(&plan_id))
        .await
        .unwrap_or_else(|e| Err(format!("Erreur interne : {e}")))
}

#[tauri::command]
async fn repair_report() -> String {
    tauri::async_runtime::spawn_blocking(repair::report).await.unwrap_or_default()
}

/// A command that is not async runs on the main thread: while `open` or `pgrep`
/// runs (up to their timeouts), the window would freeze.
async fn off_main(f: impl FnOnce() -> Result<(), String> + Send + 'static) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(f).await.unwrap_or_else(|e| Err(format!("Erreur interne : {e}")))
}

#[tauri::command]
async fn open_claude_login() -> Result<(), String> {
    off_main(steps::open_claude_login).await
}

/// The guide's « Mettre à jour »: `claude update`, in Terminal (PowerShell on Windows).
#[tauri::command]
async fn open_claude_update() -> Result<(), String> {
    off_main(steps::open_claude_update).await
}

#[tauri::command]
async fn open_codex_login() -> Result<(), String> {
    off_main(steps::open_codex_login).await
}

/// The ChatGPT code Hermes waits for while its connection step runs, and its page.
#[tauri::command]
async fn hermes_device() -> Option<hermes_connect::Device> {
    hermes_connect::device()
}

/// Hermes's ChatGPT page again, if the user closed it.
#[tauri::command]
async fn open_hermes_device_page() -> Result<(), String> {
    off_main(hermes_connect::open_device_page).await
}

#[tauri::command]
async fn start_onboarding() -> Result<(), String> {
    off_main(steps::start_onboarding).await
}

#[tauri::command]
async fn open_vault() -> Result<(), String> {
    off_main(steps::open_vault).await
}

/// Opens one of a few known pages (Voyage dashboard, downloads) in the default browser.
#[tauri::command]
async fn open_link(url: String) -> Result<(), String> {
    off_main(move || steps::open_link(&url)).await
}

/// One of the few System Settings panes a repair can point to.
#[tauri::command]
async fn open_settings(pane: String) -> Result<(), String> {
    off_main(move || repair::open_settings(&pane)).await
}

/// Plan B: Claude in Terminal, asking before each command.
#[tauri::command]
async fn open_repair_terminal(step: String) -> Result<(), String> {
    off_main(move || repair::open_terminal(&step)).await
}

/// Which AI the brain should work with, for the screen that asks it (where Codex can be chosen).
#[tauri::command]
async fn machine_advice() -> Option<model::Advice> {
    tauri::async_runtime::spawn_blocking(|| advice::now(&sys::home())).await.ok().flatten()
}

/// One step of « Importer ma mémoire » off the main thread, with the installed importer.
async fn importing<T: Send + 'static>(
    f: impl FnOnce(&import::Kit) -> Result<T, import::ImportError> + Send + 'static,
) -> Result<T, import::ImportError> {
    tauri::async_runtime::spawn_blocking(move || f(&import::Kit::for_user()?))
        .await
        .unwrap_or_else(|e| Err(import::ImportError { code: "interne".into(), message: format!("Erreur interne : {e}") }))
}

/// Whether the import can run: the brain is there, and new enough to have the importer.
#[tauri::command]
async fn import_check() -> Result<(), import::ImportError> {
    importing(|_| Ok(())).await
}

/// The guide's offer before the end: is an import already in the brain, and who to ask first.
/// None without a brain: nothing is offered.
#[tauri::command]
async fn import_offer() -> Option<import::Offer> {
    tauri::async_runtime::spawn_blocking(|| {
        let home = sys::home();
        let brain = detect::vault(&home)?;
        Some(import::Offer { deja: import::imported(&brain), source: advice::memory_source(&home) })
    })
    .await
    .ok()
    .flatten()
}

/// The sentence to paste in ChatGPT or Claude.
#[tauri::command]
async fn import_phrase() -> Result<String, import::ImportError> {
    importing(import::phrase).await
}

/// The system's file picker, then the export read: None when nothing was picked. The reading's
/// progress arrives as "import-progress" events.
#[tauri::command]
async fn import_pick(app: tauri::AppHandle) -> Result<Option<import::PlanView>, import::ImportError> {
    importing(move |kit| {
        let Some(paths) = pick_export(&app) else { return Ok(None) };
        import::read_files(kit, &paths, &mut |ev| {
            let _ = app.emit("import-progress", ev);
        })
        .map(Some)
    })
    .await
}

/// The answer pasted from ChatGPT or Claude.
#[tauri::command]
async fn import_paste(text: String) -> Result<import::PlanView, import::ImportError> {
    importing(move |kit| import::read_paste(kit, &text)).await
}

/// Exactly what would leave for that many requests.
#[tauri::command]
async fn import_preview(appels: u32) -> Result<String, import::ImportError> {
    importing(move |kit| import::preview(kit, appels)).await
}

/// The requests sent, the note written; "import-progress" events meanwhile.
#[tauri::command]
async fn import_run(app: tauri::AppHandle, appels: u32) -> Result<import::Outcome, import::ImportError> {
    importing(move |kit| {
        import::run(kit, appels, &mut |ev| {
            let _ = app.emit("import-progress", ev);
        })
    })
    .await
}

#[tauri::command]
fn import_cancel() {
    import::cancel();
}

/// The plan not run is thrown away.
#[tauri::command]
async fn import_forget() {
    let _ = importing(|kit| {
        import::forget(kit);
        Ok(())
    })
    .await;
}

/// « Voir la note »: the last import's note in Obsidian.
#[tauri::command]
async fn import_open_note() -> Result<(), String> {
    off_main(|| {
        let home = sys::home();
        let note = detect::vault(&home).and_then(|v| import::note(&v)).ok_or_else(|| "Il n'y a pas de note à ouvrir.".to_string())?;
        steps::open_note(&note)
    })
    .await
}

// ── The guide (guide.rs): its own window, always on top ─────────────────────

/// The usable area of the screen the guide is on (else the installer's, else the main one), in
/// logical pixels.
fn guide_area(app: &tauri::AppHandle) -> Option<guide::Frame> {
    let on = |label: &str| app.get_webview_window(label).and_then(|w| w.current_monitor().ok().flatten());
    let monitor = on("guide").or_else(|| on("main")).or_else(|| app.primary_monitor().ok().flatten())?;
    let (area, scale) = (monitor.work_area(), monitor.scale_factor());
    Some(guide::Frame {
        x: f64::from(area.position.x) / scale,
        y: f64::from(area.position.y) / scale,
        w: f64::from(area.size.width) / scale,
        h: f64::from(area.size.height) / scale,
    })
}

/// The guide's window, or brought to the front: small, in the bottom right corner, above the
/// program it guides in. The installer's window goes to the Dock or the taskbar. A capture dropped
/// on it is read in Rust: the page never gets a path to send back.
fn open_guide(app: &tauri::AppHandle) -> tauri::Result<()> {
    if let Some(w) = app.get_webview_window("guide") {
        let _ = w.unminimize();
        w.show()?;
        return w.set_focus();
    }
    let frame = guide_area(app).map(|area| guide::frame(area, guide::Taille::Normal));
    let mut builder = tauri::WebviewWindowBuilder::new(app, "guide", tauri::WebviewUrl::App("guide.html".into()))
        .title("Bibo, ton guide")
        .min_inner_size(360.0, 220.0)
        .always_on_top(true)
        .visible_on_all_workspaces(true);
    builder = match frame {
        Some(f) => builder.inner_size(f.w, f.h).position(f.x, f.y),
        None => builder.inner_size(400.0, 600.0),
    };
    let window = builder.build()?;
    let handle = app.clone();
    window.on_window_event(move |event| match event {
        // The installer's window comes back: an app with no window in sight looks gone.
        tauri::WindowEvent::Destroyed => {
            guide::fin();
            if let Some(main) = handle.get_webview_window("main") {
                let _ = main.unminimize();
            }
        }
        tauri::WindowEvent::DragDrop(tauri::DragDropEvent::Drop { paths, .. }) => {
            let Some(path) = paths.first().cloned() else { return };
            let handle = handle.clone();
            std::thread::spawn(move || {
                let _ = handle.emit_to("guide", "guide-depose", guide::deposer_en_attente(&path));
            });
        }
        _ => {}
    });
    if let Some(main) = app.get_webview_window("main") {
        let _ = main.minimize();
    }
    Ok(())
}

/// Creating a window from a command that is not async would block Windows' event loop.
#[tauri::command]
async fn guide_open(app: tauri::AppHandle) -> Result<(), String> {
    open_guide(&app).map_err(|e| e.to_string())
}

/// The installer's welcome offers the guide: the brain and Claude Code are there (nothing is run).
#[tauri::command]
async fn guide_offert() -> bool {
    tauri::async_runtime::spawn_blocking(guide::offert).await.unwrap_or(false)
}

/// « Fermer le guide »: its window goes, the session with it (Destroyed), the installer's comes back.
#[tauri::command]
async fn guide_fermer(app: tauri::AppHandle) -> Result<(), String> {
    match app.get_webview_window("guide") {
        Some(w) => w.close().map_err(|e| e.to_string()),
        None => Ok(()),
    }
}

/// « Toujours au-dessus », the pin: set from Rust, the page has no window permission for it.
#[tauri::command]
async fn guide_epingler(app: tauri::AppHandle, epingle: bool) -> Result<(), String> {
    let w = app.get_webview_window("guide").ok_or("pas de fenêtre du guide")?;
    w.set_always_on_top(epingle).map_err(|e| e.to_string())
}

/// The window's size and place: normal, reduced (the step and « C'est fait »), or wide (the capture).
#[tauri::command]
async fn guide_taille(app: tauri::AppHandle, taille: guide::Taille) -> Result<(), String> {
    let w = app.get_webview_window("guide").ok_or("pas de fenêtre du guide")?;
    let area = guide_area(&app).ok_or("pas d'écran")?;
    let f = guide::frame(area, taille);
    w.set_size(tauri::LogicalSize::new(f.w, f.h)).map_err(|e| e.to_string())?;
    w.set_position(tauri::LogicalPosition::new(f.x, f.y)).map_err(|e| e.to_string())
}

async fn guiding<T: Send + 'static>(f: impl FnOnce() -> Result<T, guide::GuideError> + Send + 'static) -> Result<T, guide::GuideError> {
    tauri::async_runtime::spawn_blocking(f).await.unwrap_or_else(|e| Err(guide::GuideError::Interne { message: e.to_string() }))
}

/// Whether the guide can run: Claude, the capture's mode, the brain, G1 read.
#[tauri::command]
async fn guide_status() -> Result<guide::Status, String> {
    tauri::async_runtime::spawn_blocking(guide::status).await.map_err(|e| e.to_string())
}

/// « J'ai compris » (G1): an empty flag.
#[tauri::command]
async fn guide_compris() -> Result<(), String> {
    off_main(|| guide::compris().map_err(|e| e.to_string())).await
}

/// The program and the objective: a new session.
#[tauri::command]
async fn guide_commencer(logiciel: String, objectif: String) -> Result<guide::Debut, guide::GuideError> {
    guiding(move || guide::commencer(&logiciel, &objectif)).await
}

/// A pasted capture, as raw bytes (`invoke("guide_coller", bytes)`), never a JSON array of numbers.
#[tauri::command]
async fn guide_coller(request: tauri::ipc::Request<'_>) -> Result<guide::Apercu, guide::GuideError> {
    let tauri::ipc::InvokeBody::Raw(bytes) = request.body() else { return Err(guide::GuideError::ImageIllisible) };
    let bytes = bytes.clone();
    guiding(move || guide::coller(&bytes)).await
}

/// The file dropped on the window, now that it waits for a capture: its preview.
#[tauri::command]
async fn guide_prendre_depot() -> Result<guide::Apercu, guide::GuideError> {
    guiding(guide::prendre_depot).await
}

/// Every zone to hide, drawn on the preview: the new preview.
#[tauri::command]
async fn guide_cacher(zones: Vec<shot::Zone>) -> Result<guide::Apercu, guide::GuideError> {
    guiding(move || guide::cacher(&zones)).await
}

/// « Voir exactement ce que j'envoie ».
#[tauri::command]
async fn guide_envoi_texte(envoi: guide::Envoi) -> Result<String, guide::GuideError> {
    guiding(move || guide::envoi_texte(&envoi)).await
}

/// One look; its stages arrive as "guide-progress" events.
#[tauri::command]
async fn guide_envoyer(app: tauri::AppHandle, envoi: guide::Envoi) -> Result<guide::Etape, guide::GuideError> {
    guiding(move || {
        guide::envoyer(&envoi, &mut |stage| {
            let _ = app.emit_to("guide", "guide-progress", stage);
        })
    })
    .await
}

/// « Oui, c'est fait » or « Je réessaie », after « On dirait que rien n'a changé ».
#[tauri::command]
async fn guide_trancher(faite: bool) -> Result<guide::Etape, guide::GuideError> {
    guiding(move || guide::trancher(faite)).await
}

#[tauri::command]
fn guide_annuler() {
    guide::annuler();
}

/// « Fermer le guide »: the session forgotten, images included.
#[tauri::command]
fn guide_fin() {
    guide::fin();
}

/// G10: the procedure drafted from the steps.
#[tauri::command]
async fn guide_brouillon() -> Result<guide::Brouillon, guide::GuideError> {
    guiding(guide::brouillon).await
}

/// « Enregistrer dans mon cerveau »: what the user kept of the draft, read again before it is written.
#[tauri::command]
async fn guide_enregistrer(note: guide::NoteGuide) -> Result<guide::Enregistre, guide::GuideError> {
    guiding(move || guide::enregistrer(&note)).await
}

/// The picker of the system, opened from Rust: the window opens no dialog itself and never gives a
/// path. Made on the main thread and awaited off it, as Tauri's dialog plugin does on every system.
fn pick_export(app: &tauri::AppHandle) -> Option<Vec<PathBuf>> {
    let (tx, rx) = std::sync::mpsc::channel();
    let window = app.get_webview_window("main");
    app.run_on_main_thread(move || {
        let mut dialog = rfd::AsyncFileDialog::new()
            .set_title("Ton export de ChatGPT ou de Claude")
            .add_filter("Export (.zip, .json)", &["zip", "json"]);
        if let Some(w) = &window {
            dialog = dialog.set_parent(w);
        }
        let picked = dialog.pick_files();
        std::thread::spawn(move || {
            let _ = tx.send(tauri::async_runtime::block_on(picked));
        });
    })
    .ok()?;
    let files = rx.recv().ok().flatten()?;
    Some(files.iter().map(|f| f.path().to_path_buf()).collect())
}

/// println! panics when the reader goes away (`--diagnose | head`): write and move on.
fn out(line: &str) {
    use std::io::Write;
    let _ = writeln!(std::io::stdout(), "{line}");
}

fn out_json(value: &impl serde::Serialize) {
    out(&serde_json::to_string_pretty(value).unwrap_or_default());
}

/// `--repair-step <id> [--yes]`: the window's whole repair path without the window,
/// for end-to-end tests with a fake claude and for support. Runs the step; if it
/// fails, asks Claude once; with --yes, applies the plan. One JSON document per stage.
fn repair_step(id: &str, yes: bool) -> i32 {
    let key = std::env::var("VOYAGE_API_KEY").ok().filter(|k| !k.is_empty());
    let r = run_traced(id, key.as_deref(), &mut |l| out(&format!("  {l}")));
    out_json(&r);
    if r.ok {
        return 0;
    }
    let status = repair::availability(id);
    out_json(&status);
    if status.state != repair::RepairState::Ready {
        return 1;
    }
    let plan = match repair::analyze(id, &mut |stage| out_json(&stage)) {
        Ok(plan) => plan,
        Err(e) => {
            out_json(&e);
            return 1;
        }
    };
    out_json(&plan);
    if !yes {
        return 1;
    }
    let outcome = repair::apply(&plan.plan_id, key.as_deref(), &mut |_, l| out(&format!("  {l}")));
    out_json(&outcome);
    i32::from(outcome.outcome != repair::Ending::Fixed)
}

/// `--guide-ask <png> --objectif "…" [--logiciel "…"]`: one look of the guide without the window, the
/// capture from a file, for end-to-end tests with a fake claude and for support. One JSON document
/// per stage; the image itself is never printed. `--guide-ask --print-consignes`: the rules the app
/// embeds, byte for byte (the terminal skill reads the same file).
fn guide_ask(args: &[String]) -> i32 {
    if args.first().is_some_and(|a| a == "--print-consignes") {
        use std::io::Write;
        let _ = std::io::stdout().write_all(guide::CONSIGNES.as_bytes());
        return 0;
    }
    let flag = |name: &str| args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).cloned().unwrap_or_default();
    let Some(png) = args.first().filter(|a| !a.starts_with("--")) else {
        out("usage : --guide-ask <capture.png> --objectif \"…\" [--logiciel \"…\"]");
        return 2;
    };
    out_json(&guide::status());
    let res = guide::deposer(Path::new(png))
        .inspect(|a| out_json(&serde_json::json!({ "largeur": a.largeur, "hauteur": a.hauteur, "noir": a.noir, "petit": a.petit })))
        .and_then(|a| guide::commencer(&flag("--logiciel"), &flag("--objectif")).inspect(out_json).map(|_| a.capture))
        .and_then(|capture| {
            let envoi = guide::Envoi { demande: guide::Demande::Debut, remarque: None, choix: None, capture: Some(capture) };
            guide::envoyer(&envoi, &mut |stage| out_json(&stage))
        });
    guide::fin();
    match res {
        Ok(etape) => {
            out_json(&etape);
            0
        }
        Err(e) => {
            out_json(&e);
            1
        }
    }
}

pub fn run() {
    // Command-line mode, for tests and support: no window. The Voyage key comes
    // from VOYAGE_API_KEY, never from the command line.
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("--diagnose") => {
            out_json(&diagnose::run());
            return;
        }
        Some("--repair-step") => {
            let id = args.get(2).cloned().unwrap_or_default();
            let yes = args.get(3).is_some_and(|a| a == "--yes");
            std::process::exit(repair_step(&id, yes));
        }
        Some("--guide-ask") => std::process::exit(guide_ask(&args[2..])),
        Some("--run-step") => {
            let id = args.get(2).cloned().unwrap_or_default();
            let key = std::env::var("VOYAGE_API_KEY").ok().filter(|k| !k.is_empty());
            let r = steps::run(&id, key.as_deref(), &mut |l| out(&format!("  {l}")));
            out_json(&r);
            std::process::exit(if r.ok { 0 } else { 1 });
        }
        _ => {}
    }

    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            diagnose_machine,
            run_step,
            check_voyage_key,
            claude_logged_in,
            agents_offer,
            choose_agents,
            codex_login,
            repair_status,
            repair_preview,
            repair_analyze,
            repair_cancel,
            repair_apply,
            repair_undo,
            repair_report,
            open_claude_login,
            open_codex_login,
            start_onboarding,
            open_vault,
            open_link,
            open_settings,
            open_repair_terminal,
            machine_advice,
            import_check,
            import_offer,
            import_phrase,
            import_pick,
            import_paste,
            import_preview,
            import_run,
            import_cancel,
            import_forget,
            import_open_note,
            open_claude_update,
            hermes_device,
            open_hermes_device_page,
            guide_open,
            guide_offert,
            guide_fermer,
            guide_epingler,
            guide_taille,
            guide_status,
            guide_compris,
            guide_commencer,
            guide_coller,
            guide_prendre_depot,
            guide_cacher,
            guide_envoi_texte,
            guide_envoyer,
            guide_trancher,
            guide_annuler,
            guide_fin,
            guide_brouillon,
            guide_enregistrer
        ])
        .run(tauri::generate_context!())
        .expect("error while running Brain in a Box");
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    fn read(path: &str) -> String {
        std::fs::read_to_string(format!("{}/{path}", env!("CARGO_MANIFEST_DIR"))).unwrap()
    }

    /// The names between `start` and the next `]`, as Rust writes a list of them.
    fn names(text: &str, start: &str) -> BTreeSet<String> {
        let from = text.find(start).unwrap_or_else(|| panic!("{start}")) + start.len();
        let list = &text[from..from + text[from..].find(']').unwrap()];
        list.split(|c: char| !(c.is_ascii_alphanumeric() || c == '_')).filter(|w| !w.is_empty()).map(String::from).collect()
    }

    /// The app's commands a window's capability allows, `core:default` alone besides them.
    fn allowed(file: &str, window: &str) -> BTreeSet<String> {
        let cap: serde_json::Value = serde_json::from_str(&read(file)).unwrap();
        assert_eq!(cap["windows"], serde_json::json!([window]), "{file}");
        let perms: Vec<&str> = cap["permissions"].as_array().unwrap().iter().map(|p| p.as_str().unwrap()).collect();
        assert_eq!(perms.iter().filter(|p| !p.starts_with("allow-")).collect::<Vec<_>>(), [&"core:default"], "{file}");
        // Tauri names the permission of `run_step` allow-run-step.
        perms.iter().filter_map(|p| p.strip_prefix("allow-")).map(|c| c.replace('-', "_")).collect()
    }

    /// Every string of a page that names a command (`invoke('run_step')`, a table of them).
    fn named_in(page: &str, commands: &BTreeSet<String>) -> BTreeSet<String> {
        let text = read(page);
        commands.iter().filter(|c| text.contains(&format!("'{c}'"))).cloned().collect()
    }

    /// The guide's window shows Claude's words: it may call the guide's commands and four others, never
    /// the installer's (a step to run, a repair to apply, a terminal to open). Each window gets what its
    /// page calls, and every command is in the app's manifest, without which Tauri checks nothing.
    #[test]
    fn each_window_calls_its_own_commands_only() {
        let commands = names(&read("src/lib.rs"), "generate_handler![");
        assert_eq!(names(&read("build.rs"), "const COMMANDS: &[&str] = &["), commands, "build.rs's manifest is the app's commands");
        let main = allowed("capabilities/default.json", "main");
        let guide = allowed("capabilities/guide.json", "guide");
        let shared: BTreeSet<String> = ["open_link", "open_claude_login", "claude_logged_in", "open_claude_update"].map(String::from).into();
        let guides: BTreeSet<String> = commands.iter().filter(|c| c.starts_with("guide_") && !["guide_open", "guide_offert"].contains(&c.as_str())).cloned().collect();
        assert_eq!(guide, guides.union(&shared).cloned().collect(), "the guide's window");
        assert!(main.iter().all(|c| commands.contains(c)) && guide.iter().all(|c| commands.contains(c)));
        assert!(main.is_disjoint(&guides), "the installer's window has none of the guide's but its door: {:?}", main.intersection(&guides).collect::<Vec<_>>());
        for c in named_in("../src/app.js", &commands) {
            assert!(main.contains(&c), "app.js calls {c}");
        }
        for c in named_in("../src/guide.js", &commands) {
            assert!(guide.contains(&c), "guide.js calls {c}");
        }
        let nobodys: Vec<&String> = commands.iter().filter(|c| !main.contains(*c) && !guide.contains(*c)).collect();
        assert!(nobodys.is_empty(), "no window may call {nobodys:?}");
    }
}
