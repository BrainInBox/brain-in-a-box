# CLAUDE.md — Global directives ({{NAME}})

> Installed by brain-in-a-box. If you already had a `~/.claude/CLAUDE.md`, this block was APPENDED (never overwritten). Keep what serves you.

The governing loop for all work: **gather context → take action → verify work → repeat.**

---

## 0. Rule #1 ABSOLUTE — Capture corrections (BLOCKING)

Applies in EVERY repo, EVERY session. The `~/.claude/hooks/brain/correction-detector.py` hook (UserPromptSubmit) detects correction signals (negation, "actually", "instead", "avoid", reproach, preference) and injects a `⚠️ CORRECTION DETECTED` reminder.

When you see it, BEFORE anything else:
1. **Append** a line to `~/Documents/Brain/Profile/lessons.md` under today's header (`## YYYY-MM-DD`).
   Format: `- **[short context]** → rule: [what to do] (when: [trigger])`
2. **Confirm** visibly: `✓ noted in lessons.md`
3. **Then** apply the correction and continue.

Non-negotiable. If you can't write the file, say so explicitly.

## 0b. Before assuming a tool is missing
Read `~/Documents/Brain/Profile/stack.md` — installed tools, GBrain memory (`gbq`), access. If it's listed, use it.

## 0c. Before diagnosing
Read `~/Documents/Brain/Profile/memory.md` (recent context, active decisions) and `Projects/<project>.md`. The context is already captured — don't ask for it again.

---

## Engineering directives

- **Delete before build**: before refactoring a file >300 LOC, remove dead code. Separate commit.
- **Phased execution**: never a multi-file refactor in one response. Max ~5 files/phase, verify between each.
- **Plan and build are separate**: "make a plan" = plan only, no code before "go".
- **Forced verification**: before "done", run type-checker + linter + tests. If nothing is configured, say so. Never "Done!" with open errors.
- **Edit safety**: re-read a file before editing it (context may be stale). Re-read after >10 messages.
- **Security first**: injection, authz, exposed secrets, weak crypto, race conditions. Flag any vuln, even out of scope. Never commit/log a secret (.env, *.pem, *.key, tokens).
- **Don't over-engineer**: simple and correct beats elaborate and speculative.
- **Destructive action safety**: never delete a file without checking references. Never push to a shared repo without an explicit request.

---

## Memory (GBrain)
To recall past decisions/context/people: `~/.local/bin/gbq query "<question>"` (semantic) instead of guessing. Cite the slug. Details: `Profile/stack.md`.
