"""Tests of the field test "app" (engine/tests/terrain_app.py) off a VM: where the app comes from,
what the robot reads in the window, and the walk through it, played by a fake driver that shows
the app's screens with the app's own words.

    python3 -I -m unittest engine/tests/test_terrain_app.py -v
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
import threading
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import terrain  # noqa: E402
import terrain_app as ta  # noqa: E402

NB = " "   # what the app's nbsp() puts before ! ? : ;


class Horloge:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def dormir(self, s):
        self.now += s


def neuf(installe):
    """The app's screens on a fresh Mac (app/src/app.js), as Bibo words them: next screen per
    button, or (seconds, next screen) by itself. `installe()`: the first run got to the end."""
    return {
        "bienvenue": {"titre": "Salut, moi c’est Bibo" + NB + "!",
                      "texte": "Je vais installer ton second cerveau pour Claude. 10 à 20 minutes, tu n’as aucune commande à taper.",
                      "boutons": {"Vérifier mon Mac": "diag-en-cours"}},
        "diag-en-cours": {"titre": "Je regarde ton Mac…", "texte": "Ça prend quelques secondes.",
                          "apres": (3, lambda: "deja" if installe() else "diag")},
        "diag": {"titre": "J’ai fait le tour.", "texte": "Il manque Claude Code, Bun, Obsidian et ton cerveau. Je m’en occupe.",
                 "boutons": {"Revérifier": "diag-en-cours", "Tout installer": "voyage"},
                 "textes": ["macOS 26.6 · Puce Apple · 7 Go de mémoire", "Claude Code", "À installer"]},
        "deja": {"titre": "Tout est déjà installé" + NB + "!",
                 "texte": "Ton cerveau cherche par mots-clés" + NB + ": tu peux lui apprendre à chercher par le sens.",
                 "boutons": {"Activer la recherche par le sens": None, "Continuer": "verif"}},
        "voyage": {"titre": "Une option avant de commencer",
                   "texte": "Ton cerveau retrouve tes notes par mots-clés. Avec une clé Voyage AI, il les retrouve aussi par le sens.",
                   "boutons": {"Ouvrir Voyage AI": None, "Vérifier": None, "Passer (recherche par mots-clés)": "claude",
                               "Continuer avec cette clé": None}},
        "claude": {"titre": "J’installe Claude Code.", "texte": "Étape 1 sur 4. L’outil officiel d’Anthropic.", "apres": (30, "verif-inline")},
        "verif-inline": {"titre": "Je vérifie ton compte Claude…", "apres": (2, "login-inline")},
        "login-inline": {"titre": "Connecte ton compte Claude maintenant",
                         "texte": "Comme ça, si une étape coince, Claude pourra m’aider à la réparer.",
                         "boutons": {"Plus tard": "bun", "Connecter mon compte Claude": None}},
        "bun": {"titre": "J’installe Bun.", "texte": "Étape 2 sur 4. C’est le moteur de ta mémoire.", "apres": (10, "obsidian")},
        "obsidian": {"titre": "J’installe Obsidian.", "apres": (60, "brain")},
        "brain": {"titre": "Je prépare ton cerveau.", "texte": "Étape 4 sur 4. C’est l’étape la plus longue.", "apres": (400, "installe")},
        "installe": {"titre": "C’est installé" + NB + "!", "texte": "Plus qu’une petite étape.", "apres": (2, "verif")},
        "verif": {"titre": "Je vérifie ton compte Claude…", "apres": (2, "login")},
        "login": {"titre": "Dernière étape" + NB + ": ton compte Claude",
                  "texte": "Connecte-toi pour que Claude puisse travailler avec ton cerveau.",
                  "boutons": {"Plus tard": "fini", "Connecter mon compte Claude": None}},
        "fini": {"titre": "Ton second cerveau est prêt" + NB + "!", "texte": "Claude retient maintenant ce que tu lui apprends.",
                 "boutons": {"Activer la recherche par le sens": None, "Ouvrir mon cerveau dans Obsidian": None,
                             "Personnaliser mon cerveau (15 min)": None}},
    }


class FauxPilote(ta.Pilote):
    """The app's window, played from a table of screens on a fake clock."""

    machine = "Mac"
    suffixe = "_macos-universal.dmg"

    def __init__(self, horloge, home=None, change=None):
        self.h = horloge
        self.home = home
        self.fait = False
        self.sources = True     # the app unpacks the sources it was handed
        self.ecrans = neuf(lambda: self.fait)
        for k, v in (change or {}).items():
            self.ecrans[k] = v
        self.etat = None
        self.depuis = 0.0
        self.clics, self.captures, self.envs, self.fermetures = [], [], [], 0
        # {"processus", "textes", "boutons", "part_sur": button that closes it, "suite": {button: next
        # dialog or None}, "apres": (seconds, next dialog)}
        self.dialogue = None
        self.dialogue_depuis = 0.0
        self.vite = False           # offers quick reads of Bibo's words
        self.pleines = 0
        self.comment = ta.CLIC_SOURIS   # how its clicks are done
        self.ecrans_vus = 0             # nouvel_ecran() calls
        self.nuit = []

    def aller(self, etat):
        self.etat = etat() if callable(etat) else etat
        self.depuis = self.h.now
        if self.etat == "fini" and not self.fait:
            self.fait = True
            self.installer_le_kit()

    def installer_le_kit(self):
        """What the app's steps leave on the Mac, and the sources it was handed."""
        if not self.home:
            return
        home = Path(self.home)
        for rel in (".local/bin/claude", ".bun/bin/bun"):
            (home / rel).parent.mkdir(parents=True, exist_ok=True)
            (home / rel).write_text("x")
        (home / "Applications" / "Obsidian.app" / "Contents" / "MacOS").mkdir(parents=True)
        archive = self.envs[-1].get("BIAB_ARCHIVE")
        if archive and self.sources:
            src = home / ".brain-in-a-box" / "src"
            src.mkdir(parents=True)
            with zipfile.ZipFile(archive) as z:
                root = z.namelist()[0].split("/")[0]
                (src / ta.MARQUEUR).write_bytes(z.read(root + "/" + ta.MARQUEUR))

    def ouvrir(self, env):
        self.envs.append(env)
        self.aller("bienvenue")

    def vivante(self):
        return self.etat is not None

    def fermer(self):
        self.etat = None
        self.fermetures += 1
        return "Cmd+Q"

    def spec(self):
        d = self.dialogue
        if d and "apres" in d and self.h.now - self.dialogue_depuis >= d["apres"][0]:
            self.montrer(d["apres"][1])
        spec = self.ecrans[self.etat]
        if "apres" in spec and self.h.now - self.depuis >= spec["apres"][0]:
            self.aller(spec["apres"][1])
            spec = self.ecrans[self.etat]
        elif "sans_dialogue" in spec and self.dialogue is None and self.h.now - self.depuis >= 5:
            self.aller(spec["sans_dialogue"])     # Apple's window closed: the app's step moves on
            spec = self.ecrans[self.etat]
        return spec

    def montrer(self, dialogue):
        self.dialogue, self.dialogue_depuis = dialogue, self.h.now

    def nouvel_ecran(self):
        self.ecrans_vus += 1

    def ecarter_la_nuit(self):
        self.nuit.append("ecartee")

    def rendre_la_nuit(self):
        self.nuit.append("rendue")
        return {"taches": [{"nom": "BrainNightly", "etat": "Ready"}]}

    def rapide(self):
        if not self.vite or self.etat is None:
            return None
        self.h.now += 0.1
        spec = self.spec()
        return ta.norm(spec["titre"]), ta.norm(spec.get("texte", ""))

    def lire(self):
        self.h.now += 0.5   # reading the window takes a moment
        self.pleines += 1
        if self.etat is None:
            return ta.Ecran()
        spec = self.spec()
        dialogues = [dict(self.dialogue)] if self.dialogue else []
        late = self.h.now - self.depuis < spec.get("boutons_apres", 0)   # the tree shows the words first
        return ta.Ecran(fenetre=True, titre=spec["titre"], texte=spec.get("texte", ""),
                        boutons=[] if late else [(b, True) for b in spec.get("boutons", {})], textes=spec.get("textes", []),
                        dialogues=dialogues)

    def cliquer(self, libelle, processus=None):
        self.clics.append((processus, libelle) if processus else libelle)
        how = self.comment(libelle) if callable(self.comment) else self.comment
        if processus:
            if self.dialogue and libelle == self.dialogue.get("part_sur"):
                self.dialogue = None
            elif self.dialogue and libelle in self.dialogue.get("suite", {}):
                self.montrer(self.dialogue["suite"][libelle])
            return how
        nxt = self.ecrans[self.etat].get("boutons", {}).get(libelle)
        if nxt:
            self.aller(nxt)
        return how

    def capturer(self, chemin):
        Path(chemin).write_bytes(b"\x89PNG")
        self.captures.append(Path(chemin).name)

    def marquer_telecharge(self, fichier):
        return "0083;00000000;Safari;TEST"

    def installer(self, fichier, details, capturer=None):
        details["signature"] = "adhoc"
        return []

    outils = ta.PiloteMac.outils

    def arbre(self):
        return {"app": [], "faux": True}


class FauxMac(ta.PiloteMac):
    """PiloteMac with its commands played: Gatekeeper's dialog, which a click on « Terminé » closes."""

    def __init__(self, h, travail, refus=True, ouvre=False):
        super().__init__(travail)
        self.h = h
        self.refus_visible, self.ouvre, self.lance = refus, ouvre, False
        self.cmds, self.clics = [], []

    def attendre(self, predicat, delai, horloge=None, dormir=None, pas=1.0):
        return super().attendre(predicat, delai, self.h, self.h.dormir, pas)

    def _run(self, argv, timeout=120, env=None, creationflags=0):
        self.cmds.append([str(a) for a in argv])
        return 0, ""

    def _lancer(self, argv, env=None):
        # open waits for the answer to Gatekeeper's dialog: never run to its end before the click.
        self.lance = [str(a) for a in argv] == ["/usr/bin/open", str(self.app)]
        attente = self

        class Open:
            def wait(self, timeout):
                if attente.refus_visible and not attente.ouvre:
                    raise subprocess.TimeoutExpired("open", timeout)
                return 0

            def kill(self):
                attente.tue = True
        return Open()

    def vivante(self):
        # The process exists while macOS asks, as on a GitHub VM.
        return self.lance and (self.ouvre or self.refus_visible)

    def lire(self):
        self.h.now += 0.5
        d = [{"processus": "CoreServicesUIAgent", "boutons": ["?", "Done", "Move to Trash"],
              "textes": ["“Brain in a Box” Not Opened", "Apple could not verify “Brain in a Box” is free of malware."]}]
        return ta.Ecran(fenetre=self.lance and self.ouvre, dialogues=d if self.lance and self.refus_visible and not self.ouvre else [])

    def cliquer(self, libelle, processus=None):
        self.clics.append((processus, libelle))
        self.refus_visible = False
        return ta.CLIC_SOURIS


class MacInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="terrain-mac-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.h = Horloge()

    def test_gatekeeper_refuses_on_screen_and_the_dialog_is_captured_then_closed(self):
        p = FauxMac(self.h, self.tmp)
        details, shots = {}, []
        self.assertEqual(p.refus(details, capturer=lambda n: shots.append(n) or n), [])
        self.assertEqual(shots, ["gatekeeper"])
        self.assertEqual(p.clics, [("CoreServicesUIAgent", "Done")])
        self.assertEqual(p.clics_installation, [{"ecran": "gatekeeper", "bouton": "Done", "comment": ta.CLIC_SOURIS}])
        self.assertEqual(details["gatekeeper_dialogue"]["textes"][0], "“Brain in a Box” Not Opened")
        self.assertFalse(getattr(p, "tue", False), "open ended once the dialog was answered")

    def test_an_app_gatekeeper_lets_run_fails_and_no_dialog_is_said(self):
        with self.assertRaises(ta.ErreurPilote) as cm:
            FauxMac(self.h, self.tmp, ouvre=True).refus({})
        self.assertIn("pas de refus de Gatekeeper", str(cm.exception))
        notes = FauxMac(self.h, self.tmp, refus=False).refus({})
        self.assertIn("aucun dialogue de refus de Gatekeeper en 60 s", notes[0])

    def test_where_the_finder_mounted_the_dmg(self):
        p = ta.PiloteMac(self.tmp)
        dmg = Path(self.tmp) / "Brain-in-a-Box.dmg"
        dmg.write_bytes(b"x")
        info = {"images": [{"image-path": "/autre.dmg", "system-entities": [{"mount-point": "/Volumes/Autre"}]},
                           {"image-path": str(dmg), "system-entities": [{"content-hint": "GUID_partition_scheme"},
                                                                        {"mount-point": "/Volumes/Brain in a Box"}]}]}
        with mock.patch.object(p, "_run", lambda argv, timeout=120: (0, "noise\n" + __import__("plistlib").dumps(info).decode())):
            self.assertEqual(p.monte(dmg), Path("/Volumes/Brain in a Box"))
        with mock.patch.object(p, "_run", lambda argv, timeout=120: (0, __import__("plistlib").dumps({"images": []}).decode())):
            self.assertIsNone(p.monte(dmg))


class Noms(terrain.Etapes):
    """Etapes that remember every step name."""

    def __init__(self):
        super().__init__()
        self.vus = []

    def __call__(self, nom):
        self.vus.append(nom)
        self.nom = nom


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="terrain-app-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.h = Horloge()
        self.t = Noms()
        self.shots = Path(self.tmp) / "captures"
        self.shots.mkdir()

    def parcours(self, pilote):
        return ta.Parcours(self.t, pilote, self.shots, horloge=self.h, dormir=self.h.dormir)


class Walk(Base):
    def test_a_fresh_mac_is_clicked_through_to_the_brain(self):
        p = FauxPilote(self.h)
        pa = self.parcours(p)
        took = pa.premier_lancement({"HOME": "/h"})
        self.assertEqual(p.clics, ["Vérifier mon Mac", "Tout installer", "Passer (recherche par mots-clés)",
                                   "Plus tard", "Plus tard"])
        self.assertGreater(took, 500)
        shots = p.captures
        self.assertEqual(shots[:5], ["01-bienvenue.png", "02-diagnostic-en-cours.png", "03-diagnostic.png",
                                     "04-voyage.png", "05-etape-claude.png"])
        for name in ("etape-bun", "etape-obsidian", "etape-brain", "etape-brain-2min", "etape-brain-4min",
                     "etape-brain-6min", "connexion-installation", "connexion", "fini"):
            self.assertTrue(any(re.match(r"^\d\d-%s\.png$" % name, s) for s in shots), name)
        self.assertEqual(shots, sorted(shots))
        self.assertEqual(sorted(os.listdir(self.shots)), shots)
        self.assertEqual(self.t.vus[0], "app-lancement")
        for step in ("app-bienvenue", "app-diagnostic", "app-voyage", "app-etape-claude", "app-connexion-installation",
                     "app-etape-brain", "app-connexion", "app-fini"):
            self.assertIn(step, self.t.vus)
        for step in self.t.vus:
            self.assertRegex(step, terrain.ETAPE_RE)
        self.assertIn("Il manque Claude Code, Bun, Obsidian et ton cerveau.", self.t.details["diagnostic"]["bibo"])
        b = pa.bilan()
        brain = next(e for e in b["ecrans"] if e["ecran"] == "etape-brain")
        self.assertGreaterEqual(brain["duree_s"], 400)
        self.assertEqual(b["bibo"]["titre"], "Ton second cerveau est prêt !")

    def test_quick_reads_stand_for_full_ones_while_bibo_says_the_same(self):
        slow, quick = FauxPilote(self.h), FauxPilote(Horloge())
        quick.vite = True
        self.parcours(slow).premier_lancement({})
        pa = ta.Parcours(Noms(), quick, self.shots, horloge=quick.h, dormir=quick.h.dormir)
        pa.premier_lancement({})
        self.assertEqual(quick.clics, slow.clics)
        names = lambda shots: [re.sub(r"^\d\d-", "", n) for n in shots]
        self.assertEqual(names(quick.captures), names(slow.captures))
        self.assertLess(quick.pleines * 3, slow.pleines)
        reads = pa.bilan()["lecture_fenetre_ms"]["lectures"]
        self.assertEqual(reads[0], quick.pleines)
        self.assertGreater(reads[1], reads[0])

    def test_a_step_over_in_a_second_still_gets_its_capture(self):
        # Bun installs in about a second on a GitHub VM: a full read of the window (2 s) misses it,
        # and a step of the plan without its screen fails the walk (it passed, silently, before).
        short = {"bun": {"titre": "J’installe Bun.", "texte": "Étape 2 sur 4. C’est le moteur de ta mémoire.",
                         "apres": (1.0, "obsidian")}}
        slow, quick = FauxPilote(self.h, change=short), FauxPilote(Horloge(), change=short)
        quick.vite = True
        with self.assertRaises(terrain.Echec) as cm:
            self.parcours(slow).premier_lancement({})
        self.assertIn("1 étape(s) sur 4 jamais vue(s) à l'écran, donc sans capture (vues : etape-brain, etape-claude, "
                      "etape-obsidian)", str(cm.exception))
        self.assertFalse([n for n in slow.captures if "etape-bun" in n])
        pa = ta.Parcours(Noms(), quick, self.shots, horloge=quick.h, dormir=quick.h.dormir)
        pa.premier_lancement({})
        self.assertEqual(len([n for n in quick.captures if n.endswith("-etape-bun.png")]), 1)
        self.assertEqual(quick.clics, slow.clics)

    def test_every_step_of_the_plan_is_counted(self):
        vus = lambda *etapes: {cle: ta.Ecran(True, "x", texte) for cle, texte in etapes}
        self.assertIsNone(ta.etapes_manquees(vus(("etape-claude", "Étape 1 sur 2. L’outil."), ("etape-brain", "Étape 2 sur 2."))))
        # Apple's tools have no « Étape i sur n » of their own, and count all the same.
        self.assertIsNone(ta.etapes_manquees(vus(("etape-claude", "Étape 1 sur 3."), ("etape-clt", "Je t’attends."),
                                                 ("etape-brain", "Étape 3 sur 3."))))
        self.assertIn("1 étape(s) sur 3", ta.etapes_manquees(vus(("etape-claude", "Étape 1 sur 3."), ("etape-brain", "Étape 3 sur 3."))))
        self.assertIn("aucun écran d'étape", ta.etapes_manquees(vus(("diagnostic", "J’ai fait le tour."))))
        self.assertIsNone(ta.etapes_manquees(vus(("etape-brain", "C’est l’étape la plus longue."))))

    def test_buttons_late_in_the_tree_are_read_again_at_once(self):
        p = FauxPilote(self.h)
        p.vite = True
        p.ecrans["login"] = dict(p.ecrans["login"], boutons_apres=3)
        pa = self.parcours(p)
        pa.premier_lancement({})
        shown = next(e for e in pa.ecrans if e["ecran"] == "connexion")
        click = next(c for c in pa.clics if c["ecran"] == "connexion")
        self.assertEqual(click["bouton"], "Plus tard")
        self.assertLessEqual(click["a_s"] - shown["debut_s"], 5, "not at the next periodic full read")
        self.assertEqual([c["bouton"] for c in pa.clics], ["Vérifier mon Mac", "Tout installer",
                                                           "Passer (recherche par mots-clés)", "Plus tard", "Plus tard"])

    def test_opened_again_it_says_all_is_installed(self):
        p = FauxPilote(self.h)
        pa = self.parcours(p)
        pa.premier_lancement({})
        p.fermer()
        del p.clics[:]
        vus = pa.relance({})
        self.assertIn("deja-installe", vus)
        self.assertEqual(p.clics, ["Vérifier mon Mac", "Continuer", "Plus tard"])
        self.assertIn("app-relance-deja-installe", self.t.vus)
        self.assertTrue(any(s.endswith("-relance-deja-installe.png") for s in p.captures))

    def test_opened_again_and_installing_again_fails(self):
        p = FauxPilote(self.h)
        pa = self.parcours(p)
        pa.premier_lancement({})
        p.fermer()
        p.fait = False      # the app finds something missing again
        with self.assertRaises(terrain.Echec) as cm:
            pa.relance({})
        self.assertIn("au deuxième lancement, l'app veut encore installer", str(cm.exception))
        self.assertEqual(self.t.nom, "app-relance-diagnostic")

    def test_a_failed_step_names_it_and_quotes_bibo(self):
        p = FauxPilote(self.h, change={
            "brain": {"titre": "Je prépare ton cerveau.", "apres": (90, "oups")},
            "oups": {"titre": "Oups, ça a coincé.",
                     "texte": "L’installation du cerveau s’est arrêtée (code 1) sur" + NB + ": « bun install ».",
                     "boutons": {"Copier le rapport": None, "Réessayer": "brain"}}})
        pa = self.parcours(p)
        with self.assertRaises(terrain.Echec) as cm:
            pa.premier_lancement({})
        self.assertEqual(self.t.nom, "app-etape-brain")
        reason = pa.raison(str(cm.exception))
        self.assertIn("l'installation a échoué dans l'app", reason)
        self.assertIn("Bibo : « Oups, ça a coincé. L'installation du cerveau s'est arrêtée (code 1) sur : « bun install ».",
                      reason)
        self.assertTrue(p.captures[-1].endswith("-echec.png"))
        self.assertNotIn("Réessayer", p.clics)
        pa.sur_echec()
        self.assertTrue(any(n.endswith("-echec-arbre.json") for n in os.listdir(self.shots)))

    def test_a_click_that_changes_nothing_fails(self):
        p = FauxPilote(self.h, change={"bienvenue": {"titre": "Salut, moi c’est Bibo !", "boutons": {"Vérifier mon Mac": None}}})
        with self.assertRaises(terrain.Echec) as cm:
            self.parcours(p).premier_lancement({})
        self.assertIn("le clic sur « Vérifier mon Mac » ne change rien (3 essais)", str(cm.exception))
        self.assertEqual(p.clics, ["Vérifier mon Mac"] * 3)
        self.assertEqual(self.t.nom, "app-bienvenue")

    def test_a_missing_button_fails(self):
        p = FauxPilote(self.h, change={"voyage": {"titre": "Une option avant de commencer", "boutons": {"Ouvrir Voyage AI": None}}})
        with self.assertRaises(terrain.Echec) as cm:
            self.parcours(p).premier_lancement({})
        self.assertIn("l'écran « voyage » n'offre pas le bouton « Passer (recherche par mots-clés) »", str(cm.exception))

    def test_a_machine_with_everything_is_not_a_fresh_vm(self):
        p = FauxPilote(self.h)
        p.fait = True
        with self.assertRaises(terrain.Echec) as cm:
            self.parcours(p).premier_lancement({})
        self.assertIn("la VM n'est pas neuve", str(cm.exception))

    def test_a_step_that_never_ends_is_cut_with_a_capture_every_two_minutes(self):
        p = FauxPilote(self.h, change={"brain": {"titre": "Je prépare ton cerveau.", "apres": (10 ** 9, "installe")}})
        pa = self.parcours(p)
        with self.assertRaises(terrain.Echec) as cm:
            pa.premier_lancement({})
        self.assertIn("toujours sur l'écran « etape-brain » après 75 min", str(cm.exception))
        waits = [s for s in p.captures if re.match(r"^\d\d-etape-brain-\d+min\.png$", s)]
        self.assertGreaterEqual(len(waits), 35)
        self.assertLessEqual(self.h.now, ta.DELAI_PREMIER + 600)

    def test_the_walk_ends_by_itself_before_its_job_step_is_killed(self):
        p = FauxPilote(self.h, change={"brain": {"titre": "Je prépare ton cerveau.", "apres": (10 ** 9, "installe")}})
        pa = ta.Parcours(self.t, p, self.shots, horloge=self.h, dormir=self.h.dormir, echeance=20 * 60)
        with self.assertRaises(terrain.Echec) as cm:
            pa.premier_lancement({})
        self.assertRegex(str(cm.exception), r"toujours sur l'écran « etape-brain » après 1\d min \(fin du temps du scénario\)")
        self.assertLess(self.h.now, 21 * 60)

    def test_no_window_fails_with_a_capture(self):
        p = FauxPilote(self.h)
        p.ouvrir = lambda env: None
        pa = self.parcours(p)
        with self.assertRaises(terrain.Echec) as cm:
            pa.premier_lancement({})
        self.assertIn("aucune fenêtre", str(cm.exception))
        self.assertEqual(p.captures, ["01-sans-fenetre.png"])
        self.assertIn("(Bibo n'a rien dit à l'écran)", pa.raison("x"))


