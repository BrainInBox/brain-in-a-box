# Changelog

All notable changes to brain-in-a-box.

Format inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **Mac app (preview), `app/`**: installs everything without a terminal. Bibo, the mascot, checks the Mac (macOS version, disk, network, Apple tools, Bun, Claude Code and its login, Obsidian, the brain, search by meaning), installs what is missing without admin rights or Homebrew (Apple's own tools window, pinned Bun, Anthropic's official Claude Code installer, the pinned and signature-checked Obsidian DMG), then runs this repo's `install.sh`. Optional Voyage key with a one-token check. The user logs in to Claude Code in Terminal, the app never touches credentials. Tauri v2, plain HTML/JS, French. Unsigned for now. See `app/README.md`.

### Changed
- **The daily reflection can no longer be steered into editing your vault.** Claude used to run with your whole setup (hooks, MCP servers, allow rules, `acceptEdits`) on transcripts that can carry web text. It now runs with no tool at all, no settings, a short environment, in a private folder (`engine/hooks/_headless_claude.py`, checked at start-up, claude.ai login only), and returns JSON; the script writes the four journal sections and the day's entry in `memory.md` itself, keeps every line already there exactly as written (Claude can add, never reword, untick or drop), keeps the rest byte for byte, never writes a backquote or `<%` (Dataview and Templater would run them as code), and flags days whose text spoke to an AI. It no longer prunes `memory.md`.
- **Windows gets the same memory as macOS: GBrain, with search by meaning when you add a Voyage key.** A spike on four Windows runners showed gbrain #1549 (pgvector missing from PGLite on Windows) does not reproduce, so `install.ps1` now installs it:
  - **Bun** (official installer, per user, no admin) and **GBrain** at a commit the Windows CI tests, fetched alone (46 MB instead of 600). The install line, run again, moves to the next tested commit; `gbrain upgrade` is turned off so nothing bypasses that.
  - **Voyage key, optional**, same explanation and one-token check as on macOS. Asked only when a console is there; otherwise read from `VOYAGE_API_KEY`. The key goes to `~\.gbrain\config.json` through Python's environment, never in a command line or a log (masked there in case a tool prints it). As with `env -u` on macOS, what is not GBrain (winget, Bun's installer, `bun install`'s dependency scripts, git, Claude Code's installer) runs without `VOYAGE_API_KEY` in its environment. Without a key, search works on keywords; a key given on a later run moves the brain to search by meaning.
  - **Your vault is registered as a GBrain source and indexed**; notes the hooks wrote since a previous install are committed first, since GBrain reads the vault through git.
  - **Search never goes away.** `gbq.cmd` and the Git Bash `gbq` go through `engine/search/gbq.py`: GBrain when it is installed, retried once when a long write holds the brain, and the offline keyword index (brain_search, still installed and rebuilt) when GBrain is missing, broken, busy, or still running after 60 s (killed with its Bun process). If GBrain cannot be installed, the install finishes in keyword mode and says so.
  - **One nightly task, `BrainNightly`** (`engine/nightly/windows-nightly.py`, replaces `BrainSearchReindex`): commits the vault, `gbrain sync`, `gbrain embed --stale --max-usd 0.10`, then rebuilds the keyword index. Log in `~\.gbrain\nightly.log`, install log in `~\.brain-in-a-box\install.log`.
  - Existing Windows installs migrate by running the install line again, and lose nothing: a hook, `gbq` or search script that differs from the new version is copied to `~\.brain-in-a-box\backups\<date>\` before being replaced (as `install.sh` does since #29), `settings.json` is kept as `settings.json.bak-<date>` when the merge changes it, and `BrainSearchReindex` is removed only once `BrainNightly` is registered.
- **Embeddings move to Voyage AI, and the key is optional** (macOS). `install.sh` explains how to get a Voyage key and accepts Enter to skip: the brain then runs on keyword search (`gbrain init --no-embedding`) and gains search by meaning when you re-run with a key. The key is checked with a one-token request before use, travels through stdin only (never in a command line), and lands in `config.json` created with mode 600. The reranker is set to `voyage:rerank-3-lite`, inside Voyage's free tokens (gbrain's default, `rerank-2.5`, is billed from the first search). Existing ZeroEntropy installs are left as they are. A non-interactive run (an app, `curl | bash`) no longer dies on the key prompt; `VOYAGE_API_KEY` supplies the key.

### Added
- **Claude Code skills, installed with the box** (merged from the Hermes Boost pack, rewritten for Claude Code; Hermes is no longer a target):
  - `brain-boost`: what this setup already does, how to get a beginner going (the "magic prompt", one automation at a time), and how to grow it safely: skills, app connections (claude.ai connectors, remote and local MCP servers), plugins, scheduled jobs. House rules: audit before installing anything third-party, pin versions, "done" means verified.
  - `quick-research`: a fast, sourced answer in the chat, checking the user's notes (`gbq`) before the web.
  - `html-artifact`: one self-contained HTML page (report, plan, diagram, comparison), with a headless-browser preview so Claude checks what it built. Adapted from Anthropic's html-effectiveness gallery (MIT).
  - `convert-documents-to-markdown`: reads Word, Excel, PowerPoint, EPUB and more through firecrawl's anydoc (MIT), version pinned.
- `engine/install_skills.py`, run by `install.sh` and `install.ps1`: copies `skills/` into `~/.claude/skills`, never overwrites a skill the user already had or edited, and updates the ones it installed when they are untouched (hashes in `~/.brain-in-a-box/skills.json`).
- `PHILOSOPHY.md`: nine habits for getting real results from Claude Code (adapted from the Hermes Boost pack). The README gains "Get the most out of it" (the magic prompt, tips, where to start by profile) and "Updating & undo" for macOS and Windows.

### Fixed
- **New notes are indexed again on a Mac installed with a recent gbrain.** Since gbrain 0.53, a new brain's `default` source is gbrain's own folder (`~/.gbrain/content/<id>/default`), not your vault: `install.sh` imported the vault into that folder, then every nightly `gbrain sync --repo ~/Documents/Brain` failed with "not a git repository", so nothing written after the install ever reached the search. The install and the nightly now go through `engine/nightly/gbrain-sync-vault.py`, which reads `gbrain sources list` (paths compared after resolving symlinks) and picks the command:
  - **older brains**, where `default` is the vault: `sync --repo` as before, nothing else changes;
  - **new brains**: the vault is registered as its own source (`sources add vault --federated`, as `install.ps1` does), synced with `sync --source vault`, and made the default for bare commands (`sources.default`, only if you had not set one), so a bare `gbrain import` or `put` lands in the vault;
  - **installs made in between** are repaired by re-running `install.sh` (or the Mac app): the nightly already on those Macs is the old one, and only `install.sh` replaces it. Same as a new brain, then the copies of your notes left in gbrain's folder are soft-deleted so each note comes up once in a search (only pages the vault source also holds, only in gbrain's own folder; that folder is backed up first to `~/.brain-in-a-box/backups/<date>-gbrain-default/`, and gbrain keeps them restorable for 72 h). A copy changed inside gbrain since the install is removed too: its last version is in that backup.
  - `install.sh` commits the vault before indexing it (gbrain reads it through git), and a fresh install no longer warns "vault import failed" (`gbrain import` refused the skeleton's `Skills/_template.md`).
- **Windows scheduled tasks run on laptops.** Task Scheduler's defaults skipped a task when the PC was asleep at its time and never started it on battery: the nightly and the reflection now run at wake-up when missed, on battery too.
- **Re-running `install.sh` no longer loses your changes.** It used to overwrite the hooks, `gbq` and the nightly scripts with the repo's versions: on a machine where they had been adapted (another vault folder, other messages), those edits were gone for good. A file that differs is now copied to `~/.brain-in-a-box/backups/<date>/` before being replaced, and the installer says so at the end.
- **A file you changed is no longer replaced at all** (macOS). `~/.brain-in-a-box/installed.json` records the sha256 of every hook, `gbq`, nightly script and launchd plist `install.sh` writes. One changed since then is kept as is; the new version goes to `~/.brain-in-a-box/pending/` and the installer lists it at the end. A file from an install older than that record is compared with every version of it in the repo's git history: a known version is updated, any other was adapted and is kept the same way. Only without git history (a ZIP) is it backed up, then replaced. A symlink in the way is never replaced, and a new hook lands already executable.
- **`~/.claude/settings.json` is never emptied** (macOS). One that does not parse used to be replaced by our hooks alone; the install now stops before changing anything and names the file and the line (same for a `hooks` list it could not merge into). Otherwise the previous version is kept as `settings.json.bak-<date>` (mode 600) and the new one is written atomically; a symlinked file stays a symlink.
- **Install failures can be diagnosed** (macOS). The output of git clone, `bun install`/`bun link` and `gbrain init`/`import`/`embed` went to `/dev/null`; it now goes to `~/.brain-in-a-box/install.log` (mode 600, timestamped), with the Voyage key masked, and a failed step shows the last 15 lines. `bun install`, gstack's setup and brew no longer receive `VOYAGE_API_KEY`.
- **An existing vault keeps its `.gitignore`** (macOS), and a first vault commit refused (commit signing, a global pre-commit hook) is reported instead of ending the install without a word.
- **New notes get embedded again.** Since gbrain 0.60.106, `gbrain embed` asks for approval before a paid run and, without a terminal, exits 3 with nothing embedded. The install and the nightly ran it bare, so a fresh install had no semantic index until the dream cycle caught up. They now pass an explicit cap: `--max-usd 1` at install, `--max-usd 0.10` per night (a normal day of notes costs a fraction of a cent); the onboarding does the same.
- Docs no longer say a skill saved in the vault's `Skills/` folder is usable by Claude Code as is: Claude Code only loads `~/.claude/skills/<name>/SKILL.md` (README, `vault-skeleton/Skills/README.md`, `vault-skeleton/CLAUDE.md`).
- **Windows install works for people who are not developers.** Field report: a group of first-time users on Windows could not get through `install.ps1`.
  - **One line, nothing to install first**: Win+R, paste `powershell -NoExit -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/BrainInBox/brain-in-a-box/master/install.ps1 | iex"`. It fetches the sources itself (no git clone), works under the Restricted policy of a fresh PC, and stops with a readable message instead of closing the window. ISE and locked-down (Constrained Language) PowerShell are refused up front with what to do.
  - **The Microsoft Store `python` stub is no longer taken for Python.** It sits on the PATH of every fresh Windows and runs nothing (exit 9009); the installer kept it, then every step failed. Each candidate interpreter now has to run code, a Python installed by winget is found where it lands without reopening a terminal, and an accented user name (`C:\Users\Jérôme`) no longer makes a valid Python look missing.
  - **Installs what was only warned about**: Git, Claude Code (Anthropic's official installer) and Obsidian, per-user when the package allows it, so no admin prompt in most cases.
  - **Hooks are wired in exec form** (`"command": python.exe, "args": [hook]`): Claude Code spawns Python directly, so the hooks run with or without Git Bash. The shell form fell back to PowerShell without Git Bash, which cannot run `"python.exe" "hook.py"`. Re-running the installer migrates old entries and never wires a hook twice.
  - `gbq` works from Git Bash (Claude Code's Bash tool on Windows ran `~/.local/bin/gbq`, which did not exist), and `gbq sync` / `gbq embed` from the onboarding map to a reindex instead of failing. `gbq.cmd` survives an accented user name.
  - A `settings.json` that does not parse (BOM, comment) is left untouched instead of being replaced by an empty one; a symlinked one stays a symlink.
  - The nightly reindex runs with `pythonw.exe`: no black window at 04:00. Native commands no longer abort the install on a stderr warning; their exit codes are checked. An empty existing vault folder gets the skeleton.
- **CI installs GBrain on a real Windows**: the `windows` job now checks the pinned commit is the one installed, a query answered by GBrain from `gbq.cmd` and Git Bash, the onboarding's `gbq sync` / `gbq embed`, the nightly task end to end, a Voyage key refused (real API, bad key) and one accepted (local stand-in, no secret, key never in a log), and the keyword fallback when GBrain is gone or cannot be installed. `test-hooks.sh` tests `gbq.py` and the Windows nightly against a stub gbrain.
- **CI installs on a real Windows** (`windows-latest`): parse under PowerShell 5.1 and 7; a fresh install with only the Store stub, so Python really comes from winget; a hook spawned the way Claude Code does; `gbq` from Git Bash; a second run for idempotence; the one-liner under the Restricted policy.

## [0.2.0] — 2026-10-09 — Field hardening

Four months of running the box on real machines, fixed where it broke. The daily reflection is rewritten around what actually happened that day (transcripts sliced to the day, a digest of your own commits). gbrain updates no longer get stuck on an old Bun. Secrets are masked before anything is written to the vault or sent to a model. The correction detector stops firing on ordinary negations (22% → 4% of real prompts). Windows gets an experimental installer. The test suite doubles, from 25 to 50 checks, and CI now parses every script with its own shell.

### Added
- **Windows (experimental)** — `install.ps1` installs the vault skeleton, the (now cross-platform) hooks and the global `CLAUDE.md`, and registers Task Scheduler jobs (reindex 04:00, reflection 12:00/23:00). Search runs on `engine/search/brain_search.py`, a local BM25 index in pure Python, behind a `gbq.cmd` shim, because gbrain's PGLite build lacks pgvector on Windows. Still needs a real-Windows smoke test; see `docs/proposals/windows-port.md`.
- **Hardened gbrain self-update** (`engine/nightly/gbrain-selfupdate.sh`) — extracted from the nightly script so it can run from two places: the 04:00 nightly, and a new **SessionStart catch-up hook** (`gbrain-update-check.py`) that checks once per calendar day so a machine asleep at 04:00 is never more than a day behind. Every update now runs a smoke test (`gbrain doctor --fast`) after installing — a broken pull is **auto-rolled-back** to the previous commit instead of leaving a dead `gbrain`. Result is written to `~/.gbrain/last-update.json` and surfaced as a one-line block in `Profile/memory.md` (with the new version's top CHANGELOG bullets) — only when something actually changed, no daily noise.
- **Weekly lint** (`engine/nightly/gbrain-lint.sh`, launchd Monday 08:00) — verify-and-surface pass over the whole pipeline: doctor, vault lint, orphans, anomalies, back-links, stats, and a "did the nightly actually run in the last 48h" check. 🟢/🟠/🔴 verdict pinned in `Profile/memory.md` (idempotent marker block), full report in `Profile/lint.md`. Pure CLI, no LLM — a silently-failing maintenance job no longer looks healthy.
- **Link graph actually builds now** — `link_resolution.global_basename` is enabled at install (and idempotently by the nightly for existing installs). Without it, every skeleton dir (`Team/`, `Agents/`, `Decisions/`, `Skills/`, `Journal/`…) is outside gbrain's entity-dir whitelist and all wikilinks were silently dropped: empty graph, forever. Field-tested on a 562-page vault: 0 → 185 edges. Skeleton `CLAUDE.md`s document the convention: bare-basename wikilinks (`[[Page-Name]]`, no path, no `.md`) + a short `## See also` per page.

### Fixed
- **`gbq` reports a read that fails on its own.** Reads (`query`, `search`, `ask`, `graph-query`) always returned 0 because the force-kill of a hung read is intentional. A gbrain that refuses to start (wrong Bun, broken install) looked like a successful empty read. The exit code is now 0 only when `gbq` cut the read itself. First tests for `gbq` and `brain_search` in `test-hooks.sh`.
- `setup-company.sh` and `try-it-locally.sh` printed their final banner with literal `\033[1;32m` codes (escapes are not interpreted inside a heredoc); it is now printed with `printf`.
- **CI** parses each script with the shell its shebang names (the zsh scripts were checked with bash, and `gbrain-lint.sh` not at all), compiles the Python hooks, and lints the launchd templates.
- **Windows**:
  - The reflection exchanges UTF-8 with `claude` and `git` explicitly. Text mode used the locale's codepage (cp1252) and died on the first `→` or accented letter of a prompt.
  - The session recap's error log no longer uses `%F`/`%T`, which Windows' `strftime` does not support.
  - `install.ps1` falls back to the default logon type when S4U is refused (reported on Windows Home in #6), instead of installing no scheduled task.
  - UTF-8 file I/O in the session hooks and on `brain_search`'s output, and S4U scheduled tasks so they run in a locked session (#8, thanks @ismabillion-ship-it).
- **Nightly sweeps an orphan `.git/index.lock`** (only when no git runs in the repo and the lock is >5 min old). A crashed git left it behind, the vault stopped committing, and `gbrain sync` froze the index without any error.
- **`gbq` returns gbrain's real exit code.** It used to end on the stale-lock test and exit 1 after a successful command, which broke `gbq sync && …`.
- **Correction reminder states where to append** in `lessons.md` (chronological, newest at the bottom). Agents writing at the top split the file in two.
- **Docs**: removed references to a `file-protection` hook that never shipped; Windows is no longer described as unsupported; ONBOARDING re-indexes through `gbq` with sync and embed split, like the nightly; dropped a `{{LANGUAGE}}` placeholder the `CLAUDE.md` template does not have.
- **Self-update upgrades Bun when gbrain needs a newer one.** gbrain now declares `engines.bun >= 1.4.0`; on an older Bun it refuses to start, so the smoke test failed and the update was rolled back every night, forever. The self-update reads `engines.bun` after the pull, runs `bun upgrade` when the installed Bun is too old, and restores the previous Bun binary if the update is rolled back. When Bun cannot be upgraded, `memory.md` now says so instead of blaming `gbrain doctor`.

### Changed
- **Correction detector: far fewer false alarms, English + French.** A bare negation is no longer a correction: the old pattern (`no|not|don't|stop`…) fired on ordinary prompts ("it doesn't build", "I don't know which lib"). It now looks for signals aimed at the assistant's work (redirection, reproach, a lasting rule, a prompt opening on "no", a verdict on its output), in English and French.
  - Relayed content (agent output, task notifications, pasted logs) is skipped by a new `_paste_guard.py`, which fails open: if it breaks, corrections still fire.
  - A long prompt is a spec or a paste, so only its opening is read.
  - "I don't follow" / "j'ai pas compris" now gets its own reminder: the lesson is about how the assistant explains.
  - On ~1,500 real prompts, the hook fired on 22.3% before and 4.1% after. `test-hooks.sh` gains 7 checks (32 prompts).
- **Daily reflection rewritten** (`daily-reflection.py`, supersedes #4):
  - Finds the day's sessions by scanning `~/.claude/projects` and slicing each transcript to that day, instead of the Stop-hook index: a long session whose Stop fired another day was missing, and only the first 50k chars of a transcript were read.
  - Sends readable turns (user / assistant text, one line per tool call) instead of raw JSONL; tool output, which is most of the volume, is dropped. The budget is split fairly between sessions, keeping each one's start and end.
  - Adds a **git digest of your own commits** that day, the authoritative record of what was produced. Repos come from the sessions' folders (including repos nested under them), the vault, and `BRAIN_GIT_ROOTS`.
  - **Masks secrets before anything leaves the machine** (patterns now shared with the session recap in `_redact.py`; without that module the run is skipped, never sent unmasked).
  - Merges into existing `Journal/` and `memory.md` content instead of overwriting it, writes in the vault's language, and leaves its own past runs out of the next one.
  - Reads Hermes sessions when Hermes is installed.
  - On failure: a warning block in `memory.md` (removed by the next good run), a state file and a macOS notification; a usage-limit error stops the retries at once.
  - `--day YYYY-MM-DD` backfills a missed day, `--dry-run` shows the sources without calling Claude; one run at a time. The prompt goes through stdin, which also lifts the command-line length limit that broke long days on Windows.
- **Nightly waits for the network and retries the vault push.** At 04:00 a laptop is often still reconnecting: every remote step died on "Could not resolve host", and the best-effort push was skipped with a log line blaming credentials — the vault's origin could fall days behind unnoticed. The nightly now waits up to 5 min for DNS, retries the push 3 times, and logs the real cause (no network vs. push error). First tests for the nightly in `test-hooks.sh` (network up / down / push failing).
- **Nightly**: vault is pushed to its git remote after the nightly commit (best-effort, never blocks — local commits are worth little if the disk dies). Sync and embed are now split (`sync --no-embed` + `embed --stale`): sync's inline embed path fails against `zembed-1` with a misleading parse error and silently stops ingesting the vault.
- **Reflection**: the headless `claude -p` run is retried (3 attempts, 60s apart) — transient API failures ("Connection closed mid-response") were silently losing whole days of journal. Failures are logged to `daily-reflection-errors.log`.

### Security
- **Session recap masks secrets** before writing the session's first prompt to the journal. The journal is committed and pushed every night, so a key pasted into a prompt ended up in the vault's git history, and was re-copied at every Stop. Common token shapes (private keys, cloud/API keys, JWTs, bearer tokens, `password=`-style values, credentials in URLs, raw 32-byte hex keys) are replaced by `[REDACTED]`, before truncation so a half-cut key cannot slip through. Covered by two new checks in `test-hooks.sh`.

## [0.1.0] — 2026-05-26 — Initial public release

The first public-OSS day. The product was built and dogfooded privately the day before; today it went public after a multi-layer scan (file content + full git history + tier names like personal contacts) confirmed zero personal info leaks.

### Added
- **Vault skeleton with team-first folders**: `Profile/`, `Team/` (humans), `Agents/` (AI agents as first-class teammates), `Decisions/` (one file per locked-in choice), `Skills/` (gstack SKILL.md format), plus existing `Journal/`, `Projects/`, `Clients/`, `Resources/`.
- **`./install.sh`** one-command install — vault skeleton + 5 hooks + GBrain (clone + ZE-embedded init) + gbq wrapper + launchd jobs (nightly 04:00 + reflection 12:00/23:00) + Obsidian install + global `CLAUDE.md` merge. Non-destructive: never overwrites an existing vault or settings.
- **`./install.sh --with-gstack`** option — clones and sets up [garrytan/gstack](https://github.com/garrytan/gstack) alongside (23 AI specialists for Claude Code).
- **`./install.sh --company <git-url>`** — team mode, joins a shared company brain as a 2nd federated GBrain source.
- **`./setup-company.sh`** — admin scaffolds the company brain (`~/Documents/BrainCo/`), git inits, prints push + member-join instructions.
- **5 hooks** in `engine/hooks/`:
  - `correction-detector.py` (UserPromptSubmit) — captures corrections to `lessons.md`
  - `session-logger.py` + `session-indexer.py` (Stop) — per-session logs
  - `session-recap.py` (Stop) — structured journal entry per session (no LLM)
  - `daily-reflection.py` (cron, called by launchd 12:00 + 23:00) — LLM summary of the day
- **`gbq`** universal safe wrapper around `gbrain` — force-kill workaround on PGLite reads (`query/search/ask/graph-query`), clean `wait` on writes (`sync/embed/dream/skillify/brainstorm/code-def/code-refs/sources/...`).
- **Nightly maintenance** (`engine/nightly/gbrain-nightly.sh`) — stale-lock recovery → self-update gbrain → self-update gstack (if installed) → commit vault → sync personal + company → dream cycle.
- **`test-hooks.sh`** — verifies all 5 hooks (11 checks) + validates YAML frontmatters in `vault-skeleton/*/_template.md` (4 checks), in an isolated temp HOME. 15/15 expected.
- **CI** (`.github/workflows/test.yml`) — runs `test-hooks.sh` + shell syntax lint on every PR + push to master.
- **OSS hygiene** — MIT LICENSE, CONTRIBUTING.md, SECURITY.md, issue templates.

### Security
- Repository history scrubbed of all personal/business identifiers before going public (multi-layer scan: file content + full git log + tier-1 contact names).
- Branch protection on `master` (1 PR review required, no force-push, no deletion).
- `~/.gbrain/config.json` written with mode 0600 (the ZeroEntropy API key never leaves the machine).
- Hooks resolve absolute paths at install time (sed `__HOME__` → `$HOME`) — no runtime path injection surface.

### Known limitations
- macOS only (launchd for cron). Linux port welcome (issue first).
- Embeddings via ZeroEntropy (free tier). Ollama-local variant planned, not yet shipped.
- Auto-skill suggestion from session patterns is intentionally NOT shipped — speculative without real usage data. Will revisit after 2+ weeks of community usage.
