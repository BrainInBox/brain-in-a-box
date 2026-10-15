#!/usr/bin/env bash
# Rebuilds site/img/*.webp from the night robot's captures (terrain.yml artifacts
# captures-macos-app, captures-windows-app, captures-linux-app). See README.md.
#
#   ./captures.sh <folder holding macos/ windows/ linux/>
#
# Each line: source capture, crop x y width height (in the capture's pixels), output.
# The crops follow the app's window at its usual place on the robot's screens
# (1024x768 on macOS and Windows, 1280x900 on Linux). If a capture moved, fix its line.
set -euo pipefail

src=${1:?usage: ./captures.sh <captures folder>}
out=$(cd "$(dirname "$0")" && pwd)/img
command -v cwebp >/dev/null || { echo "cwebp is missing (brew install webp, apt install webp)" >&2; exit 1; }

while read -r file x y w h name; do
  [[ -z "$file" || "$file" == \#* ]] && continue
  [[ -f "$src/$file" ]] || { echo "missing: $src/$file" >&2; exit 1; }
  cwebp -quiet -near_lossless 60 -z 9 -metadata none -crop "$x" "$y" "$w" "$h" "$src/$file" -o "$out/$name.webp"
  echo "$name.webp  $(wc -c <"$out/$name.webp" | tr -d ' ') bytes"
done <<'EOF'
# Installing the app
macos/01-dmg.png                    52  91 440 150  mac-dmg
macos/02-gatekeeper.png            382 129 260 284  mac-bloquee
windows/01-smartscreen.png         246  95 532 150  win-smartscreen-1
windows/02-smartscreen.png         246  95 532 498  win-smartscreen-2
windows/03-installeur-bienvenue.png 263 166 497 386 win-installeur
windows/07-installeur-fin.png      263 166 497 386  win-installeur-fin
# Bibo, screen by screen (the Mac window)
macos/03-bienvenue.png             122  31 780 674  bibo-1-bienvenue
macos/04-diagnostic.png            122  31 780 674  bibo-2-diagnostic
macos/05-voyage.png                122  31 780 674  bibo-3-voyage
macos/08-connexion-installation.png 122 31 780 674  bibo-4-connexion
macos/13-dialogue-install-command-line-developer.png 122 31 780 580 bibo-5-outils-apple
macos/21-etape-brain.png           122  31 780 580  bibo-6-cerveau
macos/24-connexion.png             122  31 780 674  bibo-7-compte
macos/25-fini.png                  122  31 780 674  bibo-8-pret
# The same window on Windows and Linux
windows/17-etape-brain.png          60  50 780 400  bibo-windows
linux/10-etape-obsidian.png        250  98 780 580  bibo-linux
EOF