class Dialogs(Base):
    def test_the_system_asking_for_the_app_is_accepted_once_and_captured(self):
        p = FauxPilote(self.h)
        p.dialogue = {"processus": "UserNotificationCenter", "part_sur": "Allow", "boutons": ["Don't Allow", "Allow"],
                      "textes": ["“Brain in a Box” would like to access files in your Documents folder."]}
        pa = self.parcours(p)
        pa.premier_lancement({})
        self.assertEqual([c for c in p.clics if isinstance(c, tuple)], [("UserNotificationCenter", "Allow")])
        self.assertEqual(p.captures[:2], ["01-dialogue-usernotificationcenter.png", "02-bienvenue.png"])
        d = pa.bilan()["dialogues_systeme"]
        self.assertEqual((len(d), d[0]["reponse"], d[0]["essais"]), (1, "Allow", 1))

    def test_a_dialog_for_someone_else_is_never_clicked(self):
        p = FauxPilote(self.h, change={"brain": {"titre": "Je prépare ton cerveau.", "apres": (100, "installe")}})
        p.dialogue = {"processus": "CoreServicesUIAgent", "boutons": ["OK", "Move to Trash"],
                      "textes": ["“Autre chose” cannot be opened."]}
        pa = self.parcours(p)
        pa.premier_lancement({})
        self.assertFalse([c for c in p.clics if isinstance(c, tuple)])
        self.assertEqual(pa.bilan()["dialogues_systeme"][0]["reponse"], None)
        self.assertIn("dialogue système ouvert (CoreServicesUIAgent) : « “Autre chose” cannot be opened. »", pa.raison("x"))

    def test_a_dialog_that_stays_is_tried_three_times_only(self):
        p = FauxPilote(self.h)
        p.dialogue = {"processus": "UserNotificationCenter", "part_sur": None, "boutons": ["Allow"],
                      "textes": ["“Brain in a Box” would like to access files in your Documents folder."]}
        self.parcours(p).premier_lancement({})
        self.assertEqual(len([c for c in p.clics if isinstance(c, tuple)]), ta.CLIC_ESSAIS)

    def test_only_the_request_the_app_announces(self):
        self.assertEqual(ta.bouton_autoriser({"textes": ["“Brain in a Box” would like to access files in your Documents folder."],
                                              "boutons": ["Don’t Allow", "Allow"]}), "Allow")
        self.assertEqual(ta.bouton_autoriser({"textes": ["« brain-in-a-box » souhaite accéder aux fichiers de ton dossier Documents."],
                                              "boutons": ["Autoriser"]}), "Autoriser")
        self.assertIsNone(ta.bouton_autoriser({"textes": ["“Terminal” would like to control"], "boutons": ["Allow"]}))
        self.assertIsNone(ta.bouton_autoriser({"textes": ["“Brain in a Box” is damaged"], "boutons": ["Move to Trash"]}))
        # Anything else asked on the app's behalf is never accepted, and fails the night with its words.
        for d in ({"processus": "UserNotificationCenter", "boutons": ["Don’t Allow", "OK"],
                   "textes": ["« Brain in a Box » souhaite accéder à vos contacts."]},
                  {"processus": "UserNotificationCenter", "boutons": ["Don’t Allow", "Allow"],
                   "textes": ["“Brain in a Box” wants access to control “System Events”."]},
                  {"processus": "SecurityAgent", "boutons": ["Annuler", "OK"],
                   "textes": ["Brain in a Box tente d'installer un outil auxiliaire. Documents"]}):
            with self.subTest(d=d["textes"][0]):
                self.assertIsNone(ta.bouton_autoriser(d))
                what = ta.reponse_dialogue(d)
                self.assertEqual(what[0], "echec")
                self.assertIn("demande système inattendue au nom de l'app (%s)" % d["processus"], what[1])
        self.assertIsNone(ta.reponse_dialogue({"processus": "CoreServicesUIAgent", "boutons": ["OK"], "textes": ["“Autre” x"]}))

    def test_a_request_for_something_else_fails_and_is_never_answered(self):
        p = FauxPilote(self.h)
        p.dialogue = {"processus": "UserNotificationCenter", "part_sur": "OK", "boutons": ["Don’t Allow", "OK"],
                      "textes": ["“Brain in a Box” would like to access your contacts."]}
        with self.assertRaises(terrain.Echec) as cm:
            self.parcours(p).premier_lancement({})
        self.assertIn("demande système inattendue au nom de l'app (UserNotificationCenter) : « “Brain in a Box” would "
                      "like to access your contacts. »", str(cm.exception))
        self.assertFalse([c for c in p.clics if isinstance(c, tuple)])
        self.assertEqual(p.captures, ["01-dialogue-usernotificationcenter.png"])

    def test_apple_tools_window_is_clicked_through_with_a_capture_of_each_state(self):
        fini = {"processus": ta.OUTILS_APPLE, "textes": ["The software was installed."], "boutons": ["Done"], "suite": {"Done": None}}
        progres = {"processus": ta.OUTILS_APPLE, "textes": ["Downloading the software", "About 7 minutes remaining"],
                   "boutons": ["Cancel"], "apres": (300, fini)}
        licence = {"processus": ta.OUTILS_APPLE, "textes": ["Command Line Tools License Agreement"],
                   "boutons": ["Disagree", "Agree"], "suite": {"Agree": progres}}
        fenetre = {"processus": ta.OUTILS_APPLE, "boutons": ["Get Xcode", "Not Now", "Install"], "suite": {"Install": licence},
                   "textes": ["The “xcode-select” command requires the command line developer tools."]}
        p = FauxPilote(self.h, change={"claude": dict(neuf(lambda: False)["claude"], texte="Étape 1 sur 5. L’outil.")})
        p.ecrans["login-inline"]["boutons"]["Plus tard"] = "clt"
        p.ecrans["clt"] = {"titre": "Une fenêtre Apple va s’ouvrir" + NB + ": clique sur Installer.",
                           "texte": "Je t’attends. Tu ne la vois pas" + NB + "? Regarde dans le Dock.", "sans_dialogue": "bun"}
        p.ecrans["bun"] = dict(p.ecrans["bun"], texte="Étape 3 sur 5. C’est le moteur de ta mémoire.")
        p.ecrans["brain"] = dict(p.ecrans["brain"], texte="Étape 5 sur 5.")
        real = p.aller
        p.aller = lambda etat: (real(etat), p.montrer(fenetre) if p.etat == "clt" else None)
        pa = self.parcours(p)
        pa.premier_lancement({})
        self.assertEqual([c for c in p.clics if isinstance(c, tuple)],
                         [(ta.OUTILS_APPLE, "Install"), (ta.OUTILS_APPLE, "Agree"), (ta.OUTILS_APPLE, "Done")])
        shots = [c for c in p.captures if "install-command-line" in c]
        self.assertEqual(len(shots), 4, p.captures)
        self.assertIn("app-etape-clt", self.t.vus)
        self.assertEqual([c["comment"] for c in pa.clics if c["ecran"].startswith("dialogue-")], [ta.CLIC_SOURIS] * 3)

    def test_apple_saying_it_cannot_install_fails_with_its_words(self):
        d = {"processus": ta.OUTILS_APPLE, "boutons": ["Done"],
             "textes": ["Can’t install the software because it is not currently available from the Software Update server."]}
        what = ta.reponse_dialogue(d)
        self.assertEqual(what[0], "echec")
        self.assertIn("la fenêtre d'Apple pour ses outils dit : « Can't install the software", ta.norm(what[1]))
        self.assertIsNone(ta.reponse_dialogue(dict(d, boutons=["Cancel"], textes=["Finding software"])))


def tree(subrole=True, texte=True):
    """What System Events gives for the welcome screen (shape of a real dump on a GitHub VM)."""
    st = lambda s: {"r": "AXStaticText", "s": "", "n": s, "v": s, "k": []}
    group = lambda *k, s="": {"r": "AXGroup", "s": s, "n": "", "v": "", "k": list(k)}
    message = group(group(st("Salut, moi c’est Bibo" + NB + "!")),
                    *([group(st("Je vais installer ton second cerveau pour Claude."))] if texte else []),
                    s="AXApplicationStatus" if subrole else "")
    hero = group(message, s="AXLandmarkBanner")
    main = group({"r": "AXList", "s": "", "n": "", "v": "", "k": [group(st("1"), st("Je regarde ce qui manque sur ton Mac."))]},
                 group(st("Il te faut un abonnement Claude Pro ou Max.")))
    footer = group({"r": "AXButton", "s": "", "n": "Vérifier mon Mac", "v": "", "d": "", "e": True, "k": []},
                   {"r": "AXButton", "s": "", "n": "Plus tard", "v": "", "d": "", "e": False, "k": []})
    web = {"r": "AXWebArea", "s": "", "n": "", "v": "", "k": [hero, main, footer]}
    window = {"r": "AXWindow", "s": "AXStandardWindow", "n": "Brain in a Box", "v": "", "k": [
        group(group({"r": "AXScrollArea", "s": "", "n": "", "v": "", "k": [web]})),
        {"r": "AXButton", "s": "AXCloseButton", "n": "", "v": "", "d": "close button", "e": True, "k": []}]}
    return [{"titre": "Brain in a Box", "arbre": window}]


