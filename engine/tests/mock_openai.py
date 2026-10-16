"""A local stand-in for OpenAI's servers, for the real Codex CLI in ChatGPT mode: no account, no
secret, nothing leaves 127.0.0.1. From step 0's probe (commit 0c90cc1 of feat/codex).

    mock = Mock()                  # serves http://127.0.0.1:<port>
    mock.reset([fn_call(...)])     # the next model replies: forced tool calls, then the answer
    mock.responses(), mock.tools(), mock.tool_outputs(), mock.endpoints(), mock.auth_kinds()

It answers POST .../responses as an SSE stream (one queued item, else the answer as a message),
426 to the GET a websocket would start (else 5 reconnections, ~7 s), and 404 to everything else.
It reads bodies only uncompressed: the launcher turns enable_request_compression off.

As a wrapper standing for codex (`command=` of _headless_codex.run), it sends `codex exec` to the
mock and changes nothing else:

    python mock_openai.py shim <mock root> <codex> [codex arguments...]

Its URLs go after every -c of the call (the last one wins), before the prompt `-`.
"""
import base64
import datetime
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ANSWER = '{"verdict":"ok","n":1}'


def read_body(h):
    if (h.headers.get("Transfer-Encoding") or "").lower() == "chunked":
        data = b""
        while True:
            size = int(h.rfile.readline().strip().split(b";")[0] or b"0", 16)
            if size == 0:
                h.rfile.readline()
                break
            data += h.rfile.read(size)
            h.rfile.readline()
        return data
    n = int(h.headers.get("Content-Length") or 0)
    return h.rfile.read(n) if n else b""


def is_responses(path):
    return path.split("?")[0].rstrip("/").endswith("/responses")


class Mock:
    def __init__(self, answer=ANSWER):
        self.answer, self.reqs, self.calls, self.lock = answer, [], [], threading.Lock()
        mock = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _rec(self, body):
                auth = self.headers.get("Authorization", "")
                kind = None
                if auth:
                    kind = "fake-chatgpt" if auth == "Bearer " + ACCESS_TOKEN else "other(len=%d)" % len(auth)
                with mock.lock:
                    mock.reqs.append({"method": self.command, "path": self.path, "auth": kind,
                                      "enc": self.headers.get("Content-Encoding"), "body": body})

            def _empty(self, code):
                self.send_response(code)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                self._rec(None)
                self._empty(426 if is_responses(self.path) else 404)

            do_DELETE = do_PUT = do_GET

            def do_POST(self):
                raw = read_body(self)
                try:
                    body = json.loads(raw.decode("utf-8"))
                except ValueError:
                    body = None
                self._rec(body)
                if not is_responses(self.path) or not isinstance(body, dict):
                    return self._empty(404)
                with mock.lock:
                    item = mock.calls.pop(0) if mock.calls else None
                    rid = "resp_mock_%d" % len(mock.reqs)
                if item is None:
                    item = {"type": "message", "role": "assistant", "id": "msg_" + rid,
                            "content": [{"type": "output_text", "text": mock.answer}]}
                evs = [{"type": "response.created", "response": {"id": rid}},
                       {"type": "response.output_item.done", "item": item},
                       {"type": "response.completed", "response": {"id": rid, "usage": {
                           "input_tokens": 11, "input_tokens_details": None, "output_tokens": 3,
                           "output_tokens_details": None, "total_tokens": 14}}}]
                data = "".join("event: %s\ndata: %s\n\n" % (e["type"], json.dumps(e)) for e in evs).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.root = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def reset(self, calls=()):
        with self.lock:
            self.reqs.clear()
            self.calls[:] = list(calls)

    def responses(self):
        return [r for r in self.reqs if r["method"] == "POST" and is_responses(r["path"])]

    def endpoints(self):
        return sorted({"%s %s" % (r["method"], r["path"].split("?")[0]) for r in self.reqs})

    def auth_kinds(self):
        return sorted({r["auth"] for r in self.reqs if r["auth"]})

    def tools(self):
        """Every tool the requests announced, `exec`'s nested ones as exec>name."""
        names = set()
        for r in self.responses():
            if isinstance(r["body"], dict):
                names |= set(flat_tools(r["body"]))
        return sorted(names)

    def tool_outputs(self):
        """The outputs of tool calls Codex sent back, once each."""
        outs = []
        for r in self.responses():
            for it in (r["body"] or {}).get("input") or []:
                if isinstance(it, dict) and str(it.get("type", "")).endswith("_output"):
                    out = it.get("output")
                    out = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
                    if out not in outs:
                        outs.append(out)
        return outs


