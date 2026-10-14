"""Tests of the night check (engine/hooks/controle.py), Mac and Windows.

Every run happens in a throwaway HOME, vault and state folder: the user's real
brain, ~/.claude and ~/.gbrain are never read nor written.

    python3 -I -m unittest discover -s engine/tests -v
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter
from contextlib import contextmanager, redirect_stdout
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(os.path.dirname(HERE), "hooks")
CONTROLE = os.path.join(HOOKS, "controle.py")
sys.path.insert(0, HOOKS)
sys.path.insert(0, HERE)

import _controle_rules as rules  # noqa: E402
import _redact  # noqa: E402
import controle  # noqa: E402
import vault_piege  # noqa: E402
from vault_piege import age_files, commit_all, git, init_repo, write  # noqa: E402

ACTION_TRAILER = re.compile(r"^Brain-Controle-Action: ([A-Z][0-9]-\d{4}-\d{2,3})$", re.M)


def isolated_env(home):
    env = dict(os.environ, HOME=home, USERPROFILE=home, GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.path.join(home, ".gitconfig"))
    for k in ("BRAIN_DIR", "BRAIN_CONTROLE_DIR", "GBRAIN_HOME", "CLAUDE_CODE_ENTRYPOINT", "XDG_CONFIG_HOME"):
        env.pop(k, None)
    return env


def tree(root):
    """rel -> sha256 of every file of the vault but .git (links: their target)."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            if os.path.islink(p):
                out[rel] = "link:" + os.readlink(p)
            else:
                with open(p, "rb") as f:
                    out[rel] = hashlib.sha256(f.read()).hexdigest()
    return out


def read(root, rel):
    with open(os.path.join(root, *rel.split("/")), "rb") as f:
        return f.read().decode("utf-8")


def all_text_under(path):
    chunks = []
    for dirpath, _, filenames in os.walk(path):
        for fn in filenames:
            with open(os.path.join(dirpath, fn), "rb") as f:
                chunks.append(f.read().decode("utf-8", "replace"))
    return "\n".join(chunks)


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="controle-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.vault = os.path.join(self.tmp, "Brain")
        self.etat = os.path.join(self.tmp, "etat")
        self.env = isolated_env(self.home)

    def cli(self, *args):
        p = subprocess.run([sys.executable, "-I", CONTROLE] + list(args) + ["--brain", self.vault, "--etat", self.etat],
                           env=self.env, capture_output=True, timeout=600)
        out = p.stdout.decode("utf-8", "replace")
        self.assertEqual(p.returncode if args[0] != "annuler" else 0, 0, out + p.stderr.decode("utf-8", "replace"))
        return out

    def git_out(self, *args):
        return git(self.vault, *args).stdout.decode("utf-8", "replace")

    def head(self):
        return self.git_out("rev-parse", "HEAD").strip()

    def etat_json(self):
        with open(os.path.join(self.etat, "etat.json"), encoding="utf-8") as f:
            return json.load(f)

    def journal(self):
        runs = sorted(p for p in os.listdir(os.path.join(self.etat, "runs")) if p.endswith(".json"))
        with open(os.path.join(self.etat, "runs", runs[-1]), encoding="utf-8") as f:
            return json.load(f)

    def action_ids(self, since=None):
        log = self.git_out("log", "--format=%B", (since + "..HEAD") if since else "HEAD")
        return ACTION_TRAILER.findall(log)

    def small_vault(self, extra=None, committed=True):
        init_repo(self.vault)
        today = date.today()
        mem = ["# Mémoire", "", "## Active decisions", "- tout est chiffré", ""]
        for back in (40, 35, 30, 3):
            mem += ["## %s journée" % (today - timedelta(days=back)).isoformat(), "", "- fait %d" % back, ""]
        files = {
            "Profile/memory.md": "\n".join(mem) + "\n",
            "Projects/deploy.md": "# Déploiement\n\n```bash\ngit push --force origin main\n```\n",
            "Projects/a.md": "# A\n\nUne note normale, avec du contenu utile.\n",
        }
        files.update(extra or {})
        for rel, text in files.items():
            write(self.vault, rel, text)
        if committed:
            commit_all(self.vault, days_ago=40)
        age_files(self.vault)

    @contextmanager
    def in_process_home(self):
        saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "BRAIN_DIR", "BRAIN_CONTROLE_DIR",
                                                "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL")}
        os.environ.update(HOME=self.home, USERPROFILE=self.home, GIT_CONFIG_NOSYSTEM="1",
                          GIT_CONFIG_GLOBAL=os.path.join(self.home, ".gitconfig"))
        os.environ.pop("BRAIN_DIR", None)
        os.environ.pop("BRAIN_CONTROLE_DIR", None)
        try:
            yield
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def run_in_process(self, mode="nuit", run_class=None):
        opts = controle.parser().parse_args([mode, "--brain", self.vault, "--etat", self.etat])
        with self.in_process_home(), redirect_stdout(io.StringIO()):
            return (run_class or controle.Run)(opts, mode).execute()


