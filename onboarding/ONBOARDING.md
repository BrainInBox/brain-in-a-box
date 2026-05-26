# ONBOARDING — personalize this brain

> **For the agent (Claude Code)**: this file is an interview script. Run it to turn the skeleton into the user's personal brain. Ask questions in small groups, wait for answers, then write the files. Use the user's language.

## Goal
Replace every `{{PLACEHOLDER}}` in:
- `~/Documents/Brain/Profile/about.md` (identity)
- `~/Documents/Brain/Profile/business.md` (activity/projects)
- `~/Documents/Brain/Profile/soul.md` (language, tone)
- `~/Documents/Brain/Profile/stack.md` (personal tools)
- `~/.claude/CLAUDE.md` (the brain-in-a-box block: {{NAME}}, {{LANGUAGE}})

Then **seed** the team-first folders with the user's real entities (no need to fill all at once — start with what's immediately relevant):
- `~/Documents/Brain/Team/<firstname>.md` (themselves, and any humans they collaborate with regularly)
- `~/Documents/Brain/Agents/<name>.md` (any AI agent in their orbit — Hermes? gstack specialists? custom hook? — even if just stubs, declare them)
- `~/Documents/Brain/Decisions/YYYY-MM-DD-<slug>.md` (1-2 recent meaningful decisions, to amorce le pattern)
- `~/Documents/Brain/Skills/<slug>.md` (any pattern they already repeat 3+ times — easy candidates to capture)

Each of these folders ships a `README.md` (purpose) + `_template.md` (schema). Copy `_template.md` to a real filename and fill in.

## Flow (interview)

**Block 1 — Identity**: name, city/country, education/background, GitHub, emails (work/personal), machine.
**Block 2 — Work**: main role, stack/skills, domains. Company or activity (name, description, sector, stage idea/MVP/launched/scaling).
**Block 3 — Projects**: main project (1 line) + other active projects.
**Block 4 — Team & agents**: humans they collaborate with regularly (1 file per person in `Team/`), AI agents they use (Hermes, gstack specialists if installed, custom hooks — 1 file per agent in `Agents/`). Same schema for both.
**Block 5 — Decisions to seed** (1-3 of them): meaningful decisions they've already made and would want anyone joining to find. Capture in `Decisions/YYYY-MM-DD-<slug>.md` with context, options, trade-offs.
**Block 6 — Patterns to skillify** (optional): any procedure they repeat that could be captured as a `Skills/<slug>.md` (gstack-compatible format).
**Block 7 — Style**: default language, desired tone (direct/detailed), communication preferences, work preferences (plans before action? max autonomy? visible logs?).
**Block 8 — Tools/access** (no secrets): installed CLIs, cloud, DB, where secrets live (Keychain, .env) — locations, never values.
**Block 9 — Optional: gstack**: if the user wants 23 AI specialists (CEO/eng/QA/CSO/reviewer/release-engineer/…) alongside, suggest `./install.sh --with-gstack` and add an `Agents/<name>.md` for each gstack skill they'll actually use.

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
