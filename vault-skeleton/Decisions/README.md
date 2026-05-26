# Decisions — one file per meaningful decision

> Architectural, product, business, process. Anything you'll want to answer "why did we choose X?" for, six months from now, in one query. Both humans and agents read this folder before proposing changes that overlap an existing decision.

Pattern: `Decisions/YYYY-MM-DD-{{slug}}.md`. Start from `_template.md`.

What makes a decision worth a file:
- It locks in a trade-off (cost vs perf, build vs buy, simple vs flexible).
- Reversing it would cost real time or money.
- You'd want anyone joining the team to find it.

What does NOT belong here:
- Today's tactical micro-choices (those go in `../Journal/`).
- Lessons learned (those go in `../Profile/lessons.md`).
- Active project status (that goes in `../Projects/<name>.md`).

Status fields:
- `active` — current truth
- `superseded` — replaced by a newer decision (link in the file)
- `reversed` — explicitly walked back (link to the reversal)

Federated with the company vault in team mode → `BrainCo/Decisions/` for company-wide calls. Personal calls stay here.