# --------------------------------------------------------------------------- the trapped vault, one night

class TrapVault(Sandbox):
    """The vault of spec §14.1, checked once; each test reads one aspect."""

    @classmethod
    def setUpClass(cls):
        cls.box = Sandbox("setUp")
        cls.box.setUp()
        b = cls.box
        cls.info = vault_piege.build(b.vault)
        cls.before = {rel: read(b.vault, rel) for rel in tree(b.vault) if rel.endswith(".md")
                      and not os.path.islink(os.path.join(b.vault, rel))}
        cls.base_head = b.head()
        cls.out = b.cli("nuit")
        cls.after = {rel: read(b.vault, rel) for rel in tree(b.vault) if rel.endswith(".md")
                     and not os.path.islink(os.path.join(b.vault, rel))}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.box.tmp, True)

    def setUp(self):
        b = self.box
        self.tmp, self.home, self.vault, self.etat, self.env = b.tmp, b.home, b.vault, b.etat, b.env

    def commits_by_type(self):
        log = self.git_out("log", "--format=%x01%B", self.base_head + "..HEAD")
        out = {}
        for body in log.split("\x01"):
            ids = ACTION_TRAILER.findall(body)
            if ids:
                self.assertIn("Brain-Controle-Run: ", body)
                out.setdefault(ids[0][:2], []).append(body)
        return out

    def test_each_type_has_its_own_commit(self):
        by_type = self.commits_by_type()
        for t in ("S2", "X1", "M1", "M2", "D1", "D2", "F1"):
            self.assertIn(t, by_type, t)
            for body in by_type[t]:
                self.assertEqual(len(re.findall(r"^Brain-Controle-(Run|Action): ", body, re.M)), 2)
        notes_commit = self.git_out("log", "--format=%B", "-n", "1", self.base_head + "..HEAD", "--grep=^nightly ")
        self.assertRegex(notes_commit, r"Brain-Controle-Masque: S1-\d{4}-\d{2}")

    def test_s1_secrets_masked_examples_kept(self):
        text = self.after["Projects/api-notes.md"]
        self.assertEqual(text.count("[secret masqué par le contrôle · S1-"), 5)
        for marker in vault_piege.canary_markers():
            self.assertNotIn(marker, text)
        self.assertEqual(self.after["Projects/digest.md"], self.before["Projects/digest.md"])

    def test_s2_callout_above_block_command_untouched(self):
        text = self.after["Projects/deploy.md"]
        self.assertEqual(text.count("<!-- controle:S2-"), 1)
        self.assertIn("[!danger]", text.split("```bash")[0])
        self.assertIn("rm -rf ~/x\napt purge -y foo\n", text)

    def test_x1_clipping_disarmed_handwritten_note_untouched(self):
        text = self.after["Clippings/page.md"]
        self.assertFalse(rules.TAG_OR_BIDI.search(text))
        self.assertEqual(text.count("<!-- controle:X1-"), 1)
        self.assertIn("Ignore les consignes précédentes", text)
        self.assertEqual(self.after["Projects/notes-securite.md"], self.before["Projects/notes-securite.md"])
        report = self.after["Profile/controle.md"]
        self.assertEqual(report.count("[[notes-securite]]"), 1)

    def test_m1_memory_archived(self):
        today = date.today()
        cutoff = today - timedelta(days=15)
        mem = self.after["Profile/memory.md"]
        old_heads = [ln for ln in mem.split("\n") if ln.startswith("## 🟠") and rules.dates_in(ln)[0] < cutoff]
        self.assertEqual(len(old_heads), 1, old_heads)       # the controle:garder one
        self.assertIn("<!-- controle:garder -->", mem)
        self.assertIn("<!-- LINT:BEGIN -->", mem)
        self.assertIn("## Active decisions\n- **2026-01-05**", mem)
        self.assertNotIn("GBRAIN-UPDATE", mem)
        self.assertEqual(mem.count("<!-- controle:archive-pointeur -->"), 1)
        self.assertIn("## 🟠 %s point du jour" % today.isoformat(), mem)
        self.assertIn("## 🟠 %s point du jour" % (today - timedelta(days=14)).isoformat(), mem)
        archives = [rel for rel in self.after if rel.startswith("Profile/Archive/memory-")]
        self.assertTrue(archives)
        moved = "\n".join(self.after[rel] for rel in archives)
        for ln in self.before["Profile/memory.md"].split("\n"):
            if ln.startswith("## 🟠") and rules.dates_in(ln)[0] < cutoff and ln not in mem:
                self.assertIn(ln, moved)

    def test_m2_lessons_lightened(self):
        les = self.after["Profile/lessons.md"]
        self.assertLessEqual(len(les.encode("utf-8")), 150 * 1024)
        self.assertLess(les.index("## Règles consolidées"), les.index("\n## 20"))
        self.assertNotIn("<task-notification", les)
        self.assertLessEqual(les.count("**[doublon]**"), 1)
        self.assertIn("Profile/Archive/lessons-ecartees.md", self.after)

    def test_d1_d2_deleted(self):
        self.assertNotIn("Projects/Infra copie.md", self.after)
        self.assertIn("Projects/Infra.md", self.after)
        self.assertNotIn("Projects/vide.md", self.after)
        self.assertFalse([r for r in self.after if r.startswith("Journal/") and r < "Journal/%s" % (
            date.today() - timedelta(days=30)).isoformat()])
        self.assertEqual(self.after["Projects/fm-ouvert.md"], self.before["Projects/fm-ouvert.md"])

    def test_f1_frontmatter_repaired(self):
        self.assertIn('title: "{{X}}"', self.after["Projects/fm-titre.md"])
        self.assertIn("\n  status: active\n", self.after["Projects/fm-tab.md"])

    def test_conservation(self):
        """Every line removed from a note exists elsewhere in the vault or in quarantine
        (masked lines), or the note was removed by a commit git can revert."""
        quarantine = all_text_under(os.path.join(self.etat, "quarantaine"))
        everywhere = Counter()
        for text in self.after.values():
            everywhere.update(norm(ln) for ln in text.split("\n"))
        for rel, text in self.before.items():
            if rel not in self.after:
                log = self.git_out("log", "--format=%B", "--diff-filter=D", "-n", "1", "--", rel)
                self.assertRegex(log, r"Brain-Controle-Action: D[12]-", rel)
                continue
            gone = Counter(text.split("\n")) - Counter(self.after[rel].split("\n"))
            for ln in gone:
                if ln.strip():
                    self.assertTrue(everywhere[norm(ln)] or json.dumps(ln, ensure_ascii=False)[1:-1] in quarantine,
                                    "%s: lost %r" % (rel, ln[:80]))

    def test_no_leak(self):
        markers = vault_piege.canary_markers()
        history = self.git_out("log", "-p", "--all")
        etat_dir = os.path.join(self.etat)
        outputs = {
            "git log -p --all": history,
            "report": self.after["Profile/controle.md"],
            "runs": all_text_under(os.path.join(etat_dir, "runs")),
            "etat.json": read(etat_dir, "etat.json"),
            "stdout": self.out,
        }
        for name, text in outputs.items():
            for m in markers:
                self.assertNotIn(m, text, "%s leaks %s" % (name, m[:12]))
        quarantine = all_text_under(os.path.join(etat_dir, "quarantaine"))
        for m in markers[:-1]:
            self.assertIn(m, quarantine)
        self.assertNotIn("CANARI-LIEN", quarantine)
        if os.name != "nt":
            for fn in os.listdir(os.path.join(etat_dir, "quarantaine")):
                self.assertEqual(os.stat(os.path.join(etat_dir, "quarantaine", fn)).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(etat_dir).st_mode & 0o777, 0o700)

    def test_symlink_never_followed(self):
        with open(self.info["canary"], encoding="utf-8") as f:
            self.assertTrue(f.read().startswith("CANARI-LIEN"))
        if os.path.islink(os.path.join(self.vault, "Projects", "lien.md")):
            self.assertNotIn("lien", " ".join(a["chemins"][0] for a in self.journal()["actions"]))

    def test_report(self):
        report = self.after["Profile/controle.md"]
        fm, _, _, state = rules.parse_frontmatter(report)
        self.assertEqual(state, "ok")
        self.assertEqual(fm["type"], "rapport")
        self.assertIn(fm["verdict"], ("vert", "orange", "rouge"))
        for section in ("## Ce que j'ai corrigé", "## À regarder toi-même", "## Santé", "## Historique"):
            self.assertIn(section, report)
        self.assertNotIn(self.tmp, report)
        self.assertNotIn(os.path.realpath(self.tmp), report)
        self.assertFalse(re.search(r"(/Users/|/home/|[A-Z]:\\)", report))
        self.assertIn("Projets/ et projets/", report)
        etat = self.etat_json()
        self.assertEqual(etat["rapport"], "Profile/controle.md")
        self.assertEqual(etat["corrections"], len([a for a in self.journal()["actions"] if a["statut"] == "fait"]))

    def test_z_second_night_does_nothing(self):
        age_files(self.vault)
        head = self.head()
        self.cli("nuit")
        self.assertEqual(self.action_ids(head), [])
        deploy = read(self.vault, "Projects/deploy.md")
        self.assertEqual(deploy.count("<!-- controle:S2-"), 1)
        self.assertEqual(read(self.vault, "Clippings/page.md").count("<!-- controle:X1-"), 1)