class ReadingTheWindow(unittest.TestCase):
    def test_bibo_speaks_in_the_status_region(self):
        e = ta.ecran_depuis_arbre(tree())
        self.assertEqual((e.fenetre, e.lecture), (True, "statut"))
        self.assertEqual(e.titre, "Salut, moi c'est Bibo !")
        self.assertEqual(e.texte, "Je vais installer ton second cerveau pour Claude.")
        self.assertEqual(e.boutons, [("Vérifier mon Mac", True), ("Plus tard", False)])
        self.assertIs(e.bouton("Vérifier mon Mac"), True)
        self.assertIs(e.bouton("Plus tard"), False)
        self.assertIsNone(e.bouton("close button"))
        self.assertIn("Il te faut un abonnement Claude Pro ou Max.", e.textes)
        self.assertEqual(ta.cle_ecran(e), ("bienvenue", None))
        # Where a quick read finds Bibo again: window 0, group, group, scroll area, page, header, region.
        self.assertEqual(e.statut, (0, (0, 0, 0, 0, 0, 0)))
        self.assertIsNone(ta.ecran_depuis_arbre(tree(subrole=False)).statut)
        self.assertEqual(ta.dits_rapides(["Salut, moi c’est Bibo" + NB + "!", " ", "Je vais installer."]),
                         ("Salut, moi c'est Bibo !", "Je vais installer."))
        self.assertIsNone(ta.dits_rapides([]))

    def test_without_the_status_role_the_first_texts_stand_in(self):
        e = ta.ecran_depuis_arbre(tree(subrole=False))
        self.assertEqual((e.lecture, e.titre, e.texte), ("repli", "Salut, moi c'est Bibo !",
                                                          "Je vais installer ton second cerveau pour Claude."))
        e = ta.ecran_depuis_arbre(tree(subrole=False, texte=False))
        self.assertEqual((e.titre, e.texte), ("Salut, moi c'est Bibo !", ""))

    def test_no_window_no_page(self):
        self.assertEqual(ta.cle_ecran(ta.ecran_depuis_arbre([])), ("sans-fenetre", None))
        bare = [{"titre": "Brain in a Box", "arbre": {"r": "AXWindow", "k": []}}]
        self.assertEqual(ta.cle_ecran(ta.ecran_depuis_arbre(bare)), ("chargement", None))

    def test_a_dialog_as_data(self):
        d = ta.dialogue_depuis_arbre("UserNotificationCenter", {"r": "AXWindow", "k": [
            {"r": "AXStaticText", "n": "", "v": "“Brain in a Box” would like to access files in your Documents folder.", "k": []},
            {"r": "AXButton", "n": "Don’t Allow", "k": []}, {"r": "AXButton", "n": "Allow", "k": []}]})
        self.assertEqual(d["boutons"], ["Don't Allow", "Allow"])
        self.assertEqual(ta.bouton_autoriser(d), "Allow")

    def test_the_screens_the_robot_knows(self):
        for titre, cle in (("Je regarde ton PC…", "diagnostic-en-cours"), ("J’installe Bun.", "etape-bun"),
                           ("Une fenêtre Apple va s’ouvrir" + NB + ": clique sur Installer.", "etape-clt"),
                           ("Dernière étape" + NB + ": ton compte Claude", "connexion"),
                           ("Je n’ai rien fait.", "inconnu"), ("Ouvre l’app Brain in a Box.", "hors-app")):
            self.assertEqual(ta.cle_ecran(ta.Ecran(True, titre))[0], cle, titre)
        repair = ta.Ecran(True, "Je n’ai rien fait.", boutons=[("Copier le rapport", True), ("Réessayer", True)])
        self.assertEqual(ta.cle_ecran(repair)[0], "echec")

    def test_every_word_the_robot_looks_for_is_in_the_app(self):
        # The robot reads the app's own words: a reworded screen must change terrain_app.py too.
        with open(os.path.join(REPO, "app", "src", "app.js"), encoding="utf-8") as f:
            app = ta.norm(f.read().replace("${OS.machine}", "Mac"))
        words = [start for _, start in ta.ECRANS] + list(ta.ETAPES) + list(ta.BOUTONS_ECHEC) + [
            "Vérifier mon Mac", "Tout installer", "Passer (recherche par mots-clés)", "Plus tard", "Continuer",
            "Personnaliser mon cerveau (15 min)", "Ouvrir mon cerveau dans Obsidian"]
        for w in words:
            self.assertIn(ta.norm(w), app, w)


class Names(unittest.TestCase):
    def test_capture_names_sort_and_stay_plain(self):
        self.assertEqual(ta.nom_capture(3, "etape-brain-2min"), "03-etape-brain-2min.png")
        self.assertEqual(ta.nom_capture(12, "relance-déjà installé !"), "12-relance-deja-installe.png")
        self.assertEqual(ta.nom_capture(7, "dialogue-UserNotificationCenter"), "07-dialogue-usernotificationcenter.png")
        self.assertEqual(ta.nom_capture(1, "«»"), "01-ecran.png")

    def test_master_downloads_a_branch_builds(self):
        self.assertEqual(ta.choisir_source("master"), "release")
        for ref in ("feat/terrain-app", "v0.2.0", "a" * 40, "", "master2"):
            self.assertEqual(ta.choisir_source(ref), "build", ref)
        p = subprocess.run([sys.executable, "-I", os.path.join(HERE, "terrain_app.py"), "source", "--ref", "master"],
                           capture_output=True)
        self.assertEqual(p.stdout.decode().strip(), "source=release")

    def test_the_app_gets_a_users_environment_never_a_token(self):
        secrets = {"GH_TOKEN": "ghs_x", "GITHUB_TOKEN": "ghs_y", "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-z", "CI": "true",
                   "ACTIONS_RUNTIME_TOKEN": "t", "PATH": "/opt/homebrew/bin:/usr/bin"}
        with mock.patch.dict(os.environ, secrets):
            env = ta.env_utilisateur("/Users/runner", {"BIAB_ARCHIVE": "/tmp/k.zip"})
        for k in secrets:
            self.assertTrue(k == "PATH" or k not in env, k)
        self.assertEqual(env["PATH"], "/usr/bin:/bin:/usr/sbin:/sbin")
        self.assertEqual((env["HOME"], env["BIAB_ARCHIVE"]), ("/Users/runner", "/tmp/k.zip"))


def release(tag, created, prerelease=True, draft=False, names=("Brain-in-a-Box_%s_macos-universal.dmg", "SHA256SUMS")):
    label = tag[len("app-v"):] if tag.startswith("app-v") else tag
    return {"tag_name": tag, "created_at": created, "prerelease": prerelease, "draft": draft, "target_commitish": "c" * 40,
            "assets": [{"name": n % label if "%s" in n else n, "size": 0, "digest": "",
                        "browser_download_url": "https://github.com/%s/releases/download/%s/%s" % (
                            ta.REPO_SLUG, tag, n % label if "%s" in n else n)} for n in names]}


class Release(Base):
    def test_the_newest_preview(self):
        rels = [release("app-v0.1.0-preview.4", "2026-10-14T17:31:20Z"),
                release("app-v0.1.0-preview.5", "2026-10-15T09:00:00Z", draft=True),
                release("v0.3.0", "2026-10-16T00:00:00Z", prerelease=False),
                release("app-v0.1.0-preview.6-rc", "2026-10-16T00:00:00Z"),
                release("app-v0.1.0-preview.3", "2026-10-13T00:00:00Z"), "garbage"]
        self.assertEqual(ta.derniere_preview(rels)["tag_name"], "app-v0.1.0-preview.4")
        self.assertIsNone(ta.derniere_preview([]))

    def test_only_this_repositorys_files(self):
        rel = release("app-v0.1.0-preview.4", "x")
        name, url, _, _ = ta.asset(rel, "_macos-universal.dmg", "app-v0.1.0-preview.4")
        self.assertEqual(name, "Brain-in-a-Box_0.1.0-preview.4_macos-universal.dmg")
        rel["assets"][0]["browser_download_url"] = "https://evil.example/x.dmg"
        self.assertIsNone(ta.asset(rel, "_macos-universal.dmg", "app-v0.1.0-preview.4"))
        self.assertIsNone(ta.asset(rel, "_windows-x64-setup.exe", "app-v0.1.0-preview.4"))
        with self.assertRaises(ValueError):
            ta._get("https://github.com/x", token="ghs_x")

    def test_sha256sums(self):
        sums = "%s  a.dmg\n%s *b.exe\nnot a line\n" % ("1" * 64, "2" * 64)
        self.assertEqual(ta.somme_attendue(sums, "a.dmg"), "1" * 64)
        self.assertEqual(ta.somme_attendue(sums, "b.exe"), "2" * 64)
        self.assertIsNone(ta.somme_attendue(sums, "c.dmg"))
        self.assertIsNone(ta.somme_attendue("XYZ  a.dmg", "a.dmg"))

    def download(self, content, sums_for=None, attestation=0):
        name = "Brain-in-a-Box_0.1.0-preview.4_macos-universal.dmg"
        good = hashlib.sha256(sums_for if sums_for is not None else content).hexdigest()
        files = {"SHA256SUMS": ("%s  %s\n" % (good, name)).encode(), name: content}
        rel = release("app-v0.1.0-preview.4", "2026-10-14T17:31:20Z")
        calls = []

        def get(url, token=None, timeout=60):
            calls.append((url, token))
            return io.BytesIO(json.dumps([rel]).encode())

        def fetch(url, dest, timeout=600):
            calls.append((url, None))
            Path(dest).write_bytes(files[url.rsplit("/", 1)[1]])

        def gh(argv, **kw):
            calls.append((argv, kw["env"].get("GH_TOKEN")))
            return subprocess.CompletedProcess(argv, attestation, b"verification failed" if attestation else b"ok")

        details = {}
        with mock.patch.object(ta, "_get", get), mock.patch.object(ta, "telecharger", fetch), \
                mock.patch.object(ta.shutil, "which", lambda n: "/usr/bin/gh"), mock.patch.object(ta.subprocess, "run", gh):
            dest = ta.depuis_release(self.t, FauxPilote(self.h), self.tmp, "ghs_token", details)
        return dest, details, calls

    def test_a_download_is_checked_like_a_careful_user_does(self):
        dest, details, calls = self.download(b"dmg bytes")
        self.assertEqual(dest.read_bytes(), b"dmg bytes")
        self.assertEqual((details["release"], details["attestation"]),
                         ("app-v0.1.0-preview.4", "vérifiée (release.yml, refs/heads/master)"))
        gh = [c for c in calls if isinstance(c[0], list)][0]
        self.assertEqual(gh[0][1:3], ["attestation", "verify"])
        self.assertIn("--signer-workflow", gh[0])
        self.assertEqual(gh[0][gh[0].index("--source-ref") + 1], "refs/heads/master")
        # The token goes to the API and to gh, never to a file download.
        self.assertEqual({tok for url, tok in calls if isinstance(url, str) and not url.startswith("https://api.github.com/")},
                         {None})
        self.assertEqual(self.t.vus, ["app-telechargement", "app-verification"])

    def test_a_file_that_does_not_match_its_sum_or_its_attestation_fails(self):
        with self.assertRaises(terrain.Echec) as cm:
            self.download(b"tampered", sums_for=b"dmg bytes")
        self.assertIn("ne correspond pas à SHA256SUMS", str(cm.exception))
        self.assertEqual(self.t.nom, "app-verification")
        with self.assertRaises(terrain.Echec) as cm:
            self.download(b"dmg bytes", attestation=1)
        self.assertIn("l'attestation", str(cm.exception))


    def retard(self, statut, quand, commits=True):
        def api(path, token):
            self.assertEqual(token, "ghs_token")
            if path.startswith("commits?"):
                self.assertIn("sha=" + "a" * 40, path)
                if not commits or "path=app&" not in path:
                    return []
                return [{"sha": "d" * 40, "commit": {"committer": {"date": quand}}}]
            self.assertEqual(path, "compare/%s...app-v0.1.0-preview.7" % ("d" * 40))
            return {"status": statut}
        rel = release("app-v0.1.0-preview.7", "2026-10-14T00:00:00Z")
        with mock.patch.object(ta, "_api", api):
            return ta.retard_preview(self.t, "ghs_token", rel, "a" * 40,
                                     maintenant=ta.datetime(2026, 10, 15, 1, 17, tzinfo=ta.timezone.utc))

    def test_the_preview_holds_masters_last_change_to_the_app(self):
        self.assertIsNone(self.retard("ahead", "2026-10-14T20:00:00Z"))
        self.assertIsNone(self.retard("identical", "2026-10-14T20:00:00Z"))
        self.assertIsNone(self.retard("behind", "2026-10-14T20:00:00Z", commits=False))
        # Published within the day: release.yml may still be at it.
        late = self.retard("behind", "2026-10-14T22:00:00Z")
        self.assertIn("la préversion app-v0.1.0-preview.7 n'a pas le dernier changement de l'app sur master (ddddddd, il y a 3 h)", late)
        # A day later, the night tests an older app than master's: a failure.
        with self.assertRaises(terrain.Echec) as cm:
            self.retard("behind", "2026-10-13T20:00:00Z")
        self.assertIn("il y a 29 h) : release.yml ne l'a pas publié", str(cm.exception))
        with self.assertRaises(terrain.Echec):
            self.retard("diverged", "2026-10-13T20:00:00Z")


