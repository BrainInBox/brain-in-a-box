"""The kit's launcher without tools for Claude (engine/hooks/_headless_claude.py) and the app's copy of
it (app/src-tauri/src/claude.rs) must send Claude the same question: the same argv and the same stdin,
byte for byte, images included (docs/proposals/guide.md, §3 and §9).

The Rust test `the_app_and_the_launcher_send_alike` (claude.rs) writes its questions here, on stdin
(JSON: text, images in hex, schema as the JSON text claude.rs sends, system, model, effort, budget_usd,
max_turns). Each one goes through _headless_claude.run, fake_claude.py standing for claude in a home of
its own; what the fake was given comes back on stdout (JSON: argv, stdin's sha256 and size, the text
and the images it read, and how the run ended), for the test to compare with claude.rs's argv() and stdin().

    python3 -I claude_contract.py <dir> <python>    <python> runs fake_claude.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(os.path.dirname(HERE), "hooks")
FIXTURES = os.path.join(os.path.dirname(os.path.dirname(HERE)), "app", "src-tauri", "tests", "fixtures", "repair")
FAKE = os.path.join(HERE, "fake_claude.py")
sys.path.insert(0, HOOKS)

import _headless_claude as hc  # noqa: E402

ANSWER = {"verdict": "ok"}


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def ask(q, root, python):
    """One question through the launcher, in a home of its own: what the fake claude was given."""
    home, work = os.path.join(root, "home"), os.path.join(root, "work")
    fake = os.path.join(home, ".claude", "fake")
    os.makedirs(fake)
    os.makedirs(work)
    write(os.path.join(fake, "fixtures"), FIXTURES)
    write(os.path.join(fake, "answer.json"), json.dumps(ANSWER))
    os.environ["HOME"] = os.environ["USERPROFILE"] = home
    hc.MANAGED_DIR = os.path.join(root, "managed")
    try:
        a = hc.run(q["text"], json.loads(q["schema"]), q["system"], budget_usd=q["budget_usd"], timeout=60,
                   max_turns=q["max_turns"], model=q["model"], effort=q["effort"], command=[python, "-I", FAKE],
                   work_root=work, images=[bytes.fromhex(h) for h in q["images"]])
        outcome = "ok" if a.output == ANSWER else "another answer"
    except hc.HeadlessError as e:
        outcome = e.kind
    return {"argv": json.loads(read(os.path.join(fake, "argv.json"))),
            "stdin": json.loads(read(os.path.join(fake, "stdin.json"))),
            "prompt": read(os.path.join(fake, "prompt.txt")),
            "images": json.loads(read(os.path.join(fake, "images.json"))),
            "outcome": outcome}


def main(base, python):
    questions = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    # Nothing of this machine's login reaches the fake.
    os.environ.pop(hc.TOKEN_VAR, None)
    got = [ask(q, os.path.join(base, str(i)), python) for i, q in enumerate(questions)]
    sys.stdout.buffer.write(json.dumps(got, ensure_ascii=False).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
