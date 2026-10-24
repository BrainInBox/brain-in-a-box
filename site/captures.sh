#!/usr/bin/env bash
# Rebuilds site/img/*.webp from the night robot's captures (terrain.yml artifacts captures-<os>-app
# and captures-<os>-app-mise-a-jour, each in its folder: macos/, windows/, linux/, macos-mise-a-jour/,
# windows-mise-a-jour/). See README.md.
#
#   ./captures.sh <folder holding those folders>
#
# Each line: source capture, crop x y width height (in the capture's pixels), output.
# A source without its number (macos/bienvenue) is the first capture of that screen
# (macos/03-bienvenue.png): the robot numbers them in order, and the count before a screen
# changes from night to night. A source with its number and .png is that file. That first capture can
# catch a line still fading in, pale: look at each image, and crop that one from another night's captures.
# The crops follow the app's window at its usual place on the robot's screens
# (1024x768 on macOS and Windows, 1280x900 on Linux). If a window moved, fix its line.
set -euo pipefail

src=${1:?usage: ./captures.sh <captures folder>}
out=$(cd "$(dirname "$0")" && pwd)/img
command -v cwebp >/dev/null || { echo "cwebp is missing (brew install webp, apt install webp)" >&2; exit 1; }

capture() {
  local dir=${1%/*} screen=${1##*/} f
  if [[ $screen == *.png ]]; then
    echo "$src/$1"
    return 0
  fi
  for f in "$src/$dir"/[0-9][0-9]-"$screen".png "$src/$dir"/[0-9][0-9][0-9]-"$screen".png; do
    [[ -f "$f" ]] && { echo "$f"; return 0; }
  done
  return 0
}

while read -r source x y w h name; do
  [[ -z "$source" || "$source" == \#* ]] && continue
  file=$(capture "$source")
  [[ -n "$file" && -f "$file" ]] || { echo "missing: $src/$source" >&2; exit 1; }
  cwebp -quiet -near_lossless 60 -z 9 -metadata none -crop "$x" "$y" "$w" "$h" "$file" -o "$out/$name.webp"
  echo "$name.webp  ${file#"$src"/}  $(wc -c <"$out/$name.webp" | tr -d ' ') bytes"
done <<'EOF'
# Installing the app. The DMG's window as laid out by Finder (release.yml and terrain.yml build it so).
macos/dmg                           10  60 660 400  mac-dmg
macos/gatekeeper                   382 129 260 284  mac-bloquee
# SmartScreen names the file: the update's first install opens the preview before, kept under the
# name of the site's button (Brain-in-a-Box_windows-x64-setup.exe) as its user has it; the path "app"
# opens a preview's or a build's, named with its version.
windows-mise-a-jour/01-avant-smartscreen.png 246 95 532 150 win-smartscreen-1
windows-mise-a-jour/02-avant-smartscreen.png 246 95 532 498 win-smartscreen-2
windows/installeur-bienvenue       263 166 497 386  win-installeur
windows/installeur-fin             263 166 497 386  win-installeur-fin
# Bibo, screen by screen (the Mac window). The diagnosis as it opens, folded.
macos/bienvenue                    122  43 780 650  bibo-1-bienvenue
macos/diagnostic                   122  43 780 650  bibo-2-diagnostic
macos/connexion-installation       122  43 780 650  bibo-4-connexion
# Apple's window over Bibo's, installing (its count of captures varies: fix the number).
macos/12-dialogue-install-command-line-developer.png 122 43 780 650 bibo-5-outils-apple
macos/etape-brain                  122  43 780 650  bibo-6-cerveau
macos/connexion                    122  43 780 650  bibo-7-compte
# The end once Claude is connected: the robot connects none on the path "app", only the update's
# fake Claude does (captures-macos-app-mise-a-jour).
macos-mise-a-jour/relance-fini     122  43 780 650  bibo-8-pret
macos/import-choix                 122  43 780 650  bibo-9-memoire
# Bibo's guide (its own window): guide-objectif.webp and guide-etape.webp stay as they are until the
# night films the guide's automatic capture (the robot still pastes its captures): then their lines.
# The same window on Windows and Linux
windows/etape-brain                122  13 780 430  bibo-windows
linux/etape-obsidian               251 111 780 620  bibo-linux
EOF