# --------------------------------------------------------------------------- Windows

def uia(t, n="", k=(), **kw):
    """A node as pilote_windows.cs dumps it (t control type, lt localized type, n name, e enabled...)."""
    node = {"t": t, "lt": kw.pop("lt", t.lower()), "n": n, "e": kw.pop("e", True), "k": list(k)}
    node.update(kw)
    return node


def page_uia(statut="StatusBar", p_texte=False, boutons=(("Vérifier mon PC", True), ("Plus tard", False))):
    """The welcome screen as UI Automation gives WebView2's page (Chromium's roles), in the app's window."""
    texte = lambda s: uia("Text", s) if not p_texte else uia("Text", "", [uia("Text", s)])
    paragraphe = lambda s: uia("Group", "", [texte(s)], lt="paragraph") if not p_texte else texte(s)
    region = {"StatusBar": lambda k: uia("StatusBar", "", k, lt="status bar"),
              "lt": lambda k: uia("Group", "", k, lt="status"),
              "live": lambda k: uia("Group", "", k, lt="group", l=1),
              None: lambda k: uia("Group", "", k, lt="group")}[statut]
    message = region([paragraphe("Salut, moi c’est Bibo" + NB + "!"), paragraphe("Je vais installer ton second cerveau.")])
    hero = uia("Group", "", [uia("Image", "Bibo"), message], lt="banner")
    main = uia("Group", "", [uia("List", "", [uia("ListItem", "", [uia("Text", "Je regarde ce qui manque sur ton PC.")])]),
                             uia("Text", "Il te faut un abonnement Claude Pro ou Max.", l=1)], lt="main")
    footer = uia("Group", "", [uia("Button", b, [uia("Text", b)], e=on) for b, on in boutons], lt="content info")
    doc = uia("Document", "Brain in a Box", [uia("Group", "", [hero, main, footer])], a="RootWebArea")
    # WebView2's browser frame, as a GitHub VM showed it: its own buttons, and empty views that are
    # documents too, before the page.
    frame = uia("Pane", "", [
        uia("Pane", "", [uia("Button", "Minimize", c="EdgeWindowsCaptionButton"), uia("Button", "Close", c="EdgeWindowsCaptionButton")],
            c="BrowserCaptionButtonContainer"),
        uia("Text", "", lt="alert", c="AXVirtualView", l=2),
        uia("Document", "", [uia("Pane", "", c="NativeViewHost")], c="WebView", a="view_1059"),
        uia("Pane", "Chrome Legacy Window", [doc], c="Chrome_RenderWidgetHostHWND"),
        uia("Document", "", [uia("Pane", "", c="NativeViewHost")], c="ContentsWebView")], c="EmbeddedBrowserFrameView")
    return uia("Window", "Brain in a Box", [
        uia("Pane", "", [uia("Pane", "Brain in a Box", [frame], c="Chrome_WidgetWin_1")], c="WRY_WEBVIEW"),
        uia("TitleBar", "", [uia("Button", "Minimize"), uia("Button", "Close")])], c="Tauri Window")


class WindowsReading(unittest.TestCase):
    def ecran(self, arbre):
        return ta.ecran_depuis_arbre([{"titre": "Brain in a Box", "arbre": ta.arbre_uia(arbre)}])

    def test_bibo_in_the_status_region_whatever_ui_automation_calls_it(self):
        for statut in ("StatusBar", "lt", "live"):
            e = self.ecran(page_uia(statut))
            self.assertEqual((e.lecture, e.titre, e.texte), ("statut", "Salut, moi c'est Bibo !",
                                                             "Je vais installer ton second cerveau."), statut)
            self.assertEqual(e.boutons, [("Vérifier mon PC", True), ("Plus tard", False)], statut)
            self.assertEqual(ta.cle_ecran(e), ("bienvenue", None))
            self.assertIn("Il te faut un abonnement Claude Pro ou Max.", e.textes)
            # A button's own text is its label, never one more text of the page.
            self.assertEqual(e.textes.count("Vérifier mon PC"), 0)
        e = self.ecran(page_uia(None))
        self.assertEqual((e.lecture, e.titre, e.texte), ("repli", "Salut, moi c'est Bibo !", "Je vais installer ton second cerveau."))

    def test_a_paragraph_without_a_name_is_read_through(self):
        e = self.ecran(page_uia("StatusBar", p_texte=True))
        self.assertEqual((e.titre, e.texte), ("Salut, moi c'est Bibo !", "Je vais installer ton second cerveau."))

    def test_the_window_frame_is_not_the_page(self):
        e = self.ecran(page_uia())
        self.assertIsNone(e.bouton("Close"))
        self.assertIsNone(e.bouton("Minimize"))
        loading = uia("Window", "Brain in a Box", [uia("TitleBar", "", [uia("Button", "Close")]), uia("Pane", "")])
        self.assertEqual(ta.cle_ecran(self.ecran(loading)), ("chargement", None))


def nsis(page, lancer=1):
    """The installer's pages (NSIS, French, tauri-bundler's template) as UI Automation showed them on a
    GitHub VM: the wizard's buttons, then the page's own dialog, then the title bar."""
    b = lambda n, on=True: uia("Button", n, e=on, c="Button")
    t = lambda n: uia("Text", n, c="Static")
    boutons, page_ = {
        "bienvenue": ([b("Suivant >"), b("Annuler")],
                      [t("Bienvenue dans le programme d'installation de Brain in a Box"), t("Cliquez sur Suivant pour continuer.")]),
        "dossier": ([b("< Précédent"), b("Suivant >"), b("Annuler")],
                    [t("Choisissez le dossier d'installation"), uia("Edit", ""), b("Parcourir..."), t("Espace requis : 11.4 Mo")]),
        "installation": ([b("< Précédent", False), b("Suivant >", False), b("Annuler", False)],
                         [t("Installation en cours"), b("Plus d'infos"), uia("ProgressBar", "")]),
        "installe": ([b("< Précédent", False), b("Suivant >"), b("Annuler", False)],
                     [t("Installation terminée"), b("Plus d'infos"), t("Terminé")]),
        "fin": ([b("< Précédent", False), b("Fermer"), b("Annuler", False)],
                [uia("Image", ""), t("Fin de l'installation de Brain in a Box"),
                 uia("CheckBox", "Lancer Brain in a Box", x=lancer, c="Button"),
                 uia("CheckBox", "Créer un raccourci sur le bureau.", x=1, c="Button")]),
        "message": ([b("OK")], [t("Erreur : le téléchargement de WebView2 a échoué")]),
    }[page]
    frame = uia("TitleBar", "Installation de Brain in a Box ", [
        uia("MenuBar", "System Menu Bar", [uia("MenuItem", "System")]), uia("Button", "Minimize"), uia("Button", "Close")])
    if page == "message":
        window = uia("Window", "Installation de Brain in a Box ", page_ + boutons + [frame], lt="Dialog")
    else:
        window = uia("Window", "Installation de Brain in a Box ", boutons + [t("Nullsoft Install System v3.11"),
                                                                             uia("Pane", "", page_, lt="Dialog"), frame], lt="Dialog")
    return [{"h": 7, "pid": 41, "titre": "Installation de Brain in a Box ", "classe": "#32770", "arbre": window}]


class FauxSetup:
    pid = 41

    def __init__(self, h):
        self.h, self.fini, self.code = h, None, 0

    def poll(self):
        return self.code if self.fini is not None and self.h.now >= self.fini else None

    def kill(self):
        self.fini = self.h.now


class FauxWindows(ta.PiloteWindows):
    """PiloteWindows with its helper played: the installer's pages, on a fake clock."""

    def __init__(self, h, travail, pages=None):
        super().__init__(travail, outil="pilote-windows.exe", horloge=h, dormir=h.dormir)
        self.h = h
        self.setup = FauxSetup(h)
        self.page, self.lancer, self.depuis, self.duree = "bienvenue", 1, 0.0, 40
        self.suite = pages if pages is not None else {
            ("bienvenue", "Suivant >"): "dossier", ("dossier", "Suivant >"): "installation", ("installe", "Suivant >"): "fin"}
        self.appels = []
        self.quitte = 1     # seconds the installer takes to quit once « Fermer » is clicked

    def aller(self, page):
        self.page, self.depuis = page, self.h.now

    def _outil(self, args, timeout=120):
        args = [str(a) for a in args]
        self.appels.append(args)
        self.h.now += 0.3
        if self.page == "installation" and self.h.now - self.depuis >= self.duree:
            self.aller("installe")
        if args[0] == "lire":
            gone = self.setup.poll() is not None
            return {"ok": True, "fenetres": [] if gone else nsis(self.page, self.lancer), "devant": None}
        if args[0] == "devant":
            return {"ok": True, "devant": True}
        if args[0] == "ranger":
            return {"ok": True, "fenetres": [{"titre": "C:\\ProgramData\\GitHub\\HostedComputeAgent", "classe": "CASCADIA_HOSTING_WINDOW_CLASS"}]}
        if args[0] == "cocher":
            self.lancer = int(args[3])
            return {"ok": True, "coche": bool(self.lancer)}
        if args[0] == "cliquer":
            if self.page == "fin" and args[2] == "Fermer" and self.setup.fini is None:
                self.setup.fini = self.h.now + self.quitte
            nxt = self.suite.get((self.page, args[2]))
            if nxt:
                self.aller(nxt)
            return {"ok": True, "comment": "Invoke"}
        raise AssertionError(args)


