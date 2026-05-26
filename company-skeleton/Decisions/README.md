# Decisions — company-wide decisions, one file each

> Architectural, product, business, process. Anything you'll want to answer "why did we choose X?" for, six months from now, in one query. Both humans and agents read this folder before proposing changes that overlap an existing decision.

Pattern: `Decisions/YYYY-MM-DD-{{slug}}.md`. Start from `_template.md`.

**Company decisions** go here. **Personal decisions** stay in each member's `~/Documents/Brain/Decisions/`. The federated query surfaces both.

What makes a decision worth a file:
- It locks in a trade-off (cost vs perf, build vs buy, simple vs flexible)
- Reversing it would cost real time or money
- You'd want anyone joining the company to find it

Status fields:
- `active` — current truth
- `superseded` — replaced by a newer decision (link in the file)
- `reversed` — explicitly walked back
