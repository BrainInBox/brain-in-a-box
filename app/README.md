# Brain in a Box — Mac app (preview)

A small window that installs brain-in-a-box for people who never open a terminal. Bibo, the mascot, checks the Mac, installs what is missing (Apple's command line tools, Bun, Claude Code, Obsidian), runs this repo's `install.sh`, offers an optional Voyage AI key, then hands over to Claude Code for the login and the onboarding. Nothing needs admin rights. The interface is in French.

It does not touch Anthropic credentials: the user logs in to the official `claude` CLI in Terminal (`claude auth login`).

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

Every push to master that touches `app/` publishes that DMG as a pre-release on GitHub (`app-v<version>-preview.<n>`, the 5 newest kept, with `SHA256SUMS`), and a pull request gets it as an artifact of its run: `.github/workflows/release.yml`. The Windows installer joins the same release once the Tauri config declares an NSIS bundle.

The build is not signed by Apple, only ad-hoc (an app with no signature at all would read as "damaged" once downloaded). On another Mac, macOS blocks it: open it once from System Settings → Privacy & Security → "Open Anyway". A signed, notarized build needs an Apple Developer account.

## Check without the window

```bash
./target/debug/brain-in-a-box --diagnose          # JSON: what is there, what is missing
./target/debug/brain-in-a-box --run-step bun      # one step: clt, bun, claude, obsidian, brain
```

`--run-step brain` reads the Voyage key from `VOYAGE_API_KEY`, never from the command line, and `BIAB_ARCHIVE` (a local `.zip` or an https URL) replaces the GitHub sources.

## Tests

```bash
cargo test                                         # logic, with fake homes
cargo test -- --ignored obsidian_real --nocapture  # real Obsidian download (233 MB) into a temp folder
```

`dev/mock.html` opens the interface in a browser with a fake backend (`?s=` picks a scenario) to check every screen.