class WindowsInstaller(Base):
    def test_the_wizard_is_clicked_through_with_a_capture_of_every_page(self):
        p = FauxWindows(self.h, self.tmp)
        shots = []
        pages = p.assistant(p.setup, [], capturer=lambda name: shots.append(name) or name)
        self.assertEqual([x["page"] for x in pages], ["bienvenue", "dossier", "installation", "installe", "fin", "fin"])
        self.assertEqual([c for x in pages for c in x["clics"]],
                         ["Suivant >", "Suivant >", "Suivant >", "Lancer Brain in a Box : décoché", "Fermer"])
        # « Suivant > » on three pages in a row: three first clicks, the mouse's (a GitHub VM showed Invoke on the second).
        self.assertEqual([a[3] for a in p.appels if a[0] == "cliquer"], ["souris"] * 4)
        self.assertEqual(shots, ["installeur-" + x["page"] for x in pages])
        # The desktop shortcut is left as the installer offers it.
        self.assertFalse([a for a in p.appels if a[0] == "cocher" and "bureau" in a[2]])
        self.assertEqual(p.lancer, 0)
        self.assertEqual(p.setup.poll(), 0)

    def test_fermer_is_clicked_once_and_the_installer_given_time_to_quit(self):
        # On a GitHub VM, the installer took a minute to quit after « Fermer »: clicking again would not help.
        p = FauxWindows(self.h, self.tmp)
        p.quitte = 75
        shots = []
        pages = p.assistant(p.setup, [], capturer=lambda name: shots.append(name) or name)
        self.assertEqual(pages[-1]["clics"], ["Fermer"])
        self.assertGreaterEqual(pages[-1]["quitte_apres_s"], 75)
        self.assertEqual(shots[-1], "installeur-fermeture")
        p = FauxWindows(Horloge(), self.tmp)
        p.quitte = 10 ** 9
        with self.assertRaises(ta.ErreurPilote) as cm:
            p.assistant(p.setup, [])
        self.assertIn("l'installeur est toujours ouvert 180 s après « Fermer »", str(cm.exception))
        self.assertEqual(len([a for a in p.appels if a[:3] == ["cliquer", "7", "Fermer"]]), 1)

    def test_pages_as_data(self):
        keys = {k: ta.page_installeur(nsis(k))["cle"] for k in ("bienvenue", "dossier", "installation", "installe", "fin", "message")}
        self.assertEqual(keys, {k: k for k in keys})
        self.assertEqual(ta.action_installeur(ta.page_installeur(nsis("installation"))), None)
        self.assertEqual(ta.action_installeur(ta.page_installeur(nsis("fin", lancer=0))), ("cliquer", "Fermer"))
        self.assertEqual(ta.norm_win("&Suivant >"), "Suivant >")
        # The title bar's Close is the window's, never the installer's last button.
        self.assertNotIn("Close", ta.page_installeur(nsis("bienvenue"))["boutons"])

    def test_an_error_box_of_the_installer_fails_with_its_words(self):
        p = FauxWindows(self.h, self.tmp)
        p.page = "message"
        with self.assertRaises(ta.ErreurPilote) as cm:
            p.assistant(p.setup, [])
        self.assertIn("l'installeur affiche : « Erreur : le téléchargement de WebView2 a échoué »", str(cm.exception))

    def test_a_click_that_changes_nothing_and_an_installer_that_never_ends(self):
        p = FauxWindows(self.h, self.tmp, pages={})
        with self.assertRaises(ta.ErreurPilote) as cm:
            p.assistant(p.setup, [])
        self.assertIn("le clic sur « Suivant > » ne change rien dans l'installeur (3 essais)", str(cm.exception))
        # The mouse, as a user clicks; then Invoke, as a screen reader presses it; then the mouse again.
        self.assertEqual([a[3] for a in p.appels if a[0] == "cliquer"], ["souris", "invoke", "souris"])
        p = FauxWindows(Horloge(), self.tmp, pages={("bienvenue", "Suivant >"): "installation"})
        p.duree = 10 ** 9
        with self.assertRaises(ta.ErreurPilote) as cm:
            p.assistant(p.setup, [])
        self.assertIn("l'installeur n'a pas fini en 10 min (page « installation »)", str(cm.exception))

    def installed(self, ruche="HKCU", ailleurs=False, cible=None):
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        local, roaming, profile = (root / n for n in ("Local", "Roaming", "Profile"))
        folder = (root / "Programs" if ailleurs else local) / "Brain in a Box"
        folder.mkdir(parents=True)
        exe = folder / "brain-in-a-box.exe"
        exe.write_bytes(b"MZ")
        menu = roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        menu.mkdir(parents=True)
        (menu / "Brain in a Box.lnk").write_bytes(b"L")
        (profile / "Desktop").mkdir(parents=True)
        p = FauxWindows(self.h, self.tmp)
        p._outil = lambda args, timeout=120: {"ok": True, "cible": str(cible or exe)}
        entries = [{"ruche": ruche, "cle": "Brain in a Box", "desinstalleur": str(folder / "uninstall.exe")}]
        details = {}
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local), "APPDATA": str(roaming), "USERPROFILE": str(profile)}), \
                mock.patch.object(ta.PiloteWindows, "desinstallations", staticmethod(lambda: entries)):
            notes = p.verifier_installation(details)
        return p, details, notes, exe

    def test_installed_for_the_user_alone_and_opened_from_the_start_menu(self):
        p, details, notes, exe = self.installed()
        self.assertEqual(p.exe, str(exe))
        self.assertEqual((details["app"], details["raccourci_bureau"]), ("brain-in-a-box.exe", False))
        self.assertEqual(notes, [])
        with self.assertRaises(ta.ErreurPilote) as cm:
            self.installed(ruche="HKLM")
        self.assertIn("pour tous les utilisateurs", str(cm.exception))

    def test_installed_elsewhere_or_a_shortcut_elsewhere_fails(self):
        with self.assertRaises(ta.ErreurPilote) as cm:
            self.installed(ailleurs=True)
        self.assertIn("hors des programmes de l'utilisateur", str(cm.exception))
        with self.assertRaises(ta.ErreurPilote) as cm:
            self.installed(cible=Path(self.tmp) / "autre" / "brain-in-a-box.exe")
        self.assertIn("le raccourci du menu Démarrer mène à", str(cm.exception))


class WindowsApp(Base):
    def test_the_window_a_dialog_in_front_and_a_click_on_it(self):
        p = FauxWindows(self.h, self.tmp)
        p.proc = FauxSetup(self.h)
        p.exe = r"C:\Users\r\AppData\Local\Brain in a Box\brain-in-a-box.exe"
        devant = {"h": 9, "pid": 77, "classe": "#32770", "exe": r"C:\Windows\System32\SmartScreen.exe",
                  "arbre": uia("Window", "Windows", [uia("Text", "Brain in a Box souhaite accéder à ton dossier Documents"),
                                                     uia("Button", "Autoriser"), uia("Button", "Refuser")])}
        console = dict(devant, classe="ConsoleWindowClass")
        calls = []

        def outil(args, timeout=120):
            calls.append([str(a) for a in args])
            if args[0] == "lire":
                # Tauri's event window first: 16 x 16, visible, nothing a user sees.
                return {"ok": True, "fenetres": [
                    {"h": 6, "classe": "Tao Thread Event Target", "titre": "", "rect": [0, 0, 16, 16],
                     "arbre": uia("Pane", "", c="Tao Thread Event Target")},
                    {"h": 5, "classe": "Tauri Window", "titre": "Brain in a Box", "rect": [130, 49, 796, 719],
                     "arbre": page_uia()}], "devant": outil.devant}
            return {"ok": True, "comment": "Invoke"}
        p._outil = outil
        outil.devant = devant
        e = p.lire()
        self.assertEqual((e.titre, ta.cle_ecran(e)[0]), ("Salut, moi c'est Bibo !", "bienvenue"))
        self.assertEqual(e.dialogues[0]["processus"], "SmartScreen")
        self.assertEqual(ta.bouton_autoriser(e.dialogues[0]), "Autoriser")
        p.cliquer("Autoriser", processus="SmartScreen")
        p.cliquer("Vérifier mon PC")
        self.assertEqual([c for c in calls if c[0] == "cliquer"], [["cliquer", "9", "Autoriser", "souris"],
                                                                   ["cliquer", "5", "Vérifier mon PC", "souris"]])
        # Brought to the front once, the first time the window was seen.
        p.lire()
        self.assertEqual([c for c in calls if c[0] == "devant"], [["devant", "5"]])
        outil.devant = console
        self.assertEqual(p.lire().dialogues, [])

    def test_a_pc_is_clicked_through_with_its_own_words(self):
        f = FauxPilote(self.h)
        f.machine = "PC"
        f.ecrans["bienvenue"] = dict(f.ecrans["bienvenue"], boutons={"Vérifier mon PC": "diag-en-cours"})
        f.ecrans["login-inline"]["boutons"]["Plus tard"] = "brain"     # Windows: Claude Code, then install.ps1
        f.ecrans["claude"]["texte"] = "Étape 1 sur 2. L’outil officiel d’Anthropic."
        f.ecrans["brain"] = dict(f.ecrans["brain"], texte="Étape 2 sur 2. C’est l’étape la plus longue : 10 à 20 minutes.")
        self.parcours(f).premier_lancement({})
        self.assertEqual(f.clics, ["Vérifier mon PC", "Tout installer", "Passer (recherche par mots-clés)",
                                   "Plus tard", "Plus tard"])
        self.assertFalse([c for c in f.captures if "etape-bun" in c or "etape-obsidian" in c])


def smartscreen(plus=False):
    """SmartScreen's question as a GitHub VM showed it: a UWP frame of explorer's, without a title,
    in front, holding a CoreWindow « Windows Defender SmartScreen »: a link « More info », then the
    button « Run anyway »."""
    lien = uia("Hyperlink", "More info", [uia("Text", "More info")], lt="link", a="moreBtn")
    corps = [uia("Group", "", [uia("Text", "Windows protected your PC")], a="messageTitle"),
             uia("Group", "", [uia("Text", "Microsoft Defender SmartScreen prevented an unrecognized app from starting. "
                                           "Running this app might put your PC at risk.")], a="messageBody")]
    corps += [uia("Text", "App: Brain-in-a-Box_0.1.0_windows-x64-setup.exe"), uia("Text", "Publisher: Unknown publisher")] \
        if plus else [lien]
    boutons = ([uia("Button", "Run anyway", a="runAnywayBtn")] if plus else []) + [uia("Button", "Don’t run", a="dontRunBtn")]
    vue = uia("Pane", "about:blank", [uia("Pane", "", [uia("Group", "Windows protected your PC", corps, a="bodyWrapper")] + boutons)],
              a="smartScreenWebView", c="WebView")
    arbre = uia("Window", "", [
        uia("Window", "", [uia("MenuBar", "System", [uia("MenuItem", "System")]), uia("Button", "Close ", a="Close")],
            a="TitleBar", c="ApplicationFrameTitleBarWindow"),
        uia("Window", "Windows Defender SmartScreen", [vue], c="Windows.UI.Core.CoreWindow"),
        uia("Pane", "", c="ApplicationFrameInputSinkWindow"),
        uia("TitleBar", "", [uia("Button", "Close", a="Close")], a="TitleBar")], c="ApplicationFrameWindow")
    return {"ok": True, "h": 327790, "pid": 1772, "titre": "", "classe": "ApplicationFrameWindow", "devant": True,
            "exe": r"C:\Windows\explorer.exe", "arbre": arbre}


