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
        # Opened again with the brain and Claude Code there, Claude logged in (the guide's fake Claude says so).
        "retour": {"titre": "Content de te revoir" + NB + "!",
                   "texte": "Je peux te guider pas à pas dans un logiciel : tu me montres ta fenêtre, je te dis où cliquer.",
                   "boutons": {"Vérifier mon Mac": "diag-en-cours", "Guide-moi dans un logiciel": None}},
        # The same, the login put off: the welcome asks for it before the guide.
        "retour-sans-compte": {"titre": "Content de te revoir" + NB + "!",
                               "texte": "Il manque ton compte Claude" + NB + ": connecte-le, puis je pourrai te guider pas à pas dans un logiciel.",
                               "boutons": {"Vérifier mon Mac": "diag-en-cours", "Connecter mon compte Claude": None}},
        "diag-en-cours": {"titre": "Je regarde ton Mac…", "texte": "Ça prend quelques secondes.",
                          "apres": (3, lambda: "deja" if installe() else "diag")},
        # The lines folded: the robot unfolds them, then installs.
        "diag": {"titre": "J’ai fait le tour" + NB + ": ton Mac est prêt.",
                 "texte": "J’installe Claude Code, le moteur de ta mémoire, Obsidian et ton cerveau" + NB + ": 10 à 20 minutes. Branche-toi sur un Wi-Fi si tu peux.",
                 "boutons": {"Voir le détail": "diag-detail", "Tout installer": "claude"},
                 "textes": ["Mon conseil" + NB + ":", "Ton cerveau marche avec Claude" + NB + ": il te faudra un abonnement Claude Pro ou Max."]},
        "diag-detail": {"titre": "J’ai fait le tour" + NB + ": ton Mac est prêt.",
                        "texte": "J’installe Claude Code, le moteur de ta mémoire, Obsidian et ton cerveau" + NB + ": 10 à 20 minutes. Branche-toi sur un Wi-Fi si tu peux.",
                        "boutons": {"Masquer le détail": "diag", "Tout installer": "claude"},
                        "textes": ["Mon conseil" + NB + ":", "Ton cerveau marche avec Claude" + NB + ": il te faudra un abonnement Claude Pro ou Max.",
                                   "macOS 26.6 · Puce Apple · 7 Go de mémoire", "Claude Code", "À installer"]},
        "deja": {"titre": "Tout est déjà installé" + NB + "!",
                 "texte": "Ton cerveau cherche par mots-clés" + NB + ": tu peux lui apprendre à chercher par le sens.",
                 "boutons": {"Activer la recherche par le sens": None, "Continuer": "verif"}},
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
        # Claude's login put off: the end asks for it first.
        "fini": {"titre": "Presque prêt" + NB + ": il manque ton compte Claude",
                 "texte": "Connecte-le pour que Claude puisse travailler avec ton cerveau. Il faut un abonnement Claude Pro ou Max.",
                 "boutons": {"Importer ma mémoire": "import-verif", "Voir mes notes": None, "Recherche par le sens": None,
                             "Connecter mon compte Claude": None}},
        # « Importer ma mémoire »: the robot goes to the copy-paste and back, no AI asked.
        "import-verif": {"titre": "Je prépare l’import…", "apres": (3, "import-choix")},
        "import-choix": {"titre": "D’où vient ta mémoire" + NB + "?",
                         "texte": "ChatGPT ou Claude savent déjà des choses sur toi. Claude en tire l’essentiel, je le range dans ton cerveau.",
                         "boutons": {"Retour": "fini", "Choisir mon fichier d’export": None, "Je copie-colle (2 min)": "import-collage"}},
        "import-collage": {"titre": "Demande-lui ce qu’il sait de toi", "texte": "Trois étapes, deux minutes. Rien ne part avant que tu valides.",
                           "boutons": {"Copier la phrase": None, "Ouvrir ChatGPT": None, "Ouvrir Claude": None, "Retour": "import-choix",
                                       "Continuer": None},
                           "textes": ["Copie cette phrase.", "Je change d'outil et j'ai besoin d'exporter mes données. Liste chaque souvenir…"]},
    }


def neuf_codex(installe):
    """The same Mac where the kit lets Codex be chosen (the gate open in the robot's copy): the welcome
    names no AI, the choice comes first, Codex's step and its ChatGPT login in Claude's place."""
    e = neuf(installe)
    e["bienvenue"] = {"titre": "Salut, moi c’est Bibo" + NB + "!",
                      "texte": "Je vais installer ton second cerveau pour ton IA, Claude ou Codex. 10 à 20 minutes, tu n’as aucune commande à taper.",
                      "boutons": {"Choisir mon IA": "choix"}}
    e["choix"] = {"titre": "Avec quelle IA veux-tu travailler" + NB + "?",
                  "texte": "Ton cerveau marche avec Claude, avec Codex, ou avec les deux.",
                  "boutons": {"Claude": "diag-en-cours", "Codex": "diag-en-cours", "Les deux": "resume"},
                  "textes": ["Mon conseil" + NB + ":", "Ton cerveau marche avec Claude" + NB + ": il te faudra un abonnement Claude Pro ou Max."]}
    # Where Codex can be chosen, the advice is on the choice screen.
    texte = "J’installe Codex, le moteur de ta mémoire, Obsidian et ton cerveau" + NB + ": 10 à 20 minutes. Branche-toi sur un Wi-Fi si tu peux."
    for k in ("diag", "diag-detail"):
        e[k] = dict(e[k], texte=texte, textes=[x for x in e[k]["textes"] if "conseil" not in x and "Pro ou Max" not in x],
                    boutons=dict(e[k]["boutons"], **{"Tout installer": "codex"}))
    e["resume"] = {"titre": "Lequel écrit le résumé de chaque soir" + NB + "?", "boutons": {"Retour": "choix", "Claude": None, "Codex": None}}
    e["import-choix"] = dict(e["import-choix"], texte="ChatGPT ou Claude savent déjà des choses sur toi. Codex en tire l’essentiel, je le range dans ton cerveau.")
    e["codex"] = {"titre": "J’installe Codex.", "texte": "Étape 1 sur 4. L’outil officiel d’OpenAI.", "apres": (30, "bun")}
    e["installe"] = dict(e["installe"], apres=(2, "verif-codex"))
    e["deja"] = dict(e["deja"], boutons={"Activer la recherche par le sens": None, "Continuer": "verif-codex"})
    e["verif-codex"] = {"titre": "Je vérifie la connexion de Codex…", "apres": (2, "login-codex")}
    e["login-codex"] = {"titre": "Dernière étape" + NB + ": ton compte ChatGPT",
                        "texte": "Connecte Codex à ton compte ChatGPT pour qu’il puisse travailler avec ton cerveau.",
                        "boutons": {"Plus tard": "fini", "Connecter Codex à mon compte ChatGPT": None}}
    # The ChatGPT login put off: Codex's login comes first, the onboarding after it (§4.3).
    e["fini"] = dict(e["fini"], titre="Ton second cerveau est prêt" + NB + "!", texte="Codex retient maintenant ce que tu lui apprends.",
                     boutons={"Importer ma mémoire": "import-verif", "Voir mes notes": None, "Recherche par le sens": None,
                              "Connecter Codex à mon compte ChatGPT": None},
                     textes=["Pense à connecter Codex à ton compte ChatGPT. S’il te le demande au premier lancement, "
                             "choisis « Sign in with ChatGPT », jamais une clé API : elle serait facturée."])
    return e


def guide_ecrans(etape_textes, via=None, colle_au_choix=False):
    """The guide's window (app/src/guide.js), as Bibo words it: next screen per button, or (seconds, next
    screen) by itself. via None: paste mode, a capture pasted on « coller-* » goes to « lecture ». via "clic"
    (Windows) or "selecteur" (macOS): Bibo takes the window in photo, shown by the user's gesture (montrer).
    colle_au_choix: paste mode where Bibo first waits for the window (X11), a capture pasted there instead."""
    e = guide_collage(etape_textes)
    if colle_au_choix:
        e["comment"]["boutons"]["J’ai compris"] = "choisir"
        e["choisir"] = guide_ecrans([], "clic")["choisir"]
        # The capture pasted before the goal: its preview once the goal is given.
        e["lecture-choix"] = {"titre": "Je lis ton image…", "apres": (1, "objectif")}
        e["objectif"] = dict(e["objectif"], boutons=dict(e["objectif"]["boutons"], Continuer="apercu"))
    if via is None:
        return e
    e["comment"]["boutons"]["J’ai compris"] = "choisir"
    if via == "clic":
        e["choisir"] = {"titre": "Montre-moi ta fenêtre.", "texte": "Clique une fois dans la fenêtre de ton logiciel : je la reconnais tout seul.",
                        "textes": ["Temps pour cliquer : 45 s"],
                        "boutons": {"Mon logiciel n’est pas encore ouvert": None, "J’ai déjà une capture": None, "Arrêter le guide": None,
                                    "Choisir dans une liste": None}, "montrer": "fenetre"}
        e["fenetre"] = {"titre": "C’est bien celle-ci ?", "textes": ["Atelier Demo", ta.TITRE_LOGICIEL],
                        "boutons": {"Arrêter le guide": None, "Non, une autre": "choisir", "Oui, c’est elle": "objectif"}}
        e["objectif"] = dict(e["objectif"], texte="Dis-moi ce que tu veux faire dans ton logiciel : je te guide une étape à la fois.")
    else:
        e["choisir"] = {"titre": "Montre-moi ta fenêtre.",
                        "texte": "Clique sur « Choisir la fenêtre » : ton Mac te montre tes fenêtres. Clique sur celle de ton logiciel, "
                                 "puis sur le bouton qui apparaît au milieu (en anglais : « Share This Window »).",
                        "boutons": {"J’ai déjà une capture": None, "Arrêter le guide": None, "Choisir la fenêtre": "selecteur"}}
        e["selecteur"] = {"titre": "Choisis ta fenêtre.", "texte": "Clique sur la fenêtre de ton logiciel, puis sur le bouton qui apparaît au milieu.",
                          "boutons": {"J’ai déjà une capture": None, "Arrêter le guide": None}, "montrer": "objectif"}
    e["objectif"] = dict(e["objectif"], boutons=dict(e["objectif"]["boutons"], Continuer="photo"))
    e["photo"] = {"titre": "Je prends ta fenêtre en photo…", "apres": (1, "apercu")}
    e["apercu"] = dict(e["apercu"], boutons={"Autre fenêtre": None, "Cacher une zone": None, "Envoyer à Claude": "regarde"})
    e["etape"] = dict(e["etape"], boutons={"Arrêter le guide": None, "Je suis bloqué": None, "C’est fait": "photo"})
    e["photo-noire"] = {"titre": "Ton logiciel ne me laisse pas le prendre en photo.", "texte": "Fais une capture toi-même et colle-la ici : je te montre comment.",
                        "boutons": {"Arrêter le guide": None, "Choisir une autre fenêtre": None}}
    e["apercu-meme"] = {"titre": "Ta fenêtre n’a pas changé depuis la dernière photo.", "texte": "As-tu fait l’étape dans ton logiciel ? Fais-la, puis reprends la photo.",
                        "boutons": {"Envoyer quand même": None, "Cacher une zone": None, "Reprendre la photo": "photo"}}
    return e


def guide_collage(etape_textes):
    return {
        "charge": {"titre": "Je prépare le guide…", "texte": "Je vérifie Claude Code sur ton Mac.", "apres": (1, "comment")},
        "comment": {"titre": "Comment je regarde ton écran", "texte": "Avant de commencer, quatre choses à savoir.",
                    "boutons": {"Voir mon réglage": None, "Pas maintenant": None, "J’ai compris": "objectif"}},
        "objectif": {"titre": "Qu’est-ce que tu veux faire" + NB + "?",
                     "texte": "Dis-moi dans quel logiciel, et ce que tu veux y faire : je te guide une étape à la fois.",
                     "boutons": {"Comment je regarde ton écran": None, "Arrêter le guide": None, "Continuer": "coller-debut"}},
        "coller-debut": {"titre": "Montre-moi ta fenêtre.", "texte": "Fais une capture de la fenêtre d’Atelier Demo, puis colle-la ici.",
                         "boutons": {"Arrêter le guide": None, "Retour": "objectif"}},
        "lecture": {"titre": "Je lis ton image…", "apres": (1, "apercu")},
        "apercu": {"titre": "Voici ce que Claude va voir.", "texte": "Vérifie qu’il n’y a rien que tu ne veux pas envoyer : tu peux cacher des zones.",
                   "boutons": {"Autre capture": None, "Cacher une zone": None, "Envoyer à Claude": "regarde"}},
        "regarde": {"titre": "Claude regarde ta fenêtre…", "texte": "5 secondes environ.", "apres": (3, "reponse")},
        "etape": {"titre": "Étape 1", "textes": etape_textes,
                  "boutons": {"Arrêter le guide": None, "Je suis bloqué": None, "C’est fait": "coller-fait"}},
        "coller-fait": {"titre": "Montre-moi où tu en es.", "texte": "Fais une capture de la fenêtre d’Atelier Demo, puis colle-la ici.",
                        "boutons": {"Arrêter le guide": None, "Retour": "etape"}},
        "fin": {"titre": "Bravo, c’est fait" + NB + "!", "texte": "Voici ce qu’on a fait ensemble.",
                "textes": ["Clique sur « Nouveau » en haut à gauche."],
                "boutons": {"Fermer le guide": None, "Nouvel objectif": None, "Enregistrer cette procédure": "brouillon"}},
        "brouillon": {"titre": "Je prépare la procédure…", "apres": (1, "enregistrer")},
        "enregistrer": {"titre": "Je l’écris dans ton cerveau" + NB + "?",
                        "texte": "Relis et corrige si tu veux : la prochaine fois, je m’en servirai.",
                        "boutons": {"Retour": None, "Enregistrer dans mon cerveau": "enregistre"}},
        "enregistre": {"titre": "Enregistré dans Documents › Brain › Guides.", "texte": "Je m’en servirai la prochaine fois.",
                       "boutons": {"Fermer le guide": None, "Nouvel objectif": None}},
    }


