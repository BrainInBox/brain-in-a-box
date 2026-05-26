---
type: agent
runtime: "{{RUNTIME}}"              # claude-code | hermes | openclaw | custom
skills:
  - "{{SKILL_NAME_1}}"
  - "{{SKILL_NAME_2}}"
owner-of:
  - "{{SCOPE_1}}"
  - "{{SCOPE_2}}"
status: active                      # active | paused | retired
---

# {{AGENT_NAME}}

## Runtime
{{RUNTIME}} — {{RUNTIME_DETAILS}}

## Skills
- {{SKILL_1}} — `Skills/{{slug}}.md`
- {{SKILL_2}} — `Skills/{{slug}}.md`

## Scope
- {{OWNED_THING_1}}
- {{OWNED_THING_2}}

## How to invoke
- Trigger phrases: {{TRIGGER_PHRASES}}
- Slash command (if any): `/{{SLASH}}`

## Recent
- {{YYYY-MM-DD}} — {{OUTCOME}}
