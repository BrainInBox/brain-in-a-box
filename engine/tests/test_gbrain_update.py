"""Tests for engine/hooks/gbrain_update.py, on macOS and Windows, entirely local: a bare
repository with tags stands for GitHub, a local HTTP server for Bun's releases, and fake
bun/gbrain written in Python (wrapped in pip's .exe launcher on Windows) for the real ones.

    python -I -m unittest discover -s engine/tests -v
"""
import datetime as dt
import functools
import hashlib
import http.server
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "hooks" / "gbrain_update.py"
WINDOWS = os.name == "nt"
EXE = ".exe" if WINDOWS else ""
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
T0 = dt.datetime(2026, 10, 1, 4, 0, tzinfo=dt.timezone.utc)
CANARIES = {"GITHUB_TOKEN": "canari-gh", "FAKE_API_KEY": "canari-key", "MY_SECRET": "canari-secret",
            "DB_PASSWORD": "canari-pw", "SMTP_PASSWD": "canari-passwd", "GOOGLE_CREDENTIALS": "canari-cred",
            "NPM_CONFIG__AUTH": "canari-auth", "MIRROR_URL": "https://me:canari-url@mirror.example/x",
            # Not a secret, but gbrain would migrate that database instead of the snapshotted one.
            "DATABASE_URL": "postgres://me:canari-db@db.example/app",
            # What a Claude Code session passes to its children: git settings through the environment.
            "GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "core.abbrev", "GIT_CONFIG_VALUE_0": "12",
            "GIT_CONFIG_KEY_1": "http.extraHeader", "GIT_CONFIG_VALUE_1": "Authorization: Bearer canari-header",
            "GIT_ASKPASS": "/nonexistent/askpass"}

_spec = importlib.util.spec_from_file_location("gbrain_update", str(SCRIPT))
gu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gu)


