#!/usr/bin/env python3
"""Daily reflection: turns a day of work into Journal/YYYY-MM-DD.md and the
"Recent context" of Profile/memory.md, through a headless `claude -p` run.

Scheduled at 12:00 and 23:00 (launchd / Task Scheduler). By hand:
    daily-reflection.py                    today, slot picked from the hour
    daily-reflection.py --day 2026-01-31   backfill a missed day
    daily-reflection.py --dry-run          show the sources and prompt size, call nothing

Sources, all read-only:
  - Claude Code transcripts (~/.claude/projects/*/*.jsonl), sliced to the day.
  - Hermes sessions (~/.hermes/state.db), when Hermes is installed.
  - A digest of your own git commits that day: the authoritative record of
    what was produced, since the chat under-reports the busiest days.

Environment: BRAIN_DIR (vault, default ~/Documents/Brain), BRAIN_GIT_ROOTS
(extra repos to scan, os.pathsep-separated), HERMES_DB, CLAUDE_BIN,
BRAIN_NO_NOTIFY=1 (no desktop notification on failure).
"""
import argparse, glob, json, os, re, shutil, sqlite3, subprocess, sys, tempfile, time
from datetime import datetime, timedelta
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: the scheduler runs one task at a time anyway
    fcntl = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _redact import redact
except ImportError:
    redact = None

HOME = Path.home()
BRAIN = Path(os.environ.get("BRAIN_DIR") or HOME / "Documents" / "Brain")
MEMORY = BRAIN / "Profile" / "memory.md"
LOGS = HOME / ".claude" / "logs"
STATE = LOGS / "daily-reflection.state.json"
PROJECTS = HOME / ".claude" / "projects"
HERMES_DB = Path(os.environ.get("HERMES_DB") or HOME / ".hermes" / "state.db")

BUDGET = 300_000      # hard cap on the session text sent in the prompt
MSG_CAP = 4_000       # one pasted log must not eat a whole session's share
TIMEOUT = 900
LIMIT_RE = re.compile(r"hit your .*limit|session limit|usage limit", re.I)
STATUS_BEGIN, STATUS_END = "<!-- REFLECTION-STATUS:BEGIN -->", "<!-- REFLECTION-STATUS:END -->"
# Marks our own `claude -p` runs: their transcripts land in ~/.claude/projects
# too, and must not be fed back into the next run.
SENTINEL = "<!-- brain-daily-reflection -->"


def log_error(msg):
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with (LOGS / "daily-reflection-errors.log").open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg[:500]}\n")
    except Exception:
        pass


