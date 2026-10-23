# Skills — tes procédures

Une note par tâche que Claude sait refaire pour toi : quand la faire, et ses étapes. Claude les écrit quand tu lui apprends une tâche que tu refais souvent. Les procédures que Bibo t'a montrées dans un logiciel sont dans le dossier `Guides`.

## In English

> One file per skill. The YAML frontmatter is the [gstack](https://github.com/garrytan/gstack) **SKILL.md** format: this folder is your library of procedures, searchable with `gbq`. Claude Code only *runs* skills from `~/.claude/skills/<name>/SKILL.md`: copy one there (folder named after the skill) when it should trigger on its own. Ask Claude, the `brain-boost` skill knows how.

Pattern: `Skills/{{slug}}.md`. Start from `_template.md`.

The frontmatter fields:
- `name` — the skill's slug (matches the filename)
- `version` — semantic version, bump on behavior change
- `description` — one paragraph; the agent reads this to decide whether to invoke
- `allowed-tools` — which Claude Code tools the skill may use (security gate)
- `triggers` — phrases that should auto-route to this skill ("when in doubt, invoke")

What goes in the body:
- **When to invoke** — explicit conditions
- **Workflow** — numbered steps, the actual procedure
- **Examples** — input → output
- **Quality gates** — what must be true before "done"

Why this matters for your vision:
- A new agent on the team reads `Skills/` and immediately knows what procedures exist.
- A correction captured by the brain hooks can be promoted to a skill (manual for now; eventual auto-promotion when SkillOpt-style evals land in gbrain).
- Federated with `BrainCo/Skills/` in team mode → shared org-wide procedures.

If you also install **gstack**, its skills live at `~/.claude/skills/gstack/` and don't move into this folder — but you reference them from your `../Agents/<name>.md` files so the team registry stays unified.
