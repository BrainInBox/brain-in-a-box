"""A stand-in for `claude`, for test-hooks.sh and test_reflection.py. It runs nothing.

Driven by files in $HOME/.claude/fake/ (HOME is one of the few variables the
reflection passes on):
    fixtures     folder of the real stream-json captures (app/src-tauri/tests/fixtures/repair)
    scenario     a capture to replay (default plan-ok), or tool-bash, second-init (a start-up line
                 with Bash after a clean one), orphan (leaves a child that let go of the pipes), hang
    answer.json  the structured answer put in place of the capture's one
    auth         authMethod `claude auth status` reports (default claude.ai)
    version      what `claude --version` prints (default 2.1.296 (Claude Code))
Each `-p` run appends to calls and records argv.json, env.json, cwd.json and prompt.txt.
"""
import json
import os
import stat
import subprocess
import sys
import time

FLAGS = ("--restricted --safe-mode --tools --permission-prompts --json-schema --max-budget-usd "
         "--no-session-persistence --strict-mcp-config --disallowedTools --settings --append-system-prompt")
FAKE = os.path.join(os.environ.get("HOME", ""), ".claude", "fake")


def setting(name, default=""):
    try:
        with open(os.path.join(FAKE, name), encoding="utf-8") as f:
            return f.read().strip() or default
    except OSError:
        return default


def record(name, data):
    with open(os.path.join(FAKE, name), "w", encoding="utf-8") as f:
        f.write(data)


def emit(event):
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main(args):
    if args == ["--version"]:
        print(setting("version", "2.1.296 (Claude Code)"))
        return 0
    if args == ["--help"]:
        print("Usage: claude [options]\n" + FLAGS)
        return 0
    if args[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": True, "authMethod": setting("auth", "claude.ai")}))
        return 0
    if "-p" not in args:
        return 2
    prompt = sys.stdin.read()
    with open(os.path.join(FAKE, "calls"), "a") as f:
        f.write("-p\n")
    record("argv.json", json.dumps(args))
    record("env.json", json.dumps(dict(os.environ)))
    cwd = os.getcwd()
    record("cwd.json", json.dumps({"cwd": cwd, "mode": oct(stat.S_IMODE(os.stat(cwd).st_mode))}))
    record("prompt.txt", prompt)
    scenario = setting("scenario", "plan-ok")
    if scenario == "hang":
        # A child of its own, as Claude Code's tools would start: the group kill must reach it.
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        record("grandchild.pid", str(child.pid))
        time.sleep(60)
        return 0
    with open(os.path.join(FAKE, "answer.json"), encoding="utf-8") as f:
        answer = json.load(f)
    if scenario == "orphan":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        record("grandchild.pid", str(child.pid))
    name = "plan-ok" if scenario in ("tool-bash", "second-init", "orphan") else scenario
    with open(os.path.join(setting("fixtures"), name + ".jsonl"), encoding="utf-8") as f:
        lines = f.read().splitlines()
    if scenario == "second-init":
        with open(os.path.join(setting("fixtures"), "init-bash.jsonl"), encoding="utf-8") as f:
            lines.insert(1, f.readline().strip())
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            sys.stdout.write(line + "\n")  # broken-json: replayed as captured
            continue
        content = (event.get("message") or {}).get("content")
        answering = event.get("type") == "assistant" and isinstance(content, list) and any(
            b.get("name") == "StructuredOutput" for b in content)
        if answering:
            if scenario == "tool-bash":
                emit({**event, "message": {**event["message"], "content": [
                    {"type": "tool_use", "id": "toolu_FAKE_BASH", "name": "Bash",
                     "input": {"command": "touch \"$HOME/pwned\""}}]}})
            for b in content:
                if b.get("name") == "StructuredOutput":
                    b["input"] = answer
        if event.get("type") == "result" and "structured_output" in event:
            event["structured_output"] = answer
            event["result"] = json.dumps(answer, ensure_ascii=False)
        emit(event)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
