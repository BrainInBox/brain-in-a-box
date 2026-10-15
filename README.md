# brain-in-a-box

A **personal second brain** for Claude Code — and a **shared memory layer for mixed AI + human teams**. One command to install. Markdown vault (browsable in Obsidian) + auto-capture hooks + semantic memory (GBrain). Composes with any agent stack: [gstack](https://github.com/garrytan/gstack), [openclaw](https://github.com/openclaw/openclaw), [Hermes](https://github.com/NousResearch/HermesAgent), or plain Claude Code — they all read and write the same vault.

Everyone gets the **same engine**, with **their own data** — everything stays **local on their machine**.

## What's inside

| Layer | What |
|---|---|
| **Vault** (`~/Documents/Brain`) | Your markdown notes: Profile · **Team · Agents · Decisions · Skills** · Journal · Projects · Clients · Resources |
| **Skills** (`~/.claude/skills`) | `brain-boost` (what the setup does, how to grow it safely) · `controle` (the night check: what it fixed, undo, GBrain updates) · `quick-research` · `html-artifact` · `convert-documents-to-markdown`. Installed with the box; a skill of yours with the same name, or one you edited, is never overwritten |
| **Hooks** (`~/.claude/hooks/brain`) | Auto-capture: corrections → `lessons.md`, sessions → `Journal/`, summaries → `memory.md` |
| **GBrain** (`~/.gbrain`) | Search over the vault (`gbq query "..."`): by meaning with a Voyage AI key, by keywords without one |
| **Nightly** (launchd 04:00; Linux: a systemd user timer, else cron) | update GBrain → night check → commit vault → sync → dream cycle (dedup, facts, consolidation). Windows (Task Scheduler 04:00, catches up when the PC wakes): update GBrain → night check → commit vault → sync → embed |
| **GBrain updates** (in the nightly) | the newest release this machine has known for at least 7 days: never master, never lower, with the exact Bun gbrain's release uses (checksum verified). Code, Bun, config and brain are snapshotted first; if `gbrain doctor` fails after the switch, all of it is restored. A new install starts at the release CI tests (`engine/gbrain-pin.json`) |
| **Night check** (in the nightly, `/controle`) | Fixes what is safe by itself, one git commit per fix: masks passwords and keys written in clear (the value stays in a local quarantine, outside the vault, never pushed, 30 days or as long as its mask is in a note), puts a warning above commands that erase things, removes hidden characters, moves `memory.md` entries older than 15 days to `Profile/Archive/`, sets aside duplicate or pasted lessons, deletes exact copies (frontmatter included) and empty notes (no text, title or property of yours), repairs broken frontmatter. With Claude Code logged in, Claude (no tools, masked excerpts) also points out contradictions and replaced decisions; the script checks its quotes and writes the warnings. The rest is listed in `Profile/controle.md`. A vault without git, or in the middle of a merge or rebase, is left untouched |
| **Session start** (SessionStart) | one line on what the night check did, or on a job that stopped (no check for 48 h, no update check for 72 h). If no GBrain update check succeeded for 36 h (machine off at 04:00), one starts in the background |
| **Reflection** (launchd or systemd/cron 12:00 + 23:00) | LLM summary of the day's sessions and your git commits → `Journal/` + the day's entry in `memory.md` (the night check archives entries older than 15 days). Claude answers with no tool; the script writes. Secrets masked before sending; a failing run pins a warning in `memory.md`. Missed a day? `daily-reflection.py --day YYYY-MM-DD` (`--dry-run` to preview) |
| **Weekly lint** (launchd or systemd/cron Monday 08:00) | Verifies the whole pipeline (doctor, lint, orphans, nightly freshness) → 🟢/🟠/🔴 verdict pinned in `memory.md` + report in `Profile/lint.md` |

It all runs inside Claude Code (terminal/IDE). No bot, no server, no shared cloud.

## Install (per person)

### Download the app (preview)

**[brain-in-a-box.vercel.app](https://brain-in-a-box.vercel.app)**: the download for your system and a step-by-step guide in French, with the real screens (`site/`).

No terminal: a window checks your Mac or PC (Windows or Linux), installs what is missing and sets up your brain (the interface is in French). Every change to the app publishes an **unsigned preview** on the [Releases page](https://github.com/BrainInBox/brain-in-a-box/releases?q=pr%C3%A9version&expanded=true) (tags `app-v<version>-preview.<n>`, the 5 newest are kept), with a `SHA256SUMS` file and a build attestation (how to check one: `app/README.md`).

**Mac** (macOS 13+, Apple Silicon and Intel)

1. Download `Brain-in-a-Box_<version>-preview.<n>_macos-universal.dmg`, open it and drag **Brain in a Box** to **Applications**.
2. Open the app. macOS blocks it because Apple has not signed it yet: close the message (do not move it to the Bin).
3. Go to **System Settings → Privacy & Security**, scroll to *Security*, click **Open Anyway** (« Ouvrir quand même » on a Mac in French) next to Brain in a Box, and confirm. From then on it opens normally.

**Windows** (10 or 11, 64-bit)

1. Download `Brain-in-a-Box_<version>-preview.<n>_windows-x64-setup.exe` and run it (no administrator rights asked).
2. If Windows says « Windows a protégé votre ordinateur » (SmartScreen, the app is not signed yet), click **Informations complémentaires**, then **Exécuter quand même**.

**Linux** (Ubuntu 22.04+, Debian 12, x86-64; tested on Ubuntu 24.04)

- **AppImage**, nothing to install: download `Brain-in-a-Box_<version>-preview.<n>_linux-x86_64.AppImage`, make it executable (right click → Properties → « Exécutable comme un programme », or `chmod +x` it), then double-click it. Nothing else to install on Ubuntu 22.04 or 24.04: it mounts itself with the system's FUSE (`fusermount3`, package `fuse3`, on every Ubuntu desktop). Elsewhere, if it does not start: `sudo apt install fuse3`, or `./Brain-in-a-Box_…AppImage --appimage-extract-and-run`. With a screen reader on Ubuntu 24.04, prefer the `.deb`: the AppImage, built on 22.04, gives some accessibility roles shifted there (a paragraph announced as a footer).
- **.deb**: `sudo apt install ./Brain-in-a-Box_<version>-preview.<n>_linux-amd64.deb`, then open Brain in a Box from the applications menu.

The app installs git, python3 and curl when the system lacks them (a fresh Ubuntu desktop has neither git nor curl), and Obsidian's official package: for those two steps only, the system's own window asks for your password (pkexec): it shows « Brain in a Box installe » and the packages, or Obsidian. What runs as root is two fixed scripts that install only those packages, and only Obsidian's pinned package (`app/src-tauri/src/root.rs`). Everything else lands in your home folder, as on a Mac. Another distribution: the app says what to install with its package manager. arm64: no Obsidian package, the app says where to get it.

### Windows

Press **Win + R**, paste this line, press **Enter** (it works the same pasted in PowerShell or the Command Prompt):

```
powershell -NoExit -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/BrainInBox/brain-in-a-box/master/install.ps1 | iex"
```

That's all. It installs what's missing (Python, Git, Bun, Claude Code, Obsidian — per-user, no admin rights when the package allows it) and the same memory as on macOS (GBrain), sets up your brain in `Documents\Brain`, and prints the next steps. Along the way it offers to add a [Voyage AI](https://dashboard.voyageai.com) key so your brain finds notes by meaning; press Enter to skip, search then works on keywords, and running the line again later adds the key. Run the same line again to update. You need a Claude **Pro or Max** plan to use Claude Code.

Prefer to read the code first? Download the repository (**Code → Download ZIP**), extract it, read it, then run `powershell -ExecutionPolicy Bypass -File install.ps1` from the extracted folder: it uses those files and downloads nothing else from this repo. Started without a console (from an app, a script), it asks nothing: set `VOYAGE_API_KEY` in that process to give the key.

GBrain starts at the release the Windows CI tests (`engine/gbrain-pin.json`), then the nightly moves it to the newest release at least 7 days old, as on macOS; running the install line again never moves it back down. If it cannot be installed (no network, a locked-down PC), or stops answering, `gbq query` answers from a local keyword index instead: search never goes away. Details in [the port notes](docs/proposals/windows-port.md); the install log is `~\.brain-in-a-box\install.log`.

### macOS

Requirements: [Claude Code](https://code.claude.com/docs/en/setup). Optional: a [Voyage AI](https://dashboard.voyageai.com) key, so your brain finds notes by meaning, not just keywords. Voyage asks for a card (without one it allows 3 requests a minute); 200M tokens are free and a personal brain uses well under 1% of that.

```bash
git clone <URL_OF_THIS_REPO> brain-in-a-box
cd brain-in-a-box
./install.sh        # offers to add a Voyage key (Enter skips), installs everything, overwrites nothing
```

Then the onboarding (15 min, fills in your profile):
```bash
cd ~/Documents/Brain && claude
# paste the line the installer printed at the end:
# "Read <path-to-brain-in-a-box>/onboarding/ONBOARDING.md and run it to personalize me."
```

`install.sh` also installs **Obsidian** (if you don't have it) so you can browse your brain visually — open it and "Open folder as vault" → `~/Documents/Brain`.

**Verify your install** any time (safe, runs in a throwaway temp dir — never touches your real brain):
```bash
./test-hooks.sh    # runs all hooks + gbrain self-update end-to-end, reports pass/fail
```

### Linux (Debian, Ubuntu)

Tested on **Ubuntu 24.04** x86-64 (CI and the nightly field tests). Expected to work on Debian 12+, Ubuntu 22.04+ and their derivatives (Mint, Pop!_OS), x86-64 or arm64, WSL 2 included. A fresh Ubuntu desktop has no git or curl:

```bash
sudo apt install -y git curl
git clone <URL_OF_THIS_REPO> brain-in-a-box
cd brain-in-a-box
./install.sh        # as yourself, not with sudo: everything goes in your home folder
```

It installs Bun, GBrain and Claude Code (Anthropic's official installer) in your home folder, and runs `sudo` only after you say yes in the terminal: to install a missing package (python3 3.9+, git, curl, unzip, procps, with apt-get), to turn on lingering, or to install Obsidian. Started without a terminal (a script, an app), it asks nothing: it prints the exact command and goes on, or stops when a prerequisite is missing. Then the onboarding, as on macOS.

- **Scheduled jobs**: systemd user timers (`gbrain-nightly`, `brain-reflection`, `gbrain-lint` in `~/.config/systemd/user`; see them with `systemctl --user list-timers`). A time missed while the computer was off runs at the next start. They run while you are logged in; say yes to *lingering* (`loginctl enable-linger $USER`) for them to run logged out too, on a server for instance. Where your user has no systemd (WSL without `systemd=true` in `/etc/wsl.conf`, a container), the installer uses cron instead and says so: lines between `# >>> brain-in-a-box` markers in your crontab, the rest of it untouched. Cron does not catch up a missed night, and its service must run (`sudo service cron start` on WSL).
- **Obsidian**, on Debian or Ubuntu x86-64: its official `.deb` (pinned version, SHA-256 checked), installed with `sudo apt-get` once you say yes, else the one line to paste. Not the AppImage: Ubuntu 24.04 lacks the libfuse2 it needs, and restricts the user namespaces of Electron's sandbox, so it would only run with `--no-sandbox`. On arm64 (no `.deb`) or another distribution: [obsidian.md](https://obsidian.md/download), or `flatpak install --user flathub md.obsidian.Obsidian`.
- **Other distributions** (Fedora, Arch…): `install.sh` runs there too, untested; it names a missing prerequisite instead of installing it.
- `gbq` and the nightly scripts are bash: no zsh needed.

## Optional: gstack (23 AI specialists)

Garry Tan's [gstack](https://github.com/garrytan/gstack) ships 23 Claude Code skills (a "virtual engineering team": CEO, eng manager, designer, reviewer, QA, CSO, release engineer…). brain-in-a-box installs it alongside on request — they compose: gstack's specialists work, brain-in-a-box captures and remembers.

```bash
./install.sh --with-gstack
```
You get slash commands like `/office-hours`, `/plan-ceo-review`, `/review`, `/qa`, `/retro`, `/investigate`, `/cso`, `/learn` (and 15 more). All read your vault for context, all can write decisions/learnings back into it.

## Team-first vault — humans & agents side by side

The vault treats **humans** (`Team/`) and **AI agents** (`Agents/`) as first-class entities with the same schema. New teammate, human or AI, gets a file. Any agent can query "who knows X" and get a unified answer.

Decisions live in `Decisions/` (one file per decision, with options + trade-offs + status) — answer "why did we choose X?" six months later in one query. Procedures live in `Skills/` in [gstack's SKILL.md format](https://github.com/garrytan/gstack) (YAML frontmatter + triggers + workflow): the team's library, searchable with `gbq`. Claude Code only *runs* skills from `~/.claude/skills/<name>/SKILL.md`, so copy one there when it should trigger on its own.

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
- **Skillify**: `gbq skillify <url-or-file>` extracts a SKILL.md draft from a tweet, article, or doc (via [gbrain](https://github.com/garrytan/gbrain)). Keep it in `~/Documents/Brain/Skills/` (searchable), and copy it to `~/.claude/skills/<name>/SKILL.md` when Claude Code should use it on its own. The `brain-boost` skill walks you through it.
- **Brainstorm / code search**: `gbq brainstorm "<question>"` (idea generation), `gbq code-def <symbol>` / `code-refs <symbol>` (when you indexed a code repo as a source). All gbrain commands are safe through `gbq` — it routes reads through the lock-safe path and writes via clean wait.
- **In the morning**: the dream cycle deduped/consolidated overnight, and Claude's first line says what the night check did. `/controle` explains it; `/controle annuler <ID>` undoes one fix (the IDs are in `Profile/controle.md`), `/controle annuler derniere-nuit` the whole night (masked secrets stay masked: each one comes back on its own, by its ID), `/controle pause` stops the fixes and keeps the report.

## Get the most out of it

Read [PHILOSOPHY.md](PHILOSOPHY.md): nine habits that turn Claude from an advisor into an operator. Not sure where to start? Ask Claude *"what can you do for me?"*: the `brain-boost` skill answers.

**The magic prompt.** Paste it in a new session, one topic at a time:
> Looking at all the work and tasks I've given you about **<topic>**, what can we automate? Posting, writing, research, scheduling, whatever makes sense. And what would the setup look like?

- **Show, don't describe**: paste a screenshot (Ctrl+V on macOS, Alt+V on Windows) or drag a file or a PDF into Claude Code.
- **Talk instead of typing**: your computer's dictation works in the prompt (Windows: Win+H, macOS: press Fn twice).
- **Teach it once**: "from now on…" or "never…" is saved to `lessons.md` and applies to every future session.
- **One automation a week**: automate one chore end to end, live with it, then take the next.

| You are… | Start with |
|---|---|
| Creator / marketer | the magic prompt, `html-artifact` for visual pages, screenshots for feedback |
| Student / researcher | `quick-research`, `convert-documents-to-markdown`, PDFs read directly |
| Ops / business | recurring reports, one file per decision in `Decisions/`, app connections (`brain-boost` explains) |
| Developer | everything, plus `./install.sh --with-gstack` on macOS |
| Personal life | "fix this for me", a screenshot of what's broken, dictation |

## Security / privacy
- Everything is **local**: your vault, your index, your key. Nothing goes to a third party (except, with a Voyage key, the text sent to Voyage AI to compute embeddings: in the Voyage dashboard, set Organization → Terms of Service to "Opted Out" so it is not used for training). Installs from before 0.3 keep ZeroEntropy.
- Never a secret in the vault. The global `CLAUDE.md` directives tell your agents so, and the night check masks the passwords and keys it recognises before your notes are committed. One that was already committed stays in git history, and wherever the vault was pushed: the report tells you to change it. Keep secrets in your password manager or Keychain and write down where they live, never the value.
- The night check never pushes, never rewrites git history, never follows a link out of the vault, and never lets Claude write: Claude only points at quotes, the script checks them and writes fixed sentences.
- The reflection and the night check call Claude only through your claude.ai subscription, never an API key (it would be billed). On a machine without a browser (a server, a VM), run `claude setup-token` and save the token it prints, alone on its line, in `~/.brain-in-a-box/claude-oauth-token`, readable by you only (`chmod 600`). It counts as your subscription: they read it when they ask Claude and give it to Claude alone, never to the other steps of the night (gbrain included) nor to a log. Do not put it in the scheduled jobs' environment: `launchctl setenv` would hand it to every program of your session.
- **Found a security issue?** See [SECURITY.md](SECURITY.md) — please use GitHub Security Advisories, not public Issues.

## Contributing
PRs welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow (the `master` branch is protected — every change goes through a PR). MIT licensed (see [LICENSE](LICENSE)). Changelog tracked in [CHANGELOG.md](CHANGELOG.md).

## Updating & undo

Update: on Windows, run the install line again; a hook, `gbq` or search script that differs from the new version (one you adapted) is copied to `~\.brain-in-a-box\backups\<date>\` before being replaced, `settings.json` to `settings.json.bak-<date>`, and the installer lists them. On macOS and Linux (a file you changed, a timer or cron line included, is kept; the new version waits in `~/.brain-in-a-box/pending`):
```bash
cd brain-in-a-box && git pull && ./install.sh   # idempotent, re-applies without breaking
```

Undo any part. Your vault (`Documents/Brain`) is never touched by these:

| What | macOS (Linux: the same, except where said) | Windows (PowerShell) |
|---|---|---|
| Hooks | `rm -rf ~/.claude/hooks/brain`, then remove their entries in `~/.claude/settings.json` | `Remove-Item -Recurse ~\.claude\hooks\brain`, same for `settings.json` |
| One skill | `rm -rf ~/.claude/skills/<name>` | `Remove-Item -Recurse ~\.claude\skills\<name>` |
| Scheduled jobs | `launchctl bootout gui/$(id -u)/com.$USER.gbrain-nightly` (same for `brain-reflection`, `gbrain-lint`). Linux: `systemctl --user disable --now gbrain-nightly.timer brain-reflection.timer gbrain-lint.timer`, or `crontab -e` and delete the `brain-in-a-box` block | `Unregister-ScheduledTask BrainNightly, BrainReflection -Confirm:$false` |
| GBrain and its index | `rm -rf ~/DEV/gbrain ~/.gbrain` | `Remove-Item -Recurse ~\DEV\gbrain, ~\.gbrain, ~\.brain-search`; Bun: Settings → Apps → Bun |
| Global directives | in `~/.claude/CLAUDE.md`, delete the block that starts with `<!-- brain-in-a-box -->` | same file |

## Not included (on purpose)
- No Telegram bot — separate add-on.
- Embeddings = Voyage AI (optional; keyword search without a key). A 100%-local/offline variant (nothing leaves the machine), with Ollama `nomic-embed-text`, is planned for full privacy.
