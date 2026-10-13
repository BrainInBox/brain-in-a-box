#!/usr/bin/env python3
"""brain-in-a-box nightly maintenance on Windows (Task Scheduler "BrainNightly", 04:00),
the counterpart of gbrain-nightly.sh on macOS:

  commit the vault -> gbrain sync -> gbrain embed --stale --max-usd 0.10
  -> rebuild the keyword index (brain_search) that gbq falls back to.

The gbrain steps run only when install.ps1 set gbrain up; the keyword index is
always rebuilt. Runs under pythonw: no window, so every child gets CREATE_NO_WINDOW.
Log: ~/.gbrain/nightly.log (same place as on macOS).

    windows-nightly.py --brain <vault> [--git <git.exe>]
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
GDATA = Path(os.environ.get("GBRAIN_HOME", "").strip() or HOME) / ".gbrain"
BSDIR = HOME / ".brain-search"
BUN_BIN = Path(os.environ.get("BUN_INSTALL") or HOME / ".bun") / "bin"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LOG = GDATA / "nightly.log"


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def run(cmd, cwd=None, timeout=600, env=None):
    """Runs cmd with its output appended to the log. Returns the exit code (124 on timeout)."""
    with open(LOG, "ab") as out:
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brain", required=True)
    ap.add_argument("--git", default=shutil.which("git"))
    args = ap.parse_args()
    vault = Path(args.brain)
    GDATA.mkdir(parents=True, exist_ok=True)
    log(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} nightly start =====")

    # gbrain sync reads the vault through git: without a commit, today's notes are invisible.
    commit_vault(args.git, vault)

    try:
        engine = (BSDIR / "engine").read_text(encoding="utf-8").strip()
    except OSError:
        engine = "brain_search"
    gbrain = BUN_BIN / "gbrain.exe"
    if not gbrain.is_file():
        gbrain = BUN_BIN / "gbrain"
    if engine == "gbrain" and gbrain.is_file():
        env = dict(os.environ)
        extra = [str(BUN_BIN)] + ([str(Path(args.git).parent)] if args.git else [])
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
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
        import traceback
        log(traceback.format_exc())
        sys.exit(1)
