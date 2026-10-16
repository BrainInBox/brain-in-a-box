#!/usr/bin/env python3
"""
Stop hook: extracts lightweight stats from a Claude session and appends them to
~/Documents/Brain/Journal/YYYY-MM-DD.md under the "📊 Claude Sessions" section.

Idempotent (replaces the session's earlier block). No LLM call.

With --agent codex (Codex's hooks.json), the session is Codex's: its rollout is read by
_codex_rollout.py, only for a session _agents.codex_hook() lets through (silent otherwise), and
its block, in the same section, names Codex and holds the whole session id: Codex's are UUIDv7,
whose first 8 characters are the minute, so two sessions of one minute would share a block.
Claude's blocks are as before.
"""
import json, sys, os, re, time, tempfile
from pathlib import Path
from collections import Counter
from datetime import datetime

BRAIN = Path.home() / "Documents" / "Brain"
JOURNAL_DIR = BRAIN / "Journal"
LOCK_DIR = Path(tempfile.gettempdir()) / "claude-session-locks"
SECTION = "## 📊 Claude Sessions"
# Claude Code's session ids are UUIDs. The id names the lock file and goes in the journal:
# anything else is refused.
SID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")

# The first prompt, the folder and the file names land in the journal, which Obsidian
# renders and the nightly pushes. They can come from a pasted page or be picked by the
# model: _vault_write.py makes each one line of plain text, secrets masked, as it does for
# the reflection. Without it (or _redact.py) nothing is written.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _vault_write import clean_item, write_atomic
except ImportError:
    clean_item = write_atomic = None


SIGNAL_PATTERNS = [
    # SSH / infra
    ("ssh",            re.compile(r"\bssh\s+\S")),
    # GitHub
    ("gh pr create",   re.compile(r"\bgh pr create\b")),
    ("gh pr merge",    re.compile(r"\bgh pr merge\b")),
    ("gh run",         re.compile(r"\bgh run\b")),
    ("gh workflow",    re.compile(r"\bgh workflow\b")),
    # Cloud / IaC
    ("terraform",      re.compile(r"\bterraform\b")),
    ("aws cli",        re.compile(r"\baws\s+(s3|ec2|ecs|rds|logs|iam|ssm)\b")),
    ("kubectl",        re.compile(r"\bkubectl\b")),
    ("vercel",         re.compile(r"\bvercel\b")),
    # DB
    ("psql",           re.compile(r"\bpsql\b|postgresql@1[56]/bin/psql")),
    ("mysql",          re.compile(r"\bmysql\b")),
    ("redis",          re.compile(r"\bredis-cli\b")),
    ("mongo",          re.compile(r"\bmongosh\b|\bmongo\b")),
    (".sql",           re.compile(r"\.sql\b")),
    # Build / runtime
    ("npm/yarn build", re.compile(r"\b(npm|yarn|pnpm) (run )?(build|deploy)\b")),
    ("bun",            re.compile(r"\bbun (run|install|test|dev)\b")),
    ("docker",         re.compile(r"\bdocker (ps|build|run|compose|exec)\b")),
    ("podman",         re.compile(r"\bpodman\s+\w+")),
    # Pentest / security
    ("nmap",           re.compile(r"\bnmap\b")),
    ("gobuster",       re.compile(r"\bgobuster\b")),
    ("hashcat",        re.compile(r"\bhashcat\b")),
    ("sqlmap",         re.compile(r"\bsqlmap\b")),
    ("hydra",          re.compile(r"\bhydra\s+-")),
    ("burp",           re.compile(r"\bburp(suite)?\b")),
    ("gitleaks",       re.compile(r"\bgitleaks\b")),
    # API calls
    ("curl",           re.compile(r"\bcurl\s+[^\n]*https?://")),
    # Secrets
    ("keychain",       re.compile(r"security find-generic-password")),
]


