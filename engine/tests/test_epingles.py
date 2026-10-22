"""Tests of .github/ci/epingles.py: the installers install.ps1 pins (Git for Windows, Python), against
their latest release. A newer one out for 14 days or more is red: the pin has to move.

    python3 -I -m unittest discover -s engine/tests -p test_epingles.py -v
"""
import contextlib
import datetime as dt
import importlib.util
import io
import os
import re
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "..", ".github", "ci", "epingles.py")
INSTALL_PS1 = os.path.join(HERE, "..", "..", "install.ps1")

_spec = importlib.util.spec_from_file_location("epingles", SCRIPT)
epingles = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(epingles)

PS1 = '''$OFFICIAL_INSTALLERS = @{
    "Git" = @{
        Version = "2.56.0.2"
        x64 = @("https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.2/Git-2.56.0.2-64-bit.exe", "1")
    }
    "Python" = @{
        Version = "3.14.8"
        x64 = @("https://www.python.org/ftp/python/3.14.8/python-3.14.8-amd64.exe", "2")
    }
}
'''
NOW = dt.datetime(2026, 11, 5, 12, 0, tzinfo=dt.timezone.utc)
PYTHONS = [("Python 3.14.8", "2026-09-30T21:32:03Z", False), ("Python 3.15.0", "2026-10-01T10:00:00Z", False),
           ("Python 3.13.15", "2026-10-09T10:00:00Z", False)]


def fake(git=("v2.56.0.windows.2", "2026-10-05T10:00:00Z"), pythons=PYTHONS, broken=()):
    def fetch(url):
        if url in broken:
            raise OSError("no network")
        if url == epingles.GIT_API:
            return {"tag_name": git[0], "published_at": git[1], "prerelease": False,
                    "html_url": "https://github.com/git-for-windows/git/releases/tag/" + git[0]}
        if url == epingles.PYTHON_API:
            return [{"name": n, "release_date": d, "pre_release": pre, "is_published": True,
                     "resource_uri": "https://www.python.org/api/v2/downloads/release/%d/" % i}
                    for i, (n, d, pre) in enumerate(pythons)]
        raise AssertionError("unexpected url " + url)
    return fetch


