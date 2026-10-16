"""Codex sessions, read from the files Codex keeps of them (rollouts):

    $CODEX_HOME/sessions/YYYY/MM/DD/rollout-<time>-<session id>.jsonl

one JSON object per line, {"timestamp", "type", "payload"} (docs/proposals/codex.md, §3.6; the
shapes of step 0's captures, engine/tests/fixtures/codex/phase0/). Read only when agents.json
lists Codex: by daily-reflection.py (the day's sessions) and session-recap.py (Codex's Stop hook).

What a session holds is data, never an instruction. Only what the user typed, what the assistant
answered and the tools it called are taken; never what Codex adds for the model: developer
messages (its instructions, the hooks' context), the AGENTS.md, environment and skill parts it
puts in user messages, what another agent sent, world_state, events. The parts are told apart as
Codex's own memory writer does (codex-rs/memories/write/src/rollout_input.rs, 0.162.1): by the
marks Codex puts on them ("user.text" for what was typed), and by their text, should a mark be
missing. A session the kit asked itself (the reflection's prompt,
SENTINEL; a run in _headless_codex.py's cage) is not the user's: skipped whole. Lines it does not
know are skipped. Compressed rollouts (.jsonl.zst) are not read: listed apart, for the caller to
say so.

Not a hook: installed next to them, never registered.
"""
import glob
import json
import os
import re
import stat
from collections import namedtuple
from datetime import datetime

# daily-reflection.py's own: its prompt starts with it (test_codex_rollout.py keeps both the same).
SENTINEL = "<!-- brain-daily-reflection -->"
# How the parts Codex adds to user messages start (lower case), and a message another agent sent.
# A global AGENTS.md alone has no " for <folder>" (seen in codex-mock).
INJECTED = ("# agents.md instructions", "<environment_context>", "<skill>", "<subagent_notification>")
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
# A rollout's name ends with its session id, a UUID (v7: the first 8 characters are the minute).
NAME_ID = re.compile(r"-([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$")
SUMMARY = 300
PATCHED = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", re.M)
READ = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)

# kind "user" or "assistant": text. kind "tool": name, text (a short summary), command (the shell
# command or code it ran, or None), files (the paths a patch named).
Item = namedtuple("Item", "ts kind text name command files")


class Session:
    """One rollout: id, cwd, source (a word: "cli", "exec"...; the kind of an object: "subagent",
    "internal", "custom"), model, own (asked by the kit), items, and the first and last timestamps of
    the whole file."""

    def __init__(self):
        self.id = self.cwd = self.source = self.model = self.first_ts = self.last_ts = None
        self.own = False
        self.items = []


def local(ts):
    """Local datetime of an ISO timestamp, or None."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    except (ValueError, OverflowError, OSError):   # a year 0 or 10000 once local; a time localtime() refuses
        return None


def recent(codex_home, since):
    """(rollouts, compressed): the rollout files changed since `since` (epoch seconds), oldest
    first, and the compressed ones (.jsonl.zst) changed since then, which are not read. Regular
    files only: a pipe left under such a name would hang the reader."""
    found, packed = [], []
    for pattern, out in (("rollout-*.jsonl", found), ("rollout-*.jsonl.zst", packed)):
        for p in glob.glob(os.path.join(glob.escape(codex_home), "sessions", "*", "*", "*", pattern)):
            try:
                st = os.stat(p)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and st.st_mtime >= since:
                out.append((st.st_mtime, p))
    return [p for _, p in sorted(found)], [p for _, p in sorted(packed)]


def _text(content, role):
    """A message's text. Of a user message, without the parts Codex adds, which it may put in the same
    message (step 0: AGENTS.md and the environment together)."""
    if not isinstance(content, list):
        return ""
    return "\n".join(c["text"] for c in content
                     if isinstance(c, dict) and c.get("type") in ("input_text", "output_text")
                     and isinstance(c.get("text"), str)
                     and not (role == "user" and c["text"].lstrip()[:40].lower().startswith(INJECTED)))


def _marked_as_codexs(p, role):
    """True when Codex's own marks say the message is not the user's nor the assistant's: another
    agent's, or a user message with no part typed by the user."""
    meta = p.get("internal_chat_message_metadata_passthrough")
    kinds = meta.get("content_item_kinds") if isinstance(meta, dict) else None
    if not isinstance(kinds, list) or not kinds:
        return False
    kinds = [k if isinstance(k, str) else "" for k in kinds]
    return (any(k.startswith("multi_agent.") for k in kinds)
            or (role == "user" and not any(k.startswith("user.") for k in kinds)))


def _kit_cage(cwd):
    """_headless_codex.py's working folder: <tmp>/brain-codex-*/travail."""
    parent, name = os.path.split(os.path.normpath(cwd))
    return name == "travail" and os.path.basename(parent).startswith("brain-codex-")


