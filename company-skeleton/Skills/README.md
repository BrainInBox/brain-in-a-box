# Skills — invocable procedures for any agent in the company

> One file per skill. YAML frontmatter is the [gstack](https://github.com/garrytan/gstack) **SKILL.md** format — directly usable by gstack, Claude Code, and any agent stack that respects the convention.

**Skills are distinct from `../Process/`**:
- `Skills/` — invocable procedures with YAML triggers (an agent detects a phrase and executes the workflow). For human + AI consumption.
- `Process/` — HR / ops / business processes (onboarding flow, vacation policy, expense reimbursement). Mostly human consumption, descriptive not invocable.

Pattern: `Skills/{{slug}}.md`. Start from `_template.md`.

Frontmatter fields:
- `name` — slug (matches filename)
- `version` — semver, bump on behavior change
- `description` — paragraph; agent reads it to decide whether to invoke
- `allowed-tools` — security gate (which Claude Code tools the skill may use)
- `triggers` — phrases that auto-route to this skill ("when in doubt, invoke")

A skill here is invokable by any team member's Claude Code via the trigger phrases or as a slash command. Federated with each member's personal `Skills/`.
