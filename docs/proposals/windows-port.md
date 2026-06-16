# Proposal — Windows port (solo first)

Status: **proposal** (per CONTRIBUTING, approach before code). Scope: make
brain-in-a-box installable and runnable for a **solo user on Windows**. Team mode
(`setup-company.sh`) is out of scope for v1.

> **Update 2026-06 — Phase 1 done.** The engine hooks are now cross-platform
> (pure Python): `/tmp` → `tempfile.gettempdir()`, `claude` resolved via
> `shutil.which()`, home resolved at runtime with `Path.home()` (no `__HOME__`
> bake-in), and `.gitattributes` enforces LF so CRLF can't break the hooks on
> Windows. This benefits macOS too and is **not gated by gbrain**. Remaining:
> `install.ps1`, the scheduler step, and the search path (Phases 2–6 below).
> **Field note:** a downstream deployment ran fully native on Windows by putting
> **semantic search server-side** (a small search API the client hits over HTTP)
> instead of embedded gbrain — which sidesteps the #1549 PGLite/pgvector gating
> entirely. Worth considering as the Windows search path here.

## Why it doesn't run on Windows today

Everything macOS-specific, by layer:

| Layer | macOS today | Windows needs |
|---|---|---|
| Installer | `install.sh` (bash); `uname = Darwin \|\| die`; `brew` | `install.ps1` (PowerShell); OS detect; `winget` |
| Scheduler | `launchd` plists (nightly 04:00 + reflection 12:00/23:00) | **Task Scheduler** (`Register-ScheduledTask`) |
| Safe wrapper | `gbq` (zsh) | cross-platform wrapper (Node or Python) |
| Nightly | `gbrain-nightly.sh` (bash) | PowerShell, or rewrite logic in Python |
| Hooks (Python) | hardcoded `/tmp/...` locks, `__HOME__/.local/bin/claude` | `tempfile.gettempdir()`, resolve `claude` on PATH |
| Obsidian | `brew install --cask obsidian` | `winget install Obsidian.Obsidian` |

## The gating dependency — gbrain on Windows (researched 2026-06)

brain-in-a-box sits on **gbrain** (separate project, bun-based). The *foundation*
is Windows-ready: **bun's Windows support went stable in bun 1.2** (Jan 2026; ARM64
in 1.3.10), and **PGLite is WASM** so it runs wherever bun/node runs. So the
building blocks are fine.

**gbrain itself is the blocker.** It is not CI-tested on Windows (CI = macOS +
Ubuntu only), and its issue tracker has open/known Windows bugs — crucially **on
the solo/PGLite path we'd target**:

- **#1549 — PGLite on Windows 10: the `pgvector` extension is missing from the WASM
  binary** → `search`/`think` don't work. This is the dealbreaker: no semantic
  search = no brain.
- **#1605 — Supabase-pooler migration `getaddrinfo ENOTFOUND`** on Windows (team
  path, less relevant to solo).
- **#1554 — cross-platform node shim** (PR): the POSIX-shell postinstall didn't run
  on Windows.
- **#1665 — "critical fix wave"** merged Windows migration-spawn fixes.

So fixes are actively landing upstream, but as of this research **a solo Windows
user cannot get working semantic search** until #1549 (PGLite pgvector on Windows)
is resolved. **Our port is gated on that.** Building `install.ps1` before gbrain's
PGLite works on Windows would ship a broken brain.

## Plan

**Phase 0 — Spike (gating).** On a real Windows box: install bun (≥1.3.10),
`gbrain init --pglite`, embed, `gbrain sync`, and crucially **`gbrain query`** —
this is the check for issue #1549 (PGLite pgvector on Windows). If `query` returns
ranked results → green, proceed. If pgvector is still missing → **stop**; the port
is blocked upstream. Track gbrain #1549 / #1554 / #1665. *Decision point.*

**Phase 1 — Cross-platform hooks (also benefits macOS).** Replace `/tmp` with
`tempfile.gettempdir()`; resolve the Claude binary via `shutil.which("claude")`
with the `__HOME__` path as fallback; audit for any other POSIX assumptions. Pure
Python, low risk. Keep `test-hooks.sh` green; add a PowerShell sibling.

**Phase 2 — Scheduler abstraction.** A thin installer step that registers the two
jobs with the OS scheduler: launchd on darwin (today), **Task Scheduler** on
Windows (nightly 04:00 + reflection 12:00/23:00, running `python daily-reflection.py`
and the nightly).

**Phase 3 — `gbq` cross-platform.** Port the zsh wrapper (force-kill on PGLite
read hangs, clean wait on writes, stale-lock sweep) to a small **Node or Python**
script that runs everywhere. Single source of truth, drop the zsh version or keep
it as a thin shim.

**Phase 4 — `install.ps1`.** PowerShell mirror of `install.sh`: copy vault
skeleton, install hooks (`__HOME__` → `$HOME` replace), install/clone gbrain,
Obsidian via `winget`, register scheduled tasks, merge global `CLAUDE.md`.
Non-destructive, same as the bash installer. Replace the `uname` guard with OS
detection that routes to the right scheduler.

**Phase 5 — Tests + CI.** A cross-platform `test-hooks` (PowerShell or a Python
runner) and a `windows-latest` entry in the CI matrix so it doesn't regress.

**Phase 6 — Docs.** README + CONTRIBUTING: drop "macOS only", add Windows setup.

## Scope decisions

- **Solo + PGLite only** for v1. Defer team mode and the Supabase engine (that's
  where the known Windows bug lives).
- **WSL is not the target** — native PowerShell, so a non-technical Windows user
  isn't asked to install a Linux subsystem. (WSL would "work" trivially but isn't
  a real Windows port.)

## Risks

- **gbrain-on-Windows** (gating, Phase 0).
- **CRLF line endings** breaking the Python/JSON hooks — enforce LF via
  `.gitattributes`.
- **Task Scheduler** quirks (working dir, env, user session) vs launchd's model.
- Path separators / `%USERPROFILE%` vs `~` — handled by `pathlib`/`os.path` if we
  remove the remaining hardcoded POSIX paths (Phase 1).

## Recommendation

**Don't build the Windows port yet** — it's gated on gbrain fixing PGLite/pgvector
on Windows (#1549). The right move now:

1. **Phase 1 (cross-platform hooks) regardless** — pure Python cleanup, makes the
   hooks better on macOS too, and is the only part not blocked by gbrain.
2. **Watch gbrain #1549** (semantic search on Windows PGLite). The instant it's
   fixed, run the Phase 0 spike to confirm, then do Phases 2–6.
3. Until then, point Windows users at the standalone **git-only journal** tool
   (`devjournal`) for the part that doesn't need gbrain — they get the journal,
   just not the searchable brain.
