"""Relevés de disque pour la preuve H1 (jetable).

    python h1_disk.py size DIR
    python h1_disk.py newfiles SINCE_EPOCH --root R [--root R2] [--exclude X]
    python h1_disk.py hashes DIR...       (empreinte de sentinelles)
"""

import argparse
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path


def size(d):
    d = Path(d)
    top, sub = defaultdict(int), defaultdict(int)
    total = files = 0
    for dirpath, _, names in os.walk(d):
        rel = Path(dirpath).relative_to(d).parts
        for n in names:
            try:
                s = os.lstat(os.path.join(dirpath, n)).st_size
            except OSError:
                continue
            total += s
            files += 1
            top[rel[0] if rel else "."] += s
            if len(rel) >= 2:
                sub["/".join(rel[:2])] += s
    big = dict(sorted(sub.items(), key=lambda kv: -kv[1])[:25])
    return {"dir": str(d), "bytes": total, "files": files, "top": dict(top), "biggest_subdirs": big}


def newfiles(since, roots, exclude):
    groups = defaultdict(lambda: [0, 0])
    total = count = 0
    paths = []
    for r in roots:
        r = Path(r)
        if not r.exists():
            continue
        for dirpath, dirnames, names in os.walk(r, onerror=lambda e: None):
            low = dirpath.lower()
            if any(low.startswith(str(x).lower()) for x in exclude):
                dirnames[:] = []
                continue
            for n in names:
                p = os.path.join(dirpath, n)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                born = getattr(st, "st_birthtime", 0) or (st.st_ctime if os.name == "nt" else 0)
                if max(st.st_mtime, born) < since:
                    continue
                rel = Path(p).relative_to(r).parts
                key = str(Path(r, *rel[:3])) if len(rel) > 3 else str(Path(r, *rel[:-1]) if len(rel) > 1 else r)
                groups[key][0] += st.st_size
                groups[key][1] += 1
                if len(paths) < 5000:
                    paths.append(p)
                total += st.st_size
                count += 1
    ordered = sorted(groups.items(), key=lambda kv: -kv[1][0])
    return {"files": count, "bytes": total,
            "groups": [{"path": k, "bytes": v[0], "files": v[1]} for k, v in ordered[:60]], "paths": paths}


def hashes(paths):
    out = {}
    for p in paths:
        p = Path(p)
        if p.is_file():
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        elif p.is_dir():
            for q in sorted(p.rglob("*")):
                if q.is_file():
                    out[str(q)] = hashlib.sha256(q.read_bytes()).hexdigest()[:16]
        else:
            out[str(p)] = None
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("size")
    a.add_argument("dir")
    b = sp.add_parser("newfiles")
    b.add_argument("since", type=float)
    b.add_argument("--root", action="append", default=[])
    b.add_argument("--exclude", action="append", default=[])
    c = sp.add_parser("hashes")
    c.add_argument("paths", nargs="+")
    args = ap.parse_args()
    if args.cmd == "size":
        res = size(args.dir)
    elif args.cmd == "newfiles":
        res = newfiles(args.since, args.root, args.exclude)
    else:
        res = hashes(args.paths)
    print(json.dumps(res, indent=1))
