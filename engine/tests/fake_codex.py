"""A stand-in for `codex`, for test_headless_codex.py. It runs nothing and calls no one.

It answers the probes the way Codex 0.162.1 does (the captures of step 0, docs/proposals/codex.md),
applies the `-c` it is given the way the real one does, and plays `codex exec --json` streams.
Driven by files in $HOME/.codex-fake/ (HOME is one of the few variables the launcher passes on):
    version        what `codex --version` prints (default codex-cli 0.162.1)
    help           a file `codex exec --help` prints (default: the 0.162.1 capture)
    login          the line `codex login status` writes on stderr (default Logged in using ChatGPT)
    features       a `codex features list` capture (default: 0.162.1 on Linux, step 0)
    features-extra lines added to that list (a feature a new version brings)
    features-stuck names whose `-c features.<name>=false` it ignores
    features-user-off  names the user's config.toml turns off: seen only with CODEX_HOME that file's
    features-rc    exit code of `codex features list`
    home           the user's CODEX_HOME: its MCP servers are listed only when CODEX_HOME is that
    mcp-system     JSON list of the MCP servers of the system's config (every CODEX_HOME sees them)
    mcp-user       JSON list of the MCP servers of the user's config.toml
    mcp-stuck      names whose `-c mcp_servers.<name>.enabled=false` it ignores
    plant          JSON [folder, icacls grant]: made, with a config.toml, when `mcp list` reads the user's
                   CODEX_HOME, the last probe (Windows: system settings that appear before the question)
    scenario       what `codex exec` does (default ok), see main()
    answer.json    the answer it gives
Each run appends to calls.jsonl; `exec` also records argv.json, env.json, cwd.json, stdin.txt,
schema.json (the --output-schema file it was given) and system.md (its model_instructions_file).
As the real one, `exec` refuses `-c mcp_servers.<name>...` for a server its layers do not
define: with --ignore-user-config, the user's servers are not among them (config/src/mcp_types.rs,
"invalid transport"); and every command that loads the configuration refuses an empty
model_instructions_file.
"""
import json
import os
import stat
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = os.path.join(os.environ.get("HOME", ""), ".codex-fake")
CAPTURE = os.path.join(HERE, "fixtures", "codex", "phase0", "ubuntu-0.162.1", "features-default.txt")
HELP = os.path.join(HERE, "fixtures", "codex", "exec-help-0.162.1.txt")
CODE_MODE = ("Code Mode is unavailable because code-mode host is disabled. Enable the code_mode_host "
             "feature to use Code Mode.")


def setting(name, default=""):
    try:
        with open(os.path.join(FAKE, name), encoding="utf-8") as f:
            return f.read().strip() or default
    except OSError:
        return default


def listing(name):
    return json.loads(setting(name, "[]"))


def record(name, data):
    with open(os.path.join(FAKE, name), "w", encoding="utf-8") as f:
        f.write(data)


def overrides(args):
    """{key: value as typed} of every -c."""
    out = {}
    for i, a in enumerate(args[:-1]):
        if a in ("-c", "--config"):
            key, _, value = args[i + 1].partition("=")
            out[key.strip()] = value.strip()
    return out


def emit(event):
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def features(c):
    stuck = set(setting("features-stuck").split())
    # `features list` reads the user's config.toml (cli/src/main.rs: LoaderOverrides::default()).
    user_off = set(setting("features-user-off").split()) if os.environ.get("CODEX_HOME") == setting("home") else set()
    with open(setting("features", CAPTURE), encoding="utf-8") as f:
        lines = f.read().splitlines()
    lines += [l for l in setting("features-extra").splitlines() if l.strip()]
    for line in lines:
        parts = line.split()
        name, stage, on = parts[0], " ".join(parts[1:-1]), parts[-1] == "true"
        asked = c.get("features." + name)
        # The removed stage ignores -c, unified_exec stays on (step 0).
        if asked in ("true", "false") and stage != "removed" and name != "unified_exec" and name not in stuck:
            on = asked == "true"
        elif asked is None and name in user_off:
            on = False
        print("%-40s %-18s %s" % (name, stage, "true" if on else "false"))
    return int(setting("features-rc", "0"))


def mcp_list(c):
    names = listing("mcp-system")
    if os.environ.get("CODEX_HOME") == setting("home"):
        names += [n for n in listing("mcp-user") if n not in names]
        if setting("plant"):
            folder, grant = json.loads(setting("plant"))
            folder = os.path.normpath(folder)
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "config.toml"), "w", encoding="utf-8") as f:
                f.write('developer_instructions = "planted"\n')
            subprocess.run(["icacls", folder, "/grant", grant], check=True, capture_output=True)
    stuck = set(listing("mcp-stuck"))
    print(json.dumps([{"name": n, "enabled": c.get("mcp_servers.%s.enabled" % n) != "false" or n in stuck,
                       "disabled_reason": None, "transport": {"type": "stdio", "command": "x"}} for n in names],
                     indent=2))
    return 0


def output_file(args):
    for flag in ("-o", "--output-last-message"):
        if flag in args:
            return args[args.index(flag) + 1]
    return None


