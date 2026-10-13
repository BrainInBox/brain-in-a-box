#!/usr/bin/env python3
"""Indexes the vault into gbrain, whatever shape the brain has. Run by install.sh
and by the nightly (gbrain-nightly.sh).

gbrain reads the vault through a source, and which one depends on the brain's age:

- made by an older gbrain: the 'default' source is the vault. `sync --repo <vault>`,
  as it always was. Nothing else changes.
- made by gbrain 0.60.19x or later: 'default' is gbrain's own folder
  (~/.gbrain/content/<id>/default), so the vault is a source of its own, 'vault',
  synced with `sync --source vault`. It also becomes the default for bare commands
  (sources.default), so a bare `gbrain import` or `put` lands in the vault.
- installed in between: the vault was imported into gbrain's folder and never synced
  again (`sync --repo` fails there: "not a git repository"). Same as above, then the
  copies left in gbrain's folder are soft-deleted (a backup of that folder is kept in
  ~/.brain-in-a-box/backups/), or every note would come up twice in a search.

    gbrain-sync-vault.py <vault>

Prints what it decides on "[vault] ..." lines; gbrain's own output passes through.
Exit code: 0 once the vault is indexed, else the failing step's.
"""
import json
import os
import shutil
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
GBRAIN = os.path.join(HOME, ".bun", "bin", "gbrain")
CONTENT = os.path.join(HOME, ".gbrain", "content")
BACKUPS = os.path.join(HOME, ".brain-in-a-box", "backups")
SOURCE_ID = "vault"


def say(msg):
    print("[vault] " + msg, flush=True)


def gbrain(args, capture=False, timeout=600):
    """Runs gbrain. Returns (exit code, stdout, stderr); output is captured only when asked."""
    try:
        p = subprocess.run([GBRAIN] + args, stdin=subprocess.DEVNULL, timeout=timeout,
                           stdout=subprocess.PIPE if capture else None,
                           stderr=subprocess.PIPE if capture else None)
    except subprocess.TimeoutExpired:
        say("gbrain %s: still running after %d s, stopped" % (args[0], timeout))
        return 124, "", ""
    except OSError as e:
        say("cannot run %s: %s" % (GBRAIN, e))
        return 127, "", ""
    return p.returncode, (p.stdout or b"").decode("utf-8", "replace"), (p.stderr or b"").decode("utf-8", "replace")


def tell(rc, out, err):
    """The tail of what a captured gbrain call said, for the log, when it failed."""
    for line in (out + err).strip().splitlines()[-5:]:
        print("    " + line, flush=True)


def real(path):
    return os.path.realpath(os.path.expanduser(path))


def inside(path, root):
    return path.startswith(root.rstrip(os.sep) + os.sep)


