"""Is this prompt RELAYED content rather than something the user wrote?

Agent output, task notifications, a scheduled job's prompt, pasted logs or a
stack trace often contain "no", "don't" or "wrong" without being a correction
of the assistant. Used by correction-detector.py.

Conservative on purpose: a missed correction is lost from long-term memory,
while a false alarm costs one line in lessons.md. So it only says "relayed"
on unmistakable machine output, or when the user wrote almost nothing.

Not a hook: installed next to them, never registered in settings.json.
"""
import re

# Never typed by hand.
HARD_MARKERS = (
    "<task-notification",
    "[system notification",
    "<!-- brain-daily-reflection -->",
    "=== git digest",
    "=== sessions ===",
    "npm err!",
    "traceback (most recent",
    "node_modules/",
    "<command-name>",
    "<local-command-stdout>",
)

_FENCE = re.compile(r"```.*?```", re.S)
_LOG_LINE = re.compile(
    r"^\s*(at |npm |yarn |pnpm |\$ |> |\+ |- \[|warn|error|info|debug|fatal|"
    r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}|\[[\w.-]+\]|[|+└├─]|✓|✗|✅|❌|⚠️|▸|PASS|FAIL)",
    re.I,
)


def _prose_chars(text):
    """What the user wrote themselves: outside code fences and log-shaped lines."""
    kept = [ln for ln in _FENCE.sub(" ", text).splitlines() if ln.strip() and not _LOG_LINE.match(ln)]
    return len(" ".join(kept).strip())


def _log_lines(text):
    # Length alone does not tell: a 330-char test verdict is relayed, a
    # 600-char correction is not. Log-shaped lines do.
    return sum(1 for ln in text.splitlines() if _LOG_LINE.match(ln))


def is_relayed(text):
    if not text:
        return False
    if any(m in text.lower() for m in HARD_MARKERS):
        return True
    # The user wrote little themselves AND the rest is a big paste or dense in
    # log lines. "no, not that: ```x```" must still pass (short, no log line).
    if _prose_chars(text) >= 150:
        return False
    return len(text) >= 400 or _log_lines(text) >= 3