def norm(line):
    line = rules.strip_tag_bidi(line).replace("\t", "  ").replace('"', "").replace("\\", "")
    return " ".join(line.split())


# --------------------------------------------------------------------------- one rule at a time

class SecretAlreadyCommitted(Sandbox):
    def test_s1_own_commit_red_verdict(self):
        token = "gh" + "p_" + "canariHIST" + "x7Y8" * 7
        self.small_vault({"Projects/vieux.md": "# Vieux\n\ntoken: %s\n" % token})
        self.cli("nuit")
        ids = [i for i in self.action_ids() if i.startswith("S1")]
        self.assertEqual(len(ids), 1)
        self.assertNotIn(token, read(self.vault, "Projects/vieux.md"))
        etat = self.etat_json()
        self.assertEqual(etat["verdict"], "rouge")
        self.assertEqual(etat["secrets_a_changer"], 1)
        self.assertIn("change-le", read(self.vault, "Profile/controle.md"))
        out = self.cli("annuler", "derniere-nuit")
        self.assertIn("historique", out)
        self.assertIn(token, read(self.vault, "Projects/vieux.md"))
        self.assertTrue(os.path.exists(os.path.join(self.etat, "blanche.json")))
        age_files(self.vault)
        head = self.head()
        self.cli("nuit")
        self.assertEqual(self.action_ids(head), [])
        self.assertIn(token, read(self.vault, "Projects/vieux.md"))


