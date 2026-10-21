# Brain in a Box — the app (preview, Mac, Windows and Linux)

A small window that installs brain-in-a-box for people who never open a terminal. Bibo, the mascot, checks the computer, installs what is missing, offers an optional Voyage AI key, then hands over to Claude Code for the login and the onboarding. Nothing needs admin rights. The interface is in French.

- **Mac**: Apple's command line tools, Bun, Claude Code and Obsidian, then this repo's `install.sh`.
- **Windows**: Claude Code first, then this repo's `install.ps1`, which brings Python, Git, Bun, the memory (gbrain) and Obsidian along with the brain. The app runs it without a console window and without a question (the Voyage key goes through `VOYAGE_API_KEY`), and shows its sections as it goes. Windows 10 (1809) or 11.
- **Linux** (Ubuntu, Debian and their derivatives): the system's tools first when some are missing (git, python3, curl, unzip, procps: a fresh Ubuntu desktop lacks git and curl), then Claude Code, Bun, Obsidian's official `.deb` (pinned, its SHA-256 checked) and this repo's `install.sh`, run without a terminal (systemd user timers when the session's systemd answers, else cron). Only the system's tools and Obsidian need root: the system's own password window (pkexec) runs a fixed `apt-get install` (`src-tauri/src/root.rs`; the package is copied into root's folder and checked there before apt reads it). Without pkexec, the step gives the command to type. Another distribution: the window lists what to install. arm64: Obsidian is left to the user (no official package).

It does not touch Anthropic credentials: the user logs in to the official `claude` CLI in a window it opens (Terminal on a Mac, PowerShell on Windows, the desktop's terminal on Linux: `claude auth login`).

## Build

Needs Rust 1.85+ and the Tauri CLI (2.11.5 is the last one that builds with Rust 1.85):

```bash
cargo install tauri-cli --version "=2.11.5" --locked
cd app/src-tauri
cargo tauri dev                          # run it
CI=true cargo tauri build --bundles dmg  # target/release/bundle/dmg/*.dmg

# what the release ships: one DMG for Apple Silicon and Intel, ad-hoc signed
rustup target add aarch64-apple-darwin x86_64-apple-darwin
APPLE_SIGNING_IDENTITY=- cargo tauri build --ci --target universal-apple-darwin --bundles dmg -- --locked
```

Every push to master that touches `app/` publishes that DMG as a pre-release on GitHub (`app-v<version>-preview.<n>`, the 5 newest kept, with `SHA256SUMS`), and a pull request gets it as an artifact of its run (not a fork's): `.github/workflows/release.yml`. The Windows installer and the Linux AppImage and `.deb` join the same release (the Tauri configs declare them).

The build that publishes uses no cache, and GitHub attests each file it publishes. A file of a preview was built by that workflow from master if this passes:

```bash
gh attestation verify <file> -R BrainInBox/brain-in-a-box \
  --signer-workflow BrainInBox/brain-in-a-box/.github/workflows/release.yml --source-ref refs/heads/master
```

The build is not signed by Apple, only ad-hoc (an app with no signature at all would read as "damaged" once downloaded). On another Mac, macOS blocks it: open it once from System Settings → Privacy & Security → "Open Anyway". A signed, notarized build needs an Apple Developer account.

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

`tauri.windows.conf.json` makes it an NSIS installer for the current user only (no administrator), in French, with `icons/icon.ico`. The CI (`test.yml`) builds it the same way, installs it silently, checks it went to the user's own programs and starts it (`--diagnose`). The file people download comes from `release.yml` only: the app previews on GitHub Releases, or the artifact of a pull request's `app-release` run (none for a fork's). It is not signed: SmartScreen warns, "Informations complémentaires" → "Exécuter quand même". A signed build needs a code signing certificate.

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

`dev/mock.html` opens the interface in a browser with a fake backend (`?s=` picks a scenario, `&os=windows` or `&os=linux` that system's words) to check every screen. `dev/guide-mock.html` does the same for the guide's window (`?g=` what guide.rs answers, on a made-up program drawn in SVG). `node dev/screens.mjs` runs both in a small DOM and compares every screen with `dev/screens-claude.txt` and `dev/screens-guide.txt` (the CI's app job does too).

## Linux

Built on Ubuntu 22.04 for the release (what it builds runs on 22.04 and later), with WebKitGTK 4.1 and GTK 3:

```bash
sudo apt install libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev libayatana-appindicator3-dev libssl-dev libxdo-dev patchelf file
bash ../../.github/ci/appimage-tools.sh      # the AppImage tools and runtime, pinned and checked (else downloaded unchecked)
export LDAI_RUNTIME_FILE=~/.cache/tauri/runtime-x86_64   # the pinned runtime, not type2-runtime's "continuous" of the day
cargo tauri build --ci --target x86_64-unknown-linux-gnu --bundles deb,appimage -- --locked
bash ../../.github/ci/appimage-tools.sh check target/x86_64-unknown-linux-gnu/release/bundle/appimage/*.AppImage
# target/x86_64-unknown-linux-gnu/release/bundle/{deb,appimage}/
```

`tauri.linux.conf.json` makes the AppImage (no install, for the user alone; it mounts itself with the system's `fusermount3`, on every Ubuntu desktop since 22.04) and the `.deb` (`/usr/bin/brain-in-a-box`, a desktop entry, depends on `libwebkit2gtk-4.1-0`). Neither is signed; `SHA256SUMS` and the attestation are the check.

- **Run from an AppImage**, the app has the AppImage's own Python home, libraries and GTK modules in its environment (AppImageKit's AppRun, linuxdeploy's GTK hook): none of it reaches the programs it starts (`sys.rs`, `APPIMAGE_ENV`), or the system's python3 would not start (`tests/appimage_env.rs`).
- **Work folders** go to the session's own `/run/user/<uid>` (private, named after no one), else `~/.brain-in-a-box/tmp`: never `/tmp`, which every account shares.
- **The terminal** for the login, the onboarding and Plan B is the desktop's (`x-terminal-emulator`, else GNOME Terminal, Ptyxis, Console, Konsole, Xfce's, MATE's, xterm). Links open with `xdg-open`. No settings pane: a repair's human action is said in words.
- `/home/<name>` is masked like `/Users/<name>`, and the user's own home wherever it is.

The CI (`test.yml`) runs `cargo test` and clippy on Ubuntu 24.04 (the real `install.sh` under the configuration guard, the brain step from an AppImage-like environment, the root scripts against a stand-in pkexec), checks `--diagnose`, and builds both packages as the release does, on 22.04: the AppImage starts with the pinned runtime, the `.deb` installs with apt and answers, the AppImage too, on 22.04 and on 24.04. Every night, `terrain.yml` runs the AppImage built that way (job `app-linux-build`) on an Ubuntu 24.04 desktop without git nor curl and clicks through its real window (job `app-linux`).

- **Root** (`src/root.rs`, reviewed by the maintainer): two fixed scripts through pkexec, one for the packages of its list, one for the file whose SHA-256 it holds (Obsidian's). The password window shows a command's first 38 and last 37 characters (pkexec 124): each script starts with a comment naming what it does, and its `$0` is a label, so both ends read « Brain in a Box installe » and the packages, or Obsidian.
