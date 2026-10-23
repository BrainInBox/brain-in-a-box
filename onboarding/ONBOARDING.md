# ONBOARDING — personalize this brain

> **For the agent (Claude Code or Codex)**: this file is an interview script. Run it to turn the skeleton into the person's own brain: ask a few questions at a time, wait for the answers, then write the files yourself. The person never types a command and never opens a file: you do it.

## Where the brain is
The brain is the **current folder** (it holds `CLAUDE.md` and `Profile/`): Bibo and the installer start you there. If it is not, use the folder your session start names ("Brain: this person's notes are in …") or the one the person's message names, else `$BRAIN_DIR`. Every path below is relative to the brain. Never create a second brain somewhere else.

## How to talk
Speak the person's language: the one your session start names, else French. The message that started you may be Bibo's (the app), always in English: it says nothing of their language. Switch only when they themselves write in another one. In French: « tu », short sentences, everyday words, French typography. Before each step, say in one sentence what you are doing. Any question can be skipped: « je ne sais pas » or « passe » is a fine answer.

## Before your first write
Claude Code asks before each write (Codex does not). Before your first one, tell them, in their language: « Claude Code va me demander, en anglais, la permission d'écrire dans tes notes. Choisis la réponse qui finit par « for this session » (pour cette session) avec les flèches du clavier, puis appuie sur Entrée. » Never quote the whole answer: its words change with Claude Code's versions. Then nothing more is asked in the brain folder. Write nowhere else on Path A.

## Files to fill
Replace every `{{PLACEHOLDER}}` in:
- `Profile/about.md` (who they are)
- `Profile/business.md` (their work)
- `Profile/soul.md` (language, tone)
- `Profile/stack.md` (their tools)
- Path B only: your global instructions, in the brain-in-a-box block (after `<!-- brain-in-a-box -->`): `~/.claude/CLAUDE.md` for Claude Code, `~/.codex/AGENTS.md` for Codex (`AGENTS.override.md` there when it has text; `$CODEX_HOME` instead of `~/.codex` when set). It holds {{NAME}}; the language goes in `soul.md`. On Path A, leave it: it is outside the brain, and Claude Code would ask again.

Write these notes in the person's language, headings and explanations included. Keep the file and folder names as they are: programs look for them. In `soul.md`, the language stays under its own heading, the word for "language" in theirs (« ## Langue »), and the line below names it (« Français »): the journal and every session take it from there. Remove the sections that do not apply and the lines left empty. If they do not read French, rewrite `Commence ici.md` (their map of the brain) in their language too, under the same name.

## First question
Greet them in two sentences: about fifteen minutes of simple questions, so that you know them from now on; any question can be skipped. Then ask:

**« Est-ce que tu écris du code ? Je veux dire : tu es développeur ou développeuse ? »**
(In English: "Do you write code? I mean: are you a developer?")

- No, or not sure → **Path A** (most people).
- Yes → **Path B**.

## Path A — not a developer
Four short blocks, one at a time. Give examples when it helps (« par exemple : Sage, Excel, Gmail »).

