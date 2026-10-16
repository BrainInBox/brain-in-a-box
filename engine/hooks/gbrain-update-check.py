#!/usr/bin/env python3
"""SessionStart catch-up for the 7-day gbrain update (macOS and Windows).

The nightly runs gbrain_update.py at 04:00. A computer that is off or asleep at
that time, night after night, would never check: when the last successful check
(derniere_verif_ok in ~/.gbrain/last-update.json, or $GBRAIN_HOME/.gbrain) is
more than 36 h old, this starts the gbrain_update.py next to it in the background,
detached from the session, and returns at once. A check that keeps failing
(offline) is retried at most once an hour. Nothing happens without a gbrain clone
in ~/DEV/gbrain, nor beside a gbrain-selfupdate.sh you changed and kept (the
installer leaves it in place): its own logic runs at night, and two updaters must
never work on the same clone. With --agent codex (Codex's hooks.json), only in a
session _agents.codex_hook() lets through.
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


def own_updater(hooks):
    """Whether the gbrain-selfupdate.sh beside this hook is not the kit's relay."""
    try:
        with open(str(hooks / "gbrain-selfupdate.sh"), encoding="utf-8", errors="replace") as f:
            return "gbrain_update.py" not in f.read()
    except FileNotFoundError:  # Windows: the nightly calls gbrain_update.py itself
        return False
    except OSError:
        return True


def main():
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    if sys.argv[1:] == ["--agent", "codex"]:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import _agents
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = None
        if not _agents.codex_hook("gbrain-update-check", payload):
            return
    home = Path.home()
    data = Path(os.environ.get("GBRAIN_HOME", "").strip() or str(home)) / ".gbrain"
    script = Path(__file__).resolve().parent / "gbrain_update.py"
    if not (home / "DEV" / "gbrain" / ".git").exists() or not script.is_file() or not sys.executable:
        return
    if own_updater(script.parent):
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
    cmd = [sys.executable, "-I", str(script)]
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        try:  # out of the session's job object: closing Claude Code must not kill an update halfway
            subprocess.Popen(cmd, creationflags=flags | 0x01000000, **kw)  # CREATE_BREAKAWAY_FROM_JOB
            return
        except OSError:  # a job that forbids breaking away; gbrain_update.py rolls back a cut run
            kw["creationflags"] = flags
    else:
        kw["start_new_session"] = True
    subprocess.Popen(cmd, **kw)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