def _local(ts):
    """Local datetime of an ISO timestamp, or None."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    except Exception:
        return None


def _day_bounds(day):
    """[start, end) of `day` in local time, as epoch seconds."""
    d = datetime.strptime(day, "%Y-%m-%d")
    return d.astimezone().timestamp(), (d + timedelta(days=1)).astimezone().timestamp()


def _trim(text, budget):
    """Keeps the start (2/3) and the end (1/3) on line boundaries: the end of a
    day matters as much as its start."""
    if len(text) <= budget:
        return text
    head, tail = budget * 2 // 3, budget // 3
    return text[:head].rsplit("\n", 1)[0] + "\n…[cut]…\n" + text[-tail:].split("\n", 1)[-1]


def _tool_summary(name, inp):
    inp = inp or {}
    if name == "Bash":
        return (inp.get("command") or "")[:300]
    for key in ("file_path", "path", "pattern", "url", "query"):
        if inp.get(key):
            return str(inp[key])[:300]
    return json.dumps(inp, ensure_ascii=False)[:200]


def _render(d, hhmm):
    """Readable lines for one transcript record. Tool results are dropped: they
    are most of the volume and the git digest says what was produced."""
    if d.get("isMeta"):  # system reminders and command caveats injected into the chat
        return []
    content = (d.get("message") or {}).get("content")
    out = []
    if d.get("type") == "user":
        if isinstance(content, str):
            out.append(f"{hhmm} user: {_trim(content.strip(), MSG_CAP)}")
        elif isinstance(content, list):
            out += [f"{hhmm} user: {_trim(c.get('text', '').strip(), MSG_CAP)}"
                    for c in content if c.get("type") == "text" and c.get("text", "").strip()]
    elif d.get("type") == "assistant" and isinstance(content, list):
        for c in content:
            if c.get("type") == "text" and c.get("text", "").strip():
                out.append(f"{hhmm} assistant: {_trim(c['text'].strip(), MSG_CAP)}")
            elif c.get("type") == "tool_use":
                out.append(f"{hhmm}   → {c.get('name', '?')} {_tool_summary(c.get('name'), c.get('input'))}")
    return out


def day_session(path, day):
    """(text, cwd, n_messages) of one transcript, restricted to the records of
    `day` (local date). Streams the file: memory is O(day), not O(file)."""
    lines, cwd, n = [], None, 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                if SENTINEL in raw:
                    return ("", None, 0)
                try:
                    d = json.loads(raw)
                except Exception:
                    continue
                when = _local(d.get("timestamp"))
                if not when or when.strftime("%Y-%m-%d") != day:
                    continue
                cwd = d.get("cwd") or cwd
                rendered = _render(d, when.strftime("%H:%M"))
                n += sum(1 for l in rendered if " user: " in l or " assistant: " in l)
                lines += rendered
    except OSError:
        return ("", None, 0)
    return ("\n".join(lines), cwd, n)


def claude_sessions(day):
    """Claude Code sessions active on `day`, found by scanning the transcripts
    rather than the Stop-hook index: a session whose Stop fires on another day
    (long-running work) never appeared in that day's index. A transcript active
    on `day` has mtime >= day 00:00, which skips most files unread."""
    day_start, _ = _day_bounds(day)
    out = []
    for tp in glob.glob(str(PROJECTS / "*" / "*.jsonl")):
        try:
            if os.path.getmtime(tp) < day_start:
                continue
        except OSError:
            continue
        text, cwd, n = day_session(tp, day)
        if n >= 3:  # shorter exchanges carry no signal worth a model's attention
            out.append((f"claude {Path(tp).stem[:8]} · cwd={cwd or '?'}", text, cwd))
    return out


def hermes_sessions(day):
    """Hermes sessions of `day`, read-only from its SQLite store. Hermes writes
    nothing to the vault, so without this its work only shows through commits.
    Only user/assistant turns are kept; cron runs (unattended checks) are not."""
    if not HERMES_DB.exists():
        return []
    start, end = _day_bounds(day)
    try:
        con = sqlite3.connect(HERMES_DB.as_uri() + "?mode=ro", uri=True, timeout=10)
        try:
            rows = con.execute(
                """SELECT m.session_id, coalesce(s.title, ''), coalesce(s.source, '?'),
                          m.role, m.timestamp, m.content
                   FROM messages m LEFT JOIN sessions s ON s.id = m.session_id
                   WHERE m.timestamp >= ? AND m.timestamp < ?
                     AND m.role IN ('user', 'assistant')
                     AND coalesce(s.source, '') <> 'cron'
                     AND length(coalesce(m.content, '')) > 0
                   ORDER BY m.session_id, m.timestamp, m.id""",
                (start, end),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        log_error(f"cannot read Hermes sessions: {e}")
        return []
    sessions = {}
    for sid, title, source, role, ts, content in rows:
        label = f"hermes {sid} · {source} · {title or 'untitled'}"
        hhmm = datetime.fromtimestamp(ts).strftime("%H:%M")
        sessions.setdefault(label, []).append(f"{hhmm} {role}: {_trim(content.strip(), MSG_CAP)}")
    return [(label, "\n".join(lines), None) for label, lines in sessions.items()]


def _git(*args, cwd=None):
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def git_roots(cwds):
    """Repos to scan: those the day's sessions ran in, the vault, BRAIN_GIT_ROOTS.
    A session started from a folder holding several repos (a plain folder, or a
    repo with independent repos nested in it) contributes the repos directly
    under it too, or the work done from that folder would be missed."""
    roots = set()
    candidates = {c for c in cwds if c} | {str(BRAIN)}
    candidates |= {p for p in os.environ.get("BRAIN_GIT_ROOTS", "").split(os.pathsep) if p}
    for c in candidates:
        if not os.path.isdir(c):
            continue
        top = _git("rev-parse", "--show-toplevel", cwd=c)
        if top:
            roots.add(top)
        for sub in glob.glob(os.path.join(c, "*", ".git")):
            roots.add(os.path.dirname(sub))
    return sorted(roots)


def git_digest(day, roots):
    """Your own non-merge commits of `day` (local time), deduplicated by subject
    (a squash or rebase repeats a subject under a new SHA). "Your own" = the
    global git identity; with none configured, every author is kept."""
    nxt = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    authors = [a for a in (_git("config", "--global", "user.email"), _git("config", "--global", "user.name")) if a]
    out = []
    for root in roots:
        log = _git("-C", root, "log", "--all", "--no-merges", "--fixed-strings", *[f"--author={a}" for a in authors],
                   f"--since={day} 00:00", f"--until={nxt} 00:00",
                   "--date=format:%H:%M", "--pretty=format:%ad | %s")
        seen, uniq = set(), []
        for l in log.splitlines():
            key = l.split("|", 1)[-1].strip()
            if l.strip() and key not in seen:
                seen.add(key)
                uniq.append(l)
        if uniq:
            out.append(f"### {Path(root).name} — {len(uniq)} commit(s)\n" + "\n".join(uniq[:60]))
    return "\n\n".join(out)


def _allocate(lengths, budget, floor=3000):
    """Per-session share of the budget: short sessions keep everything, the rest
    is split evenly among the long ones. A flat budget/n share wasted what the
    short ones did not use and cut the long ones short."""
    alloc, remaining = {}, budget
    left = sorted(range(len(lengths)), key=lambda i: lengths[i])
    while left:
        share = max(floor, remaining // len(left))
        if lengths[left[0]] > share:
            alloc.update({i: share for i in left})
            break
        i = left.pop(0)
        alloc[i] = lengths[i]
        remaining -= lengths[i]
    return alloc


def build_prompt(day, slot, sessions, gitlog):
    if sessions:
        alloc = _allocate([len(text) for _, text in sessions], BUDGET)
        parts = [f"[session {label}]\n{_trim(text, alloc[i])}" for i, (label, text) in enumerate(sessions)]
        body = "\n---SESSION---\n".join(parts)[:BUDGET]
    else:
        body = "(no chat session that day — see the git digest)"
    sources = "Claude Code sessions (label \"claude\")"
    if any(label.startswith("hermes") for label, _ in sessions):
        sources += " and Hermes sessions (label \"hermes\")"
    backfill = ""
    if slot == "backfill":
        backfill = (f"\n- This is a BACKFILL run: the scheduled runs for {day} did not produce it. In memory.md, "
                    f"put the {day} entry at its chronological place (not necessarily first) and merge it "
                    f"with any existing {day} entry.")
    return f"""{SENTINEL}
