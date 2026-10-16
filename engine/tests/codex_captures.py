"""Codex sessions for the tests, made from step 0's real captures (fixtures/codex/phase0/): the
lines Codex 0.162.1 and 0.163.0-alpha.11 wrote, moved to the day a test needs. Every line added
is a copy of one of the capture's own lines, its text or command changed: the shapes stay Codex's.
Also what Codex gave its hooks in step 0, and the schemas Codex checks a hook's answer against
(fixtures/codex/hooks-schema-0.162.1/: codex-rs/hooks/schema/generated, tag rust-v0.162.1).
And open_gate(), which opens Codex in a copy of _agents.py.
"""
import copy
import json
import re
from pathlib import Path

P0 = Path(__file__).resolve().parent / "fixtures" / "codex" / "phase0"
SCHEMAS = P0.parent / "hooks-schema-0.162.1"
VAULT = "ubuntu-0.162.1/rollout-exec-vault-linux.jsonl"     # AGENTS.md of the vault, "Bonjour"
LOCK = "ubuntu-0.162.1/rollout-exec-lock-linux.jsonl"       # two tool calls (exec, exec_command)
TUI = "macos-0.162.1/rollout-tui-apikey-macos.jsonl"        # interactive: source "cli", the hooks' context
TRUSTED = "ubuntu-0.162.1/rollout-exec-hooks-trusted-linux.jsonl"
TRUSTED_HOOKS = "ubuntu-0.162.1/hook-payloads-trusted-linux.jsonl"
TUI_HOOKS = "macos-0.162.1/hook-payloads-tui-apikey-macos.jsonl"
OPEN_GATE = 'CODEX_OUVERT = frozenset({"darwin", "linux", "windows"})'


def open_gate(text):
    """_agents.py's text with Codex open on every system, whatever its gate says (closed on every system
    today, open on some once a change opens them): only the tests that pin the closed gate change then.
    None when the gate's line moved."""
    opened, n = re.subn(r"^CODEX_OUVERT = frozenset\(.*\)$", OPEN_GATE, text, flags=re.M)
    return opened if n == 1 else None


def records(name):
    return [json.loads(line) for line in (P0 / name).read_text(encoding="utf-8").splitlines()]


def rollouts():
    """Every rollout step 0 kept."""
    return sorted(P0.glob("*/rollout-*.jsonl"))


def stop_payload(name=TRUSTED_HOOKS):
    """What Codex gave its Stop hook in that capture."""
    return next(r["stdin"] for r in map(json.loads, (P0 / name).read_text(encoding="utf-8").splitlines())
                if r["event"] == "Stop")


def _message(recs, role):
    """The capture's last message of `role`: for the user, what was typed (Codex's own come first)."""
    return [r for r in recs if r["type"] == "response_item" and r["payload"].get("type") == "message"
            and r["payload"].get("role") == role][-1]


def hook_payloads(pattern="*/hook-payloads-*.jsonl"):
    """(capture, record) of every hook Codex ran in step 0: record["event"], and record["stdin"], what
    Codex gave the hook."""
    for p in sorted(P0.glob(pattern)):
        for line in p.read_text(encoding="utf-8").splitlines():
            yield "%s/%s" % (p.parent.name, p.name), json.loads(line)