def parse_json(text):
    """gbrain's JSON output, even when a notice line comes before it."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(("{", "[")):
            try:
                return json.loads("\n".join(lines[i:]))
            except ValueError:
                continue
    raise ValueError("no JSON in gbrain's output")


def sources():
    rc, out, _ = gbrain(["sources", "list", "--json"], capture=True)
    if rc != 0:
        return None
    try:
        return [r for r in parse_json(out)["sources"] if isinstance(r, dict) and r.get("id")]
    except (ValueError, TypeError, KeyError):
        return None


def sync(args):
    rc = gbrain(["sync"] + args + ["--no-pull", "--no-embed"], timeout=7200)[0]
    if rc != 0:
        say("gbrain sync %s failed (exit %d)" % (" ".join(args), rc))
    return rc


def slugs(source):
    """Every live page of a source, paged so a row cap (older gbrain) cannot cut the list."""
    found, offset, size = set(), 0, 500
    while True:
        rc, out, _ = gbrain(["list", "--source-id", source, "--sort", "slug", "--limit", str(size),
                             "--offset", str(offset), "--json"], capture=True)
        try:
            rows = parse_json(out) if rc == 0 else None
        except ValueError:
            rows = None
        if not isinstance(rows, list):
            return None
        found.update(r["slug"] for r in rows if isinstance(r, dict) and r.get("slug"))
        if len(rows) < size:
            return found
        offset += size


def make_default(sid):
    """Bare commands (import, put, a page the dream cycle writes) go to the vault,
    unless sources.default was already set: then it is your choice, kept."""
    rc, out, err = gbrain(["config", "get", "sources.default"], capture=True)
    if rc == 0 or "not found" not in err.lower():
        return
    r = gbrain(["sources", "default", sid], capture=True)
    if r[0] == 0:
        say("bare gbrain commands now go to the vault (sources.default = %s)" % sid)
    else:
        say("could not make '%s' the default source" % sid)
        tell(*r)


def remove_copies(sid, default):
    """Soft-deletes from 'default' the pages the vault source holds too. Only when
    'default' is gbrain's own folder: deleting a page deletes its file there."""
    folder = default.get("local_path") or ""
    if not default.get("page_count") or not folder or not inside(real(folder), real(CONTENT)):
        return
    mine, theirs = slugs(sid), slugs("default")
    if mine is None or theirs is None:
        say("could not list the pages: the copies in gbrain's own folder stay until the next run")
        return
    copies = sorted(s for s in mine & theirs if not s.startswith("-"))  # never read as an option
    if not copies:
        return
    backup = os.path.join(BACKUPS, time.strftime("%Y%m%d-%H%M%S") + "-gbrain-default")
    try:
        os.makedirs(BACKUPS, mode=0o700, exist_ok=True)
        shutil.copytree(folder, backup, symlinks=True)
        os.chmod(backup, 0o700)  # copies of your notes: yours only
    except (OSError, shutil.Error) as e:
        say("could not back up %s (%s): the copies of your notes there stay for now" % (folder, e))
        return
    say("removing the %d copies of your notes left in gbrain's own folder (backup: %s)" % (len(copies), backup))
    failed = []
    for slug in copies:
        r = gbrain(["delete", slug, "--source-id", "default", "--force"], capture=True)
        if r[0] != 0:
            if not failed:
                tell(*r)
            failed.append(slug)
    if failed:
        say("%d not removed (%s): the next run tries again" % (len(failed), ", ".join(failed[:5])))
    if len(theirs) > len(copies):
        say("%d page(s) only in gbrain's own folder are kept" % (len(theirs) - len(copies)))


def main():
    if len(sys.argv) != 2:
        print("usage: gbrain-sync-vault.py <vault>", file=sys.stderr)
        return 2
    vault = sys.argv[1]
    rows = sources()
    if rows is None:
        say("gbrain did not list its sources: syncing the vault as before")
        return sync(["--repo", vault])
    default = next((r for r in rows if r["id"] == "default"), {})
    matching = [r["id"] for r in rows if r.get("local_path") and real(r["local_path"]) == real(vault)]
    if "default" in matching or (default and not default.get("local_path")):
        return sync(["--repo", vault])  # the vault is the default source: as always

    if matching:
        sid = SOURCE_ID if SOURCE_ID in matching else matching[0]
    else:
        taken = next((r for r in rows if r["id"] == SOURCE_ID), None)
        if taken:
            say("a gbrain source named '%s' already points to %s, not to %s: left alone. If the vault moved: "
                "gbrain sources set-path %s \"%s\"" % (SOURCE_ID, taken.get("local_path"), vault, SOURCE_ID, vault))
            return 1
        r = gbrain(["sources", "add", SOURCE_ID, "--path", vault, "--federated"], capture=True)
        if r[0] != 0:
            say("gbrain sources add failed (exit %d): is %s a git repo with a commit?" % (r[0], vault))
            tell(*r)
            return r[0]
        say("vault registered as gbrain source '%s'" % SOURCE_ID)
        sid = SOURCE_ID

    rc = sync(["--source", sid])
    if rc == 0:
        make_default(sid)
        remove_copies(sid, default)
    return rc


if __name__ == "__main__":
    sys.exit(main())
