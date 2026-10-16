"""Codex sessions for the tests, made from step 0's real captures (fixtures/codex/phase0/): the
lines Codex 0.162.1 and 0.163.0-alpha.11 wrote, moved to the day a test needs. Every line added
is a copy of one of the capture's own lines, its text or command changed: the shapes stay Codex's.
"""
import copy
import json
from pathlib import Path

P0 = Path(__file__).resolve().parent / "fixtures" / "codex" / "phase0"
VAULT = "ubuntu-0.162.1/rollout-exec-vault-linux.jsonl"     # AGENTS.md of the vault, "Bonjour"
LOCK = "ubuntu-0.162.1/rollout-exec-lock-linux.jsonl"       # two tool calls (exec, exec_command)
TUI = "macos-0.162.1/rollout-tui-apikey-macos.jsonl"        # interactive: source "cli", the hooks' context
TRUSTED = "ubuntu-0.162.1/rollout-exec-hooks-trusted-linux.jsonl"
TRUSTED_HOOKS = "ubuntu-0.162.1/hook-payloads-trusted-linux.jsonl"


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


def moved(name, day, sid=None, said=(), tools=(), hhmm="12:00", cwd=None, prompt=None):
    """The capture `name` as a session of `day` (every line at hh:mm UTC), its id `sid` when given, what
    the user typed `prompt` when given, then one more user or assistant message per (role, text) of
    `said`, and one more exec_command call per item of `tools` (its command, or its arguments), before
    its last line. Returns the lines, as text."""
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
