"""The launcher without tools for Codex (engine/hooks/_headless_codex.py) as scenarios the app's own
copy of it (app/src-tauri/src/codex.rs) must answer the same way: the same refusals and pauses, with
the same words, and the same probes, call, environment, folder, stdin and schema given to Codex.

Each scenario is a home, a CODEX_HOME, the fake Codex's settings (fake_codex.py), an administrator's
layers and a question. The Rust test `the_app_and_the_launcher_answer_alike` (codex.rs) sets them up
here, runs codex.rs on each and has what the fake Codex saw read back here, scenario by scenario;
then it runs the launcher on the same scenarios, set up again in another folder, and compares.

    python3 -I codex_contract.py setup <dir> <python>    the scenarios of this system, set up (JSON)
    python3 -I codex_contract.py record <dir> <index>    what the fake saw, for the outcome on stdin
    python3 -I codex_contract.py run <dir> <python>      the same, through the launcher
    python3 -I codex_contract.py acl                     who may change a folder, decided by the launcher

<python> runs fake_codex.py on both sides: Codex's PATH starts with its folder.
"""
import base64
import contextlib
import hashlib
import json
import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
HOOKS = os.path.join(ENGINE, "hooks")
FAKE = os.path.join(HERE, "fake_codex.py")
FIXTURES = os.path.join(HERE, "fixtures", "codex")
FEATURES = os.path.join(ENGINE, "codex-features.json")
sys.path.insert(0, HOOKS)
sys.path.insert(0, HERE)

import _headless_codex as codex  # noqa: E402
import mock_openai  # noqa: E402
from _headless_claude import HeadlessError  # noqa: E402

WINDOWS = os.name == "nt"
SYSTEM = "windows" if WINDOWS else "macos" if sys.platform == "darwin" else "linux"
UNIX = ("macos", "linux")
CAPTURE = {"macos": "macos", "linux": "ubuntu", "windows": "windows"}[SYSTEM]
FIFO = "<fifo>"

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdict", "items"],
          "properties": {"verdict": {"type": "string", "enum": ["ok", "ko"]},
                         "items": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                              "required": ["t"], "properties": {"t": {"type": "string"}}}}}}
