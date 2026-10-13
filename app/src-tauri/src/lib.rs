mod diagnose;
mod model;
mod steps;
mod sys;
mod voyage;

use tauri::Emitter;

#[tauri::command]
async fn diagnose_machine() -> model::Diagnosis {
    tauri::async_runtime::spawn_blocking(diagnose::run).await.expect("diagnosis panicked")
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
    tauri::async_runtime::spawn_blocking(move || voyage::check_key(&key)).await.expect("key check panicked")
}

#[tauri::command]
async fn claude_logged_in() -> bool {
    tauri::async_runtime::spawn_blocking(steps::claude_logged_in).await.unwrap_or(false)
}

#[tauri::command]
fn open_claude_login() -> Result<(), String> {
    steps::open_claude_login()
}

#[tauri::command]
fn start_onboarding() -> Result<(), String> {
    steps::start_onboarding()
}

#[tauri::command]
fn open_vault() -> Result<(), String> {
    steps::open_vault()
}

/// Opens one of a few known pages (Voyage dashboard, downloads) in the default browser.
#[tauri::command]
fn open_link(url: String) -> Result<(), String> {
    steps::open_link(&url)
}

pub fn run() {
    // Command-line mode, for tests and support: no window. The Voyage key comes
    // from VOYAGE_API_KEY, never from the command line.
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("--diagnose") => {
            println!("{}", serde_json::to_string_pretty(&diagnose::run()).unwrap_or_default());
            return;
        }
        Some("--run-step") => {
            let id = args.get(2).cloned().unwrap_or_default();
            let key = std::env::var("VOYAGE_API_KEY").ok().filter(|k| !k.is_empty());
            let r = steps::run(&id, key.as_deref(), &mut |l| println!("  {l}"));
            println!("{}", serde_json::to_string_pretty(&r).unwrap_or_default());
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
            open_claude_login,
            start_onboarding,
            open_vault,
            open_link
        ])
        .run(tauri::generate_context!())
        .expect("error while running Brain in a Box");
}
