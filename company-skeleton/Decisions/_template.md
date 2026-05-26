---
type: decision
date: "{{YYYY-MM-DD}}"
owner: "{{OWNER}}"                 # person or agent
status: active                     # active | superseded | reversed
revisit: "{{WHEN_OR_NULL}}"        # e.g. "after 1st enterprise client" or null
supersedes: "{{LINK_OR_NULL}}"
---

# {{DECISION_TITLE}}

## Context
{{WHAT_TRIGGERED_THIS}}

## Options considered
1. **{{OPTION_A}}** — {{TRADE_OFFS}}
2. **{{OPTION_B}}** — {{TRADE_OFFS}}
3. **{{OPTION_C}}** — {{TRADE_OFFS}}

## Decision
{{WHAT_WE_CHOSE}} — because {{PRIMARY_REASON}}.

## Trade-offs accepted
- {{COST_OR_RISK_1}}
- {{COST_OR_RISK_2}}

## How we'll know if this was right
{{VERIFIABLE_SIGNAL_OR_DATE}}

## Superseded by
{{LINK_IF_REVERSED}}
