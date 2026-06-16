#!/usr/bin/env python3
"""brain_search — recherche locale dans le vault Brain, cross-platform et sans
dépendance lourde (BM25 pur Python). Alternative à gbrain sur Windows, où la
recherche embarquée (PGLite/pgvector) est cassée (#1549). Marche aussi sur
macOS/Linux. Hors-ligne, instantané, zéro modèle à télécharger.

Commandes :
    brain_search.py index [--brain DIR]      (re)construit l'index
    brain_search.py query "ma question" [--k 5] [--json]
    brain_search.py health

Config (env) :
    BRAIN_DIR            vault (def: ~/Documents/Brain)
    BRAIN_SEARCH_INDEX   fichier d'index (def: ~/.brain-search/index.json)
"""

import argparse
import json
import math
import os
import re
import sys
import unicodedata
from pathlib import Path

BRAIN = Path(os.environ.get("BRAIN_DIR") or (Path.home() / "Documents" / "Brain"))
INDEX = Path(os.environ.get("BRAIN_SEARCH_INDEX") or (Path.home() / ".brain-search" / "index.json"))
K1, B = 1.5, 0.75
_TOKEN = re.compile(r"[a-z0-9]+")
SKIP_DIRS = {".git", ".obsidian", ".trash", ".logs", "node_modules", "__pycache__"}


def norm(text: str) -> str:
    """minuscule + sans accents (déploiement ~ deploiement)."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def tokenize(text: str):
    return _TOKEN.findall(norm(text))


def chunk_markdown(text: str):
    """Découpe par titres ## / ### ; fallback : tout le fichier."""
    parts, cur = [], []
    for line in text.splitlines():
        if re.match(r"^#{2,3}\s", line) and cur:
            parts.append("\n".join(cur).strip())
            cur = [line]
        else:
            cur.append(line)
    if cur:
        parts.append("\n".join(cur).strip())
    return [p for p in parts if p] or ([text.strip()] if text.strip() else [])


def cmd_index(args):
    brain = Path(args.brain) if args.brain else BRAIN
    if not brain.is_dir():
        print(f"[brain_search] vault introuvable : {brain}", file=sys.stderr)
        sys.exit(1)
    docs, df = [], {}
    for md in sorted(brain.rglob("*.md")):
        if any(part in SKIP_DIRS for part in md.relative_to(brain).parts):
            continue
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = md.relative_to(brain).as_posix()
        for ci, chunk in enumerate(chunk_markdown(text)):
            toks = tokenize(chunk)
            if not toks:
                continue
            tf = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            for t in tf:
                df[t] = df.get(t, 0) + 1
            snippet = re.sub(r"\s+", " ", chunk).strip()[:240]
            docs.append({"path": rel, "chunk": ci, "len": len(toks), "tf": tf, "snippet": snippet})
    avgdl = (sum(d["len"] for d in docs) / len(docs)) if docs else 0.0
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps({"avgdl": avgdl, "N": len(docs), "df": df, "docs": docs}),
                     encoding="utf-8")
    print(f"[brain_search] indexé {len(docs)} chunks depuis {brain} -> {INDEX}")


def _load():
    if not INDEX.exists():
        print(f"[brain_search] pas d'index ({INDEX}). Lance d'abord : brain_search.py index",
              file=sys.stderr)
        sys.exit(2)
    return json.loads(INDEX.read_text(encoding="utf-8"))


def cmd_query(args):
    idx = _load()
    N, avgdl, df, docs = idx["N"], idx["avgdl"], idx["df"], idx["docs"]
    qterms = set(tokenize(args.q))
    if not qterms or not docs:
        print(json.dumps({"query": args.q, "hits": []}) if args.json else "  0 résultat")
        return
    idf = {t: math.log(1 + (N - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5)) for t in qterms}
    scored = []
    for d in docs:
        s = 0.0
        for t in qterms:
            tf = d["tf"].get(t)
            if not tf:
                continue
            denom = tf + K1 * (1 - B + B * d["len"] / (avgdl or 1))
            s += idf[t] * (tf * (K1 + 1)) / denom
        if s > 0:
            scored.append((s, d))
    scored.sort(key=lambda x: x[0], reverse=True)
    hits = [{"path": d["path"], "chunk": d["chunk"], "score": round(s, 3), "snippet": d["snippet"]}
            for s, d in scored[: args.k]]
    if args.json:
        print(json.dumps({"query": args.q, "hits": hits}, ensure_ascii=False))
        return
    print(f"\n  '{args.q}' — {len(hits)} hits\n")
    for i, h in enumerate(hits, 1):
        print(f"  {i}. [{h['score']}] {h['path']}#chunk{h['chunk']}")
        print(f"     {h['snippet']}\n")


def cmd_health(args):
    ok = INDEX.exists()
    n = _load()["N"] if ok else 0
    print(json.dumps({"status": "ok" if ok else "no-index", "count": n, "index": str(INDEX)}))


def main():
    ap = argparse.ArgumentParser(prog="brain_search")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("index"); p.add_argument("--brain"); p.set_defaults(func=cmd_index)
    p = sub.add_parser("query"); p.add_argument("q"); p.add_argument("--k", type=int, default=5)
    p.add_argument("--json", action="store_true"); p.set_defaults(func=cmd_query)
    p = sub.add_parser("health"); p.set_defaults(func=cmd_health)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
