#!/usr/bin/env python3
"""Moves gbrain to its newest release that has been public for at least 7 days.

Same code on macOS and Windows. Run by the nightly (through gbrain-selfupdate.sh on
macOS), by the SessionStart catch-up hook and by `controle gbrain maintenant`.

Only `refs/tags/v*` are installed: never master, never a lower version (the brain
may already be migrated). A tag's age counts from the first time this machine saw
it, or from its GitHub release date when older, never from the commit date, which a
lightweight tag lets anyone pick. Bun is the exact version gbrain's release workflow
uses, checked against Bun's SHASUMS256.txt. A snapshot (code, Bun, config.json,
brain.pglite) is taken before the switch; any failure restores all of it, and
gbrain doctor must prove the rollback.

Not a hook: installed next to them, never registered in settings.json.
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

UPSTREAM = "https://github.com/garrytan/gbrain.git"
RELEASES_API = "https://api.github.com/repos/garrytan/gbrain/releases"
BUN_RELEASES = "https://github.com/oven-sh/bun/releases/download"
MIN_AGE = dt.timedelta(days=7)
PAUSE = dt.timedelta(days=7)
KEEP = dt.timedelta(days=3)
LATE_DAYS = 14
LOCK_STALE_S = 2 * 3600
BUDGET_S = 45 * 60
DISK_MARGIN_MB = 500
BUN_ZIP_MAX = 200 * 2 ** 20
TIMEOUT = {"ls-remote": 120, "fetch": 300, "download": 300, "install": 900,
           "migrate": 900, "doctor": 300, "copy": 900, "short": 60}
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(\.\d+)?$")
OID_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
STAMP = "%Y%m%d-%H%M%S"
SECRET_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
GIT_ENV_DROP = re.compile(r"^GIT_(DIR|WORK_TREE|INDEX_FILE|ASKPASS|CONFIG_PARAMETERS|CONFIG_COUNT"
                          r"|CONFIG_KEY_\d+|CONFIG_VALUE_\d+)$")
WINDOWS = os.name == "nt"
EXE = ".exe" if WINDOWS else ""
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
UTC = dt.timezone.utc


class Postpone(Exception):
    """Stops the run before anything is switched: status postponed, with its cause."""

    def __init__(self, cause, code):
        super().__init__(cause)
        self.cause, self.code = cause, code


def iso(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(text):
    if not isinstance(text, str) or not text.strip():
        return None
    s = text.strip()
    if s[-1] in "Zz":
        s = s[:-1] + "+00:00"
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=UTC)).astimezone(UTC)


def version(text):
    """'v0.60.210.0' or '0.60.210.0' -> (0, 60, 210, 0); None for anything else."""
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?$", text.strip()) if isinstance(text, str) else None
    return tuple(int(x or 0) for x in m.groups()) if m else None


def system_tool(name):
    """Windows tools by full path: the children's PATH starts with Bun's folder."""
    path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", name)
    return path if os.path.isfile(path) else name


def child_env(bin_dir):
    """The current environment minus anything that looks like a credential, and minus git
    settings passed through the environment (a Claude Code session sets GIT_CONFIG_COUNT
    and GIT_CONFIG_KEY_n/VALUE_n pairs: a value can be an auth header, and dropping only
    the KEY half would leave git unable to start)."""
    env = {k: v for k, v in os.environ.items()
           if not any(w in k.upper() for w in SECRET_WORDS) and not GIT_ENV_DROP.match(k.upper())}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["PATH"] = os.pathsep.join(p for p in (str(bin_dir), env.get("PATH", "")) if p)
    return env


def kill_tree(p):
    if WINDOWS:
        subprocess.run([system_tool("taskkill.exe"), "/T", "/F", "/PID", str(p.pid)], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    else:
        try:
            os.killpg(p.pid, signal.SIGTERM)
            p.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:  # a grandchild may outlive its parent's SIGTERM
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        p.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def run(cmd, timeout, cwd=None, env=None):
    """Runs cmd without a shell. Returns (exit code, stdout, stderr): 124 when it ran out
    of time (its whole process tree is then killed), 127 when it could not start.
    Output goes through temporary files: a grandchild holding a pipe cannot hang us."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        kw = {"cwd": cwd, "env": env, "stdin": subprocess.DEVNULL, "stdout": out, "stderr": err}
        if WINDOWS:
            kw["creationflags"] = NO_WINDOW | NEW_GROUP
        else:
            kw["start_new_session"] = True
        try:
            p = subprocess.Popen([str(c) for c in cmd], **kw)
        except OSError as e:
            return 127, "", str(e)
        try:
            rc = p.wait(timeout=max(1, timeout))
        except subprocess.TimeoutExpired:
            kill_tree(p)
            rc = 124
        out.seek(0)
        err.seek(0)
        return rc, out.read().decode("utf-8", "replace"), err.read().decode("utf-8", "replace")


def pid_alive(pid):
    if pid <= 0:
        return False
    if WINDOWS:
        import ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = ctypes.c_void_p
        k.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        k.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
        k.CloseHandle.argtypes = (ctypes.c_void_p,)
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5  # access denied: it exists
        try:
            code = ctypes.c_ulong()
            return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class Lock:
    """O_EXCL file holding "<pid> <epoch>"; stale after 2 h or once its process is gone."""

    def __init__(self, path):
        self.path, self.held = path, False

    def acquire(self):
        for _ in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if not self.stale():
                    return False
                try:
                    self.path.unlink()
                except OSError:
                    return False
                continue
            with os.fdopen(fd, "w") as f:
                f.write("%d %d\n" % (os.getpid(), int(time.time())))
            self.held = True
            return True
        return False

    def stale(self):
        try:
            pid, started = (int(x) for x in self.path.read_text().split()[:2])
        except (OSError, ValueError):
            try:  # just created, not written yet: only age can tell
                return time.time() - self.path.stat().st_mtime > LOCK_STALE_S
            except OSError:
                return True
        return time.time() - started > LOCK_STALE_S or not pid_alive(pid)

    def release(self):
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass


