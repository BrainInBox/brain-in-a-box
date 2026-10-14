#!/usr/bin/env bash
# The commit a terrain run tests (.github/workflows/terrain.yml): a branch, a tag or a full sha
# of THIS repository, and that commit must sit on one of its branches. Anything else is refused:
# refs/pull/N/head, a fork's commit only reachable through a pull request, an option, a range.
# Only collaborators push branches here: a fork's code never runs on the terrain's VMs.
#
#   terrain-ref.sh <ref>     appends sha=<40 hex> to $GITHUB_OUTPUT (stdout without it)
#
# Run in a clone that fetched every branch and tag (actions/checkout with fetch-depth 0), from
# the workflow's own commit: this guard is never the tested ref's code.
set -euo pipefail

ref=${1:-}
refuse() {
  echo "::error::terrain: ref refused, $1"
  exit 1
}

[[ $ref =~ ^[A-Za-z0-9._/-]{1,100}$ ]] || refuse "only letters, digits and ._/- are allowed"
case $ref in
  -* | *..* | refs/* | /* | */ | *//* | *.lock) refuse "not a plain branch, tag or commit name" ;;
esac

if [[ $ref =~ ^[0-9a-f]{40}$ ]]; then
  sha=$(git rev-parse -q --verify "$ref^{commit}") || refuse "unknown commit"
elif sha=$(git rev-parse -q --verify "refs/remotes/origin/$ref^{commit}"); then
  :
elif sha=$(git rev-parse -q --verify "refs/tags/$ref^{commit}"); then
  :
else
  refuse "no branch or tag of that name"
fi

[ -n "$(git branch -r --contains "$sha")" ] || refuse "the commit is on no branch of this repository"
echo "sha=$sha" >> "${GITHUB_OUTPUT:-/dev/stdout}"
