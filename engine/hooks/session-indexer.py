#!/usr/bin/env python3
import json, sys, os, time
from pathlib import Path

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

sid = payload.get("session_id") or payload.get("sessionId") or "unknown"
day = time.strftime("%Y-%m-%d")
lock_dir = Path("/tmp/claude-session-locks")
lock_dir.mkdir(parents=True, exist_ok=True)
# Lock per (sid, day), not per sid alone: a session resumed across days must be
# indexed on EVERY active day. A permanent {sid} lock indexed it only on its
# first Stop, so the daily-reflection of later days never saw it (root cause of
# under-reporting big days). The reflect also scans transcripts directly now, so
# the index is no longer the only source — but keep it complete for other tools.
lock = lock_dir / f"indexer-{sid}-{day}.lock"
if lock.exists():
    sys.exit(0)
lock.write_text(str(time.time()))

log_dir = Path.home() / ".claude" / "logs"
log_dir.mkdir(parents=True, exist_ok=True)
entry = {
    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "session_id": sid,
    "cwd": payload.get("cwd") or os.getcwd(),
    "transcript_path": payload.get("transcript_path"),
}
with (log_dir / f"sessions-{day}.jsonl").open("a") as f:
    f.write(json.dumps(entry) + chr(10))
sys.exit(0)