def exec_(args, c):
    stdin = sys.stdin.buffer.read()
    with open(os.path.join(FAKE, "stdin.txt"), "wb") as f:
        f.write(stdin)
    record("argv.json", json.dumps(args))
    record("env.json", json.dumps(dict(os.environ)))
    cwd = os.getcwd()
    record("cwd.json", json.dumps({"cwd": cwd, "mode": oct(stat.S_IMODE(os.stat(cwd).st_mode)),
                                   "listing": sorted(os.listdir(cwd))}))
    if "--output-schema" in args:
        with open(args[args.index("--output-schema") + 1], encoding="utf-8") as f:
            record("schema.json", f.read())
    if c.get("model_instructions_file"):
        # Byte for byte: text mode would turn its line ends into the system's.
        with open(json.loads(c["model_instructions_file"]), "rb") as f, open(os.path.join(FAKE, "system.md"), "wb") as out:
            out.write(f.read())
    for key in c:
        if key.startswith("mcp_servers.") and key.split(".")[1] not in listing("mcp-system"):
            sys.stderr.write("Error loading config.toml: invalid transport in `%s`\n" % key.rsplit(".", 1)[0])
            return 1
    scenario = setting("scenario", "ok")
    with open(os.path.join(FAKE, "answer.json"), encoding="utf-8") as f:
        answer = f.read().strip()
    if scenario == "hang":
        # A child of its own, as a tool would start: the group kill must reach it.
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        record("grandchild.pid", str(child.pid))
        time.sleep(60)
        return 0
    if scenario == "orphan":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        record("grandchild.pid", str(child.pid))
    if scenario != "no-thread":
        emit({"type": "thread.started", "thread_id": "019a0000-0000-7000-8000-00000000c0de"})
    if scenario == "reconnecting":
        emit({"type": "error", "message": "Reconnecting... 1/5 (stream disconnected before completion)"})
    emit({"type": "item.completed", "item": {"id": "item_0", "type": "error", "message": CODE_MODE}})
    emit({"type": "turn.started"})
    tool = {"command": {"id": "item_1", "type": "command_execution", "command": "cat ~/.ssh/id_ed25519",
                        "aggregated_output": "", "exit_code": None, "status": "in_progress"},
            "file-change": {"id": "item_1", "type": "file_change", "changes": [{"path": "x", "kind": "add"}],
                            "status": "completed"},
            "unknown-item": {"id": "item_1", "type": "image_view", "path": "/etc/passwd"}}.get(scenario)
    if tool:
        emit({"type": "item.started", "item": tool})
    if scenario == "unknown-event":
        emit({"type": "thread.forked", "thread_id": "x"})
    failed = {"401": "unexpected status 401 Unauthorized: Missing bearer or basic authentication in header",
              "usage-limit": "You’ve hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), "
                             "visit https://chatgpt.com/settings/usage to purchase more credits",
              "api-error": "We’re currently experiencing high demand, which may cause temporary errors."}
    if scenario in failed:
        emit({"type": "error", "message": failed[scenario]})
        emit({"type": "turn.failed", "error": {"message": failed[scenario]}})
        return 1
    if scenario == "no-result":
        return 0
    text = {"not-json": "Voici ma réponse.", "not-object": "[1, 2]"}.get(scenario, answer)
    emit({"type": "item.completed", "item": {"id": "item_2", "type": "agent_message", "text": text}})
    if scenario == "second-turn":
        emit({"type": "turn.started"})
    emit({"type": "turn.completed", "usage": {"input_tokens": 1200, "cached_input_tokens": 0, "output_tokens": 34,
                                              "reasoning_output_tokens": 8}})
    target = output_file(args)
    if target and scenario != "no-file":
        with open(target, "w", encoding="utf-8") as f:
            f.write('{"autre": true}' if scenario == "mismatch" else text)
    return 0


def main(args):
    c = overrides(args)
    words = [a for i, a in enumerate(args) if not a.startswith("-") and (i == 0 or args[i - 1] not in ("-c", "--config"))]
    with open(os.path.join(FAKE, "calls.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": args, "codex_home": os.environ.get("CODEX_HOME")}) + "\n")
    if args == ["--version"]:
        print(setting("version", "codex-cli 0.162.1"))
        return 0
    if args == ["exec", "--help"]:
        with open(setting("help", HELP), encoding="utf-8") as f:
            sys.stdout.write(f.read())
        return 0
    instructions = c.get("model_instructions_file")
    if instructions:
        # As Codex loads its configuration: an empty instructions file is an error (core/src/config/mod.rs).
        with open(json.loads(instructions), encoding="utf-8") as f:
            if not f.read().strip():
                sys.stderr.write("Error loading configuration: model instructions file is empty\n")
                return 1
    if words[:2] == ["login", "status"]:
        line = setting("login", "Logged in using ChatGPT")
        sys.stderr.write(line + "\n")
        return 1 if line.startswith("Not logged in") else 0
    if words[:2] == ["features", "list"]:
        return features(c)
    if words[:2] == ["mcp", "list"] and "--json" in args:
        return mcp_list(c)
    if words[:1] == ["exec"]:
        return exec_(args, c)
    return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