class Contract(Sandbox):
    def test_buggy_fix_is_put_back(self):
        self.small_vault()
        before = read(self.vault, "Profile/memory.md")
        real = rules.MAKERS["M1"]

        def buggy(note, targets, ctx, aid, limit=None):
            ch = real(note, targets, ctx, aid, limit)
            text = ch.writes[note.rel].split("\n")
            del text[2]
            ch.writes[note.rel] = "\n".join(text)
            return ch

        rules.MAKERS["M1"] = buggy
        try:
            self.run_in_process()
        finally:
            rules.MAKERS["M1"] = real
        self.assertEqual(read(self.vault, "Profile/memory.md"), before)
        self.assertFalse(os.path.exists(os.path.join(self.vault, "Profile", "Archive")))
        failed = [a for a in self.journal()["actions"] if a["type"] == "M1"]
        self.assertEqual([a["statut"] for a in failed], ["echec"])
        self.assertEqual(self.etat_json()["verdict"], "rouge")
        self.assertNotIn("M1", [i[:2] for i in self.action_ids()])
        self.assertIn("S2", [i[:2] for i in self.action_ids()])


class Paths(Sandbox):
    def test_decomposed_accents_are_still_fixed(self):
        name = "Projects/De\u0301ploiement.md"
        self.small_vault({name: "# D\n\n```sh\nterraform destroy\n```\n"})
        self.cli("nuit")
        rel = next(r for r in tree(self.vault) if r.endswith("ploiement.md"))
        self.assertIn("<!-- controle:S2-", read(self.vault, rel))


