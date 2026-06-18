#!/usr/bin/env python3
"""
Stop hook: extracts lightweight stats from a Claude session and appends them to
~/Documents/Brain/Journal/YYYY-MM-DD.md under the "📊 Claude Sessions" section.

Idempotent (skips if session_id already present). No LLM call.
"""
import json, sys, os, re, time, tempfile
from pathlib import Path
from collections import Counter
from datetime import datetime

BRAIN = Path.home() / "Documents" / "Brain"
JOURNAL_DIR = BRAIN / "Journal"
LOCK_DIR = Path(tempfile.gettempdir()) / "claude-session-locks"

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

    with open(path, "r", encoding="utf-8", errors="replace") as f:
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
                            stats["first_prompt"] = c.get("text", "")[:200]
                            break
                elif isinstance(content, str):
                    stats["first_prompt"] = content[:200]

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
    bases = [Path(f).name for f in sorted(files)]
    if len(bases) <= 3:
        return ", ".join(bases) if bases else "—"
    return ", ".join(bases[:3]) + f" (+{len(bases)-3})"


def render_entry(stats, session_id, cwd):
    end = fmt_time(stats["last_ts"])
    dur_str, resumed = fmt_duration(stats["first_ts"], stats["last_ts"])
    header_time = (f"~{end}" if resumed else f"{fmt_time(stats['first_ts'])}–{end}")
    cwd_s = short_cwd(cwd)
    model = (stats["model"] or "").replace("claude-", "")
    top_tools = ", ".join(f"{n} {c}" for n, c in stats["tools"].most_common(5))
    files_s = short_files(stats["files"])
    prompt = (stats["first_prompt"] or "").replace("\n", " ").strip()
    if len(prompt) > 140:
        prompt = prompt[:137] + "..."
    signals = ", ".join(f"{s}×{n}" for s, n in stats["signals"].most_common(5))

    lines = [
        f"- **{header_time}** ({dur_str}) `{cwd_s}` {model} · sid:`{session_id[:8]}`",
        f"  - tools: {top_tools or '—'}",
    ]
    if prompt:
        lines.append(f"  - intent: {prompt}")
    if files_s != "—":
        lines.append(f"  - files: {files_s}")
    if signals:
        lines.append(f"  - infra/cli: {signals}")
    return "\n".join(lines)


def ensure_journal(day):
    f = JOURNAL_DIR / f"{day}.md"
    if not f.exists():
        JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        f.write_text(f"# {day} — Daily\n\n## 📊 Claude Sessions\n\n", encoding="utf-8")
    elif "## 📊 Claude Sessions" not in f.read_text(encoding="utf-8"):
        with f.open("a", encoding="utf-8") as fh:
            fh.write("\n## 📊 Claude Sessions\n\n")
    return f


def upsert_session(journal, session_id, entry):
    """Replace existing block for this sid (or append). True if changed."""
    sid_short = session_id[:8]
    txt = journal.read_text(encoding="utf-8")
    # Match existing block: header line + nested "  - ..." continuation lines
    pattern = re.compile(
        rf"^- \*\*[^\n]+sid:`{re.escape(sid_short)}`[^\n]*\n(?:  - [^\n]+\n?)*",
        re.MULTILINE,
    )
    new_txt = pattern.sub("", txt)
    # Ensure section header still present after removal
    if "## 📊 Claude Sessions" not in new_txt:
        new_txt = new_txt.rstrip() + "\n\n## 📊 Claude Sessions\n\n"
    new_txt = new_txt.rstrip("\n") + "\n" + entry + "\n"
    if new_txt != txt:
        journal.write_text(new_txt, encoding="utf-8")
        return True
    return False


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    sid = payload.get("session_id") or payload.get("sessionId") or "unknown"
    cwd = payload.get("cwd") or os.getcwd()
    transcript_path = payload.get("transcript_path")

    # Debounce: skip if the last recap for this sid was < DEBOUNCE_SECS ago
    DEBOUNCE_SECS = 30
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock = LOCK_DIR / f"recap-{sid}.lock"
    if lock.exists():
        age = time.time() - lock.stat().st_mtime
        if age < DEBOUNCE_SECS:
            sys.exit(0)
    lock.write_text(str(time.time()))

    # Skip smoke tests / temp cwd
    if cwd.startswith(tempfile.gettempdir()) or cwd.startswith("/tmp") or cwd.startswith("/private/tmp"):
        sys.exit(0)

    stats = parse_transcript(transcript_path)
    # Skip sessions < 3 tool calls (probablement triviales)
    if sum(stats["tools"].values()) < 3:
        sys.exit(0)

    day = time.strftime("%Y-%m-%d")
    journal = ensure_journal(day)
    entry = render_entry(stats, sid, cwd)
    upsert_session(journal, sid, entry)
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
