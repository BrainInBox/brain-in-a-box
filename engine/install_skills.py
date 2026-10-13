#!/usr/bin/env python3
"""
Installs the bundled Claude Code skills (skills/<name>/) into ~/.claude/skills/.
Shared by install.sh and install.ps1. Pure stdlib, Python 3.8+.

Never touches what is not ours. A manifest (~/.brain-in-a-box/skills.json)
records the hash of every skill we installed:
  - absent from ~/.claude/skills          -> installed
  - ours and untouched, newer in the repo -> updated
  - ours but edited by the user           -> kept as is (reported)
  - not ours (same name, another tool)    -> kept as is (reported)
Exit code 1 only when the source folder is missing.
"""
import argparse, hashlib, json, os, shutil, sys
from pathlib import Path


JUNK = {".DS_Store", "Thumbs.db", "desktop.ini", "__pycache__"}  # dropped by the OS, not an edit


def tree_hash(root):
    """Content hash of a folder: relative paths + bytes, independent of mtimes."""
    h = hashlib.sha256()
    files = (p for p in Path(root).rglob("*") if p.is_file() and not JUNK & set(p.relative_to(root).parts))
    for path in sorted(files):
        h.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        h.update(path.read_bytes() + b"\0")
    return h.hexdigest()


def load_manifest(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_manifest(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(str(tmp), str(path))


def copy_skill(src, dst):
    tmp = dst.with_name("." + dst.name + ".installing")
    if tmp.exists():
        shutil.rmtree(str(tmp))
    shutil.copytree(str(src), str(tmp))
    if dst.exists():
        shutil.rmtree(str(dst))
    os.replace(str(tmp), str(dst))


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--src", required=True, help="folder holding one sub-folder per skill")
    ap.add_argument("--dest", default=str(Path.home() / ".claude" / "skills"))
    ap.add_argument("--manifest", default=str(Path.home() / ".brain-in-a-box" / "skills.json"))
    args = ap.parse_args()

    src, dest, mpath = Path(args.src), Path(args.dest), Path(args.manifest)
    if not src.is_dir():
        print("skills: source folder not found: %s" % src)
        return 1
    dest.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(mpath)

    for skill in sorted(p for p in src.iterdir() if (p / "SKILL.md").is_file()):
        name, new = skill.name, tree_hash(skill)
        dst = dest / name
        mine = manifest.get(name)
        try:
            # lexists: a dangling symlink still means "someone put something here"
            if not os.path.lexists(str(dst)):
                copy_skill(skill, dst)
                manifest[name] = new
                print("  installed  %s" % name)
            elif dst.is_symlink() or not mine:
                print("  kept       %s (not installed by brain-in-a-box)" % name)
            elif tree_hash(dst) != mine:
                print("  kept       %s (you edited it; the repo version is in %s)" % (name, skill))
            elif mine != new:
                copy_skill(skill, dst)
                manifest[name] = new
                print("  updated    %s" % name)
            else:
                print("  up to date %s" % name)
        except OSError as e:
            print("  failed     %s: %s" % (name, e))

    save_manifest(mpath, manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