class Caps(Sandbox):
    def test_sixty_dangerous_blocks_give_thirty_callouts(self):
        blocks = "\n".join("Étape %d\n\n```bash\nrm -rf /tmp/build-%d\n```\n" % (i, i) for i in range(60))
        self.small_vault({"Projects/menage.md": "# Ménage\n\n" + blocks})
        self.cli("nuit")
        text = read(self.vault, "Projects/menage.md") + read(self.vault, "Projects/deploy.md")
        self.assertEqual(text.count("<!-- controle:S2-"), 30)
        self.assertIn("la suite la nuit prochaine", read(self.vault, "Profile/controle.md"))

    def test_breaker_on_five_hundred_duplicates(self):
        body = "# Copie\n\n" + "Le même paragraphe recopié partout, assez long pour compter comme un doublon. " * 4
        extra = {"Projects/copies/c%03d.md" % i: body for i in range(500)}
        self.small_vault(extra)
        self.cli("nuit")
        self.assertNotIn("D1", [i[:2] for i in self.action_ids()])
        self.assertEqual(len([f for f in os.listdir(os.path.join(self.vault, "Projects", "copies"))]), 500)
        self.assertIn({"niveau": "orange", "code": "disjoncteur"}, self.etat_json()["alertes"])


class Cas(Sandbox):
    def test_file_changed_between_read_and_write_is_skipped(self):
        self.small_vault()
        vault = self.vault

        class Racing(controle.Run):
            def apply_action(self, t, rel, planned):
                if t == "S2":
                    with open(os.path.join(vault, "Projects", "deploy.md"), "a", encoding="utf-8") as f:
                        f.write("ajout de l'utilisateur\n")
                    old = time.time() - 7200
                    os.utime(os.path.join(vault, "Projects", "deploy.md"), (old, old))
                return controle.Run.apply_action(self, t, rel, planned)

        self.run_in_process(run_class=Racing)
        text = read(self.vault, "Projects/deploy.md")
        self.assertNotIn("<!-- controle:S2-", text)
        self.assertTrue(text.endswith("ajout de l'utilisateur\n"))
        causes = {(s["type"], s["cause"]) for s in self.journal()["abandons"]}
        self.assertIn(("S2", "changee_depuis_lecture"), causes)

    def test_fresh_mtime_is_skipped(self):
        self.small_vault()
        os.utime(os.path.join(self.vault, "Projects", "deploy.md"), None)
        self.cli("nuit")
        self.assertNotIn("<!-- controle:S2-", read(self.vault, "Projects/deploy.md"))
        self.assertIn(("S2", "modifiee_recemment"), {(s["type"], s["cause"]) for s in self.journal()["abandons"]})

    def test_crlf_and_bom_kept(self):
        raw = b"\xef\xbb\xbf# Win\r\n\r\n```powershell\r\nRemove-Item C:\\x -Recurse -Force\r\n```\r\n"
        init_repo(self.vault)
        write(self.vault, "Projects/win.md", None, raw=raw)
        write(self.vault, "Projects/a.md", "# A\n\nUne note normale, avec du contenu utile.\n")
        commit_all(self.vault)
        age_files(self.vault)
        self.cli("nuit")
        with open(os.path.join(self.vault, "Projects", "win.md"), "rb") as f:
            data = f.read()
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        self.assertIn("<!-- controle:S2-".encode(), data)
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))
        self.assertIn(b"Remove-Item C:\\x -Recurse -Force\r\n", data)


