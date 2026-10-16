"""The launcher without tools (engine/hooks/_headless_codex.py) with the real Codex CLI, in ChatGPT
mode, against mock_openai.py: fake tokens, no account, no secret, nothing leaves 127.0.0.1
(docs/proposals/codex.md, §5.2); then the reflection's and the review's own questions, through the
choice of engine (_headless.py), with Codex chosen. The job codex-mock of test.yml runs it on macOS, Linux and
Windows once the official installer has put the pinned Codex in place. It writes Codex's system
settings (/etc/codex, %ProgramData%\\OpenAI\\Codex): on a CI runner only. Every scenario is
required: a codex missing, or one that cannot run, is a failure.

    python3 -I engine/tests/codex_mock.py
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ENGINE, "hooks"))
sys.path.insert(0, HERE)

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8", errors="replace")

import _agents  # noqa: E402
import _controle_judge as judge  # noqa: E402
import _headless  # noqa: E402
import _headless_codex as codex  # noqa: E402
import mock_openai  # noqa: E402
from _headless_claude import HeadlessError  # noqa: E402

WINDOWS = os.name == "nt"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdict", "n"],
          "properties": {"verdict": {"type": "string", "enum": ["ok", "ko"]}, "n": {"type": "integer"}}}
SYSTEM = "You are a strict JSON judge. MOCK-SYSTEM-MARKER"
PROMPT = "Judge this day of notes: é à ü 漢字 🙂. MOCK-PROMPT-SENTINEL"
# What Codex 0.162.1 announces under the lock (step 0, the same on the three systems).
TOOLS = {"functions.exec", "functions.exec>apply_patch", "functions.exec>clock__curr_time",
         "functions.exec>list_mcp_resource_templates", "functions.exec>list_mcp_resources",
         "functions.exec>read_mcp_resource", "functions.request_user_input_async", "functions.wait"}
ENDPOINTS = {"GET /v1/models", "GET /v1/responses", "POST /v1/responses", "GET /backend-api/wham/accounts/check",
             "GET /backend-api/wham/settings/user"}
CANARIES = {"OPENAI_API_KEY": "sk-canary-parent-openai-0123456789", "CODEX_API_KEY": "sk-canary-parent-codex-0123456789",
            "CODEX_ACCESS_TOKEN": "canary-parent-access-token", "OPENAI_BASE_URL": "http://127.0.0.1:9/canary"}
FAILURES = []


def check(cond, what):
    print(("ok    " if cond else "FAIL  ") + what, flush=True)
    if not cond:
        FAILURES.append(what)


def scenario(fn):
    print("\n## " + fn.__name__, flush=True)
    try:
        fn()
    except Exception:
        traceback.print_exc()
        FAILURES.append(fn.__name__ + ": " + traceback.format_exc().strip().splitlines()[-1])


class Bench:
    def __init__(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="codex-mock-"))
        self.home = os.path.join(self.root, "codex-home")
        self.markers = os.path.join(self.root, "markers")
        os.makedirs(self.home)
        os.makedirs(self.markers)
        with open(os.path.join(self.home, "auth.json"), "w", encoding="utf-8") as f:
            json.dump(mock_openai.fake_auth(), f)
        self.marker_py = os.path.join(self.root, "marker.py")
        with open(self.marker_py, "w", encoding="utf-8") as f:
            f.write("import pathlib, sys\npathlib.Path(%r, sys.argv[1]).write_text('x')\nsys.exit(1)\n" % self.markers)
        self.codex = codex.find_codex()
        self.mock = mock_openai.Mock(answer='{"verdict":"ok","n":7}')
        self.command = [sys.executable, "-I", os.path.join(HERE, "mock_openai.py"), "shim", self.mock.root,
                        self.codex[0]]

    def marker_argv(self, name):
        return [sys.executable, self.marker_py, name]

    def found_markers(self):
        time.sleep(2)   # notify runs after the turn
        return sorted(os.listdir(self.markers))

    def run(self, **kw):
        return codex.run(PROMPT, SCHEMA, SYSTEM, codex_home=self.home, timeout=180, command=self.command,
                         work_root=self.root, **kw)

    def raw_exec(self, cwd):
        """codex exec as anyone would run it, without the launcher: the control of a trap."""
        argv = [self.codex[0], "exec", "--json", "--skip-git-repo-check", "--ephemeral",
                "-c", 'cli_auth_credentials_store="file"', "-c", "features.enable_request_compression=false",
                "-c", "openai_base_url=" + json.dumps(self.mock.root + "/v1"),
                "-c", "chatgpt_base_url=" + json.dumps(self.mock.root + "/backend-api/"), "-"]
        env = {k: v for k, v in os.environ.items() if k not in CANARIES and k != "RUST_LOG"}
        env["CODEX_HOME"] = self.home
        return subprocess.run(argv, input=b"Reply.", cwd=cwd, env=env, capture_output=True, timeout=180)


B = None


def answers_with_everything_locked():
    B.mock.reset()
    os.environ.update(CANARIES)
    try:
        a = B.run()
    finally:
        for k in CANARIES:
            os.environ.pop(k, None)
    check(a.output == {"verdict": "ok", "n": 7}, "the mock's answer comes back as the parsed object")
    check(a.version == os.environ.get("CODEX_RELEASE", a.version), "version %s" % a.version)
    reqs = B.mock.responses()
    check(len(reqs) == 1, "one request to the model (%d)" % len(reqs))
    body = reqs[0]["body"] if reqs else {}
    check(B.mock.auth_kinds() == ["fake-chatgpt"], "every request carries the ChatGPT login, no key: %s"
          % B.mock.auth_kinds())
    check(set(B.mock.endpoints()) <= ENDPOINTS, "only the fixed URLs: %s" % B.mock.endpoints())
    extra = set(B.mock.tools()) - TOOLS
    check(not extra, "no tool announced beyond step 0's: %s" % sorted(extra))
    fmt = (body.get("text") or {}).get("format") or {}
    check(fmt.get("type") == "json_schema" and fmt.get("strict") is True and fmt.get("schema") == SCHEMA,
          "the answer schema, strict")
    dump = json.dumps(B.mock.reqs, ensure_ascii=False)
    check("MOCK-SYSTEM-MARKER" in dump, "the system prompt reaches the model")
    check(PROMPT in dump, "the prompt reaches the model whole (stdin, UTF-8)")
    for k, v in CANARIES.items():
        check(v not in dump, "%s of the caller's environment reaches nothing" % k)
    left = []
    for d, _, files in os.walk(B.home):
        for name in files:
            with open(os.path.join(d, name), "rb") as f:
                if b"MOCK-PROMPT-SENTINEL" in f.read():
                    left.append(os.path.relpath(os.path.join(d, name), B.home))
    check(not left, "nothing of the prompt left in CODEX_HOME (--ephemeral): %s" % left)
    check(not os.path.isdir(os.path.join(B.home, "sessions")), "no session file")


def forced_tool_calls_are_refused():
    target = os.path.join(B.root, "pwned.txt")
    cmd = ('Set-Content -Path "%s" -Value x' if WINDOWS else "echo x > '%s'") % target
    cases = [("exec (JavaScript)", mock_openai.js_exec_call(cmd), ("code-mode host is disabled",)),
             ("exec_command", mock_openai.fn_call("exec_command", {"cmd": cmd}, "call_fc_1"),
              ("unsupported call: exec_command",)),
             ("shell", mock_openai.fn_call("shell", {"command": ["bash", "-lc", cmd]}, "call_sh_1"),
              ("unsupported call: shell",)),
             ("apply_patch", mock_openai.patch_call(target), ("patch rejected: ",)),
             ("view_image", mock_openai.fn_call("view_image", {"path": B.marker_py}, "call_vi_1"),
              ("unsupported call: view_image",))]
    for name, call, refusals in cases:
        B.mock.reset([call])
        try:
            a = B.run()
            got = a.output
        except HeadlessError as e:
            got = "HeadlessError %s" % e
        outs = B.mock.tool_outputs()
        check(got == {"verdict": "ok", "n": 7}, "%s: the run still answers (the stream hides tools): %s" % (name, got))
        check(len(outs) == 1 and outs[0].startswith(refusals), "%s refused: %s" % (name, [o[:120] for o in outs]))
        check(not os.path.exists(target), "%s wrote nothing" % name)


def an_unknown_feature_pauses_codex():
    with open(os.path.join(ENGINE, "codex-features.json"), encoding="utf-8") as f:
        d = json.load(f)
    fewer = os.path.join(B.root, "fewer-features.json")
    d["coupees"] = [n for n in d["coupees"] if n != "fast_mode"]
    with open(fewer, "w", encoding="utf-8") as f:
        json.dump(d, f)
    try:
        codex.check(B.home, B.command, work_root=B.root, features_file=fewer)
        check(False, "a feature left on (fast_mode) pauses Codex")
    except HeadlessError as e:
        check(e.kind == "unsupported" and e.reason == "features" and "fast_mode" in str(e),
              "a feature left on pauses Codex: %s" % e)
    check(codex.check(B.home, B.command, work_root=B.root) == os.environ.get("CODEX_RELEASE", "0.162.1"),
          "the kit's own list passes this Codex's features list")


def the_user_s_mcp_server_never_starts():
    cfg = os.path.join(B.home, "config.toml")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write("[mcp_servers.usermcp]\ncommand = %s\nargs = %s\n" % (json.dumps(sys.executable),
                                                                     json.dumps([B.marker_py, "usermcp"])))
    try:
        B.mock.reset()
        a = B.run()
        check(a.output == {"verdict": "ok", "n": 7}, "an MCP server in the user's config.toml does not break the call")
        check("usermcp" not in B.found_markers(), "and it does not start")
        work = tempfile.mkdtemp(dir=B.root)
        B.mock.reset()
        B.raw_exec(work)
        check("usermcp" in B.found_markers(), "control: codex exec without the launcher starts it")
    finally:
        os.remove(cfg)
        for m in os.listdir(B.markers):
            os.remove(os.path.join(B.markers, m))


def system_dir():
    if WINDOWS:
        return os.path.join(os.environ.get("ProgramData") or r"C:\ProgramData", "OpenAI", "Codex")
    return "/etc/codex"


def admin_write(path, text):
    if WINDOWS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return
    subprocess.run(["sudo", "-n", "mkdir", "-p", os.path.dirname(path)], check=True)
    subprocess.run(["sudo", "-n", "tee", path], input=text.encode("utf-8"), stdout=subprocess.DEVNULL, check=True)


def admin_rm(path):
    if WINDOWS:
        if os.path.exists(path):
            os.remove(path)
    else:
        subprocess.run(["sudo", "-n", "rm", "-f", path], check=True)


def a_system_config_is_neutralised():
    path = os.path.join(system_dir(), "config.toml")
    text = ('developer_instructions = "SYSTEM-DEV-MARKER"\nnotify = %s\n\n[mcp_servers.sysmcp]\ncommand = %s\n'
            'args = %s\n' % (json.dumps(B.marker_argv("notify")), json.dumps(sys.executable),
                             json.dumps([B.marker_py, "sysmcp"])))
    admin_write(path, text)
    try:
        B.mock.reset()
        a = B.run()
        check(a.output == {"verdict": "ok", "n": 7}, "a system config.toml with an MCP server and notify: answered")
        check(B.found_markers() == [], "its MCP server and its notify program do not run: %s" % B.found_markers())
        check("SYSTEM-DEV-MARKER" not in json.dumps(B.mock.reqs), "its developer instructions are not sent")
        B.mock.reset()
        B.raw_exec(tempfile.mkdtemp(dir=B.root))
        seen = B.found_markers()
        check("sysmcp" in seen and "notify" in seen, "control: without the launcher both run: %s" % seen)
        check("SYSTEM-DEV-MARKER" in json.dumps(B.mock.reqs), "control: and its instructions are sent")
    finally:
        admin_rm(path)
        for m in os.listdir(B.markers):
            os.remove(os.path.join(B.markers, m))


def requirements_pause_codex():
    path = os.path.join(system_dir(), "requirements.toml")
    admin_write(path, 'model_provider = "elsewhere"\n')
    try:
        codex.check(B.home, B.command, work_root=B.root)
        check(False, "requirements.toml pauses Codex")
    except HeadlessError as e:
        check(e.kind == "unsupported" and e.reason == "managed", "requirements.toml pauses Codex: %s" % e)
    finally:
        admin_rm(path)


def an_api_key_login_is_refused():
    p = os.path.join(B.home, "auth.json")
    with open(p, encoding="utf-8") as f:
        good = f.read()
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-canary-apikey-login-0123456789"}, f)
    try:
        B.mock.reset()
        codex.check(B.home, B.command, work_root=B.root)
        check(False, "an API key login is refused")
    except HeadlessError as e:
        check(e.kind == "auth" and e.reason == "billed" and "sk-canary" not in str(e), "an API key login is refused")
    finally:
        with open(p, "w", encoding="utf-8") as f:
            f.write(good)
    check(B.mock.reqs == [], "and nothing was sent")


def the_reflection_and_the_review_ask_codex_through_the_choice():
    """daily-reflection.py's and the review's own schema, instructions and settings, through
    _headless.py with Codex the one chosen (a reading of agents.json given as is: Codex stays
    closed on every system). The real Codex must send each schema strict, as it is."""
    spec = importlib.util.spec_from_file_location("daily_reflection", os.path.join(ENGINE, "hooks", "daily-reflection.py"))
    dr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dr)
    chosen = (_agents.Choix(("codex",), "codex", B.home), "ok")
    day = {"what_i_did": ["Shipped the login page"], "key_decisions": [], "projects": ["app"], "todo": [],
           "memory": ["Login page shipped"], "suspect": False}
    review = {"suspect": False, "constats": [], "lecons": []}
    saved = B.mock.answer
    try:
        B.mock.reset()
        B.mock.answer = json.dumps(day)
        a = _headless.run(dr.SENTINEL + "\n" + PROMPT, dr.SCHEMA, dr.SYSTEM, claude={},
                          codex=dict(timeout=180, command=B.command, work_root=B.root), agents=chosen)
        check((a.moteur, a.output) == ("codex", day), "the reflection's question: Codex answers, named")
        check(dr.clean_answer(a.output)["what_i_did"] == ["Shipped the login page"], "and its answer is the reflection's")
        sent = B.mock.responses()[0]["body"] if B.mock.responses() else {}
        fmt = (sent.get("text") or {}).get("format") or {}
        check(fmt.get("strict") is True and fmt.get("schema") == dr.SCHEMA, "the reflection's schema, strict, as is")
        check(dr.SYSTEM[:60] in json.dumps(B.mock.reqs, ensure_ascii=False), "the reflection's instructions are sent")
        sch = judge.schema(["E01", "E02"], ["L01"])
        B.mock.reset()
        B.mock.answer = json.dumps(review)
        out = judge.ask('Contrôle.\n<extrait id="E01" note="a.md">La base est sur Neon.</extrait>\n', sch,
                        codex_command=B.command, work_root=B.root, agents=chosen)
        check((out.cause, out.moteur, out.answer) == (None, "codex", review),
              "the review's question: Codex answers, named: %s %s" % (out.cause, out.detail))
        sent = B.mock.responses()[0]["body"] if B.mock.responses() else {}
        fmt = (sent.get("text") or {}).get("format") or {}
        check(fmt.get("strict") is True and fmt.get("schema") == sch, "the review's schema, strict, as is")
        effort = json.dumps(sent.get("reasoning") or {})
        check(judge.CODEX_EFFORT in effort, "the review's effort: %s" % effort)
    finally:
        B.mock.answer = saved


def main():
    global B
    if os.environ.get("GITHUB_ACTIONS") != "true":
        sys.exit("codex_mock.py writes Codex's system settings: it runs on a CI runner only")
    B = Bench()
    if not B.codex:
        sys.exit("FAIL  no codex to run (the official installer's place, or PATH)")
    print("codex:", B.codex[0], subprocess.run(B.codex + ["--version"], capture_output=True, text=True).stdout.strip())
    try:
        for fn in (answers_with_everything_locked, forced_tool_calls_are_refused, an_unknown_feature_pauses_codex,
                   the_user_s_mcp_server_never_starts, a_system_config_is_neutralised, requirements_pause_codex,
                   an_api_key_login_is_refused, the_reflection_and_the_review_ask_codex_through_the_choice):
            scenario(fn)
    finally:
        B.mock.close()
        shutil.rmtree(B.root, ignore_errors=True)
    print("\n%d failure(s)" % len(FAILURES) + "".join("\n  - " + f for f in FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
