# ONBOARDING — personalize this brain

> **For the agent (Claude Code)**: this file is an interview script. Run it to turn the skeleton into the user's personal brain. Ask questions in small groups, wait for answers, then write the files. Use the user's language.

## Goal
Replace every `{{PLACEHOLDER}}` in:
- `~/Documents/Brain/Profile/about.md` (identity)
- `~/Documents/Brain/Profile/business.md` (activity/projects)
- `~/Documents/Brain/Profile/soul.md` (language, tone)
- `~/Documents/Brain/Profile/stack.md` (personal tools)
- `~/.claude/CLAUDE.md` (the brain-in-a-box block: {{NAME}}, {{LANGUAGE}})

## Flow (interview)

**Block 1 — Identity**: name, city/country, education/background, GitHub, emails (work/personal), machine.
**Block 2 — Work**: main role, stack/skills, domains. Company or activity (name, description, sector, stage idea/MVP/launched/scaling).
**Block 3 — Projects**: main project (1 line) + other active projects.
**Block 4 — Team** (if relevant): who does what, who decides what (who to contact for X).
**Block 5 — Style**: default language, desired tone (direct/detailed), communication preferences, work preferences (plans before action? max autonomy? visible logs?).
**Block 6 — Tools/access** (no secrets): installed CLIs, cloud, DB, where secrets live (Keychain, .env) — locations, never values.

## After the interview

1. **Edit** each Profile file, replacing the `{{...}}` with real answers. Remove irrelevant empty sections.
2. **Verify**: `grep -rn "{{" ~/Documents/Brain/Profile ~/.claude/CLAUDE.md` → must be empty (no placeholders left).
3. **Commit + re-index**:
   ```bash
   cd ~/Documents/Brain && git add -A && git commit -q -m "onboarding: profile filled"
   ~/.local/bin/gbq query "who am I"   # force a refresh, check recall
   gbrain sync --repo ~/Documents/Brain --no-pull   # re-embed the profile
   ```
4. **Confirm** to the user: profile filled, memory active, next step = just live (the hooks capture corrections + sessions, the nightly consolidates at 4am).

## Rules
- NEVER write a secret in the vault (API key, password, token). Reference where it lives.
- Human tone, no corporate fluff. It's THEIR memory, not a form.
- If the user doesn't know an answer, leave it cleanly empty (no leftover placeholder).