def flat_tools(body):
    names = set()

    def add(t, ns=None):
        if not isinstance(t, dict):
            return
        n = t.get("name") or t.get("type") or "?"
        full = ns + "." + n if ns else n
        if t.get("tools"):
            for x in t["tools"]:
                add(x, full)
            return
        names.add(full)
        if n == "exec":
            for m in re.findall(r"^### `([A-Za-z0-9_\-]+)`", t.get("description") or "", re.M):
                names.add(full + ">" + m)

    for t in body.get("tools") or []:
        add(t)
    for it in body.get("input") or []:
        if isinstance(it, dict) and it.get("type") == "additional_tools":
            for t in it.get("tools") or []:
                add(t)
    return sorted(names)


# --------------------------------------------------------------------------- a ChatGPT login, fake

ACCESS_TOKEN = "fake-access-token"


def _b64(o):
    return base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")


def fake_id_token(plan="plus"):
    """An unsigned id_token as a ChatGPT login stores it; `plan` in the claim Codex reads (None: no plan)."""
    claim = {"chatgpt_account_id": "acct-fake", "chatgpt_user_id": "user-fake"}
    if plan is not None:
        claim["chatgpt_plan_type"] = plan
    return (_b64({"alg": "none", "typ": "JWT"}) + "." + _b64({"email": "mock@example.com",
                                                              "https://api.openai.com/auth": claim}) + ".c2lnbmF0dXJl")


def fake_auth(plan="plus"):
    """auth.json as a ChatGPT login writes it, with fake tokens (an unsigned id_token)."""
    return {"auth_mode": "chatgpt", "OPENAI_API_KEY": None,
            "tokens": {"id_token": fake_id_token(plan), "access_token": ACCESS_TOKEN,
                       "refresh_token": "fake-refresh-token", "account_id": "acct-fake"},
            "last_refresh": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")}


# --------------------------------------------------------------------------- forced tool calls

def js_exec_call(cmd, cid="call_js_1"):
    js = "const r = await tools.exec_command({cmd: " + json.dumps(cmd) + ", login: false});\ntext(r);"
    return {"type": "custom_tool_call", "call_id": cid, "namespace": "functions", "name": "exec", "input": js}


def fn_call(name, args, cid, namespace=None):
    c = {"type": "function_call", "call_id": cid, "name": name, "arguments": json.dumps(args)}
    if namespace:
        c["namespace"] = namespace
    return c


def patch_call(path):
    return {"type": "custom_tool_call", "call_id": "call_patch_1", "name": "apply_patch",
            "input": "*** Begin Patch\n*** Add File: %s\n+x\n*** End Patch\n" % path}


# --------------------------------------------------------------------------- the wrapper

def shim(root, codex, args):
    if args[:1] == ["exec"] and args[-1:] == ["-"]:
        args = args[:-1] + ["-c", "openai_base_url=" + json.dumps(root + "/v1"),
                            "-c", "chatgpt_base_url=" + json.dumps(root + "/backend-api/"), "-"]
    if os.name == "nt":
        # Inherits the pipes; the launcher's taskkill /T reaches codex through this process.
        return subprocess.call([codex] + args)
    os.execv(codex, [codex] + args)


if __name__ == "__main__":
    if sys.argv[1:2] == ["shim"] and len(sys.argv) >= 4:
        sys.exit(shim(sys.argv[2], sys.argv[3], sys.argv[4:]))
    sys.exit("usage: mock_openai.py shim <mock root> <codex> [codex arguments...]")