class Epingles(unittest.TestCase):
    def setUp(self):
        fd, self.ps1 = tempfile.mkstemp(suffix=".ps1")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(PS1)
        self.addCleanup(os.remove, self.ps1)

    def run_main(self, fetch, now=NOW):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = epingles.main(["--install-ps1", self.ps1], fetch=fetch, now=now)
        return rc, out.getvalue()

    def test_the_pins_are_install_ps1s(self):
        with open(INSTALL_PS1, encoding="utf-8") as f:
            text = f.read()
        found = epingles.pins(text)
        self.assertEqual(sorted(found), ["Git", "Python"])
        # The version each installer's URL names, for both architectures.
        git_tag = re.sub(r"^(\d+\.\d+\.\d+)(?:\.(\d+))?$", lambda m: "v%s.windows.%s" % (m.group(1), m.group(2) or "1"), found["Git"])
        self.assertEqual(epingles.git_version(git_tag), found["Git"])
        for arch in ("64-bit", "arm64"):
            self.assertIn("/%s/Git-%s-%s.exe" % (git_tag, found["Git"], arch), text)
        for arch in ("amd64", "arm64"):
            self.assertIn("/python/%s/python-%s-%s.exe" % ((found["Python"],) * 2 + (arch,)), text)
        self.assertEqual(epingles.pins(PS1), {"Git": "2.56.0.2", "Python": "3.14.8"})
        self.assertEqual(epingles.pins("nothing"), {})

    def test_git_for_windows_tags(self):
        # The first build of a Git version has no fourth number in its installer's name (Git-2.57.0-64-bit.exe).
        for tag, version in (("v2.56.0.windows.3", "2.56.0.3"), ("v2.57.0.windows.1", "2.57.0"),
                             ("v2.57.0-rc1.windows.1", None), ("2.57.0", None)):
            self.assertEqual(epingles.git_version(tag), version, tag)
        self.assertGreater(epingles.vtuple("2.57.0"), epingles.vtuple("2.56.0.3"))
        self.assertLess(epingles.vtuple("2.56.0"), epingles.vtuple("2.56.0.2"))

    def test_up_to_date_is_green(self):
        rc, out = self.run_main(fake())
        self.assertEqual(rc, 0, out)
        self.assertNotIn("::error::", out)
        self.assertNotIn("::warning::", out)
        self.assertIn("Git for Windows : 2.56.0.2, à jour", out)
        # Python's newest of the pinned minor only: 3.15 is another line, a choice of its own.
        self.assertIn("Python : 3.14.8, à jour", out)

    def test_a_newer_release_younger_than_14_days_warns_with_its_day(self):
        rc, out = self.run_main(fake(git=("v2.56.0.windows.3", "2026-10-22T19:10:21Z")))
        self.assertEqual(rc, 0, out)
        self.assertIn("::warning::Git for Windows 2.56.0.3 est sortie le 2026-10-22 ; install.ps1 épingle 2.56.0.2 : "
                      "à monter à partir du 2026-11-05", out)

    def test_a_newer_release_out_for_14_days_is_red(self):
        pythons = PYTHONS + [("Python 3.14.9", "2026-10-21T13:08:09Z", False), ("Python 3.14.10rc1", "2026-10-30T10:00:00Z", True)]
        rc, out = self.run_main(fake(pythons=pythons))
        self.assertEqual(rc, 1, out)
        self.assertIn("::error::Python 3.14.9 est sortie le 2026-10-21, il y a 14 jours ; install.ps1 épingle encore 3.14.8", out)
        self.assertNotIn("3.14.10", out)
        self.assertNotIn("3.15", out)
        # The day it becomes due is already red.
        rc, out = self.run_main(fake(git=("v2.56.0.windows.3", "2026-10-22T19:10:21Z")),
                                now=dt.datetime(2026, 11, 5, 19, 10, 21, tzinfo=dt.timezone.utc))
        self.assertEqual(rc, 1, out)
        self.assertIn("::error::Git for Windows 2.56.0.3", out)

    def test_a_source_it_cannot_read_is_red(self):
        rc, out = self.run_main(fake(broken=(epingles.PYTHON_API,)))
        self.assertEqual(rc, 1, out)
        self.assertIn("::error::Python : impossible de lire %s : no network" % epingles.PYTHON_API, out)
        self.assertIn("Git for Windows : 2.56.0.2, à jour", out)
        for odd in ({"tag_name": "x", "published_at": "2026-10-22T19:10:21Z"}, {}, []):
            rc, out = self.run_main(lambda url, odd=odd: odd if url == epingles.GIT_API else fake()(url))
            self.assertEqual(rc, 1, out)
            self.assertIn("::error::Git for Windows : ", out)

    def test_pins_missing_from_install_ps1_are_red(self):
        with open(self.ps1, "w", encoding="utf-8") as f:
            f.write("$OFFICIAL_INSTALLERS = @{}\n")
        rc, out = self.run_main(fake())
        self.assertEqual(rc, 1, out)
        self.assertIn("::error::install.ps1 n'épingle plus Git", out)

    def test_the_token_never_follows_a_redirect(self):
        seen = []

        class Answer(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def urlopen(req, timeout):
            seen.append(req)
            return Answer(b"{}")

        real = epingles.urllib.request.urlopen
        epingles.urllib.request.urlopen = urlopen
        self.addCleanup(setattr, epingles.urllib.request, "urlopen", real)
        old = os.environ.get("GH_TOKEN")
        os.environ["GH_TOKEN"] = "jeton-de-test"
        self.addCleanup(lambda: os.environ.pop("GH_TOKEN") if old is None else os.environ.__setitem__("GH_TOKEN", old))
        epingles.fetch_json(epingles.GIT_API)
        epingles.fetch_json(epingles.PYTHON_API)
        github, python = seen
        # urllib copies req.headers onto a redirect, whatever the host; it never copies unredirected ones.
        self.assertNotIn("Authorization", github.headers)
        self.assertEqual(github.unredirected_hdrs.get("Authorization"), "Bearer jeton-de-test")
        self.assertIsNone(python.get_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