class WindowsShell(Base):
    def lancer(self, montre):
        """lancer_setup with SmartScreen on the screen while `montre` says so, a click on « Run anyway » ending it."""
        p = FauxWindows(self.h, self.tmp)
        etat = {"plus": False, "fini": threading.Event()}
        calls = []

        def outil(args, timeout=120):
            args = [str(a) for a in args]
            calls.append(args)
            self.h.now += 0.3
            shown = montre and not etat["fini"].is_set()
            if args[0] == "fenetres":
                # explorer's windows, the UWP frame among them: neither its title nor its program says SmartScreen.
                return {"ok": True, "fenetres": ([{k: v for k, v in smartscreen().items() if k != "arbre"}] if shown else []) + [
                    {"h": 196740, "pid": 1772, "titre": "", "classe": "Shell_TrayWnd", "exe": r"C:\Windows\explorer.exe"},
                    {"h": 3, "pid": 1772, "titre": "Program Manager", "classe": "Progman", "exe": r"C:\Windows\explorer.exe"}]}
            if args[0] == "arbre":
                self.assertEqual(args[1], "327790")
                return smartscreen(etat["plus"])
            if args[0] == "lire":
                self.assertEqual(args[1], str(os.getpid()))
                return {"ok": True, "fenetres": [], "devant": None}
            if args[0] == "cliquer":
                if args[2] == "More info":
                    etat["plus"] = True
                if args[2] == "Run anyway":
                    etat["fini"].set()
                return {"ok": True, "comment": "clic souris"}
            return {"ok": True}
        p._outil = outil

        def ouvrir(fichier, out):
            # ShellExecuteEx holds until SmartScreen's question is answered.
            if montre:
                etat["fini"].wait(10)
            out.update(ok=True, erreur=0, handle=1234)
        shots, details = [], {}
        out, notes = p.lancer_setup(Path(self.tmp) / "setup.exe", details, capturer=lambda n: shots.append(n) or n, ouvrir=ouvrir)
        return p, out, notes, details, shots, calls

    def test_setup_opened_by_the_shell_and_smartscreen_answered_as_a_user_does(self):
        p, out, notes, details, shots, calls = self.lancer(montre=True)
        self.assertEqual(out["handle"], 1234)
        self.assertEqual(notes, [])
        self.assertEqual([c[2] for c in calls if c[0] == "cliquer"], ["More info", "Run anyway"])
        self.assertEqual(shots, ["smartscreen", "smartscreen"])
        self.assertEqual([c["comment"] for c in p.clics_installation], [ta.CLIC_SOURIS] * 2)
        self.assertEqual(details["avant_installeur"][1]["boutons"], ["Close", "Run anyway", "Don't run"])
        self.assertEqual(details["avant_installeur"][0]["liens"], ["More info"])
        self.assertIn("Windows protected your PC", details["avant_installeur"][0]["textes"])

    def test_no_smartscreen_is_said_never_hidden(self):
        p, out, notes, details, shots, calls = self.lancer(montre=False)
        self.assertEqual(shots, [])
        self.assertIn("SmartScreen ne s'est pas affiché : setup.exe porte pourtant la Mark of the Web", notes[0])

    def test_the_attachment_managers_question_is_answered_too(self):
        p = FauxWindows(self.h, self.tmp)
        fini = threading.Event()
        boite = {"h": 31, "pid": os.getpid(), "classe": "#32770", "titre": "Open File - Security Warning",
                 "arbre": uia("Window", "Open File - Security Warning", [
                     uia("Text", "The publisher could not be verified. Are you sure you want to run this software?"),
                     uia("Button", "Run"), uia("Button", "Cancel")])}

        def outil(args, timeout=120):
            self.h.now += 0.3
            if args[0] == "fenetres":
                return {"ok": True, "fenetres": []}
            if args[0] == "lire":
                return {"ok": True, "fenetres": [] if fini.is_set() else [boite], "devant": None}
            if args[0] == "cliquer" and str(args[2]) == "Run":
                fini.set()
            return {"ok": True, "comment": "clic souris"}
        p._outil = outil
        out, notes = p.lancer_setup(Path(self.tmp) / "setup.exe", {}, ouvrir=lambda f, out: (fini.wait(10), out.update(ok=True, handle=7)))
        self.assertEqual((out["handle"], notes), (7, []))
        self.assertEqual(p.clics_installation, [{"ecran": ta.nom_programme(sys.executable).lower(), "bouton": "Run",
                                                 "comment": ta.CLIC_SOURIS}])

    def test_a_question_nobody_answers_names_the_windows_on_screen(self):
        p = FauxWindows(self.h, self.tmp)
        def outil(args, timeout=120):
            self.h.now += 5
            if args[0] == "fenetres":
                return {"ok": True, "fenetres": [{"h": 3, "titre": "Program Manager", "exe": r"C:\Windows\explorer.exe"}]}
            return {"ok": True, "fenetres": []}
        p._outil = outil
        bloque = threading.Event()
        self.addCleanup(bloque.set)
        with self.assertRaises(ta.ErreurPilote) as cm:
            p.lancer_setup(Path(self.tmp) / "setup.exe", {}, ouvrir=lambda f, out: bloque.wait(60))
        self.assertIn("setup.exe ne s'ouvre pas en 5 min (aucune question lue à l'écran ; fenêtres : Program Manager (explorer))",
                      str(cm.exception))

    def test_the_mark_of_the_web_a_browser_leaves(self):
        p = FauxWindows(self.h, self.tmp)
        f = Path(self.tmp) / "setup.exe"
        f.write_bytes(b"MZ")
        self.assertEqual(p.marquer_telecharge(f), "Zone.Identifier : ZoneId=3 (Internet)")
        with open(str(f) + ":Zone.Identifier", encoding="ascii", newline="") as z:
            self.assertTrue(z.read().startswith("[ZoneTransfer]\r\nZoneId=3\r\n"))

    def test_a_new_screen_starts_its_clicks_with_the_mouse(self):
        # The same label on two screens in a row (« Plus tard ») is two first clicks, not a retry.
        p = FauxWindows(self.h, self.tmp)
        modes = []
        p._outil = lambda args, timeout=120: modes.append(str(args[3])) or {"ok": True, "comment": "clic souris"}
        p.hwnd = 5
        p.cliquer("Plus tard")
        p.nouvel_ecran()
        p.cliquer("Plus tard")
        p.cliquer("Plus tard")
        self.assertEqual(modes, ["souris", "souris", "invoke"])
        f = FauxPilote(self.h)
        pa = self.parcours(f)
        pa.premier_lancement({})
        self.assertEqual(f.ecrans_vus, len(pa.ecrans))


class WindowsEnvironment(unittest.TestCase):
    def test_a_session_s_environment_the_store_python_first_never_a_token(self):
        block = ["=C:=C:\\", "Path=C:\\Windows\\system32;C:\\Users\\r\\AppData\\Local\\Microsoft\\WindowsApps",
                 "USERPROFILE=C:\\Users\\r", "TEMP=C:\\Users\\r\\AppData\\Local\\Temp", "broken"]
        session = ta.env_depuis_bloc(block)
        self.assertEqual(sorted(session), ["Path", "TEMP", "USERPROFILE"])
        with mock.patch.dict(os.environ, {"GH_TOKEN": "ghs_x", "GITHUB_TOKEN": "ghs_y", "CI": "true"}):
            env = ta.env_app_windows(session, "C:\\stub", {"BIAB_ARCHIVE": "C:\\t\\k.zip"})
        self.assertEqual(env["Path"], "C:\\stub;C:\\Windows\\system32;C:\\Users\\r\\AppData\\Local\\Microsoft\\WindowsApps")
        self.assertEqual(sorted(env), ["BIAB_ARCHIVE", "Path", "TEMP", "USERPROFILE"])
        self.assertEqual(ta.env_app_windows(session)["Path"], session["Path"])

    def test_the_helper_source_is_ascii_and_answers_every_request(self):
        with open(ta.PiloteWindows.SOURCE, "rb") as f:
            src = f.read()
        self.assertEqual([i for i, c in enumerate(src) if c > 0x7f][:3], [], "csc reads it in the ANSI code page")
        with open(ta.__file__, encoding="utf-8") as f:
            py = f.read()
        asked = set(re.findall(r'_outil\(\[\s*"([a-z]+)"', py)) | {"cocher", "cliquer"}
        self.assertEqual(asked, {"lire", "cliquer", "cocher", "fermer", "devant", "capturer", "raccourci", "ranger", "fenetres",
                                 "arbre"})
        for op in asked:
            self.assertIn('op == "%s"' % op, src.decode("ascii"))


def atspi_page(statut="status bar", boutons=(("Revérifier", True), ("Tout installer", True))):
    """The helper's tree of the diagnosis screen, as WebKitGTK gives it on Ubuntu 24.04 (recorded, cut)."""
    ligne = lambda label, statut_, detail: {"r": "list item", "n": label, "v": "%s : %s\n%s\n" % (label, statut_, detail)}
    return {"r": "frame", "n": "Brain in a Box", "x": [249, 97, 782, 705], "k": [
        {"r": "filler", "n": "", "k": [{"r": "filler", "n": "", "k": [{"r": "filler", "n": "", "k": [
            {"r": "scroll pane", "n": "", "k": [{"r": "document web", "n": "Brain in a Box", "x": [250, 117, 780, 680], "k": [
                {"r": "landmark", "n": "", "k": [{"r": statut, "n": "", "l": "polite", "k": [
                    {"r": "paragraph", "n": "", "v": "J’ai fait le tour."},
                    {"r": "paragraph", "n": "", "v": "Il manque Claude Code, Bun et ton cerveau. Je m’en occupe."}]}]},
                {"r": "landmark", "n": "", "k": [
                    {"r": "paragraph", "n": "", "v": "Ubuntu 24.04.5 LTS · 12 Go de mémoire · 118 Go libres"},
                    {"r": "panel", "n": "", "k": [
                        ligne("Système", "OK", "Ubuntu 24.04.5 LTS"),
                        ligne("Outils de base (git, python3, curl)", "OK", "git, python3, curl et unzip sont là."),
                        ligne("Bun (le moteur de la mémoire)", "À installer", "Pas encore installé.")]}]},
                {"r": "landmark", "n": "", "k": [{"r": "push button", "n": b, "e": on, "x": [700 + 120 * i, 736, 110, 41]}
                                                 for i, (b, on) in enumerate(boutons)]}]}]}]}]}]}]}


class LinuxReading(unittest.TestCase):
    """PiloteLinux's side of the window: AT-SPI's tree as the robot reads it."""

    def test_bibo_in_the_status_bar_and_the_checks_as_macos_gives_them(self):
        e = ta.ecran_depuis_arbre([{"titre": "Brain in a Box", "arbre": ta.arbre_atspi(atspi_page())}])
        self.assertEqual((e.titre, e.texte, e.lecture), ("J'ai fait le tour.", "Il manque Claude Code, Bun et ton cerveau. Je m'en occupe.",
                                                         "statut"))
        self.assertEqual(e.boutons, [("Revérifier", True), ("Tout installer", True)])
        self.assertEqual(ta.cle_ecran(e), ("diagnostic", None))
        # The window's path to Bibo's region: a quick read follows it in the app's own tree.
        self.assertEqual(e.statut, (0, (0, 0, 0, 0, 0, 0, 0)))
        self.assertEqual(ta.deja_presents(e.textes, ta.PiloteLinux.neufs),
                         [("Outils de base (git, python3, curl)", "git, python3, curl et unzip sont là.")])
        self.assertEqual(e.textes[e.textes.index("Bun (le moteur de la mémoire)") + 1], ": À installer")

    def test_a_live_region_stands_for_a_status_bar_and_a_disabled_button_says_so(self):
        e = ta.ecran_depuis_arbre([{"titre": "x", "arbre": ta.arbre_atspi(atspi_page("section", (("Plus tard", False),)))}])
        self.assertEqual(e.titre, "J'ai fait le tour.")
        self.assertEqual(e.bouton("Plus tard"), False)

    def test_lines_of_a_text_and_a_checks_status_apart(self):
        self.assertEqual(ta.lignes("Connexion à Claude\u00a0: À noter\nTu te connecteras une fois Claude Code installé.\n"),
                         ["Connexion à Claude", ": À noter", "Tu te connecteras une fois Claude Code installé."])
        self.assertEqual(ta.lignes("Il te faut un abonnement Claude Pro ou Max."), ["Il te faut un abonnement Claude Pro ou Max."])
        self.assertEqual(ta.lignes("L'heure : 12 h\n\n"), ["L'heure : 12 h"])

    def test_the_window_where_x_says_it_is(self):
        out = ("xwininfo: Window id: 0x400003 \"Brain in a Box\"\n\n  Absolute upper-left X:  250\n  Absolute upper-left Y:  117\n"
               "  Relative upper-left X:  1\n  Relative upper-left Y:  20\n  Width: 780\n  Height: 680\n")
        self.assertEqual(ta.place_xwininfo(out), [250, 117, 780, 680])
        self.assertIsNone(ta.place_xwininfo("X Error of failed request: BadWindow"))

    def test_what_pkexec_ran_from_its_journal(self):
        cmd = "/bin/sh -c export DEBIAN_FRONTEND=noninteractive\napt-get update -q || true\napt-get install -y -q \"$@\"\n sh git curl"
        lines = [json.dumps({"MESSAGE": "runner: Executing command [USER=root] [TTY=unknown] [CWD=/home/runner] [COMMAND=%s]" % cmd}),
                 json.dumps({"MESSAGE": list(("runner: Executing command [USER=root] [TTY=unknown] [CWD=/] [COMMAND=/bin/sh -c x]")
                                             .encode("utf-8"))}),
                 json.dumps({"MESSAGE": "runner: Error executing command as another user: Not authorized"}), "not json"]
        self.assertEqual(ta.commandes_root("\n".join(lines)), [cmd, "/bin/sh -c x"])

    def test_the_app_gets_a_desktops_environment_never_a_token(self):
        with mock.patch.dict(os.environ, {"GH_TOKEN": "ghs_x", "GITHUB_TOKEN": "ghs_y", "CI": "true", "USER": "runner"}):
            env = ta.env_linux("/home/runner", ":99", 1001, {"BIAB_ARCHIVE": "/tmp/k.zip"})
        self.assertEqual(sorted(env), ["BIAB_ARCHIVE", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "HOME", "LANG", "LOGNAME", "PATH",
                                       "SHELL", "USER", "XDG_RUNTIME_DIR", "XDG_SESSION_TYPE"])
        self.assertEqual((env["DBUS_SESSION_BUS_ADDRESS"], env["XDG_RUNTIME_DIR"]), ("unix:path=/run/user/1001/bus", "/run/user/1001"))
        self.assertTrue(env["PATH"].startswith("/usr/local/sbin:") and "/home/" not in env["PATH"])

    def test_the_vm_loses_git_and_curl_only_when_apt_can_put_them_back(self):
        class Apt(ta.PiloteLinux):
            """apt on a runner: `installable` are what apt would install again at their newest version."""

            def __init__(self, installable):
                self.installable, self.calls = installable, []

            def _run(self, argv, timeout=120, env=None, creationflags=0):
                self.calls.append(list(argv))
                if argv[0] == "dpkg-query":
                    return 0, {"git": "1:2.55.0-0ppa1", "curl": "8.5.0-2ubuntu10.16"}[argv[-1]]
                if "-s" in argv:
                    asked = argv[argv.index("install") + 1:]
                    bad = [p for p in asked if p not in self.installable]
                    return (100, "The following packages have unmet dependencies:\n %s : Depends: libx (= 2)\n"
                                 "E: Unable to correct problems, you have held broken packages." % bad[0]) if bad else (0, "Inst")
                if "remove" in argv:
                    return 0, "Removing git (1) ...\nRemoving curl (1) ...\nRemoving git-lfs (3.8.0) ...\n"
                return 0, ""

        with mock.patch.object(ta.shutil, "which", return_value=None):
            p = Apt(("git", "curl"))
            out = p.poste_neuf()
            self.assertEqual((out["retires"], out["aussi"], out["gardes"]), (["git", "curl"], ["git-lfs"], {}))
            self.assertNotIn("note", out)
            self.assertIn("update", p.calls[0], "the lists the app will see")
            # apt would not install curl's newest: curl goes back at its own version, the app installs git.
            p = Apt(("git",))
            out = p.poste_neuf()
            self.assertEqual(out["retires"], ["git"])
            self.assertIn("E: Unable to correct problems, you have held broken packages.", out["gardes"]["curl"])
            self.assertIn("apt n'y installerait pas sa version courante : curl", out["note"])
            self.assertIn(["sudo", "-n", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "-q",
                           "curl=8.5.0-2ubuntu10.16"], p.calls)
            with self.assertRaises(ta.ErreurPilote):
                Apt(()).poste_neuf()

    def test_the_helper_is_ascii_compiles_and_answers_every_request(self):
        self.assertTrue(all(ord(c) < 128 for c in ta.ATSPI), "written and read as ASCII")
        compile(ta.ATSPI, "pilote-atspi.py", "exec")
        with open(ta.__file__, encoding="utf-8") as f:
            py = f.read()
        asked = set(re.findall(r'_helper\(\{"op": "([a-z]+)"', py))
        self.assertEqual(asked, {"lire", "rapide", "bouton"})
        for op in asked:
            self.assertIn('"%s": %s' % (op, op), ta.ATSPI)


