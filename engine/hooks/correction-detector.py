#!/usr/bin/env python3
"""UserPromptSubmit hook: when the user corrects the assistant, remind it to
write the lesson to Profile/lessons.md before going on. When the user says
they did not follow an explanation, the lesson is about how it explains.

English and French. A bare negation is NOT a correction: "it doesn't build",
"je sais pas", "no worries" are ordinary prompts. Only signals aimed at the
assistant's work count. Measured on ~5,300 real prompts, the old bare-negation
regex fired on 36% of them, mostly on such ordinary negations.
"""
import json, sys, re, os
from pathlib import Path

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

raw = payload.get("prompt") or payload.get("user_prompt") or ""
prompt = raw.lower()
if not prompt:
    sys.exit(0)

# Relayed content (agent output, notifications, logs) is not the user talking.
# Fails OPEN: if the guard breaks, a false alarm beats a lost correction.
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _paste_guard import is_relayed
    if is_relayed(raw):
        sys.exit(0)
except Exception:
    pass

# --- Corrections aimed at the assistant -------------------------------------
STRONG = [
    # redirection
    r"\b(actually|instead|rather than|i'd rather|i would rather|i'd prefer|i would prefer|"
    r"no need to|it'd be better|it would be better|avoid (doing|using|adding|that|this|it))\b",
    r"\b(en fait|plut[ôo]t que|plut[ôo]t|au lieu de|je pr[ée]f[èe]re|[ée]vite de|"
    r"pas besoin|ce serait mieux|arr[êe]te de)\b",
    # reproach, second person
    r"\b(you forgot|you missed|you didn't|you did not|you were supposed to|you should have|"
    r"why didn't you|why did you|i told you|i asked you|not what i (asked|meant|wanted|said))\b",
    r"\b(t'as (pas|oubli[ée]|mal)|tu as (pas|oubli[ée]|mal)|tu devais|tu [ée]tais cens[ée]|"
    r"pourquoi t'as|pourquoi tu (n'|)as pas|je t'ai (dit|demand[ée])|c'est pas [çc]a|c'est pas ce que)\b",
    # a lasting rule
    r"\b(from now on|going forward|next time|in the future|never again|don't ever|do not ever)\b",
    r"\b([àa] l'avenir|dor[ée]navant|la prochaine fois|d[ée]sormais)\b",
    # a prompt that opens on "no" ("no worries" / "no problem" do not count)
    r"^\s*(no|nope|nah)\b(?![- ](problem|worries|rush|pressure|biggie)\b)",
    r"^\s*(non|nan)\b",
    # a verdict on what the assistant produced
    r"\b(that|this|it)('s| is| was) (wrong|incorrect|not right|not correct|not it|not what)\b",
    r"\byour (fix|code|script|plan|answer|version|approach|change|patch|analysis|summary|test)s?\b"
    r".{0,40}\b(wrong|broken|incorrect|doesn't|does not|didn't|did not|isn't|is not|fails?|failed)\b",
    r"\b(ton|ta|tes) (fix|code|script|plan|reco|version|approche|truc|analyse)\b.{0,40}\b(pas|faux|mauvais)\b",
]

# French "pas" judging the output ("c'est pas bon", "pas à jour"); the broader
# "verb + pas" fired on bug reports ("ça passe pas") and reassurance.
WRONG_OUTPUT_FR = re.compile(r"\bpas\b.{0,12}?\b(bon|bons|bonne|correct|juste|[àa] jour|[çc]a)\b")
REASSURANCE_FR = re.compile(r"\bpas\b\s*(un |de |)(pb|probl[èe]me|souci|soucis|grave|urgent|la peine)\b|\btkt\b")

# --- The user did not follow what the assistant said -------------------------
# Rare but productive: the lesson is about the assistant's way of explaining.
# "why" questions are about the product ("I don't understand why it fails").
_NOTGOT = r"(compris(es?)?|capt[ée]e?s?|suivie?s?|pig[ée]e?s?)"
CONFUSION = re.compile(
    r"\bi (don't|do not|didn't|did not) (understand|get|follow)\b(?! why)|\bnot sure i (understand|follow|get)\b|"
    r"\bwhat do you mean\b|\bi'm (lost|confused)\b|\byou lost me\b|"
    r"\b(j'ai pas " + _NOTGOT + r"|je (comprends|capte|pige|vois) pas|pas (tout )?" + _NOTGOT + r"|"
    r"je suis pas s[ûu]r d'avoir " + _NOTGOT + r"|explique[- ]moi|j'ai rien " + _NOTGOT + r")"
)

# A long prompt is a task spec or a paste: "instead", "désormais", "you should"
# in its body describe the work, they do not correct the assistant. Measured
# on ~1,500 real prompts: under 600 chars the hook fired on 4-6% of them, above
# 1,500 chars on 48%. A correction is stated up front, so only the opening of
# a long prompt is read.
scope = prompt if len(prompt) < 600 else prompt[:300]
is_strong = any(re.search(p, scope) for p in STRONG) or (
    bool(WRONG_OUTPUT_FR.search(scope)) and not REASSURANCE_FR.search(scope)
)
is_confusion = bool(CONFUSION.search(scope))
if not (is_strong or is_confusion):
    sys.exit(0)

brain = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
if not (brain / "Profile" / "lessons.md").exists():
    brain = Path.home() / "Documents" / "Brain"

if is_strong:
    banner = "⚠️ CORRECTION DETECTED"
    last = "3. Only then apply the correction and continue."
else:
    banner = "⚠️ CONFUSION DETECTED (the user did not follow what you said)"
    last = ("3. The lesson is about HOW YOU EXPLAIN (jargon, shortcuts, internal acronyms), "
            "not a technical mistake. Then explain again, starting from something concrete.")
reminder = (
    f"{banner} — BEFORE doing anything else:\n"
    f"1. Append a line to {brain}/Profile/lessons.md under today's header (## YYYY-MM-DD). The file is chronological (most recent day at the BOTTOM): today's header lives at the END of the file. Create it at the end if missing and add the entry right below it, NEVER at the top.\n"
    "   Format: - **[short context]** -> rule: [what to do] (when: [trigger condition])\n"
    "2. Confirm visibly: ✓ noted in lessons.md\n"
    f"{last}"
)
print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": reminder}}))
sys.exit(0)
