#!/usr/bin/env python3
"""gbq for Windows: the same `gbq <command>` as on macOS, behind gbq.cmd and the
Git Bash shim that install.ps1 writes.

When the installer set gbrain up (the `engine` file next to this script says
"gbrain"), every command goes to gbrain. Reads (query, search) never come back
empty-handed: if gbrain is missing, broken, or busy with a long write, the
answer comes from brain_search, the offline keyword index kept next to it.
Without gbrain, sync/embed/import rebuild that keyword index instead.

macOS and Linux keep engine/bin/gbq (bash): reads there hang on PGLite and are force-killed;
on Windows they exit on their own. A read that still runs after READ_TIMEOUT
seconds (network gone mid-request, a stuck gbrain) is killed, tree included,
and answered by keywords: Claude Code's Bash tool gives up at 120 s.
"""
import os
import subprocess
import sys
from pathlib import Path, PureWindowsPath

HERE = Path(__file__).resolve().parent
BRAIN_SEARCH = HERE / "brain_search.py"
ENGINE_FILE = HERE / "engine"
READS = {"query", "search"}
REINDEX = {"sync", "embed", "import"}
READ_TIMEOUT = float(os.environ.get("GBQ_READ_TIMEOUT") or 60)
WINDOWS = os.name == "nt"
EXE = ".exe" if WINDOWS else ""

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def note(msg):
    print(f"[gbq] {msg}", file=sys.stderr, flush=True)


def bun_bin():
    root = os.environ.get("BUN_INSTALL") or str(Path.home() / ".bun")
    return Path(root) / "bin"


def absolute_folder(d):
    """Whether `d`, a folder of the PATH, is named in full: on Windows with its drive or a share, as
    _headless_claude.absolute_folder says (Python 3.12's os.path.isabs also takes \\tools)."""
    return bool(d) and (PureWindowsPath(d).is_absolute() if WINDOWS else os.path.isabs(d))


def gbrain_exe():
    p = bun_bin() / ("gbrain" + EXE)
    if p.is_file():
        return str(p)
    # Only the PATH's absolute folders: shutil.which would look in the current folder first, where
    # `gbq` is run from (a repository, a download).
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = Path(d) / ("gbrain" + EXE)
        if absolute_folder(d) and p.is_file() and os.access(str(p), os.X_OK):
            return str(p)
    return None


def engine():
    try:
        return ENGINE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "brain_search"


def child_env():
    # A terminal opened before Bun was installed lacks ~/.bun/bin on its PATH.
    env = dict(os.environ)
    env["PATH"] = str(bun_bin()) + os.pathsep + env.get("PATH", "")
    return env


def keyword_search(args):
    words = []
    for a in args[1:]:
        if a.startswith("-"):
            break  # gbrain's own flags (--limit, --source...) mean nothing to brain_search
        words.append(a)
    if not words:
        note('usage : gbq query "ta question"')
        return 2
    return subprocess.run([sys.executable, str(BRAIN_SEARCH), "query", " ".join(words)]).returncode


def reindex():
    return subprocess.run([sys.executable, str(BRAIN_SEARCH), "index"]).returncode


def normalize_repo(args):
    # Git Bash hands gbrain C:/Users/... ; gbrain matches the vault's source by path.
    out = list(args)
    for i, a in enumerate(out[:-1]):
        if a == "--repo":
            out[i + 1] = os.path.normpath(os.path.abspath(out[i + 1]))
    return out


def kill_tree(p):
    # gbrain.exe is a shim that starts bun.exe: killing the shim alone leaves bun running.
    if WINDOWS:
        taskkill = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "taskkill.exe")
        subprocess.run([taskkill, "/F", "/T", "/PID", str(p.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        p.kill()
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def gbrain_read(exe, args):
    """gbrain's stdout goes straight through; stderr is read to spot a busy brain."""
    for attempt in (1, 2):
        try:
            p = subprocess.Popen([exe, *args], stderr=subprocess.PIPE, env=child_env())
        except OSError as e:
            note(f"gbrain ne demarre pas ({e}) : recherche par mots-cles")
            return None
        try:
            _, err = p.communicate(timeout=READ_TIMEOUT)
        except subprocess.TimeoutExpired:
            kill_tree(p)
            note(f"gbrain n'a pas repondu en {READ_TIMEOUT:.0f} s : resultats par mots-cles")
            return None
        err = err.decode("utf-8", errors="replace")
        if p.returncode == 0:
            sys.stderr.write(err)
            return 0
        if "pglite_busy" in err and attempt == 1:
            note("le cerveau se met a jour (synchronisation en cours), nouvel essai...")
            continue
        sys.stderr.write(err)
        why = "occupe par une mise a jour" if "pglite_busy" in err else f"erreur {p.returncode}"
        note(f"gbrain n'a pas repondu ({why}) : resultats par mots-cles")
        return None


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd in READS:
        # As gbq: a read starts gbrain from this folder, the kit's, and none of the caller's (Bun reads
        # a bunfig.toml, whose preload runs before gbrain, a .env and a tsconfig.json where it starts).
        # A write keeps the caller's folder: gbq import .\notes.
        os.chdir(str(HERE))
    exe = gbrain_exe() if engine() == "gbrain" else None

    if cmd in READS:
        if exe:
            rc = gbrain_read(exe, argv)
            if rc is not None:
                return rc
        elif engine() == "gbrain":
            note("gbrain introuvable : resultats par mots-cles (relance l'installation pour le reparer)")
        return keyword_search(argv)

    if not exe:
        if cmd in REINDEX:
            return reindex()
        if engine() == "gbrain":
            note("gbrain introuvable : relance la commande d'installation pour le reparer.")
        else:
            note("gbrain n'est pas installe sur ce PC : seule la recherche par mots-cles marche (gbq query).")
        return 1

    if cmd == "sync":
        argv = normalize_repo(argv)
    try:
        rc = subprocess.run([exe, *argv], env=child_env()).returncode
    except OSError as e:
        note(f"gbrain ne demarre pas ({e})")
        return 1
    if cmd in ("sync", "import") and rc == 0:
        # Keeps the fallback index as fresh as gbrain.
        subprocess.run([sys.executable, str(BRAIN_SEARCH), "index"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
