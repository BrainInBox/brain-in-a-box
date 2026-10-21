"""A stand-in for `claude`, for test-hooks.sh and test_reflection.py. It runs nothing.

Driven by files in $HOME/.claude/fake/ (HOME is one of the few variables the
reflection passes on):
    fixtures     folder of the real stream-json captures (app/src-tauri/tests/fixtures/repair)
    scenario     a capture to replay (default plan-ok), or tool-bash, second-init (a start-up line
                 with Bash after a clean one), orphan (leaves a child that let go of the pipes), hang
    answer.json  the structured answer put in place of the capture's one
    answers/<n>.json, scenarios/<n>   the same for the n-th `-p` run only (the import's requests)
    auth         authMethod `claude auth status` reports (default claude.ai)
    version      what `claude --version` prints (default 2.1.296 (Claude Code))
    flags        the options `claude --help` lists (default FLAGS)
Each `-p` run appends to calls and records argv.json, env.json, cwd.json, prompt.txt (and
prompts/<n>.txt) and stdin.json (sha256 and size of what it read). With --input-format
stream-json, stdin is user messages, one per line: prompt.txt is their text, images.json says
each image (media_type, sha256, size). No image is ever written.
"""
import base64
import hashlib
import json
import os
import stat
import subprocess
import sys
import time

FLAGS = ("--restricted --safe-mode --tools --permission-prompts --json-schema --max-budget-usd "
         "--no-session-persistence --strict-mcp-config --disallowedTools --settings --append-system-prompt "
         "--input-format")
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


def out(line):
    """UTF-8 whatever the locale, as Claude Code writes: on Windows a cp1252 stdout would turn an
    accented answer into replacement characters."""
    sys.stdout.buffer.write((line + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def emit(event):
    out(json.dumps(event, ensure_ascii=False))


def read_messages(stream):
    """The user messages of stream-json input, one per line (split on newlines only): their text,
    and what images.json says of their images. Anything else stops the fake, as Claude Code would refuse it."""
    texts, images = [], []
    for line in stream.split("\n"):
        if not line:
            continue
        message = json.loads(line)
        if message["type"] != "user" or message["message"]["role"] != "user":
            raise ValueError("not a user message: %r" % message["type"])
        for block in message["message"]["content"]:
            if block["type"] == "image":
                data = base64.b64decode(block["source"]["data"], validate=True)
                images.append({"media_type": block["source"]["media_type"],
                               "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
            elif block["type"] == "text":
                texts.append(block["text"])
            else:
                raise ValueError("a block of type %r" % block["type"])
    return "\n".join(texts), images


def main(args):
    if args == ["--version"]:
        print(setting("version", "2.1.296 (Claude Code)"))
        return 0
    if args == ["--help"]:
        print("Usage: claude [options]\n" + setting("flags", FLAGS))
        return 0
    if args[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": True, "authMethod": setting("auth", "claude.ai")}))
        return 0
    if "-p" not in args:
        return 2
    raw = sys.stdin.buffer.read()
    prompt = raw.decode("utf-8")   # what the launcher writes, whatever the locale
    with open(os.path.join(FAKE, "calls"), "a") as f:
        f.write("-p\n")
    with open(os.path.join(FAKE, "calls")) as f:
        n = f.read().count("-p")
    record("argv.json", json.dumps(args))
    record("env.json", json.dumps(dict(os.environ)))
    cwd = os.getcwd()
    record("cwd.json", json.dumps({"cwd": cwd, "mode": oct(stat.S_IMODE(os.stat(cwd).st_mode))}))
    record("stdin.json", json.dumps({"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}))
    images = []
    if "--input-format" in args and args[args.index("--input-format") + 1:][:1] == ["stream-json"]:
        prompt, images = read_messages(prompt)
    record("images.json", json.dumps(images))
    record("prompt.txt", prompt)
    os.makedirs(os.path.join(FAKE, "prompts"), exist_ok=True)
    record(os.path.join("prompts", "%d.txt" % n), prompt)
    scenario = setting(os.path.join("scenarios", str(n)), "") or setting("scenario", "plan-ok")
    if scenario == "hang":
        # A child of its own, as Claude Code's tools would start: the group kill must reach it.
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        record("grandchild.pid", str(child.pid))
        time.sleep(60)
        return 0
    nth = os.path.join(FAKE, "answers", "%d.json" % n)
    with open(nth if os.path.exists(nth) else os.path.join(FAKE, "answer.json"), encoding="utf-8") as f:
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
            out(line)  # broken-json: replayed as captured
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