class ReportOnly(Sandbox):
    def assert_untouched(self, cause):
        before = tree(self.vault)
        head = self.head() if os.path.isdir(os.path.join(self.vault, ".git")) else None
        out = self.cli("nuit")
        self.assertEqual(tree(self.vault), before)
        if head:
            self.assertEqual(self.head(), head)
        etat = self.etat_json()
        self.assertEqual(etat["corrections"], 0)
        self.assertEqual(etat["cause"], cause)
        self.assertIn("rien corrigé", out)

    def test_vault_without_git(self):
        os.makedirs(os.path.join(self.vault, "Projects"))
        write(self.vault, "Projects/deploy.md", "# D\n\n```bash\nrm -rf ~/x\n```\n")
        write(self.vault, "Projects/cle.md", "password: hunter2-sans-git-42\n")
        age_files(self.vault)
        self.assert_untouched("pas_de_git")

    def test_rebase_in_progress(self):
        self.small_vault({"Projects/cle.md": "password: hunter2-rebase-42\n"}, committed=True)
        os.makedirs(os.path.join(self.vault, ".git", "rebase-merge"))
        self.assert_untouched("rebase")

    def test_commit_refused_by_hook(self):
        self.small_vault()
        hook = os.path.join(self.vault, ".git", "hooks", "pre-commit")
        with open(hook, "w", encoding="utf-8", newline="\n") as f:
            f.write("#!/bin/sh\nexit 1\n")
        os.chmod(hook, 0o755)
        before = tree(self.vault)
        head = self.head()
        self.cli("nuit")
        self.assertEqual(tree(self.vault), before)
        self.assertEqual(self.head(), head)
        self.assertEqual(self.etat_json()["corrections"], 0)
        self.assertIn({"niveau": "orange", "code": "commit_refuse"}, self.etat_json()["alertes"])

    def test_essai_writes_nothing(self):
        self.small_vault({"Projects/cle.md": "password: hunter2-essai-42\n"}, committed=False)
        before = tree(self.vault)
        out = self.cli("essai")
        self.assertEqual(tree(self.vault), before)
        self.assertFalse(os.path.exists(self.etat))
        self.assertIn("ferait S1", out)
        self.assertNotIn("hunter2-essai-42", out)

    def test_pause_reports_without_fixing(self):
        self.small_vault()
        self.cli("pause")
        head = self.head()
        self.cli("nuit")
        self.assertEqual(self.action_ids(head), [])
        self.assertEqual(self.etat_json()["cause"], "pause")
        self.cli("reprendre")
        age_files(self.vault)
        self.cli("nuit")
        self.assertTrue(self.action_ids(head))