NOTE_GUIDE = ('---\nname: "guide-atelier-demo-faire-une-facture"\nversion: "1.0.0"\ndescription: "Faire une facture dans Atelier '
              'Demo, pas à pas (appris avec Bibo le 21/10/2026)."\nallowed-tools: []\ntriggers: ["Faire une facture dans Atelier Demo"]'
              '\nlogiciel: "Atelier Demo"\nsource: "bibo-guide"\n---\n# Faire une facture — Atelier Demo\n\n'
              '1. Clique sur « Nouveau » en haut à gauche.\n')


class FauxPilote(ta.Pilote):
    """The app's window, played from a table of screens on a fake clock."""

    machine = "Mac"
    suffixe = "_macos-universal.dmg"

    def __init__(self, horloge, home=None, change=None, codex=False):
        self.h = horloge
        self.home = home
        self.fait = False
        self.codex = codex
        self.sources = True     # the app unpacks the sources it was handed
        self.ecrans = (neuf_codex if codex else neuf)(lambda: self.fait)
        self.portes = []        # the gate of the kit's sources on disk, each time the app opens
        self.marqueurs = []     # whether the robot's marker was there, each time the app opens
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
        # The guide's window: its screen (None: not open), the keyboard and the pastes, the fake Claude's
        # calls (what the real one leaves in ~/.claude/fake), whether the step's capture shows the ring.
        self.guide, self.guide_depuis, self.saisies, self.appels = None, 0.0, [], 0
        self.anneau, self.trace, self.guide_prudent = True, None, False
        self.anneau_apercu, self.modeles_faux = False, None
        # The user's program, Bibo taking its window in photo: via None (paste mode), "clic" or "selecteur";
        # its photos, black, or the same after the step (the step not done in the program).
        self.via, self.photo_noire, self.pas_change = None, False, False
        # Bibo never sees the window shown; Bibo waits for the window, where the robot pastes (Linux).
        self.jamais_vue, self.colle_au_choix = False, False
        self.photos, self.etape_faite, self.gestes, self.programme = 0, False, [], None
        # The places of the buttons and the screen's usable area, as the Windows driver reads them: a function
        # (screen, label) -> [x, y, w, h] or None. None: no places (macOS, Linux).
        self.places, self.zone = None, None

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
        for rel in (".local/bin/codex" if self.codex else ".local/bin/claude", ".bun/bin/bun"):
            (home / rel).parent.mkdir(parents=True, exist_ok=True)
            (home / rel).write_text("x")
        (home / "Applications" / "Obsidian.app" / "Contents" / "MacOS").mkdir(parents=True)
        archive = self.envs[-1].get("BIAB_ARCHIVE")
        if archive and self.sources:
            src = home / ".brain-in-a-box" / "src"
            src.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive) as z:
                root = z.namelist()[0].split("/")[0]
                (src / ta.MARQUEUR).write_bytes(z.read(root + "/" + ta.MARQUEUR))

    def ouvrir(self, env):
        self.envs.append(env)
        gate = Path(self.home or "/nowhere") / ".brain-in-a-box" / "src" / "engine" / "hooks" / "_agents.py"
        line = re.search(r"^CODEX_OUVERT = .*$", gate.read_text(encoding="utf-8"), re.M) if gate.is_file() else None
        self.portes.append(line.group(0) if line else None)
        self.marqueurs.append((gate.parents[2] / ta.MARQUEUR).is_file())
        # Claude Code and the brain installed: the guide's welcome (Codex alone has no Claude Code), which asks
        # for Claude's login first unless the guide's fake Claude is there (it says it is logged in).
        connecte = self.home is not None and (Path(self.home) / ".claude" / "fake" / "scenario").is_file()
        self.aller(("retour" if connecte else "retour-sans-compte") if self.fait and not self.codex else "bienvenue")

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

    def guide_spec(self):
        textes = ["Clique sur « Nouveau », en haut à gauche, sous le titre.",
                  "Vérifie que le bouton s’appelle « Nouveau »." if self.guide_prudent else "",
                  ta.ANNEAU_POINTILLE if self.guide_prudent else ta.ANNEAU_PLEIN,
                  "Mode prudent : ton compte Claude ne me donne pas accès à Opus." if self.guide_prudent else "",
                  "1 capture envoyée à Claude, sur 40 pour cette fois."]
        ecrans = guide_ecrans([x for x in textes if x], self.via, self.colle_au_choix)
        spec = ecrans[self.guide]
        if "apres" in spec and self.h.now - self.guide_depuis >= spec["apres"][0]:
            nxt = spec["apres"][1]
            if self.guide == "photo":
                self.photos += 1
                nxt = "photo-noire" if self.photo_noire else "apercu-meme" if self.photos > 1 and not self.etape_faite else nxt
            self.aller_guide(("etape" if self.appels == 1 else "fin") if nxt == "reponse" else nxt)
            spec = ecrans[self.guide]
        return spec

    def aller_guide(self, etat):
        self.guide, self.guide_depuis = etat, self.h.now

    def lire_guide(self):
        if self.guide is None:
            return ta.Ecran()
        spec = self.guide_spec()
        tape = [x for k, x in self.saisies if k == "taper"]
        # The program's field filled in by Bibo from the window (Windows): the goal alone.
        attendu = [ta.OBJECTIF_GUIDE] if spec.get("texte", "").startswith("Dis-moi ce que tu veux faire dans ton logiciel") \
            else [ta.LOGICIEL_GUIDE, ta.OBJECTIF_GUIDE]
        boutons = [(b, not (self.guide == "objectif" and b == "Continuer" and tape != attendu)) for b in spec.get("boutons", {})]
        return ta.Ecran(fenetre=True, titre=spec["titre"], texte=spec.get("texte", ""), boutons=boutons, textes=spec.get("textes", []))

    def taper(self, texte):
        self.saisies.append(("taper", texte))

    def tabulation(self):
        self.saisies.append(("tab", None))

    def coller_image(self, png):
        self.saisies.append(("coller", hashlib.sha256(png).hexdigest()[:8]))
        if self.guide in ("coller-debut", "coller-fait"):
            self.aller_guide("lecture")
        elif self.guide == "choisir" and self.colle_au_choix:
            self.aller_guide("lecture-choix")

    def logiciel(self, dossier):
        if self.via:
            self.programme = FauxLogiciel(self, dossier)
        return self.programme

    def cliquer_guide(self, libelle):
        nxt = next((v for b, v in guide_ecrans([], self.via, self.colle_au_choix)[self.guide].get("boutons", {}).items()
                    if ta.norm(b) == ta.norm(libelle)), None)
        home = Path(self.home)
        fake = home / ".claude" / "fake"
        if libelle == "Envoyer à Claude":
            # What the fake Claude leaves of each call: its model, a piece of the image it got.
            self.appels += 1
            (fake / "fragments").mkdir(parents=True, exist_ok=True)
            modeles = (["opus", "sonnet"] if self.appels == 1 else ["sonnet"]) if self.guide_prudent else ["opus"]
            modeles = self.modeles_faux or modeles
            with open(fake / "models", "a") as f:
                f.write("".join(m + "\n" for m in modeles))
            for m in modeles:
                n = len(list((fake / "fragments").glob("*.json"))) + 1
                (fake / "fragments" / ("%d.json" % n)).write_text(json.dumps([{"raw": "ab%02d" % n * 8, "b64": "Zm9v%d" % n}]))
            if self.trace:
                self.trace.parent.mkdir(parents=True, exist_ok=True)
                self.trace.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 40)
        if libelle == "Enregistrer dans mon cerveau":
            (home / "Documents" / "Brain" / "Guides").mkdir(parents=True, exist_ok=True)
            (home / "Documents" / "Brain" / "Guides" / "atelier-demo-faire-une-facture.md").write_text(NOTE_GUIDE, encoding="utf-8")
        if libelle == "Fermer le guide":
            self.guide = None
        elif nxt:
            self.aller_guide(nxt)

    def lire(self):
        self.h.now += 0.5   # reading the window takes a moment
        self.pleines += 1
        if self.fenetre_titre == ta.GUIDE_TITRE:
            return self.lire_guide()
        if self.etat is None:
            return ta.Ecran()
        spec = self.spec()
        dialogues = [dict(self.dialogue)] if self.dialogue else []
        late = self.h.now - self.depuis < spec.get("boutons_apres", 0)   # the tree shows the words first
        boutons = [] if late else list(spec.get("boutons", {}))
        places = {b: self.places(self.etat, b) for b in boutons} if self.places else None
        return ta.Ecran(fenetre=True, titre=spec["titre"], texte=spec.get("texte", ""),
                        boutons=[(b, True) for b in boutons], textes=spec.get("textes", []),
                        dialogues=dialogues, places={b: r for b, r in (places or {}).items() if r}, zone=self.zone)

    def cliquer(self, libelle, processus=None):
        self.clics.append((processus, libelle) if processus else libelle)
        how = self.comment(libelle) if callable(self.comment) else self.comment
        if self.fenetre_titre == ta.GUIDE_TITRE:
            self.cliquer_guide(libelle)
            return how
        if libelle == ta.GUIDE and self.etat == "retour":
            self.aller_guide("charge")
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
        # The guide's preview and step: the pasted capture's green button, and on the step its red ring.
        if self.fenetre_titre == ta.GUIDE_TITRE and self.guide in ("apercu", "etape"):
            Path(chemin).write_bytes(capture_guide(self.anneau if self.guide == "etape" else self.anneau_apercu))
        else:
            Path(chemin).write_bytes(b"\x89PNG")
        self.captures.append(Path(chemin).name)

    def marquer_telecharge(self, fichier):
        return "0083;00000000;Safari;TEST"

    def installer(self, fichier, details, capturer=None):
        details["signature"] = "adhoc"
        return []

    outils = ta.PiloteMac.outils

    def racines_traces(self, home):
        return [Path(home)]

    def arbre(self):
        return {"app": [], "faux": True}


class FauxLogiciel:
    """The user's program for FauxPilote: the window shown on the wait for it moves the guide on, as Bibo
    sees it; « Nouveau » clicked in it changes what the next photo shows."""

    def __init__(self, pilote, dossier):
        self.p, self.via, self.dossier, self.titre = pilote, pilote.via, dossier, ta.TITRE_LOGICIEL
        self.ouvert, self.ferme = False, False

    def ouvrir(self):
        self.ouvert = True
        self.p.ignores = ("atelier-demo",)

    def montrer(self):
        self.p.gestes.append(("montrer", self.p.guide))
        nxt = guide_ecrans([], self.via).get(self.p.guide, {}).get("montrer")
        if nxt and not self.p.jamais_vue:
            self.p.aller_guide(nxt)
        return ta.CLIC_SOURIS

    def faire_etape(self):
        self.p.gestes.append(("Nouveau", self.p.guide))
        self.p.etape_faite = not self.p.pas_change
        return ta.CLIC_SOURIS

    def fermer(self):
        self.ferme = True


