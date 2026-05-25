#!/usr/bin/env bash
# brain-in-a-box — setup-company: the ADMIN runs this ONCE to create the team
# brain (shared vault). Scaffolds it, git inits, and prints how to push it to
# GitHub + how members join.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CO="$HOME/Documents/BrainCo"

say(){ printf "\033[1;36m▶ %s\033[0m\n" "$*"; }
ok(){ printf "  \033[1;32m✓\033[0m %s\n" "$*"; }

say "Company name"
read -r -p "  Company: " NAME
[ -n "$NAME" ] || { echo "empty name"; exit 1; }

if [ -d "$CO" ]; then
  echo "  ⚠ $CO already exists — aborting (delete/rename it to start fresh)"; exit 1
fi

say "Scaffolding the company vault → $CO"
mkdir -p "$CO"
cp -R "$REPO/company-skeleton/." "$CO/"
# inject the name into company.md
/usr/bin/sed -i '' "s#{{COMPANY}}#$NAME#g" "$CO/company.md" "$CO/CLAUDE.md" 2>/dev/null || \
  sed -i "s#{{COMPANY}}#$NAME#g" "$CO/company.md" "$CO/CLAUDE.md"
ok "structure placed (remaining {{...}} to fill in company.md)"

say "git init"
( cd "$CO" && printf '.obsidian/\n.DS_Store\n' > .gitignore && git init -q \
  && git add -A && git -c user.email="brain@local" -c user.name="brain" commit -q -m "company brain init: $NAME" )
ok "local git repo ready"

cat <<EOF

\033[1;32m✅ Team brain created locally.\033[0m

STEP 1 — push it to a PRIVATE GitHub repo (access = your members):
  cd $CO
  gh repo create <your-org>/brain-company --private --source=. --push

STEP 2 — fill in the company profile (you, or via Claude Code):
  cd $CO && claude
  # "Fill company.md with the company context (stack, team, conventions)."

STEP 3 — give each member the join command:
  cd brain-in-a-box && ./install.sh --company https://github.com/<your-org>/brain-company

Members get their personal brain + the 'company' source (queryable, git-pulled nightly).
Contribute = edit in ~/Documents/BrainCo + git push (open contribution).
EOF
