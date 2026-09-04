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
for line in index_file.read_text(encoding="utf-8").splitlines():
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
            transcripts.append(Path(tp).read_text(encoding="utf-8", errors="replace")[:50000])
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

claude_bin = shutil.which("claude.cmd") or shutil.which("claude") or os.path.expandvars(r"%APPDATA%\npm\claude.cmd")
if not claude_bin or not Path(claude_bin).exists():
    (LOGS / "daily-reflection-errors.log").open("a", encoding="utf-8").write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} claude introuvable: {claude_bin}" + chr(10))
    sys.exit(0)
# claude -p en automatisation : config MCP VIDE (--strict-mcp-config) sinon il
# lance toute la flotte MCP (Playwright, n8n...) -> hang en headless + zombies (2026-06-22).
no_mcp = Path.home() / ".gbrain" / "no-mcp-config.json"
if not no_mcp.exists():
    no_mcp.parent.mkdir(parents=True, exist_ok=True)
    no_mcp.write_text('{"mcpServers":{}}', encoding="utf-8")
try:
    # Windows: le prompt (jusqu'a 200k chars de transcripts) passe par STDIN,
    # pas par argv -- la ligne de commande Windows plafonne a ~32k chars
    # (WinError 206). cmd /c pour executer le .cmd de facon fiable.
    subprocess.run(
        ["cmd", "/c", claude_bin, "-p",
         "--strict-mcp-config", "--mcp-config", str(no_mcp),
         "--permission-mode", "acceptEdits"],
        input=prompt,
        text=True,
        encoding="utf-8",
        cwd=str(BRAIN),
        timeout=600,
        check=False,
    )
except (OSError, subprocess.SubprocessError) as e:
    (LOGS / "daily-reflection-errors.log").open("a", encoding="utf-8").write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {e}" + chr(10))
sys.exit(0)
