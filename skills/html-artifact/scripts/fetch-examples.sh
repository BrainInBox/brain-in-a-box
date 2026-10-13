#!/usr/bin/env bash
# Download the 20 example pages of Anthropic's html-effectiveness gallery
# (MIT, https://github.com/anthropics/html-effectiveness) and print the folder
# they are in.
#
# Pinned to one commit, so the content never changes under you. Safe to run
# every time: once the files are there it does nothing and needs no network.
# They go to a cache folder, not into the skill, so updating the skill never
# sees them as your own edits.
#
# Usage: bash fetch-examples.sh
# macOS, Linux and Windows (Git Bash). Needs git and internet the first time.
set -euo pipefail

REPO_URL="https://github.com/anthropics/html-effectiveness"
COMMIT="58c305be97f47b26b678f2c07dec01d4242268ec"
DEST="${XDG_CACHE_HOME:-$HOME/.cache}/html-artifact/examples"

show() {
  local dir="$DEST" count
  command -v cygpath >/dev/null 2>&1 && dir="$(cygpath -w "$DEST")"
  count="$(find "$DEST" -maxdepth 1 -name '[0-9]*.html' | wc -l | tr -d ' ')"
  echo "$count example pages in: $dir"
}

if [ "$(cat "$DEST/.commit" 2>/dev/null)" = "$COMMIT" ]; then
  show
  exit 0
fi

if ! command -v git >/dev/null 2>&1; then
  echo "error: git is not installed (macOS: xcode-select --install, Windows: winget install Git.Git)" >&2
  exit 1
fi

mkdir -p "$(dirname "$DEST")"
TMP="$(mktemp -d "$(dirname "$DEST")/examples.tmp.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading the example pages (about 400 KB) ..."
git -c init.defaultBranch=main -C "$TMP" init -q
if ! git -C "$TMP" fetch -q --depth 1 "$REPO_URL" "$COMMIT"; then
  echo "error: download failed (no internet?). Work from the references alone." >&2
  exit 1
fi
git -C "$TMP" -c advice.detachedHead=false checkout -q FETCH_HEAD
rm -rf "$TMP/.git"
echo "$COMMIT" > "$TMP/.commit"

rm -rf "$DEST"
mv "$TMP" "$DEST"
trap - EXIT
show