def read_json(path):
    try:
        with open(str(path), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path, data):
    tmp = path.with_name(path.name + ".tmp")
    with open(str(tmp), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(str(tmp), str(path))


def parse_json(text):
    """gbrain's JSON output, even when a notice line comes before it."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(("{", "[")):
            try:
                return json.loads("\n".join(lines[i:]))
            except ValueError:
                continue
    return None


def inside(path, root):
    p, r = os.path.realpath(str(path)), os.path.realpath(str(root))
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def unique(path):
    n, candidate = 1, path
    while candidate.exists() or candidate.is_symlink():
        n += 1
        candidate = path.with_name("%s-%d" % (path.name, n))
    return candidate


def stamp_of(name):
    """The trailing YYYYmmdd-HHMMSS of a name this script gave (snapshot, old Bun, failed base)."""
    m = re.search(r"(\d{8}-\d{6})(?:-\d+)?$", name)
    try:
        return dt.datetime.strptime(m.group(1), STAMP).replace(tzinfo=UTC) if m else None
    except ValueError:
        return None


def tree_size(path):
    total = 0
    for root, _, files in os.walk(str(path)):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def sysctl(key):
    try:
        p = subprocess.run(["/usr/sbin/sysctl", "-n", key], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
        return p.stdout.decode("utf-8", "replace").strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def bun_asset():
    """Name of Bun's release asset for this machine, without .zip."""
    system, machine = platform.system(), platform.machine().lower()
    if system == "Darwin":
        if machine == "arm64" or sysctl("hw.optional.arm64") == "1":
            return "bun-darwin-aarch64"
        return "bun-darwin-x64" if "AVX2" in sysctl("machdep.cpu.leaf7_features").upper() else "bun-darwin-x64-baseline"
    if system == "Windows":
        if machine in ("amd64", "x86_64"):
            import ctypes
            avx2 = ctypes.windll.kernel32.IsProcessorFeaturePresent(40)  # PF_AVX2_INSTRUCTIONS_AVAILABLE
            return "bun-windows-x64" if avx2 else "bun-windows-x64-baseline"
        return "bun-windows-aarch64" if machine in ("arm64", "aarch64") else "bun-windows-" + machine
    arch = {"x86_64": "x64", "amd64": "x64", "arm64": "aarch64"}.get(machine, machine)
    if arch == "x64":
        try:
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
                if " avx2" not in f.read():
                    arch = "x64-baseline"
        except OSError:
            pass
    return "bun-%s-%s" % (system.lower(), arch)


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise urllib.error.HTTPError(newurl, code, "redirect away from https refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, dest, limit, timeout):
    """Streams url into dest; raises past `limit` bytes or `timeout` seconds overall."""
    opener = urllib.request.build_opener(_HttpsOnly) if url.lower().startswith("https://") else urllib.request.build_opener()
    req = urllib.request.Request(url, headers={"User-Agent": "brain-in-a-box", "Accept": "*/*"})
    end, total = time.monotonic() + timeout, 0
    with opener.open(req, timeout=30) as r, open(str(dest), "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                return total
            total += len(chunk)
            if total > limit or time.monotonic() > end:
                raise OSError("download too big or too slow")
            f.write(chunk)


def sha256(path):
    h = hashlib.sha256()
    with open(str(path), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def replace_bun(target, new, suffix):
    """Puts the file `new` in place of `target`. On Windows a running bun.exe cannot be
    overwritten but can be renamed: it moves aside as <name>.<suffix>, purged 3 days later."""
    if not WINDOWS:
        tmp = target.with_name("." + target.name + ".new")
        shutil.copy2(str(new), str(tmp))
        os.chmod(str(tmp), 0o755)
        os.replace(str(tmp), str(target))
        return
    aside = None
    if target.exists():
        aside = unique(target.with_name("%s.%s" % (target.name, suffix)))
        os.rename(str(target), str(aside))
    try:
        shutil.copy2(str(new), str(target))
    except OSError:
        if aside:
            os.rename(str(aside), str(target))
        raise


def copy_tree(src, dst, env):
    if sys.platform == "darwin":  # an APFS clone: instant, and no extra space
        if run(["/bin/cp", "-c", "-R", str(src), str(dst)], TIMEOUT["copy"], env=env)[0] == 0:
            return
        shutil.rmtree(str(dst), ignore_errors=True)
    shutil.copytree(str(src), str(dst), symlinks=True)


class Updater:
    def __init__(self, args):
        self.args, self.dry = args, args.a_blanc
        home = Path.home()
        self.repo = home / "DEV" / "gbrain"
        self.data = Path(os.environ.get("GBRAIN_HOME", "").strip() or str(home)) / ".gbrain"
        bin_dir = args.bin_dir or Path(os.environ.get("BUN_INSTALL", "").strip() or str(home / ".bun")) / "bin"
        self.bin = Path(bin_dir)
        self.bun, self.gbrain = self.bin / ("bun" + EXE), self.bin / ("gbrain" + EXE)
        self.log_path = Path(args.log) if args.log else self.data / "nightly.log"
        self.remote = args.remote or UPSTREAM
        self.releases = args.releases_url or (None if args.remote else RELEASES_API)
        self.bun_base = (args.bun_base_url or BUN_RELEASES).rstrip("/")
        self.now = parse_time(args.maintenant) if args.maintenant else dt.datetime.now(UTC).replace(microsecond=0)
        self.git = args.git or shutil.which("git")
        self.env = child_env(self.bin)
        self.neutral = tempfile.gettempdir()  # outside any repository: no repo config applies
        self.deadline = time.monotonic() + BUDGET_S
        self.state, self.listing, self.db = {}, None, None
        self.before = self.installed = None
        self.alerts, self.changelog, self.bun_change = [], [], None

    # -- plumbing --------------------------------------------------------------

    def log(self, msg):
        line = "[gbrain-update] " + msg
        if self.dry or (sys.stdout is not None and sys.stdout.isatty()):
            print(line, flush=True)
        if self.dry:
            return
        try:
            with open(str(self.log_path), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def sh(self, cmd, timeout, cwd=None, show=False, hide=False, budget=True):
        """run() within the 45 min budget, logged: the exit code of a failure, and the last
        lines of its output (20 lines even on success with show; none with hide, for
        outputs read as data such as doctor's JSON)."""
        if budget:
            left = self.deadline - time.monotonic()
            if left <= 1:
                return 124, "", "time budget spent"
            timeout = min(timeout, left)
        rc, out, err = run(cmd, timeout, cwd=cwd, env=self.env)
        name = " ".join(str(c) for c in ([Path(str(cmd[0])).name] + list(cmd[1:3])))
        if rc == 124:
            self.log("%s : arrêté après %d s" % (name, timeout))
        elif rc:
            self.log("%s : code %d" % (name, rc))
        if (show or rc) and not hide:
            for line in (out + err).strip().splitlines()[-(20 if show else 5):]:
                self.log("    " + line[:300])
        return rc, out, err

    def git_(self, *args, timeout=TIMEOUT["short"], cwd=None, budget=True):
        return self.sh([self.git] + list(args), timeout, cwd=str(cwd or self.repo), budget=budget)

    def alert(self, level, code, tag=None):
        a = {"niveau": level, "code": code}
        if tag:
            a["tag"] = tag
        if a not in self.alerts:
            self.alerts.append(a)

    # -- state -----------------------------------------------------------------

    def load_state(self):
        st = read_json(self.data / "update-state.json")
        st = st if isinstance(st, dict) else {}
        tags = st.get("tags") if isinstance(st.get("tags"), dict) else {}
        st["tags"] = {t: e for t, e in tags.items()
                      if TAG_RE.match(t) and isinstance(e, dict) and isinstance(e.get("sha"), str)}
        blocked = st.get("bloques") if isinstance(st.get("bloques"), dict) else {}
        st["bloques"] = {t: e for t, e in blocked.items() if TAG_RE.match(t) and isinstance(e, dict)}
        for k in ("echecs_consecutifs", "ajournements_consecutifs"):
            st[k] = st[k] if isinstance(st.get(k), int) and st[k] >= 0 else 0
        for k in ("pause_jusqua", "derniere_verif_ok", "dernier_succes"):
            st[k] = st[k] if parse_time(st.get(k)) else None
        return st

    def first_seen(self, entry):
        times = [t for t in (parse_time(entry.get("vu_le")), parse_time(entry.get("publie_le"))) if t]
        return min(times) if times else self.now

    def open_tags(self):
        """Tags above the installed version, not blocked, still listed upstream: (version, tag, first seen)."""
        inst = version(self.installed)
        found = []
        for tag, e in self.state["tags"].items():
            v = version(tag)
            if v is None or v <= inst or tag in self.state["bloques"]:
                continue
            if self.listing is not None and self.listing.get(tag) != e["sha"]:
                continue
            found.append((v, tag, self.first_seen(e)))
        return found

    def candidate(self):
        ripe = [(v, tag) for v, tag, seen in self.open_tags() if self.now - seen >= MIN_AGE]
        return max(ripe)[1] if ripe else None

    def next_tag(self):
        later = [(seen + MIN_AGE, tuple(-x for x in v), tag) for v, tag, seen in self.open_tags()
                 if self.now - seen < MIN_AGE]
        if not later:
            return None
        when, _, tag = min(later)
        return {"tag": tag, "eligible_le": iso(when)}

    def lateness(self):
        seen = [s for _, _, s in self.open_tags()]
        return max(0, (self.now - min(seen)).days) if seen else 0

    def finish(self, status, cause=None, code=None, tag=None):
        st = self.state
        if status == "postponed":
            st["ajournements_consecutifs"] += 1
        elif status != "check_failed":
            st["ajournements_consecutifs"] = 0
        late = self.lateness() if self.installed else 0
        if late > LATE_DAYS:
            self.alert("rouge", "retard")
        if st["ajournements_consecutifs"] >= 3:
            self.alert("orange", "ajourne")
        nxt = self.next_tag() if self.installed else None
        bun = self.bun_version()
        result = {
            "date": iso(self.now), "status": status, "cause": cause, "cause_code": code, "tag": tag,
            "before": "v" + self.before if self.before else None,
            "after": "v" + self.installed if self.installed else None,
            "bun": bun, "bun_change": self.bun_change, "changelog": self.changelog,
            "derniere_verif_ok": st["derniere_verif_ok"], "dernier_succes": st["dernier_succes"],
            "retard_jours": late, "prochaine": nxt, "bloques": st["bloques"],
            "echecs_consecutifs": st["echecs_consecutifs"], "pause_jusqua": st["pause_jusqua"],
            "ajournements_consecutifs": st["ajournements_consecutifs"], "alertes": self.alerts,
        }
        words = {
            "up_to_date": "à jour",
            "waiting": "attendre : prochaine version %s, éligible le %s" % (
                (nxt or {}).get("tag"), ((nxt or {}).get("eligible_le") or "")[:10]),
            "paused": "en pause jusqu'au %s" % (st["pause_jusqua"] or "")[:10],
            "updated": "mis à jour : %s -> %s" % (result["before"], result["after"]),
            "candidate": "installer",
            "postponed": "reporté",
            "check_failed": "vérification impossible",
            "rolled_back": "retour arrière",
            "broken": "gbrain cassé",
        }
        self.log("décision : %s%s%s (gbrain %s, retard %d j)" % (
            words.get(status, status), " : " + cause if cause else "", " [%s]" % tag if tag else "",
            result["after"] or "inconnu", late))
        if self.dry:
            self.log("à blanc : rien n'a été installé ni écrit.")
            return 0
        if self.installed:
            self.forget_below()
        write_json(self.data / "update-state.json", st)
        write_json(self.data / "last-update.json", result)
        return 0

    # -- reading ---------------------------------------------------------------

    def bun_version(self):
        if not self.bun.exists():
            return None
        rc, out, _ = run([self.bun, "--version"], TIMEOUT["short"], env=self.env)
        m = re.match(r"^\s*(\d+\.\d+\.\d+)", out)
        return m.group(1) if rc == 0 and m else None

    def package_version(self, rev):
        rc, out, _ = self.git_("show", "%s:package.json" % rev)
        try:
            v = json.loads(out).get("version") if rc == 0 else None
        except (ValueError, AttributeError):
            return None
        return v if version(v) else None

    def list_tags(self):
        rc, out, _ = self.sh([self.git, "-c", "credential.helper=", "ls-remote", "--tags", "--refs",
                              self.remote, "refs/tags/v*"], TIMEOUT["ls-remote"], cwd=self.neutral)
        if rc != 0:
            return None
        tags = {}
        for line in out.splitlines():
            parts = line.strip().split("\t")
            if len(parts) == 2 and parts[1].startswith("refs/tags/"):
                name = parts[1][len("refs/tags/"):]
                if TAG_RE.match(name) and OID_RE.match(parts[0]):
                    tags[name] = parts[0]
        return tags

    def register(self):
        tags, blocked, inst = self.state["tags"], self.state["bloques"], version(self.installed)
        for tag, sha in self.listing.items():
            if version(tag) <= inst:
                continue
            e = tags.get(tag)
            if e is None:
                tags[tag] = {"sha": sha, "vu_le": iso(self.now)}
            elif e["sha"] != sha:
                tags[tag] = {"sha": sha, "vu_le": iso(self.now)}
                blocked.setdefault(tag, {"cause": "tag déplacé en amont", "le": iso(self.now)})
                self.alert("orange", "tag_deplace", tag)
                self.log("%s a changé de commit en amont : version bloquée" % tag)
        self.forget_below()

    def forget_below(self):
        """Tags at or below the installed version can never be candidates: drop them."""
        inst = version(self.installed)
        for d in (self.state["tags"], self.state["bloques"]):
            for tag in [t for t in d if version(t) <= inst]:
                del d[tag]

    def fetch_json(self, url):
        try:
            opener = urllib.request.build_opener(_HttpsOnly)
            req = urllib.request.Request(url, headers={"User-Agent": "brain-in-a-box",
                                                       "Accept": "application/vnd.github+json"})
            with opener.open(req, timeout=20) as r:
                return json.loads(r.read(5 * 2 ** 20 + 1)[:5 * 2 ** 20].decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError):
            return None

    def add_release_dates(self):
        """GitHub's own publication date: it cannot be backdated, unlike a commit date."""
        if not self.releases:
            return
        dated = 0
        for page in (1, 2):
            data = self.fetch_json("%s?per_page=100&page=%d" % (self.releases, page))
            if not isinstance(data, list):
                break
            for rel in data:
                if not isinstance(rel, dict) or rel.get("draft"):
                    continue
                tag, when = rel.get("tag_name"), parse_time(rel.get("published_at"))
                e = self.state["tags"].get(tag) if isinstance(tag, str) else None
                if when and e is not None and tag not in self.state["bloques"]:
                    e["publie_le"] = iso(when)
                    dated += 1
            if len(data) < 100:
                break
        self.log("dates de publication GitHub lues : %d" % dated)

    def database(self):
        cfg = read_json(self.data / "config.json")
        cfg = cfg if isinstance(cfg, dict) else {}
        if cfg.get("database_url") or cfg.get("engine") == "postgres":
            raise Postpone("base Postgres : mise à jour de gbrain à faire à la main", "db_unsupported")
        path = cfg.get("database_path")
        db = Path(os.path.expanduser(path.strip())) if isinstance(path, str) and path.strip() else self.data / "brain.pglite"
        if not inside(db, self.data):
            raise Postpone("base hors de ~/.gbrain : mise à jour de gbrain à faire à la main", "db_unsupported")
        return db

    def gbrain_processes(self):
        """What would make a switch unsafe: a running bun/gbrain. None when it cannot be checked."""
        if WINDOWS:
            rc, out, _ = run([system_tool("tasklist.exe"), "/FO", "CSV", "/NH"], 30, env=self.env)
            if rc != 0:
                return None
            names = [row[0].lower() for row in csv.reader(out.splitlines()) if row]
            return [n for n in names if n in ("bun.exe", "gbrain.exe")]
        rc, out, _ = run(["/bin/ps", "-axww", "-o", "pid=,ppid=,command="], 30, env=self.env)
        if rc != 0:
            return None
        procs = {}
        for line in out.splitlines():
            parts = line.split(None, 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                procs[int(parts[0])] = (int(parts[1]), parts[2])
        mine, pid = set(), os.getpid()
        while pid in procs and pid not in mine:
            mine.add(pid)
            pid = procs[pid][0]
        scope = str(self.bin) if self.args.bin_dir else ""
        return [cmd for pid, (_, cmd) in procs.items()
                if pid not in mine and re.search(r"bun.*gbrain", cmd) and scope in cmd]

    def prerequisites(self):
        """Raises Postpone when touching gbrain now would not be safe."""
        rc, out, _ = self.git_("status", "--porcelain", "--untracked-files=no")
        if rc != 0:
            raise Postpone("git status a échoué dans ~/DEV/gbrain", "clone_modified")
        changed = [line[3:].strip().strip('"') for line in out.splitlines() if line.strip()]
        if changed and set(changed) != {"bun.lock"}:
            raise Postpone("clone gbrain modifié à la main (%d fichier(s))" % len(changed), "clone_modified")
        if changed and not self.dry:
            if self.git_("checkout", "--", "bun.lock")[0] != 0:
                raise Postpone("bun.lock modifié et impossible à remettre", "clone_modified")
            self.log("bun.lock (fichier généré) remis comme dans le dépôt")
        self.db = self.database()
        if (self.db / ".gbrain-lock").exists():
            raise Postpone("base gbrain verrouillée (.gbrain-lock)", "db_locked")
        procs = self.gbrain_processes()
        if procs is None:
            raise Postpone("impossible de vérifier si gbrain tourne", "busy")
        if procs:
            raise Postpone("gbrain est en cours d'utilisation", "busy")
        need = (self.args.place_requise_mo if self.args.place_requise_mo is not None else DISK_MARGIN_MB) * 2 ** 20
        if WINDOWS and self.db.exists():  # no APFS clone: the snapshot is a real copy
            need += 2 * tree_size(self.db)
        where = self.data
        while not where.exists() and where != where.parent:
            where = where.parent
        free = shutil.disk_usage(str(where)).free
        if free < need:
            raise Postpone("place disque insuffisante (%d Mo libres, %d Mo nécessaires)"
                           % (free // 2 ** 20, need // 2 ** 20), "disk_space")
        if self.sh([self.gbrain, "--version"], TIMEOUT["short"], cwd=self.neutral)[0] != 0:
            raise Postpone("gbrain ne démarre pas : relance l'installation", "gbrain_down")

    def health(self, budget=True):
        rc, out, _ = self.sh([self.gbrain, "doctor", "--fast", "--json"], TIMEOUT["doctor"], cwd=self.neutral,
                             hide=True, budget=budget)
        doc = parse_json(out)
        fails = None
        if isinstance(doc, dict) and isinstance(doc.get("checks"), list):
            fails = frozenset(c["name"] for c in doc["checks"] if isinstance(c, dict)
                              and c.get("status") == "fail" and isinstance(c.get("name"), str))
        src_rc, src_out, _ = self.sh([self.gbrain, "sources", "list", "--json"], TIMEOUT["doctor"],
                                     cwd=self.neutral, hide=True, budget=budget)
        doc = parse_json(src_out) if src_rc == 0 else None
        sources = None
        if isinstance(doc, dict) and isinstance(doc.get("sources"), list):
            sources = frozenset(s["id"] for s in doc["sources"] if isinstance(s, dict) and isinstance(s.get("id"), str))
        return {"rc": rc, "fails": fails, "sources": sources}

    @staticmethod
    def regression(before, after, strict):
        """What got worse between two health() readings; None if nothing did. Not strict
        (proving a rollback): only what could be read before is compared."""
        if before["fails"] is not None or strict:
            if after["fails"] is None:
                return "gbrain doctor illisible"
            if before["rc"] == 0 and after["rc"] != 0:
                return "gbrain doctor en échec"
            new = after["fails"] - (before["fails"] or frozenset())
            if new:
                return "nouveaux contrôles en échec : " + ", ".join(sorted(new))[:200]
        if before["sources"] is not None or strict:
            if after["sources"] is None:
                return "liste des sources illisible"
            if before["sources"] and not before["sources"] <= after["sources"]:
                return "sources disparues"
        return None

    # -- the update ------------------------------------------------------------

    def fetch_tag(self, tag):
        shallow = self.git_("rev-parse", "--is-shallow-repository")[1].strip() == "true"
        rc = self.sh([self.git, "-c", "credential.helper=", "fetch", "-q", "--no-tags"]
                     + (["--depth", "1"] if shallow else [])
                     + [self.remote, "+refs/tags/%s:refs/tags/%s" % (tag, tag)],
                     TIMEOUT["fetch"], cwd=str(self.repo))[0]
        if rc != 0:
            raise Postpone("téléchargement de %s impossible (réseau ?)" % tag, "fetch_failed")
        # The object the tag names, as ls-remote reported it (a tag object or a commit).
        obj = self.git_("rev-parse", "--verify", "-q", "refs/tags/%s" % tag)[1].strip()
        if obj != self.state["tags"][tag]["sha"]:
            self.block(tag, "contenu différent de celui annoncé")
            self.alert("orange", "tag_change", tag)
            raise Postpone("%s a changé pendant le téléchargement : version bloquée" % tag, "tag_changed")
        commit = self.git_("rev-parse", "--verify", "-q", "refs/tags/%s^{commit}" % tag)[1].strip()
        new = self.package_version(commit) if OID_RE.match(commit) else None
        if version(new) != version(tag):
            self.block(tag, "package.json ne porte pas la version du tag")
            raise Postpone("%s : version du code différente du tag, version bloquée" % tag, "tag_mismatch")
        return commit, new

    def plan_bun(self, commit, tag):
        """The exact Bun the new gbrain needs, staged and verified; None to keep the current one."""
        rc, out, _ = self.git_("show", "%s:package.json" % commit)
        try:
            need = json.loads(out).get("engines", {}).get("bun")
        except (ValueError, AttributeError):
            need = None
        m = re.match(r"^>=\s*(\d+\.\d+\.\d+)$", need.strip()) if isinstance(need, str) else None
        if not m:
            self.block(tag, "exigence Bun illisible")
            self.alert("orange", "bun_illisible", tag)
            raise Postpone("exigence Bun illisible dans %s : version bloquée" % tag, "bun_requirement")
        floor, have = m.group(1), self.bun_version()
        if have and version(have) >= version(floor):
            return None
        exact = floor
        rc, wf, _ = self.git_("show", "%s:.github/workflows/release.yml" % commit)
        pinned = re.search(r"^\s*bun-version:\s*['\"]?(\d+\.\d+\.\d+)['\"]?\s*$", wf, re.M) if rc == 0 else None
        if pinned and version(pinned.group(1)) >= version(floor):
            exact = pinned.group(1)
        self.log("%s demande Bun >= %s (installé : %s) : Bun %s" % (tag, floor, have or "aucun", exact))
        staged = self.stage_bun(exact)
        self.bun_change = "%s -> %s" % (have or "aucun", exact)
        return staged, exact, have or "0.0.0"

    def stage_bun(self, ver):
        asset = bun_asset()
        base = "%s/bun-v%s" % (self.bun_base, ver)
        stage = self.data / "staging" / ("bun-" + ver)
        shutil.rmtree(str(stage), ignore_errors=True)
        stage.mkdir(parents=True)
        sums, archive = stage / "SHASUMS256.txt", stage / (asset + ".zip")
        try:
            download(base + "/SHASUMS256.txt", sums, 2 ** 20, TIMEOUT["download"])
        except (OSError, ValueError, urllib.error.URLError):
            raise Postpone("téléchargement de Bun %s impossible (réseau ?)" % ver, "bun_download")
        expected = None
        for line in sums.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(\S+)$", line.strip())
            if m and m.group(2) == asset + ".zip":
                expected = m.group(1).lower()
        if not expected:
            self.alert("orange", "bun_introuvable")
            raise Postpone("Bun %s introuvable pour ce processeur (%s)" % (ver, asset), "bun_asset")
        try:
            download("%s/%s.zip" % (base, asset), archive, BUN_ZIP_MAX, TIMEOUT["download"])
        except (OSError, ValueError, urllib.error.URLError):
            raise Postpone("téléchargement de Bun %s impossible (réseau ?)" % ver, "bun_download")
        if sha256(archive) != expected:
            archive.unlink()
            self.alert("rouge", "bun_empreinte")
            raise Postpone("empreinte de Bun %s incorrecte : rien n'est installé" % ver, "bun_checksum")
        target = stage / ("bun" + EXE)
        try:
            with zipfile.ZipFile(str(archive)) as z:
                info = z.getinfo("%s/bun%s" % (asset, EXE))  # this exact member: no path from the archive is used
                if stat.S_ISLNK(info.external_attr >> 16) or info.file_size > 2 ** 30:
                    raise ValueError("unexpected member")
                with z.open(info) as src, open(str(target), "wb") as dst:
                    shutil.copyfileobj(src, dst)
        except (KeyError, ValueError, OSError, zipfile.BadZipFile):
            raise Postpone("archive de Bun %s inattendue" % ver, "bun_archive")
        archive.unlink()
        os.chmod(str(target), 0o755)
        rc, out, _ = self.sh([target, "--version"], TIMEOUT["short"], cwd=self.neutral)
        if rc != 0 or out.strip() != ver:
            raise Postpone("Bun %s ne démarre pas sur cette machine" % ver, "bun_broken")
        return target

    def block(self, tag, cause):
        self.state["bloques"][tag] = {"cause": cause, "le": iso(self.now)}

    def snapshot(self, head, branch, tag):
        root = self.data / "rollback"
        snap = unique(root / self.now.strftime(STAMP))
        try:
            root.mkdir(mode=0o700, exist_ok=True)
            snap.mkdir(mode=0o700)
            write_json(snap / "head.json", {"head": head, "branch": branch, "tag": tag, "bun": self.bun_version()})
            if self.bun.exists():
                shutil.copy2(str(self.bun), str(snap / self.bun.name))
            if (self.data / "config.json").exists():
                shutil.copy2(str(self.data / "config.json"), str(snap / "config.json"))
            if self.db.exists():
                copy_tree(self.db, snap / self.db.name, self.env)
        except (OSError, shutil.Error) as e:
            shutil.rmtree(str(snap), ignore_errors=True)
            raise Postpone("instantané impossible (%s)" % e.__class__.__name__, "snapshot_failed")
        self.log("instantané : %s" % snap.name)
        return snap

    def switch(self, commit, bun):
        """Every step of the switch, in order. Returns the name of the step that failed, or None."""
        steps = []
        if bun:
            staged, exact, have = bun
            steps.append(("Bun %s" % exact, lambda: replace_bun(
                self.bun, staged, "prev-%s-%s" % (have, self.now.strftime(STAMP))) or 0))
        repo = str(self.repo)
        steps += [
            ("git checkout", lambda: self.git_("-c", "advice.detachedHead=false", "checkout", "-q", "--detach", commit)[0]),
            ("bun install", lambda: self.sh([self.bun, "install", "--frozen-lockfile"], TIMEOUT["install"], cwd=repo, show=True)[0]),
            ("bun link", lambda: self.sh([self.bun, "link"], TIMEOUT["short"] * 2, cwd=repo)[0]),
            ("gbrain apply-migrations", lambda: self.sh([self.gbrain, "apply-migrations", "--yes"], TIMEOUT["migrate"],
                                                        cwd=self.neutral, show=True)[0]),
            # The version is chosen here, 7 days behind: `gbrain upgrade` would go to master.
            ("gbrain config", lambda: self.sh([self.gbrain, "config", "set", "self_upgrade.mode", "off"],
                                              TIMEOUT["short"], cwd=self.neutral)[0]),
        ]
        for name, step in steps:
            try:
                if step() != 0:
                    return name
            except Exception as e:  # anything unexpected mid-switch still has to roll back
                self.log("%s : %s" % (name, e.__class__.__name__))
                return name
        return None

    def rollback(self, snap, head, branch, bun, tag, before):
        """Puts back code, Bun, base and config.json, then proves it with gbrain doctor.
        Returns what could not be restored (empty: rollback proven)."""
        problems = []
        if branch:
            self.git_("checkout", "-q", "-f", branch, "--", budget=False)
        if self.git_("rev-parse", "HEAD", budget=False)[1].strip() != head:
            self.git_("-c", "advice.detachedHead=false", "checkout", "-q", "-f", "--detach", head, budget=False)
        if self.git_("rev-parse", "HEAD", budget=False)[1].strip() != head:
            problems.append("code")
        stamp = self.now.strftime(STAMP)
        if bun:
            try:
                replace_bun(self.bun, snap / self.bun.name, "echec-%s-%s" % (bun[1], stamp))
            except OSError:
                problems.append("Bun")
        try:
            if self.db.exists() or self.db.is_symlink():
                os.rename(str(self.db), str(unique(self.data / ("%s.echec-%s-%s" % (self.db.name, tag, stamp)))))
            if (snap / self.db.name).exists():
                os.rename(str(snap / self.db.name), str(self.db))
        except OSError:
            problems.append("base")
        try:
            if (snap / "config.json").exists():
                tmp = self.data / "config.json.restore"
                shutil.copy2(str(snap / "config.json"), str(tmp))
                os.replace(str(tmp), str(self.data / "config.json"))
        except OSError:
            problems.append("config.json")
        for name, cmd, timeout in (("bun install", [self.bun, "install", "--frozen-lockfile"], TIMEOUT["install"]),
                                   ("bun link", [self.bun, "link"], TIMEOUT["short"] * 2)):
            if self.sh(cmd, timeout, cwd=str(self.repo), budget=False)[0] != 0:
                problems.append(name)
        rc, out, _ = self.sh([self.gbrain, "--version"], TIMEOUT["short"], cwd=self.neutral, hide=True, budget=False)
        if rc != 0 or not re.search(r"(?<![\d.])%s(?![\d.])" % re.escape(self.installed), out):
            problems.append("gbrain --version")
        why = self.regression(before, self.health(budget=False), strict=False)
        if why:
            problems.append(why)
        return problems

    def update(self, tag):
        commit, new = self.fetch_tag(tag)
        bun = self.plan_bun(commit, tag)
        head = self.git_("rev-parse", "HEAD")[1].strip()
        branch = run([self.git, "symbolic-ref", "-q", "--short", "HEAD"], TIMEOUT["short"], cwd=str(self.repo),
                     env=self.env)[1].strip()  # exit 1 when detached: not worth a log line
        before = self.health()
        snap = self.snapshot(head, branch, tag)
        self.log("bascule v%s -> %s" % (self.installed, tag))
        why = None
        try:  # from here on, whatever happens ends in a success or a rollback
            failed = self.switch(commit, bun)
            if failed is None:
                rc, out, _ = self.sh([self.gbrain, "--version"], TIMEOUT["short"], cwd=self.neutral, hide=True)
                if rc != 0 or not re.search(r"(?<![\d.])%s(?![\d.])" % re.escape(new), out):
                    failed = "gbrain --version"
                else:
                    why = self.regression(before, self.health(), strict=True)
                    failed = "contrôle de santé" if why else None
        except Exception as e:
            failed = "erreur inattendue (%s)" % e.__class__.__name__
        st = self.state
        if failed is None:
            self.installed = new
            st["echecs_consecutifs"], st["dernier_succes"], st["pause_jusqua"] = 0, iso(self.now), None
            self.changelog = self.read_changelog()
            for old in self.data.glob("*.echec-v*"):
                shutil.rmtree(str(old), ignore_errors=True)
            return self.finish("updated", tag=tag)
        self.log("échec à l'étape « %s »%s : retour arrière" % (failed, " (%s)" % why if why else ""))
        try:
            problems = self.rollback(snap, head, branch, bun, tag, before)
        except Exception as e:
            problems = ["erreur inattendue (%s)" % e.__class__.__name__]
        self.log("retour arrière %s" % ("incomplet : " + ", ".join(problems) if problems
                                        else "fait et vérifié par gbrain doctor"))
        self.block(tag, "échec : %s" % failed)
        st["echecs_consecutifs"] += 1
        if st["echecs_consecutifs"] % 3 == 0:
            st["pause_jusqua"] = iso(self.now + PAUSE)
            self.log("3 échecs de suite : mises à jour en pause jusqu'au %s" % st["pause_jusqua"][:10])
        if problems:
            self.alert("rouge", "broken", tag)
            return self.finish("broken", "retour arrière incomplet (%s) : relance l'installation"
                               % ", ".join(problems), "broken", tag)
        self.alert("rouge", "rollback", tag)
        return self.finish("rolled_back", "échec à l'étape « %s » : retour à v%s" % (failed, self.installed),
                           "step_failed", tag)

    def read_changelog(self):
        """The first 3 lines under the newest heading of gbrain's CHANGELOG.md (a 4 MB file)."""
        try:
            with open(str(self.repo / "CHANGELOG.md"), encoding="utf-8", errors="replace") as f:
                text = f.read(256 * 1024)
        except OSError:
            return []
        lines, started = [], False
        for line in text.splitlines():
            if line.startswith("## "):
                if started:
                    break
                started = True
                continue
            line = re.sub(r"[\x00-\x1f\x7f]", "", line).strip()
            if started and line and not line.startswith("#"):
                lines.append(line.replace("**", "")[:200])
                if len(lines) == 3:
                    break
        return lines

    def purge(self):
        """Last runs' leftovers: staging always, snapshots and old Bun after 3 days.
        A failed base (brain.pglite.echec-*) stays until the next successful update."""
        shutil.rmtree(str(self.data / "staging"), ignore_errors=True)
        old = self.now - KEEP
        for snap in (self.data / "rollback").glob("*") if (self.data / "rollback").is_dir() else []:
            t = stamp_of(snap.name)
            if t and t < old:
                shutil.rmtree(str(snap), ignore_errors=True)
        for f in self.bin.glob("bun*.*-*") if self.bin.is_dir() else []:
            t = stamp_of(f.name)
            if t and t < old and (".prev-" in f.name or ".echec-" in f.name):
                try:
                    f.unlink()
                except OSError:
                    pass  # still running (Windows): next time

    def go(self):
        self.state = self.load_state()
        if not self.dry:
            self.purge()
        if self.args.reprendre:
            self.state["pause_jusqua"] = None
        if not self.git:
            return self.finish("postponed", "git introuvable", "no_git")
        if not (self.repo / ".git").exists():
            return self.finish("postponed", "pas de clone gbrain dans ~/DEV/gbrain", "no_clone")
        self.before = self.installed = self.package_version("HEAD")
        if not self.installed:
            return self.finish("postponed", "version installée illisible (package.json)", "bad_version")
        self.log("gbrain installé : v%s" % self.installed)
        listing = self.list_tags()
        if listing is None:
            return self.finish("check_failed", "liste des versions de gbrain non lue (réseau ?)", "network")
        self.listing = listing
        self.state["derniere_verif_ok"] = iso(self.now)
        top = max(listing, key=version) if listing else "aucun"
        self.log("tags v* en amont : %d (le plus haut : %s)" % (len(listing), top))
        self.register()
        self.add_release_dates()
        tag = self.candidate()
        pause = parse_time(self.state["pause_jusqua"])
        if pause and pause > self.now:
            return self.finish("paused", "pause après 3 échecs de suite", "paused", tag)
        if tag is None:
            return self.finish("waiting" if self.next_tag() else "up_to_date")
        seen = self.first_seen(self.state["tags"][tag])
        self.log("candidat : %s (vu ou publié il y a %d jours)" % (tag, (self.now - seen).days))
        try:
            self.prerequisites()
            if self.dry:
                return self.finish("candidate", tag=tag)
            return self.update(tag)
        except Postpone as p:
            return self.finish("postponed", p.cause, p.code, tag)


def local_or_https(url):
    if url.lower().startswith("https://") or re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?(/|$)", url, re.I):
        return url
    raise argparse.ArgumentTypeError("https:// seulement (ou http://127.0.0.1 pour les tests)")


def parse_args(argv):
    ap = argparse.ArgumentParser(
        prog="gbrain_update.py",
        description="Met gbrain à jour vers la plus récente version publiée depuis au moins 7 jours "
                    "(jamais master, jamais plus bas), avec Bun exact vérifié et retour arrière complet.",
        epilog="Chemins : le clone ~/DEV/gbrain ; l'état dans $GBRAIN_HOME/.gbrain (sinon ~/.gbrain) : "
               "update-state.json, last-update.json, update.lock, rollback/, staging/ ; Bun et gbrain dans "
               "$BUN_INSTALL/bin (sinon ~/.bun/bin) ou --bin-dir. Pour un essai isolé, change HOME "
               "(USERPROFILE sous Windows). Aucun secret n'est lu dans l'environnement : les variables dont "
               "le nom contient KEY, TOKEN, SECRET ou PASSWORD, et les réglages git passés par "
               "l'environnement (GIT_CONFIG_*, GIT_ASKPASS), ne sont pas transmis aux commandes lancées.")
    ap.add_argument("--log", help="journal (défaut : ~/.gbrain/nightly.log)")
    ap.add_argument("--a-blanc", action="store_true", help="affiche la décision, n'installe et n'écrit rien")
    ap.add_argument("--reprendre", action="store_true", help="lève la pause posée après 3 échecs")
    ap.add_argument("--git", help="chemin de git (défaut : celui du PATH)")
    tests = ap.add_argument_group("tests seulement")
    tests.add_argument("--remote", help="dépôt gbrain à interroger (défaut : %s)" % UPSTREAM)
    tests.add_argument("--releases-url", type=local_or_https,
                       help="API des releases (défaut : celle de GitHub, sauf avec --remote : aucune)")
    tests.add_argument("--bun-base-url", type=local_or_https, help="défaut : %s" % BUN_RELEASES)
    tests.add_argument("--maintenant", help="date et heure à utiliser (ISO 8601)")
    tests.add_argument("--bin-dir", help="dossier de bun et gbrain ; limite aussi la recherche de processus à ce dossier")
    tests.add_argument("--place-requise-mo", type=int, help="place libre exigée en Mo (défaut : %d)" % DISK_MARGIN_MB)
    args = ap.parse_args(argv)
    if args.maintenant and not parse_time(args.maintenant):
        ap.error("--maintenant : date ISO 8601 illisible")
    return args


def main(argv=None):
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="replace")
        except (OSError, ValueError):
            pass
    up = Updater(parse_args(argv))
    if up.dry:
        return up.go()
    up.data.mkdir(parents=True, exist_ok=True)
    lock = Lock(up.data / "update.lock")
    if not lock.acquire():
        up.log("déjà en cours : rien à faire")
        return 0
    try:
        return up.go()
    except Exception:
        up.log("plantage : " + traceback.format_exc().strip().replace("\n", "\n    "))
        return 1
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