ANSWER = {"verdict": "ok", "items": [{"t": "journée à Montréal ✓"}]}
PROMPT = "Une journée.\n"
SYSTEM_PROMPT = "Tu es un juge strict."
UNIT = "Ligne é à ü ç — 漢字 🙂 «guillemets»\r\nsecond line\n"
BIG = "DEBUT " + UNIT * (150000 // len(UNIT)) + " FIN"
FAKE_KEY = "sk-proj-canari0123456789ABCD"
PARENT_SECRETS = {"OPENAI_API_KEY": "sk-canari-parent-openai", "CODEX_API_KEY": "sk-canari-parent-codex",
                  "CODEX_ACCESS_TOKEN": "canari-access-token", "OPENAI_BASE_URL": "http://canari.invalid/v1",
                  "RUST_LOG": "trace", "VOYAGE_API_KEY": "pa-canari-parent", "ANTHROPIC_API_KEY": "sk-ant-canari",
                  "CODEX_SQLITE_HOME": "/canari", "CODEX_ROLLOUT_TRACE_ROOT": "/canari"}
EXTRA = "brand_new_tool                           stable             true"


def chatgpt(plan="plus", **tokens):
    t = {"id_token": mock_openai.fake_id_token(plan), "access_token": "fake-access", "refresh_token": "fake-refresh",
         "account_id": "acct"}
    t.update(tokens)
    return {"auth_mode": "chatgpt", "OPENAI_API_KEY": "sk-exchanged-by-a-real-chatgpt-login", "tokens": t,
            "last_refresh": "2026-10-15T00:00:00Z"}


def ok(version="0.162.1"):
    return {"answer": ANSWER, "version": version}


def no(kind, reason=None):
    return {"error": [kind, reason]}


def login(state, expect=None):
    """A look at the login: what both find, and what the app tells the user from it."""
    return dict(call="login", login=state, expect=expect or {"login": "ok"})


def help_without(flag):
    with open(os.path.join(FIXTURES, "exec-help-0.162.1.txt"), encoding="utf-8") as f:
        return f.read().replace(flag, flag[:-3] + "xyz")


def features_without(name):
    with open(os.path.join(FIXTURES, "phase0", CAPTURE + "-0.162.1", "features-default.txt"), encoding="utf-8") as f:
        return "".join(l for l in f.read().splitlines(True) if l.split()[:1] != [name])


def plist(d):
    return plistlib.dumps(d)


# expect: what both must answer (None: whatever this machine makes the launcher answer, the app the
# same). os: the systems a scenario runs on. A text value of fake, codex_home_files or files is
# written as is, a dict as JSON, None removes a default, FIFO makes a pipe.
SCENARIOS = [
    # The answer, and what Codex is given for it.
    dict(name="a clean answer", expect=ok()),
    dict(name="nothing asked: the checks only", call="check", expect={"version": "0.162.1"}),
    dict(name="a model and an effort", model="gpt-5.2-codex", effort="medium", expect=ok()),
    dict(name="the prompt, whole, on stdin", prompt=BIG, expect=ok()),
    dict(name="none of the caller's keys or settings", env=dict(PARENT_SECRETS, LANG="fr_FR.UTF-8",
                                                                PATH=os.pathsep.join(["/home/x/evil-bin", "/usr/bin"])),
         expect=ok()),
    dict(name="proxies and their certificate", env={"HTTPS_PROXY": "http://proxy.corp:3128", "no_proxy": "localhost",
                                                    "SSL_CERT_FILE": "/etc/corp-ca.pem",
                                                    "CODEX_CA_CERTIFICATE": "/etc/corp-ca.pem"}, expect=ok()),
    dict(name="no USER: the system's name for the account", env={"USER": None}, os=UNIX, expect=ok()),
    dict(name="a CODEX_HOME written loosely", codex_home="{home}/./.codex//", expect=ok()),
    dict(name="a work folder with accents, a quote and an emoji", work="wörk 'é' ✓🙂", expect=ok()),
    dict(name="a newer Codex", fake={"version": "codex-cli 0.163.0-alpha.11",
                                     "features": os.path.join(FIXTURES, "phase0", CAPTURE + "-0.163.0-alpha.11",
                                                              "features-default.txt")},
         expect=ok("0.163.0-alpha.11")),
    dict(name="a newer Codex, nothing asked", call="check", fake={"version": "codex-cli 0.163.0-alpha.11"},
         expect={"version": "0.163.0-alpha.11"}),
    dict(name="a warning in the stream", fake={"scenario": "reconnecting"}, expect=ok()),
    dict(name="a child left behind", fake={"scenario": "orphan"}, expect=ok()),
    dict(name="the official installer's place", command="found", os=UNIX, expect=ok()),
    dict(name="on the PATH only", command="path", os=UNIX, expect=ok()),
    dict(name="nowhere on this machine", command="nowhere", os=UNIX, expect=no("start")),
    dict(name="a codex.cmd", command=["{root}\\codex.cmd"], os=("windows",), expect=no("start")),
    # The version and the options of the call.
    dict(name="an older Codex", fake={"version": "codex-cli 0.161.9"}, expect=no("unsupported", "version")),
    dict(name="a version the kit cannot read", fake={"version": "something odd"}, expect=no("start")),
    dict(name="an exec without --ignore-user-config", files={"help.txt": help_without("--ignore-user-config")},
         fake={"help": "{root}/help.txt"}, expect=no("unsupported", "flags")),
    dict(name="an exec without --ephemeral, nothing asked", call="check",
         files={"help.txt": help_without("--ephemeral")}, fake={"help": "{root}/help.txt"},
         expect=no("unsupported", "flags")),
    # The login.
    dict(name="an API key login", codex_home_files={"auth.json": {"auth_mode": "apikey", "OPENAI_API_KEY": FAKE_KEY}},
         expect=no("auth", "billed")),
    dict(name="a key without a mode", codex_home_files={"auth.json": {"OPENAI_API_KEY": FAKE_KEY}},
         expect=no("auth", "billed")),
    dict(name="a mode written another way", codex_home_files={"auth.json": dict(chatgpt(), auth_mode="ChatGPT")},
         expect=no("auth", "billed")),
    dict(name="no auth.json", codex_home_files={"auth.json": None}, expect=no("auth", "logged_out")),
    dict(name="an empty auth.json object", codex_home_files={"auth.json": {}}, expect=no("auth", "logged_out")),
    dict(name="an auth.json that is not JSON", codex_home_files={"auth.json": "{not json"},
         expect=no("auth", "logged_out")),
    dict(name="an auth.json that is a list", codex_home_files={"auth.json": [1, 2]}, expect=no("auth", "logged_out")),
    dict(name="an auth.json that is a folder", codex_home_files={"auth.json": None, "auth.json/x": "x"},
         expect=no("auth", "logged_out")),
    dict(name="an auth.json that is a pipe", codex_home_files={"auth.json": FIFO}, os=UNIX,
         expect=no("auth", "logged_out")),
    dict(name="no refresh token", codex_home_files={"auth.json": chatgpt(refresh_token=None)},
         expect=no("auth", "logged_out")),
    dict(name="a blank refresh token", codex_home_files={"auth.json": chatgpt(refresh_token="  ")},
         expect=no("auth", "logged_out")),
    dict(name="tokens that are not an object", codex_home_files={"auth.json": dict(chatgpt(), tokens=None)},
         expect=no("auth", "logged_out")),
    dict(name="a workspace account", codex_home_files={"auth.json": chatgpt("enterprise")},
         expect=no("unsupported", "managed")),
    dict(name="a team account", codex_home_files={"auth.json": chatgpt("team")}, expect=no("unsupported", "managed")),
    dict(name="no plan in the token", codex_home_files={"auth.json": chatgpt(None)},
         expect=no("unsupported", "managed")),
    dict(name="an id_token the kit cannot read", codex_home_files={"auth.json": chatgpt(id_token="x.y.z")},
         expect=no("unsupported", "managed")),
    dict(name="an id_token that is a number", codex_home_files={"auth.json": chatgpt(id_token=42)},
         expect=no("unsupported", "managed")),
    dict(name="a plan written in capitals", codex_home_files={"auth.json": chatgpt("Pro")}, expect=ok()),
    dict(name="the free plan", codex_home_files={"auth.json": chatgpt("free")}, expect=ok()),
    dict(name="login status says an API key", fake={"login": "Logged in using an API key - sk-proj-***ABCD"},
         expect=no("auth", "billed")),
    dict(name="login status says workload identity", fake={"login": "Logged in using workload identity"},
         expect=no("auth", "billed")),
    dict(name="login status says not logged in", fake={"login": "Not logged in"}, expect=no("auth", "logged_out")),
    dict(name="login status says something else", fake={"login": "Logged in using ChatGPTx"},
         expect=no("auth", "logged_out")),
    dict(name="login status shows a key", fake={"login": "Error checking login status: " + FAKE_KEY},
         expect=no("auth", "logged_out")),
    dict(name="no CODEX_HOME folder", codex_home="{root}/absent", expect=no("auth", "logged_out")),
    dict(name="a relative CODEX_HOME", codex_home="relative/.codex", expect=no("auth", "logged_out")),
    # $CODEX_HOME/.env
    dict(name="a .env that sends the question elsewhere",
         codex_home_files={".env": "# a comment\nLANG=fr_FR.UTF-8\nHTTPS_PROXY=http://evil:8080\n"},
         expect=no("unsupported", "dotenv")),
    dict(name="a .env with a key", codex_home_files={".env": "  OPENAI_API_KEY = " + FAKE_KEY + "\n"},
         expect=no("unsupported", "dotenv")),
    dict(name="a .env with RUST_LOG", codex_home_files={".env": "export RUST_LOG=trace\n"},
         expect=no("unsupported", "dotenv")),
    dict(name="a .env with a certificate", codex_home_files={".env": "NODE_EXTRA_CA_CERTS=/tmp/evil.pem\n"},
         expect=no("unsupported", "dotenv")),
    dict(name="a .env with a name of the allow-list that holds CA", codex_home_files={".env": "LOCALAPPDATA=/tmp/x\n"},
         expect=no("unsupported", "dotenv")),
    dict(name="a .env with another name", codex_home_files={".env": "SOMETHING_ELSE=1\n"},
         expect=no("unsupported", "dotenv")),
    dict(name="a .env line without =", codex_home_files={".env": "JUST A LINE\n"}, expect=no("unsupported", "dotenv")),
    dict(name="a .env that starts with a BOM", codex_home_files={".env": "\ufeffLANG=C\n"},
         expect=no("unsupported", "dotenv")),
    dict(name="a .env that is a pipe", codex_home_files={".env": FIFO}, os=UNIX, expect=no("unsupported", "dotenv")),
    dict(name="a .env of harmless names",
         codex_home_files={".env": "# mine\n\nLANG=fr_FR.UTF-8\nCODEX_ANYTHING=ignored-by-codex\nexport LANG=C.UTF-8\n"},
         expect=ok()),
    # An administrator's layers.
    dict(name="a requirements.toml", files={"etc-codex/requirements.toml": 'model_provider = "trap"\n'},
         expect=no("unsupported", "managed")),
    dict(name="a legacy managed_config.toml", files={"etc-codex/managed_config.toml": "[features]\nshell_tool = true\n"},
         expect=no("unsupported", "managed")),
    dict(name="a requirements.toml of comments", files={"etc-codex/requirements.toml": "# nothing yet\n\n   # still\n"},
         expect=ok()),
    dict(name="a requirements.toml that is not UTF-8", files={"etc-codex/requirements.toml": b"\xff\xfe# x\n"},
         expect=no("unsupported", "managed")),
    dict(name="a profile that forces Codex's settings",
         files={"mdm/com.openai.codex.plist": plist({"config_toml_base64": "bW9kZWxfcHJvdmlkZXIgPSAidHJhcCIK"})},
         os=("macos",), expect=no("unsupported", "managed")),
    dict(name="a profile that forces requirements",
         files={"mdm/com.openai.codex.plist": plistlib.dumps({"requirements_toml_base64": b"x"},
                                                             fmt=plistlib.FMT_BINARY)},
         os=("macos",), expect=no("unsupported", "managed")),
    dict(name="a profile of other settings", files={"mdm/com.openai.codex.plist": plist({"other": "x",
                                                                                       "config_toml_base64": ""})},
         os=("macos",), expect=ok()),
    dict(name="a profile the kit cannot read", files={"mdm/com.openai.codex.plist": b"not a plist"},
         os=("macos",), expect=no("unsupported", "managed")),
    dict(name="a profile that is a folder", files={"mdm/com.openai.codex.plist/x": "x"},
         os=("macos",), expect=no("unsupported", "managed")),
    # Windows: %ProgramData%\OpenAI\Codex, as the folders on this machine are.
    dict(name="a system settings folder as made here", files={"programdata/OpenAI/Codex/config.toml": "x = 1\n"},
         os=("windows",), expect=None),
    dict(name="a system settings folder its users may change", files={"programdata/OpenAI/Codex/config.toml": "x = 1\n"},
         icacls=[("programdata/OpenAI", "/grant", "*S-1-5-32-545:(OI)(CI)M")], os=("windows",),
         expect=no("unsupported", "managed")),
    # The order of the checks: the first that refuses says why.
    dict(name="an older Codex with a .env that sends the question elsewhere", fake={"version": "codex-cli 0.161.9"},
         codex_home_files={".env": "HTTPS_PROXY=http://evil:8080\n"}, expect=no("unsupported", "version")),
    dict(name="a .env that sends the question elsewhere, and a requirements.toml",
         codex_home_files={".env": "HTTPS_PROXY=http://evil:8080\n"},
         files={"etc-codex/requirements.toml": 'model_provider = "trap"\n'}, expect=no("unsupported", "dotenv")),
    dict(name="a requirements.toml, and an API key login", files={"etc-codex/requirements.toml": 'model_provider = "x"\n'},
         codex_home_files={"auth.json": {"auth_mode": "apikey", "OPENAI_API_KEY": FAKE_KEY}},
         expect=no("unsupported", "managed")),
    dict(name="an API key in auth.json, and login status says ChatGPT",
         codex_home_files={"auth.json": {"auth_mode": "apikey", "OPENAI_API_KEY": FAKE_KEY}}, expect=no("auth", "billed")),
    dict(name="login status says an API key, and a feature Codex brings on",
         fake={"login": "Logged in using an API key - sk-proj-***ABCD", "features-extra": EXTRA},
         expect=no("auth", "billed")),
    dict(name="a feature Codex brings on, and an MCP server from a layer the kit cannot name",
         fake={"features-extra": EXTRA, "mcp-user": '["cloud-tool"]'}, expect=no("unsupported", "features")),
    dict(name="a system settings folder its users may change, and an older Codex", fake={"version": "codex-cli 0.1.0"},
         files={"programdata/OpenAI/Codex/config.toml": "x = 1\n"},
         icacls=[("programdata/OpenAI", "/grant", "*S-1-5-32-545:(OI)(CI)M")], os=("windows",),
         expect=no("unsupported", "managed")),
    dict(name="a system settings folder that appears after the probes",
         fake={"plant": ["{root}/programdata/OpenAI/Codex", "*S-1-5-32-545:(OI)(CI)M"]}, os=("windows",),
         expect=no("unsupported", "managed")),
    # Codex's features.
    dict(name="a feature Codex brings on", fake={"features-extra": EXTRA}, expect=no("unsupported", "features")),
    dict(name="a feature at the removed stage", fake={"features-extra": "old_thing  removed  true"}, expect=ok()),
    dict(name="a feature that stays on", fake={"features-stuck": "image_generation"},
         expect=no("unsupported", "features")),
    dict(name="features list fails", fake={"features-rc": "1"}, expect=no("unsupported", "features")),
    dict(name="a feature only the user's config.toml turns off",
         fake={"features-extra": EXTRA, "features-user-off": "brand_new_tool"}, expect=no("unsupported", "features")),
    dict(name="a feature the kit turns off that Codex does not list", files={"features.txt": features_without("hooks")},
         fake={"features": "{root}/features.txt"}, expect=no("unsupported", "features")),
    dict(name="a line of features list the kit cannot read", fake={"features-extra": "lonely thing"},
         expect=no("unsupported", "features")),
    # MCP servers.
    dict(name="a system MCP server, turned off by name", fake={"mcp-system": '["corp-search"]'}, expect=ok()),
    dict(name="the user's MCP server, left to --ignore-user-config", fake={"mcp-user": '["my-notes"]'},
         codex_home_files={"config.toml": '[mcp_servers.my-notes]\ncommand = "notes-mcp"\n'}, expect=ok()),
    dict(name="the user's MCP server in a table",
         fake={"mcp-user": '["my-notes"]'},
         codex_home_files={"config.toml": '[mcp_servers]\nmy-notes = { command = "notes-mcp" }\n'}, expect=ok()),
    dict(name="an MCP server from a layer the kit cannot name", fake={"mcp-user": '["cloud-tool"]'},
         expect=no("unsupported", "mcp")),
    dict(name="an MCP server named in a comment only", fake={"mcp-user": '["cloud-tool"]'},
         codex_home_files={"config.toml": 'model = "o3"  # mcp_servers.cloud-tool.command = "x"\n'},
         expect=no("unsupported", "mcp")),
    dict(name="an MCP server that stays on", fake={"mcp-system": '["corp-search"]', "mcp-stuck": '["corp-search"]'},
         expect=no("unsupported", "mcp")),
    dict(name="an MCP server name Codex cannot take", fake={"mcp-system": '["corp.search"]'},
         expect=no("unsupported", "mcp")),
    # The stream.
    dict(name="a command run", fake={"scenario": "command"}, expect=no("init")),
    dict(name="a file changed", fake={"scenario": "file-change"}, expect=no("init")),
    dict(name="an item the kit does not know", fake={"scenario": "unknown-item"}, expect=no("init")),
    dict(name="an event the kit does not know", fake={"scenario": "unknown-event"}, expect=no("init")),
    dict(name="a second turn", fake={"scenario": "second-turn"}, expect=no("init")),
    dict(name="no thread.started", fake={"scenario": "no-thread"}, expect=no("init")),
    dict(name="a 401", fake={"scenario": "401"}, expect=no("auth")),
    dict(name="the usage limit", fake={"scenario": "usage-limit"}, expect=no("quota")),
    dict(name="the service failing", fake={"scenario": "api-error"}, expect=no("api")),
    dict(name="no result", fake={"scenario": "no-result"}, expect=no("api")),
    dict(name="an answer that is not JSON", fake={"scenario": "not-json"}, expect=no("invalid")),
    dict(name="an answer that is not an object", fake={"scenario": "not-object"}, expect=no("invalid")),
    dict(name="no answer file", fake={"scenario": "no-file"}, expect=no("invalid")),
    dict(name="an answer file that is not the answer", fake={"scenario": "mismatch"}, expect=no("invalid")),
    dict(name="a Codex that hangs", fake={"scenario": "hang"}, timeout=3, expect=no("timeout")),
    # What the call cannot take, refused before anything runs.
    dict(name="a schema Codex would refuse", schema={"type": "object", "properties": {"a": {"type": "string"}},
                                                    "required": ["a"]}, expect=no("value")),
    dict(name="blank instructions", system=" \n", expect=no("value")),
    dict(name="a model that is not a name", model="x; rm -rf ~", expect=no("value")),
    dict(name="an effort that is not a word", effort='low"\nnotify=["sh"]', expect=no("value")),
    # The login, as the app shows it (§4.3): auth.json, then `codex login status`, never its line.
    dict(name="the login: ChatGPT", **login("chatgpt")),
    dict(name="the login: the free plan", codex_home_files={"auth.json": chatgpt("free")}, **login("chatgpt")),
    dict(name="the login: an API key in auth.json",
         codex_home_files={"auth.json": {"auth_mode": "apikey", "OPENAI_API_KEY": FAKE_KEY}},
         **login("billed", no("auth", "billed"))),
    dict(name="the login: login status says an API key", fake={"login": "Logged in using an API key - sk-proj-***ABCD"},
         **login("billed", no("auth", "billed"))),
    dict(name="the login: no auth.json", codex_home_files={"auth.json": None},
         **login("logged_out", no("auth", "logged_out"))),
    dict(name="the login: login status says not logged in", fake={"login": "Not logged in"},
         **login("logged_out", no("auth", "logged_out"))),
    dict(name="the login: login status shows a key", fake={"login": "Error checking login status: " + FAKE_KEY},
         **login("logged_out", no("auth", "logged_out"))),
    dict(name="the login: a workspace account", codex_home_files={"auth.json": chatgpt("enterprise")},
         **login("managed", no("unsupported", "managed"))),
    dict(name="the login: no CODEX_HOME folder", codex_home="{root}/absent",
         **login("logged_out", no("auth", "logged_out"))),
    dict(name="the login: no Codex", command="nowhere", os=UNIX, **login("unknown", no("start"))),
    dict(name="the login: a codex.cmd", command=["{root}\\codex.cmd"], os=("windows",),
         **login("unknown", no("start"))),
    dict(name="the login: a system settings folder its users may change",
         files={"programdata/OpenAI/Codex/config.toml": "x = 1\n"},
         icacls=[("programdata/OpenAI", "/grant", "*S-1-5-32-545:(OI)(CI)M")], os=("windows",),
         **login("managed", no("unsupported", "managed"))),
]


def _text(value, p):
    for key in ("root", "home", "codex_home"):
        value = value.replace("{%s}" % key, p[key])
    return value


def _deep(value, p):
    if isinstance(value, str):
        return _text(value, p)
    if isinstance(value, list):
        return [_deep(v, p) for v in value]
    if isinstance(value, dict):
        return {k: _deep(v, p) for k, v in value.items()}
    return value


def _put(path, value, p):
    if value is None:
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.lexists(path):
            os.remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if value == FIFO:
        if os.path.lexists(path):
            os.remove(path)
        os.mkfifo(path)
    elif isinstance(value, bytes):
        with open(path, "wb") as f:
            f.write(value)
    elif isinstance(value, str):
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(_text(value, p))
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_deep(value, p), f)


