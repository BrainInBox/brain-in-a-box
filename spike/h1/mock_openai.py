"""Faux fournisseur compatible OpenAI pour la preuve H1 (jetable).

Enregistre chaque requête (corps entier, en-tête Authorization réduit à sa présence) dans un
fichier JSONL et répond un JSON fixe, le contrat du guide. Trois cas scriptés :
- la question du relecteur « smart » des approbations : il répond APPROVE ;
- un message utilisateur qui contient H1-PROVOKE, sans résultat d'outil encore : il demande
  l'outil `terminal` avec la commande fournie au démarrage ;
- sinon : le JSON du guide.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ANSWER = {
    "code_vu": "K7Q2", "etat": "etape", "consigne": "Clique sur Nouveau.", "libelle": "Nouveau",
    "cible": {"x": 100, "y": 40, "largeur": 80, "hauteur": 24}, "confiance": 0.9, "question": None,
    "choix": [], "precedente": "sans_objet", "action_sensible": False, "suspect": False,
    "resume_etape": "Ouvrir une nouvelle facture", "element": 7,
}
SMART_QUESTION = "Respond with exactly one word: APPROVE, DENY, or ESCALATE"


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


class Mock:
    def __init__(self, log_path, provoke_command=""):
        self.log_path = log_path
        self.provoke_command = provoke_command
        self.lock = threading.Lock()
        self.records = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def snapshot(self):
        with self.lock:
            return list(self.records)

    def _record(self, rec):
        with self.lock:
            self.records.append(rec)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")

    def _decide(self, body):
        raw = json.dumps(body)
        if SMART_QUESTION in raw:
            return "text", "APPROVE"
        messages = body.get("messages") or []
        if messages and messages[-1].get("role") == "tool":
            return "text", json.dumps(ANSWER, ensure_ascii=False)
        provoked = any(m.get("role") == "user" and "H1-PROVOKE" in _text(m.get("content")) for m in messages)
        if provoked and self.provoke_command:
            return "tool", self.provoke_command
        return "text", json.dumps(ANSWER, ensure_ascii=False)

    def _handler(self):
        mock = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _rec(self, body_bytes, body, kind):
                headers = {k: v for k, v in self.headers.items() if k.lower() != "authorization"}
                mock._record({
                    "t": time.time(), "method": self.command, "path": self.path, "kind": kind,
                    "auth_present": "authorization" in {k.lower() for k in self.headers.keys()},
                    "headers": headers, "body_len": len(body_bytes), "body": body,
                })

            def do_GET(self):
                self._rec(b"", None, "get")
                if self.path.rstrip("/").endswith("/models"):
                    self._send(200, {"object": "list", "data": [
                        {"id": "mock-model", "object": "model", "owned_by": "h1", "context_length": 200000}]})
                else:
                    self._send(404, {"error": {"message": "not found"}})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body_bytes = self.rfile.read(n)
                try:
                    body = json.loads(body_bytes)
                except Exception:
                    body = {"_raw": body_bytes.decode("utf-8", "replace")[:2000]}
                if not self.path.rstrip("/").endswith("/chat/completions"):
                    self._rec(body_bytes, body, "unknown")
                    self._send(404, {"error": {"message": "not implemented"}})
                    return
                kind, payload = mock._decide(body)
                self._rec(body_bytes, body, kind)
                usage = {"prompt_tokens": max(1, len(body_bytes) // 4), "completion_tokens": 60,
                         "total_tokens": max(1, len(body_bytes) // 4) + 60}
                created = int(time.time())
                if kind == "tool":
                    call = {"id": "call_h1", "type": "function",
                            "function": {"name": "terminal", "arguments": json.dumps({"command": payload})}}
                    message, finish = {"role": "assistant", "content": None, "tool_calls": [call]}, "tool_calls"
                else:
                    message, finish = {"role": "assistant", "content": payload}, "stop"
                if not body.get("stream"):
                    self._send(200, {"id": "chatcmpl-h1", "object": "chat.completion", "created": created,
                                     "model": "mock-model", "usage": usage,
                                     "choices": [{"index": 0, "message": message, "finish_reason": finish}]})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()

                def chunk(delta, finish_reason=None, **extra):
                    obj = {"id": "chatcmpl-h1", "object": "chat.completion.chunk", "created": created,
                           "model": "mock-model",
                           "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}], **extra}
                    self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")

                if kind == "tool":
                    call = message["tool_calls"][0]
                    chunk({"role": "assistant", "content": None, "tool_calls": [
                        {"index": 0, "id": call["id"], "type": "function",
                         "function": {"name": "terminal", "arguments": ""}}]})
                    chunk({"tool_calls": [{"index": 0, "function": {"arguments": call["function"]["arguments"]}}]})
                else:
                    chunk({"role": "assistant", "content": ""})
                    chunk({"content": payload})
                chunk({}, finish)
                obj = {"id": "chatcmpl-h1", "object": "chat.completion.chunk", "created": created,
                       "model": "mock-model", "choices": [], "usage": usage}
                self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                self.close_connection = True

        return H
