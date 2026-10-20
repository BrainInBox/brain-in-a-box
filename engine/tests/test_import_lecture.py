"""Tests of the import's reading (engine/hooks/_import_lecture.py): each export format, each hostile
archive (refused, or its bad part set aside, within time and memory), the exact allow-list of files
(never the account's), the person's words only, masking before anything is kept.

Every archive is built in memory by import_fixtures.py: no real export, no file committed.

    python3 -I -m unittest discover -s engine/tests -v
"""
import io
import json
import os
import shutil
import socket
import stat
import sys
import tempfile
import time
import tracemalloc
import unittest
import zipfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "hooks"))
sys.path.insert(0, str(HERE))

import _import_lecture as L  # noqa: E402
import import_fixtures as F  # noqa: E402

POSIX = os.name != "nt"


def lire(*datas, **kw):
    return L.lire_fichiers([io.BytesIO(d) for d in datas], **kw)


def tout(lecture):
    return "\n".join(b["titre"] + "\n" + b["texte"] for b in lecture.blocs())


class Ouverts:
    """Records the names zipfile opens, to show that only the allow-list is ever read."""

    def __init__(self):
        self.noms = []
        self._open = zipfile.ZipFile.open

    def __enter__(self):
        rec = self

        def open_(zf, name, mode="r", *a, **kw):
            if mode == "r":   # a fixture being built writes
                rec.noms.append(name.orig_filename if isinstance(name, zipfile.ZipInfo) else name)
            return rec._open(zf, name, mode, *a, **kw)
        self.patch = mock.patch.object(zipfile.ZipFile, "open", open_)
        self.patch.start()
        return self

    def __exit__(self, *a):
        self.patch.stop()


