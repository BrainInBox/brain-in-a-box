#!/usr/bin/env python3
"""brain-in-a-box nightly maintenance on Windows (Task Scheduler "BrainNightly", 04:00),
the counterpart of gbrain-nightly.sh on macOS:

  gbrain update (newest release at least 7 days old) -> night check of the vault
  -> commit the vault -> gbrain sync -> gbrain embed --stale --max-usd 0.10
  -> rebuild the keyword index (brain_search) that gbq falls back to.

The gbrain steps run only when install.ps1 set gbrain up; the night check only
once controle.py is installed next to this script; the keyword index is always
rebuilt. The vault is committed even when the check fails or crashes. Runs under
pythonw: no window, so every child gets CREATE_NO_WINDOW.
Log: ~/.gbrain/nightly.log (same place as on macOS).

    windows-nightly.py --brain <vault> [--git <git.exe>]
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

HOME = Path.home()
HERE = Path(__file__).resolve().parent  # ~/.claude/hooks/brain once installed
GDATA = Path(os.environ.get("GBRAIN_HOME", "").strip() or HOME) / ".gbrain"
BSDIR = HOME / ".brain-search"
BUN_BIN = Path(os.environ.get("BUN_INSTALL") or HOME / ".bun") / "bin"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LOG = GDATA / "nightly.log"


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def run(cmd, cwd=None, timeout=600, env=None, out=None):
    """Runs cmd with its output appended to the log, or written to out. Returns the exit code (124 on timeout)."""
    if out is None:
        with open(LOG, "ab") as f:
            return run(cmd, cwd, timeout, env, f)
    try:
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out,
                             stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
    except OSError as e:
        log(f"[run] cannot start {cmd[0]}: {e}")
        return 127
    try:
        return p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # gbrain.exe is a shim that starts bun.exe: kill the whole tree, the OS frees the brain's lock.
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        p.wait()
        log(f"[run] {Path(cmd[0]).name} {cmd[1] if len(cmd) > 1 else ''} killed after {timeout}s")
        return 124


def commit_vault(git, vault):
    if not git or not (vault / ".git").is_dir():
        return
    lock = vault / ".git" / "index.lock"
    # A crashed git leaves index.lock behind and every later commit fails silently (macOS field
    # case, 2026-06-28). A nightly at 04:00 is alone with the vault: an old lock is an orphan.
    if lock.exists() and time.time() - lock.stat().st_mtime > 600:
        lock.unlink()
        log("[git] removed a stale .git/index.lock")
    status = subprocess.run([git, "status", "--porcelain"], cwd=vault, capture_output=True,
                            creationflags=NO_WINDOW)
    if status.returncode or not status.stdout.strip():
        return
    run([git, "add", "-A"], cwd=vault)
    rc = run([git, "-c", "user.email=brain@local", "-c", "user.name=brain", "commit", "-q",
              "-m", f"nightly {time.strftime('%Y-%m-%d')}"], cwd=vault)
    if rc == 0:
        log("[git] vault committed")


def step_env(env):
    """Neither the update nor the check needs a credential: no *KEY*, *TOKEN*, *SECRET* or
    *PASSWORD* variable, and no git setting passed through the environment (a GIT_CONFIG_VALUE_n
    can be an auth header, and dropping only its GIT_CONFIG_KEY_n half would break git)."""
    return {k: v for k, v in env.items()
            if not any(w in k.upper() for w in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
            and not re.match(r"GIT_(CONFIG|ASKPASS$|DIR$|WORK_TREE$|INDEX_FILE$)", k.upper())}


def step(name, script, args, timeout, env):
    """Runs a Python script installed next to this one; a missing script is skipped. Its output
    goes through a file of its own: these scripts also append to the log themselves, and on
    Windows a shared handle would write their output over their own lines."""
    if not script.is_file():
        log(f"[{name}] not installed: skipped")
        return
    with tempfile.TemporaryFile() as out:
        try:
            rc = run([sys.executable, "-I", str(script)] + args, timeout=timeout, env=env, out=out)
        except Exception:
            rc = 1
            out.write(traceback.format_exc().encode("utf-8", "replace"))
        out.seek(0)
        text = out.read()
    if text:
        with open(LOG, "ab") as f:
            f.write(text if text.endswith(b"\n") else text + b"\n")
    log(f"[{name}] exit {rc}")


def main():
    ap = argparse.ArgumentParser(
        description="Maintenance de nuit de brain-in-a-box sous Windows (tâche BrainNightly).",
        epilog="Chemins : journal dans $GBRAIN_HOME/.gbrain (sinon ~/.gbrain)/nightly.log ; gbrain dans "
               "$BUN_INSTALL/bin (sinon ~/.bun/bin) ; index par mots-clés dans ~/.brain-search. "
               "gbrain_update.py et controle.py sont pris à côté de ce script. Pour un essai isolé, "
               "change HOME (USERPROFILE sous Windows).")
    ap.add_argument("--brain", required=True, help="le dossier du cerveau (vault)")
    ap.add_argument("--git", default=shutil.which("git"), help="chemin de git (défaut : celui du PATH)")
    args = ap.parse_args()
    vault = Path(args.brain)
    GDATA.mkdir(parents=True, exist_ok=True)
    log(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} nightly start =====")

    try:
        engine = (BSDIR / "engine").read_text(encoding="utf-8").strip()
    except OSError:
        engine = "brain_search"
    gbrain = BUN_BIN / "gbrain.exe"
    if not gbrain.is_file():
        gbrain = BUN_BIN / "gbrain"
    env = dict(os.environ)
    extra = [str(BUN_BIN)] + ([str(Path(args.git).parent)] if args.git else [])
    env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    git_arg = ["--git", args.git] if args.git else []
    py_env = step_env(env)

    # Each step logs its exit code and never stops the night: the vault is committed whatever happens.
    # The update keeps to 45 min, but a rollback is never cut short (up to ~25 min more): killing
    # it halfway would leave gbrain broken, hence 90 min here.
    if engine == "gbrain" and gbrain.is_file():
        step("gbrain-update", HERE / "gbrain_update.py", ["--log", str(LOG)] + git_arg, 5400, py_env)
    step("controle", HERE / "controle.py", ["nuit", "--brain", str(vault)] + git_arg, 1500, py_env)

    # gbrain sync reads the vault through git: without a commit, today's notes are invisible.
    commit_vault(args.git, vault)

    if engine == "gbrain" and gbrain.is_file():
        rc = run([str(gbrain), "sync", "--source", "vault", "--no-pull", "--no-embed"], timeout=7200, env=env)
        log(f"[sync] exit {rc}")
        # Since gbrain 0.60.106 a paid run needs a cap; a keyword brain declines cleanly.
        rc = run([str(gbrain), "embed", "--stale", "--max-usd", "0.10"], timeout=3600, env=env)
        log(f"[embed] exit {rc}")
    elif engine == "gbrain":
        log("[gbrain] not found: keyword index only (re-run the installer to repair)")

    rc = run([sys.executable, str(BSDIR / "brain_search.py"), "index", "--brain", str(vault)])
    log(f"[brain_search] exit {rc}")
    log(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} nightly done =====")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # pythonw has no console: a crash is only visible in the log
        log(traceback.format_exc())
        sys.exit(1)
