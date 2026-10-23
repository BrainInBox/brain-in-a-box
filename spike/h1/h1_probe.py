"""Preuve H1 (jetable) : Hermes piloté par sa TUI gateway (JSON-RPC stdio), contre un faux fournisseur.

    python h1_probe.py --root <HERMES_HOME de Bibo> --out <dossier> [--mutation NOM]

Crée le profil, le configure comme Bibo le ferait, lance la gateway, envoie image + objectif,
mesure ce qui part (faux fournisseur), ce qui est écrit sur disque, purge, et rend un JSON de
critères. Code de sortie 0 seulement si tous les critères passent. Une mutation retire une
protection : la preuve doit alors échouer.
"""

import argparse
import base64
import hashlib
import json
import os
import shutil
import statistics
import struct
import subprocess
import sys
import threading
import time
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mock_openai import ANSWER, Mock  # noqa: E402

IS_WIN = os.name == "nt"
REQUIRED = ["code_vu", "etat", "consigne", "libelle", "cible", "confiance", "question", "choix",
            "precedente", "action_sensible", "suspect", "resume_etape", "element"]
# Chaque mutation retire une protection ; la preuve doit alors rougir, et sur le critère attendu.
EXPECT = {"tools_default": "tools_empty", "env_toolsets": "tools_empty",
          "approvals_off": "approval_denied_by_bibo", "approvals_smart": "approval_denied_by_bibo",
          "yolo_env": "approval_denied_by_bibo", "no_caps": "approval_denied_by_bibo",
          "bibo_approves": "approval_denied_by_bibo",
          "title_on": "one_call_per_turn", "memory_on": "one_call_per_turn", "image_auto": "image_sent_native",
          "cwd_context": "no_local_context", "no_purge": "purge_spec_list", "home_inherited": "default_home_untouched"}
MUTATIONS = list(EXPECT)
SCHEMA = (Path(__file__).resolve().parent / "schema.json").read_text(encoding="utf-8") \
    if (Path(__file__).resolve().parent / "schema.json").exists() else "{}"

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("o200k_base")

    def tokens(text):
        return len(_ENC.encode(text, disallowed_special=()))
    TOKENIZER = "tiktoken o200k_base"
except Exception:
    def tokens(text):
        return (len(text) + 3) // 4
    TOKENIZER = "chars/4"


def log(*a):
    print("[h1]", *a, flush=True)


# ---------------------------------------------------------------- image


def make_png(w, h, seed, marker):
    """PNG RGB à motif connu (bandes + bruit déterministe), avec un bloc tEXt qui porte le repère."""
    rows = bytearray()
    x = seed * 2654435761 & 0xFFFFFFFF
    for y in range(h):
        rows.append(0)
        for i in range(w):
            if (i // 64 + y // 48) % 23 == 0:
                x = (1103515245 * x + 12345) & 0x7FFFFFFF
                rows += bytes(((x >> 16) & 255, (x >> 8) & 255, x & 255))
            else:
                rows += bytes(((i * 3 + seed) & 255, (y * 2) & 255, 160))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"tEXt", b"Comment\x00" + marker.encode()) + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
            + chunk(b"IEND", b""))


# ---------------------------------------------------------------- Hermes


def launcher_for(root):
    names = ["hermes.exe", "hermes.cmd"] if IS_WIN else ["hermes"]
    for d in (root / "hermes-agent" / ".hermes" / "bin", root / "bin"):
        for n in names:
            if (d / n).is_file():
                return d / n
    raise SystemExit(f"no hermes launcher under {root}")


def clean_env(home, extra=None, keep_home=False):
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("HERMES_", "OPENAI_", "OPENROUTER_", "ANTHROPIC_", "GEMINI_",
                                        "GOOGLE_API", "NOUS_"))}
    if keep_home and os.environ.get("H1_USER_HERMES_HOME"):
        env["HERMES_HOME"] = os.environ["H1_USER_HERMES_HOME"]
    else:
        env["HERMES_HOME"] = str(home)
    env.update(extra or {})
    return env


def run(cmd, env, timeout=300, cwd=None):
    """Sortie dans un fichier, pas dans un tube : un petit-enfant resté ouvert ne bloque pas l'attente."""
    import tempfile
    t = time.time()
    with tempfile.TemporaryFile() as f:
        p = subprocess.Popen([str(c) for c in cmd], env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=f, stderr=f)
        try:
            code = p.wait(timeout)
        except subprocess.TimeoutExpired:
            if IS_WIN:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
            else:
                p.kill()
            code = f"timeout {timeout}s"
        f.seek(0)
        out = f.read().decode("utf-8", "replace")
    return code, out, time.time() - t


def runtime_command(launcher, module, env):
    code, out, _ = run([launcher, "--print-runtime-command", "--module", module], env, timeout=120)
    if code == 0:
        for line in reversed(out.strip().splitlines()):
            try:
                cmd = json.loads(line)
                if isinstance(cmd, list) and len(cmd) >= 2:
                    return cmd, "print-runtime-command"
            except Exception:
                continue
    return [str(launcher), "--run-module", module], "run-module"