class Scenario(Base):
    """The whole scenario on the build path, a fake window and a fake check of the kit: what reaches
    the app, what the results say."""

    def run_it(self, pilote=None, check=None):
        home = Path(self.tmp) / "home"
        home.mkdir(exist_ok=True)
        dmg = Path(self.tmp) / "Brain-in-a-Box_0.1.0-terrain.abc_macos-universal.dmg"
        dmg.write_bytes(b"dmg")
        sha = subprocess.run(["git", "-C", REPO, "rev-parse", "HEAD"], capture_output=True).stdout.decode().strip()
        opts = types.SimpleNamespace(home=home, json=str(Path(self.tmp) / "r.json"), parcours="app", vm=True,
                                     source="build", fichier=str(dmg), captures=str(self.shots))
        checked = []
        self.nights = []
        kit = types.SimpleNamespace(Echec=terrain.Echec, workdir=terrain.workdir, run=terrain.run, REPO=terrain.REPO,
                                    tested_ref=lambda: sha,
                                    check_install=check or (lambda t, o: (t("hooks"), checked.append(o.home))),
                                    run_nightly=lambda t, o: (t("nuit-reelle"), self.nights.append(list(p.nuit))))
        p = pilote or FauxPilote(self.h, home=home)
        p.home = home
        secrets = {"GH_TOKEN": "ghs_x", "GITHUB_TOKEN": "ghs_y", "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-z"}
        with mock.patch.dict(os.environ, secrets):
            terrain.play("app", lambda t, o: ta.scenario(t, o, kit, pilote=p, horloge=self.h, dormir=self.h.dormir), opts)
            left = {k: os.environ.get(k) for k in secrets}
        with open(opts.json, encoding="utf-8") as f:
            row = json.load(f)["scenarios"][0]
        return row, p, checked, sha, left, home

    def test_built_installed_clicked_checked_and_opened_again(self):
        row, p, checked, sha, left, home = self.run_it()
        self.assertEqual(row["statut"], "ok", row["raison"])
        self.assertIn("deuxième lancement : déjà installé", row["raison"])
        self.assertEqual(len(checked), 1)
        self.assertEqual(p.fermetures, 2)
        for env in p.envs:
            self.assertFalse({"GH_TOKEN", "GITHUB_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"} & set(env), env)
            self.assertTrue(env["BIAB_ARCHIVE"].endswith(".zip"))
        self.assertEqual((left["GH_TOKEN"], left["GITHUB_TOKEN"]), (None, None), "popped for the rest of the job")
        d = row["details"]
        self.assertEqual((d["source"], d["sources_du_cerveau"]), ("build", "commit testé %s (BIAB_ARCHIVE)" % sha[:12]))
        self.assertTrue((home / "Downloads" / "Brain-in-a-Box_0.1.0-terrain.abc_macos-universal.dmg").is_file())
        self.assertEqual(d["captures"], sorted(os.listdir(self.shots)))
        self.assertTrue(any(c.endswith("-relance-deja-installe.png") for c in d["captures"]))
        self.assertGreater(d["duree_parcours_s"], 500)
        # The nightly the app registered, given back to the scheduler and started, once all is checked.
        self.assertEqual(self.nights, [["ecartee", "rendue"]])
        self.assertIn("une nuit lancée par le planificateur", row["raison"])
        self.assertNotIn("install_log", d)

    def test_a_click_no_user_could_do_fails_the_night(self):
        p = FauxPilote(self.h, home=Path(self.tmp) / "home")
        p.comment = lambda label: "AXPress (pas de clic souris : une autre app est devant (Finder))" if label == "Tout installer" \
            else ta.CLIC_SOURIS
        row, _, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual((row["statut"], row["etape"]), ("echec", "app-clics"))
        self.assertIn("1 clic(s) sans la souris, qu'un utilisateur n'aurait pas pu faire", row["raison"])
        self.assertIn("« Tout installer » (diagnostic) : AXPress (pas de clic souris : une autre app est devant (Finder))",
                      row["raison"])
        self.assertEqual(self.nights, [])

    def test_a_failure_keeps_the_end_of_the_install_log(self):
        home = Path(self.tmp) / "home"
        p = FauxPilote(self.h, home=home)
        log = home / ".brain-in-a-box" / "install.log"
        log.parent.mkdir(parents=True)
        log.write_text("".join("ligne %d\n" % i for i in range(200)) + "bun install: error: ENOSPC\n", encoding="utf-8")
        p.ecrans["brain"] = {"titre": "Je prépare ton cerveau.", "apres": (90, "oups")}
        p.ecrans["oups"] = {"titre": "Oups, ça a coincé.", "boutons": {"Copier le rapport": None, "Réessayer": "brain"}}
        row, _, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual((row["statut"], row["etape"]), ("echec", "app-etape-brain"))
        self.assertEqual(len(row["details"]["install_log"]), 80)
        self.assertEqual(row["details"]["install_log"][-1], "bun install: error: ENOSPC")

    def test_a_failure_keeps_the_systems_logs_and_never_fails_on_them(self):
        p = FauxPilote(self.h, home=Path(self.tmp) / "home")
        p.ecrans["bun"] = {"titre": "J’installe Bun.", "apres": (5, "oups")}
        p.ecrans["oups"] = {"titre": "Oups, ça a coincé.", "boutons": {"Copier le rapport": None, "Réessayer": "bun"}}
        p.journaux = lambda: {"apt_term_log": ["E: setgroups 65534 failed"]}
        row, _, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual((row["statut"], row["etape"]), ("echec", "app-etape-bun"))
        self.assertEqual(row["details"]["journaux_systeme"], {"apt_term_log": ["E: setgroups 65534 failed"]})
        p = FauxPilote(self.h, home=Path(self.tmp) / "home2")
        p.ecrans["bun"] = {"titre": "J’installe Bun.", "apres": (5, "oups")}
        p.ecrans["oups"] = {"titre": "Oups, ça a coincé.", "boutons": {"Copier le rapport": None, "Réessayer": "bun"}}
        p.journaux = lambda: 1 / 0
        row, _, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual(row["etape"], "app-etape-bun")
        self.assertIn("ZeroDivisionError", row["details"]["journaux_systeme"]["erreur"])

    def test_what_the_vm_had_before_the_app_is_said(self):
        p = FauxPilote(self.h, home=Path(self.tmp) / "home")
        p.neufs = ("Outils Apple (git, python3)",)
        p.ecrans["diag"]["textes"] = ["Version de macOS", ": OK", "macOS 26.6.2", "Outils Apple (git, python3)", ": OK",
                                      "git et python3 sont là.", "Bun (le moteur de la mémoire)", ": À installer", "Pas encore installé."]
        row, _, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual(row["statut"], "ok", row["raison"])
        self.assertIn("à noter : déjà sur la VM avant l'app : Outils Apple (git, python3) (git et python3 sont là.), "
                      "son installation par l'app n'est pas testée", row["raison"])
        self.assertEqual(ta.deja_presents(["Python (pour les automatismes)", ": OK", "Python 3.14.7", "Git (l'historique de tes notes)",
                                           ": À installer", "x"], ta.PiloteWindows.neufs),
                         [("Python (pour les automatismes)", "Python 3.14.7")])

    def test_a_failure_after_the_window_still_quotes_bibo(self):
        def check(t, o):
            t("lanceurs")
            t.check(False, "gbq query ne trouve pas Profile/lessons.md (code 127)")
        row, p, _, _, _, _ = self.run_it(check=check)
        self.assertEqual((row["statut"], row["etape"]), ("echec", "lanceurs"))
        self.assertIn("gbq query ne trouve pas Profile/lessons.md (code 127) ; Bibo : « Ton second cerveau est prêt !",
                      row["raison"])
        self.assertTrue(row["details"]["captures"][-1].endswith("-echec.png"))

    def test_the_machine_users_have_before_the_first_start_and_its_own_checks_after(self):
        # Linux: git and curl taken away once the app is installed, before it starts; what ran as
        # root checked once everything is installed.
        p = FauxPilote(self.h, home=Path(self.tmp) / "home")
        order = []
        p.poste_neuf = lambda: order.append(("poste", p.etat)) or {"retires": ["git", "curl"], "aussi": []}
        p.constats = lambda t, details: order.append(("constats", t.nom)) or details.update(root=["/bin/sh -c ... apt-get install"])
        row, _, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual(row["statut"], "ok", row["raison"])
        self.assertEqual(order, [("poste", None), ("constats", "app-outils")])
        self.assertEqual(row["details"]["poste_neuf"], {"retires": ["git", "curl"], "aussi": []})
        self.assertEqual(row["details"]["root"], ["/bin/sh -c ... apt-get install"])

    def test_sources_the_app_did_not_take_fail(self):
        p = FauxPilote(self.h, home=Path(self.tmp) / "home")
        p.sources = False
        row, _, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual((row["statut"], row["etape"]), ("echec", "app-sources"))
        self.assertIn("BIAB_ARCHIVE ignoré", row["raison"])


if __name__ == "__main__":
    unittest.main()
