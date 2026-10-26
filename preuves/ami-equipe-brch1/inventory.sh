#!/bin/zsh
# inventory.sh (runs IN the test VM): prints one line per note of ~/Documents/Brain, "sha256  path",
# sorted, every file except git's own folder; then the kit's brain page count and the vault's last commit.
export LC_ALL=C  # same order from any shell (Terminal in French sorts differently)
cd ~/Documents/Brain || exit 1
find . -type f -not -path './.git/*' -print0 | sort -z | xargs -0 shasum -a 256
echo "# files: $(find . -type f -not -path './.git/*' | wc -l | tr -d ' ')"
echo "# git: $(git log -1 --format='%h %s') | changes not saved: $(git status --porcelain | wc -l | tr -d ' ')"
export PATH=$HOME/.bun/bin:$HOME/.local/bin:$PATH
echo "# gbrain: $(gbrain sources list 2>/dev/null | grep -o '[0-9]* pages' | tr '\n' ' ')"
