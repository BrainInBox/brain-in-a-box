# Agents — the AI agents the company uses

> Same schema as `../Team/`, but for AI agents. Each agent is a first-class member of the team, not a hidden tool — name, runtime, skills, scope, track record. A new teammate (human or AI) discovers the team by browsing `Team/` + `Agents/`.

Pattern: `Agents/{{agent-name}}.md` per agent. Start from `_template.md`.

What goes in each file:
- Runtime (claude-code, gstack, hermes, openclaw, custom)
- Skills (cf `../Skills/`)
- Scope / owner-of
- Trigger phrases or slash commands to invoke
- Recent successes/failures

If the company adopts **gstack**, its 23 specialists (CEO, eng manager, designer, reviewer, QA, CSO…) each get a small entry here pointing to the gstack skill file — they become known members of the team.
