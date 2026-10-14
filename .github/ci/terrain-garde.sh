#!/usr/bin/env bash
# The check "terrain-garde" (.github/workflows/terrain-garde.yml): a pull request of the repair
# agent (a claude/terrain-* branch) goes red when it touches a path of .github/CODEOWNERS,
# deletes or changes a line of a test, or adds a Python module beside the tests that is not a
# test_*.py (a file there could stand for a module the field test imports, in a job that holds
# the Claude token). The agent merges green pull requests only
# (docs/terrain-agent.md): those wait for the maintainer. Any other pull request passes.
# Reads the pull request's file list through the API; its code is never checked out here.
#
#   PR=<number> HEAD_REF=<branch> HEAD_REPO=<owner/repo> GITHUB_REPOSITORY=<owner/repo> terrain-garde.sh
set -euo pipefail

case "$HEAD_REF" in
  claude/terrain-*) ;;
  *) echo "terrain-garde: not a repair-agent branch ($HEAD_REF), nothing to check"; exit 0 ;;
esac
if [ "$HEAD_REPO" != "$GITHUB_REPOSITORY" ]; then
  echo "::error::terrain-garde: a fork's claude/terrain-* branch is not the repair agent's"
  exit 1
fi

owned=$(grep -v '^#' .github/CODEOWNERS | awk 'NF { sub("^/", "", $1); print $1 }')
[ -n "$owned" ] || { echo "::error::terrain-garde: .github/CODEOWNERS lists nothing"; exit 1; }
files=$(gh api --paginate "repos/$GITHUB_REPOSITORY/pulls/$PR/files" \
  --jq '.[] | [.filename, (.previous_filename // "-"), (.deletions | tostring), .status] | @tsv')

# "-" for no previous name: a tab is whitespace to read, an empty field would vanish.
bad=0
while IFS=$'\t' read -r name prev deletions status; do
  [ -n "$name" ] || continue
  for f in "$name" "$prev"; do
    [ -n "$f" ] && [ "$f" != "-" ] || continue
    while IFS= read -r o; do
      if [ "$f" = "$o" ] || { [ "${o%/}" != "$o" ] && [ "${f#"$o"}" != "$f" ]; }; then
        echo "::error::terrain-garde: $f is the maintainer's to review (.github/CODEOWNERS: /$o)"
        bad=1
      fi
    done <<< "$owned"
  done
  case "$name" in
    engine/tests/*|test-hooks.sh|app/src-tauri/tests/*)
      if [ "$deletions" != 0 ]; then
        echo "::error::terrain-garde: $name loses or changes $deletions line(s) of a test, the maintainer reviews it"
        bad=1
      fi ;;
  esac
  # A package too (engine/tests/<name>/__init__.py): only a new test_*.py passes.
  case "$name:$status" in
    engine/tests/*.py:added|engine/tests/*.py:renamed|engine/tests/*.py:copied)
      case "${name##*/}" in
        test_*.py) ;;
        *) echo "::error::terrain-garde: $name is a new module beside the tests, the maintainer reviews it"
           bad=1 ;;
      esac ;;
  esac
done <<< "$files"

[ "$bad" = 0 ] && echo "terrain-garde: nothing here is the maintainer's alone"
exit "$bad"
