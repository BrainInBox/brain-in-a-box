# Brain — {{NAME}}

Personal second brain. Open it by running `claude` from `~/Documents/Brain/`.
The global `~/.claude/CLAUDE.md` also applies. This file adds Brain-specific context.

Language: {{LANGUAGE}}.

---

## SessionStart — REQUIRED

On open, read in this order BEFORE responding to anything:
1. `Profile/soul.md` — personality, values, style
2. `Profile/memory.md` — key decisions + recent context
3. `Profile/lessons.md` — past corrections to never repeat

---

## Rule #1 ABSOLUTE — Capture corrections (blocking)

The `correction-detector.py` hook (UserPromptSubmit) detects corrections. When it injects `⚠️ CORRECTION DETECTED`, BEFORE anything else:
1. Apply the correction in the current response
2. Append a line to `Profile/lessons.md` under today's header (`## YYYY-MM-DD`)
3. Confirm visibly: `✓ noted in lessons.md`
4. Only then continue

Format: `- **[short context]** → rule: [what to do] (when: [trigger])`

---

## Memory recall — GBrain (`gbq`)

Before answering about a past decision/context/person, **query instead of guessing**:
```
~/.local/bin/gbq query "<natural-language question>"   # semantic search over the vault
~/.local/bin/gbq search "<keyword>"                     # fast keyword search
~/.local/bin/gbq skillify <url-or-file>                 # draft a SKILL.md from content
~/.local/bin/gbq brainstorm "<question>"                # bisociation idea generator
```
Always use `gbq` — it's the safe universal wrapper for every gbrain command (reads avoid the PGLite lock hang, writes wait cleanly). Cite the returned slug. The index refreshes every night (sync + dream cycle). See `Profile/stack.md`.

---

## Vault structure

| Folder | Content |
|---|---|
| `Profile/` | Identity, rules, business, stack (permanent) |
| `Team/` | Humans on the team — one file per person (gstack-style entity) |
| `Agents/` | AI agents on the team — same schema as `Team/`, both first-class |
| `Decisions/` | One file per meaningful decision (`YYYY-MM-DD-<slug>.md`) |
| `Skills/` | Procedures in [gstack SKILL.md format](https://github.com/garrytan/gstack) (YAML triggers + workflow) |
| `Journal/` | Daily notes YYYY-MM-DD.md (auto) |
| `Projects/` | Active projects (roadmap, status) |
| `Clients/` | Actifs/, Prospects/ |
| `Resources/` | Templates/, reusable |

## Wikilinks — how the graph gets built

Link pages with **bare-basename wikilinks**: `[[Page-Name]]` — no folder path, no
`.md`, case-insensitive. The nightly extract turns them into graph edges
(`gbq backlinks <slug>` to read them back). Path-qualified links
(`[[Projects/Page-Name]]`) break resolution on some write paths — avoid them.

When you write or update a page, end it with a short `## See also` section
(2-5 wikilinks to genuinely related pages, one short reason each). A page
nobody links to is invisible to graph traversal. Few good links > many weak.

---

## Routing — where to write
- Correction → `Profile/lessons.md` (Rule #1)
- Meaningful decision worth answering "why X?" in 6 months → `Decisions/YYYY-MM-DD-<slug>.md`
- New teammate (human) → `Team/<name>.md` · new agent → `Agents/<name>.md`
- New procedure / skill → `Skills/<slug>.md` (gstack-compatible SKILL.md format)
- Business/project info → `Profile/business.md` or `Projects/<name>.md`
- Tools/env → `Profile/stack.md`

---

## Forced Verification (Brain-adapted)
Before saying "done" on a Brain edit:
- [ ] Valid markdown
- [ ] No committed secret (.env, key, token)
- [ ] If editing lessons.md/memory.md → grep that the line is actually there

## Where depth lives
Don't summarize here. Point to the file/folder/command where the depth actually lives (a path, a `gbq` search, a file pattern) — never a conclusion that ages.