def parse_transcript(path):
    """Returns dict with stats from the JSONL transcript."""
    stats = {
        "tools": Counter(),
        "files": set(),
        "first_prompt": None,
        "first_ts": None,
        "last_ts": None,
        "model": None,
        "signals": Counter(),
        "bash_commands": [],
    }
    if not path or not Path(path).exists():
        return stats

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue

            ts = d.get("timestamp") or d.get("ts")
            if ts:
                if stats["first_ts"] is None:
                    stats["first_ts"] = ts
                stats["last_ts"] = ts

            # First user prompt
            if d.get("type") == "user" and stats["first_prompt"] is None:
                msg = d.get("message", {})
                content = msg.get("content", "")
                if isinstance(content, list):
                    for c in content:
                        if c.get("type") == "text":
                            stats["first_prompt"] = c.get("text", "")
                            break
                elif isinstance(content, str):
                    stats["first_prompt"] = content

            # Model name
            if d.get("type") == "assistant" and not stats["model"]:
                msg = d.get("message", {})
                stats["model"] = msg.get("model", "")[:30]

            # Tool uses
            if d.get("type") == "assistant":
                msg = d.get("message", {})
                content = msg.get("content", [])
                if isinstance(content, list):
                    for c in content:
                        if c.get("type") == "tool_use":
                            name = c.get("name", "?")
                            stats["tools"][name] += 1
                            inp = c.get("input", {}) or {}
                            # File paths
                            fp = inp.get("file_path")
                            if fp:
                                stats["files"].add(fp)
                            # Bash command signal detection
                            if name == "Bash":
                                cmd = inp.get("command", "")
                                stats["bash_commands"].append(cmd[:200])
                                for label, pat in SIGNAL_PATTERNS:
                                    if pat.search(cmd):
                                        stats["signals"][label] += 1
    return stats


def fmt_duration(first_ts, last_ts):
    """Returns (display_str, is_resumed_bool). Si > 24h → reprise."""
    if not first_ts or not last_ts:
        return ("?min", False)
    try:
        a = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
        b = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
        secs = int((b - a).total_seconds())
        if secs < 0:
            return (f"?", False)
        if secs >= 86400:
            days = secs // 86400
            hours = (secs % 86400) // 3600
            return (f"reprise/{days}j{hours}h", True)
        if secs < 60:
            return (f"{secs}s", False)
        m = secs // 60
        if m < 60:
            return (f"{m}min", False)
        return (f"{m//60}h{m%60:02d}", False)
    except Exception:
        return ("?min", False)


def fmt_time(ts):
    if not ts:
        return "??:??"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%H:%M")
    except Exception:
        return "??:??"


def short_cwd(cwd):
    if not cwd:
        return "?"
    # Normalize: drop trailing slash, expand home
    cwd = cwd.rstrip("/").replace(str(Path.home()), "~")
    if "/Documents/Brain" in cwd:
        return "Brain"
    if cwd.startswith("~/DEV/"):
        return cwd.replace("~/DEV/", "")
    return cwd


def short_files(files):
    """Shows up to 3 basenames + count of remaining."""
    bases = [clean_item(Path(f).name, 80) for f in sorted(files)]
    if len(bases) <= 3:
        return ", ".join(bases) if bases else "—"
    return ", ".join(bases[:3]) + f" (+{len(bases)-3})"


def parse_rollout(path):
    """parse_transcript's stats, from a Codex session (_codex_rollout.py). A session the kit
    asked itself comes without items: no tool, so no block."""
    import _codex_rollout as rollout   # here: Claude's recap never loads it
    stats = parse_transcript(None)
    s = rollout.read(path) if isinstance(path, str) and path else None
    if s is None:
        return stats
    stats.update(first_ts=s.first_ts, last_ts=s.last_ts, model=s.model)
    for it in s.items:
        if it.kind == "user" and stats["first_prompt"] is None:
            stats["first_prompt"] = it.text
        elif it.kind == "tool":
            stats["tools"][it.name] += 1
            stats["files"].update(it.files)
            if it.command:
                stats["bash_commands"].append(it.command[:200])
                for label, pat in SIGNAL_PATTERNS:
                    if pat.search(it.command):
                        stats["signals"][label] += 1
    return stats


def shown_id(session_id, agent):
    """The id a block shows and is found again by: Claude's first 8 characters, as before; Codex's whole."""
    return session_id if agent == "codex" else session_id[:8]


def render_entry(stats, session_id, cwd, agent="claude"):
    end = fmt_time(stats["last_ts"])
    dur_str, resumed = fmt_duration(stats["first_ts"], stats["last_ts"])
    header_time = (f"~{end}" if resumed else f"{fmt_time(stats['first_ts'])}–{end}")
    # In a code span: no leading "=" or "$=" either, Dataview would run it as a query.
    cwd_s = clean_item(short_cwd(cwd), 200).lstrip("=$ ") or "?"
    model = clean_item((stats["model"] or "").replace("claude-", ""), 30)
    if agent == "codex":
        model = f"codex {model}".rstrip()
    top_tools = ", ".join(f"{clean_item(n, 60)} {c}" for n, c in stats["tools"].most_common(5))
    files_s = short_files(stats["files"])
    prompt = clean_item(stats["first_prompt"], 140)
    signals = ", ".join(f"{s}×{n}" for s, n in stats["signals"].most_common(5))

    lines = [
        f"- **{header_time}** ({dur_str}) `{cwd_s}` {model} · sid:`{shown_id(session_id, agent)}`",
        f"  - tools: {top_tools or '—'}",
    ]
    if prompt:
        lines.append(f"  - intent: {prompt}")
    if files_s != "—":
        lines.append(f"  - files: {files_s}")
    if signals:
        lines.append(f"  - infra/cli: {signals}")
    return "\n".join(lines)


