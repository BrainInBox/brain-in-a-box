# Team — les personnes avec qui tu travailles

Une note par personne (collègue, client, fournisseur) : son rôle, ce qu'elle fait, comment la joindre. Claude les écrit quand tu lui parles de quelqu'un. Pour en écrire une toi-même, pars de `_template.md`.

## In English

> One file per person. You and any AI agent can query this folder to answer "who knows X" or "who owns Y". In team mode, your `Team/` is federated with `BrainCo/Team/` — searches return both your personal contacts and the shared company roster.

Pattern: `Team/{{firstname-lastname}}.md` per person. Start from `_template.md` next to this file.

What lives in each file:
- Role + one-liner
- Focus areas
- What they own (projects, components, processes)
- Who they work with on what
- Contact
- Recent activity (manual, accretes over time)

Why a folder and not a list: each person has their own depth, and humans live next door to AI agents (`../Agents/`) — same schema, both first-class members of the team.
