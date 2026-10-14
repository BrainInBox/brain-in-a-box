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
CI=true cargo tauri build --bundles dmg  # target/release/bundle/dmg/*.dmg
```

The build is not signed yet. On another Mac, macOS blocks it: open it once from System Settings → Privacy & Security → "Open Anyway". A signed, notarized build needs an Apple Developer account.

## Check without the window

```bash
./target/debug/brain-in-a-box --diagnose          # JSON: what is there, what is missing
./target/debug/brain-in-a-box --run-step bun      # one step: clt, bun, claude, obsidian, brain (Windows: claude, brain)
```

`--run-step brain` reads the Voyage key from `VOYAGE_API_KEY`, never from the command line, and `BIAB_ARCHIVE` (a local `.zip` or an https URL) replaces the GitHub sources. On Windows, the release build has no console: redirect its output (`brain-in-a-box.exe --diagnose > diag.json`), or use the debug build.

## Windows

Built on Windows (PowerShell, with the MSVC build tools and WebView2, which Windows 11 has), with the command the release uses:

```powershell
cd app\src-tauri
cargo tauri build --ci --target x86_64-pc-windows-msvc --bundles nsis -- --locked
# target\x86_64-pc-windows-msvc\release\bundle\nsis\*-setup.exe
```

`tauri.windows.conf.json` makes it an NSIS installer for the current user only (no administrator), in French, with `icons/icon.ico`. The CI builds it the same way, installs it silently, checks it went to the user's own programs and starts it (`--diagnose`); a pull request from this repository, or master, keeps it as the artifact `brain-in-a-box-windows-preview`, a fork's pull request does not. It is not signed: SmartScreen warns, "Informations complémentaires" → "Exécuter quand même". A signed build needs a code signing certificate.

- **Where the brain goes**: `Documents\Brain`, or `%USERPROFILE%\Brain` when Windows' controlled folder access protects Documents (install.ps1 asks `Get-MpPreference`, the app reads the folder it saved in `BRAIN_DIR`). Neither ever suggests allowing PowerShell, git or Python into protected folders: every script they run would get in too. If writing there fails anyway, the repair moves the brain out of Documents (`cerveau_hors_documents`), only while nothing is in it yet.
- **Work folders** (a downloaded installer, the folder Claude reads during a repair) go to `%USERPROFILE%\.brain-in-a-box\tmp`, never to `TEMP`, which may be a folder every account of the PC can write to.
- **Updating a brain installed before the app**: install.ps1 now keeps `~\.brain-in-a-box\installed.json`, as install.sh does. For an older install, the configuration guard takes a hook still identical to the sources it came from (`~\.brain-in-a-box\src`) as the kit's, and a hook in `settings.json` re-pointed to another Python or to the exec form as the same hook.

On a Mac, `cargo check --target x86_64-pc-windows-msvc` works once the target is added with rustup, given a stand-in for `llvm-rc` (tauri-build compiles the Windows resources): any script named in `RC` that writes an empty file at its `/fo` argument. Linking needs Windows.

## Tests

```bash
cargo test                                         # logic, with fake homes
cargo test -- --ignored obsidian_real --nocapture  # Mac: real Obsidian download (233 MB) into a temp folder
```

On Windows, `cargo test` runs the same logic, plus Windows' own: processes started without a console and stopped with everything they started, PowerShell quoting, the Python install.ps1 would pick, its sections. The CI (`.github/workflows/test.yml`) runs both systems, then, on a fresh Windows with only the Store's `python.exe` stub, Documents protected and a shared `TEMP`, the app's two steps for real (`--run-step claude`, `--run-step brain` with this commit's `install.ps1`): the diagnosis must find nothing left to install, with winget's Python. It then turns that brain into one from before the app (no manifest, an older hook, hooks wired to another Python) and runs the brain step again in a folder with an accent and a space: nothing is put back, and install.ps1's lines reach the app whole.

`dev/mock.html` opens the interface in a browser with a fake backend (`?s=` picks a scenario, `&os=windows` the PC's words) to check every screen.