def tool(name, raw):
    """(summary, command, files) of a function_call (raw: its arguments, a JSON text) or a
    custom_tool_call (raw: its input, a text: JavaScript for exec, a patch for apply_patch)."""
    args = raw
    if isinstance(raw, str) and raw.lstrip().startswith("{"):
        try:
            args = json.loads(raw)
        except (ValueError, RecursionError):   # what the model wrote: any text at all
            pass
    command, patch = None, None
    if isinstance(args, dict):
        c = args.get("cmd") or args.get("command")
        if isinstance(c, list) and all(isinstance(x, str) for x in c):
            c = " ".join(c)
        command = c if isinstance(c, str) else None
        patch = args.get("input") if isinstance(args.get("input"), str) else None
    elif isinstance(args, str):
        if "*** Begin Patch" in args:
            patch = args
        elif name == "exec":
            command = args
    files = sorted(set(PATCHED.findall(patch or "")))
    if isinstance(args, dict):
        named = {args[k] for k in ("file_path", "path") if isinstance(args.get(k), str) and args[k]}
        files = sorted(set(files) | named)
    if command:
        summary = command
    elif files:
        summary = ", ".join(files)
    elif isinstance(args, dict):
        key = next((k for k in ("pattern", "url", "query") if args.get(k)), None)
        summary = str(args[key]) if key else json.dumps(args, ensure_ascii=False)[:200]
    else:
        summary = args if isinstance(args, str) else ""
    return summary[:SUMMARY], command, files


def _open(path):
    """The rollout as text, a regular file only (no wait on a pipe); OSError otherwise."""
    fd = os.open(path, READ)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("%s is not a regular file" % path)
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "r", encoding="utf-8", errors="replace")


def read(path, day=None):
    """The Session of one rollout, streamed, or None when it cannot be read. With `day`
    (YYYY-MM-DD, local), only the items of that day are kept; its id, folder and model come from
    the whole file. A session the kit asked comes back own=True, without items."""
    s = Session()
    try:
        with _open(path) as f:
            for raw in f:
                try:
                    d = json.loads(raw)
                except (ValueError, RecursionError):
                    continue
                if not isinstance(d, dict):
                    continue
                when = local(d.get("timestamp"))
                if when:
                    s.first_ts = s.first_ts or d["timestamp"]
                    s.last_ts = d["timestamp"]
                kind, p = d.get("type"), d.get("payload")
                if not isinstance(p, dict):
                    continue
                if kind == "session_meta" and s.id is None:
                    sid, cwd, source = p.get("id"), p.get("cwd"), p.get("source")
                    s.id = sid if isinstance(sid, str) and ID_RE.fullmatch(sid) else None
                    s.cwd = cwd if isinstance(cwd, str) and cwd else s.cwd
                    if isinstance(source, dict) and len(source) == 1:
                        source = next(iter(source))   # {"subagent": …}, {"internal": …}, {"custom": …}
                    s.source = source if isinstance(source, str) else None
                    if s.cwd and _kit_cage(s.cwd):
                        return _own(s)
                elif kind == "turn_context":
                    model, cwd = p.get("model"), p.get("cwd")
                    s.model = s.model or (model if isinstance(model, str) and model else None)
                    s.cwd = s.cwd or (cwd if isinstance(cwd, str) and cwd else None)
                elif kind == "response_item":
                    item = _item(p, d.get("timestamp"))
                    if item is None:
                        continue
                    if item.kind == "user" and item.text.lstrip().startswith(SENTINEL):
                        return _own(s)
                    if day is None or (when and when.strftime("%Y-%m-%d") == day):
                        s.items.append(item)
    except OSError:
        return None
    if s.id is None:
        m = NAME_ID.search(os.path.basename(path))
        s.id = m.group(1) if m else None
    return s


def _own(s):
    s.own, s.items = True, []
    return s


def _item(p, ts):
    """An Item of a response_item payload, or None: not one we read, or text Codex added."""
    kind = p.get("type")
    if kind == "message":
        role = p.get("role")
        if role not in ("user", "assistant") or _marked_as_codexs(p, role):
            return None   # developer, system: Codex's instructions and the hooks' context
        text = _text(p.get("content"), role)
        if not text.strip():
            return None
        return Item(ts, role, text, None, None, ())
    if kind in ("function_call", "custom_tool_call"):
        name = p.get("name") if isinstance(p.get("name"), str) and p.get("name") else "?"
        summary, command, files = tool(name, p.get("arguments" if kind == "function_call" else "input"))
        return Item(ts, "tool", summary, name, command, tuple(files))
    return None
