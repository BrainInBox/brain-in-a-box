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
each image (media_type, sha256, size), and fragments/<n>.json a piece of its bytes and of its
base64, for a search of the disk. No image is ever written.

The scenario "guide" answers Bibo's guide (the field test, terrain_app.py) as Claude Code would,
on the image it was sent: the band's code read back and the green button found by the app tests'
second reader (app/src-tauri/tests/fixtures/guide/fake_claude.py), the answer of guide/<demande>.json
(debut, fait...) around them, the start-up line of the model asked for. With the file refuse-opus,
Opus is refused as Claude Code refuses it to a Pro plan. models gets each run's --model. The base is
HOME, else USERPROFILE (what the app gives Claude on Windows).
"""
import sys

# Run by the app on the field test's VM, it loads the app tests' reader from the repository: no
# bytecode written there (the robot searches the disk for anything the guide left).
sys.dont_write_bytecode = True

import base64  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402

FLAGS = ("--restricted --safe-mode --tools --permission-prompts --json-schema --max-budget-usd "
         "--no-session-persistence --strict-mcp-config --disallowedTools --settings --append-system-prompt "
         "--input-format")
FAKE = os.path.join(os.environ.get("HOME") or os.environ.get("USERPROFILE", ""), ".claude", "fake")
HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "..", "..", "app", "src-tauri", "tests", "fixtures")


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


def read_messages(stream, raw=None):
    """The user messages of stream-json input, one per line (split on newlines only): their text,
    and what images.json says of their images (raw: their bytes, appended there). Anything else stops
    the fake, as Claude Code would refuse it."""
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
                if raw is not None:
                    raw.append(data)
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
    images, pngs = [], []
    if "--input-format" in args and args[args.index("--input-format") + 1:][:1] == ["stream-json"]:
        prompt, images = read_messages(prompt, pngs)
    record("images.json", json.dumps(images))
    if pngs:
        os.makedirs(os.path.join(FAKE, "fragments"), exist_ok=True)
        b64s = [base64.b64encode(png).decode("ascii") for png in pngs]
        record(os.path.join("fragments", "%d.json" % n), json.dumps(
            [{"b64": b[len(b) // 2:len(b) // 2 + 80], "raw": png[len(png) // 2:len(png) // 2 + 64].hex()}
             for b, png in zip(b64s, pngs)]))
    record("prompt.txt", prompt)
    os.makedirs(os.path.join(FAKE, "prompts"), exist_ok=True)
    record(os.path.join("prompts", "%d.txt" % n), prompt)
    model = args[args.index("--model") + 1] if "--model" in args else ""
    with open(os.path.join(FAKE, "models"), "a") as f:
        f.write(model + "\n")
    scenario = setting(os.path.join("scenarios", str(n)), "") or setting("scenario", "plan-ok")
    if scenario == "guide":
        return guide(prompt, pngs, model)
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


def second_reader():
    """The app tests' reader of the band and of the green button (a PNG decoder and a 5x7 font of its own)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("lecteur_bande", os.path.join(FIXTURES, "guide", "fake_claude.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def guide(prompt, pngs, model):
    """One look of Bibo's guide: the answer of guide/<demande>.json, with the band's code read on the image
    and, for "cible": "vert", the green button's box."""
    family = model if model in ("opus", "sonnet") else "opus"
    if family == "opus" and os.path.exists(os.path.join(FAKE, "refuse-opus")):
        with open(os.path.join(FIXTURES, "repair", "pro-sans-opus.jsonl"), encoding="utf-8") as f:
            sys.stdout.buffer.write(f.read().encode("utf-8"))
        return 0
    lecteur = second_reader()
    w, full_h, rows = lecteur.decode_png(pngs[0])
    h = full_h - lecteur.BAND
    demande = prompt.split("<demande>", 1)[1].split("</demande>", 1)[0] if "<demande>" in prompt else "debut"
    with open(os.path.join(FAKE, "guide", demande + ".json"), encoding="utf-8") as f:
        answer = json.load(f)
    answer["code_vu"] = lecteur.read_code(rows, h)
    if answer.get("cible") == "vert":
        answer["cible"] = lecteur.green_box(rows, h)
    name = "claude-%s-5-5" % family
    emit(dict(lecteur.INIT, model=name, claude_code_version=setting("version", "2.1.306 (Claude Code)").split()[0]))
    emit({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "unifiedWindows": {
        "five_hour": {"utilization": 0.2, "resetsAt": 1792544400}, "seven_day": {"utilization": 0.3, "resetsAt": 1793044800}}}})
    emit({"type": "assistant", "message": {"model": name, "content": [
        {"type": "tool_use", "id": "toolu_1", "name": "StructuredOutput", "input": answer}]}})
    emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 2, "total_cost_usd": 0,
          "usage": {"input_tokens": 3, "output_tokens": 420, "cache_read_input_tokens": 3730, "cache_creation_input_tokens": 2000},
          "modelUsage": {name: {"inputTokens": 3}}, "structured_output": answer})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
