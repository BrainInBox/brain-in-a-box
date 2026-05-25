#!/usr/bin/env bash
# try-it-locally.sh — install brain-in-a-box into an ISOLATED home so you can
# experience the full product (vault, hooks, gbq, onboarding, Obsidian) WITHOUT
# touching your real ~/Documents/Brain, ~/.gbrain, ~/.claude, or your launchd.
#
# For someone who already has gbrain installed (like the author) and wants to
# dogfood the package before giving it to a friend. A fresh machine just runs
# ./install.sh directly.
#
# Safe by design:
#   - HOME is redirected to $TRYHOME (default ~/biab-tryit) → all data isolated
#   - launchctl is stubbed → no real launchd job is loaded
#   - gbrain + bun are symlinked from your real install → `bun link` is a no-op
#     on your real repo (your global gbrain never changes)
#   - your real ZeroEntropy key is reused (read locally) so search actually works
#
# Usage:  ./try-it-locally.sh           # set up (or reuse) the sandbox
#         ./try-it-locally.sh --fresh   # wipe and rebuild it
#         ./try-it-locally.sh --teardown
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRYHOME="${BIAB_TRYHOME:-$HOME/biab-tryit}"
STUB="$TRYHOME/.stubs"
REAL_BUN="$HOME/.bun"
REAL_GBRAIN="$HOME/DEV/gbrain"

if [ "${1:-}" = "--teardown" ]; then
  echo "Removing $TRYHOME …"; rm -rf "$TRYHOME"; echo "done. (your real setup was never touched)"; exit 0
fi
if [ "${1:-}" = "--fresh" ] && [ -d "$TRYHOME" ]; then
  echo "Wiping $TRYHOME …"; rm -rf "$TRYHOME"
fi
if [ -d "$TRYHOME/Documents/Brain" ]; then
  echo "Sandbox already set up at $TRYHOME. Use --fresh to rebuild."; SKIP_INSTALL=1; else SKIP_INSTALL=0
fi

[ -d "$REAL_GBRAIN/.git" ] || { echo "✗ need an existing gbrain at $REAL_GBRAIN (this tool is for dogfooding)"; exit 1; }

mkdir -p "$TRYHOME/DEV" "$STUB"
ln -sfn "$REAL_BUN" "$TRYHOME/.bun"
ln -sfn "$REAL_GBRAIN" "$TRYHOME/DEV/gbrain"
printf '#!/bin/zsh\nexit 0\n' > "$STUB/launchctl"; chmod +x "$STUB/launchctl"

if [ "$SKIP_INSTALL" = "0" ]; then
  # reuse the real ZE key so embeddings work (read locally, never printed)
  ZE="$(/usr/bin/python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.gbrain/config.json"))).get("zeroentropy_api_key",""))' 2>/dev/null)"
  [ -n "$ZE" ] || { echo "✗ no ZeroEntropy key found in ~/.gbrain/config.json"; exit 1; }
  echo "▶ Installing brain-in-a-box into $TRYHOME (isolated)…"
  printf '%s\n' "$ZE" | env HOME="$TRYHOME" \
    PATH="$STUB:$REAL_BUN/bin:/opt/homebrew/bin:/usr/bin:/bin:$HOME/.local/bin" \
    bash "$REPO/install.sh" 2>&1 | grep -iE "▶|✓|⚠|✅" | sed 's/^/  /'
fi

cat <<EOF

\033[1;32m✅ Sandbox ready at $TRYHOME — go play (your real brain is untouched).\033[0m

1) Query the memory (works now):
   HOME=$TRYHOME $TRYHOME/.local/bin/gbq query "what is this brain"

2) Do the onboarding (fills the profile, like a friend would):
   HOME=$TRYHOME claude
   # then: "Read $REPO/onboarding/ONBOARDING.md and run it to personalize me."

3) See the hooks fire on a fake session:
   echo '{"prompt":"no, actually do it differently"}' | HOME=$TRYHOME python3 $TRYHOME/.claude/hooks/brain/correction-detector.py

4) Browse the vault in Obsidian:
   open -a Obsidian   # then "Open folder as vault" → $TRYHOME/Documents/Brain

5) Run the nightly maintenance manually (no cron was loaded):
   HOME=$TRYHOME zsh $TRYHOME/.claude/hooks/brain/gbrain-nightly.sh ; cat $TRYHOME/.gbrain/nightly.log

When done:  ./try-it-locally.sh --teardown
EOF