def day(n, hours=0):
    return (T0 + dt.timedelta(days=n, hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


RECORD = r'''
import json, os, re, sys, time
args = sys.argv[1:]
with open(os.path.join(CTL, "calls.jsonl"), "a") as f:
    f.write(json.dumps({"prog": PROG, "version": VERSION, "args": args, "cwd": os.getcwd(),
                        "leaks": sorted(k for k, v in os.environ.items()
                                        if any(w in k.upper() for w in ("KEY", "TOKEN", "SECRET", "PASS", "CREDENTIAL", "AUTH"))
                                        or k.upper().startswith(("GIT_CONFIG_COUNT", "GIT_CONFIG_VALUE", "GIT_ASKPASS"))
                                        or k.upper() in ("DATABASE_URL", "GBRAIN_DATABASE_URL")
                                        or (re.search(r"://[^/@]+:[^/@]+@", v) and not k.upper().endswith("_PROXY"))),
                        "owns_migrations": os.environ.get("GBRAIN_UPGRADE_OWNS_MIGRATIONS"),
                        "prompt": os.environ.get("GIT_TERMINAL_PROMPT")}) + "\n")
def ctl(name):
    try:
        with open(os.path.join(CTL, name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}
'''

FAKE_BUN = RECORD + r'''
if args[:1] == ["--version"]:
    print(VERSION)
elif args[:1] == ["sleep"]:
    time.sleep(float(args[1]))
elif args[:1] == ["install"]:
    sys.exit(ctl("bun.json").get("install_rc", 0))
'''

FAKE_GBRAIN = RECORD + r'''
with open(os.path.join(REPO, "package.json")) as f:
    ver = json.load(f)["version"]
db = os.path.join(DATA, "brain.pglite")
if args[:1] == ["--version"]:
    print("gbrain " + ver)
    sys.exit(ctl("gbrain.json").get("version_rc", 0))
elif args[:1] == ["doctor"]:
    d = ctl("doctor.json").get(ver, {})
    checks = [{"name": n, "status": "fail", "message": "x"} for n in d.get("fails", [])]
    print("notice: a line before the JSON")
    print(json.dumps({"schema_version": 2, "checks": checks + [{"name": "connection", "status": "ok"}]}))
    sys.exit(d.get("rc", 0))
elif args[:2] == ["sources", "list"]:
    ids = ctl("sources.json").get(ver, ["default", "vault"])
    print(json.dumps({"sources": [{"id": i, "page_count": 3} for i in ids]}))
elif args[:1] == ["apply-migrations"]:
    with open(os.path.join(db, "migrated-" + ver), "w") as f:
        f.write("schema " + ver)
    # What the real one writes besides the base: its ledger, and preferences on some versions.
    os.makedirs(os.path.join(DATA, "migrations"), exist_ok=True)
    with open(os.path.join(DATA, "migrations", "completed.jsonl"), "a") as f:
        f.write(json.dumps({"version": ver, "status": "complete"}) + "\n")
    with open(os.path.join(DATA, "preferences.json"), "w") as f:
        json.dump({"set_in_version": ver}, f)
    if ctl("hang.json").get(ver):  # a run cut short in the middle of the migrations
        with open(os.path.join(CTL, "hung.pid"), "w") as f:
            f.write(str(os.getpid()))
        time.sleep(600)
elif args[:2] == ["config", "set"]:
    p = os.path.join(DATA, "config.json")
    with open(p) as f:
        c = json.load(f)
    c[args[2]] = args[3]
    with open(p, "w") as f:
        json.dump(c, f)
'''


FAKE_GIT = r'''
import json, os, subprocess, sys
args = sys.argv[1:]
try:
    with open(os.path.join(CTL, "git.json")) as f:
        broken = json.load(f).get("show_fails")
except (OSError, ValueError):
    broken = False
# git itself failing (127: could not start) while reading the release: nothing to do with the release.
if broken and args[:1] == ["show"] and args[1].endswith(":package.json") and not args[1].startswith("HEAD:"):
    sys.exit(127)
sys.exit(subprocess.call([REAL] + args))
'''


def windows_launcher():
    """pip's console-script launcher: an .exe that runs the zip appended to itself."""
    spec = importlib.util.find_spec("pip")
    name = "t64-arm.exe" if "arm" in os.environ.get("PROCESSOR_ARCHITECTURE", "").lower() else "t64.exe"
    for base in (spec.submodule_search_locations or []) if spec else []:
        path = Path(base) / "_vendor" / "distlib" / name
        if path.is_file():
            return path.read_bytes()
    raise unittest.SkipTest("pip's distlib launcher (%s) not found: no fake .exe possible" % name)


def program(source, **consts):
    """Bytes of a fake program: a Python script, or on Windows the .exe launcher wrapping it."""
    head = "".join("%s = %r\n" % kv for kv in sorted(consts.items()))
    if not WINDOWS:
        return ("#!%s\n%s%s" % (sys.executable, head, source)).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("__main__.py", head + source)
    exe = sys.executable
    shebang = ('#!"%s"' % exe if " " in exe else "#!" + exe).encode("utf-8") + b"\r\n"
    return windows_launcher() + shebang + buf.getvalue()


def put_program(path, data):
    path.write_bytes(data)
    path.chmod(0o755)


def tree(root):
    out = {}
    for base, _, files in os.walk(str(root)):
        for f in files:
            p = Path(base) / f
            out[p.relative_to(root).as_posix()] = p.read_bytes()
    return out


def rmtree(path):
    def retry(func, p, _):
        os.chmod(p, stat.S_IWRITE)  # git's read-only objects on Windows
        func(p)
    if sys.version_info >= (3, 12):
        shutil.rmtree(str(path), onexc=retry)
    else:
        shutil.rmtree(str(path), onerror=retry)


def pid_alive(pid):
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


def kill(p):
    if WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    else:
        p.kill()
    p.wait(timeout=30)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class World:
    """A throwaway HOME with a gbrain clone, its data, fake bun/gbrain, and an upstream."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="gbu-")).resolve()
        self.home = self.root / "home"
        self.repo = self.home / "DEV" / "gbrain"
        self.data = self.home / ".gbrain"
        self.db = self.data / "brain.pglite"
        self.bin = self.home / ".bun" / "bin"
        self.ctl, self.src, self.www = self.root / "ctl", self.root / "src", self.root / "www"
        self.upstream = self.root / "upstream.git"
        self.url = self.upstream.as_uri()
        self.log = self.data / "nightly.log"
        for d in (self.repo.parent, self.data, self.bin, self.ctl, self.www):
            d.mkdir(parents=True)
        self.server = None
        self.procs = []

    def close(self):
        for p in self.procs:
            if p.poll() is None:
                kill(p)
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        rmtree(self.root)

    # -- upstream ----------------------------------------------------------------

    def git(self, *args, cwd=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "XDG_CONFIG_HOME"}
        env.update(HOME=str(self.root), USERPROFILE=str(self.root), GIT_CONFIG_NOSYSTEM="1",
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        p = subprocess.run(["git", "-c", "init.defaultBranch=master", "-c", "core.autocrlf=false",
                            "-c", "advice.detachedHead=false"] + list(args), cwd=str(cwd) if cwd else None,
                           env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=60)
        if p.returncode:
            raise AssertionError("git %s: %s" % (" ".join(args), p.stderr.decode("utf-8", "replace")))
        return p.stdout.decode("utf-8", "replace").strip()

    def release(self, ver, floor=">=1.0.0", pin="1.0.0", tag=True, note=""):
        s = self.src
        (s / "package.json").write_text(json.dumps({"name": "gbrain", "version": ver,
                                                    "engines": {"bun": floor}}, indent=2) + "\n")
        wf = s / ".github" / "workflows"
        wf.mkdir(parents=True, exist_ok=True)
        (wf / "release.yml").write_text("jobs:\n  build:\n    steps:\n      - uses: oven-sh/setup-bun@v2\n"
                                        "        with:\n          bun-version: %s\n" % pin)
        (s / "bun.lock").write_text("lockfile %s%s\n" % (ver, note))
        (s / "CHANGELOG.md").write_text("# Changelog\n\nIntro.\n\n## [%s] - 2026-09-01\n\n**Release %s%s.**\n\n"
                                        "### Itemized changes\n\n- feat: thing %s\n\n## [0.0.1]\n\n- old\n"
                                        % (ver, ver, note, ver))
        self.git("add", "-A", cwd=s)
        self.git("commit", "-q", "-m", "release %s%s" % (ver, note), cwd=s)
        if tag:
            self.git("tag", "-f", "v" + ver, cwd=s)

    def build(self, bun=None):
        self.git("init", "-q", "--bare", str(self.upstream))
        self.git("init", "-q", str(self.src))
        for ver in ("0.1.0", "0.2.0", "0.3.0", "0.4.0"):
            floor, pin = (bun or {}).get(ver, (">=1.0.0", "1.0.0"))
            self.release(ver, floor, pin)
        self.git("tag", "latest-stable", "v0.2.0", cwd=self.src)
        self.release("0.5.0", tag=False)  # master, ahead of every tag
        self.push()

    def push(self, *refs):
        self.git("push", "-q", "--force", "--tags", str(self.upstream), *(refs or ("master",)), cwd=self.src)

    def sha(self, ref, repo=None):
        return self.git("rev-parse", ref + "^{commit}", cwd=repo or self.upstream)

    # -- the machine -------------------------------------------------------------

    def install(self, at="v0.1.0", shallow=False, branch=None):
        if shallow:  # as install.ps1 does it: one commit, no remote
            self.git("init", "-q", str(self.repo))
            self.git("fetch", "-q", "--depth", "1", self.url, self.sha(at), cwd=self.repo)
            self.git("checkout", "-q", "FETCH_HEAD", cwd=self.repo)
        else:
            self.git("clone", "-q", str(self.upstream), str(self.repo))
            if branch:
                self.git("checkout", "-q", "-b", branch, at, cwd=self.repo)
            else:
                self.git("checkout", "-q", "--detach", at, cwd=self.repo)
        (self.db / "base" / "1").mkdir(parents=True)
        (self.db / "PG_VERSION").write_text("17\n")
        (self.db / "base" / "1" / "16384").write_bytes(os.urandom(4096))
        (self.data / "config.json").write_text(json.dumps({"engine": "pglite", "database_path": str(self.db)}))
        self.put_bun(self.bin / ("bun" + EXE), "1.0.0")
        put_program(self.bin / ("gbrain" + EXE), program(FAKE_GBRAIN, PROG="gbrain", VERSION="", CTL=str(self.ctl),
                                                         REPO=str(self.repo), DATA=str(self.data)))

    def put_bun(self, path, version):
        put_program(path, program(FAKE_BUN, PROG="bun", VERSION=version, CTL=str(self.ctl)))

    def control(self, name, data):
        (self.ctl / name).write_text(json.dumps(data))

    def serve(self):
        if not self.server:
            handler = functools.partial(Quiet, directory=str(self.www))
            self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def publish_bun(self, version, checksum_ok=True, with_asset=True):
        """Bun's release page for this machine, as GitHub serves it, on the local server."""
        asset = gu.bun_asset()
        folder = self.www / ("bun-v" + version)
        folder.mkdir(parents=True)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            info = zipfile.ZipInfo("%s/bun%s" % (asset, EXE))
            info.external_attr = 0o755 << 16
            z.writestr(info, program(FAKE_BUN, PROG="bun", VERSION=version, CTL=str(self.ctl)))
        (folder / (asset + ".zip")).write_bytes(buf.getvalue())
        digest = hashlib.sha256(buf.getvalue()).hexdigest() if checksum_ok else "0" * 64
        lines = ["%s  bun-linux-riscv.zip" % ("1" * 64)]
        if with_asset:
            lines.append("%s  %s.zip" % (digest, asset))
        (folder / "SHASUMS256.txt").write_text("\n".join(lines) + "\n")
        return self.serve()

    def command(self, now, *extra, env=None, remote=None):
        cmd = [sys.executable, "-I", str(SCRIPT), "--remote", remote or self.url, "--bin-dir", str(self.bin),
               "--maintenant", now, "--log", str(self.log)] + list(extra)
        e = {k: v for k, v in os.environ.items()
             if k not in ("GBRAIN_HOME", "BUN_INSTALL", "XDG_CONFIG_HOME", "DATABASE_URL", "GBRAIN_DATABASE_URL")
             and not k.startswith("GIT_")}
        e.update(HOME=str(self.home), USERPROFILE=str(self.home), GIT_CONFIG_NOSYSTEM="1")
        e.update(env or {})
        return cmd, e

    def start(self, now, *extra):
        cmd, e = self.command(now, *extra)
        p = subprocess.Popen(cmd, env=e, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        self.procs.append(p)
        return p

    def run(self, now, *extra, env=None, remote=None):
        cmd, e = self.command(now, *extra, env=env, remote=remote)
        p = subprocess.run(cmd, env=e, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=300)
        out = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
        if p.returncode != 0:
            raise AssertionError("gbrain_update.py exited %d:\n%s\n%s" % (p.returncode, out, self.logged()))
        return out

    def result(self):
        return json.loads((self.data / "last-update.json").read_text(encoding="utf-8"))

    def state(self):
        return json.loads((self.data / "update-state.json").read_text(encoding="utf-8"))

    def logged(self):
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    def head(self):
        return self.git("rev-parse", "HEAD", cwd=self.repo)

    def calls(self, prog=None):
        p = self.ctl / "calls.jsonl"
        rows = [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []
        return [r for r in rows if prog is None or r["prog"] == prog]

    def switch_calls(self):
        wanted = (["install"], ["link"], ["apply-migrations"], ["config"])
        return [" ".join([c["prog"]] + c["args"]) for c in self.calls() if c["args"][:1] in wanted]


class Base(unittest.TestCase):
    bun = None

    def setUp(self):
        self.w = World()
        self.addCleanup(self.w.close)
        self.w.build(self.bun)

    def assertStatus(self, status, code=None):
        res = self.w.result()
        self.assertEqual(res["status"], status, "%s\n%s" % (res, self.w.logged()))
        if code:
            self.assertEqual(res["cause_code"], code, res)
        return res


class Update(Base):
    def test_waits_seven_days_then_installs_the_highest_tag(self):
        w = self.w
        w.install("v0.1.0")
        w.run(day(0), env=CANARIES)
        res = self.assertStatus("waiting")
        self.assertEqual(res["prochaine"], {"tag": "v0.4.0", "eligible_le": day(7)})
        self.assertEqual(res["derniere_verif_ok"], day(0))
        self.assertEqual(w.head(), w.sha("v0.1.0"))
        self.assertEqual(w.switch_calls(), [])
        w.run(day(6, 23))
        self.assertStatus("waiting")

        w.run(day(7), env=CANARIES)
        res = self.assertStatus("updated")
        self.assertEqual(w.head(), w.sha("v0.4.0"))
        self.assertNotEqual(w.head(), w.sha("master"))
        self.assertEqual((res["before"], res["after"], res["tag"]), ("v0.1.0", "v0.4.0", "v0.4.0"))
        self.assertEqual(res["dernier_succes"], day(7))
        self.assertIn("Release 0.4.0.", res["changelog"])
        self.assertEqual(w.switch_calls(), ["bun install --frozen-lockfile", "bun link",
                                            "gbrain apply-migrations --yes", "gbrain config set self_upgrade.mode off"])
        install = [c for c in w.calls("bun") if c["args"][:1] == ["install"]][0]
        self.assertEqual(Path(install["cwd"]).resolve(), w.repo.resolve())
        self.assertEqual(install["owns_migrations"], "1")  # gbrain's postinstall leaves them to their own step
        # Children get no credential from the environment, and git never prompts.
        self.assertEqual({tuple(c["leaks"]) for c in w.calls()}, {()})
        self.assertEqual({c["prompt"] for c in w.calls()}, {"0"})
        written = w.logged() + json.dumps(w.result()) + json.dumps(w.state())
        for value in CANARIES.values():
            if "canari" in value:
                self.assertNotIn(value, written)

        w.run(day(30))
        self.assertStatus("up_to_date")  # master is ahead, but never a candidate
        self.assertEqual(w.head(), w.sha("v0.4.0"))

    def test_never_lower(self):
        w = self.w
        w.install("v0.3.0")
        w.run(day(0))
        self.assertEqual(sorted(w.state()["tags"]), ["v0.4.0"])
        w.run(day(8))
        self.assertStatus("updated")
        self.assertEqual(w.head(), w.sha("v0.4.0"))

    def test_a_clone_on_master_is_left_alone(self):
        w = self.w
        w.install("master", branch="main")
        w.run(day(0))
        w.run(day(20))
        self.assertStatus("up_to_date")
        self.assertEqual(w.head(), w.sha("master"))
        self.assertEqual(w.state()["tags"], {})

    def test_shallow_clone_like_windows(self):
        w = self.w
        w.install("v0.1.0", shallow=True)
        w.run(day(0))
        w.run(day(8))
        self.assertStatus("updated")
        self.assertEqual(w.head(), w.sha("v0.4.0"))
        self.assertEqual(w.git("rev-parse", "--is-shallow-repository", cwd=w.repo), "true")

    def test_only_tags_are_listed_and_latest_stable_may_move(self):
        w = self.w
        w.install("v0.1.0")
        trace = w.root / "trace.txt"
        w.run(day(0), env={"GIT_TRACE_PACKET": str(trace)})
        sent = [line for line in trace.read_text().splitlines() if "ref-prefix" in line]
        self.assertTrue(sent)
        self.assertTrue(all("ref-prefix refs/tags/" in line for line in sent), sent)
        self.assertNotIn("refs/heads/", trace.read_text())
        w.git("tag", "-f", "latest-stable", "v0.3.0", cwd=w.src)
        w.push()
        w.run(day(1))
        self.assertStatus("waiting")
        self.assertNotIn("latest-stable", json.dumps(w.state()))

    def test_a_moved_tag_is_blocked(self):
        w = self.w
        w.install("v0.1.0")
        w.run(day(0))
        w.git("checkout", "-q", "-b", "side", "v0.3.0", cwd=w.src)
        w.release("0.4.0", note=" (moved)")
        w.push("side")
        w.run(day(8))
        res = self.assertStatus("updated")
        self.assertEqual(res["bloques"]["v0.4.0"]["cause"], "tag déplacé en amont")
        self.assertIn({"niveau": "orange", "code": "tag_deplace", "tag": "v0.4.0"}, res["alertes"])
        self.assertEqual(w.head(), w.sha("v0.3.0"))

    def test_a_tag_that_differs_at_fetch_time_is_blocked(self):
        w = self.w
        w.install("v0.1.0")
        mirror = w.root / "mirror.git"
        w.git("clone", "-q", "--mirror", str(w.upstream), str(mirror))
        w.git("checkout", "-q", "-b", "evil", "v0.3.0", cwd=w.src)
        w.release("0.4.0", note=" (evil)")
        w.git("push", "-q", "--force", str(mirror), "refs/tags/v0.4.0", cwd=w.src)
        # The listing asks GitHub; the fetch, made inside the clone, is sent elsewhere.
        w.git("config", "url.%s.insteadOf" % mirror.as_uri(), w.url, cwd=w.repo)
        w.run(day(0))
        before = w.head()
        w.run(day(8))
        res = self.assertStatus("postponed", "tag_changed")
        self.assertIn("v0.4.0", res["bloques"])
        self.assertEqual(w.head(), before)
        self.assertFalse((w.data / "rollback").exists())
        w.run(day(9))
        self.assertStatus("updated")
        self.assertEqual(w.head(), w.sha("v0.3.0"))

    def test_a_github_release_date_never_shortens_the_wait(self):
        # A tag force-pushed under a month-old release keeps the release's date: on a machine
        # that has never seen the tag, that date would make yesterday's commit installable.
        w = self.w
        w.install("v0.1.0")
        (w.www / "releases").write_text(json.dumps([
            {"tag_name": "v0.4.0", "published_at": day(-30), "draft": False},
            {"tag_name": "v0.3.0", "published_at": day(-10), "draft": False},
            {"tag_name": "v0.2.0", "published_at": day(-20), "draft": True},
        ]))
        url = w.serve()
        w.run(day(0), "--releases-url", url + "/nothing-here")
        self.assertStatus("waiting")  # API silent: nothing changes
        w.run(day(0, 1), "--releases-url", url + "/releases")
        res = self.assertStatus("waiting")
        self.assertEqual(res["prochaine"], {"tag": "v0.4.0", "eligible_le": day(7)})
        self.assertEqual(w.head(), w.sha("v0.1.0"))
        self.assertEqual(w.state()["tags"]["v0.4.0"]["publie_le"], day(-30))  # kept, for information
        w.run(day(7), "--releases-url", url + "/releases")
        self.assertStatus("updated")
        self.assertEqual(w.head(), w.sha("v0.4.0"))

    def test_a_damaged_state_never_holds_updates_back(self):
        w = self.w
        w.install("v0.1.0")
        (w.data / "update-state.json").write_text(json.dumps({
            "tags": {"v0.4.0": {"sha": w.sha("v0.4.0"), "vu_le": "2036-01-01T00:00:00Z"},  # a clock once wrong
                     "v0.3.0": {"sha": "not a commit", "vu_le": day(-30)},
                     "v0.2.0": {"sha": w.sha("v0.2.0"), "vu_le": "yesterday"}},
            "bloques": "nothing", "pause_jusqua": "2036-01-01T00:00:00Z", "echecs_consecutifs": -4}))
        w.run(day(0))
        res = self.assertStatus("waiting")
        self.assertEqual(res["prochaine"], {"tag": "v0.4.0", "eligible_le": day(7)})
        self.assertIsNone(res["pause_jusqua"])
        self.assertEqual({t: e["vu_le"] for t, e in w.state()["tags"].items()},
                         {"v0.2.0": day(0), "v0.3.0": day(0), "v0.4.0": day(0)})
        w.run(day(7))
        self.assertStatus("updated")
        self.assertEqual(w.head(), w.sha("v0.4.0"))

    def test_a_git_failure_never_blocks_a_release(self):
        w = self.w
        w.install("v0.1.0")
        w.run(day(0))
        git = w.root / ("git" + EXE)
        put_program(git, program(FAKE_GIT, REAL=shutil.which("git"), CTL=str(w.ctl)))
        w.control("git.json", {"show_fails": True})
        w.run(day(8), "--git", str(git))
        res = self.assertStatus("postponed", "git_failed")
        self.assertEqual(res["bloques"], {})
        self.assertEqual(w.head(), w.sha("v0.1.0"))
        w.control("git.json", {})
        w.run(day(9), "--git", str(git))
        self.assertStatus("updated")
        self.assertEqual(w.head(), w.sha("v0.4.0"))

    def test_a_database_the_snapshot_does_not_cover_postpones(self):
        w = self.w
        w.install("v0.1.0")
        w.run(day(0))
        url = "postgres://me:canari-db@db.example/brain"
        (w.data / ".env").write_text("# gbrain reads this file by itself\nexport DATABASE_URL='%s'\n" % url)
        w.run(day(8))
        self.assertStatus("postponed", "db_unsupported")
        (w.data / ".env").write_text("DATABASE_URL=\n")
        w.run(day(8, 1), env={"GBRAIN_DATABASE_URL": url})
        self.assertStatus("postponed", "db_unsupported")
        self.assertEqual(w.head(), w.sha("v0.1.0"))
        self.assertNotIn("canari", w.logged() + json.dumps(w.result()) + json.dumps(w.state()))
        w.run(day(8, 2), env={"DATABASE_URL": url})  # someone else's database: gbrain never sees it
        self.assertStatus("updated")
        self.assertEqual({tuple(c["leaks"]) for c in w.calls()}, {()})

    def test_a_check_failing_before_and_after_is_not_a_failure(self):
        w = self.w
        w.install("v0.1.0")
        w.control("doctor.json", {v: {"rc": 1, "fails": ["embeddings"]} for v in ("0.1.0", "0.4.0")})
        w.run(day(0))
        w.run(day(8))
        self.assertStatus("updated")

    def test_a_lost_source_rolls_back(self):
        w = self.w
        w.install("v0.1.0")
        w.control("sources.json", {"0.4.0": ["default"]})
        w.run(day(0))
        w.run(day(8))
        res = self.assertStatus("rolled_back", "step_failed")
        self.assertEqual(w.head(), w.sha("v0.1.0"))
        self.assertIn("v0.4.0", res["bloques"])

    def test_bun_lock_alone_is_restored_any_other_edit_postpones(self):
        w = self.w
        w.install("v0.1.0")
        (w.repo / "CHANGELOG.md").write_text("my notes\n")
        w.run(day(0))
        w.run(day(8))
        self.assertStatus("postponed", "clone_modified")
        self.assertEqual((w.repo / "CHANGELOG.md").read_text(), "my notes\n")
        self.assertEqual(w.head(), w.sha("v0.1.0"))
        w.run(day(9))
        w.run(day(16))
        res = self.assertStatus("postponed", "clone_modified")
        self.assertEqual(res["retard_jours"], 16)
        self.assertIn({"niveau": "rouge", "code": "retard"}, res["alertes"])
        self.assertIn({"niveau": "orange", "code": "ajourne"}, res["alertes"])

        w.git("checkout", "--", "CHANGELOG.md", cwd=w.repo)
        (w.repo / "bun.lock").write_text("regenerated by bun\n")
        w.run(day(17))
        self.assertStatus("updated")
        self.assertEqual(w.head(), w.sha("v0.4.0"))

    def test_busy_brain_or_full_disk_postpones(self):
        w = self.w
        w.install("v0.1.0")
        w.run(day(0))
        (w.db / ".gbrain-lock").mkdir()
        w.run(day(8))
        self.assertStatus("postponed", "db_locked")
        (w.db / ".gbrain-lock").rmdir()

        if WINDOWS:
            busy = [str(w.bin / "bun.exe"), "sleep", "60"]
        else:
            busy = [sys.executable, "-c", "import time; time.sleep(60)", "bun", str(w.bin / "gbrain"), "serve"]
        p = subprocess.Popen(busy, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, creationflags=NO_WINDOW)
        w.procs.append(p)
        time.sleep(1)
        w.run(day(8, 1))
        self.assertStatus("postponed", "busy")
        kill(p)

        w.run(day(8, 2), "--place-requise-mo", "100000000")
        self.assertStatus("postponed", "disk_space")
        self.assertEqual(w.head(), w.sha("v0.1.0"))
        w.run(day(8, 3))
        self.assertStatus("updated")

    def test_dry_run_changes_nothing(self):
        w = self.w
        w.install("v0.1.0")
        w.run(day(0))
        files = {p: p.read_bytes() for p in (w.data / "update-state.json", w.data / "last-update.json", w.log,
                                             w.data / "update.lock")}
        out = w.run(day(8), "--a-blanc")
        self.assertIn("décision : installer", out)
        self.assertIn("[v0.4.0]", out)
        self.assertEqual(w.head(), w.sha("v0.1.0"))
        self.assertEqual({p: p.read_bytes() for p in files}, files)
        self.assertEqual(w.switch_calls(), [])

    def test_a_second_instance_does_nothing(self):
        w = self.w
        w.install("v0.1.0")
        lock = gu.Lock(w.data / "update.lock")
        self.assertTrue(lock.acquire())
        try:
            w.run(day(0))
        finally:
            lock.release()
        self.assertIn("déjà en cours", w.logged())
        self.assertFalse((w.data / "last-update.json").exists())
        # What a killed run leaves behind is the file, never the lock.
        (w.data / "update.lock").write_text("99999999 0\n")
        w.run(day(0))
        self.assertStatus("waiting")

    def test_three_failures_pause_seven_days_then_resume(self):
        w = self.w
        w.install("v0.1.0")
        w.control("doctor.json", {v: {"rc": 1, "fails": ["schema"]} for v in ("0.2.0", "0.3.0", "0.4.0")})
        w.run(day(0))
        for n, tag in ((8, "v0.4.0"), (9, "v0.3.0"), (10, "v0.2.0")):
            w.run(day(n))
            res = self.assertStatus("rolled_back")
            self.assertEqual(res["tag"], tag)
        self.assertEqual(res["echecs_consecutifs"], 3)
        self.assertEqual(res["pause_jusqua"], day(17))
        w.git("tag", "v0.5.0", "master", cwd=w.src)
        w.push()
        w.run(day(11))
        self.assertStatus("paused")
        w.run(day(12), "--reprendre")
        res = self.assertStatus("waiting")
        self.assertIsNone(res["pause_jusqua"])
        w.run(day(18))
        res = self.assertStatus("updated")
        self.assertEqual((res["after"], res["echecs_consecutifs"]), ("v0.5.0", 0))
        self.assertEqual(w.head(), w.sha("v0.5.0"))


class Failures(Base):
    bun = {"0.4.0": (">=9.9.0", "9.9.1")}

    def before(self):
        w = self.w
        return {"head": w.head(), "bun": (w.bin / ("bun" + EXE)).read_bytes(), "db": tree(w.db),
                "config": (w.data / "config.json").read_bytes(), "ledger": tree(w.data / "migrations"),
                "preferences": (w.data / "preferences.json").exists()}

    def assertRestored(self, before):
        w = self.w
        self.assertEqual(w.head(), before["head"])
        self.assertEqual((w.bin / ("bun" + EXE)).read_bytes(), before["bun"])
        self.assertEqual(tree(w.db), before["db"])
        self.assertEqual((w.data / "config.json").read_bytes(), before["config"])
        # The ledger says which migrations ran: left saying "0.4.0 done" over a base put back
        # to 0.1.0, it would make a later version skip them.
        self.assertEqual(tree(w.data / "migrations"), before["ledger"])
        self.assertEqual((w.data / "preferences.json").exists(), before["preferences"])
        self.assertFalse((w.data / "update-journal.json").exists())

    def test_failed_update_restores_code_bun_base_and_config(self):
        w = self.w
        w.install("v0.1.0", branch="work")
        (w.data / "migrations").mkdir()
        (w.data / "migrations" / "completed.jsonl").write_text('{"version": "0.1.0", "status": "complete"}\n')
        w.control("doctor.json", {"0.4.0": {"rc": 1, "fails": ["schema"]}})
        w.run(day(0))
        before = self.before()
        w.run(day(8), "--bun-base-url", w.publish_bun("9.9.1"))
        res = self.assertStatus("rolled_back", "step_failed")
        self.assertRestored(before)
        self.assertEqual(w.git("symbolic-ref", "--short", "HEAD", cwd=w.repo), "work")
        # The rollback's own bun install never ran migrations on the base it had put back.
        self.assertEqual({c["owns_migrations"] for c in w.calls("bun") if c["args"][:1] == ["install"]}, {"1"})
        self.assertEqual(res["bloques"]["v0.4.0"]["cause"], "échec : contrôle de santé")
        self.assertEqual(res["echecs_consecutifs"], 1)
        self.assertIn({"niveau": "rouge", "code": "rollback", "tag": "v0.4.0"}, res["alertes"])
        failed = list(w.data.glob("brain.pglite.echec-v0.4.0-*"))
        self.assertEqual(len(failed), 1)
        self.assertTrue((failed[0] / "migrated-0.4.0").exists())
        # The 9.9.1 that was tried did run the install: the switch really happened.
        self.assertIn("9.9.1", {c["version"] for c in w.calls("bun") if c["args"][:1] == ["install"]})

        w.run(day(9))
        self.assertStatus("updated")
        self.assertEqual(w.head(), w.sha("v0.3.0"))
        self.assertEqual(list(w.data.glob("brain.pglite.echec-*")), [])

    def test_a_switch_cut_short_is_rolled_back_by_the_next_run(self):
        w = self.w
        w.install("v0.1.0")
        w.run(day(0))
        before = self.before()
        w.control("hang.json", {"0.4.0": True})
        p = w.start(day(8), "--bun-base-url", w.publish_bun("9.9.1"))
        hung = w.ctl / "hung.pid"
        for _ in range(1200):
            if hung.exists() and hung.read_text():
                break
            time.sleep(0.1)
        pid = int(hung.read_text())
        kill(p)  # killed in the middle of the migrations: power cut, a task's time limit
        if not WINDOWS:  # the fake gbrain runs in a session of its own
            os.kill(pid, 9)
        for _ in range(100):
            if not pid_alive(pid):
                break
            time.sleep(0.1)
        self.assertTrue((w.data / "update-journal.json").exists())
        self.assertEqual(w.head(), w.sha("v0.4.0"))
        w.control("hang.json", {})

        w.run(day(8, 1))
        res = self.assertStatus("rolled_back", "interrupted")
        self.assertRestored(before)
        self.assertEqual(res["bloques"]["v0.4.0"]["cause"], "échec : mise à jour interrompue")
        self.assertEqual(len(list(w.data.glob("brain.pglite.echec-v0.4.0-*"))), 1)
        w.run(day(9))
        self.assertStatus("updated")
        self.assertEqual(w.head(), w.sha("v0.3.0"))

    def test_a_switch_cut_short_before_the_code_moved_keeps_the_base(self):
        # Killed while Bun was being replaced: the base was never migrated, and what was
        # written to gbrain since must stay.
        w = self.w
        w.install("v0.1.0")
        w.run(day(0))
        before = self.before()
        snap = w.data / "rollback" / "20261009-040000"
        (snap / "state").mkdir(parents=True)
        shutil.copy2(str(w.data / "config.json"), str(snap / "state" / "config.json"))
        shutil.copy2(str(w.bin / ("bun" + EXE)), str(snap / ("bun" + EXE)))
        w.put_bun(w.bin / ("bun" + EXE), "9.9.1")
        (w.db / "written-since").write_text("a page saved the next morning")
        (w.data / "update-journal.json").write_text(json.dumps({
            "snap": snap.name, "head": w.head(), "branch": "", "tag": "v0.4.0", "from": "0.1.0", "le": day(8),
            "before": {"rc": 0, "fails": [], "sources": ["default", "vault"]}}))
        w.run(day(12))  # the computer stayed off for days: the snapshot is not purged first
        self.assertStatus("rolled_back", "interrupted")
        self.assertEqual((w.bin / ("bun" + EXE)).read_bytes(), before["bun"])
        self.assertEqual((w.db / "written-since").read_text(), "a page saved the next morning")
        self.assertEqual(w.head(), before["head"])
        self.assertFalse((w.data / "update-journal.json").exists())

    def test_an_incomplete_rollback_stays_red_until_gbrain_starts(self):
        w = self.w
        w.install("v0.1.0")
        w.run(day(0))
        w.control("bun.json", {"install_rc": 1})
        w.run(day(8), "--bun-base-url", w.publish_bun("9.9.1"))
        res = self.assertStatus("broken", "broken")
        self.assertIn("bun install", res["cause"])
        w.control("gbrain.json", {"version_rc": 1})
        w.run(day(9))
        self.assertStatus("broken", "broken")
        self.assertIn({"niveau": "rouge", "code": "broken", "tag": "v0.4.0"}, w.result()["alertes"])
        w.control("gbrain.json", {})
        w.control("bun.json", {})
        w.run(day(10))
        self.assertStatus("updated")
        self.assertEqual(w.head(), w.sha("v0.3.0"))


class Bun(Base):
    bun = {"0.4.0": (">=9.9.0", "9.9.1")}

    def bun_bytes(self):
        return (self.w.bin / ("bun" + EXE)).read_bytes()

    def test_exact_bun_from_the_release_workflow(self):
        w = self.w
        w.install("v0.1.0")
        w.run(day(0))
        w.run(day(8), "--bun-base-url", w.publish_bun("9.9.1"))
        res = self.assertStatus("updated")
        self.assertEqual((res["bun"], res["bun_change"]), ("9.9.1", "1.0.0 -> 9.9.1"))
        out = subprocess.run([str(w.bin / ("bun" + EXE)), "--version"], stdout=subprocess.PIPE,
                             creationflags=NO_WINDOW).stdout.decode().strip()
        self.assertEqual(out, "9.9.1")
        self.assertEqual({c["version"] for c in w.calls("bun") if c["args"][:1] == ["install"]}, {"9.9.1"})

    def test_wrong_checksum_installs_nothing(self):
        w = self.w
        w.install("v0.1.0")
        w.run(day(0))
        before = self.bun_bytes()
        w.run(day(8), "--bun-base-url", w.publish_bun("9.9.1", checksum_ok=False))
        res = self.assertStatus("postponed", "bun_checksum")
        self.assertIn({"niveau": "rouge", "code": "bun_empreinte"}, res["alertes"])
        self.assertEqual(self.bun_bytes(), before)
        self.assertEqual(w.head(), w.sha("v0.1.0"))
        self.assertFalse((w.data / "rollback").exists())
        self.assertEqual(w.switch_calls(), [])

    def test_no_bun_for_this_processor(self):
        w = self.w
        w.install("v0.1.0")
        w.run(day(0))
        w.run(day(8), "--bun-base-url", w.publish_bun("9.9.1", with_asset=False))
        res = self.assertStatus("postponed", "bun_asset")
        self.assertIn({"niveau": "orange", "code": "bun_introuvable"}, res["alertes"])
        self.assertEqual(w.head(), w.sha("v0.1.0"))


class Helpers(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="gbu-")).resolve()
        self.addCleanup(rmtree, self.dir)

    def test_timeout_kills_the_whole_tree(self):
        pidfile = self.dir / "grandchild.pid"
        child = ("import subprocess, sys, time\n"
                 "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
                 "open(sys.argv[1], 'w').write(str(g.pid))\n"
                 "time.sleep(120)\n")
        started = time.monotonic()
        rc, _, _ = gu.run([sys.executable, "-c", child, str(pidfile)], 3)
        self.assertEqual(rc, 124)
        self.assertLess(time.monotonic() - started, 60)
        grandchild = int(pidfile.read_text())
        for _ in range(100):
            if not pid_alive(grandchild):
                break
            time.sleep(0.1)
        self.assertFalse(pid_alive(grandchild))

    def test_bun_can_be_replaced_while_it_runs(self):
        ctl = self.dir / "ctl"
        ctl.mkdir()
        target, new = self.dir / ("bun" + EXE), self.dir / ("new-bun" + EXE)
        put_program(target, program(FAKE_BUN, PROG="bun", VERSION="1.0.0", CTL=str(ctl)))
        put_program(new, program(FAKE_BUN, PROG="bun", VERSION="9.9.1", CTL=str(ctl)))
        p = subprocess.Popen([str(target), "sleep", "60"], stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        try:
            time.sleep(1)
            gu.replace_bun(target, new, "prev-1.0.0-20261001-040000")
            self.assertEqual(target.read_bytes(), new.read_bytes())
            if WINDOWS:
                self.assertTrue((self.dir / "bun.exe.prev-1.0.0-20261001-040000").exists())
        finally:
            kill(p)

    def test_versions_and_names(self):
        self.assertEqual(gu.version("v0.60.210.0"), (0, 60, 210, 0))
        self.assertEqual(gu.version("1.4.3"), (1, 4, 3, 0))
        self.assertIsNone(gu.version("latest-stable"))
        self.assertGreater(gu.version("v0.60.210.1"), gu.version("0.60.210"))
        self.assertTrue(gu.TAG_RE.match("v0.60.210.0") and not gu.TAG_RE.match("v0.60.210.0-rc1"))
        self.assertEqual(gu.stamp_of("bun.exe.prev-1.4.2-20261020-040000-2"),
                         dt.datetime(2026, 10, 20, 4, 0, tzinfo=dt.timezone.utc))
        self.assertRegex(gu.bun_asset(), r"^bun-[a-z]+-[a-z0-9-]+$")


if __name__ == "__main__":
    unittest.main()
