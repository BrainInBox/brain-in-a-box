# Team brain — {{COMPANY}}

Shared vault indexed as the `company` source in each member's GBrain.

## Structure
| Folder | Content |
|---|---|
| `company.md` | Identity, stack, team, conventions |
| `Team/` | Humans on the team — one file per person (replaces older `People/`) |
| `Agents/` | AI agents the company uses — same schema as `Team/`, both first-class |
| `Decisions/` | Locked decisions, one file per decision (`YYYY-MM-DD-<slug>.md`) |
| `Skills/` | Procedures in [gstack SKILL.md format](https://github.com/garrytan/gstack) — invokable by any agent |
| `Process/` | HR / ops / business processes (onboarding, vacation, expense) — distinct from `Skills/` |
| `Projects/` | Active projects (status, roadmap, decisions) |
| `Clients/` | Client/partner sheets |

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
