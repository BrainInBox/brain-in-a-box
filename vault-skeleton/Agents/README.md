# Agents — the AI agents on your team

> Same schema as `../Team/`, but for AI agents. An agent here is a first-class teammate, not a hidden tool — it has a name, a runtime, skills, a scope, and a track record. Anyone (human or other agent) can query "who can do X" and get a unified answer across humans + agents.

Pattern: `Agents/{{agent-name}}.md` per agent. Start from `_template.md`.

Why this matters for mixed AI+human teams:
- A new teammate (human or AI) discovers the team by browsing one folder.
- Each agent file describes **who** the agent is, what it owns, and how to invoke it. The **how it works** (the actual procedure) lives in `../Skills/`.
- Federated with the company vault in team mode — shared agent roster across the org.

Compatible with **gstack**: if you install gstack, its 23 specialists (CEO, eng manager, designer, reviewer, QA, CSO, release engineer…) each get a small entry here pointing to the gstack skill file. brain-in-a-box doesn't ship the skill code — it manages the team.
