# Changelog

All notable changes to brain-in-a-box.

Format inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
