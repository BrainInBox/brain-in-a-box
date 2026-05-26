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

- The `install.sh` script (running it on a clean Mac shouldn't be exploitable)
- The hooks (`engine/hooks/*.py`) — they execute on every Claude Code session
- The nightly script (`engine/nightly/gbrain-nightly.sh`) — runs as launchd
- The `gbq` wrapper — runs against your local gbrain
- Path-traversal / injection in any of the above

## What's NOT in scope (upstream projects)

- **GBrain** (garrytan/gbrain) — report to the gbrain project directly
- **gstack** (garrytan/gstack) — report to gstack project
- **ZeroEntropy** (the embedding API) — report to zeroentropy.dev
- **Claude Code** itself — report to Anthropic

## What's NOT a vulnerability

- A friend you added as collaborator can push code (that's collaboration, not a vuln — see the README discussion on trust models)
- The brain content is readable to whoever has access to your machine (it's plaintext markdown, by design)
- An attacker with shell access on your machine can read `~/.gbrain/config.json` (file is `0600`, but root or your own user can still read it — that's local-trust scope)

## Hardening recommendations (for users)

- Enable FileVault on your Mac (encrypts the vault at rest)
- Don't share your `~/Documents/Brain` over insecure channels
- If you set up team mode (BrainCo), use a private GitHub repo and audit who has access
- Never put secrets in the vault — references only (Keychain entries, file paths, env var names)

## Credit

Vulnerability reporters who follow this policy get credit in the fix release notes, unless they opt out.