def _wrapper(path, python):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\nexec '%s' -I '%s' \"$@\"\n" % (python, FAKE))
    os.chmod(path, 0o755)


def scenarios():
    return [(i, s) for i, s in enumerate(SCENARIOS) if SYSTEM in s.get("os", ("macos", "linux", "windows"))]


def setup(base, python):
    """Each scenario of this system in a folder of its own under base; what the app needs to run it."""
    out = []
    base = os.path.realpath(base)
    for i, s in scenarios():
        root = os.path.join(base, str(i))
        home = os.path.join(root, "home")
        p = {"index": i, "name": s["name"], "root": root, "home": home, "codex_home": os.path.join(home, ".codex")}
        work = os.path.join(root, s.get("work", "work"))
        for d in (p["codex_home"], os.path.join(home, ".codex-fake"), work):
            os.makedirs(d)
        _put(os.path.join(p["codex_home"], "auth.json"), chatgpt(), p)
        fake = {"answer.json": json.dumps(ANSWER, ensure_ascii=False), "home": p["codex_home"]}
        fake.update(s.get("fake", {}))
        for name, value in fake.items():
            _put(os.path.join(home, ".codex-fake", name), value, p)
        for name, value in s.get("codex_home_files", {}).items():
            _put(os.path.join(p["codex_home"], *name.split("/")), value, p)
        for name, value in s.get("files", {}).items():
            _put(os.path.join(root, *name.split("/")), value, p)
        for where, *args in s.get("icacls", ()):
            subprocess.run(["icacls", os.path.join(root, *where.split("/"))] + args, check=True, capture_output=True)
        env = {"HOME": home}
        if WINDOWS:
            env.update({k: os.environ[k] for k in codex.WINDOWS_VARS if os.environ.get(k)})
            env.update({"USERPROFILE": home, "APPDATA": os.path.join(home, "AppData", "Roaming"),
                        "LOCALAPPDATA": os.path.join(home, "AppData", "Local")})
        else:
            env["USER"] = "jo"
        for k, v in s.get("env", {}).items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
        command = s.get("command", "fake")
        if command == "fake":
            command = [python, "-I", FAKE]
        elif command == "found":
            _wrapper(os.path.join(home, ".local", "bin", "codex"), python)
            command, env["PATH"] = None, os.path.join(root, "empty")
        elif command == "path":
            _wrapper(os.path.join(root, "bin", "codex"), python)
            command, env["PATH"] = None, os.path.join(root, "bin")
        elif command == "nowhere":
            found = shutil.which("codex", path=os.pathsep.join(codex.EXTRA_DIRS))
            if found:
                # Both sides look there too: they would run it, with this scenario's fake login.
                raise SystemExit("%s is a real codex: the scenario %r would run it" % (found, s["name"]))
            command, env["PATH"] = None, os.path.join(root, "empty")
        else:
            command = [_text(c, p) for c in command]
        p.update({"call": s.get("call", "run"), "codex_home": _text(s.get("codex_home", "{codex_home}"), p),
                  "work_root": work, "features_file": FEATURES, "env": env, "command": command,
                  "managed": [os.path.join(root, "etc-codex", n) for n in ("requirements.toml", "managed_config.toml")],
                  "mdm": [os.path.join(root, "mdm", "com.openai.codex.plist")],
                  "program_data": os.path.join(root, "programdata"),
                  "prompt": s.get("prompt", PROMPT), "schema": s.get("schema", SCHEMA),
                  "system": s.get("system", SYSTEM_PROMPT), "model": s.get("model"), "effort": s.get("effort"),
                  "timeout": s.get("timeout", 60), "expect": s["expect"], "login": s.get("login")})
        out.append(p)
    return out


