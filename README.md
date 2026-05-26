# brain-in-a-box

A **personal second brain** for Claude Code — and a **shared memory layer for mixed AI + human teams**. One command to install. Markdown vault (browsable in Obsidian) + auto-capture hooks + semantic memory (GBrain). Composes with any agent stack: [gstack](https://github.com/garrytan/gstack), [openclaw](https://github.com/openclaw/openclaw), [Hermes](https://github.com/NousResearch/HermesAgent), or plain Claude Code — they all read and write the same vault.

Everyone gets the **same engine**, with **their own data** — everything stays **local on their machine**.

## What's inside

| Layer | What |
|---|---|
| **Vault** (`~/Documents/Brain`) | Your markdown notes: Profile · **Team · Agents · Decisions · Skills** · Journal · Projects · Clients · Resources |
| **Hooks** (`~/.claude/hooks/brain`) | Auto-capture: corrections → `lessons.md`, sessions → `Journal/`, summaries → `memory.md` |
| **GBrain** (`~/.gbrain`) | Semantic search over the vault (`gbq query "..."`) — ZeroEntropy embeddings |
| **Nightly** (launchd 04:00) | commit vault → sync → dream cycle (dedup, facts, consolidation) → self-update |
| **Reflection** (launchd 12:00 + 23:00) | LLM summary of the day's sessions → `Journal/` + rolling 15-day `memory.md` |

It all runs inside Claude Code (terminal/IDE). No bot, no server, no shared cloud.

## Install (per person)

Requirements: macOS, [Claude Code](https://claude.ai/code), a **free** [ZeroEntropy](https://dashboard.zeroentropy.dev) key (embeddings — free account, no card).

```bash
git clone <URL_OF_THIS_REPO> brain-in-a-box
cd brain-in-a-box
./install.sh        # asks for the ZE key, installs everything, overwrites nothing
```

Then the onboarding (15 min, fills in your profile):
```bash
cd ~/Documents/Brain && claude
# paste: "Read onboarding/ONBOARDING.md and run it to personalize me."
```

`install.sh` also installs **Obsidian** (if you don't have it) so you can browse your brain visually — open it and "Open folder as vault" → `~/Documents/Brain`.

**Verify your install** any time (safe, runs in a throwaway temp dir — never touches your real brain):
```bash
./test-hooks.sh    # runs all 5 hooks end-to-end, reports pass/fail
```

## Optional: gstack (23 AI specialists)

Garry Tan's [gstack](https://github.com/garrytan/gstack) ships 23 Claude Code skills (a "virtual engineering team": CEO, eng manager, designer, reviewer, QA, CSO, release engineer…). brain-in-a-box installs it alongside on request — they compose: gstack's specialists work, brain-in-a-box captures and remembers.

```bash
./install.sh --with-gstack
```
You get slash commands like `/office-hours`, `/plan-ceo-review`, `/review`, `/qa`, `/retro`, `/investigate`, `/cso`, `/learn` (and 15 more). All read your vault for context, all can write decisions/learnings back into it.

## Team-first vault — humans & agents side by side

The vault treats **humans** (`Team/`) and **AI agents** (`Agents/`) as first-class entities with the same schema. New teammate, human or AI, gets a file. Any agent can query "who knows X" and get a unified answer.

Decisions live in `Decisions/` (one file per decision, with options + trade-offs + status) — answer "why did we choose X?" six months later in one query. Skills live in `Skills/` in [gstack's SKILL.md format](https://github.com/garrytan/gstack) (YAML frontmatter + triggers + workflow) — directly invokable by Claude Code, gstack, or any compatible runtime.

In team mode, all of these federate with the company vault (`BrainCo/`) — your personal entities stay personal, your shared ones are searched across the org.

## Team mode (company)

Beyond the personal brain, a team can share a **company brain** (decisions, projects, clients, processes, "who does what") — each member still keeps their personal brain. Architecture: the company brain is a **private Git repo**, added as a 2nd GBrain source (`company`) on each member's machine, **git-pulled nightly**. No server, 100% local per member.

**Admin (once)**:
```bash
cd brain-in-a-box && ./setup-company.sh          # scaffold + git init
cd ~/Documents/BrainCo
gh repo create <org>/brain-company --private --source=. --push
```

**Each member**:
```bash
git clone <REPO_URL> brain-in-a-box && cd brain-in-a-box
./install.sh --company https://github.com/<org>/brain-company
```
They then have their personal brain **+** the `company` source (federated → found by `gbq query`, or targeted via `--source company`).

**Contributing (open contribution)**:
```bash
cd ~/Documents/BrainCo
# edit/add a .md (decision, project note, client sheet)
git add -A && git commit -m "decision X" && git push
```
Each member's nightly pulls the team's contributions + re-indexes. The dream cycle dedups/consolidates.

## Daily usage

- **Live your life** in Claude Code: the corrections you give are captured in `lessons.md`, your sessions summarized in `Journal/`. Zero effort.
- **Recall**: `gbq query "what did we decide about X?"` → answer from your memory, with the source.
- **In the morning**: the dream cycle deduped/consolidated overnight.

## Security / privacy
- Everything is **local**: your vault, your index, your key. Nothing goes to a third party (except the text embedding sent to ZeroEntropy at indexing time — see their policy).
- Never a secret in the vault (the `file-protection` hook + the directives remind you).

## Updating
```bash
cd brain-in-a-box && git pull && ./install.sh   # idempotent, re-applies without breaking
```

## Not included (on purpose)
- No Telegram bot — separate add-on.
- Embeddings = ZeroEntropy (**free**, no-card account). For a 100%-local/offline variant (nothing leaves the machine), an Ollama `nomic-embed-text` variant is planned — not for cost (ZE is already free), just for full privacy.
