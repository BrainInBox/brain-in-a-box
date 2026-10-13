"""A stand-in for the Voyage API, for CI only: no key, no network, no cost.

Embeddings are hashed bags of words (1024 dims, unit length), so two texts that
share words land close together and a vector query ranks the page that talks
about it first. That is enough to prove the vector path end to end: pgvector
loads, vectors are written, and a query is answered from them.

    python fake_voyage.py 8899      # serves http://127.0.0.1:8899/v1
"""
import hashlib
import json
import math
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIMS = 1024


def embed(text, dims=DIMS):
    v = [0.0] * dims
    for word in re.findall(r"\w+", text.lower()):
        h = int.from_bytes(hashlib.sha256(word.encode()).digest()[:8], "big")
        v[h % dims] += 1.0 if (h >> 32) & 1 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        path = self.path.rstrip("/")
        if path.endswith("/embeddings"):
            inputs = body.get("input") or []
            if isinstance(inputs, str):
                inputs = [inputs]
            dims = int(body.get("output_dimension") or body.get("dimensions") or DIMS)
            data = [{"object": "embedding", "index": i, "embedding": embed(t, dims)} for i, t in enumerate(inputs)]
            tokens = sum(len(t.split()) for t in inputs)
            print(f"[fake-voyage] embeddings n={len(inputs)} dims={dims} model={body.get('model')}", flush=True)
            return self._send(200, {"object": "list", "data": data, "model": body.get("model"),
                                    "usage": {"total_tokens": tokens}})
        if path.endswith("/rerank"):
            q = embed(body.get("query") or "")
            docs = body.get("documents") or []
            scores = [sum(a * b for a, b in zip(q, embed(d if isinstance(d, str) else json.dumps(d)))) for d in docs]
            order = sorted(range(len(docs)), key=lambda i: -scores[i])[: int(body.get("top_k") or len(docs))]
            print(f"[fake-voyage] rerank n={len(docs)}", flush=True)
            return self._send(200, {"object": "list", "model": body.get("model"),
                                    "data": [{"index": i, "relevance_score": max(scores[i], 0.0)} for i in order],
                                    "usage": {"total_tokens": 1}})
        print(f"[fake-voyage] 404 {self.path}", flush=True)
        self._send(404, {"detail": "not found"})

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