class Formats(unittest.TestCase):

    def test_chatgpt_2025_the_branch_shown_the_person_and_what_chatgpt_saved(self):
        with Ouverts() as o:
            lec = lire(F.chatgpt_2025())
        self.assertEqual(o.noms, ["conversations.json"])
        self.assertEqual(lec.source, "chatgpt")
        self.assertEqual(lec.compte["conversations"], 4)
        txt = tout(lec)
        self.assertIn("Le Comptoir", txt)
        self.assertIn("Marc", txt)
        self.assertIn("Voici la photo de la vitrine.", txt)
        self.assertIn("Shopify", txt)                       # a voice message's transcription
        self.assertNotIn("BRANCHE-MORTE", txt)             # regenerated and edited branches
        self.assertNotIn("D'accord, c'est noté", txt)      # the assistant's own words
        self.assertNotIn(F.CANARI_CACHE, txt)              # a hidden node
        self.assertNotIn("asset_pointer", txt)
        genres = {}
        for b in lec.blocs():
            genres[b["genre"]] = genres.get(b["genre"], "") + b["texte"]
        self.assertIn("Habite à Lyon", genres["souvenir"])
        self.assertIn("Je tiens une boulangerie à Lyon", genres["instructions"])
        self.assertIn("Réponds en français", genres["instructions"])
        self.assertIn("vend son pain au marché", txt)      # a memory saved along the way (bio)
        for c in F.canaris():
            self.assertNotIn(c, txt)

    def test_chatgpt_2026_split_files_without_the_hidden_nodes(self):
        with Ouverts() as o:
            lec = lire(F.chatgpt_2026(n=250))
        self.assertEqual(sorted(o.noms), ["conversations-000.json", "conversations-001.json", "conversations-002.json"])
        self.assertEqual(lec.compte["conversations"], 250)
        self.assertFalse([b for b in lec.blocs() if b["genre"] == "instructions"])
        for c in F.canaris():
            self.assertNotIn(c, tout(lec))

    def test_claude_combined_archive(self):
        with Ouverts() as o:
            lec = lire(F.claude_combinee())
        self.assertNotIn("users.json", o.noms)
        self.assertEqual(lec.source, "claude")
        blocs = lec.blocs()
        par = {}
        for b in blocs:
            par.setdefault(b["genre"], []).append(b)
        self.assertIn("Les Trois Épis", par["souvenir"][0]["texte"])
        self.assertEqual(par["memoire_projet"][0]["titre"], "Boutique en ligne")
        self.assertEqual([b["titre"] for b in par["projet"]], ["Boutique en ligne"])   # not Claude's example
        self.assertIn("expert du commerce", par["projet"][0]["texte"])
        self.assertEqual(len(par["conversation"]), 3)
        self.assertEqual([b["date"] for b in par["conversation"]], ["2026-10-05", "2026-09-20", "2026-09-01"])
        txt = tout(lec)
        self.assertIn("Inès", txt)
        self.assertNotIn("Bien noté", txt)
        self.assertNotIn("réflexion", txt)
        for c in F.canaris():   # attachments, whole project documents, the example project, the account
            self.assertNotIn(c, txt)

    def test_claude_split_zips_together_as_one_and_the_account_zip_left(self):
        z = F.claude_scindee()
        with Ouverts() as o:
            lec = lire(z["conversations"], z["memoire"], z["projets"], z["compte"])
        self.assertFalse({"users.json", "login_history.json"} & set(o.noms))
        self.assertEqual(lec.compte["compte_seul"], 1)
        self.assertEqual(sorted(b["genre"] for b in lec.blocs()),
                         sorted(b["genre"] for b in lire(F.claude_combinee()).blocs()))
        self.assertEqual(len(lec.empreintes), 4)
        with self.assertRaises(L.Refus) as e:
            lire(z["compte"])
        self.assertEqual(e.exception.code, "compte")
        self.assertIn("infos de ton compte", e.exception.message)

    def test_the_json_of_links_is_refused_and_no_link_followed(self):
        def no_network(*a, **kw):
            raise AssertionError("a connection was attempted")
        with mock.patch.object(socket, "socket", no_network), mock.patch.object(socket, "create_connection", no_network):
            with self.assertRaises(L.Refus) as e:
                lire(F.claude_liens())
        self.assertEqual(e.exception.code, "liens")
        self.assertIn("Je ne suis jamais ces liens", e.exception.message)

    def test_a_json_given_alone_is_read_by_its_content(self):
        lec = lire(json.dumps(F.claude_conversations()).encode())
        self.assertEqual((lec.source, lec.compte["conversations"]), ("claude", 3))
        lec = lire(("\ufeff" + json.dumps(F.chatgpt_conversations(3))).encode())   # a BOM too
        self.assertEqual((lec.source, lec.compte["conversations"]), ("chatgpt", 3))
        lec = lire(json.dumps(F.claude_memoires()).encode())
        self.assertEqual([b["genre"] for b in lec.blocs()], ["souvenir", "memoire_projet"])

    def test_an_export_zipped_again_in_a_folder(self):
        data = F.reecrit_dans_un_dossier()
        with Ouverts() as o:
            lec = lire(data)
        self.assertEqual(lec.compte["conversations"], 120)
        self.assertTrue(all(n.startswith("Export ChatGPT/conversations-") for n in o.noms), o.noms)

    def test_chatgpt_and_claude_together_are_refused(self):
        with self.assertRaises(L.Refus) as e:
            lire(F.chatgpt_2025(), F.claude_combinee())
        self.assertEqual(e.exception.code, "melange")

    def test_a_memory_of_an_unknown_shape_is_read_at_best_and_said(self):
        odd = [{"profile": {"markdown": "Profil : boulangère à Lyon, se lève à 4 h, aime le pain au levain."},
                "id": "x", "notes": ["court"]}]
        lec = lire(F.zip_bytes([("memories/9f1e2d3c-aaaa-4bbb-8ccc-1d2e3f4a5b6c.json", json.dumps(odd))]))
        self.assertEqual(lec.compte["formes_inconnues"], 1)
        b = lec.blocs()[0]
        self.assertIn("forme inconnue", b["titre"])
        self.assertIn("profile.markdown : Profil : boulangère", b["texte"])