class Locking(Sandbox):
    def test_second_run_says_already_running(self):
        self.small_vault()
        os.makedirs(self.etat)
        with open(os.path.join(self.etat, "run.lock"), "w") as f:
            f.write("%d %d\n" % (os.getpid(), int(time.time())))
        head = self.head()
        out = self.cli("nuit")
        self.assertIn("déjà en cours", out)
        self.assertEqual(self.head(), head)

    def test_stale_lock_is_taken_over(self):
        self.small_vault()
        os.makedirs(self.etat)
        with open(os.path.join(self.etat, "run.lock"), "w") as f:
            f.write("%d %d\n" % (os.getpid(), int(time.time()) - 3 * 3600))
        out = self.cli("nuit")
        self.assertNotIn("déjà en cours", out)
        self.assertFalse(os.path.exists(os.path.join(self.etat, "run.lock")))


class Undo(Sandbox):
    def night(self):
        age_files(self.vault)
        head = self.head()
        self.cli("nuit")
        return head

    def test_undo_last_night(self):
        secret = "hunter2-undo-" + "canari42"
        self.small_vault({"Projects/f.md": "---\ntitle: a: b\n---\n\n# F\n\nUne note avec un en-tête cassé.\n"})
        write(self.vault, "Projects/cle.md", "# Clé\n\npassword: %s\n" % secret)
        age_files(self.vault)
        self.cli("nuit")
        base = self.git_out("log", "--format=%H", "-n", "1", "--grep=^nightly ").strip()
        touched = sorted({p for a in self.journal()["actions"] if a.get("commit") for p in a["chemins"]})
        self.assertNotIn(secret, read(self.vault, "Projects/cle.md"))
        out = self.cli("annuler", "derniere-nuit", "--essai")
        self.assertIn("J'annulerais", out)
        self.assertNotIn(secret, out)
        self.cli("annuler", "derniere-nuit")
        self.assertEqual(git(self.vault, "diff", "--quiet", base, "HEAD", "--", *touched, check=False).returncode, 0)
        self.assertIn(secret, read(self.vault, "Projects/cle.md"))
        self.assertIn("## Annulé le", read(self.vault, "Profile/controle.md"))
        head = self.night()
        self.assertEqual(self.action_ids(head), [], "an undone fix must never come back")
        self.assertNotIn(secret, self.etat_json().get("rapport") or "")

    def test_undo_one_after_user_edit_elsewhere(self):
        self.small_vault()
        self.cli("nuit")
        m1 = [i for i in self.action_ids() if i.startswith("M1")][0]
        mem = os.path.join(self.vault, "Profile", "memory.md")
        with open(mem, "a", encoding="utf-8") as f:
            f.write("- ajout de l'utilisateur en bas\n")
        commit_all(self.vault, "user", days_ago=0)
        self.cli("annuler", m1)
        text = read(self.vault, "Profile/memory.md")
        self.assertIn("ajout de l'utilisateur en bas", text)
        self.assertIn("- fait 40", text)
        self.assertNotIn("archive-pointeur", text)
        self.assertIn("S2", [i[:2] for i in self.action_ids()])
        self.assertIn("<!-- controle:S2-", read(self.vault, "Projects/deploy.md"))

    def test_undo_conflict_is_left_by_hand(self):
        self.small_vault()
        self.cli("nuit")
        s2 = [i for i in self.action_ids() if i.startswith("S2")][0]
        path = os.path.join(self.vault, "Projects", "deploy.md")
        text = read(self.vault, "Projects/deploy.md").replace("Ne l'exécute pas", "Ne l'exécute JAMAIS")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        commit_all(self.vault, "user", days_ago=0)
        head = self.head()
        out = self.cli("annuler", s2)
        self.assertIn("à la main", out)
        self.assertEqual(self.head(), head)
        self.assertEqual(read(self.vault, "Projects/deploy.md"), text)
        self.assertEqual(self.git_out("status", "--porcelain"), "")

    def test_two_undone_nights_pause_the_fixes(self):
        self.small_vault()
        self.cli("nuit")
        self.cli("annuler", "derniere-nuit")
        write(self.vault, "Projects/autre.md", "# Autre\n\n```sh\ngit reset --hard HEAD~3\n```\n")
        commit_all(self.vault, "user", days_ago=0)
        head = self.night()
        self.assertTrue(self.action_ids(head))
        out = self.cli("annuler", "derniere-nuit")
        self.assertIn("pause", out)
        self.assertTrue(os.path.exists(os.path.join(self.etat, "pause")))


