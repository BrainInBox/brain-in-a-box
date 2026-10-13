#!/usr/bin/env bash
# Look at an HTML file. macOS, Linux and Windows (Git Bash).
#
#   bash preview.sh shot <file.html> [width] [height]
#       Renders the page in a headless Chrome, Edge, Chromium or Brave and
#       prints the path of a PNG screenshot (default 1280x2000). Read the PNG
#       to see the page. Width 500 is the narrowest these browsers render, so
#       use it for a narrow-screen check; a smaller width is raised to 500.
#       On a Mac with none of them, falls back to Quick Look (built in): top
#       of the page only, and JavaScript does not run.
#   bash preview.sh open <file.html>
#       Opens the page in the default browser, for the user.
#
# To pick the browser yourself, set HTML_ARTIFACT_BROWSER to its full path.
set -euo pipefail

die() { echo "preview: $*" >&2; exit 1; }

[ $# -ge 2 ] || die "usage: preview.sh shot|open <file.html> [width] [height]"
mode=$1
file=$2
width=${3:-1280}
height=${4:-2000}

windows=false
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) windows=true ;; esac

# Accept C:\... paths too under Git Bash.
$windows && file=$(cygpath -u "$file")
[ -f "$file" ] || die "no such file: $file"
file="$(cd "$(dirname "$file")" && pwd)/$(basename "$file")"

# The path a native program (browser, Read tool) understands: C:\... on Windows.
native() { if $windows; then cygpath -w "$1"; else printf '%s\n' "$1"; fi; }

find_browser() {
  local c root
  if [ -n "${HTML_ARTIFACT_BROWSER:-}" ]; then
    printf '%s\n' "$HTML_ARTIFACT_BROWSER"
    return
  fi
  if $windows; then
    local pf pf86 local_app
    pf=$(cygpath -F 38 2>/dev/null || echo "/c/Program Files")
    pf86=$(cygpath -F 42 2>/dev/null || echo "/c/Program Files (x86)")
    local_app=$(cygpath -F 28 2>/dev/null || echo "$HOME/AppData/Local")
    for c in "$pf86/Microsoft/Edge/Application/msedge.exe" \
             "$pf/Microsoft/Edge/Application/msedge.exe" \
             "$pf/Google/Chrome/Application/chrome.exe" \
             "$pf86/Google/Chrome/Application/chrome.exe" \
             "$local_app/Google/Chrome/Application/chrome.exe"; do
      [ -f "$c" ] && { printf '%s\n' "$c"; return; }
    done
  elif [ "$(uname -s)" = Darwin ]; then
    for c in "Google Chrome" "Microsoft Edge" "Chromium" "Brave Browser"; do
      for root in /Applications "$HOME/Applications"; do
        [ -x "$root/$c.app/Contents/MacOS/$c" ] && { printf '%s\n' "$root/$c.app/Contents/MacOS/$c"; return; }
      done
    done
  else
    for c in google-chrome google-chrome-stable chromium chromium-browser microsoft-edge brave-browser; do
      command -v "$c" >/dev/null 2>&1 && { command -v "$c"; return; }
    done
  fi
  return 1
}

case "$mode" in
  open)
    if $windows; then
      p=$(native "$file")
      cmd //c start "" "${p//&/^&}"
    elif [ "$(uname -s)" = Darwin ]; then
      open "$file"
    else
      xdg-open "$file" >/dev/null 2>&1 &
    fi
    echo "Opened $(native "$file")"
    ;;

  shot)
    case "$width$height" in *[!0-9]*) die "width and height must be numbers" ;; esac
    # Headless Chrome and Edge never lay a page out narrower than 500px: a smaller
    # width only crops a 500px page, which looks broken when it is not.
    if [ "$width" -lt 500 ]; then
      echo "preview: width raised to 500, the narrowest the browser renders" >&2
      width=500
    fi

    tmp="${TMPDIR:-/tmp}"
    tmp=${tmp%/}
    shots="$tmp/html-artifact-shots"
    mkdir -p "$shots"
    name=$(basename "$file")
    png="$shots/${name%.*}-${width}x${height}.png"
    rm -f "$png"

    if ! bin=$(find_browser); then
      # Many Macs only have Safari, which has no headless mode. Quick Look renders
      # the page with the same engine, without JavaScript, as a square image.
      if [ "$(uname -s)" = Darwin ] && command -v qlmanage >/dev/null 2>&1; then
        rm -f "$shots/$name.png"
        qlmanage -t -s "$width" -o "$shots" "$file" >/dev/null 2>&1 || true
        if [ -s "$shots/$name.png" ]; then
          mv -f "$shots/$name.png" "$png"
          echo "preview: no Chrome or Edge, used Quick Look (top of the page only, no JavaScript)" >&2
          native "$png"
          exit 0
        fi
      fi
      die "no Chrome, Edge, Chromium or Brave found. Windows has Edge built in; on a Mac, Chrome is free at google.com/chrome."
    fi
    # A throwaway profile, so a browser the user already has open is left alone.
    profile=$(mktemp -d "$tmp/html-artifact-profile.XXXXXX")
    trap 'rm -rf "$profile"' EXIT

    if $windows; then url="file:///$(cygpath -m "$file")"; else url="file://$file"; fi
    url=${url//\%/%25}; url=${url// /%20}; url=${url//\#/%23}; url=${url//\?/%3F}

    # MSYS_NO_PATHCONV: Git Bash must not rewrite the Windows paths we pass.
    MSYS_NO_PATHCONV=1 "$bin" --headless=new --disable-gpu --hide-scrollbars \
      --no-first-run --no-default-browser-check \
      --user-data-dir="$(native "$profile")" \
      --window-size="$width,$height" --virtual-time-budget=3000 \
      --screenshot="$(native "$png")" "$url" >/dev/null 2>&1 &
    pid=$!
    for _ in $(seq 1 60); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true

    [ -s "$png" ] || die "the browser did not produce a screenshot ($bin)"
    native "$png"
    ;;

  *) die "unknown mode '$mode' (use shot or open)" ;;
esac
