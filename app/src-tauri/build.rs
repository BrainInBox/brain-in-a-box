// The app's commands, in an app manifest: without one, Tauri checks no capability for them, and any
// window could call any command. With it, each window gets only what its capability allows
// (capabilities/*.json): the guide's window, which shows Claude's words, none of the installer's.
const COMMANDS: &[&str] = &[
    "diagnose_machine",
    "run_step",
    "check_voyage_key",
    "claude_logged_in",
    "agents_offer",
    "choose_agents",
    "codex_login",
    "repair_status",
    "repair_preview",
    "repair_analyze",
    "repair_cancel",
    "repair_apply",
    "repair_undo",
    "repair_report",
    "open_claude_login",
    "open_codex_login",
    "start_onboarding",
    "open_vault",
    "brain_folder",
    "brain_complete",
    "open_link",
    "open_settings",
    "open_repair_terminal",
    "machine_advice",
    "import_check",
    "import_phrase",
    "import_pick",
    "import_paste",
    "import_preview",
    "import_run",
    "import_cancel",
    "import_forget",
    "import_open_note",
    "open_claude_update",
    "guide_open",
    "guide_offert",
    "guide_fermer",
    "guide_epingler",
    "guide_taille",
    "guide_status",
    "guide_compris",
    "guide_commencer",
    "guide_coller",
    "guide_coller_presse_papiers",
    "guide_prendre_depot",
    "guide_fenetres",
    "guide_attendre_clic",
    "guide_choisir",
    "guide_regarder",
    "guide_cacher",
    "guide_envoi_texte",
    "guide_envoyer",
    "guide_trancher",
    "guide_annuler",
    "guide_fin",
    "guide_brouillon",
    "guide_enregistrer",
    "guide_calque_montrer",
    "guide_calque_effacer",
    "start_claude",
    "save_report",
];

/// Tauri's own Windows manifest (tauri-build's windows-app-manifest.xml): Common Controls 6.
const COMMON_CONTROLS_6: &str = r#"<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <dependency>
    <dependentAssembly>
      <assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0" processorArchitecture="*" publicKeyToken="6595b64144ccf1df" language="*" />
    </dependentAssembly>
  </dependency>
</assembly>
"#;

fn main() {
    let manifest = tauri_build::AppManifest::new().commands(COMMANDS);
    let mut attributes = tauri_build::Attributes::new().app_manifest(manifest);
    // On Windows, the linker embeds Tauri's manifest in every binary of the package: the app, as tauri-build
    // would, and the lib's tests, which run Tauri's own windows (capture/vm.rs) and would not start without
    // it (STATUS_ENTRYPOINT_NOT_FOUND: Common Controls 6 only has TaskDialogIndirect). tauri-build then
    // embeds none, or the app would have two.
    let var = |k: &str| std::env::var(k).unwrap_or_default();
    if var("CARGO_CFG_TARGET_OS") == "windows" && var("CARGO_CFG_TARGET_ENV") == "msvc" {
        let path = std::path::Path::new(&var("OUT_DIR")).join("windows-app-manifest.xml");
        std::fs::write(&path, COMMON_CONTROLS_6).expect("the Windows manifest");
        println!("cargo:rustc-link-arg=/MANIFEST:EMBED");
        println!("cargo:rustc-link-arg=/MANIFESTUAC:NO");
        println!("cargo:rustc-link-arg=/MANIFESTINPUT:{}", path.display());
        attributes = attributes.windows_attributes(tauri_build::WindowsAttributes::new_without_app_manifest());
    }
    tauri_build::try_build(attributes).expect("tauri-build");
}
