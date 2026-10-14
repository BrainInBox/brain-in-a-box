#!/usr/bin/env zsh
# gbrain self-update, kept as a relay. The nightly (step 0), the SessionStart
# catch-up hook and a gbrain-nightly.sh you edited (kept as is by the installer)
# all call this file; the logic lives next to it in gbrain_update.py: newest
# gbrain release at least 7 days old, never master, exact Bun checked against its
# published checksum, full rollback (code, Bun, brain.pglite, config.json).
#
# Usage: gbrain-selfupdate.sh [log-file]   (default: ~/.gbrain/nightly.log)
# Result: ~/.gbrain/last-update.json

export PATH="$HOME/.bun/bin:/opt/homebrew/bin:/usr/bin:/bin"
exec python3 -I "${0:A:h}/gbrain_update.py" --log "${1:-$HOME/.gbrain/nightly.log}"
