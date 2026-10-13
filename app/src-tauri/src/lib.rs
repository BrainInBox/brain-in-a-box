mod detect;
mod diagnose;
mod model;
mod repair;
mod steps;
mod sys;
mod voyage;

use tauri::Emitter;

#[tauri::command]
async fn diagnose_machine() -> model::Diagnosis {
    tauri::async_runtime::spawn_blocking(diagnose::run).await.unwrap_or_else(|e| diagnose::failed(&e.to_string()))
}

/// Runs one install step; its output arrives as "step-log" events.
#[tauri::command]
async fn run_step(app: tauri::AppHandle, id: String, voyage_key: Option<String>) -> model::StepResult {
    let step = id.clone();
    let res = tauri::async_runtime::spawn_blocking(move || {
        let tag = id.clone();
        steps::run(&id, voyage_key.as_deref(), &mut |line| {
            let _ = app.emit("step-log", model::LogLine { step: tag.clone(), line });
        })
    })
    .await;
    res.unwrap_or_else(|e| model::StepResult { id: step, ok: false, message: format!("Erreur interne : {e}") })
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
        .unwrap_or(repair::RepairStatus { state: repair::RepairState::Disabled })
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

/// println! panics when the reader goes away (`--diagnose | head`): write and move on.
fn out(line: &str) {
    use std::io::Write;
    let _ = writeln!(std::io::stdout(), "{line}");
}

pub fn run() {
    // Command-line mode, for tests and support: no window. The Voyage key comes
    // from VOYAGE_API_KEY, never from the command line.
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("--diagnose") => {
            out(&serde_json::to_string_pretty(&diagnose::run()).unwrap_or_default());
            return;
        }
        Some("--run-step") => {
            let id = args.get(2).cloned().unwrap_or_default();
            let key = std::env::var("VOYAGE_API_KEY").ok().filter(|k| !k.is_empty());
            let r = steps::run(&id, key.as_deref(), &mut |l| out(&format!("  {l}")));
            out(&serde_json::to_string_pretty(&r).unwrap_or_default());
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
            open_claude_login,
            start_onboarding,
            open_vault,
            open_link
        ])
        .run(tauri::generate_context!())
        .expect("error while running Brain in a Box");
}
