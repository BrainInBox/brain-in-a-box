# Contributing to brain-in-a-box

Thanks for wanting to make this better. The project is small, the workflow is straightforward.

## Quick rules

- **macOS only** (the nightly + reflection rely on `launchd`). Linux/Windows ports welcome via discussion first — propose the approach before coding.
- **One concern per PR.** Schema change in one PR, doc change in another. Easier to review, easier to revert.
- **No secrets, ever.** Not in the vault, not in tests, not in code. The `file-protection.sh` hook tries to catch them but don't rely on it.
- **Match the existing style.** Concise English, no corporate fluff. Read what's there before writing.

## Workflow

The `master` branch is protected: no direct push, no force-push, no deletion. Every change goes through a PR.

```bash
# 1. Branch from master
git checkout master && git pull --ff-only
git checkout -b <type>/<short-name>     # e.g. fix/yaml-validation, feat/ollama-variant

# 2. Make your change

# 3. Run tests locally BEFORE pushing
bash test-hooks.sh                       # 25/25 expected
bash -n install.sh                       # syntax check on every shell script you touched

# 4. Push + open PR
git push -u origin <your-branch>
gh pr create                             # or use the GitHub UI

# 5. CI runs automatically (.github/workflows/test.yml — macos-latest)
#    All checks must pass. 1 review approval required before merge.
```

## What you can contribute

- **Hook fixes** (`engine/hooks/*.py`) — fixes welcome, ping if behavior change
- **New skills** (`vault-skeleton/Skills/` templates) — gstack-compatible SKILL.md format
- **Docs** (`README.md`, `CONTRIBUTING.md`, this one) — clarity > completeness
- **CI improvements** (`.github/workflows/`) — make tests faster, add coverage
- **Linux port** (open an issue first to discuss approach — launchd needs replacement)
- **Ollama variant** (`engine/bin/gbq`, install.sh) — opt-in offline embeddings
- **Translations** (vault-skeleton templates) — keep the structure, swap the prose

## What NOT to contribute

- New folders in `vault-skeleton/` without strong rationale (the schema is meant to stay stable)
- Heavy dependencies (the install is bash + python3 + bun + git; keep it light)
- Telemetry, analytics, or any phone-home (the project is 100% local by design)

## Where to ask first

- **Vision / scope questions** → open a Discussion (not an Issue)
- **Bugs** → open an Issue using the template
- **Security vulnerabilities** → see [SECURITY.md](SECURITY.md), never an Issue
- **"Is this aligned with the roadmap?"** → ping in a Discussion before coding

## A note on the maintainer's bandwidth

This is a side project of one person. PRs that are tight, tested, and aligned get merged fast. PRs that need a lot of back-and-forth may sit. If your PR has been quiet for >2 weeks, ping it.