**Bloc 1 — Qui tu es** : ton prénom (et ton nom si tu veux), ta ville et ton pays, comment tu veux que je t'appelle.
**Bloc 2 — Ton travail et tes logiciels** : ton métier ; ton entreprise ou ton activité (son nom, ce qu'elle fait) ; les logiciels que tu utilises au travail (Sage, Odoo, Excel, Word, Gmail, WhatsApp…) ; les personnes avec qui tu travailles le plus (collègues, clients, fournisseurs).
**Bloc 3 — Ce qui te prend du temps** : les tâches que tu refais souvent, ce que tu cherches sans le trouver, ce que tu aimerais que je fasse pour toi.
**Bloc 4 — Comment tu veux que je te parle** : la langue ; des réponses courtes ou détaillées ; tout expliquer simplement ou aller droit au but ; des emojis ou pas.

Ask about nothing else. If they bring up something technical themselves, follow them.

Then write:
- `Profile/about.md`: identity, work, software, what takes them time, style. Remove the developer section.
- `Profile/business.md`: the activity, its sector, the people they work with. Remove the technical sections (architecture, access, infra).
- `Profile/soul.md`: the language and the tone they chose. Rewrite "Style" for them: plain words, no jargon nor acronym, one step at a time, say exactly what to click in the software they named. Drop what is about code.
- `Profile/behavior.md`: drop what is about code (tests, type-checker, linter, Telegram).
- `Profile/stack.md`: the software they named, under "My tools / access"; `{{MACHINE}}` and `{{USER}}` from what you already know (the system, the brain's path), without asking. Leave the rest.
- `Team/<prénom>.md` for each person they named, from `Team/_template.md`: only the fields they gave.

## Path B — developer
Ask in groups, wait for answers, then write the files.

**Block 1 — Identity**: name, city/country, education/background, GitHub, emails (work/personal), machine.
**Block 2 — Work**: main role, stack/skills, domains. Company or activity (name, description, sector, stage idea/MVP/launched/scaling).
**Block 3 — Projects**: main project (1 line) + other active projects.
**Block 4 — Team & agents**: humans they collaborate with regularly (1 file per person in `Team/`), AI agents they use (Hermes, gstack specialists if installed, custom hooks — 1 file per agent in `Agents/`). Same schema for both.
**Block 5 — Decisions to seed** (1-3 of them): meaningful decisions they've already made and would want anyone joining to find. Capture in `Decisions/YYYY-MM-DD-<slug>.md` with context, options, trade-offs.
**Block 6 — Patterns to skillify** (optional): any procedure they repeat that could be captured as a `Skills/<slug>.md` (gstack-compatible format).
**Block 7 — Style**: default language, desired tone (direct/detailed), communication preferences, work preferences (plans before action? max autonomy? visible logs?).
**Block 8 — Tools/access** (no secrets): installed CLIs, cloud, DB, where secrets live (Keychain, .env) — locations, never values.
**Block 9 — Optional: gstack**: if the user wants 23 AI specialists (CEO/eng/QA/CSO/reviewer/release-engineer/…) alongside, suggest `./install.sh --with-gstack` and add an `Agents/<name>.md` for each gstack skill they'll actually use.

Then **seed** the team-first folders with the user's real entities (no need to fill all at once — start with what's immediately relevant):
- `Team/<firstname>.md` (themselves, and any humans they collaborate with regularly)
- `Agents/<name>.md` (any AI agent in their orbit — Hermes? gstack specialists? custom hook? — even if just stubs, declare them)
- `Decisions/YYYY-MM-DD-<slug>.md` (1-2 recent meaningful decisions, to start the pattern)
- `Skills/<slug>.md` (any pattern they already repeat 3+ times — easy candidates to capture)

Each of these folders ships a `README.md` (purpose) + `_template.md` (schema). Copy `_template.md` to a real filename and fill in.

## After the interview (both paths)
1. **Edit** each Profile file, replacing the `{{...}}` with real answers. Remove irrelevant empty sections.
2. **Verify**: no `{{` left in `Profile/` (Path B: nor in your global instructions, `~/.claude/CLAUDE.md`; Codex: the `AGENTS.md` above). Read or search the files with your own tools, not a shell command: reading asks nothing.
3. **Save + re-index** — Path A: skip this step and show no command; the night saves and indexes the notes. Path B, with the brain's full path for `<brain>`:
   ```bash
   git -C "<brain>" add -A && git -C "<brain>" commit -q -m "onboarding: profile filled"
   ~/.local/bin/gbq sync --repo "<brain>" --no-pull --no-embed   # import the profile
   ~/.local/bin/gbq embed --stale --max-usd 0.10         # embed it (kept separate, like the nightly; paid runs need a cap)
   ~/.local/bin/gbq query "who am I"                     # check recall
   ```
   One that fails is said in one sentence, then you go on: the notes are written, and the night indexes them.
4. **Confirm**, in their language:
   - Path A, in a few plain sentences, for example: « C'est fait : je te connais maintenant. Ce que je sais de toi est dans tes notes, dossier Profile : Bibo les ouvre dans Obsidian. Commence par la note « Commence ici ». Quand je me trompe, corrige-moi : je note la leçon et j'évite de refaire l'erreur. Chaque jour où on se parle, je résume nos conversations dans ton journal (à midi et le soir, si ton ordinateur est allumé). Et Bibo peut te guider pas à pas dans un logiciel. »
   - Path B: profile filled, memory active, next step = just live (the hooks capture corrections + sessions, the nightly consolidates at 4am).

## Rules
- NEVER write a secret in the vault (API key, password, token). Reference where it lives.
- Human tone, no corporate fluff. It's THEIR memory, not a form.
- If the user doesn't know an answer, leave it cleanly empty (no leftover placeholder).
- Path A: never ask them to type a command, open a file or install anything.