class Hostiles(unittest.TestCase):
    """Each one refused or set aside, fast, without growing memory."""

    def refus(self, data, code):
        t = time.monotonic()
        with self.assertRaises(L.Refus) as e:
            lire(data)
        self.assertEqual(e.exception.code, code)
        self.assertLess(time.monotonic() - t, 10)
        return e.exception

    def test_refused_archives(self):
        for name, code in (("bombe", "trop_gros"), ("taille_menteuse", "abime"), ("doublon", "ambigu"),
                           ("chiffree", "chiffre"), ("repertoire_enorme", "trop_gros"), ("tronquee", "abime"),
                           ("pas_un_zip", "pas_un_export"), ("utf16", "illisible"), ("utf8_invalide", "illisible"),
                           ("lien_symbolique", "pas_un_export"), ("methode_inconnue", "illisible")):
            with self.subTest(name):
                self.refus(getattr(F, name)(), code)

    def test_a_forged_zip64_directory_is_refused_before_zipfile_reads_it(self):
        self.assertEqual(lire(F.zip64()).compte["conversations"], 3)   # a real Zip64 end is fine
        forged = (F.zip64(cd_size=1 << 40), F.zip64(entries=10 ** 9), F.repertoire_enorme())
        with mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError("zipfile reached")):
            for data in forged:
                self.refus(data, "trop_gros")

    def test_entries_sharing_their_bytes_are_refused(self):
        data = bytearray(F.zip_bytes([("conversations-000.json", json.dumps(F.chatgpt_conversations(3))),
                                      ("conversations-001.json", json.dumps(F.chatgpt_conversations(3)))]))
        # The second entry's central record pointed at the first one's data (CVE-2024-0450's trick).
        i = data.rfind(b"PK\x01\x02")
        data[i + 42:i + 46] = (0).to_bytes(4, "little")
        self.refus(bytes(data), "abime")

    def test_bad_elements_are_set_aside_and_the_rest_read(self):
        for name in ("json_profond", "entier_geant", "nan"):
            with self.subTest(name):
                t = time.monotonic()
                lec = lire(getattr(F, name)())
                self.assertLess(time.monotonic() - t, 10)
                self.assertEqual(lec.compte["illisibles"], 1)
                self.assertEqual(lec.compte["conversations"], 2)

    def test_a_huge_message_is_skipped_as_it_streams(self):
        data = F.message_geant()   # 100 MB, in one message
        lec = lire(data)
        self.assertEqual((lec.compte["trop_gros"], lec.compte["conversations"]), (1, 2))
        # Memory follows the cap on one element, not the message: with a cap of 4 MB, a few MB.
        with mock.patch.object(L, "ELEMENT_MAX", 4 << 20):
            tracemalloc.start()
            try:
                lec = lire(data)
                peak = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
        self.assertEqual((lec.compte["trop_gros"], lec.compte["conversations"]), (1, 2))
        self.assertLess(peak, 24 << 20, "memory grew with the message")

    def test_names_that_climb_or_hide_are_never_read(self):
        with Ouverts() as o:
            lec = lire(F.noms_hostiles())
        self.assertEqual(o.noms, ["conversations-001.json"])
        self.assertEqual(lec.compte["noms_refuses"], 5)

    def test_a_cycle_ends_and_a_missing_current_node_takes_the_latest_leaf(self):
        lec = lire(F.cycle())
        self.assertIn("Premier message de la boucle", tout(lec))
        lec = lire(F.sans_noeud_courant())
        self.assertIn("feuille la plus récente", tout(lec))

    def test_every_limit_is_checked_on_declared_sizes_first(self):
        with mock.patch.object(L, "ENTREE_MAX", 100):
            self.refus(F.chatgpt_2025(), "trop_gros")
        with mock.patch.object(L, "LU_MAX", 100):
            self.refus(F.chatgpt_2025(), "trop_gros")
        with mock.patch.object(L, "FICHIERS_MAX", 2):
            self.refus(F.chatgpt_2026(n=250), "trop_gros")


class Texte(unittest.TestCase):

    def test_secrets_are_masked_before_anything_is_kept(self):
        for data in (F.chatgpt_2025(), F.claude_combinee()):
            lec = lire(data)
            txt = json.dumps(lec.blocs(), ensure_ascii=False)
            self.assertGreaterEqual(lec.compte["masques"], 4)
            self.assertIn("[REDACTED]", txt)
            for c in F.canaris():
                self.assertNotIn(c, txt)

    def test_hidden_characters_go_and_mark_their_block(self):
        lec = lire(F.chatgpt_2025())
        piege = [b for b in lec.blocs() if b["titre"] == "Recette copiée"][0]
        self.assertGreater(piege["caches"], 0)
        self.assertNotIn(F.TAG, piege["texte"])
        self.assertIn(F.PIEGE, piege["texte"])   # data: the AI is told so, and the note checked
        self.assertEqual(lec.caches, 1)

    def test_messages_cut_and_only_the_first_and_last_six(self):
        msgs = ["Message %02d : " % i + "mot " * 400 for i in range(30)]
        c = F.chatgpt_conversation("c-long", "Longue", F.T0, msgs)
        lec = lire(json.dumps([c]).encode())
        b = lec.blocs()[0]
        kept = [l for l in b["texte"].split("\n") if l.startswith("• ")]
        self.assertEqual([l[2:12] for l in kept], ["Message %02d" % i for i in list(range(6)) + list(range(24, 30))])
        self.assertTrue(all(len(l) <= L.MESSAGE_MAX + 2 for l in kept))
        self.assertLessEqual(len(b["texte"]), L.CONVERSATION_MAX)

    def test_logs_and_code_pasted_by_the_person_are_set_aside(self):
        log = "\n".join("2026-10-01 12:00:%02d ERROR worker %d failed" % (i, i) for i in range(20))
        code = "def f(x):\n    return x\nclass A:\n    pass\nimport os\n"
        c = F.chatgpt_conversation("c-log", "Logs", F.T0, [log, code, "Mon four est un Bongard de 1998."])
        lec = lire(json.dumps([c]).encode())
        self.assertEqual(lec.compte["ecartes"], 2)
        self.assertEqual(lec.blocs()[0]["texte"], "• Mon four est un Bongard de 1998.")

    def test_the_most_recent_conversations_are_kept_within_the_cap(self):
        convs = [F.chatgpt_conversation("c-%d" % i, "C%d" % i, F.T0 + i * 3600, ["Sujet %d " % i + "x" * 900])
                 for i in range(50)]
        convs = convs[25:] + convs[:25]   # split by id, not by date
        lec = lire(json.dumps(convs).encode(), plafond=10 * 1000)
        dates = [b["titre"] for b in lec.blocs()]
        self.assertEqual(dates, ["C%d" % i for i in range(49, 49 - len(dates), -1)])
        self.assertLessEqual(len(dates), 11)
        self.assertGreater(lec.compte["anciennes"], 30)
        self.assertEqual(lec.compte["conversations"], 50)

    def test_what_an_earlier_import_took_is_not_taken_again(self):
        first = lire(F.claude_combinee())
        keys = {b["cle"] for b in first.blocs()}
        again = lire(F.claude_combinee(), connues=keys)
        self.assertEqual(again.blocs(), [])
        self.assertEqual(again.compte["deja"], len(keys))