def _read(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def upsert_session(journal, session_id, entry, agent="claude"):
    """Writes this session's block into the day's journal, in place of its earlier one. True if changed.
    Atomically, and re-read just before: a write made meanwhile (another session's recap, the
    reflection, which writes the same file) is merged, not lost."""
    if journal.is_symlink():
        raise RuntimeError(f"{journal.name} is a link: left as it is")  # as the reflection does
    # Match existing block: header line + nested "  - ..." continuation lines
    pattern = re.compile(
        rf"^- \*\*[^\n]+sid:`{re.escape(shown_id(session_id, agent))}`[^\n]*\n(?:  - [^\n]+\n?)*",
        re.MULTILINE,
    )
    for _ in range(3):
        raw = _read(journal)
        txt = raw.decode("utf-8") if raw is not None else f"# {journal.stem} — Daily\n\n{SECTION}\n\n"
        nl = "\r\n" if txt.count("\r\n") * 2 > txt.count("\n") else "\n"  # the file's own, as the reflection
        new_txt = pattern.sub("", txt)
        # Ensure section header still present after removal
        if SECTION not in new_txt:
            new_txt = new_txt.rstrip() + f"{nl}{nl}{SECTION}{nl}{nl}"
        new_txt = new_txt.rstrip("\r\n") + nl + entry.replace("\n", nl) + nl
        if raw is not None and new_txt == txt:
            return False
        if _read(journal) == raw:
            write_atomic(journal, [new_txt], None)
            return True
    raise RuntimeError(f"{journal.name} kept changing: recap not written")


def agent_of(argv):
    """The agent the hook runs for: Codex with exactly --agent codex (hooks.json); else Claude Code
    (settings.json passes nothing), any other argument ignored as before."""
    return "codex" if argv == ["--agent", "codex"] else "claude"


def main():
    agent = agent_of(sys.argv[1:])
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if agent == "codex":
        import _agents
        if not _agents.codex_hook("session-recap", payload):
            sys.exit(0)   # Codex not chosen, the kit's own run, a session not typed: nothing read, nothing written

    if clean_item is None:
        raise RuntimeError("_vault_write.py or _redact.py not found next to session-recap.py: nothing written")
    sid = payload.get("session_id") or payload.get("sessionId") or "unknown"
    if not isinstance(sid, str) or not SID_RE.fullmatch(sid):
        raise ValueError("session_id is not a plain id: nothing written")
    cwd = payload.get("cwd") or os.getcwd()
    transcript_path = payload.get("transcript_path")

    # Debounce: skip if the last recap for this sid was < DEBOUNCE_SECS ago.
    # A shared /tmp: never follow a link someone left under that name.
    DEBOUNCE_SECS = 30
    LOCK_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = LOCK_DIR / f"recap-{sid}.lock"
    try:
        if time.time() - lock.lstat().st_mtime < DEBOUNCE_SECS:
            sys.exit(0)
    except OSError:
        pass  # absent; anything else, the open below says
    fd = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, str(time.time()).encode())
    finally:
        os.close(fd)

    # Skip smoke tests / temp cwd (cross-platform)
    if cwd.startswith(tempfile.gettempdir()) or cwd.startswith("/tmp") or cwd.startswith("/private/tmp"):
        sys.exit(0)

    stats = parse_rollout(transcript_path) if agent == "codex" else parse_transcript(transcript_path)
    # Skip sessions < 3 tool calls (probablement triviales)
    if sum(stats["tools"].values()) < 3:
        sys.exit(0)

    day = time.strftime("%Y-%m-%d")
    entry = render_entry(stats, sid, cwd, agent)
    upsert_session(JOURNAL_DIR / f"{day}.md", sid, entry, agent)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Log the error but never break the Stop hook
        err_log = Path.home() / ".claude" / "logs" / "session-recap-errors.log"
        try:
            err_log.open("a", encoding="utf-8").write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {type(e).__name__}: {e}\n")
        except Exception:
            pass
        sys.exit(0)
