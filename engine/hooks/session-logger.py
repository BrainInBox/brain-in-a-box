#!/usr/bin/env python3
"""Stop hook: one line per session, the first time it stops. With --agent codex (Codex's hooks.json),
only for a session _agents.codex_hook() lets through, and the line says "agent": "codex"."""
import hashlib, json, os, re, sys, tempfile, time
from pathlib import Path

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

CODEX = sys.argv[1:] == ["--agent", "codex"]
if CODEX:
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import _agents
        if not _agents.codex_hook("session-logger", payload):
            sys.exit(0)
    except Exception:
        sys.exit(0)

sid = payload.get("session_id") or payload.get("sessionId") or "unknown"
# Once per session: the lock is named after the id, a plain one as is (Claude Code's are UUIDs), any
# other by its hash, so that no id leads out of the lock folder. Created here or not at all, never
# through a link left under its name.
if isinstance(sid, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sid):
    name = sid
else:
    name = "h." + hashlib.sha256(json.dumps(sid).encode("ascii")).hexdigest()[:32]
lock_dir = Path(tempfile.gettempdir()) / "claude-session-locks"
lock_dir.mkdir(parents=True, exist_ok=True)
try:
    fd = os.open(str(lock_dir / f"logger-{name}.lock"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
except FileExistsError:
    sys.exit(0)
with os.fdopen(fd, "w") as f:
    f.write(str(time.time()))

log_dir = Path.home() / ".claude" / "logs"
log_dir.mkdir(parents=True, exist_ok=True)
entry = {
    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "session_id": sid,
    "cwd": payload.get("cwd") or os.getcwd(),
    "transcript_path": payload.get("transcript_path"),
}
if CODEX:
    entry["agent"] = "codex"
with (log_dir / "sessions.log").open("a", encoding="utf-8") as f:
    f.write(json.dumps(entry) + chr(10))
sys.exit(0)