# --------------------------------------------------------------------------- what the fake saw

CAGE = re.compile(r"brain-codex-[A-Za-z0-9_]+")


def _norm(value, root):
    if isinstance(value, dict):
        return {_norm(k, root): _norm(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [_norm(v, root) for v in value]
    if not isinstance(value, str):
        return value
    value = CAGE.sub("brain-codex-X", value)
    # As a path, and as a TOML or JSON string holds it (model_instructions_file="C:\\...").
    for form in (json.dumps(root)[1:-1], root):
        value = value.replace(form, "<root>")
    return value


def _read(path, binary=False):
    try:
        with open(path, "rb" if binary else "r", **({} if binary else {"encoding": "utf-8"})) as f:
            return f.read()
    except OSError:
        return None


def record(p, outcome):
    """What the fake Codex was given, the outcome and what is left in the work folder, paths written
    from the scenario's folder."""
    fake = os.path.join(p["home"], ".codex-fake")
    calls = _read(os.path.join(fake, "calls.jsonl"))
    stdin = _read(os.path.join(fake, "stdin.txt"), binary=True)
    schema = _read(os.path.join(fake, "schema.json"))
    seen = {"calls": [json.loads(l) for l in (calls or "").splitlines() if l.strip()]}
    for name in ("argv", "env", "cwd"):
        text = _read(os.path.join(fake, name + ".json"))
        seen[name] = json.loads(text) if text is not None else None
    if seen["env"] is not None:
        # Set by macOS and Python in the fake itself, whoever starts it.
        for k in ("__CF_USER_TEXT_ENCODING", "LC_CTYPE"):
            seen["env"].pop(k, None)
    seen["stdin"] = None if stdin is None else {"bytes": len(stdin), "sha256": hashlib.sha256(stdin).hexdigest()}
    seen["schema"] = None if schema is None else json.loads(schema)
    seen["left"] = sorted(os.listdir(p["work_root"]))
    seen["grandchild"] = _grandchild(os.path.join(fake, "grandchild.pid"))
    seen["outcome"] = outcome
    return _norm(seen, p["root"])


def _grandchild(pid_file):
    """What a child the fake Codex started became once the call was over: "gone" when its group was
    killed with it (the hang, and the child it left behind), stopped here otherwise. POSIX only:
    Windows has no group, and taskkill /T needs the program alive to find its tree."""
    text = _read(pid_file)
    if text is None or WINDOWS:
        return None
    pid = int(text)
    for _ in range(60):
        try:
            os.kill(pid, 0)
        except OSError:
            return "gone"
        time.sleep(0.05)
    os.kill(pid, signal.SIGKILL)
    return "alive"


# --------------------------------------------------------------------------- the launcher

@contextlib.contextmanager
def _as(p):
    saved = (dict(os.environ), codex.MANAGED, codex.MDM, codex.PROGRAM_DATA)
    os.environ.clear()
    os.environ.update(p["env"])
    codex.MANAGED, codex.MDM, codex.PROGRAM_DATA = p["managed"], p["mdm"], p["program_data"]
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved[0])
        codex.MANAGED, codex.MDM, codex.PROGRAM_DATA = saved[1:]


def look_at_login(p):
    """The login as the app reads it (codex.rs, `login`), made of the launcher's own checks: the
    system settings' folder, the binary, CODEX_HOME, auth.json, then `codex login status` with
    every -c of a call."""
    codex.check_system_settings()
    argv0 = codex._codex(p["command"])
    home = codex._codex_home(p["codex_home"])
    codex.check_auth_file(home)
    coupees = codex.load_features(p["features_file"])
    with codex._cage(argv0, home, p["work_root"]) as cage:
        codex._write(cage.system, "Answer only with the JSON object of the schema.")
        codex.check_login(argv0, cage.env, cage.work, codex.config_flags(coupees, (), cage.system))


def launch(p):
    """The launcher's outcome, written as the app writes its own."""
    try:
        with _as(p):
            if p["call"] == "check":
                return {"version": codex.check(p["codex_home"], command=p["command"], work_root=p["work_root"],
                                               features_file=p["features_file"])}
            if p["call"] == "login":
                look_at_login(p)
                return {"login": "ok"}
            a = codex.run(p["prompt"], p["schema"], p["system"], codex_home=p["codex_home"], timeout=p["timeout"],
                          model=p["model"], effort=p["effort"], command=p["command"], work_root=p["work_root"],
                          features_file=p["features_file"])
            return {"answer": a.output, "version": a.version, "num_turns": a.num_turns, "tokens": a.tokens}
    except HeadlessError as e:
        return {"error": [e.kind, e.reason, e.detail]}
    except ValueError as e:
        return {"error": ["value", None, str(e)]}


# --------------------------------------------------------------------------- Windows' access lists

SIDS = {"system": "S-1-5-18", "admins": "S-1-5-32-544", "users": "S-1-5-32-545", "everyone": "S-1-1-0",
        "authenticated": "S-1-5-11", "interactive": "S-1-5-4", "creator_owner": "S-1-3-0", "owner_rights": "S-1-3-4",
        "trusted_installer": "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",
        "a_user": "S-1-5-21-1004336348-1177238915-682003330-1001"}
FULL, MODIFY, READ_EXECUTE = 0x1F01FF, 0x1301BF, 0x1200A9
LOCKED = [(0, 0x3, FULL, SIDS["system"]), (0, 0x3, FULL, SIDS["admins"]), (0, 0x3, READ_EXECUTE, SIDS["users"]),
          (0, 0xB, FULL, SIDS["creator_owner"])]


def acl_cases():
    """[owner, access list, child_files, what the launcher says]: every owner, and an entry of each
    kind, flags, rights and account added to a locked folder's list, for the folder and its files."""
    masks = (0, 0x1, 0x2, 0x4, 0x8, 0x10, 0x20, 0x40, 0x80, 0x100, 0x10000, 0x20000, 0x40000, 0x80000, 0x100000,
             READ_EXECUTE, MODIFY, FULL, 0x6, 0x10000000, 0x20000000, 0x40000000, 0x80000000, 0xA0000000)
    cases = []
    for owner in SIDS.values():
        for aces in (LOCKED, None, []):
            cases.append([owner, aces])
    for kind in (0, 1, 5, 6, 9, 10, 11, 12, 17):
        for flags in (0, 0x1, 0x2, 0x3, 0x8, 0x9, 0xA, 0xB, 0x10, 0x13, 0x1B):
            for mask in masks:
                for sid in SIDS.values():
                    cases.append([SIDS["admins"], LOCKED + [(kind, flags, mask, sid if kind in (0, 1) else None)]])
    out = []
    for owner, aces in cases:
        for child in (False, True):
            out.append([owner, aces, child, codex.unsafe(owner, aces, child_files=child)])
    return out


def sid_cases():
    """[a SID's bytes in hex, its text or None]: as Windows writes them, and broken ones."""
    def raw(authority, *subs):
        return bytes([1, len(subs)]) + authority.to_bytes(6, "big") + b"".join(x.to_bytes(4, "little") for x in subs)
    good = [raw(5, 32, 544), raw(5, 18), raw(1, 0), raw(3, 0), raw(5, 21, 1004336348, 1177238915, 682003330, 1001),
            raw(5, 80, 956008885, 3418522649, 1831038044, 1853292631, 2271478464), raw(0xFFFFFFFFFFFF, 0xFFFFFFFF),
            raw(15, 3, 1024, 2, 4294967295)]
    bad = [b"", raw(5, 18)[:7], raw(5, 32, 544)[:12], b"\x02" + raw(5, 18)[1:], b"\x01\x05" + raw(5, 18)[2:]]
    out = []
    for b in good + bad:
        try:
            out.append([b.hex(), codex._sid(b)])
        except ValueError:
            out.append([b.hex(), None])
    return out


def main(argv):
    if len(argv) == 3 and argv[0] == "setup":
        out = setup(argv[1], argv[2])
        with open(os.path.join(argv[1], "params.json"), "w", encoding="utf-8") as f:
            json.dump(out, f)
    elif len(argv) == 3 and argv[0] == "record":
        with open(os.path.join(argv[1], "params.json"), encoding="utf-8") as f:
            params = {p["index"]: p for p in json.load(f)}
        out = record(params[int(argv[2])], json.loads(sys.stdin.buffer.read().decode("utf-8")))
    elif len(argv) == 3 and argv[0] == "run":
        out = [record(p, launch(p)) for p in setup(argv[1], argv[2])]
    elif argv == ["acl"]:
        out = {"acl": acl_cases(), "sid": sid_cases()}
    else:
        sys.stderr.write(__doc__)
        return 2
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
