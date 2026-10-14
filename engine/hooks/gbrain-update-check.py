#!/usr/bin/env python3
"""SessionStart catch-up for the 7-day gbrain update (macOS and Windows).

The nightly runs gbrain_update.py at 04:00. A computer that is off or asleep at
that time, night after night, would never check: when the last successful check
(derniere_verif_ok in ~/.gbrain/last-update.json, or $GBRAIN_HOME/.gbrain) is
more than 36 h old, this starts the gbrain_update.py next to it in the background,
detached from the session, and returns at once. A check that keeps failing
(offline) is retried at most once an hour. Nothing happens without a gbrain clone
in ~/DEV/gbrain.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

STALE = dt.timedelta(hours=36)
RETRY_S = 3600


def last_check(data):
    try:
        with open(str(data / "last-update.json"), encoding="utf-8") as f:
            text = json.load(f).get("derniere_verif_ok")
        return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def main():
    try:
        sys.stdin.read()
    except Exception:
        pass
    home = Path.home()
    data = Path(os.environ.get("GBRAIN_HOME", "").strip() or str(home)) / ".gbrain"
    script = Path(__file__).resolve().parent / "gbrain_update.py"
    if not (home / "DEV" / "gbrain" / ".git").exists() or not script.is_file() or not sys.executable:
        return
    now = dt.datetime.now(dt.timezone.utc)
    seen = last_check(data)
    # A date in the future (clock changed) would silence the catch-up forever.
    if seen is not None and seen <= now + dt.timedelta(hours=1) and now - seen < STALE:
        return
    stamp = data / "last-selfupdate-check"
    try:
        if time.time() - stamp.stat().st_mtime < RETRY_S:
            return
    except OSError:
        pass
    data.mkdir(parents=True, exist_ok=True)
    stamp.write_text(now.strftime("%Y-%m-%dT%H:%M:%SZ") + "\n", encoding="utf-8")
    kw = {"cwd": str(home), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
          "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
                               | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kw["start_new_session"] = True
    subprocess.Popen([sys.executable, "-I", str(script)], **kw)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