def moved(name, day, sid=None, said=(), tools=(), hhmm="12:00", cwd=None, prompt=None, source=None):
    """The capture `name` as a session of `day` (every line at hh:mm UTC), its id `sid` when given, what
    the user typed `prompt` when given, then one more user or assistant message per (role, text) of
    `said`, and one more exec_command call per item of `tools` (its command, or its arguments), before
    its last line. `source`, when given, replaces the session's ("exec" in the exec captures; "cli" is
    what the terminal's sessions say, TUI). Returns the lines, as text."""
    recs = records(name)
    if prompt is not None:
        typed = _message(recs, "user")["payload"]
        typed["content"] = [dict(typed["content"][0], text=prompt)]
    for r in recs:
        r["timestamp"] = "%sT%s:00.000Z" % (day, hhmm)
        if r["type"] == "session_meta":
            if sid:
                r["payload"]["id"] = r["payload"]["session_id"] = sid
            if cwd:
                r["payload"]["cwd"] = cwd
            if source is not None:
                r["payload"]["source"] = source
    extra = []
    for role, text in said:
        m = copy.deepcopy(_message(recs, role))
        m["payload"]["content"] = [dict(m["payload"]["content"][0], text=text)]
        extra.append(m)
    calls = [r for r in records(LOCK) if r["type"] == "response_item" and r["payload"].get("type") == "function_call"]
    for i, cmd in enumerate(tools):
        c = copy.deepcopy(calls[0])
        c["timestamp"] = recs[0]["timestamp"]
        c["payload"]["arguments"] = json.dumps(cmd if isinstance(cmd, dict) else {"cmd": cmd}, ensure_ascii=False)
        c["payload"]["call_id"] = "call_more_%d" % i
        extra.append(c)
    recs[-1:-1] = extra
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs)


def put(codex_home, day, text, sid):
    """Writes a rollout where Codex keeps it, named as Codex names it."""
    folder = Path(codex_home) / "sessions" / day[:4] / day[5:7] / day[8:10]
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / ("rollout-%sT12-00-00-%s.jsonl" % (day, sid))
    p.write_bytes(text.encode("utf-8"))   # as Codex writes it: UTF-8, LF, on every system
    return p


EVENT_SCHEMA = {"SessionStart": "session-start", "UserPromptSubmit": "user-prompt-submit", "Stop": "stop"}


def output_errors(event, answer):
    """What Codex 0.162.1 would refuse in a hook's answer (`answer`, parsed JSON) to `event`, checked
    against its own schema: [] when it takes it. Codex refuses the whole answer for one unknown field
    (deny_unknown_fields). Only the keywords these schemas use are known; any other is an error, so
    a schema of a newer Codex cannot pass unread."""
    schema = json.loads((SCHEMAS / ("%s.command.output.schema.json" % EVENT_SCHEMA[event])).read_text(encoding="utf-8"))
    return _errors(schema, answer, schema, "$")


def _errors(node, value, root, where):
    known = {"$schema", "title", "description", "default", "definitions", "type", "properties",
             "additionalProperties", "required", "const", "enum", "allOf", "$ref"}
    if set(node) - known:
        return ["%s: schema keyword not handled: %s" % (where, sorted(set(node) - known))]
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        if not node["$ref"].startswith("#/definitions/") or name not in root.get("definitions", {}):
            return ["%s: unknown $ref %s" % (where, node["$ref"])]
        return _errors(root["definitions"][name], value, root, where)
    out = []
    for sub in node.get("allOf", ()):
        out += _errors(sub, value, root, where)
    kind = node.get("type")
    types = {"object": dict, "string": str, "boolean": bool}
    if kind is not None:
        if kind not in types:
            return ["%s: type %s not handled" % (where, kind)]
        if not isinstance(value, types[kind]):
            return out + ["%s: not a %s: %r" % (where, kind, value)]
    if "const" in node and value != node["const"]:
        out.append("%s: %r is not %r" % (where, value, node["const"]))
    if "enum" in node and value not in node["enum"]:
        out.append("%s: %r not in %r" % (where, value, node["enum"]))
    if isinstance(value, dict) and kind == "object":
        props = node.get("properties", {})
        if node.get("additionalProperties") is False:
            out += ["%s: unknown field %s" % (where, k) for k in value if k not in props]
        out += ["%s: %s missing" % (where, k) for k in node.get("required", ()) if k not in value]
        for k, sub in props.items():
            if k in value:
                out += _errors(sub, value[k], root, "%s.%s" % (where, k))
    return out
