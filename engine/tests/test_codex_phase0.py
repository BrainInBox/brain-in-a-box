"""The captures of step 0 of the Codex plan (docs/proposals/codex.md, "Étape 0 — résultats"):
the real Codex CLI, 0.162.1 and 0.163.0-alpha.11, against a local fake server on GitHub's macOS,
Ubuntu and Windows runners. No secret: fake tokens only. The runs' artifacts expired after five
days; these copies are what phases 2 and 4 build their fixtures from, and what the plan's facts
rest on. One missing, unreadable, or holding something shaped like a secret fails here.

    fixtures/codex/phase0/<os>-<version>/          rollouts, hook payloads, features, classification
    fixtures/codex/phase0/facts/run<n>-<run id>/<os>/[newest/]results.json, lock-request.json

    python3 -I -m unittest discover -s engine/tests -v
"""
import json
import re
import unittest
from pathlib import Path

P0 = Path(__file__).resolve().parent / "fixtures" / "codex" / "phase0"
OS = {"macos": "macos", "ubuntu": "linux", "windows": "windows"}   # folder -> the name in file names
STABLE, ALPHA = "0.162.1", "0.163.0-alpha.11"
RUNS = ("run1-38777454388", "run2-38779052837", "run3-38780332011")
SECRET = re.compile(rb"sk-[A-Za-z0-9_-]{16,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|gh[pousr]_[A-Za-z0-9]{20,}"
                    rb"|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY|pa-[A-Za-z0-9_-]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}")
REFUS = ("code-mode host is disabled", "unsupported call: ", "patch rejected: ", "failed to parse function arguments: ")


def captures(os_, version):
    """The files step 0 kept for one system and one version of Codex."""
    n = OS[os_]
    runs = ["bypass", "trusted"]
    if os_ == "windows":
        runs += ["nopwsh", "ps51"]          # Windows PowerShell 5.1, and no pwsh at all
    elif version == STABLE:
        runs += ["profile-" + p for p in ("bashenv", "inline-noise", "noisy", "quiet")]   # a chatty shell profile
        if os_ == "macos":
            runs.append("profile-zsh")
    files = {"classify-%s.json" % n, "features-default.txt"}
    files |= {"hook-payloads-%s-%s.jsonl" % (r, n) for r in runs}
    files |= {"rollout-exec-hooks-%s-%s.jsonl" % (r, n) for r in runs}
    if version == STABLE:
        files |= {"rollout-exec-lock-%s.jsonl" % n, "rollout-exec-vault-%s.jsonl" % n}
        if os_ != "windows":                # the TUI, in a pty
            files |= {"hook-payloads-tui-apikey-%s.jsonl" % n, "rollout-tui-apikey-%s.jsonl" % n}
    return files


def results():
    """{(run, os, "" or "newest"): parsed results.json}."""
    out = {}
    for run in RUNS:
        for os_ in OS:
            for sub in ("", "newest"):
                p = P0 / "facts" / run / os_ / sub / "results.json"
                if p.exists():
                    out[run, os_, sub] = json.loads(p.read_text(encoding="utf-8"))
    return out


class Phase0(unittest.TestCase):

    def test_every_capture_is_there_and_nothing_else(self):
        for os_ in OS:
            for version in (STABLE, ALPHA):
                d = P0 / ("%s-%s" % (os_, version))
                with self.subTest(d=d.name):
                    self.assertTrue(d.is_dir(), d)
                    self.assertEqual({p.name for p in d.iterdir()}, captures(os_, version))
        self.assertEqual(sorted(p.name for p in P0.iterdir()),
                         sorted(["facts"] + ["%s-%s" % (o, v) for o in OS for v in (STABLE, ALPHA)]))

    def test_every_capture_reads(self):
        files = [p for p in P0.rglob("*") if p.is_file()]
        self.assertGreater(len(files), 90)
        for p in files:
            with self.subTest(p=str(p.relative_to(P0))):
                text = p.read_bytes().decode("utf-8")
                self.assertTrue(text.strip())
                if p.suffix == ".json":
                    json.loads(text)
                elif p.suffix == ".jsonl":
                    for line in text.splitlines():
                        if line.strip():
                            json.loads(line)
                else:
                    self.assertEqual(p.suffix, ".txt")

    def test_nothing_shaped_like_a_secret(self):
        for p in P0.rglob("*"):
            if p.is_file():
                self.assertIsNone(SECRET.search(p.read_bytes()), p.relative_to(P0))

    def test_the_facts_of_each_run(self):
        r = results()
        for run in RUNS:
            for os_ in OS:
                with self.subTest(run=run, os=os_):
                    self.assertEqual(r[run, os_, ""]["version"]["codex"], "codex-cli " + STABLE)
                    self.assertTrue((P0 / "facts" / run / os_ / "lock-request.json").is_file())
        for os_ in OS:   # the newest prerelease with binaries, on every system
            self.assertEqual(r["run2-38779052837", os_, "newest"]["version"]["codex"], "codex-cli " + ALPHA)
            self.assertTrue((P0 / "facts" / "run2-38779052837" / os_ / "newest" / "lock-request.json").is_file())

    def test_no_forced_call_ran_under_the_lock(self):
        # The plan's fact (§ "Étape 0", "Verrouillage complet"): every forced call refused, except
        # request_user_input_async on 0.162.1, accepted (a message to the user, no access).
        seen, accepted = 0, set()
        for (run, os_, sub), d in results().items():
            calls = d.get("lock", {}).get("LC_forced_calls") or {}
            for name, c in calls.items():
                with self.subTest(run=run, os=os_, sub=sub, call=name):
                    seen += 1
                    self.assertIs(c["secret_leaked"], False)
                    self.assertEqual(c["files_written"], [])
                    for out in c["tool_outputs"]:
                        if out == '{"accepted":true}':
                            self.assertEqual(name, "request_user_input_async_valid")
                            self.assertEqual(d["version"]["codex"], "codex-cli " + STABLE)
                            accepted.add((run, os_))
                        else:
                            self.assertTrue(out.startswith(REFUS), out)
        self.assertGreater(seen, 100)
        self.assertEqual(accepted, {("run3-38780332011", o) for o in OS})


if __name__ == "__main__":
    unittest.main()
