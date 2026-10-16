# Security policy

## Reporting a vulnerability

**Do NOT open a public GitHub Issue for security bugs.**

Use one of these instead:
1. **GitHub Security Advisories** (preferred): https://github.com/BrainInBox/brain-in-a-box/security/advisories/new
2. **Direct contact** via the maintainer's profile: https://github.com/Chad-Mufasax

Include in the report:
- Steps to reproduce
- Impact (what an attacker could do)
- Suggested mitigation if you have one
- Whether you'd like credit in the fix announcement

## Response

The maintainer is one person. Reasonable response targets:
- **Acknowledgment**: within 72h
- **Triage + severity**: within 1 week
- **Fix or mitigation**: depends on severity (critical = days, low = weeks)

## What's in scope

- The `install.sh` and `install.ps1` scripts (running them on a clean Mac, PC or Linux machine shouldn't be exploitable), and what `install.sh` runs as root on Linux (apt, after a yes)
- The hooks (`engine/hooks/*.py`) — they execute on every Claude Code session
- The nightly script (`engine/nightly/gbrain-nightly.sh`) — runs from launchd on a Mac, a systemd user timer (or cron) on Linux
- The desktop app (`app/`), and on Linux the two fixed commands it runs as root through pkexec (`app/src-tauri/src/root.rs`)
- The `gbq` wrapper — runs against your local gbrain
- Path-traversal / injection in any of the above

## What's NOT in scope (upstream projects)

- **GBrain** (garrytan/gbrain) — report to the gbrain project directly
- **gstack** (garrytan/gstack) — report to gstack project
- **Voyage AI** (the embedding API, ZeroEntropy on installs from before 0.3) — report to voyageai.com (or zeroentropy.dev)
- **Claude Code** itself — report to Anthropic

## What's NOT a vulnerability

- A friend you added as collaborator can push code (that's collaboration, not a vuln — see the README discussion on trust models)
- The brain content is readable to whoever has access to your machine (it's plaintext markdown, by design)
- An attacker with shell access on your machine can read `~/.gbrain/config.json` (file is `0600`, but root or your own user can still read it — that's local-trust scope)

## Codex (built, not open on any system yet)

When you choose Codex (`--agents codex`, `-Agents codex` on Windows), the installer (`install.sh`,
`install.ps1`) **approves the kit's six Codex hooks for you**: it adds their trust entries to
`~/.codex/config.toml` (only new `[hooks.state."…"]` tables, after a backup, checked by Codex itself),
and says so on screen. Codex then runs them without asking. The approval covers each hook's command
line, not the script's content. See them, or turn them off, with `/hooks` in Codex: a hook you turned
off stays off when the installer runs again, even after a hook of yours moved it. On Windows, run again
after Python moved (another version installed), it points the kit's hooks at the new Python and
approves them again; an entry of the kit's you changed is kept as it is.

On macOS and Linux it also lets Codex run `gbq query` and `gbq search` without asking, outside its
sandbox, and only for `~/.local/bin/gbq` (`~/.codex/rules/brain-in-a-box.rules`). gbq starts these two
reads from `/`, so nothing a session writes in its folder (a `bunfig.toml` whose preload Bun would run,
a `.env`, a `tsconfig.json`) reaches gbrain. Don't give Codex your home folder as its workspace: its
sandbox would then let a session rewrite `~/.local/bin/gbq` itself, which the rule runs. On Windows
there is no such rule, and Codex asks before every gbq: gbq is `gbq.cmd` there, and `cmd.exe` reads its
arguments again (an `&` in them would start a second command).

On Windows, Codex reads system settings from `%ProgramData%\OpenAI\Codex`, a folder any account can
create when it is missing. The evening summary and the night review do not ask Codex while that folder,
or `%ProgramData%\OpenAI`, exists and could be changed by an account other than SYSTEM and the
administrators. The full picture of what Codex can and cannot do with your brain comes with Codex's
opening (docs/proposals/codex.md).

## Hardening recommendations (for users)

- Enable FileVault on your Mac (encrypts the vault at rest)
- Don't share your `~/Documents/Brain` over insecure channels
- If you set up team mode (BrainCo), use a private GitHub repo and audit who has access
- Never put secrets in the vault — references only (Keychain entries, file paths, env var names)

## Credit

Vulnerability reporters who follow this policy get credit in the fix release notes, unless they opt out.