class Gateway:
    def __init__(self, cmd, env, cwd, label, out, capabilities=True, approval_choice="deny"):
        self.label, self.out = label, out
        self.approval_choice = approval_choice
        self.lock, self.cond = threading.Lock(), threading.Condition()
        self.next_id, self.pending, self.events, self.server_requests = 0, {}, [], []
        self.stderr_tail = []
        self.t_spawn = time.time()
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WIN else 0
        self.proc = subprocess.Popen([str(c) for c in cmd], env=env, cwd=str(cwd), stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()
        ready = self.wait_event(lambda e: e.get("type") == "gateway.ready", 240)
        self.ready_s = time.time() - self.t_spawn
        self.ready_payload = ready.get("payload")
        self.caps = self.request("client.capabilities", {"server_requests": True}) if capabilities else None

    def _write(self, obj):
        data = (json.dumps(obj) + "\n").encode("utf-8")
        with self.lock:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()

    def _read_err(self):
        with open(self.out / f"gateway-{self.label}.stderr.log", "ab") as f:
            for line in self.proc.stderr:
                f.write(line)
                self.stderr_tail = (self.stderr_tail + [line.decode("utf-8", "replace").rstrip()])[-40:]

    def _read(self):
        for raw in self.proc.stdout:
            try:
                frame = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                continue
            if not isinstance(frame, dict):
                continue
            method = frame.get("method")
            if method == "event":
                with self.cond:
                    params = frame.get("params") or {}
                    params["_t"] = time.time()
                    self.events.append(params)
                    self.cond.notify_all()
            elif method and "id" in frame:
                # Question du serveur : Bibo refuse toute approbation, et n'implémente rien d'autre.
                self.server_requests.append({"t": time.time(), "method": method, "params": frame.get("params")})
                if method == "approval":
                    self._write({"jsonrpc": "2.0", "id": frame["id"], "result": {"choice": self.approval_choice}})
                else:
                    self._write({"jsonrpc": "2.0", "id": frame["id"],
                                 "error": {"code": -32601, "message": "not implemented by Bibo"}})
            elif "id" in frame:
                with self.cond:
                    self.pending[frame["id"]] = frame
                    self.cond.notify_all()
        with self.cond:
            self.cond.notify_all()

    def request(self, method, params, timeout=120):
        self.next_id += 1
        rid = self.next_id
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.time() + timeout
        with self.cond:
            while rid not in self.pending:
                if self.proc.poll() is not None or time.time() > deadline:
                    raise RuntimeError(f"{method}: no answer ({'exited' if self.proc.poll() is not None else 'timeout'})"
                                       f"\n" + "\n".join(self.stderr_tail[-15:]))
                self.cond.wait(0.5)
            frame = self.pending.pop(rid)
        if "error" in frame:
            raise RuntimeError(f"{method}: {frame['error']}")
        return frame.get("result")

    def wait_event(self, pred, timeout, start=0):
        deadline = time.time() + timeout
        with self.cond:
            while True:
                for e in self.events[start:]:
                    if pred(e):
                        return e
                if self.proc.poll() is not None or time.time() > deadline:
                    raise RuntimeError("event wait: " + ("exited" if self.proc.poll() is not None else "timeout")
                                       + "\n" + "\n".join(self.stderr_tail[-15:]))
                self.cond.wait(0.5)

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(20)
        except subprocess.TimeoutExpired:
            if IS_WIN:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True)
            else:
                self.proc.kill()
            self.proc.wait(10)
        return self.proc.returncode


# ---------------------------------------------------------------- config


def profile_config(mock_url, mutation, tools=None):
    cfg = {
        "model": {"default": "mock-model", "provider": "custom", "base_url": f"{mock_url}/v1",
                  "context_length": 200000, "supports_vision": True},
        "custom_providers": [{"name": "Mock", "base_url": f"{mock_url}/v1", "key_env": "OPENAI_API_KEY"}],
        "platform_toolsets": {"cli": tools or []},
        "agent": {"image_input_mode": "native"},
        "approvals": {"mode": "manual"},
        "memory": {"memory_enabled": False, "user_profile_enabled": False, "write_approval": True,
                   "nudge_interval": 0},
        "skills": {"write_approval": True},
        "auxiliary": {"title_generation": {"enabled": False, "model_upgrade_enabled": False},
                      "background_review": {"enabled": False}},
        "onboarding": {"run": False},
        "telemetry": {"shared_metrics": {"enabled": False, "send": False, "offer_version": 2}},
        "mcp_servers": {},
    }
    if mutation == "tools_default":
        del cfg["platform_toolsets"]
    if mutation == "approvals_off":
        cfg["approvals"]["mode"] = "off"
    if mutation == "approvals_smart":
        cfg["approvals"]["mode"] = "smart"
    if mutation == "title_on":
        del cfg["auxiliary"]["title_generation"]
    if mutation == "memory_on":
        # Mémoire par défaut, relecture de fond permise, et son déclencheur au plus court.
        del cfg["auxiliary"]["background_review"]
        cfg["memory"] = {"nudge_interval": 1}
        cfg["skills"] = {"creation_nudge_interval": 1}
    if mutation == "image_auto":
        del cfg["agent"]["image_input_mode"]
        del cfg["model"]["supports_vision"]
    return cfg


