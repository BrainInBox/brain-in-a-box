# Team brain — {{COMPANY}}

Shared vault indexed as the `company` source in each member's GBrain.

## Structure
| Folder | Content |
|---|---|
| `company.md` | Identity, stack, team, conventions |
| `Decisions/` | Locked decisions (one file per decision, dated) |
| `Process/` | Recurring procedures (onboarding, deploy, incident, reconcile…) |
| `Projects/` | Active projects (status, roadmap, decisions) |
| `Clients/` | Client/partner sheets |
| `People/` | Who does what, internal contacts |

## Contributing (open contribution)
Everyone can enrich:
```bash
cd ~/Documents/BrainCo
# edit/add a .md
git add -A && git commit -m "add decision X / project note Y" && git push
```
Each member's nightly `git pull`s this vault + re-indexes. The dream cycle dedups/consolidates.

## Rules
- **Zero secrets** (shared vault + on GitHub). Locations only.
- 1 decision = 1 dated file in `Decisions/` (`YYYY-MM-DD-topic.md`), clear title, the "why".
- Factual, no personal drafts (those go in your personal vault `~/Documents/Brain`).