class Reports(Sandbox):
    def test_quiet_night_leaves_the_report_alone(self):
        init_repo(self.vault)
        write(self.vault, "Projects/a.md", "# A\n\nUne note normale, avec du contenu utile.\n")
        write(self.vault, "Profile/controle.md", "# Ancien rapport\n")
        commit_all(self.vault)
        age_files(self.vault)
        head = self.head()
        self.cli("nuit")
        self.assertEqual(read(self.vault, "Profile/controle.md"), "# Ancien rapport\n")
        self.assertEqual(self.head(), head)
        self.assertEqual(self.etat_json()["verdict"], "vert")

    def test_status_and_lessons_commands(self):
        self.small_vault({"Profile/lessons.md": "# Lessons\n\n## 2026-01-02\n- **[a]** -> règle : b\n- **[c]** -> d\n"})
        self.cli("nuit")
        self.assertIn("Dernier contrôle", self.cli("statut"))
        out = self.cli("lecons")
        self.assertIn("2 entrées", out)
        self.assertIn("lessons.md", out)


class SecretShapes(unittest.TestCase):
    def kinds(self, text):
        return [(text[s:e], k) for s, e, k in _redact.find_secrets(text)]

    def test_examples_and_digests_are_not_secrets(self):
        for text in ("password: changeme", "token: ${TOKEN}", "api_key: <ta-clé>", "pwd: xxxxxxxx",
                     "sha256 " + "ab" * 32, "mot de passe : voir le trousseau", "password: [REDACTED]",
                     "secret: [secret masqué par le contrôle · S1-1014-01]"):
            self.assertEqual(self.kinds(text), [], text)

    def test_real_shapes(self):
        s = vault_piege.fake_secrets()
        self.assertEqual(self.kinds("aws " + s["aws"]), [(s["aws"], "aws")])
        self.assertEqual(self.kinds("x " + s["jwt"]), [(s["jwt"], "jwt")])
        self.assertEqual(self.kinds("**Password** : hunter2-ci-x"), [("hunter2-ci-x", "mot_de_passe")])
        self.assertEqual(self.kinds("postgres://admin:S3cret-x@db/x"), [("S3cret-x", "url")])
        fake_jwt = "eyJ" + vault_piege.b64('{"x":1}')[3:] + ".abcdefghijkl.abcdefghijkl"
        self.assertEqual(self.kinds(fake_jwt), [])

    def test_doubtful_values_are_reported_only(self):
        self.assertEqual([k for _, _, k in _redact.find_doubtful_secrets("api_key=" + "a1" * 32)], ["mot_de_passe"])
        self.assertEqual(_redact.find_secrets("api_key=" + "a1" * 32), [])

    def test_redact_unchanged(self):
        self.assertEqual(_redact.redact("password: hunter2-ci-x")[0], "password: [REDACTED]")


if __name__ == "__main__":
    unittest.main()
