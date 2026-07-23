# Changelog

All notable changes to brain-in-a-box.

Format inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **Weekly lint** (`engine/nightly/gbrain-lint.sh`, launchd Monday 08:00) — verify-and-surface pass over the whole pipeline: doctor, vault lint, orphans, anomalies, back-links, stats, and a "did the nightly actually run in the last 48h" check. 🟢/🟠/🔴 verdict pinned in `Profile/memory.md` (idempotent marker block), full report in `Profile/lint.md`. Pure CLI, no LLM — a silently-failing maintenance job no longer looks healthy.
- **Link graph actually builds now** — `link_resolution.global_basename` is enabled at install (and idempotently by the nightly for existing installs). Without it, every skeleton dir (`Team/`, `Agents/`, `Decisions/`, `Skills/`, `Journal/`…) is outside gbrain's entity-dir whitelist and all wikilinks were silently dropped: empty graph, forever. Field-tested on a 562-page vault: 0 → 185 edges. Skeleton `CLAUDE.md`s document the convention: bare-basename wikilinks (`[[Page-Name]]`, no path, no `.md`) + a short `## See also` per page.

### Changed
- **Nightly**: vault is pushed to its git remote after the nightly commit (best-effort, never blocks — local commits are worth little if the disk dies). Sync and embed are now split (`sync --no-embed` + `embed --stale`): sync's inline embed path fails against `zembed-1` with a misleading parse error and silently stops ingesting the vault.
- **Reflection**: the headless `claude -p` run is retried (3 attempts, 60s apart) — transient API failures ("Connection closed mid-response") were silently losing whole days of journal. Failures are logged to `daily-reflection-errors.log`.

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
