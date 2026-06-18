#!/usr/bin/env python3
import json, sys, re, os
from pathlib import Path

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

prompt = (payload.get("prompt") or payload.get("user_prompt") or "").lower()
if not prompt:
    sys.exit(0)

PATTERNS = [
    r"\b(no|nope|nah|stop|don't|do not|not)\b",
    r"\b(actually|instead|rather|i'd prefer|i would prefer|avoid|no need to|it'd be better|it would be better)\b",
    r"\b(that's not it|that's wrong|you forgot|you were supposed to|why didn't you|why did you not)\b",
    r"\b(i want you to|from now on|going forward|next time|please don't)\b",
]
if not any(re.search(p, prompt) for p in PATTERNS):
    sys.exit(0)

brain = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
if not (brain / "Profile" / "lessons.md").exists():
    brain = Path.home() / "Documents" / "Brain"

reminder = (
    "⚠️ CORRECTION DETECTED — BEFORE doing anything else:\n"
    f"1. Append a line to {brain}/Profile/lessons.md under today's header (## YYYY-MM-DD).\n"
    "   Format: - **[short context]** -> rule: [what to do] (when: [trigger condition])\n"
    "2. Confirm visibly: ✓ noted in lessons.md\n"
    "3. Only then apply the correction and continue."
)
print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": reminder}}))
sys.exit(0)