def capture_guide(anneau, pointille=False):
    """A capture of the screen with the guide's window: the pasted image's green button, shrunk, and,
    when `anneau`, Bibo's red ellipse round it (here a frame, dotted when `pointille`)."""
    e = ta.ecrans()
    t = e.Toile(600, 400, (240, 240, 240))
    t.rect(100, 100, 40, 14, ta.VERT)
    if anneau:
        for x in range(88, 152):
            for y in (90, 91, 122, 123):
                if not pointille or (x // 4) % 2 == 0:
                    t.rect(x, y, 1, 1, ta.ROUGE)
        for y in range(90, 124):
            for x in (88, 89, 150, 151):
                if not pointille or (y // 4) % 2 == 0:
                    t.rect(x, y, 1, 1, ta.ROUGE)
    return t.png()


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
        self.assertEqual(p.clics, ["Vérifier mon Mac", "Voir le détail", "Tout installer",
                                   "Plus tard", "Plus tard", "Importer ma mémoire", "Je copie-colle (2 min)", "Retour", "Retour"])
        self.assertGreater(took, 500)
        shots = p.captures
        # Search by meaning is no longer on the way: the install comes right after the diagnosis.
        self.assertEqual(shots[:4], ["01-bienvenue.png", "02-diagnostic-en-cours.png", "03-diagnostic.png", "04-etape-claude.png"])
        for name in ("etape-bun", "etape-obsidian", "etape-brain", "etape-brain-2min", "etape-brain-4min",
                     "etape-brain-6min", "connexion-installation", "connexion", "fini", "import-choix", "import-collage"):
            self.assertTrue(any(re.match(r"^\d\d-%s\.png$" % name, s) for s in shots), name)
        self.assertEqual(shots, sorted(shots))
        self.assertEqual(sorted(os.listdir(self.shots)), shots)
        self.assertEqual(self.t.vus[0], "app-lancement")
        self.assertNotIn("app-voyage", self.t.vus)
        for step in ("app-bienvenue", "app-diagnostic", "app-etape-claude", "app-connexion-installation",
                     "app-etape-brain", "app-connexion", "app-fini"):
            self.assertIn(step, self.t.vus)
        for step in self.t.vus:
            self.assertRegex(step, terrain.ETAPE_RE)
        self.assertIn("J'installe Claude Code, le moteur de ta mémoire, Obsidian et ton cerveau", self.t.details["diagnostic"]["bibo"])
        # The lines unfolded, read: what the VM had before the app.
        self.assertIn("Claude Code", self.t.details["diagnostic"]["textes"])
        b = pa.bilan()
        brain = next(e for e in b["ecrans"] if e["ecran"] == "etape-brain")
        self.assertGreaterEqual(brain["duree_s"], 400)
        self.assertEqual(b["bibo"]["titre"], "Presque prêt : il manque ton compte Claude")
        self.assertIn("app-import-collage", self.t.vus)
        self.assertEqual(self.t.details["conseil"], "Ton cerveau marche avec Claude : il te faudra un abonnement Claude Pro ou Max.")
        self.assertTrue(self.t.details["import"]["phrase"])

    def test_codex_chosen_alone_is_clicked_through_to_the_brain(self):
        p = FauxPilote(self.h, codex=True)
        pa = ta.Parcours(self.t, p, self.shots, horloge=self.h, dormir=self.h.dormir, codex=True)
        pa.premier_lancement({})
        self.assertEqual(p.clics, ["Choisir mon IA", "Codex", "Voir le détail", "Tout installer", "Plus tard",
                                   "Importer ma mémoire", "Je copie-colle (2 min)", "Retour", "Retour"])
        for step in ("app-bienvenue", "app-choix", "app-diagnostic", "app-etape-codex", "app-verif-compte-codex",
                     "app-connexion-chatgpt", "app-fini"):
            self.assertIn(step, self.t.vus)
        p.fermer()
        del p.clics[:]
        vus = pa.relance({})
        self.assertIn("deja-installe", vus)
        self.assertEqual(p.clics, ["Choisir mon IA", "Codex", "Continuer", "Plus tard"])
        # Opened again, the app asks again who the brain works with (« rouvre l'app et refais ce choix »).
        p.fermer()
        p.ecrans["bienvenue"] = dict(p.ecrans["bienvenue"], boutons={"Choisir mon IA": "diag-en-cours"})
        with self.assertRaises(terrain.Echec) as cm:
            pa.relance({})
        self.assertIn("au deuxième lancement, l'app n'a pas redemandé quelle IA", str(cm.exception))

    def test_each_path_fails_on_the_other_ones_screens(self):
        # The app users get keeps Codex closed: a choice asked on the path of Claude alone is a failure.
        p = FauxPilote(self.h, codex=True)
        with self.assertRaises(terrain.Echec) as cm:
            self.parcours(p).premier_lancement({})
        self.assertIn("n'offre pas le bouton « Vérifier mon Mac »", str(cm.exception))
        p = FauxPilote(Horloge(), codex=True)
        p.ecrans["bienvenue"]["boutons"] = {"Vérifier mon Mac": "choix"}
        with self.assertRaises(terrain.Echec) as cm:
            ta.Parcours(Noms(), p, self.shots, horloge=p.h, dormir=p.h.dormir).premier_lancement({})
        self.assertIn("écran « choix » : l'app livrée garde Codex fermé", str(cm.exception))
        # Codex alone: no Claude Code step, no Claude login, and the end names Codex.
        for change, said in (({"codex": {"titre": "J’installe Codex.", "texte": "Étape 1 sur 5.", "apres": (30, "claude")},
                               "claude": {"titre": "J’installe Claude Code.", "texte": "Étape 2 sur 5.", "apres": (30, "bun")}},
                              "l'app a installé Claude Code alors que seul Codex est choisi"),
                             ({"diag-detail": dict(neuf_codex(lambda: False)["diag-detail"], boutons={"Tout installer": "bun"})},
                              "le parcours n'est pas passé par l'écran « etape-codex »"),
                             ({"verif-codex": {"titre": "Je vérifie ton compte Claude…", "apres": (2, "login-codex")}},
                              "écran « verif-compte » : Codex est choisi seul"),
                             ({"verif-codex": {"titre": "Je vérifie la connexion de Codex…", "apres": (2, "login")}},
                              "écran « connexion » : Codex est choisi seul"),
                             ({"fini": {"titre": "Ton second cerveau est prêt" + NB + "!", "texte": "Claude retient maintenant ce que tu lui apprends.",
                                        "boutons": {"Voir mes notes": None, "Guide-moi dans un logiciel": None}}},
                              "l'écran final ne nomme pas Codex"),
                             ({"choix": {"titre": "Avec quelle IA veux-tu travailler" + NB + "?", "boutons": {"Codex": "resume"}}},
                              "écran « choix-resume » : Codex est choisi seul"),
                             ({"fini": {"titre": "Ton second cerveau est prêt" + NB + "!", "texte": "Codex retient maintenant ce que tu lui apprends.",
                                        "boutons": {"Voir mes notes": None, "Présente-toi à Codex (15 min)": None,
                                                    "Connecter Codex à mon compte ChatGPT": None},
                                        "textes": ["choisis « Sign in with ChatGPT »"]}},
                              "l'écran final propose de se présenter à un Codex qui n'est pas connecté à ChatGPT"),
                             ({"fini": {"titre": "Ton second cerveau est prêt" + NB + "!", "texte": "Codex retient maintenant ce que tu lui apprends.",
                                        "boutons": {"Voir mes notes": None, "Connecter Codex à mon compte ChatGPT": None},
                                        "textes": ["Pense à connecter Codex : il te le demandera au premier lancement."]}},
                              "l'écran final ne dit pas de choisir « Sign in with ChatGPT »")):
            h = Horloge()
            p = FauxPilote(h, codex=True, change=change)
            with self.subTest(said=said), self.assertRaises(terrain.Echec) as cm:
                ta.Parcours(Noms(), p, self.shots, horloge=h, dormir=h.dormir, codex=True).premier_lancement({})
            self.assertIn(said, str(cm.exception))

    def test_claude_alone_says_claude_where_it_always_did(self):
        # The words of before Codex: Claude named in the welcome and at the end (all of them: app/dev/screens.mjs).
        for change, said in (({"bienvenue": {"titre": "Salut, moi c’est Bibo" + NB + "!",
                                             "texte": "Je vais installer ton second cerveau pour ton IA. 10 à 20 minutes, tu n’as aucune commande à taper.",
                                             "boutons": {"Vérifier mon Mac": "diag-en-cours"}}},
                              "la bienvenue ne parle pas de Claude"),
                             # Claude's login put off: « prêt » and the onboarding would open Claude Code unconnected.
                             ({"fini": {"titre": "Ton second cerveau est prêt" + NB + "!", "texte": "Claude retient maintenant ce que tu lui apprends.",
                                        "boutons": {"Voir mes notes": None, "Importer ma mémoire": None, "Connecter mon compte Claude": None}}},
                              "l'écran final se dit prêt sans compte Claude"),
                             ({"fini": dict(neuf(lambda: False)["fini"], boutons={"Voir mes notes": None, "Importer ma mémoire": None,
                                                                                  "Présente-toi à Claude (15 min)": None, "Connecter mon compte Claude": None})},
                              "l'écran final propose de se présenter à Claude, qui n'est pas connecté")):
            h = Horloge()
            p = FauxPilote(h, change=change)
            with self.subTest(said=said), self.assertRaises(terrain.Echec) as cm:
                ta.Parcours(Noms(), p, self.shots, horloge=h, dormir=h.dormir).premier_lancement({})
            self.assertIn(said, str(cm.exception))

    def test_the_advice_and_the_import_are_on_the_screens(self):
        # The advice at the diagnosis (the choice screen where Codex is open), « Importer ma mémoire » at the
        # end, its copy-paste showing the sentence to paste: each missing one fails, named.
        diag, detail = neuf(lambda: False)["diag"], neuf(lambda: False)["diag-detail"]
        fini = neuf(lambda: False)["fini"]
        collage = neuf(lambda: False)["import-collage"]
        for codex, change, said in (
                (False, {"diag": dict(diag, textes=[]), "diag-detail": dict(detail, textes=["Claude Code", "À installer"])},
                 "le diagnostic ne donne pas « Mon conseil »"),
                (True, {"choix": dict(neuf_codex(lambda: False)["choix"], textes=[])}, "l'écran du choix ne donne pas « Mon conseil »"),
                (False, {"fini": dict(fini, boutons={k: v for k, v in fini["boutons"].items() if k != "Importer ma mémoire"})},
                 "l'écran final n'a pas le bouton « Importer ma mémoire »"),
                (False, {"import-collage": dict(collage, textes=["Copie cette phrase.", "Je prépare la phrase…"])},
                 "l'écran du copier-coller ne montre pas la phrase à coller"),
                (False, {"import-verif": {"titre": "Je prépare l’import…", "apres": (1, "a-jour")},
                         "a-jour": {"titre": "Ton cerveau doit d’abord être mis à jour",
                                    "texte": "Il a été installé avant l’import de mémoire.", "boutons": {"Retour": "fini"}}},
                 "l'app ne trouve pas l'import dans le cerveau qu'elle vient d'installer"),
                (False, {"import-verif": {"titre": "Je prépare l’import…", "apres": (1, "erreur")},
                         "erreur": {"titre": "Je ne peux pas importer pour l’instant.", "texte": "Python n’est pas là.",
                                    "boutons": {"Retour": "fini"}}},
                 "« Importer ma mémoire » s'arrête sur une erreur")):
            h = Horloge()
            p = FauxPilote(h, change=change, codex=codex)
            with self.subTest(said=said), self.assertRaises(terrain.Echec) as cm:
                ta.Parcours(Noms(), p, self.shots, horloge=h, dormir=h.dormir, codex=codex).premier_lancement({})
            self.assertIn(said, str(cm.exception))

    def test_the_sentence_may_come_a_moment_after_the_screen(self):
        # The window asks the kit for the sentence when the screen shows: the robot waits for it, not forever.
        h = Horloge()
        p = FauxPilote(h, change={"import-collage": {"titre": "Demande-lui ce qu’il sait de toi", "boutons": {"Retour": "import-choix"},
                                                     "textes": ["Je prépare la phrase…"], "apres": (6, "import-collage-2")},
                                  "import-collage-2": dict(neuf(lambda: False)["import-collage"])})
        ta.Parcours(Noms(), p, self.shots, horloge=h, dormir=h.dormir).premier_lancement({})
        self.assertEqual(p.clics[-4:], ["Importer ma mémoire", "Je copie-colle (2 min)", "Retour", "Retour"])

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
        # The slow one failed at the end of the install, before « Importer ma mémoire ».
        self.assertEqual(quick.clics, slow.clics + ["Importer ma mémoire", "Je copie-colle (2 min)", "Retour", "Retour"])

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
        self.assertEqual([c["bouton"] for c in pa.clics], ["Vérifier mon Mac", "Voir le détail", "Tout installer",
                                                           "Plus tard", "Plus tard", "Importer ma mémoire", "Je copie-colle (2 min)", "Retour", "Retour"])

    def test_opened_again_it_says_all_is_installed(self):
        p = FauxPilote(self.h)
        pa = self.parcours(p)
        pa.premier_lancement({})
        p.fermer()
        del p.clics[:]
        vus = pa.relance({})
        self.assertIn("deja-installe", vus)
        self.assertIn("bienvenue-retour", vus)
        self.assertEqual(p.clics, ["Vérifier mon Mac", "Continuer", "Plus tard"], "the guide is offered, not opened")
        self.assertIn("app-relance-deja-installe", self.t.vus)
        self.assertTrue(any(s.endswith("-relance-deja-installe.png") for s in p.captures))

    def test_opened_again_without_claude_the_welcome_asks_for_it_before_the_guide(self):
        # The welcome as it was: the guide first, though Claude is not logged in.
        avant = dict(neuf(lambda: True)["retour"])
        for retour, said in ((avant, "sans compte Claude, l'accueil ne demande pas « Connecter mon compte Claude » avant le guide"),
                             (None, "l'app ne dit pas « Content de te revoir »")):
            self.setUp()
            p = FauxPilote(self.h)
            pa = self.parcours(p)
            pa.premier_lancement({})
            p.fermer()
            p.ecrans["retour-sans-compte"] = retour or p.ecrans["bienvenue"]
            with self.assertRaises(terrain.Echec) as cm:
                pa.relance({})
            self.assertIn(said, str(cm.exception))

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

    def test_on_a_mac_a_failure_keeps_the_systems_log_of_the_app_and_the_picker(self):
        tmp = Path(tempfile.mkdtemp(prefix="journal-"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        mac = ta.PiloteMac(tmp)
        runs = []
        mac._run = lambda argv, timeout=120, **kw: runs.append([str(a) for a in argv]) or (0, "\n".join(map(str, range(500))))
        self.assertEqual(mac.journaux(), {"log_show": [str(i) for i in range(100, 500)]})
        self.assertEqual(runs[0][:6], ["/usr/bin/log", "show", "--last", "5m", "--style", "compact"])
        for who in ('process == "brain-in-a-box"', 'process == "ControlCenter"', 'subsystem == "com.apple.ScreenCaptureKit"'):
            self.assertIn(who, runs[0][-1])
        mac._run = lambda argv, timeout=120, **kw: (1, "refusé")
        self.assertEqual(mac.journaux(), {"log_show": ["code 1", "refusé"]})

    def test_a_click_that_changes_nothing_fails(self):
        p = FauxPilote(self.h, change={"bienvenue": {"titre": "Salut, moi c’est Bibo !", "boutons": {"Vérifier mon Mac": None}}})
        with self.assertRaises(terrain.Echec) as cm:
            self.parcours(p).premier_lancement({})
        self.assertIn("le clic sur « Vérifier mon Mac » ne change rien (3 essais)", str(cm.exception))
        self.assertEqual(p.clics, ["Vérifier mon Mac"] * 3)
        self.assertEqual(self.t.nom, "app-bienvenue")

    def test_a_missing_button_fails(self):
        detail = neuf(lambda: False)["diag-detail"]
        p = FauxPilote(self.h, change={"diag-detail": dict(detail, boutons={"Masquer le détail": "diag"})})
        with self.assertRaises(terrain.Echec) as cm:
            self.parcours(p).premier_lancement({})
        self.assertIn("l'écran « diagnostic » n'offre pas le bouton « Tout installer »", str(cm.exception))

    def test_search_by_meaning_on_the_way_to_the_brain_fails(self):
        # A bank card asked before anything is installed scared people off: it is an option of the end.
        p = FauxPilote(self.h, change={"diag-detail": dict(neuf(lambda: False)["diag-detail"], boutons={"Tout installer": "voyage"}),
                                       "voyage": {"titre": "Activons la recherche par le sens", "boutons": {"Retour": "claude"}}})
        with self.assertRaises(terrain.Echec) as cm:
            self.parcours(p).premier_lancement({})
        self.assertIn("l'app demande une clé Voyage avant la fin de l'installation", str(cm.exception))

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

    def test_the_main_button_is_whole_in_the_usable_area_of_the_screen(self):
        # The night's Windows VM: 1024 x 768, a taskbar 48 high. Each screen's buttons in a row at the bottom
        # of the window, as the app puts them.
        def fenetre(haut, hauteur):
            return lambda etat, b: [400 + 150 * list(p.ecrans[etat]["boutons"]).index(b), haut + hauteur - 60, 140, 40]
        p = FauxPilote(self.h)
        p.machine, p.zone = "PC", [0, 0, 1024, 720]
        p.ecrans["bienvenue"] = dict(p.ecrans["bienvenue"], boutons={"Vérifier mon PC": "diag-en-cours"})
        # Before: 780 x 680 at (60, 50), its title bar 31 high, Bibo's buttons 20 px under the taskbar.
        p.places = fenetre(50 + 31, 680)
        pa = self.parcours(p)
        with self.assertRaises(terrain.Echec) as cm:
            pa.premier_lancement({})
        self.assertIn("écran « bienvenue » : le bouton « Vérifier mon PC » sort de l'écran utile (21 px en bas)", str(cm.exception))
        self.assertTrue(p.captures[-1].endswith("-bienvenue-bouton-cache.png"), p.captures)
        # Now: 657 high, centered (lib.rs, place_main): every main button whole, each measured once.
        p, self.h.now = FauxPilote(self.h), 0.0
        p.machine, p.zone = "PC", [0, 0, 1024, 720]
        p.ecrans["bienvenue"] = dict(p.ecrans["bienvenue"], boutons={"Vérifier mon PC": "diag-en-cours"})
        p.places = fenetre(12 + 31, 657)
        pa = self.parcours(p)
        pa.premier_lancement({})
        self.assertEqual(sorted(pa.principaux), ["bienvenue", "connexion", "connexion-installation", "diagnostic", "fini"])
        self.assertEqual(pa.principaux["fini"]["bouton"], "Connecter mon compte Claude")
        self.assertEqual(pa.principaux["diagnostic"], {"bouton": "Tout installer", "place": [550, 640, 140, 40], "zone": [0, 0, 1024, 720]})
        self.assertEqual(pa.bilan()["boutons_principaux"], pa.principaux)
        # A driver without places (macOS, Linux) measures nothing and fails nothing.
        p, self.h.now = FauxPilote(self.h), 0.0
        pa = self.parcours(p)
        pa.premier_lancement({})
        self.assertEqual(pa.principaux, {})
        # Windows' driver must measure them: a tree that stops giving the places fails, it does not pass unmeasured.
        p, self.h.now = FauxPilote(self.h), 0.0
        p.machine, p.mesure_places = "PC", True
        p.ecrans["bienvenue"] = dict(p.ecrans["bienvenue"], boutons={"Vérifier mon PC": "diag-en-cours"})
        with self.assertRaises(terrain.Echec) as cm:
            self.parcours(p).premier_lancement({})
        self.assertIn("écran « bienvenue » : le bouton principal n'a pas été mesuré", str(cm.exception))

    def test_opened_twice_the_second_one_goes_by_itself(self):
        p = FauxPilote(self.h)
        p.ouvrir_encore = lambda env: 0.8
        self.parcours(p).premier_lancement({})
        self.assertEqual(self.t.details["deuxieme_ouverture_s"], 0.8)
        p, self.h.now = FauxPilote(self.h), 0.0
        p.ouvrir_encore = lambda env: None
        with self.assertRaises(terrain.Echec) as cm:
            self.parcours(p).premier_lancement({})
        self.assertIn("l'app ouverte une deuxième fois reste ouverte : deux Bibo à la fois", str(cm.exception))

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
            "Vérifier mon Mac", ta.DETAIL, "Tout installer", "Plus tard", "Continuer", "Présente-toi à", "Voir mes notes",
            "Importer ma mémoire", "Je copie-colle (2 min)", "Retour", ta.CONSEIL] + [
            b.replace("{machine}", "Mac") for bs in ta.PRINCIPAUX.values() for b in bs]
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

    def test_each_buttons_place_and_the_usable_area(self):
        page = page_uia()
        footer = [n for n in ta._aplatir(page)[::-1] if n[1].get("lt") == "content info"][0][1]
        footer["k"][0]["b"] = [552, 676, 160, 44]
        footer["k"][1]["b"] = [430, 676, 110, 0]     # a place without a height: none
        e = self.ecran(page)
        self.assertEqual(e.places, {"Vérifier mon PC": [552, 676, 160, 44]})
        e.zone = [0, 0, 1024, 720]
        self.assertEqual(ta.cache_du_bouton(e, "Vérifier mon PC"), "")
        e.zone = [0, 0, 1024, 700]
        self.assertEqual(ta.cache_du_bouton(e, "Vérifier mon PC"), "20 px en bas")
        e.places["Vérifier mon PC"] = [-8, -4, 160, 44]
        self.assertEqual(ta.cache_du_bouton(e, "Vérifier mon PC"), "4 px en haut, 8 px à gauche")
        self.assertIsNone(ta.cache_du_bouton(e, "Plus tard"))
        e.zone = None
        self.assertIsNone(ta.cache_du_bouton(e, "Vérifier mon PC"))

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
    def test_opened_a_second_time_as_a_user_does_and_killed_if_it_stays(self):
        p = FauxWindows(self.h, self.tmp)
        p.exe = r"C:\Users\r\AppData\Local\Brain in a Box\brain-in-a-box.exe"
        runs = []
        p._run = lambda argv, timeout=120, **kw: runs.append([str(a) for a in argv]) or (0, "")
        for fini, attendu in ((3.0, 3.0), (None, None)):
            second = FauxSetup(self.h)
            second.fini = None if fini is None else self.h.now + fini
            with mock.patch.object(ta.subprocess, "Popen", return_value=second) as popen:
                self.assertEqual(p.ouvrir_encore({"USERPROFILE": "C:\\Users\\r"}), attendu)
            self.assertEqual(popen.call_args[0][0], [p.exe])
            self.assertEqual(popen.call_args[1]["env"], {"USERPROFILE": "C:\\Users\\r"})
        self.assertEqual(runs, [["taskkill", "/PID", str(second.pid), "/T", "/F"]])

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
                     "arbre": page_uia()}], "devant": outil.devant, "zone": [0, 0, 1024, 720]}
            return {"ok": True, "comment": "Invoke"}
        p._outil = outil
        outil.devant = devant
        e = p.lire()
        self.assertEqual((e.titre, ta.cle_ecran(e)[0]), ("Salut, moi c'est Bibo !", "bienvenue"))
        self.assertEqual(e.zone, [0, 0, 1024, 720])
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
        self.assertEqual(f.clics, ["Vérifier mon PC", "Voir le détail", "Tout installer",
                                   "Plus tard", "Plus tard", "Importer ma mémoire", "Je copie-colle (2 min)", "Retour", "Retour"])
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

    def test_a_pc_without_git_on_its_path(self):
        env = {"ProgramFiles": "C:\\Program Files", "ProgramFiles(x86)": "C:\\Program Files (x86)",
               "LOCALAPPDATA": "C:\\Users\\r\\AppData\\Local"}
        roots = ta.racines_git(env)
        self.assertEqual(roots, ["C:\\Program Files\\Git", "C:\\Program Files (x86)\\Git", "C:\\Users\\r\\AppData\\Local\\Programs\\Git"])
        path = ("C:\\Program Files\\Git\\cmd;C:\\Windows\\system32;;c:\\program files\\git\\usr\\bin\\;C:\\Program Files\\GitHub CLI\\;"
                "C:\\Program Files\\Git;C:\\Users\\r\\AppData\\Local\\Programs\\Git\\cmd;\"C:\\Program Files\\Git\\bin\";C:\\Gitx")
        self.assertEqual(ta.sans_git_path(path, roots), "C:\\Windows\\system32;C:\\Program Files\\GitHub CLI\\;C:\\Gitx")
        self.assertEqual(ta.racines_git({}), [])

    def test_git_goes_before_the_first_start_when_the_workflow_asks(self):
        p = ta.PiloteWindows(Path(tempfile.mkdtemp(prefix="terrain-win-")), outil="pilote-windows.exe")
        self.addCleanup(shutil.rmtree, p.travail, True)
        calls = []
        p._run = lambda argv, timeout=120, env=None, creationflags=0: calls.append([str(a) for a in argv]) or (0, "ok\nGit introuvable\n")
        with mock.patch.dict(os.environ, {"PATH": "C:\\Program Files\\Git\\cmd;C:\\Windows"}, clear=False):
            os.environ.pop(ta.SANS_GIT, None)
            self.assertIsNone(p.poste_neuf())
            self.assertEqual((calls, os.environ["PATH"]), ([], "C:\\Program Files\\Git\\cmd;C:\\Windows"))
        env = {ta.SANS_GIT: "C:\\t\\sans-git.json", "PATH": "C:\\Program Files\\Git\\cmd;C:\\Windows", "ProgramFiles": "C:\\Program Files"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(p.poste_neuf(), {"sans_git": ["ok", "Git introuvable"]})
            self.assertEqual(os.environ["PATH"], "C:\\Windows", "nothing the robot starts finds the runner's Git")
        self.assertEqual(calls, [["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                                  str(ta.SANS_GIT_PS1), "-Preuve", "C:\\t\\sans-git.json"]])
        self.assertTrue(ta.SANS_GIT_PS1.is_file())
        p._run = lambda argv, timeout=120, env=None, creationflags=0: (1, "x\nGit est encore trouvable sur cette VM : where : C:\\Git\n")
        with mock.patch.dict(os.environ, env), self.assertRaises(ta.ErreurPilote) as cm:
            p.poste_neuf()
        self.assertIn("Git n'a pas quitté la VM (sans-git.ps1, code 1) : x Git est encore trouvable", str(cm.exception))

    def test_the_apps_diagnosis_saw_no_git(self):
        p = ta.PiloteWindows(Path(tempfile.mkdtemp(prefix="terrain-win-")), outil="pilote-windows.exe")
        self.addCleanup(shutil.rmtree, p.travail, True)
        textes = lambda statut: ["Python (pour les automatismes)", ": À installer", "x", ta.LIGNE_GIT, statut,
                                 "Pas encore installé. Je le prends chez Microsoft (winget)."]
        for statut, why in ((": À installer", None), (": OK", "le diagnostic de l'app disait Git « OK »"),
                            (None, "ne dit rien de Git")):
            t = Noms()
            details = {"diagnostic": {"textes": textes(statut) if statut else ["Python (pour les automatismes)", ": OK"]}}
            with mock.patch.dict(os.environ, {ta.SANS_GIT: "C:\\t\\sans-git.json"}):
                if why:
                    with self.assertRaises(terrain.Echec) as cm:
                        p.constats(t, details)
                    self.assertIn(why, str(cm.exception))
                else:
                    p.constats(t, details)
            self.assertEqual(t.vus, ["app-git-diagnostic"])
        t = Noms()
        with mock.patch.dict(os.environ, {}):
            os.environ.pop(ta.SANS_GIT, None)
            p.constats(t, {"diagnostic": {"textes": textes(": OK")}})
        self.assertEqual(t.vus, [], "a VM that keeps its Git checks nothing of it")
        # The app's own words: diagnose.rs' line for Git, app.js' word for what is missing.
        with open(os.path.join(REPO, "app", "src-tauri", "src", "diagnose.rs"), encoding="utf-8") as f:
            self.assertIn('Item("git", "%s", Some("brain"))' % ta.LIGNE_GIT, f.read())
        with open(os.path.join(REPO, "app", "src", "app.js"), encoding="utf-8") as f:
            self.assertIn("missing: '%s'" % ta.A_INSTALLER.lstrip(": "), f.read())

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


def atspi_page(statut="status bar", boutons=(("Revérifier", True), ("Tout installer", True)), decale=False):
    """The helper's tree of the diagnosis screen, as WebKitGTK gives it on Ubuntu 24.04 (recorded, cut).
    decale: as the AppImage built on 22.04 gives it there, some roles one below theirs (recorded)."""
    if decale:
        tree = json.loads(json.dumps(atspi_page(statut, boutons)))
        for _, n in ta._aplatir(tree):
            n["r"] = {"document web": "document text", "paragraph": "footer", "landmark": "article"}.get(n["r"], n["r"])
        return tree
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

    def test_the_22_04_appimage_s_roles_one_below_read_the_same_and_are_noted(self):
        # Its page reads « document text », its paragraphs « footer », its landmarks « article ».
        page = atspi_page(decale=True)
        e = ta.ecran_depuis_arbre([{"titre": "Brain in a Box", "arbre": ta.arbre_atspi(page)}])
        self.assertEqual((e.titre, e.texte, e.lecture), ("J'ai fait le tour.", "Il manque Claude Code, Bun et ton cerveau. Je m'en occupe.",
                                                         "statut"))
        self.assertEqual(e.boutons, [("Revérifier", True), ("Tout installer", True)])
        self.assertEqual(ta.roles_decales(page), {"article": "landmark", "document text": "document web", "footer": "paragraph"})
        self.assertEqual(ta.roles_decales(atspi_page()), {})
        self.assertIn('"document text"', ta.ATSPI, "the helper finds that page and its place too")

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

    def scripts(self):
        with open(ta.ROOT_RS, encoding="utf-8") as f:
            return ta.scripts_root(f.read())

    def test_root_rs_is_read_as_rustc_reads_it(self):
        s = self.scripts()
        self.assertEqual(s["PACKAGES"], ["python3", "git", "curl", "procps", "unzip", "ca-certificates"])
        self.assertTrue(s["APT_INSTALL"].startswith('# Brain in a Box installe :\n[ "$(id -u)" = 0 ] || { echo'), s["APT_INSTALL"][:80])
        self.assertTrue(s["APT_INSTALL"].endswith('\napt-get -o DPkg::Lock::Timeout=600 install -y -q -- "$@"\n'))
        self.assertIn('\nif ! echo "%s  $d/paquet.deb" | sha256sum -c --status -; then\necho "paquet.deb : somme SHA-256 inattendue, '
                      "rien n'est installé\" >&2\n" % s["OBSIDIAN_DEB_SHA256"], s["APT_INSTALL_DEB"])
        self.assertEqual((s["PKEXEC"], s["PACKAGES_LABEL"]), ("/usr/bin/pkexec", "Brain in a Box installe avec apt-get :"))
        # Rust's escapes and continued lines.
        rust = 'const A: &str = "a\\\\b\\"c\\n\\\n              d\\u{e9}\\x41\\t";\n'
        self.assertEqual(ta.scripts_root(rust)["A"], 'a\\b"c\ndéA\t')

    def test_root_ran_root_rs_scripts_to_the_letter_with_what_they_accept(self):
        s = self.scripts()
        home = "/home/runner"
        paquets = "/bin/sh -c %s %s " % (s["APT_INSTALL"], s["PACKAGES_LABEL"])
        deb = "/bin/sh -c %s %s " % (s["APT_INSTALL_DEB"], s["OBSIDIAN_LABEL"])
        chemin = "/home/runner/.brain-in-a-box/.biab-0123456789abcdef/obsidian.deb"
        self.assertEqual(ta.forme_root(paquets + "git curl", s, home), ("paquets", ["git", "curl"]))
        self.assertEqual(ta.forme_root(deb + chemin, s, home), ("obsidian", chemin))
        for autre in (paquets + "git; curl x | sh", paquets + "unzip -oDPkg::Pre-Invoke::=id", paquets + "openssh-server",
                      paquets.rstrip(), deb + "/tmp/.biab-0123456789abcdef/obsidian.deb", deb + chemin + " x",
                      deb.replace("sha256sum -c", "true", 1) + chemin, "/bin/sh -c x; apt-get install git curl",
                      paquets.replace("apt-get update", "curl e | sh; apt-get update", 1) + "git"):
            self.assertIsNone(ta.forme_root(autre, s, home), autre[-70:])
        # What the system's password window shows of them (pkexec 124), as root.rs's own test says.
        self.assertEqual(ta.ligne_mot_de_passe(paquets + "git curl"),
                         "/bin/sh -c # Brain in a Box installe : ...  Box installe avec apt-get : git curl")
        self.assertEqual(ta.ligne_mot_de_passe(deb + chemin),
                         "/bin/sh -c # Brain in a Box : Obsidian ... x/.biab-0123456789abcdef/obsidian.deb")
        self.assertEqual(ta.ligne_mot_de_passe("/usr/bin/true"), "/usr/bin/true")

    def test_app_root_wants_each_removed_package_and_obsidian_through_root_rs_and_their_window(self):
        s = self.scripts()
        paquets = "/bin/sh -c %s %s " % (s["APT_INSTALL"], s["PACKAGES_LABEL"])
        deb = "/bin/sh -c %s %s %s/.brain-in-a-box/.biab-0123456789abcdef/obsidian.deb" % (
            s["APT_INSTALL_DEB"], s["OBSIDIAN_LABEL"], Path.home())

        class Journal(ta.PiloteLinux):
            def __init__(self, cmds, fenetres=None):
                self.debut, self.cmds = 0, cmds
                self.fenetres = [ta.ligne_mot_de_passe(c) for c in cmds] if fenetres is None else fenetres

            def _run(self, argv, timeout=120, env=None, creationflags=0):
                if "pkexec" in argv:
                    return 0, "\n".join(json.dumps({"MESSAGE": "runner: Executing command [USER=root] [TTY=unknown] [CWD=/] "
                                                               "[COMMAND=%s]" % c}) for c in self.cmds)
                if ta.FENETRE in argv:
                    return 0, "\n".join(self.fenetres)
                raise AssertionError(argv)

        def constats(pilote, retires=("git", "curl")):
            t = Noms()
            t.details = {"poste_neuf": {"retires": list(retires)}}
            pilote.constats(t, t.details)
            return t

        t = constats(Journal([paquets + "git curl", deb]))
        self.assertEqual((t.nom, len(t.details["root"]), len(t.details["fenetre_mot_de_passe"])), ("app-root", 2, 2))
        self.assertEqual(t.notes, [])
        j = Journal([paquets + "git curl", deb])
        j.roles_decales = {"document text": "document web"}
        t = constats(j)
        self.assertEqual(t.notes, ["rôles d'accessibilité décalés d'un cran (« document text » pour « document web ») : "
                                   "un lecteur d'écran les annonce ainsi"])
        for cmds, retires, why in (([paquets + "git curl", deb], (), "l'étape « Outils de base » (pkexec, apt) n'a pas été testée"),
                                   ([paquets + "git", deb], ("git", "curl"), "pkexec n'a pas installé curl"),
                                   ([paquets + "git curl"], ("git", "curl"), "Obsidian installé par pkexec 0 fois"),
                                   ([paquets + "git curl", deb, deb], ("git", "curl"), "Obsidian installé par pkexec 2 fois"),
                                   ([paquets + "git curl", deb, "/bin/sh -c x; apt-get install git"], ("git",),
                                    "pkexec a lancé autre chose que les scripts de root.rs")):
            with self.assertRaises(terrain.Echec) as e:
                constats(Journal(cmds), retires)
            self.assertIn(why, str(e.exception))
        with self.assertRaises(terrain.Echec) as e:
            constats(Journal([paquets + "git curl", deb], fenetres=[ta.ligne_mot_de_passe(deb)]))
        self.assertIn("la fenêtre du mot de passe n'a pas montré « /bin/sh -c # Brain in a Box installe : ... ", str(e.exception))

    def test_the_appimage_mounts_with_a_setuid_fusermount_a_vm_without_one_gets_fuse3(self):
        tmp = Path(tempfile.mkdtemp(prefix="terrain-fuse-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        for d, n in (("a", "fusermount"), ("b", "fusermount3"), ("b", "other"), ("c", "fusermount")):
            (tmp / d).mkdir(exist_ok=True)
            (tmp / d / n).write_text("")
        real = os.stat
        setuid = {str(tmp / "b" / "fusermount3"), str(tmp / "b" / "other"), str(tmp / "c" / "fusermount")}

        def fake(p, *a, **k):
            return types.SimpleNamespace(st_uid=0, st_mode=0o104755) if str(p) in setuid else real(p, *a, **k)

        path = os.pathsep.join(str(tmp / d) for d in ("none", "a", "c", "b"))
        with mock.patch.object(ta.os, "stat", fake):
            self.assertEqual(ta.fusermount_setuid(path), [str(tmp / "c" / "fusermount"), str(tmp / "b" / "fusermount3")],
                             "setuid root only, fusermount* only, in the PATH's order")
            self.assertEqual(ta.fusermount_setuid(str(tmp / "a")), [])

        image = tmp / "Brain-in-a-Box_0.1.0_linux-x86_64.AppImage"
        image.write_bytes(b"\x7fELF\x02\x01\x01\x00AI\x02" + b"\0" * 64)

        class Vm(ta.PiloteLinux):
            """A server's image: libEGL and Mesa's EGL driver missing."""

            def __init__(self):
                self.calls = []

            def _run(self, argv, timeout=120, env=None, creationflags=0):
                if argv[0] == "dpkg-query":
                    return 0, "" if argv[-1] in ("libegl1", "libegl-mesa0") else "install ok installed"
                self.calls.append(argv)
                return 0, ""

        with mock.patch.object(ta.Path, "home", return_value=tmp), \
                mock.patch.object(ta, "fusermount_setuid", side_effect=[["/usr/bin/fusermount"], ["/usr/bin/fusermount",
                                                                                                    "/usr/bin/fusermount3"]]):
            vm, details = Vm(), {}
            notes = vm.installer(image, details)
        self.assertEqual(vm.calls, [["sudo", "-n", "apt-get", "install", "-y", "-q", "fuse3"],
                                    ["sudo", "-n", "apt-get", "update", "-q"],
                                    ["sudo", "-n", "apt-get", "install", "-y", "-q", "--no-install-recommends", "libegl1",
                                     "libegl-mesa0"]])
        self.assertEqual((details["fuse"], details["bureau"]), ("fuse3 installé par le robot (/usr/bin/fusermount, /usr/bin/fusermount3)",
                                                                ["libegl1", "libegl-mesa0"]))
        self.assertIn("qu'un bureau Ubuntu a (fuse3)", notes[0])
        self.assertIn("installées par le robot : libegl1, libegl-mesa0", notes[1])
        self.assertNotIn("libfuse2", json.dumps(vm.calls))

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
            # git's manual goes with git: the runner's is a PPA's, which would bar Ubuntu's git.
            removal = next(c for c in p.calls if "remove" in c)
            self.assertEqual(removal[-3:], ["git", "curl", "git-man"])
            self.assertNotIn("note", out)
            self.assertIn("update", p.calls[0], "the lists the app will see")
            self.assertIn("--only-upgrade", p.calls[1], "at their newest, which the lists hold")
            self.assertTrue(out["mis_a_jour"])
            # apt would not install curl's newest: curl goes back at its own version, the app installs git.
            p = Apt(("git",))
            out = p.poste_neuf()
            self.assertEqual(out["retires"], ["git"])
            self.assertIn("E: Unable to correct problems, you have held broken packages.", out["gardes"]["curl"])
            self.assertIn("apt n'y installerait pas sa version courante : curl", out["note"])
            self.assertIn(["sudo", "-n", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "-q",
                           "curl=8.5.0-2ubuntu10.16"], p.calls)
            # Neither: both back, the app's step goes untested, and the results say why.
            out = Apt(()).poste_neuf()
            self.assertEqual((out["retires"], sorted(out["gardes"])), ([], ["curl", "git"]))
            self.assertIn("git, curl ; son installation par l'app n'est pas testée", out["note"])
            # A version apt can no longer download (its PPA's source gone): it stays removed, the app says the rest.
            p = Apt(("curl",))
            real = p._run
            p._run = lambda argv, **kw: (100, "E: Can't find a source to download version") if any("=" in a for a in argv[5:]) \
                else real(argv, **kw)
            out = p.poste_neuf()
            self.assertEqual((out["retires"], out["gardes"]), (["git", "curl"], {}))
            self.assertIn("git", out["sans_retour"])

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

    def run_it(self, pilote=None, check=None, parcours="app", source="build", release=None):
        home = Path(self.tmp) / "home"
        home.mkdir(exist_ok=True)
        dmg = Path(self.tmp) / "Brain-in-a-Box_0.1.0-terrain.abc_macos-universal.dmg"
        dmg.write_bytes(b"dmg")
        sha = subprocess.run(["git", "-C", REPO, "rev-parse", "HEAD"], capture_output=True).stdout.decode().strip()
        opts = types.SimpleNamespace(home=home, json=str(Path(self.tmp) / "r.json"), parcours=parcours, vm=True,
                                     source=source, fichier=str(dmg), captures=str(self.shots))
        checked = []
        self.nights = []
        kit = types.SimpleNamespace(Echec=terrain.Echec, workdir=terrain.workdir, run=terrain.run, REPO=terrain.REPO,
                                    tested_ref=lambda: sha,
                                    check_install=check or (lambda t, o: (t("hooks"), checked.append(o.home))),
                                    run_nightly=lambda t, o: (t("nuit-reelle"), self.nights.append(list(p.nuit))))
        p = pilote or FauxPilote(self.h, home=home)
        p.home = home
        secrets = {"GH_TOKEN": "ghs_x", "GITHUB_TOKEN": "ghs_y", "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-z"}
        with mock.patch.dict(os.environ, secrets), mock.patch.object(ta, "depuis_release", release or ta.depuis_release):
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
        self.assertEqual(p.fermetures, 3, "the guide's launch closed too")
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

    def test_codex_chosen_alone_in_the_robots_copy_of_the_kit(self):
        def zipped_gate(env):
            with zipfile.ZipFile(env["BIAB_ARCHIVE"]) as z:
                text = z.read("brain-in-a-box-terrain/engine/hooks/_agents.py").decode("utf-8")
            return re.search(r"^CODEX_OUVERT = .*$", text, re.M).group(0)

        for source in ("build", "release"):
            with self.subTest(source=source):
                shutil.rmtree(Path(self.tmp) / "home", True)
                p = FauxPilote(Horloge(), home=Path(self.tmp) / "home", codex=True)
                self.h = p.h
                release = lambda t, pilote, dossier, token, details, sha=None: Path(dossier) / "app.dmg"
                row, p, checked, sha, _, home = self.run_it(pilote=p, parcours="app-codex", source=source, release=release)
                self.assertEqual(row["statut"], "ok", row["raison"])
                self.assertIn("Codex choisi seul", row["raison"])
                # The gate open in the robot's copy only: the archive the app gets, and its sources on
                # disk before its first start, without the marker it must bring itself.
                self.assertEqual([zipped_gate(env) for env in p.envs], [ta.PORTE_OUVERTE] * 2)
                self.assertEqual(p.portes, [ta.PORTE_OUVERTE] * 2)
                # The marker comes with the app's own install only: absent before its first start.
                self.assertEqual(p.marqueurs, [False, True])
                self.assertIn("Codex ouvert dans cette copie", row["details"]["sources_du_cerveau"])
                with zipfile.ZipFile(p.envs[0]["BIAB_ARCHIVE"]) as z, \
                        zipfile.ZipFile(ta.archive_testee(terrain, terrain.Etapes(), types.SimpleNamespace(home=home), sha)) as plain:
                    self.assertEqual(sorted(z.namelist()), sorted(plain.namelist()))
                    for name in plain.namelist():
                        if not name.endswith("/engine/hooks/_agents.py"):
                            self.assertEqual(z.read(name), plain.read(name), name)
                            self.assertEqual(z.getinfo(name).external_attr, plain.getinfo(name).external_attr, name)
                self.assertTrue(os.access(home / ".brain-in-a-box" / "src" / "install.sh", os.X_OK))
        # The app that installs from master's zip on the path of Claude alone gets no copy of the robot's.
        shutil.rmtree(Path(self.tmp) / "home", True)
        p = FauxPilote(Horloge(), home=Path(self.tmp) / "home")
        self.h = p.h
        row, p, _, _, _, home = self.run_it(pilote=p, source="release", release=lambda t, pi, d, tok, det, sha=None: Path(d) / "app.dmg")
        self.assertEqual(row["statut"], "ok", row["raison"])
        self.assertEqual([env.get("BIAB_ARCHIVE") for env in p.envs], [None, None, None], "the guide's launch too")
        self.assertEqual(p.portes, [None, None, None])

    def test_codex_alone_wants_codex_and_no_claude_code(self):
        p = FauxPilote(self.h, home=Path(self.tmp) / "home", codex=True)
        original = p.installer_le_kit
        p.installer_le_kit = lambda: (original(), (Path(p.home) / ".local" / "bin" / "claude").write_text("x"))
        row, _, _, _, _, _ = self.run_it(pilote=p, parcours="app-codex")
        self.assertEqual((row["statut"], row["etape"]), ("echec", "app-outils"))
        self.assertIn("Claude Code est installé alors que seul Codex est choisi", row["raison"])
        shutil.rmtree(Path(self.tmp) / "home", True)
        p = FauxPilote(Horloge(), home=Path(self.tmp) / "home", codex=True)
        self.h = p.h
        original2 = p.installer_le_kit
        p.installer_le_kit = lambda: (original2(), (Path(p.home) / ".local" / "bin" / "codex").unlink())
        row, _, _, _, _, _ = self.run_it(pilote=p, parcours="app-codex")
        self.assertEqual((row["statut"], row["etape"]), ("echec", "app-outils"))
        self.assertIn("Codex n'est pas installé", row["raison"])

    def test_codex_is_looked_for_where_the_pilots_system_puts_it(self):
        # The system of the window driven, not the one the harness runs on: OpenAI's installer puts
        # Codex in ~/.local/bin on a Mac or Linux, under LOCALAPPDATA on Windows.
        home = Path(self.tmp) / "home"
        local = Path(self.tmp) / "Local"
        self.assertEqual(FauxPilote(self.h, home=home).codex_installe(home), [home / ".local" / "bin" / "codex"])
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
            self.assertEqual(FauxWindows(self.h, self.tmp).codex_installe(home),
                             [local / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"])
        p = FauxPilote(self.h, home=home, codex=True)
        elsewhere = Path(self.tmp) / "ailleurs" / "codex"
        p.codex_installe = lambda h: [elsewhere]
        row, _, _, _, _, _ = self.run_it(pilote=p, parcours="app-codex")
        self.assertEqual((row["statut"], row["etape"]), ("echec", "app-outils"))
        self.assertIn("Codex n'est pas installé (%s)" % elsewhere, row["raison"])

    def test_the_gate_is_opened_in_the_line_it_is(self):
        with open(os.path.join(REPO, "engine", "hooks", "_agents.py"), encoding="utf-8") as f:
            text = f.read()
        opened = ta.ouvrir_porte(text)
        self.assertEqual([l for l in opened.splitlines() if l.startswith("CODEX_OUVERT")], [ta.PORTE_OUVERTE])
        self.assertEqual(len(opened.splitlines()), len(text.splitlines()))
        self.assertIsNone(ta.ouvrir_porte(text.replace("CODEX_OUVERT = frozenset", "CODEX_OUVERT=frozenset")))

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
        p.ecrans["diag"]["textes"] = ["Mon conseil :", "Ton cerveau marche avec Claude : il te faudra un abonnement Claude Pro ou Max.",
                                      "Version de macOS", ": OK", "macOS 26.6.2", "Outils Apple (git, python3)", ": OK",
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
        self.assertIn("gbq query ne trouve pas Profile/lessons.md (code 127) ; Bibo : « Presque prêt : il manque ton compte Claude",
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

    def test_the_app_s_environment_is_read_once_the_vm_lost_what_the_app_brings(self):
        # Windows: the session's PATH comes from the registry, read after Git left it.
        p = FauxPilote(self.h, home=Path(self.tmp) / "home")
        order = []
        p.poste_neuf = lambda: order.append("poste") or {"sans_git": ["Git introuvable"]}
        p.environnement = lambda home, extra=None: order.append("env") or ta.env_utilisateur(home, extra)
        row, _, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual(row["statut"], "ok", row["raison"])
        self.assertEqual(order, ["poste", "env"])
        p = FauxPilote(self.h, home=Path(self.tmp) / "home2")
        p.poste_neuf = lambda: (_ for _ in ()).throw(ta.ErreurPilote("Git n'a pas quitté la VM (sans-git.ps1, code 1) : où"))
        row, _, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual((row["statut"], row["etape"]), ("echec", "app-poste-neuf"))
        self.assertIn("Git n'a pas quitté la VM", row["raison"])

    def test_git_s_checks_join_the_summary(self):
        def check(t, o):
            t("git-bash")
            t.details["git"] = {"resume": "Git absent au départ, installé par winget (portée utilisateur)"}
        row, _, _, _, _, _ = self.run_it(check=check)
        self.assertEqual(row["statut"], "ok", row["raison"])
        self.assertIn("une nuit lancée par le planificateur ; Git absent au départ, installé par winget (portée utilisateur)",
                      row["raison"])

    def test_sources_the_app_did_not_take_fail(self):
        p = FauxPilote(self.h, home=Path(self.tmp) / "home")
        p.sources = False
        row, _, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual((row["statut"], row["etape"]), ("echec", "app-sources"))
        self.assertIn("BIAB_ARCHIVE ignoré", row["raison"])

class Guide(Scenario):
    """The guide's walk on the third launch (Claude alone): the fake window of guide.js's words."""

    def test_walked_to_the_procedure_its_ring_found_and_nothing_left(self):
        p = FauxPilote(self.h, home=Path(self.tmp) / "home")
        row, p, _, _, _, home = self.run_it(pilote=p)
        self.assertEqual(row["statut"], "ok", row["raison"])
        d = row["details"]["guide"]
        # The screens Bibo passes by itself (a second or two) may come and go between two readings.
        self.assertEqual([e for e in d["ecrans"] if e not in ta.ATTENTES_GUIDE],
                         ["comment", "objectif", "coller-debut", "apercu", "etape", "coller-fait", "apercu", "fin", "enregistrer",
                          "enregistre"])
        self.assertEqual(d["collages"], ["debut", "fait"])
        self.assertEqual([k for k, _ in p.saisies], ["taper", "tab", "taper", "coller", "coller"])
        self.assertEqual([x for k, x in p.saisies if k == "taper"], [ta.LOGICIEL_GUIDE, ta.OBJECTIF_GUIDE])
        self.assertEqual(len({x for k, x in p.saisies if k == "coller"}), 2, "two captures, not the same one twice")
        self.assertEqual(d["modeles"], ["opus", "opus"])
        self.assertEqual(d["note"], "atelier-demo-faire-une-facture.md")
        self.assertEqual((d["temoin"]["boutons_verts"], d["temoin"]["entoures"]), (1, []))
        self.assertEqual((d["etape"]["niveau"], len(d["etape"]["entoures"])), ("plein", 1))
        self.assertIn(ta.GUIDE, p.clics)
        for b in ("J'ai compris", "Continuer", "Envoyer à Claude", "C'est fait", "Enregistrer cette procédure",
                  "Enregistrer dans mon cerveau", "Fermer le guide"):
            self.assertIn(b, p.clics)
        self.assertIn("guide : deux captures collées, l'étape entourée, la procédure dans Guides/, aucune image", row["raison"])
        # The real Claude Code back, the fake gone.
        self.assertEqual((home / ".local" / "bin" / "claude").read_text(), "x")
        self.assertFalse((home / ".local" / "bin" / "claude.terrain-vrai").exists())

    def test_on_windows_bibo_photographs_the_program_clicked_in(self):
        # The night as a user of a program of their own: one click in its window, the program's name Bibo's,
        # the goal typed, two photos Bibo takes (none pasted), the step done in the program in between.
        p = FauxPilote(self.h, home=Path(self.tmp) / "home")
        p.via = "clic"
        row, p, _, _, _, home = self.run_it(pilote=p)
        self.assertEqual(row["statut"], "ok", row["raison"])
        d = row["details"]["guide"]
        self.assertEqual([e for e in d["ecrans"] if e not in ta.ATTENTES_GUIDE],
                         ["comment", "choisir", "fenetre", "objectif", "apercu", "etape", "apercu", "fin", "enregistrer", "enregistre"])
        self.assertEqual((d["capture"], d["collages"], d["gestes"]), ("clic", [], ["choisir", "Nouveau"]))
        self.assertEqual(p.gestes, [("montrer", "choisir"), ("Nouveau", "etape")])
        self.assertEqual(p.saisies, [("taper", ta.OBJECTIF_GUIDE)], "the program's name is Bibo's, from the window")
        self.assertEqual(d["saisie"], "clavier, le logiciel rempli par Bibo")
        for b in ("J'ai compris", "Oui, c'est elle", "Continuer", "Envoyer à Claude", "C'est fait"):
            self.assertIn(b, p.clics)
        self.assertEqual((d["etape"]["niveau"], len(d["etape"]["entoures"])), ("plein", 1))
        self.assertEqual(d["note"], "atelier-demo-faire-une-facture.md")
        self.assertIn("guide : la fenêtre d'Atelier Demo montrée d'un clic, deux photos prises par Bibo, l'étape entourée", row["raison"])
        self.assertTrue(p.programme.ouvert and p.programme.ferme)
        self.assertEqual(p.ignores, (), "the program's windows are dialogs again once it is closed")
        self.assertIn({"ecran": "logiciel", "bouton": "Nouveau"}, [{k: c[k] for k in ("ecran", "bouton")} for c in row["details"]["clics"]])

    def test_on_macos_the_window_is_picked_in_the_systems_picker(self):
        p = FauxPilote(self.h, home=Path(self.tmp) / "home")
        p.via = "selecteur"
        row, p, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual(row["statut"], "ok", row["raison"])
        d = row["details"]["guide"]
        self.assertEqual([e for e in d["ecrans"] if e not in ta.ATTENTES_GUIDE],
                         ["comment", "choisir", "objectif", "apercu", "etape", "apercu", "fin", "enregistrer", "enregistre"])
        self.assertIn("selecteur", d["ecrans"])
        self.assertIn("Choisir la fenêtre", p.clics)
        self.assertEqual(p.gestes, [("montrer", "selecteur"), ("Nouveau", "etape")])
        # A program in a browser: its name not found in the window, typed.
        self.assertEqual([x for k, x in p.saisies if k == "taper"], [ta.LOGICIEL_GUIDE, ta.OBJECTIF_GUIDE])
        self.assertEqual((d["capture"], d["collages"]), ("selecteur", []))
        self.assertIn("guide : la fenêtre d'Atelier Demo choisie dans le sélecteur de macOS, deux photos prises par Bibo", row["raison"])
        # No capture of the screen while the picker is out, as a user takes none then.
        self.assertFalse([c for c in row["details"]["captures"] if c.endswith("-guide-selecteur.png")], row["details"]["captures"])
        self.assertTrue([c for c in row["details"]["captures"] if c.endswith("-guide-objectif.png")])

    def test_where_bibo_could_take_the_window_linux_pastes(self):
        # The X11 of the night's VM: Bibo waits for a click, the robot pastes there (the paste is Linux's walk).
        p = FauxPilote(self.h, home=Path(self.tmp) / "home")
        p.colle_au_choix = True
        row, p, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual(row["statut"], "ok", row["raison"])
        d = row["details"]["guide"]
        self.assertEqual((d["capture"], d["collages"]), ("collage", ["debut", "fait"]))
        self.assertIn("choisir", d["ecrans"])
        self.assertIn("guide : deux captures collées, l'étape entourée", row["raison"])

    def test_a_black_photo_of_the_program_fails(self):
        p = FauxPilote(self.h, home=Path(self.tmp) / "home")
        p.via, p.photo_noire = "clic", True
        row, p, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual((row["statut"], row["etape"]), ("echec", "app-guide-photo-noire"))
        self.assertIn("la photo de la fenêtre d'Atelier Demo est noire", row["raison"])
        self.assertTrue(p.programme.ferme, "closed all the same")

    def test_the_step_not_done_in_the_program_fails(self):
        p = FauxPilote(self.h, home=Path(self.tmp) / "home")
        p.via, p.pas_change = "clic", True
        row, _, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual((row["statut"], row["etape"]), ("echec", "app-guide-apercu-meme"))
        self.assertIn("le clic sur « Nouveau » dans Atelier Demo n'a rien changé", row["raison"])

    def test_a_window_bibo_never_sees_shown_fails_on_its_screen(self):
        # The click in the window never seen: the gesture tried three times, then the night says so.
        p = FauxPilote(self.h, home=Path(self.tmp) / "home")
        p.via, p.jamais_vue = "clic", True
        row, p, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual((row["statut"], row["etape"]), ("echec", "app-guide-choisir"))
        self.assertIn("« montrer » ne change rien sur l'écran « choisir » du guide (3 essais)", row["raison"])
        self.assertEqual(len(p.gestes), 3)

    def test_without_opus_sonnet_and_its_dotted_ring(self):
        p = FauxPilote(self.h, home=Path(self.tmp) / "home")
        p.guide_prudent = True
        row, p, _, _, _, home = self.run_it(pilote=p)
        self.assertEqual(row["statut"], "ok", row["raison"])
        d = row["details"]["guide"]
        self.assertEqual((d["prudent"], d["modeles"], d["etape"]["niveau"]), (True, ["opus", "sonnet", "sonnet"], "pointille"))
        self.assertIn("l'étape entourée en pointillés (Sonnet, mode prudent)", row["raison"])
        self.assertTrue((home / ".claude" / "fake" / "refuse-opus").is_file())

    def test_no_ring_on_the_step_fails(self):
        p = FauxPilote(self.h, home=Path(self.tmp) / "home")
        p.anneau = False
        row, _, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual((row["statut"], row["etape"]), ("echec", "app-guide-etape"))
        self.assertIn("aucune ellipse rouge autour du bouton vert", row["raison"])

    def test_an_image_the_app_left_on_the_disk_fails(self):
        home = Path(self.tmp) / "home"
        p = FauxPilote(self.h, home=home)
        p.trace = home / "Library" / "Caches" / "com.braininbox.installer" / "WebKit" / "x.png"
        row, _, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual((row["statut"], row["etape"]), ("echec", "app-guide-disque"))
        self.assertIn("une image PNG dans un dossier de l'app", row["raison"])
        self.assertIn("~/Library/Caches/com.braininbox.installer/WebKit/x.png", row["raison"])

    def test_a_ring_before_any_answer_fails(self):
        p = FauxPilote(self.h, home=Path(self.tmp) / "home")
        p.anneau_apercu = True
        row, _, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual((row["statut"], row["etape"]), ("echec", "app-guide-apercu"))
        self.assertIn("une ellipse rouge sur l'aperçu, avant toute réponse de Claude", row["raison"])

    def test_the_fake_claude_asked_another_model_fails(self):
        p = FauxPilote(self.h, home=Path(self.tmp) / "home")
        p.modeles_faux = ["sonnet"]
        row, _, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual((row["statut"], row["etape"]), ("echec", "app-guide-modeles"))
        self.assertIn("le faux Claude a été lancé avec ['sonnet', 'sonnet'] (attendu : ['opus', 'opus'])", row["raison"])

    def test_the_cautious_mode_said_where_opus_answers_fails(self):
        p = FauxPilote(self.h, home=Path(self.tmp) / "home")
        spec = p.guide_spec
        said = lambda sp: dict(sp, textes=sp["textes"] + ["Mode prudent : ton compte Claude ne me donne pas accès à Opus."]) \
            if p.guide == "etape" else sp
        p.guide_spec = lambda: said(spec())
        row, _, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual((row["statut"], row["etape"]), ("echec", "app-guide-etape"))
        self.assertIn("l'étape dit à tort le mode prudent", row["raison"])


class GuideParts(unittest.TestCase):
    def test_the_ring_is_found_round_the_green_button_only_when_drawn(self):
        e = ta.ecrans()
        for anneau, pointille, entoure in ((True, False, 1), (True, True, 1), (False, False, 0)):
            w, h, px = e.lire_png(capture_guide(anneau, pointille))
            trouves = ta.anneaux(w, h, px)
            self.assertEqual((len(trouves), len(ta.entoures(trouves))), (1, entoure), (anneau, pointille, trouves))
        # Red elsewhere on the screen, or on two sides of the button only, is no ring round it.
        for rouge in ((300, 50, 80, 4), (80, 100, 6, 14), (80, 100, 6, 14, 150, 100, 6, 14)):
            t = e.Toile(600, 400, (240, 240, 240))
            t.rect(100, 100, 40, 14, ta.VERT)
            for i in range(0, len(rouge), 4):
                t.rect(*rouge[i:i + 4], ta.ROUGE)
            w, h, px = e.lire_png(t.png())
            self.assertEqual(ta.entoures(ta.anneaux(w, h, px)), [], rouge)

    def test_the_screens_and_the_words_the_robot_reads_are_the_guides(self):
        with open(os.path.join(REPO, "app", "src", "guide.js"), encoding="utf-8") as f:
            js = ta.norm(f.read())
        for start in [x[1] for x in ta.ECRANS_GUIDE + ta.PHOTO_RATEE]:
            self.assertIn(ta.norm(start), js, start)
        for word in ("J'ai compris", "Continuer", "Envoyer à Claude", "C'est fait", "Enregistrer cette procédure",
                     "Enregistrer dans mon cerveau", "Fermer le guide", "Refaire la capture", ta.PRUDENT,
                     "Sur l'image, « ${e.libelle} » est entouré en rouge.", ta.ANNEAU_POINTILLE,
                     ta.CHOISIR_LISTE, ta.CHOISIR_FENETRE, "Oui, c'est elle", "Réessayer", ta.PROGRAMME_CONNU):
            self.assertIn(ta.norm(word), js, word)
        # The paste's screen offers neither button of the wait for the window (cle_guide tells them apart so).
        self.assertIn(ta.norm("Choisir ma fenêtre"), js)
        self.assertEqual(ta.ANNEAU_PLEIN, "Sur l'image, « Nouveau » est entouré en rouge.")
        self.assertEqual(ta.REPONSES_GUIDE["debut"]["libelle"], "Nouveau")
        with open(os.path.join(REPO, "app", "src-tauri", "src", "lib.rs"), encoding="utf-8") as f:
            rs = f.read()
        self.assertIn('const GUIDE_TITLE: &str = "%s";' % ta.GUIDE_TITRE, rs)
        self.assertIn(".title(GUIDE_TITLE)", rs)
        with open(os.path.join(REPO, "app", "src", "styles.css"), encoding="utf-8") as f:
            css = f.read()
        self.assertIn("stroke: #%02x%02x%02x;" % ta.ROUGE, css)
        self.assertIn("border: 3px solid #%02x%02x%02x;" % ta.ROUGE, css)
        self.assertEqual(ta.VERT, ta.ecrans().VERT)

    def test_the_wait_for_the_window_and_the_paste_share_their_words_not_their_buttons(self):
        titre = "Montre-moi ta fenêtre."
        ecran = lambda *boutons: ta.Ecran(fenetre=True, titre=titre, boutons=[(b, True) for b in boutons])
        self.assertEqual(ta.cle_guide(ecran("Arrêter le guide", "Choisir dans une liste")), "choisir")
        self.assertEqual(ta.cle_guide(ecran("Arrêter le guide", "Choisir la fenêtre")), "choisir")
        self.assertEqual(ta.cle_guide(ecran("Arrêter le guide", "Choisir ma fenêtre")), "coller-debut")
        self.assertEqual(ta.cle_guide(ta.Ecran(fenetre=True, titre="Ton logiciel ne me laisse pas le prendre en photo.")), "photo-noire")
        self.assertEqual(ta.cle_guide(ta.Ecran(fenetre=True, titre="Choisis ta fenêtre dans la liste.")), "inconnu")
        self.assertEqual(ta.cle_guide(ta.Ecran(fenetre=True, titre="Choisis ta fenêtre.")), "selecteur")

    def test_the_windows_program_is_compiled_opened_and_clicked_by_name(self):
        tmp = Path(tempfile.mkdtemp(prefix="atelier-"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        h = Horloge()
        p = ta.PiloteWindows(tmp, outil="pilote-windows.exe", horloge=h, dormir=h.dormir)
        calls = []

        def run(argv, timeout=120, env=None, creationflags=0, entree=None):
            argv = [str(a) for a in argv]
            calls.append(argv)
            Path(next(a for a in argv if a.startswith("/out:"))[len("/out:"):]).write_bytes(b"MZ")
            return 0, ""

        def outil(args, timeout=120, entree=None):
            args = [str(a) for a in args]
            calls.append(args)
            if args[0] == "fenetres":
                # Its window shows on the second look.
                shown = len([c for c in calls if c[0] == "fenetres"]) > 1
                return {"ok": True, "fenetres": [{"h": 31, "pid": 4242, "titre": "Brain in a Box"}]
                        + ([{"h": 77, "pid": 4242, "titre": ta.TITRE_LOGICIEL}] if shown else [])}
            return {"ok": True, "comment": "clic souris"}
        p._run, p._outil = run, outil
        logiciel = p.logiciel(tmp / "logiciel")
        self.assertEqual(logiciel.via, "clic")
        proc = types.SimpleNamespace(pid=4242, poll=lambda: None, kill=lambda: calls.append(["kill"]), wait=lambda timeout: 0)
        with mock.patch.object(ta.subprocess, "Popen", return_value=proc) as popen:
            logiciel.ouvrir()
        d = tmp / "logiciel"
        self.assertEqual(calls[0][1:5], ["/nologo", "/target:winexe", "/optimize+", "/out:%s" % (d / "atelier-demo.exe")])
        self.assertTrue(all(ord(c) < 128 for c in (d / "atelier-demo.cs").read_text(encoding="ascii")))
        # The drawn list shrunk to 800 × 500: « Nouveau » at (230, 120), « Importer » at (374, 120), 130 × 40 each.
        self.assertEqual(popen.call_args[0][0], [str(d / "atelier-demo.exe"), ta.TITRE_LOGICIEL, str(d / "liste.png"), str(d / "facture.png"),
                                                 "40", "60", "800", "500", "144", "75", "81", "25", "234", "75", "81", "25"])
        self.assertEqual(ta.ecrans().lire_png((d / "liste.png").read_bytes())[:2], (1280, 800))
        self.assertEqual((logiciel.h, p.ignores), (77, ("atelier-demo",)))
        self.assertEqual(logiciel.montrer(), "clic souris")
        self.assertEqual(logiciel.faire_etape(), "clic souris")
        self.assertEqual([c for c in calls if c[0] == "cliquer"], [["cliquer", "77", "Importer", "souris"], ["cliquer", "77", "Nouveau", "souris"]])
        logiciel.fermer()
        self.assertEqual(calls[-1], ["kill"])

    def test_the_program_in_front_is_no_dialog_over_the_app(self):
        tmp = Path(tempfile.mkdtemp(prefix="devant-"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        h = Horloge()
        p = ta.PiloteWindows(tmp, outil="pilote-windows.exe", horloge=h, dormir=h.dormir)
        p.proc = types.SimpleNamespace(pid=7, poll=lambda: None)
        devant = {"h": 77, "pid": 4242, "classe": "WindowsForms10.Window.8.app.0.1", "exe": r"C:\r\logiciel\atelier-demo.exe",
                  "arbre": uia("Window", ta.TITRE_LOGICIEL, [uia("Button", "Importer"), uia("Button", "Nouveau")])}
        p._outil = lambda args, timeout=120, entree=None: {"ok": True, "fenetres": [], "devant": devant, "zone": [0, 0, 1024, 720]}
        self.assertEqual([d["processus"] for d in p.lire().dialogues], ["atelier-demo"])
        p.ignores = ("atelier-demo",)
        self.assertEqual(p.lire().dialogues, [])
        mac = ta.PiloteMac(tmp)
        asked = []
        mac._osa = lambda req, timeout=90: asked.append(req) or {"app": [], "dialogues": [], "devant": "Google Chrome"}
        mac.ignores = ("Google Chrome",)
        mac.lire()
        self.assertIn("Google Chrome", asked[0]["ignorer"])
        self.assertIn("ControlCenter", asked[0]["ignorer"], "macOS's picker is no dialog either")

    def test_on_a_mac_the_program_is_a_page_picked_in_the_systems_picker(self):
        tmp = Path(tempfile.mkdtemp(prefix="atelier-mac-"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        chrome = tmp / "Google Chrome"
        chrome.write_text("")
        p = ta.PiloteMac(tmp)
        asked = []
        frame = {"pos": [40, 60], "size": [800, 540]}
        share = {"n": "", "d": "", "pos": [380, 100], "size": [120, 28]}
        # Another small button of ControlCenter's, off the window, and a narrower one on it.
        ailleurs, etroit = {"n": "", "pos": [900, 300], "size": [160, 28]}, {"n": "x", "pos": [700, 90], "size": [20, 20]}
        replies = {"cadre": lambda r: frame, "boutons": lambda r: {"boutons": [] if len(asked) < 6 else [ailleurs, etroit, share]},
                   "souris": lambda r: {"ok": True}, "cliquer": lambda r: {"ok": True, "comment": "clic souris"}}
        p._osa = lambda req, timeout=90: asked.append(req) or replies[req["op"]](req)
        logiciel = p.logiciel(tmp / "logiciel")
        self.assertEqual(logiciel.via, "selecteur")
        proc = types.SimpleNamespace(pid=4242, poll=lambda: None, kill=lambda: None, wait=lambda timeout: 0)
        with mock.patch.object(ta, "CHROME_MAC", str(chrome)), mock.patch.object(ta.subprocess, "Popen", return_value=proc) as popen:
            logiciel.ouvrir()
        argv = popen.call_args[0][0]
        self.assertEqual(argv[0], str(chrome))
        for flag in ("--no-first-run", "--force-renderer-accessibility", "--window-position=40,60", "--window-size=800,540",
                     "--app=" + (tmp / "logiciel" / "atelier.html").as_uri(), "--user-data-dir=%s" % (tmp / "logiciel" / "profil")):
            self.assertIn(flag, argv)
        page = (tmp / "logiciel" / "atelier.html").read_text(encoding="utf-8")
        self.assertIn("<title>%s</title>" % ta.TITRE_LOGICIEL, page)
        self.assertIn('<button id=nouveau type=button style="left:144px;top:75px;width:81px;height:25px;background:rgb(20,160,60)', page)
        self.assertEqual(ta.VERT, (20, 160, 60))
        self.assertEqual(p.ignores, ("Google Chrome",))
        with mock.patch.object(ta.time, "sleep"):
            said = logiciel.montrer()
        self.assertEqual(said, "sélecteur de macOS : « Share This Window » (clic à 470, 114 ; boutons lus : "
                               "sans nom [900, 300, 160, 28], x [700, 90, 20, 20], sans nom [380, 100, 120, 28])")
        clicks = [r for r in asked if r["op"] == "souris" and r["clic"]]
        self.assertEqual(clicks, [{"op": "souris", "x": 380 + 120 * 0.75, "y": 114.0, "clic": True}])
        hovers = [r for r in asked if r["op"] == "souris" and not r["clic"]]
        self.assertTrue(hovers and all(40 < r["x"] < 840 and 60 < r["y"] < 600 for r in hovers), hovers)
        # macOS 14: a button of the window's own size over it, clicked where the pointer is.
        asked.clear()
        replies["boutons"] = lambda r: {"boutons": [dict(frame, n="")]}
        with mock.patch.object(ta.time, "sleep"):
            self.assertTrue(logiciel.montrer().startswith("sélecteur de macOS : la fenêtre, sous le sélecteur (clic à 440, 124 ;"))
        self.assertEqual([r for r in asked if r["op"] == "souris" and r["clic"]], [{"op": "souris", "x": 440.0, "y": 60 + 0.12 * 540, "clic": True}])
        self.assertEqual(logiciel.faire_etape(), "clic souris")
        self.assertEqual(asked[-1], {"op": "cliquer", "processus": "Google Chrome", "fenetre": None, "libelle": "Nouveau"})

    def test_the_traces_are_the_guides_images_and_pngs_of_the_app(self):
        tmp = Path(tempfile.mkdtemp(prefix="traces-"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        frag = b"\x01\x02morceau-de-capture\x03"
        old = tmp / "home" / "Library" / "Caches" / "com.braininbox.installer" / "vieux.png"
        old.parent.mkdir(parents=True)
        old.write_bytes(ta.PNG_SIGNATURE + frag)
        os.utime(old, (1000, 1000))
        (tmp / "home" / "Documents").mkdir(parents=True)
        (tmp / "home" / "Documents" / "note.md").write_bytes(b"rien")
        (tmp / "home" / "Documents" / "autre.png").write_bytes(ta.PNG_SIGNATURE)
        self.assertEqual(ta.traces_du_guide([tmp / "home"], 2000, [], [frag], ("com.braininbox.installer",)), [],
                         "older than the walk, or a PNG elsewhere than the app's folders")
        (tmp / "home" / "Documents" / "fuite.txt").write_bytes(b"avant " + frag + b" apres")
        cache = tmp / "home" / "AppData" / "Local" / "com.braininbox.installer" / "EBWebView" / "Default" / "Cache" / "f_0001"
        cache.parent.mkdir(parents=True)
        cache.write_bytes(b"entete" + ta.PNG_SIGNATURE + b"donnees")
        robot = tmp / "home" / "terrain-travail" / "copie.png"
        robot.parent.mkdir(parents=True)
        robot.write_bytes(ta.PNG_SIGNATURE + frag)
        got = sorted((Path(p).name, why) for p, why in ta.traces_du_guide(
            [tmp / "home", tmp / "home" / "Documents"], 2000, [tmp / "home" / "terrain-travail"], [frag], ("com.braininbox.installer",)))
        self.assertEqual(got, [("f_0001", "une image PNG dans un dossier de l'app"), ("fuite.txt", "un morceau d'une image du guide")])

    def test_the_note_must_be_the_guides_own(self):
        tmp = Path(tempfile.mkdtemp(prefix="note-"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        self.assertIsNone(ta.note_du_guide(tmp, 0)[0])
        (tmp / "Guides").mkdir()
        (tmp / "Guides" / "atelier-demo-faire-une-facture.md").write_text(NOTE_GUIDE, encoding="utf-8")
        self.assertEqual(ta.note_du_guide(tmp, 0)[0].name, "atelier-demo-faire-une-facture.md")
        (tmp / "Guides" / "atelier-demo-faire-une-facture.md").write_text(NOTE_GUIDE.replace('source: "bibo-guide"', ""), encoding="utf-8")
        note, why = ta.note_du_guide(tmp, 0)
        self.assertIsNone(note)
        self.assertIn('source: "bibo-guide"', why)

    def test_the_fake_claude_says_the_plan_the_guide_asks_for(self):
        # Only a subscription asks Claude (guide.rs, detect::login_of): a claude.ai login whose plan Claude
        # Code does not say stops the guide on « Le guide demande Claude Pro ou Max. ».
        for prudent, plan in ((False, "max"), (True, "pro")):
            tmp = Path(tempfile.mkdtemp(prefix="faux-claude-"))
            self.addCleanup(shutil.rmtree, str(tmp), True)
            home = tmp / "home"
            exe = home / ".local" / "bin" / "claude"
            exe.parent.mkdir(parents=True)
            exe.write_text("vrai", encoding="utf-8")
            ta.poser_faux_claude(home, exe, tmp, prudent)
            p = subprocess.run([sys.executable, "-I", str(ta.FAUX_CLAUDE), "auth", "status"], capture_output=True,
                               env=dict(os.environ, HOME=str(home), USERPROFILE=str(home)), timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr)
            said = json.loads(p.stdout)
            self.assertEqual((said["authMethod"], said.get("subscriptionType")), ("claude.ai", plan), prudent)

    @unittest.skipUnless(os.name == "nt", "the relay is compiled by the .NET Framework's csc, on Windows")
    def test_the_relay_hands_the_fake_claude_its_arguments_and_streams_as_they_are(self):
        tmp = Path(tempfile.mkdtemp(prefix="relais-"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        home = tmp / "home"
        exe = home / ".local" / "bin" / "claude.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"MZ vrai")
        vrai = ta.poser_faux_claude(home, exe, tmp, False)
        (home / ".claude" / "fake" / "scenario").write_text("plan-ok", encoding="utf-8")
        (home / ".claude" / "fake" / "fixtures").write_text(os.path.join(REPO, "app", "src-tauri", "tests", "fixtures", "repair"), encoding="utf-8")
        (home / ".claude" / "fake" / "answer.json").write_text("{}", encoding="utf-8")
        args = ["-p", "--tools", "", "--append-system-prompt", 'Tu es « Bibo ».\n"guillemets" \\ fin\\', "--settings", '{"a":true}']
        p = subprocess.run([str(exe)] + args, input="la question\n".encode("utf-8"), capture_output=True,
                           env=dict(os.environ, USERPROFILE=str(home), HOME=""), timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        self.assertIn(b'"subtype":"init"', p.stdout.replace(b" ", b""))
        fake = home / ".claude" / "fake"
        self.assertEqual(json.loads((fake / "argv.json").read_text(encoding="utf-8")), args)
        self.assertEqual((fake / "prompt.txt").read_text(encoding="utf-8"), "la question\n")
        ta.rendre_vrai_claude(exe, vrai)
        self.assertEqual(exe.read_bytes(), b"MZ vrai")


if __name__ == "__main__":
    unittest.main()
