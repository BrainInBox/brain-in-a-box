# Brain in a Box — the app (preview, Mac and Windows)

A small window that installs brain-in-a-box for people who never open a terminal. Bibo, the mascot, checks the computer, installs what is missing, offers an optional Voyage AI key, then hands over to Claude Code for the login and the onboarding. Nothing needs admin rights. The interface is in French.

- **Mac**: Apple's command line tools, Bun, Claude Code and Obsidian, then this repo's `install.sh`.
- **Windows**: Claude Code first, then this repo's `install.ps1`, which brings Python, Git, Bun, the memory (gbrain) and Obsidian along with the brain. The app runs it without a console window and without a question (the Voyage key goes through `VOYAGE_API_KEY`), and shows its sections as it goes. Windows 10 (1809) or 11.

It does not touch Anthropic credentials: the user logs in to the official `claude` CLI in a window it opens (Terminal on a Mac, PowerShell on Windows: `claude auth login`).

## Build

Needs Rust 1.85+ and the Tauri CLI (2.11.5 is the last one that builds with Rust 1.85):

```bash
cargo install tauri-cli --version "=2.11.5" --locked
cd app/src-tauri
cargo tauri dev                          # run it
CI=true cargo tauri build --bundles dmg  # Mac: target/release/bundle/dmg/*.dmg
```

On Windows (PowerShell, with the MSVC build tools and WebView2, which Windows 11 has):

```powershell
cd app\src-tauri
cargo tauri build --bundles nsis         # target\release\bundle\nsis\*-setup.exe
```

`tauri.windows.conf.json` makes it an NSIS installer for the current user only (no administrator), in French, with `icons/icon.ico`. The CI builds it on every pull request (artifact `brain-in-a-box-windows-preview`).

Neither build is signed yet. On another Mac, macOS blocks it: open it once from System Settings → Privacy & Security → "Open Anyway". On Windows, SmartScreen warns: "Informations complémentaires" → "Exécuter quand même". Signed builds need an Apple Developer account and a code signing certificate.

On a Mac, `cargo check --target x86_64-pc-windows-msvc` works once the target is added with rustup, given a stand-in for `llvm-rc` (tauri-build compiles the Windows resources): any script named in `RC` that writes an empty file at its `/fo` argument. Linking needs Windows.

## Check without the window

```bash
./target/debug/brain-in-a-box --diagnose          # JSON: what is there, what is missing
./target/debug/brain-in-a-box --run-step bun      # one step: clt, bun, claude, obsidian, brain (Windows: claude, brain)
```

`--run-step brain` reads the Voyage key from `VOYAGE_API_KEY`, never from the command line, and `BIAB_ARCHIVE` (a local `.zip` or an https URL) replaces the GitHub sources. On Windows, the release build has no console: redirect its output (`brain-in-a-box.exe --diagnose > diag.json`), or use the debug build.

## Tests

```bash
cargo test                                         # logic, with fake homes
cargo test -- --ignored obsidian_real --nocapture  # Mac: real Obsidian download (233 MB) into a temp folder
```

On Windows, `cargo test` runs the same logic, plus Windows' own: processes started without a console and stopped with everything they started, PowerShell quoting, the Python install.ps1 would pick, its sections. The CI (`.github/workflows/test.yml`) runs both systems, then, on a fresh Windows, the app's two steps for real (`--run-step claude`, `--run-step brain` with this commit's `install.ps1`) and checks the diagnosis has nothing left to install.

`dev/mock.html` opens the interface in a browser with a fake backend (`?s=` picks a scenario, `&os=windows` the PC's words) to check every screen.