# ---------------------------------------------------------------- disque


def walk_new(roots, since, exclude):
    seen, found = set(), []
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
            if any(str(dirpath).lower().startswith(str(x).lower()) for x in exclude):
                dirnames[:] = []
                continue
            for name in filenames:
                p = os.path.join(dirpath, name)
                if p.lower() in seen:
                    continue
                seen.add(p.lower())
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if max(st.st_mtime, getattr(st, "st_birthtime", 0) or 0, st.st_ctime if IS_WIN else 0) >= since:
                    found.append((p, st.st_size))
    return found


def scan(roots, since, needles, exclude, known=()):
    """Fichiers nouveaux ou modifiés depuis `since` sous `roots`, plus `known` (un relevé précédent),
    et ceux qui contiennent un des repères."""
    files = walk_new(roots, since, exclude)
    listed = {p.lower() for p, _ in files}
    files += [(p, os.path.getsize(p)) for p in known if p.lower() not in listed and os.path.exists(p)]
    hits, unreadable = [], []
    for p, size in files:
        if size > 400 * 1024 * 1024:
            continue
        try:
            with open(p, "rb") as f:
                data = f.read()
        except OSError:
            unreadable.append(p)
            continue
        names = [n for n, b in needles.items() if any(x in data for x in b)]
        if names:
            hits.append({"path": p, "size": size, "needles": names})
    return {"new_files": [{"path": p, "size": s} for p, s in files], "hits": hits, "unreadable": unreadable}


def tree(path):
    out = {}
    if path.exists():
        for p in sorted(path.rglob("*")):
            if p.is_file():
                out[str(p.relative_to(path))] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    return out


def dir_size(path):
    total = n = 0
    for dirpath, _, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
                n += 1
            except OSError:
                pass
    return total, n


def rm(path):
    p = Path(path)
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p, ignore_errors=False)
    elif p.exists() or p.is_symlink():
        p.unlink()


# ---------------------------------------------------------------- mesures sur les requêtes


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") in ("text", "input_text"))
    return ""


def images_of(body):
    found = []
    for m in body.get("messages") or []:
        content = m.get("content")
        if isinstance(content, list):
            for p in content:
                if isinstance(p, dict) and p.get("type") in ("image_url", "input_image", "image"):
                    url = p.get("image_url", {}).get("url") if isinstance(p.get("image_url"), dict) else p.get("image_url") or p.get("url") or ""
                    data = b""
                    if isinstance(url, str) and url.startswith("data:") and "," in url:
                        try:
                            data = base64.b64decode(url.split(",", 1)[1])
                        except Exception:
                            pass
                    found.append({"role": m.get("role"), "url_len": len(url or ""),
                                  "sha256": hashlib.sha256(data).hexdigest() if data else None, "bytes": len(data)})
    return found


def measure_call(rec, our_texts):
    body = rec["body"] or {}
    msgs = body.get("messages") or []
    system = "".join(text_of(m.get("content")) for m in msgs if m.get("role") in ("system", "developer"))
    user = "".join(text_of(m.get("content")) for m in msgs if m.get("role") == "user")
    tools = body.get("tools") or []
    tools_json = json.dumps(tools) if tools else ""
    ours = sum(len(t) for t in our_texts if t in user)
    user_extra = user
    for t in our_texts:
        user_extra = user_extra.replace(t, "")
    other = "".join(text_of(m.get("content")) for m in msgs if m.get("role") not in ("system", "developer", "user"))
    return {
        "t": rec["t"], "body_bytes": rec["body_len"], "messages": len(msgs), "roles": [m.get("role") for m in msgs],
        "system_chars": len(system), "system_tokens": tokens(system),
        "tools": len(tools), "tool_names": sorted(t.get("function", {}).get("name", "?") for t in tools),
        "tools_tokens": tokens(tools_json) if tools_json else 0,
        "user_chars": len(user), "user_ours_chars": ours, "user_extra_tokens": tokens(user_extra),
        "other_tokens": tokens(other),
        "fixed_tokens": tokens(system) + (tokens(tools_json) if tools_json else 0) + tokens(user_extra),
        "images": images_of(body), "stream": body.get("stream"), "model": body.get("model"),
        "keys": sorted(body.keys()), "system_head": system[:160],
    }


def validate_answer(text):
    try:
        obj = json.loads(text)
    except Exception as exc:
        return False, f"not JSON: {exc}: {text[:200]!r}"
    missing = [k for k in REQUIRED if k not in obj]
    if missing:
        return False, f"missing {missing}"
    if not isinstance(obj["element"], (int, type(None))) or not isinstance(obj["confiance"], (int, float)):
        return False, "bad types"
    return obj == ANSWER, "verbatim" if obj == ANSWER else "valid but altered"


