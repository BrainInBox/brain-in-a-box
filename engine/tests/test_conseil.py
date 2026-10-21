"""Tests of the advice at the start (engine/hooks/_conseil.py): each sentence on made-up homes, Codex
never offered while it is closed (and offered once a copy of the gate opens it, as the other test
benches do), Hermes said, no login file ever opened.

    python3 -I -m unittest discover -s engine/tests -v
"""
import builtins
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
FAKE = HERE / "fake_claude.py"
sys.path.insert(0, str(HOOKS))

import _agents  # noqa: E402
import _conseil  # noqa: E402
import _headless_claude as hc  # noqa: E402

POSIX = os.name != "nt"
OUVERT = frozenset({"darwin", "linux", "windows"})


class Conseil(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="biab-conseil-"))
        self.home = self.root / "home"
        self.apps = self.root / "Applications"
        (self.home / ".claude" / "fake").mkdir(parents=True)
        self.apps.mkdir()
        self.saved_env = dict(os.environ)
        for k in ("CODEX_HOME", "HERMES_HOME", "HERMES_DB", "CLAUDE_CODE_OAUTH_TOKEN"):
            os.environ.pop(k, None)
        os.environ.update(HOME=str(self.home), USERPROFILE=str(self.home), LOCALAPPDATA=str(self.home / "AppData"),
                          PATH=str(self.root / "nobin"))
        self.saved_managed, hc.MANAGED_DIR = hc.MANAGED_DIR, str(self.root / "managed")

    def tearDown(self):
        hc.MANAGED_DIR = self.saved_managed
        os.environ.clear()
        os.environ.update(self.saved_env)
        shutil.rmtree(self.root, ignore_errors=True)

    def auth(self, method):
        (self.home / ".claude" / "fake" / "auth").write_text(method, encoding="utf-8")

    def app(self, name):
        if os.name == "nt":
            local = self.home / "AppData"
            if name == "Claude":
                (local / "AnthropicClaude").mkdir(parents=True, exist_ok=True)
                (local / "AnthropicClaude" / "claude.exe").write_bytes(b"")
            else:
                (local / "Packages" / "OpenAI.ChatGPT-Desktop_2p2nqsd0c76g0").mkdir(parents=True, exist_ok=True)
            return
        d = self.apps / ("%s.app" % name) / "Contents"
        d.mkdir(parents=True, exist_ok=True)
        (d / "Info.plist").write_text("<plist/>", encoding="utf-8")

    def codex_login(self):
        (self.home / ".codex").mkdir(exist_ok=True)
        (self.home / ".codex" / "auth.json").write_text('{"tokens": "secret"}', encoding="utf-8")

    def ask(self, claude=False, codex=False, ouvert=False):
        return _conseil.conseil(claude=[sys.executable, str(FAKE)] if claude else [],
                                codex=["codex"] if codex else [], applications=[str(self.apps)],
                                codex_ouvert=OUVERT if ouvert else frozenset())

    def test_closed_gate_the_four_sentences_and_never_codex(self):
        p = _conseil.REGLES["phrases"]
        self.auth("claude.ai")
        self.assertEqual(self.ask(claude=True)["phrase"], p["claude_connecte"])
        self.auth("none")
        self.assertEqual(self.ask(claude=True)["phrase"], p["claude_vu"])
        self.assertEqual(self.ask()["phrase"], p["rien"])
        self.app("ChatGPT")
        c = self.ask()
        self.assertEqual((c["conseil"], c["phrase"]), ("claude", p["chatgpt_bientot"]))
        self.assertIn("arrive bientôt", c["phrase"])
        self.app("Claude")
        self.assertEqual(self.ask()["phrase"], p["claude_vu"])
        self.codex_login()
        for case in (self.ask(), self.ask(codex=True), self.ask(claude=True, codex=True)):
            self.assertEqual(case["conseil"], "claude")
            self.assertFalse(case["vu"]["porte"])

    def test_every_closed_combination_advises_claude(self):
        regles = _conseil.REGLES
        for bits in range(32):
            faits = {k: bool(bits >> i & 1) for i, k in enumerate(("claude", "claude_connecte", "chatgpt",
                                                                   "chatgpt_connecte", "hermes"))}
            faits["porte"] = False
            choix, cle = _conseil.decider(faits, regles)
            self.assertEqual(choix, "claude", faits)
            self.assertNotIn("Codex, l'IA de ChatGPT, sur ton abonnement", regles["phrases"][cle])

    def test_the_gate_opened_codex_is_offered(self):
        self.codex_login()
        c = self.ask(codex=True, ouvert=True)
        self.assertEqual(c["conseil"], "codex")
        self.app("Claude")
        self.assertEqual(self.ask(codex=True, ouvert=True)["conseil"], "les_deux")

    def test_the_gate_is_read_from_agents_py(self):
        self.app("ChatGPT")
        with mock.patch.object(_agents, "CODEX_OUVERT", OUVERT):
            c = _conseil.conseil(claude=[], codex=[], applications=[str(self.apps)])
        self.assertEqual(c["conseil"], "codex")
        c = _conseil.conseil(claude=[], codex=[], applications=[str(self.apps)])
        self.assertEqual(c["conseil"], "claude")

    def test_hermes_is_said_when_it_is_there(self):
        self.assertIsNone(self.ask()["hermes"])
        (self.home / ".hermes").mkdir()
        (self.home / ".hermes" / "config.yaml").write_text("model: x\n", encoding="utf-8")
        h = self.ask()["hermes"]
        self.assertTrue(h.startswith("J'ai vu Hermes, ton IA au quotidien."), h)
        shutil.rmtree(str(self.home / ".hermes"))
        os.environ["HERMES_HOME"] = str(self.root / "ailleurs")
        (self.root / "ailleurs").mkdir()
        (self.root / "ailleurs" / "state.db").write_bytes(b"")
        self.assertIsNotNone(self.ask()["hermes"])

    def test_the_evening_reading_of_hermes_is_said_only_where_it_happens(self):
        # The reflection reads $HERMES_DB, else ~/.hermes/state.db (daily-reflection.py): only then is it said.
        soir = "chaque soir ton cerveau lit aussi ses conversations"
        (self.home / ".hermes").mkdir()
        (self.home / ".hermes" / "config.yaml").write_text("model: x\n", encoding="utf-8")
        c = self.ask()
        self.assertTrue(c["vu"]["hermes"] and not c["vu"]["hermes_lu"])
        self.assertNotIn("soir", c["hermes"])
        (self.home / ".hermes" / "state.db").write_bytes(b"")
        self.assertIn(soir, self.ask()["hermes"])
        # Hermes elsewhere: seen, but its conversations are not where the reflection looks.
        shutil.rmtree(str(self.home / ".hermes"))
        os.environ["HERMES_HOME"] = str(self.root / "ailleurs")
        (self.root / "ailleurs").mkdir()
        (self.root / "ailleurs" / "state.db").write_bytes(b"")
        c = self.ask()
        self.assertTrue(c["vu"]["hermes"] and not c["vu"]["hermes_lu"])
        self.assertNotIn("soir", c["hermes"])
        os.environ["HERMES_DB"] = str(self.root / "ailleurs" / "state.db")
        self.assertIn(soir, self.ask()["hermes"])
        # Nothing is promised before the brain is there: its skills come with it.
        for h in (c["hermes"], self.ask()["hermes"]):
            self.assertIn("arrivent dans Claude avec ton cerveau", h)
            self.assertNotIn("déjà dans Claude", h)

    def test_a_chatgpt_user_is_told_which_subscription(self):
        self.app("ChatGPT")
        p = self.ask()["phrase"]
        self.assertIn("abonnement Claude Pro ou Max", p)
        self.assertIn("ton abonnement ChatGPT ne suffit pas", p)
        self.assertIn("ce que ChatGPT sait de toi", p)
        for phrase in _conseil.REGLES["phrases"].values():
            self.assertNotIn(" y apporter", phrase)

    def test_no_login_file_is_ever_opened(self):
        self.codex_login()
        (self.home / ".hermes").mkdir()
        (self.home / ".hermes" / "config.yaml").write_text("x", encoding="utf-8")
        secrets = [str(self.home / ".codex" / "auth.json"), str(self.home / ".hermes" / ".env"),
                   str(self.home / ".hermes" / "config.yaml")]
        if POSIX:
            os.mkfifo(secrets[1])   # opened, it would wait forever
        opened = []
        real_open, real_os_open = builtins.open, os.open

        def spy(path, *a, **kw):
            opened.append(str(path))
            return real_open(path, *a, **kw)

        def spy_os(path, *a, **kw):
            opened.append(str(path))
            return real_os_open(path, *a, **kw)
        done = []
        with mock.patch.object(builtins, "open", spy), mock.patch.object(os, "open", spy_os):
            t = threading.Thread(target=lambda: done.append(self.ask(codex=True)))
            t.start()
            t.join(30)
        self.assertTrue(done, "the advice waited on a login file")
        self.assertTrue(done[0]["vu"]["chatgpt_connecte"])
        self.assertTrue(done[0]["vu"]["hermes"])
        self.assertFalse([p for p in opened if os.path.exists(p) and
                          any(os.path.samefile(p, s) for s in secrets if os.path.exists(s))], opened)

    def test_the_block_the_app_reads(self):
        text = (HOOKS / "_conseil.py").read_text(encoding="utf-8")
        start = text.index('CONSEIL_JSON = r"""\n') + len('CONSEIL_JSON = r"""\n')
        block = json.loads(text[start:text.index('\n"""', start)])
        self.assertEqual(block, _conseil.REGLES)
        self.assertEqual(set(block["faits"]), {"porte", "claude", "claude_connecte", "chatgpt", "chatgpt_connecte",
                                               "hermes", "hermes_lu"})
        for r in block["hermes"]:
            self.assertLessEqual(set(r["si"]), set(block["faits"]))
            self.assertTrue(r["si"]["hermes"])
            self.assertIn(r["phrase"], block["phrases"])
        for r in block["regles"]:
            self.assertLessEqual(set(r["si"]), set(block["faits"]))
            self.assertIn(r["phrase"], block["phrases"])
            self.assertIn(r["conseil"], ("claude", "codex", "les_deux"))
        self.assertEqual([r["si"] for r in block["regles"] if len(r["si"]) == 1], [{"porte": False}, {"porte": True}])

    def test_the_command_says_it(self):
        spec = importlib.util.spec_from_file_location("importer_memoire", str(HOOKS / "importer-memoire.py"))
        im = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(im)
        (self.home / ".hermes").mkdir()
        (self.home / ".hermes" / "state.db").write_bytes(b"")
        with mock.patch.object(_conseil, "sondes", lambda **kw: {"porte": False, "claude": False,
                                                                 "claude_connecte": False, "chatgpt": True,
                                                                 "chatgpt_connecte": False, "hermes": True,
                                                                 "hermes_lu": True}):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = im.main(["conseil"])
        self.assertEqual(rc, 0)
        lines = out.getvalue().strip().split("\n")
        self.assertEqual(lines, [_conseil.REGLES["phrases"]["chatgpt_bientot"], _conseil.REGLES["phrases"]["hermes_lu"]])


if __name__ == "__main__":
    unittest.main()
