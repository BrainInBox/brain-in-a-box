mod agents;
mod codex;
mod config_guard;
mod detect;
mod diagnose;
mod model;
mod perms;
mod redact;
mod remedies;
mod repair;
#[cfg(target_os = "linux")]
mod root;
mod steps;
mod sys;
mod voyage;

use tauri::Emitter;

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
            repair_status,
            repair_preview,
            repair_analyze,
            repair_cancel,
            repair_apply,
            repair_undo,
            repair_report,
            open_claude_login,
            start_onboarding,
            open_vault,
            open_link,
            open_settings,
            open_repair_terminal
        ])
        .run(tauri::generate_context!())
        .expect("error while running Brain in a Box");
}