# ---------------------------------------------------------------- la preuve


def prompt_text(marker, n):
    return ("Tu es le guide de Bibo. La personne veut : « faire une facture pour Dupont » dans Sage.\n"
            "L'image jointe montre l'appli de devant ; tout le reste est noirci.\n"
            "Éléments visibles (commandes seulement) :\n"
            "#1 bouton \"Nouveau\" [96,36,88,28]\n#2 bouton \"Ouvrir\" [190,36,88,28]\n"
            "#3 onglet \"Ventes\" [10,80,90,24]\n#4 champ \"Client\" [120,140,220,24]\n"
            "#5 bouton \"Valider\" [880,690,96,30]\n#6 menu \"Fichier\" [4,4,52,20]\n#7 bouton \"Nouveau\" [96,36,88,28]\n"
            f"Étape {n}. Réponds UNIQUEMENT par un objet JSON conforme à ce schéma, plus \"element\" (numéro ou null) :\n"
            f"{SCHEMA}\nRepère : {marker}-{n}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mutation", default="", choices=[""] + MUTATIONS)
    ap.add_argument("--profile", default="bibo-guide")
    ap.add_argument("--scan-home", action="store_true", help="scanner tout le profil utilisateur (preuve principale)")
    args = ap.parse_args()
    mutation = args.mutation
    root = Path(args.root).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    tag = mutation or "main"
    rnd = hashlib.sha256(os.urandom(16)).hexdigest()[:12]
    TXT, IMG, CTX = f"BIBOH1TXT{rnd}", f"BIBOH1IMG{rnd}", f"BIBOH1CTX{rnd}"
    t0 = time.time() - 2
    report = {"mutation": mutation, "tokenizer": TOKENIZER, "root": str(root), "platform": sys.platform,
              "criteria": {}, "measures": {}}
    crit, meas = report["criteria"], report["measures"]

    def check(name, ok, detail):
        crit[name] = {"ok": bool(ok), "detail": detail}
        log(f"{'OK ' if ok else 'KO '} {name}: {json.dumps(detail)[:400]}")

    launcher = launcher_for(root)
    base_env = clean_env(root)
    meas["launcher"] = str(launcher)
    gw_cmd, how = runtime_command(launcher, "tui_gateway.entry", base_env)
    meas["gateway_command"] = {"how": how, "argv0": gw_cmd[0], "args": len(gw_cmd)}

    # Profils : celui du guide, et une variante avec le seul terminal pour provoquer une approbation.
    name = args.profile + ("" if not mutation else f"-{mutation.replace('_', '-')}")
    profiles = {}
    for pname in (name, name + "-appr"):
        prof = root / "profiles" / pname
        if prof.exists():
            shutil.rmtree(prof)
        code, txt, dt = run([launcher, "profile", "create", pname, "--no-alias", "--no-skills"], base_env, 300)
        if code != 0:
            raise SystemExit(f"profile create {pname} failed ({code}):\n{txt[-3000:]}")
        size, nfiles = dir_size(prof)
        profiles[pname] = prof
        if pname == name:
            meas["profile_create"] = {"seconds": round(dt, 2), "bytes": size, "files": nfiles,
                                      "layout": sorted(str(p.relative_to(prof)) for p in prof.rglob("*"))[:60]}
    prof, prof_appr = profiles[name], profiles[name + "-appr"]

    sentinel = root / f"h1-sentinel-{tag}.txt"
    provoke = f'echo pwned > "{sentinel.as_posix()}" ; rm -rf "{(root / "h1-nonexistent-dir").as_posix()}"'
    mock = Mock(out / f"mock-{tag}.jsonl", provoke_command=provoke).start()
    for p, tools in ((prof, None), (prof_appr, ["terminal"])):
        cfg = profile_config(mock.url, mutation, tools)
        (p / "config.yaml").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        # Fausses clés, jamais de vraies : la clé « OpenRouter » doit survivre à la purge.
        (p / ".env").write_text(f"OPENAI_API_KEY=h1-fake-key\nOPENAI_BASE_URL={mock.url}/v1\n"
                                "H1_FAKE_OPENROUTER_KEY=sk-or-v1-h1-fake-not-a-secret\n", encoding="utf-8")
    keep_before = {n: hashlib.sha256((prof / n).read_bytes()).hexdigest() for n in ("config.yaml", ".env")}
    shutil.copy(prof / "config.yaml", out / f"config-{tag}-before.yaml")
    baselines = {p: {q.relative_to(p).as_posix() for q in p.rglob("*")} for p in (prof, prof_appr)}

    cwd = root / f"guide-cwd-{tag}"
    cwd.mkdir(exist_ok=True)
    if mutation == "cwd_context":
        (cwd / "AGENTS.md").write_text(f"Notes privées de la personne : {CTX}\n", encoding="utf-8")

    extra = {}
    if mutation == "env_toolsets":
        extra["HERMES_TUI_TOOLSETS"] = "terminal,file,web"
    env = clean_env(prof, extra, keep_home=(mutation == "home_inherited"))
    env_appr = clean_env(prof_appr, {"HERMES_YOLO_MODE": "1"} if mutation == "yolo_env" else {},
                         keep_home=(mutation == "home_inherited"))
    caps = mutation != "no_caps"

    images = {i: make_png(1280, 720, i, f"{IMG}-{i}") for i in range(1, 7)}
    our_texts = {i: prompt_text(TXT, i) for i in range(1, 7)}
    turns = []

    def turn(gw, sid, i, label, record=True):
        start = len(gw.events)
        n_before = len(mock.snapshot())
        t_attach = time.time()
        att = gw.request("image.attach_bytes", {"session_id": sid, "filename": f"ecran-{i}.png",
                                                 "content_base64": base64.b64encode(images[i]).decode()})
        t_submit = time.time()
        gw.request("prompt.submit", {"session_id": sid, "text": our_texts[i]})
        try:
            done = gw.wait_event(lambda e: e.get("type") == "message.complete" and e.get("session_id") == sid,
                                 240, start)
        except RuntimeError as exc:
            done = {"_t": time.time(), "payload": {"status": f"no completion: {str(exc)[:300]}"}}
        t_done = done["_t"]
        calls = [r for r in mock.snapshot()[n_before:] if r["method"] == "POST"]
        payload = done.get("payload") or {}
        ok, why = validate_answer(payload.get("text") or "")
        info = {"label": label, "i": i, "status": payload.get("status"), "json_ok": ok, "json": why,
                "attach_ms": round((t_submit - t_attach) * 1000), "latency_ms": round((t_done - t_submit) * 1000),
                "first_call_ms": round((calls[0]["t"] - t_submit) * 1000) if calls else None,
                "after_last_call_ms": round((t_done - calls[-1]["t"]) * 1000) if calls else None,
                "posts": len(calls), "kinds": [c["kind"] for c in calls],
                "attach_result_keys": sorted((att or {}).keys()),
                "calls": [measure_call(c, list(our_texts.values())) for c in calls],
                "sent_sha256": hashlib.sha256(images[i]).hexdigest()}
        if record:
            turns.append(info)
        log(f"turn {label}: status={info['status']} json={why} posts={info['posts']} latency={info['latency_ms']}ms")
        return info

    # --- G1 : le profil du guide
    gw = Gateway(gw_cmd, env, cwd, f"{tag}-guide", out, capabilities=caps)
    meas["gateway_ready_s"] = round(gw.ready_s, 2)
    meas["client_capabilities"] = gw.caps
    sessions = []
    s = gw.request("session.create", {"cols": 100, "cwd": str(cwd), "source": "tui"})
    sessions.append(s["session_id"])
    meas["session_create_keys"] = sorted(s.keys())
    n_turns = 1 if mutation else 3
    for i in range(1, n_turns + 1):
        turn(gw, s["session_id"], i, f"same-session-{i}")
    try:
        meas["context_breakdown"] = gw.request("session.context_breakdown", {"session_id": s["session_id"]})
    except Exception as exc:
        meas["context_breakdown"] = {"error": str(exc)[:300]}
    if not mutation:
        for i in (4, 5, 6):
            s2 = gw.request("session.create", {"cols": 100, "cwd": str(cwd), "source": "tui"})
            sessions.append(s2["session_id"])
            turn(gw, s2["session_id"], i, f"fresh-session-{i}")
    # Le modèle demande un outil qu'il n'a pas.
    s3 = gw.request("session.create", {"cols": 100, "cwd": str(cwd), "source": "tui"})
    sessions.append(s3["session_id"])
    n_before = len(mock.snapshot())
    start = len(gw.events)
    gw.request("prompt.submit", {"session_id": s3["session_id"], "text": "H1-PROVOKE " + our_texts[1]})
    try:
        done = gw.wait_event(lambda e: e.get("type") == "message.complete" and e.get("session_id") == s3["session_id"],
                             240, start)
    except RuntimeError as exc:
        done = {"payload": {"status": f"no completion: {str(exc)[:300]}"}}
    provoke_guide = {"status": (done.get("payload") or {}).get("status"),
                     "posts": len([r for r in mock.snapshot()[n_before:] if r["method"] == "POST"]),
                     "sentinel_written": sentinel.exists(),
                     "approvals_seen": len([r for r in gw.server_requests if r["method"] == "approval"]),
                     "tool_messages": [text_of(m.get("content"))[:240] for r in mock.snapshot()[n_before:]
                                       for m in (r["body"] or {}).get("messages", []) if m.get("role") == "tool"][:2]}
    # Appels tardifs (relecture de fond, titre) : 12 s d'observation après le dernier tour.
    n_late = len(mock.snapshot())
    t_end = time.time() + 12
    while time.time() < t_end:
        time.sleep(0.5)
    late = [r for r in mock.snapshot()[n_late:] if r["method"] == "POST"]
    g1_posts = len([r for r in mock.snapshot() if r["method"] == "POST"])
    unattributed = g1_posts - sum(t["posts"] for t in turns) - provoke_guide["posts"] - len(late)
    for sid in sessions:
        try:
            gw.request("session.close", {"session_id": sid}, 30)
        except Exception:
            pass
    gw_requests = list(gw.server_requests)
    meas["gateway_exit"] = gw.close()

    # --- G2 : variante avec le terminal, pour provoquer une approbation
    sentinel.unlink(missing_ok=True)
    gw2 = Gateway(gw_cmd, env_appr, cwd, f"{tag}-appr", out, capabilities=caps,
                  approval_choice="once" if mutation == "bibo_approves" else "deny")
    s4 = gw2.request("session.create", {"cols": 100, "cwd": str(cwd), "source": "tui"})
    n_before = len(mock.snapshot())
    start = len(gw2.events)
    gw2.request("prompt.submit", {"session_id": s4["session_id"], "text": "H1-PROVOKE " + our_texts[2]})
    try:
        done2 = gw2.wait_event(lambda e: e.get("type") == "message.complete" and e.get("session_id") == s4["session_id"],
                               300, start)
        status2 = (done2.get("payload") or {}).get("status")
    except RuntimeError as exc:
        status2 = f"no completion: {str(exc)[:200]}"
    appr_calls = [r for r in mock.snapshot()[n_before:] if r["method"] == "POST"]
    approval_frames = [r for r in gw2.server_requests if r["method"] == "approval"]
    provoke_appr = {"status": status2, "posts": len(appr_calls), "kinds": [c["kind"] for c in appr_calls],
                    "tools_offered": sorted({n for c in appr_calls for n in measure_call(c, [])["tool_names"]}),
                    "approvals_seen": len(approval_frames),
                    "approval_command": [(f.get("params") or {}).get("command") for f in approval_frames][:2],
                    "sentinel_written": sentinel.exists(),
                    "tool_result": [text_of(m.get("content"))[:240] for r in appr_calls
                                    for m in (r["body"] or {}).get("messages", []) if m.get("role") == "tool"][:2]}
    try:
        gw2.request("session.close", {"session_id": s4["session_id"]}, 30)
    except Exception:
        pass
    meas["gateway2_exit"] = gw2.close()

    # --- mesures et critères du tour
    main_turns = [t for t in turns]
    meas["turns"] = main_turns
    meas["provoke_in_guide_profile"] = provoke_guide
    meas["provoke_with_terminal"] = provoke_appr
    meas["late_calls"] = [{"kind": r["kind"], "system_head": measure_call(r, [])["system_head"]} for r in late]
    meas["server_requests_guide"] = [{"method": r["method"]} for r in gw_requests]
    all_posts = [r for r in mock.snapshot() if r["method"] == "POST"]
    meas["all_requests"] = {"posts": len(all_posts),
                            "gets": [r["path"] for r in mock.snapshot() if r["method"] == "GET"],
                            "unknown": [r["path"] for r in mock.snapshot() if r["kind"] == "unknown"]}

    check("gateway_json", all(t["status"] == "complete" and t["json_ok"] for t in turns),
          [{"turn": t["label"], "status": t["status"], "json": t["json"]} for t in turns])
    img_ok = []
    for t in turns:
        last = t["calls"][0]["images"] if t["calls"] else []
        img_ok.append(any(im["bytes"] for im in last))
        t["image_identical"] = any(im["sha256"] == t["sent_sha256"] for im in last)
    check("image_sent_native", all(img_ok) and bool(turns),
          [{"turn": t["label"], "images_in_call": [(im["bytes"], (im["sha256"] or "")[:12]) for im in (t["calls"][0]["images"] if t["calls"] else [])],
            "sent": t["sent_sha256"][:12], "identical": t["image_identical"]} for t in turns])
    tool_calls = [c for t in turns for c in t["calls"]]
    cb = meas.get("context_breakdown") or {}
    cb_tools = {c.get("id"): c.get("tokens") for c in cb.get("categories", [])} if isinstance(cb, dict) else {}
    check("tools_empty", tool_calls and all(c["tools"] == 0 for c in tool_calls)
          and not any(cb_tools.get(k) for k in ("tool_definitions", "mcp", "subagent_definitions")),
          {"tools_per_call": [c["tools"] for c in tool_calls], "names": sorted({n for c in tool_calls for n in c["tool_names"]})[:40],
           "gateway_breakdown": cb_tools})
    check("one_call_per_turn", turns and all(t["posts"] == 1 for t in turns) and not late and unattributed == 0,
          {"posts_per_turn": [t["posts"] for t in turns], "late_posts": len(late), "between_turns": unattributed,
           "provoke_posts": provoke_guide["posts"]})
    check("provoked_tool_not_run", not provoke_guide["sentinel_written"] and provoke_guide["approvals_seen"] == 0,
          provoke_guide)
    check("approval_denied_by_bibo", provoke_appr["approvals_seen"] >= 1 and not provoke_appr["sentinel_written"],
          provoke_appr)
    raw_all = "\n".join(json.dumps(r["body"]) for r in all_posts)
    check("no_local_context", CTX not in raw_all and not any(t["calls"] and "AGENTS.md" in json.dumps(t["calls"][0]["system_head"]) for t in turns),
          {"ctx_marker_in_requests": CTX in raw_all})

    fixed = [c["fixed_tokens"] for t in turns for c in t["calls"][:1]]
    meas["fixed_tokens"] = {"per_call": fixed, "system_tokens": [c["system_tokens"] for t in turns for c in t["calls"][:1]],
                            "tools_tokens": [c["tools_tokens"] for t in turns for c in t["calls"][:1]],
                            "user_extra_tokens": [c["user_extra_tokens"] for t in turns for c in t["calls"][:1]],
                            "body_bytes": [c["body_bytes"] for t in turns for c in t["calls"][:1]],
                            "images_per_call": [len(c["images"]) for t in turns for c in t["calls"][:1]],
                            "our_prompt_tokens": tokens(our_texts[1]), "image_bytes": len(images[1])}
    lat = [t["latency_ms"] for t in turns]
    meas["latency_ms"] = {"all": lat, "p50": statistics.median(lat) if lat else None,
                          "max": max(lat) if lat else None,
                          "first_call_ms": [t["first_call_ms"] for t in turns],
                          "after_last_call_ms": [t["after_last_call_ms"] for t in turns]}

    # --- disque : ce qui a été écrit, puis la purge
    needles = {"text": [TXT.encode(), TXT.encode("utf-16-le")], "image_marker": [IMG.encode()],
               "image_b64": [base64.b64encode(images[1])[400:480], base64.b64encode(images[2])[400:480]],
               "ctx": [CTX.encode()]}
    home = Path.home()
    user_home = Path(os.environ.get("H1_USER_HERMES_HOME") or (Path(os.environ.get("LOCALAPPDATA", home)) / "hermes"))
    roots = [root, Path(os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp"), user_home, home / ".hermes"]
    if args.scan_home:
        roots = [home] + ([Path(os.environ.get("ProgramData", "C:/ProgramData"))] if IS_WIN else [Path("/tmp")]) + roots
    exclude = [out, home / "work", home / "actions-runner", home / "runners", Path(__file__).resolve().parent,
               cwd]
    before = scan(roots, t0, needles, exclude)
    known = [f["path"] for f in before["new_files"]]
    small_roots = [root, user_home, home / ".hermes"]
    meas["written_during_session"] = {
        "in_profile": sorted({str(Path(f["path"]).relative_to(prof)) for f in before["new_files"]
                              if Path(f["path"]).is_relative_to(prof)})[:80],
        "outside_profile": sorted({f["path"] for f in before["new_files"]
                                   if not Path(f["path"]).is_relative_to(prof) and not Path(f["path"]).is_relative_to(prof_appr)
                                   and "__pycache__" not in f["path"]})[:120],
        "marker_hits": before["hits"]}

    def purge_spec(p):
        removed = []
        for rel in ("sessions", "state.db", "state.db-wal", "state.db-shm", "images"):
            if (p / rel).exists():
                rm(p / rel)
                removed.append(rel)
        return removed

    def purge_new(p, base):
        """Tout ce que la séance a créé dans le profil, sauf la config et les clés."""
        removed = []
        for q in sorted(p.rglob("*"), key=lambda x: len(x.parts)):
            if not q.exists() and not q.is_symlink():
                continue
            rel = q.relative_to(p).as_posix()
            if rel in base or rel in ("config.yaml", ".env", "auth.json"):
                continue
            try:
                rm(q)
                removed.append(rel)
            except OSError as exc:
                removed.append(f"{rel} (FAILED: {exc})")
        return removed

    if mutation != "no_purge":
        spec_removed = purge_spec(prof) + purge_spec(prof_appr)
        after_spec = scan(small_roots, t0, needles, exclude, known)
        new_removed = purge_new(prof, baselines[prof]) + purge_new(prof_appr, baselines[prof_appr])
        after_all = scan(small_roots, t0, needles, exclude, known)
    else:
        spec_removed = new_removed = []
        after_spec = after_all = scan(small_roots, t0, needles, exclude, known)
    keep_after = {n: hashlib.sha256((prof / n).read_bytes()).hexdigest() if (prof / n).exists() else None
                  for n in ("config.yaml", ".env")}
    if (prof / "config.yaml").exists():
        shutil.copy(prof / "config.yaml", out / f"config-{tag}-after.yaml")
    cfg_after = {}
    try:
        cfg_after = json.loads((prof / "config.yaml").read_text(encoding="utf-8"))
    except Exception:
        try:
            import yaml  # noqa
            cfg_after = yaml.safe_load((prof / "config.yaml").read_text(encoding="utf-8"))
        except Exception:
            cfg_after = {}
    meas["purge"] = {"spec_removed": spec_removed, "spec_residual_hits": after_spec["hits"],
                     "extended_removed": new_removed[:80], "final_hits": after_all["hits"],
                     "kept_files_unchanged": keep_after == keep_before,
                     "config_rewritten_by_hermes": keep_after["config.yaml"] != keep_before["config.yaml"],
                     "approvals_mode_after": (cfg_after.get("approvals") or {}).get("mode") if isinstance(cfg_after, dict) else None}
    check("purge_spec_list", not after_spec["hits"] and keep_after["config.yaml"] and keep_after[".env"],
          {"residual": [(h["path"], h["needles"]) for h in after_spec["hits"]][:20]})
    check("purge_final", not after_all["hits"] and keep_after["config.yaml"] is not None and keep_after[".env"] == keep_before[".env"],
          {"residual": [(h["path"], h["needles"]) for h in after_all["hits"]][:20],
           "env_unchanged": keep_after[".env"] == keep_before[".env"]})
    user_hits = [h for h in before["hits"] if Path(h["path"]).is_relative_to(user_home) or Path(h["path"]).is_relative_to(home / ".hermes")]
    check("default_home_untouched", not user_hits, {"hits": [h["path"] for h in user_hits][:10]})
    # La config réécrite par Hermes garde-t-elle les protections ?
    safety = {"approvals.mode": (cfg_after.get("approvals") or {}).get("mode"),
              "platform_toolsets.cli": (cfg_after.get("platform_toolsets") or {}).get("cli"),
              "memory.memory_enabled": (cfg_after.get("memory") or {}).get("memory_enabled"),
              "auxiliary.background_review.enabled": ((cfg_after.get("auxiliary") or {}).get("background_review") or {}).get("enabled"),
              "onboarding_seen": (cfg_after.get("onboarding") or {}).get("seen")} if isinstance(cfg_after, dict) else {}
    meas["config_after_session"] = safety
    if mutation in ("", "no_purge", "image_auto", "cwd_context", "no_caps", "yolo_env", "env_toolsets", "home_inherited", "title_on", "bibo_approves"):
        check("config_safety_kept", safety.get("approvals.mode") == "manual" and safety.get("platform_toolsets.cli") == []
              and safety.get("memory.memory_enabled") is False, safety)

    # Ce qui part chez le fournisseur sur la personne elle-même (nom de compte, chemins).
    first = next((r for r in all_posts), None)
    raw_first = json.dumps(first["body"]) if first else ""
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    meas["identity_in_request"] = {"account_name": user, "account_name_occurrences": raw_first.count(user) if user else None,
                                   "home_path_occurrences": raw_first.count(json.dumps(str(home))[1:-1]),
                                   "reasoning_effort": (first["body"] or {}).get("reasoning_effort") if first else None}

    # Séance suivante, après la purge : la note « premier message » revient-elle ?
    if not mutation:
        n0 = len(mock.snapshot())
        gw3 = Gateway(gw_cmd, env, cwd, "main-after-purge", out, capabilities=True)
        s5 = gw3.request("session.create", {"cols": 100, "cwd": str(cwd), "source": "tui"})
        info = turn(gw3, s5["session_id"], 1, "after-purge-1", record=False)
        posts3 = [r for r in mock.snapshot()[n0:] if r["method"] == "POST"]
        raw3 = "\n".join(json.dumps(r["body"]) for r in posts3)
        meas["next_seance_after_purge"] = {"status": info["status"], "json": info["json"], "posts": info["posts"],
                                           "system_note_in_request": "[System note" in raw3,
                                           "first_message_note": "very first message ever" in raw3,
                                           "user_extra_tokens": [c["user_extra_tokens"] for c in info["calls"]],
                                           "latency_ms": info["latency_ms"], "ready_s": round(gw3.ready_s, 2)}
        try:
            gw3.request("session.close", {"session_id": s5["session_id"]}, 30)
        except Exception:
            pass
        gw3.close()
        purge_spec(prof)
        purge_new(prof, baselines[prof])
        after_second = scan(small_roots, t0, needles, exclude, known)
        check("purge_second_seance", not after_second["hits"],
              {"residual": [(h["path"], h["needles"]) for h in after_second["hits"]][:20]})
    mock.close()

    report["ok"] = all(c["ok"] for c in crit.values())
    report["failed"] = [k for k, c in crit.items() if not c["ok"]]
    if mutation:
        report["expected_red"] = EXPECT[mutation]
        report["killed_as_expected"] = EXPECT[mutation] in report["failed"]
    (out / f"report-{tag}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"RESULT {tag}: {'PASS' if report['ok'] else 'FAIL'} failed={report['failed']}")
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        import traceback
        tb = traceback.format_exc()
        log("CRASH\n" + tb)
        a = sys.argv
        tag = a[a.index("--mutation") + 1] if "--mutation" in a and a[a.index("--mutation") + 1] else "main"
        outp = Path(a[a.index("--out") + 1])
        outp.mkdir(parents=True, exist_ok=True)
        (outp / f"report-{tag}.json").write_text(json.dumps({"ok": False, "failed": ["crash"], "crash": tb[-4000:]}, indent=2))
        sys.exit(1)
