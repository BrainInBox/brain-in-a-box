#!/usr/bin/env python3
import json, sys, os, time
from pathlib import Path

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

sid = payload.get("session_id") or payload.get("sessionId") or "unknown"
lock_dir = Path("/tmp/claude-session-locks")
lock_dir.mkdir(parents=True, exist_ok=True)
lock = lock_dir / f"indexer-{sid}.lock"
if lock.exists():
    sys.exit(0)
lock.write_text(str(time.time()))

log_dir = Path.home() / ".claude" / "logs"
log_dir.mkdir(parents=True, exist_ok=True)
day = time.strftime("%Y-%m-%d")
entry = {
    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "session_id": sid,
    "cwd": payload.get("cwd") or os.getcwd(),
    "transcript_path": payload.get("transcript_path"),
}
with (log_dir / f"sessions-{day}.jsonl").open("a") as f:
    f.write(json.dumps(entry) + chr(10))
sys.exit(0)