class Collage(unittest.TestCase):

    def test_a_pasted_answer(self):
        lec = L.lire_collage([F.collage_chatgpt()])
        b = lec.blocs()
        self.assertEqual([x["genre"] for x in b], ["collage"])
        self.assertNotIn("```", b[0]["texte"])
        self.assertIn("Tient la boulangerie", b[0]["texte"])
        self.assertGreater(b[0]["caches"], 0)
        for c in F.canaris():
            self.assertNotIn(c, b[0]["texte"])
        self.assertEqual(len(lec.empreintes[0]), 64)
        self.assertEqual(L.lire_collage([F.collage_chatgpt()]).empreintes, lec.empreintes)

    def test_too_long_empty_and_split(self):
        with self.assertRaises(L.Refus) as e:
            L.lire_collage(["x" * (L.COLLAGE_MAX + 1)])
        self.assertEqual(e.exception.code, "collage_long")
        with self.assertRaises(L.Refus) as e:
            L.lire_collage(["```\n```\n  \n"])
        self.assertEqual(e.exception.code, "vide")
        lec = L.lire_collage(["".join("- souvenir numéro %d\n" % i for i in range(11000))])
        blocs = lec.blocs()
        self.assertEqual(len(blocs), 3)
        self.assertTrue(all(len(b["texte"]) <= L.COLLAGE_BLOC for b in blocs))
        self.assertEqual(len({b["cle"] for b in blocs}), 3)


class Ouvrir(unittest.TestCase):

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="biab-import-open-"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_a_regular_file_opens_anything_else_is_refused(self):
        p = self.d / "export.zip"
        p.write_bytes(F.chatgpt_2025())
        with L.ouvrir(str(p)) as f:
            self.assertEqual(L.lire_fichiers([f]).compte["conversations"], 4)
        with self.assertRaises(L.Refus) as e:
            L.ouvrir(str(self.d / "absent.zip"))
        self.assertEqual(e.exception.code, "introuvable")
        with self.assertRaises(L.Refus) as e:
            L.ouvrir(str(self.d))
        self.assertIn(e.exception.code, ("lien", "introuvable"))
        if POSIX:
            os.symlink(str(p), str(self.d / "lien.zip"))
            with self.assertRaises(L.Refus) as e:
                L.ouvrir(str(self.d / "lien.zip"))
            self.assertEqual(e.exception.code, "lien")
            os.mkfifo(str(self.d / "tube.zip"))
            t = time.monotonic()
            with self.assertRaises(L.Refus) as e:
                L.ouvrir(str(self.d / "tube.zip"))
            self.assertEqual(e.exception.code, "lien")
            self.assertLess(time.monotonic() - t, 5, "waited on a pipe")

    def test_a_descriptor_handed_over_by_the_app(self):
        p = self.d / "export.zip"
        p.write_bytes(F.claude_combinee())
        fd = os.open(str(p), os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            with L.depuis_fd(fd) as f:
                self.assertEqual(L.lire_fichiers([f]).source, "claude")
        finally:
            os.close(fd)
        r, w = os.pipe()
        try:
            with self.assertRaises(L.Refus) as e:
                L.depuis_fd(r)
            self.assertEqual(e.exception.code, "lien")
        finally:
            os.close(r)
            os.close(w)
        self.assertTrue(stat.S_ISREG(os.stat(str(p)).st_mode))


if __name__ == "__main__":
    unittest.main()
