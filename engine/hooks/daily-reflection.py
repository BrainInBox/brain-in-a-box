#!/usr/bin/env python3
import json, sys, os, time, subprocess, glob
from pathlib import Path
from datetime import datetime, timedelta

HOME = Path.home()
BRAIN = HOME / "Documents" / "Brain"
LOGS = HOME / ".claude" / "logs"
PROJECTS = HOME / ".claude" / "projects"

CLAUDE_BIN = "__HOME__/.local/bin/claude"  # templated by install.sh; falls back to PATH
if not Path(CLAUDE_BIN).exists():
    CLAUDE_BIN = "claude"

DAY = time.strftime("%Y-%m-%d")
SLOT = "midday" if int(time.strftime("%H")) < 18 else "evening"
BUDGET = 300000  # hard cap on the transcript block in the prompt


def _local_date(ts):
    """Local YYYY-MM-DD for an ISO timestamp, or None."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
    except Exception:
        return None


def _trim(text, budget):
    """Keep head (2/3) + tail (1/3) on line boundaries — end-of-day matters as
    much as the start of a session."""
    if len(text) <= budget:
        return text
    head, tail = budget * 2 // 3, budget // 3
    a = text[:head].rsplit("\n", 1)[0]
    b = text[-tail:].split("\n", 1)[-1]
    return a + "\n…[trimmed]…\n" + b


def day_session(path, day):
    """(day_text, cwd) for a transcript: JSONL lines whose timestamp falls on
    `day` (local date). Streamed → RAM is O(day), not O(file). Records without a
    timestamp (control metadata) are skipped (not datable)."""
    buf, cwd = [], None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if _local_date(d.get("timestamp") or d.get("ts")) != day:
                    continue
                if d.get("cwd"):
                    cwd = d["cwd"]
                buf.append(line.rstrip("\n"))
    except Exception:
        return ("", None)
    return ("\n".join(buf), cwd)


def discover_sessions(day):
    """Sessions active on `day` by SCANNING transcripts — not the Stop-driven
    index (which misses continuous-work sessions whose Stop lands on another day).
    mtime pre-filter: a transcript active on day J has mtime >= J 00:00."""
    try:
        day_start = datetime.strptime(day, "%Y-%m-%d").astimezone().timestamp()
    except Exception:
        day_start = 0
    out = []
    for tp in glob.glob(str(PROJECTS / "*" / "*.jsonl")):
        try:
            if os.path.getmtime(tp) < day_start:
                continue
        except Exception:
            continue
        text, cwd = day_session(tp, day)
        if text:
            out.append((Path(tp).stem[:8], cwd, text))
    return out


def _git_root(path):
    try:
        r = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return None


def discover_git_roots(cwds):
    """Git repos worked in today, derived from the day's session cwds. If a cwd
    is not itself a repo (e.g. a parent dir holding several repos), scan its
    immediate children for repos. No hardcoded paths."""
    roots = set()
    for c in cwds:
        if not c or not os.path.isdir(c):
            continue
        r = _git_root(c)
        if r:
            roots.add(r)
        else:
            try:
                for name in os.listdir(c):
                    p = os.path.join(c, name)
                    if os.path.isdir(os.path.join(p, ".git")):
                        roots.add(p)
            except Exception:
                pass
    return sorted(roots)


def _git_user(root):
    try:
        r = subprocess.run(["git", "-C", root, "config", "user.name"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None
    except Exception:
        return None


def git_digest(day, cwds):
    """Your own commits (no-merge, deduped by subject) on the repos you worked in
    today. Ground truth for what was actually shipped — chat under-represents big
    days. Window [day 00:00, day+1 00:00) in local time."""
    try:
        nxt = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    except Exception:
        return ""
    out = []
    for root in discover_git_roots(cwds):
        author = _git_user(root)
        args = ["git", "-C", root, "log", "--all", "--no-merges",
                f"--since={day} 00:00", f"--until={nxt} 00:00",
                "--date=format:%H:%M", "--pretty=format:%ad | %s"]
        if author:
            args.insert(5, f"--author={author}")
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=20)
        except Exception:
            continue
        seen, uniq = set(), []
        for l in r.stdout.splitlines():
            if not l.strip():
                continue
            key = l.split("|", 1)[-1].strip()  # dedup squash/rebase: same subject, different SHAs
            if key not in seen:
                seen.add(key)
                uniq.append(l)
        if uniq:
            out.append(f"### {Path(root).name} — {len(uniq)} commit(s)\n" + "\n".join(uniq[:60]))
    return "\n\n".join(out)


def build_prompt(day, slot, sessions, gitlog):
    if sessions:
        per = max(8000, BUDGET // len(sessions))
        parts = [f"[session {sid} · cwd={cwd or '?'}]\n{_trim(text, per)}" for sid, cwd, text in sessions]
        big = ("\n---SESSION---\n".join(parts))[:BUDGET]
    else:
        big = "(no chat sessions today — see the git digest)"
    gitlog = gitlog or "(no commits of yours detected today)"
    return f"""Read these Claude Code session transcripts from {day} ({slot} run). Generate:

1. A journal summary for Journal/{day}.md with sections:
   - What I did today
   - Key decisions
   - Projects I worked on
   - To do tomorrow

2. An update to Profile/memory.md (Recent context section): keep the last 15 days max, add today's salient items.

Write both files. IMPORTANT: if they already exist, READ them first and COMPLETE/MERGE without overwriting existing content (preserve hand-written sections and entries already present). Terse style, no filler. Skip sessions with <3 messages.

IMPORTANT — The git digest below is the AUTHORITATIVE record of what was actually
shipped today (chat under-represents big days). Every repo with commits MUST
appear in "Projects" and "What I did", even if the transcripts barely mention it.
Group by theme (feature, fix, security, docs), not commit-by-commit.

=== GIT DIGEST FOR {day} (ground truth) ===
{gitlog}

=== TRANSCRIPTS (context / intent / decisions) ===
{big}
"""


def main():
    lock = Path(f"/tmp/brain-daily-reflection-{DAY}-{SLOT}.lock")
    if lock.exists() and (time.time() - lock.stat().st_mtime) < 3600:
        return
    lock.write_text(str(time.time()))

    sessions = discover_sessions(DAY)
    cwds = {cwd for _, cwd, _ in sessions if cwd}
    gitlog = git_digest(DAY, cwds)
    if not sessions and not gitlog:
        return

    prompt = build_prompt(DAY, SLOT, sessions, gitlog)
    try:
        subprocess.run(
            [CLAUDE_BIN, "-p", "--permission-mode", "acceptEdits", prompt],
            cwd=str(BRAIN), timeout=600, check=False,
        )
    except Exception as e:
        try:
            (LOGS / "daily-reflection-errors.log").open("a").write(
                f"{time.strftime('%FT%T')} {type(e).__name__}: {e}\n")
        except Exception:
            pass


if __name__ == "__main__":
    main()