Below is the activity of {day} ({slot} run): {sources}, and a digest of the git commits you made that day.

Update two files in this vault:

1. Journal/{day}.md, with sections:
   - What I did
   - Key decisions
   - Projects I worked on
   - To do next
2. Profile/memory.md, its "Recent context" section: add the day's salient items, keep the last 15 days at most.

Rules:
- If a file exists, READ it first and merge into it: keep hand-written sections and existing entries, never overwrite them.
- Do not touch the block between {STATUS_BEGIN} and {STATUS_END}.
- Write in the vault's language (see Profile/soul.md), terse, no filler.
- The git digest is the authoritative record of what was actually produced: every repo in it must appear under "Projects I worked on" and "What I did", even if the chat barely mentions it. Group by theme (feature, fix, security, docs), not one line per commit.{backfill}

=== GIT DIGEST {day} ===
{gitlog or "(no commit of yours found that day)"}

=== SESSIONS ===
{body}
"""


def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(st):
    try:
        STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def set_status_block(text):
    """Puts (or removes, when text is empty) a warning at the top of memory.md.
    Agents read that file as the state of the world: when the reflection is
    failing, they must see the file is stale instead of trusting it."""
    try:
        src = MEMORY.read_text(encoding="utf-8")
    except OSError:
        return
    new = re.sub(re.escape(STATUS_BEGIN) + r".*?" + re.escape(STATUS_END) + r"\n*", "", src, flags=re.S)
    if text:
        block = f"{STATUS_BEGIN}\n{text}\n{STATUS_END}\n\n"
        # After the lint block if there is one, else after the frontmatter and
        # the title (never inside a YAML frontmatter: it would corrupt it).
        m = re.search(r"<!-- LINT:END -->\n+", new) or re.search(r"\A(?:---\n.*?\n---\n)?\s*(?:# [^\n]*\n)?\n*", new, re.S)
        new = new[:m.end()] + block + new[m.end():]
    if new != src:
        tmp = MEMORY.with_name(MEMORY.name + ".reflection-tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, MEMORY)


def notify(msg):
    if os.environ.get("BRAIN_NO_NOTIFY") or sys.platform != "darwin":
        return
    # The message goes in argv: nothing is interpolated into the AppleScript source.
    try:
        subprocess.run(["osascript", "-e", "on run argv", "-e",
                        'display notification (item 1 of argv) with title "Brain: daily reflection failed"',
                        "-e", "end run", msg], capture_output=True, timeout=10)
    except Exception:
        pass


def claude_bin():
    local = HOME / ".local" / "bin" / "claude"
    return os.environ.get("CLAUDE_BIN") or (str(local) if local.exists() else None) or shutil.which("claude") or str(local)


def run_claude(prompt, day, slot):
    """Runs the synthesis, 3 attempts. Returns None on success, else the cause."""
    # launchd starts with a bare PATH: claude's own tools (node, bun) must be reachable.
    extra = [str(HOME / ".local" / "bin"), str(HOME / ".bun" / "bin"), "/opt/homebrew/bin", "/usr/local/bin"]
    path = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]  # an empty entry means "."
    env = {**os.environ, "PATH": os.pathsep.join(path + extra)}
    reason = None
    for attempt in range(3):
        try:
            # The prompt goes through stdin: up to 300 KB does not fit in one
            # argument on Linux (128 KB) nor in a Windows command line (32 KB).
            r = subprocess.run([claude_bin(), "-p", "--permission-mode", "acceptEdits"], input=prompt,
                               cwd=str(BRAIN), timeout=TIMEOUT, capture_output=True, text=True, env=env)
            sys.stdout.write(r.stdout)  # the scheduler keeps them in its own logs
            sys.stderr.write(r.stderr)
            if r.returncode == 0:
                return None
            last = (r.stdout.strip() or r.stderr.strip() or "?").splitlines()[-1]
            reason = f"exit {r.returncode}: {last[:200]}"
        except subprocess.TimeoutExpired:
            reason = f"timeout {TIMEOUT}s"
        except Exception as e:
            reason = f"{type(e).__name__}: {str(e)[:200]}"
        log_error(f"{day} {slot} attempt {attempt + 1}/3: {reason}")
        if LIMIT_RE.search(reason):
            break  # usage limit: retrying in a minute cannot help
        if attempt < 2:
            time.sleep(60)
    return reason


def parse_args():
    p = argparse.ArgumentParser(description="Daily reflection: Journal + memory.md")
    p.add_argument("--day", default=time.strftime("%Y-%m-%d"), help="YYYY-MM-DD, default: today")
    p.add_argument("--slot", choices=["midday", "evening", "backfill"])
    p.add_argument("--dry-run", action="store_true", help="show sources and prompt size, call nothing")
    a = p.parse_args()
    datetime.strptime(a.day, "%Y-%m-%d")
    if not a.slot:
        if a.day != time.strftime("%Y-%m-%d"):
            a.slot = "backfill"
        else:
            a.slot = "midday" if int(time.strftime("%H")) < 18 else "evening"
    return a


def main():
    args = parse_args()
    day, slot = args.day, args.slot
    if redact is None:
        # Never ship raw transcripts to a model: _redact.py must sit next to this hook.
        log_error("_redact.py not found next to daily-reflection.py — run skipped")
        return

    found = hermes_sessions(day) + claude_sessions(day)
    gitlog = git_digest(day, git_roots([cwd for _, _, cwd in found]))
    masked, n_masked = [], 0
    for label, text, _ in found:
        text, n = redact(text)
        masked.append((label, text))
        n_masked += n
    gitlog = redact(gitlog)[0]

    if args.dry_run:
        prompt = build_prompt(day, slot, masked, gitlog)
        n_h = sum(1 for label, _ in masked if label.startswith("hermes"))
        print(f"{day} {slot}: {len(masked) - n_h} Claude session(s), {n_h} Hermes session(s), "
              f"{gitlog.count('### ')} repo(s) with commits, {n_masked} secret(s) masked, prompt {len(prompt)} chars")
        for label, text in masked:
            print(f"  {label} ({len(text)} chars)")
        return
    if not masked and not gitlog:
        return

    lock = Path(tempfile.gettempdir()) / f"brain-daily-reflection-{day}-{slot}.lock"
    if lock.exists() and (time.time() - lock.stat().st_mtime) < 3600:
        return
    lock.write_text(str(time.time()))

    LOGS.mkdir(parents=True, exist_ok=True)
    # One run at a time: two `claude -p` editing memory.md together lose writes
    # (a manual backfill during the 12:00 run, for instance).
    with open(LOGS / "daily-reflection.run.lock", "w") as run_lock:
        if fcntl:
            fcntl.flock(run_lock, fcntl.LOCK_EX)
        reason = run_claude(build_prompt(day, slot, masked, gitlog), day, slot)

        st = load_state()
        if reason is None:
            st.update(last_ok=f"{day} {slot}", failing_since=None, last_error=None)
            set_status_block(None)
        else:
            st["failing_since"] = st.get("failing_since") or time.strftime("%Y-%m-%d %H:%M")
            st["last_error"] = reason
            set_status_block(
                f"> **⚠️ Daily reflection failing** since {st['failing_since']} · last good run: "
                f"{st.get('last_ok') or 'unknown'} · cause: {reason}. Journal and memory.md do not cover "
                f"the period since. Backfill: `python3 ~/.claude/hooks/brain/daily-reflection.py --day YYYY-MM-DD`."
            )
            notify(f"{day} {slot}: {reason}")
        save_state(st)


if __name__ == "__main__":
    main()
