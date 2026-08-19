# Stack — tools, memory, env

> Capability brief. Everything below is AVAILABLE. Partly filled in by the onboarding.

## Machine
- {{MACHINE}} · user `{{USER}}` · home `~`

## Memory — GBrain (installed by brain-in-a-box)
- Local index: `~/.gbrain/brain.pglite` (ZeroEntropy embeddings)
- Tool: `~/DEV/gbrain` (clone of garrytan/gbrain)
- **Search**: `~/.local/bin/gbq query "<question>"` (semantic) · `gbq search "<kw>"` (keyword)
  - ⚠️ Always `gbq`, never `gbrain query` directly (hangs on the single-connection PGLite lock)
- **Nightly** (launchd `com.{{USER}}.gbrain-nightly`, 04:00): commit vault → sync → dream cycle (dedup, facts, consolidation) → self-update GBrain (smoke-tested, auto-rollback if broken)
- **Self-update catch-up** (SessionStart hook, once/day): if the machine missed the 04:00 run, the first Claude Code session of the day triggers it in the background — see `Profile/memory.md` for the last result
- ZeroEntropy key: in `~/.gbrain/config.json` (field `zeroentropy_api_key`)

## Brain hooks (global, `~/.claude/hooks/brain/`)
- `correction-detector.py` (UserPromptSubmit) — Rule #1
- `session-logger.py` + `session-indexer.py` (Stop) — logs in `~/.claude/logs/`
- `session-recap.py` (Stop) — `Journal/YYYY-MM-DD.md`
- `daily-reflection.py` (cron) — semantic summary in `memory.md`
- `gbrain-update-check.py` (SessionStart) — daily gbrain self-update catch-up

## My tools / access
{{MY_TOOLS}}

## Secrets rules
- NEVER commit: `.env*`, `*.pem`, `*.key`, `credentials.json`, tokens
- Reference where things live, not the values
