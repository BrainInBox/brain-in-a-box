#!/usr/bin/env python3
# SessionStart catch-up: the nightly job self-updates gbrain at 04:00, but a
# machine asleep/off at that time only catches up on next launchd wake — this
# closes that gap by checking once per calendar day, on session start. Never
# blocks the session: the actual pull/install/migrate/smoke-test runs in
# gbrain-selfupdate.sh, dispatched in the background.
import subprocess, sys, time
from pathlib import Path

try:
    sys.stdin.read()
except Exception:
    pass

STAMP = Path.home() / ".gbrain" / "last-selfupdate-check"
today = time.strftime("%Y-%m-%d")
if STAMP.exists() and STAMP.read_text().strip() == today:
    sys.exit(0)

SCRIPT = Path.home() / ".claude" / "hooks" / "brain" / "gbrain-selfupdate.sh"
if not SCRIPT.exists():
    sys.exit(0)

STAMP.parent.mkdir(parents=True, exist_ok=True)
STAMP.write_text(today)

subprocess.Popen(
    [str(SCRIPT)],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    stdin=subprocess.DEVNULL,
    start_new_session=True,
)
sys.exit(0)
