#!/usr/bin/env python3
import json, sys, os, time, subprocess, tempfile, shutil
from pathlib import Path

BRAIN = Path.home() / "Documents" / "Brain"
LOGS = Path.home() / ".claude" / "logs"
DAY = time.strftime("%Y-%m-%d")
slot = "midday" if int(time.strftime("%H")) < 18 else "evening"

lock = Path(tempfile.gettempdir()) / f"brain-daily-reflection-{DAY}-{slot}.lock"
if lock.exists() and (time.time() - lock.stat().st_mtime) < 3600:
    sys.exit(0)
lock.write_text(str(time.time()))

index_file = LOGS / f"sessions-{DAY}.jsonl"
if not index_file.exists():
    sys.exit(0)

sessions = []
for line in index_file.read_text().splitlines():
    try:
        sessions.append(json.loads(line))
    except Exception:
        pass
if not sessions:
    sys.exit(0)

transcripts = []
for s in sessions:
    tp = s.get("transcript_path")
    if tp and Path(tp).exists():
        try:
            transcripts.append(Path(tp).read_text()[:50000])
        except Exception:
            pass

if not transcripts:
    sys.exit(0)

big = (chr(10) + "---SESSION---" + chr(10)).join(transcripts)[:200000]
prompt = f"""Read these Claude Code session transcripts from {DAY} ({slot} run). Generate:

1. A journal summary for Journal/{DAY}.md with sections:
   - What I did today
   - Key decisions
   - Projects I worked on
   - To do tomorrow

2. An update to Profile/memory.md (Recent context section): keep the last 15 days max, add today's salient items.

Write both files directly. Terse style, no filler. Skip sessions with <3 messages.

Transcripts:
{big}
"""

home_claude = Path.home() / ".local" / "bin" / "claude"
claude_bin = (
    os.environ.get("CLAUDE_BIN")
    or (str(home_claude) if home_claude.exists() else None)
    or shutil.which("claude")
    or str(home_claude)
)
# Transient API failures ("Connection closed mid-response") kill headless
# claude runs often enough that an unretried cron silently loses whole days
# of journal. 3 attempts, 60s apart; a non-zero exit counts as a failure.
for attempt in range(3):
    try:
        r = subprocess.run(
            [claude_bin, "-p", "--permission-mode", "acceptEdits", prompt],
            cwd=str(BRAIN),
            timeout=600,
            check=False,
        )
        if r.returncode == 0:
            break
        err = f"exit {r.returncode}"
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    (LOGS / "daily-reflection-errors.log").open("a").write(
        f"{time.strftime(chr(37)+chr(70)+chr(84)+chr(37)+chr(84))} attempt {attempt + 1}/3 failed: {err}" + chr(10))
    if attempt < 2:
        time.sleep(60)
sys.exit(0)
