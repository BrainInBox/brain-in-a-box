"""Reads an export of ChatGPT or Claude, or an answer pasted from one of them, as hostile data: in
memory and bounded, never extracted on disk, only the files it needs (an exact allow-list: never
the account's user.json, users.json, login_history.json, user_settings.json), the person's own
words only, each message cut, secrets masked before anything is kept. No AI here and no writing
into the vault: importer-memoire.py does both with the blocks this module gives it.

    lecture = lire_fichiers([fichier, ...], connues=set())   # 1 to 4 exports (.zip, or a .json of one)
    lecture = lire_collage(texte)                            # the answer copied from ChatGPT or Claude
    lecture.blocs(), lecture.compte, lecture.source, lecture.empreintes

No one publishes these formats; open-source readers of real exports document them (see the PR
that added the import). ChatGPT: conversations.json, or since June 2026 conversations-000.json...,
each conversation a tree of messages (mapping) read from current_node back to its root. Claude:
conversations.json (chat_messages), memories.json or memories/<uuid>.json, projects.json or
projects/<uuid>.json. Every field is optional: a form it does not know is counted, never fatal.

Not a hook: installed next to them, never registered in settings.json.
"""
import calendar
import codecs
import hashlib
import heapq
import json
import math
import os
import re
import stat
import struct
import sys
import time
import unicodedata
import zipfile
import zlib
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paste_guard import is_relayed  # noqa: E402
from _redact import MASK, find_secrets, redact  # noqa: E402
from _vault_write import clean_item  # noqa: E402

FICHIER_MAX = 8 << 30          # an export, as a file
ENTREES_MAX = 500000
# zipfile reads the whole central directory in memory and makes an object of each entry in it, as
# many as its size holds (46 bytes at least each), whatever number the end record says.
REPERTOIRE_MAX = ENTREES_MAX * 46
ENTREE_MAX = 1 << 30           # one file of the archive, as declared
RATIO_MAX = 200                # a JSON export deflates 5 to 15 times; a bomb, thousands
RATIO_DES = 1 << 20            # below this size, the ratio says nothing
LU_MAX = 3 << 30               # all the files read, inflated
ELEMENT_MAX = 16 << 20         # one conversation, in characters
VALEURS_MAX = 1000000          # its arrays, objects and strings: json builds each (a real one, < 350 000)
MORCEAU = 1 << 20
FICHIERS_MAX = 2000            # conversations-NNN.json, 100 conversations each
NOEUDS_MAX = 100000
CHIFFRES_MAX = 30              # Python 3.9.6 has no guard against int("9" * 10**6) (CVE-2020-10735)
PROFONDEUR_MAX = 200           # an export nests a dozen levels; json's own limit depends on the Python
MESSAGE_MAX = 1000
TITRE_MAX = 1000               # what of a title is looked at, its secrets masked, before it is cut to 100
MESSAGES_GARDES = 6            # the first and the last ones of the person, per conversation
CONVERSATION_MAX = 12000
MEMOIRE_MAX = 20000
PROJET_MAX = 2000
INCONNUE_MAX = 30000
MEMOIRES_MAX, MEMOIRES_TOTAL = 2000, 400000   # memories, instructions and projects kept, in blocks and characters
COLLAGE_MAX = 300000
COLLAGE_BLOC = 95000
SNIFF = 1 << 20
PLAFOND = 2000000              # the most recent conversations kept, in characters: 20 requests' worth

LECTURE = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
# The files read, by their name in the export (after a single folder that holds everything, when
# the export was unzipped and zipped again). Nothing else is ever opened.
NOMS = re.compile(r"(?:conversations(?:-\d{3,5})?|memories|projects)\.json"
                  r"|(?:memories|projects)/[0-9A-Za-z-]{8,64}\.json")
# What Claude's archive of account data holds, and nothing else of use.
COMPTE_CLAUDE = {"users.json", "login_history.json"}
# Refusals of a file that the other files given with it make up for.
LAISSES = ("compte", "pas_un_export", "liens")

MESSAGES = {
    "introuvable": "Je ne trouve pas ce fichier.",
    "lien": "Ce fichier est un lien ou n'est pas un fichier ordinaire : je ne le lis pas.",
    "pas_un_zip": "Ce fichier n'est ni une archive .zip lisible ni un export en .json.",
    "abime": "Cette archive est abîmée ou incomplète : télécharge l'export à nouveau.",
    "trop_gros": "Cette archive est trop grosse pour être lue sans risque.",
    "chiffre": "Cette archive est protégée par un mot de passe : je ne la lis pas.",
    "ambigu": "Cette archive contient deux fois le même fichier : je ne sais pas lequel croire.",
    "compte": "Ce fichier ne contient que les infos de ton compte Claude. Donne-moi les autres fichiers .zip "
              "de ton export (conversations, mémoire, projets).",
    "liens": "Ce fichier contient seulement des liens vers ton export Claude. Ouvre ces liens dans ton "
             "navigateur, connecté à Claude, télécharge les fichiers .zip, puis donne-les-moi. Je ne suis "
             "jamais ces liens moi-même.",
    "pas_un_export": "Je ne trouve ni conversation ni mémoire de ChatGPT ou de Claude dans ce fichier.",
    "illisible": "Je n'arrive pas à lire cet export : il n'est pas en UTF-8 ou son contenu est abîmé.",
    "melange": "Ces fichiers viennent de ChatGPT et de Claude : importe-les l'un après l'autre.",
    "collage_long": "C'est trop long pour un copier-coller : passe plutôt par le fichier d'export.",
    "vide": "Il n'y a rien à importer dans ce texte.",
}


class Refus(Exception):
    """An input the import does not take: `code` in a word, `message` in French for the person."""

    def __init__(self, code, detail=None):
        Exception.__init__(self, code)
        self.code, self.message, self.detail = code, MESSAGES[code], detail


# --------------------------------------------------------------------------- text

# Characters a person does not see and an AI reads: zero-width, bidi overrides, Unicode tags.
CACHES = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\U000e0000-\U000e007f]")
# Control, format, surrogate and private-use characters, line breaks and tabs aside.
INVISIBLES = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u00ad\u0600-\u0605\u061c\u06dd\u070f\u180e"
                        "\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\ufff9-\ufffb\ud800-\udfff\ue000-\uf8ff"
                        "\U000e0000-\U000e007f\U000f0000-\U0010ffff]")
CODE = re.compile(r"^\s*(?:[{}()\[\];]|def |class |import |from \S+ import |const |let |var |function\b|return\b|"
                  r"if \(|for \(|while \(|#include|public |private |<\/?[a-z][a-z0-9]*[\s>/]|\$ |>>> )|[;{}]\s*$")


def propre(texte):
    """(text in NFC without invisible nor control character but line breaks and tabs, how many of the
    characters that hide text from a person it held)."""
    t = unicodedata.normalize("NFC", texte.replace("\r\n", "\n").replace("\r", "\n"))
    caches = len(CACHES.findall(t))
    return INVISIBLES.sub("", t), caches


def masquer(texte):
    """(text with its secrets masked, how many): the patterns of what is sent, then the vault's."""
    t, n = redact(texte)
    spans = find_secrets(t)
    for s, e, _ in reversed(spans):
        t = t[:s] + MASK + t[e:]
    return t, n + len(spans)


def _code(texte):
    lignes = [l for l in texte.split("\n") if l.strip()]
    return len(lignes) >= 4 and 2 * sum(1 for l in lignes if CODE.search(l)) > len(lignes)


def _borne(texte, n):
    """texte cut at n characters, without the word the cut falls in: a secret is then masked whole
    afterwards, or not kept at all, never a piece of it too short for its pattern."""
    if len(texte) <= n:
        return texte
    return texte[:n] if texte[n].isspace() else re.sub(r"\S+\Z", "", texte[:n])


def _couper(texte, cap):
    texte = re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", texte)).strip()
    return texte if len(texte) <= cap else texte[:cap - 1].rstrip() + "…"


def titre(texte, cap=100, compte=None):
    """A title for a block header and a note: one line, plain, its secrets masked before it is cut
    (counted in compte["masques"]), no bracket, emphasis mark nor "<" (no tag of the prompt's)."""
    t, n = masquer(_borne(texte, TITRE_MAX)) if isinstance(texte, str) else ("", 0)
    if compte is not None:
        compte["masques"] += n
    return clean_item(re.sub(r"[\[\]*_|]", " ", t.replace("<", "‹")), cap) or "sans titre"


def _jour(quand):
    return time.strftime("%Y-%m-%d", time.gmtime(quand)) if quand else None


def _nombre(v):
    """Unix seconds as ChatGPT writes them, or None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 < v < 1e11:
        return None
    return float(v)


def _iso(v):
    """ISO 8601 as Claude writes it, as Unix seconds, or None."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}):(\d{2}))?", v) if isinstance(v, str) else None
    if not m:
        return None
    try:
        return float(calendar.timegm(time.strptime("%s-%s-%s %s:%s:%s" % tuple(g or "00" for g in m.groups()),
                                                   "%Y-%m-%d %H:%M:%S")))
    except (ValueError, OverflowError):
        return None


def cle(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8", "replace")).hexdigest()[:16]


# --------------------------------------------------------------------------- what was read

class Lecture:
    """What an export holds once read: the blocks that may be sent, and what was counted on the way.
    Conversations are kept in a bounded heap, the most recent ones whose text fits in `plafond`."""

    def __init__(self, connues=frozenset(), plafond=PLAFOND):
        self.source, self.connues, self.plafond = None, connues, plafond
        self.compte = Counter()
        self.empreintes = []
        self.tas, self.taille, self.seq, self.vues = [], 0, 0, set()
        self.jours = []                  # the first and last day of the conversations found
        self.memoires, self.taille_memoires = [], 0   # souvenirs, instructions, project memories, projects
        self.noms_projets = {}           # uuid: the project's name, as a title (masked)
        self.contexte = self.instructions = None   # ChatGPT's: the most recent of each, (t, ...)
        self.bio = {}

    def de(self, source):
        if self.source not in (None, source):
            raise Refus("melange")
        self.source = source

    def _texte(self, texte, cap):
        """(text safe to keep, characters hidden in it); masked before it is cut."""
        t, caches = propre(_borne(texte, cap * 4))
        t, n = masquer(t)
        self.compte["masques"] += n
        return _couper(t, cap), caches

    def _message(self, texte):
        if not isinstance(texte, str) or not texte.strip():
            return None
        t, caches = propre(_borne(texte, MESSAGE_MAX * 4))
        if is_relayed(t) or _code(t):
            self.compte["ecartes"] += 1
            return None
        t, n = masquer(t)
        self.compte["masques"] += n
        t = _couper(t, MESSAGE_MAX)
        return (t, caches) if t else None

    def conversation(self, source, cid, nom, quand, messages):
        """One conversation: the person's messages in order. Kept when recent enough to fit."""
        self.de(source)
        if not any(isinstance(m, str) and m.strip() for m in messages):
            return
        k = cle(source, cid if isinstance(cid, (str, int)) else "", quand or "")
        if k in self.vues:
            self.compte["doublons"] += 1   # the same conversation in two files of the export
            return
        self.vues.add(k)
        self.compte["conversations"] += 1
        jour = _jour(quand)
        if jour:
            self.jours = [min(self.jours[0], jour), max(self.jours[1], jour)] if self.jours else [jour, jour]
        if k in self.connues:
            self.compte["deja"] += 1
            return
        if self.tas and self.taille >= self.plafond - CONVERSATION_MAX and (quand or 0) <= self.tas[0][0]:
            self.compte["anciennes"] += 1   # older than all those kept, and no room left
            return
        # The first and the last messages of the person: each one looked at once at most.
        debut, fin, apres = [], [], 0
        for apres, m in enumerate(messages, 1):
            r = self._message(m)
            if r:
                debut.append(r)
            if len(debut) == MESSAGES_GARDES:
                break
        for m in reversed(messages[apres:]):
            if len(fin) == MESSAGES_GARDES:
                break
            r = self._message(m)
            if r:
                fin.append(r)
        gardes = debut + fin[::-1]
        if not gardes:
            return
        texte = _couper("\n".join("• " + t for t, _ in gardes), CONVERSATION_MAX)
        bloc = {"genre": "conversation", "titre": titre(nom, compte=self.compte), "date": jour, "cle": k,
                "texte": texte, "caches": sum(c for _, c in gardes), "quand": quand or 0}
        self.seq += 1
        heapq.heappush(self.tas, (quand or 0, self.seq, bloc))
        self.taille += len(texte)
        while self.taille > self.plafond and len(self.tas) > 1:
            _, _, vieux = heapq.heappop(self.tas)
            self.taille -= len(vieux["texte"])
            self.compte["anciennes"] += 1

    def memoire(self, source, genre, nom, texte, quand=None, cap=MEMOIRE_MAX, ident=None, projet=None):
        """A souvenir, instructions, a project or its memory: always sent first."""
        self.de(source)
        if not isinstance(texte, str) or not texte.strip():
            return
        if len(self.memoires) >= MEMOIRES_MAX or self.taille_memoires >= MEMOIRES_TOTAL:
            self.compte["memoires_en_trop"] += 1   # thousands of projects or memory files: the rest left
            return
        t, caches = self._texte(texte, cap)
        if not t:
            return
        k = cle(source, genre, ident or t)
        if k in self.connues:
            self.compte["deja"] += 1
            return
        self.compte["souvenirs" if genre in ("souvenir", "memoire_projet", "instructions") else "projets"] += 1
        self.memoires.append({"genre": genre, "titre": titre(nom, compte=self.compte), "date": _jour(quand), "cle": k,
                              "texte": t, "caches": caches, "quand": quand or 0})
        self.taille_memoires += len(t)
        if projet is not None:
            self.memoires[-1]["projet"] = projet   # names a project memory, here or in another zip

    def fin_chatgpt(self):
        """ChatGPT's saved memories and instructions, once every conversation is read: the most
        recent of each, the memories it saved along the way (bio) without repeats."""
        if self.contexte:
            self.memoire("chatgpt", "souvenir", "souvenirs enregistrés", self.contexte[1], self.contexte[0])
        if self.bio:
            texte = "\n".join("- " + t for t, _ in sorted(self.bio.items(), key=lambda x: x[1]))
            self.memoire("chatgpt", "souvenir", "souvenirs enregistrés en cours de conversation", texte,
                         max(self.bio.values()))
        if self.instructions:
            self.memoire("chatgpt", "instructions", "instructions personnalisées", self.instructions[1],
                         self.instructions[0])

    def blocs(self):
        """The memory blocks (instructions, souvenirs, project memories, projects), then the
        conversations kept, most recent first. No reference yet: the plan numbers them."""
        rang = {"instructions": 0, "souvenir": 1, "memoire_projet": 2, "projet": 3, "collage": 4}
        out = []
        for b in sorted(self.memoires, key=lambda b: (rang.get(b["genre"], 9), -b["quand"])) + \
                [b for _, _, b in sorted(self.tas, key=lambda x: (-x[0], -x[1]))]:
            b = dict(b)
            if b["genre"] == "memoire_projet" and b.get("projet") in self.noms_projets:
                b["titre"] = self.noms_projets[b["projet"]]
            out.append(b)
        return out

    @property
    def caches(self):
        return sum(1 for b in self.memoires if b["caches"]) + sum(1 for _, _, b in self.tas if b["caches"])


# --------------------------------------------------------------------------- JSON, in chunks

TROP_GROS, ILLISIBLE = object(), object()
_BLANC = re.compile(r"[ \t\r\n]*")
_SEP = re.compile(r"[ \t\r\n,]*")
_JETON = re.compile(r'[\[\]{}"]')
_CHAINE = re.compile(r'[^"\\]*(?:\\.[^"\\]*)*', re.S)
_SCALAIRE = re.compile(r'[^,\]}\s]*')


def _decodeur():
    def entier(s):
        if len(s) > CHIFFRES_MAX + 1:
            raise ValueError("number too long")
        return int(s)

    def flottant(s):
        if len(s) > 100:
            raise ValueError("number too long")
        return float(s)

    def constante(s):
        raise ValueError("not JSON: %s" % s[:10])
    return json.JSONDecoder(parse_int=entier, parse_float=flottant, parse_constant=constante)


class _Flux:
    def __init__(self, morceaux):
        self.it, self.buf, self.pos, self.fini = iter(morceaux), "", 0, False

    def plus(self, garde):
        """More text at the end, what is before `garde` dropped. False at the end of the text."""
        if not self.fini:
            for m in self.it:
                if m:
                    self.buf = self.buf[garde:] + m
                    return True
            self.fini = True
        return False


def _avance(buf, i, prof, chaine, haut=0, nb=0):
    """Scans buf from i: (where to go on from, depth, inside a string, end of the value or None,
    deepest depth seen, arrays, objects and strings opened)."""
    n = len(buf)
    while i < n:
        if chaine:
            j = _CHAINE.match(buf, i).end()
            if j >= n:
                return n, prof, True, None, haut, nb
            if buf[j] == "\\":            # a backslash last: the character it escapes is to come
                return j, prof, True, None, haut, nb
            chaine, i = False, j + 1
            if prof == 0:
                return i, 0, False, i, haut, nb
            continue
        m = _JETON.search(buf, i)
        if not m:
            return n, prof, False, None, haut, nb
        c, i = m.group(), m.end()
        if c == '"':
            chaine, nb = True, nb + 1
        elif c in "[{":
            prof, nb = prof + 1, nb + 1
            haut = max(haut, prof)
        else:
            prof -= 1
            if prof <= 0:
                return i, 0, False, i, haut, nb
    return i, prof, chaine, None, haut, nb


def _valeur(f, dec, limite):
    debut = i = f.pos
    prof, chaine, garde, haut, nb = 0, False, True, 0, 0
    scalaire = f.buf[debut] not in '[{"'
    while True:
        if scalaire:
            i = _SCALAIRE.match(f.buf, i).end()
            fin = (i if i > debut else debut + 1) if i < len(f.buf) else None
        else:
            i, prof, chaine, fin, haut, nb = _avance(f.buf, i, prof, chaine, haut, nb)
        if fin is not None:
            break
        if garde and (i - debut > limite or nb > VALEURS_MAX):
            garde = False                  # skipped as it streams: only the scanner's state goes on
        coupe = debut if garde else i
        if not f.plus(coupe):
            f.pos = len(f.buf)
            if scalaire and garde:
                fin = len(f.buf)
                break
            return ILLISIBLE if garde else TROP_GROS
        debut, i = debut - coupe, i - coupe
    f.pos = fin
    if not garde or fin - debut > limite or nb > VALEURS_MAX:
        return TROP_GROS   # json would build each of its values: memory many times the text's
    if haut > PROFONDEUR_MAX:
        return ILLISIBLE   # never handed to json, whose limit is the Python's (3.14: the C stack's)
    try:
        v, end = dec.raw_decode(f.buf, debut)
    except (ValueError, RecursionError):
        return ILLISIBLE
    return v if end == fin else ILLISIBLE


def valeurs(morceaux, limite=None):
    """The values of a JSON text read in chunks, one at a time in memory: each element of a top-level
    array, or the text's one value. A value past `limite` characters is skipped as it streams
    (TROP_GROS), as is one of more than VALEURS_MAX arrays, objects and strings; one that does not
    parse, holds a number of more than CHIFFRES_MAX digits or NaN, or is nested too deep is
    ILLISIBLE. ValueError when the text is empty."""
    limite = limite or ELEMENT_MAX
    f, dec = _Flux(morceaux), _decodeur()
    while True:
        f.pos = _BLANC.match(f.buf, f.pos).end()
        if f.pos < len(f.buf):
            break
        if not f.plus(f.pos):
            raise ValueError("empty")
        f.pos = 0
    tableau = f.buf[f.pos] == "["
    if tableau:
        f.pos += 1
    while True:
        if tableau:
            while True:
                f.pos = _SEP.match(f.buf, f.pos).end()
                if f.pos < len(f.buf):
                    break
                if not f.plus(f.pos):
                    return                 # an array never closed: what was read counts
                f.pos = 0
            if f.buf[f.pos] == "]":
                return
        yield _valeur(f, dec, limite)
        if not tableau:
            return


def _texte_de(flux, etat):
    """The text of a binary stream, in chunks, UTF-8 (a BOM is fine); UTF-16 or 32 is refused and
    invalid UTF-8 raises UnicodeDecodeError. etat counts what was read, against LU_MAX."""
    tete = b""
    while len(tete) < 4:
        b = flux.read(4 - len(tete))
        if not b:
            break
        tete += b
    if tete[:2] in (b"\xff\xfe", b"\xfe\xff") or tete[:4] == b"\x00\x00\xfe\xff" or \
            (len(tete) >= 2 and (tete[0] == 0 or tete[1] == 0)):
        raise UnicodeDecodeError("utf-8", tete, 0, len(tete), "UTF-16 or UTF-32")
    if tete.startswith(b"\xef\xbb\xbf"):
        tete = tete[3:]
    dec = codecs.getincrementaldecoder("utf-8")("strict")
    etat.lu(len(tete))
    yield dec.decode(tete)
    while True:
        b = flux.read(MORCEAU)
        if not b:
            break
        etat.lu(len(b))
        yield dec.decode(b)
    yield dec.decode(b"", final=True)


class _Etat:
    """What was inflated so far, against LU_MAX, and the progress the caller hears."""

    def __init__(self, total, progres):
        self.fait, self.total, self.progres, self.dit = 0, total, progres, 0

    def lu(self, n):
        self.fait += n
        if self.fait > LU_MAX:
            raise Refus("trop_gros")
        if self.progres and self.fait - self.dit >= 4 * MORCEAU:
            self.dit = self.fait
            self.progres(self.fait, self.total)


# --------------------------------------------------------------------------- the formats

def _parts(content):
    parts, out = content.get("parts"), []
    if isinstance(parts, list):
        for p in parts[:50]:
            if isinstance(p, str):
                out.append(p)
            elif isinstance(p, dict) and p.get("content_type") == "audio_transcription" \
                    and isinstance(p.get("text"), str):
                out.append(p["text"])
    elif isinstance(content.get("text"), str):
        out.append(content["text"])
    return "\n".join(x for x in out if x.strip())


def _branche(mapping, courant):
    """The messages of the branch ChatGPT shows: from current_node back to the root (or, without it,
    from the most recent leaf), in order; a cycle or a missing parent ends it."""
    if not (isinstance(courant, str) and isinstance(mapping.get(courant), dict)):
        feuilles = []
        for k, n in mapping.items():
            if isinstance(n, dict) and not n.get("children"):
                msg = n.get("message") if isinstance(n.get("message"), dict) else {}
                feuilles.append((_nombre(msg.get("create_time")) or 0, str(k)))
        courant = max(feuilles)[1] if feuilles else None
    chemin, vus = [], set()
    while isinstance(courant, str) and courant not in vus and len(vus) < NOEUDS_MAX:
        n = mapping.get(courant)
        if not isinstance(n, dict):
            break
        vus.add(courant)
        if isinstance(n.get("message"), dict):
            chemin.append(n["message"])
        courant = n.get("parent")
    chemin.reverse()
    return chemin


def _deballe(v):
    """The person's text of a custom instruction as an export writes it: "The user provided ...:
    ```text```", or older, the text alone. None when only OpenAI's words are there."""
    if not isinstance(v, str):
        return None
    m = re.search(r"```(.*)```", v, re.S)
    if m:
        return m.group(1)
    return None if v.lstrip().startswith("The user provided") else v


def _chatgpt(c, lecture):
    mapping = c.get("mapping")
    if not isinstance(mapping, dict):
        lecture.compte["formes_inconnues"] += 1
        return
    quand = _nombre(c.get("update_time")) or _nombre(c.get("create_time"))
    paroles = []
    for m in _branche(mapping, c.get("current_node")):
        author = m.get("author") if isinstance(m.get("author"), dict) else {}
        content = m.get("content") if isinstance(m.get("content"), dict) else {}
        meta = m.get("metadata") if isinstance(m.get("metadata"), dict) else {}
        kind, role = content.get("content_type"), author.get("role")
        t = _nombre(m.get("create_time")) or quand or 0
        contexte = meta.get("user_context_message_data")
        if kind == "model_editable_context":
            if isinstance(content.get("model_set_context"), str) and (not lecture.contexte or t >= lecture.contexte[0]):
                lecture.contexte = (t, content["model_set_context"])
        elif kind == "user_editable_context" or isinstance(contexte, dict):
            # The person's own words: bare in the metadata; in the content, wrapped in OpenAI's words
            # for its model, which go.
            if isinstance(contexte, dict):
                champs = [contexte.get("about_user_message"), contexte.get("about_model_message")]
            else:
                champs = [_deballe(content.get("user_profile")), _deballe(content.get("user_instructions"))]
            texte = "\n".join("%s : %s" % (k, v.strip()) for k, v in zip(("À propos de moi", "Comment répondre"),
                                                                          champs) if isinstance(v, str) and v.strip())
            if texte and (not lecture.instructions or t >= lecture.instructions[0]):
                lecture.instructions = (t, texte)
        elif role == "assistant" and m.get("recipient") == "bio":
            texte = _borne(_parts(content), MESSAGE_MAX)
            if texte.strip() and len(lecture.bio) < 2000:
                lecture.bio[texte] = max(t, lecture.bio.get(texte, 0))
        elif role == "user" and kind in ("text", "multimodal_text") \
                and not meta.get("is_visually_hidden_from_conversation"):
            paroles.append(_parts(content))
    lecture.conversation("chatgpt", c.get("conversation_id") or c.get("id"), c.get("title"), quand, paroles)


def _claude(c, lecture):
    msgs = c.get("chat_messages")
    if not isinstance(msgs, list):
        lecture.compte["formes_inconnues"] += 1
        return
    paroles = []
    for m in msgs:
        if not isinstance(m, dict) or m.get("sender") != "human":
            continue
        content = m.get("content")
        textes = [b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"
                  and isinstance(b.get("text"), str)] if isinstance(content, list) else []
        paroles.append("\n".join(textes) if textes else (m["text"] if isinstance(m.get("text"), str) else ""))
    quand = _iso(c.get("updated_at")) or _iso(c.get("created_at"))
    lecture.conversation("claude", c.get("uuid"), c.get("name"), quand, paroles)


def _conversation(c, lecture):
    if not isinstance(c, dict):
        lecture.compte["formes_inconnues"] += 1
    elif "mapping" in c:
        _chatgpt(c, lecture)
    elif "chat_messages" in c:
        _claude(c, lecture)
    else:
        lecture.compte["formes_inconnues"] += 1


def _au_mieux(v, lecture):
    """A memory file of a shape no reader documents: its long strings under keys that look like text,
    labelled by where they are, within INCONNUE_MAX."""
    pris, total, pile = [], 0, [(v, "", 0)]
    while pile and total < INCONNUE_MAX and len(pile) < 10000:
        x, chemin, prof = pile.pop()
        if prof > 20:
            continue
        if isinstance(x, dict):
            pile.extend((y, "%s.%s" % (chemin, str(k)[:40]) if chemin else str(k)[:40], prof + 1)
                        for k, y in reversed(list(x.items())[:500]))
        elif isinstance(x, list):
            pile.extend((y, "%s[%d]" % (chemin, i), prof + 1) for i, y in reversed(list(enumerate(x[:500]))))
        elif isinstance(x, str) and len(x.strip()) >= 40 and re.search(r"memor|content|text|markdown",
                                                                        chemin.rsplit(".", 1)[-1], re.I):
            pris.append("%s : %s" % (chemin, x.strip()))
            total += len(x)
    if pris:
        lecture.compte["formes_inconnues"] += 1
        lecture.memoire("claude", "souvenir", "mémoire de Claude (forme inconnue, lue au mieux)", "\n".join(pris),
                        cap=INCONNUE_MAX)


def _memoires_claude(v, lecture):
    connue = False
    for it in (v if isinstance(v, list) else [v])[:100]:
        if not isinstance(it, dict):
            continue
        if isinstance(it.get("conversations_memory"), str):
            connue = True
            lecture.memoire("claude", "souvenir", "mémoire de Claude", it["conversations_memory"])
        pm = it.get("project_memories")
        if isinstance(pm, dict):
            connue = True
            for uid, texte in list(pm.items())[:200]:
                lecture.memoire("claude", "memoire_projet", "projet", texte, ident="%s|%s" % (uid, texte), projet=str(uid))
    if not connue:
        _au_mieux(v, lecture)


def _projets_claude(v, lecture):
    for p in (v if isinstance(v, list) else [v])[:500]:
        if not isinstance(p, dict):
            continue
        if p.get("is_starter_project") is True:
            if p.get("uuid") is not None:
                lecture.noms_projets[str(p["uuid"])] = titre(p.get("name"))
            continue   # Claude's own example project
        nom = titre(p.get("name") if isinstance(p.get("name"), str) else "projet", compte=lecture.compte)
        if p.get("uuid") is not None:
            lecture.noms_projets[str(p["uuid"])] = nom
        lignes = ["Projet : " + nom]
        for label, champ in (("Description", "description"), ("Consignes du projet", "prompt_template")):
            if isinstance(p.get(champ), str) and p[champ].strip():
                lignes.append("%s : %s" % (label, _borne(p[champ], PROJET_MAX)))
        if len(lignes) > 1:
            lecture.memoire("claude", "projet", nom, "\n".join(lignes), _iso(p.get("created_at")), cap=2 * PROJET_MAX + 200,
                            ident=p.get("uuid"), projet=str(p["uuid"]) if p.get("uuid") is not None else None)


def _lire_entree(nom, morceaux, lecture):
    """One file of the export, by its name in the export."""
    if nom.startswith("projects"):
        for v in valeurs(morceaux):
            if v is TROP_GROS or v is ILLISIBLE:
                lecture.compte["trop_gros" if v is TROP_GROS else "illisibles"] += 1
            else:
                lecture.de("claude")
                _projets_claude(v, lecture)
    elif nom.startswith("memories"):
        lecture.de("claude")
        for v in valeurs(morceaux):
            if v is TROP_GROS or v is ILLISIBLE:
                lecture.compte["trop_gros" if v is TROP_GROS else "illisibles"] += 1
            else:
                _memoires_claude(v, lecture)
    else:
        for v in valeurs(morceaux):
            if v is TROP_GROS or v is ILLISIBLE:
                lecture.compte["trop_gros" if v is TROP_GROS else "illisibles"] += 1
            else:
                _conversation(v, lecture)


# --------------------------------------------------------------------------- the archive

def _nom_sur(nom):
    """A name of the archive the import may look at: relative, with plain parts only."""
    return not (not nom or nom.startswith("/") or "\\" in nom or ":" in nom or re.search(r"[\x00-\x1f\x7f]", nom)
                or any(p in ("", ".", "..") for p in nom.rstrip("/").split("/")))


def _fin_du_zip(f):
    """The central directory that zipfile is about to read in memory, bounded before it does: its size
    as zipfile's own _EndRecData finds it, the function ZipFile() calls first, Zip64 record included
    as this Python reads it (whether or not a 32-bit field calls for one). Refus otherwise."""
    try:
        fin = zipfile._EndRecData(f)
    except (zipfile.BadZipFile, OSError, ValueError, EOFError, struct.error):
        fin = None
    if not fin:
        raise Refus("abime")   # it starts as a zip: a download cut short
    if fin[zipfile._ECD_SIZE] > REPERTOIRE_MAX or fin[zipfile._ECD_ENTRIES_TOTAL] > ENTREES_MAX:
        raise Refus("trop_gros")
    f.seek(0)


def _entrees(zf, lecture):
    """{name in the export: ZipInfo} of the files read; refused when one comes twice, is encrypted,
    too big or a bomb; those of another kind (a link, another compression) are counted and left."""
    noms = []
    for info in zf.infolist():
        nom = info.orig_filename
        if nom.endswith("/"):
            continue
        if not _nom_sur(nom):
            lecture.compte["noms_refuses"] += 1
            continue
        if nom.startswith("__MACOSX/") or nom.rsplit("/", 1)[-1] in (".DS_Store", "Thumbs.db"):
            continue
        noms.append((nom, info))
    choisis = [(n, i) for n, i in noms if NOMS.fullmatch(n)]
    tous = [n for n, _ in noms]
    if not choisis and noms and all("/" in n for n, _ in noms) and len({n.split("/", 1)[0] for n, _ in noms}) == 1:
        racine = noms[0][0].split("/", 1)[0] + "/"
        tous = [n[len(racine):] for n, _ in noms]
        choisis = [(n[len(racine):], i) for n, i in noms if NOMS.fullmatch(n[len(racine):])]
    if not choisis:
        if any(n.rsplit("/", 1)[-1] in COMPTE_CLAUDE for n in tous):
            raise Refus("compte")
        raise Refus("pas_un_export")
    pris = {}
    for n, info in choisis:
        if n in pris:
            raise Refus("ambigu", n)
        if info.flag_bits & 0x1:
            raise Refus("chiffre")
        if stat.S_ISLNK(info.external_attr >> 16):
            lecture.compte["liens"] += 1
            continue
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            lecture.compte["illisibles"] += 1
            continue
        if info.file_size > ENTREE_MAX or (info.file_size > RATIO_DES and
                                           info.file_size > RATIO_MAX * max(info.compress_size, 1)):
            raise Refus("trop_gros", n)
        pris[n] = info
    if sum(1 for n in pris if n.startswith("conversations")) > FICHIERS_MAX or \
            sum(i.file_size for i in pris.values()) > LU_MAX:
        raise Refus("trop_gros")
    # Entries that share their data (CVE-2024-0450, a bomb Python 3.9 does not see): never two
    # files read from the same bytes.
    spans = sorted((i.header_offset, i.header_offset + 30 + i.compress_size) for i in pris.values())
    if any(b[0] < a[1] for a, b in zip(spans, spans[1:])):
        raise Refus("abime")
    return pris


def _ordre(nom):
    return (0 if nom.startswith("projects") else 1 if nom.startswith("memories") else 2, nom)


def _lire_zip(f, lecture, progres):
    _fin_du_zip(f)
    try:
        zf = zipfile.ZipFile(f)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, ValueError, EOFError, OSError,
            struct.error, UnicodeDecodeError, RecursionError):
        raise Refus("abime")
    with zf:
        pris = _entrees(zf, lecture)
        etat = _Etat(sum(i.file_size for i in pris.values()), progres)
        for nom in sorted(pris, key=_ordre):
            info = pris[nom]
            try:
                with zf.open(info) as flux:
                    _lire_entree(nom, _texte_de(flux, etat), lecture)
            except (UnicodeDecodeError, ValueError):
                lecture.compte["illisibles"] += 1   # not UTF-8, or not JSON at all
            except (zipfile.BadZipFile, zlib.error, EOFError, OSError, NotImplementedError, RuntimeError):
                raise Refus("abime", nom)   # a CRC that fails, data shorter or longer than declared


def _flairer(f):
    """The name a .json export plays (conversations.json, memories.json...), from its first bytes."""
    debut = f.read(SNIFF)
    f.seek(0)
    t = debut.decode("utf-8", "ignore")
    if '"mapping"' in t or '"chat_messages"' in t:
        return "conversations.json"
    if '"conversations_memory"' in t or '"project_memories"' in t:
        return "memories.json"
    if '"prompt_template"' in t or '"is_starter_project"' in t:
        return "projects.json"
    if re.search(r"https?://", t):
        raise Refus("liens")
    raise Refus("pas_un_export")


def empreinte(f):
    """SHA-256 of a whole file, read in chunks."""
    h = hashlib.sha256()
    f.seek(0)
    while True:
        b = f.read(MORCEAU)
        if not b:
            break
        h.update(b)
    f.seek(0)
    return h.hexdigest()


def ouvrir(chemin):
    """A file the person named, opened for reading without following a link: a regular file, at
    most FICHIER_MAX. Refus otherwise."""
    if os.path.islink(chemin):
        raise Refus("lien")
    try:
        fd = os.open(chemin, LECTURE)
    except (OSError, ValueError):
        raise Refus("introuvable")
    return _fichier(fd)


def depuis_fd(fd):
    """A file the app opened and handed over as stdin (it never gives a path): regular, seekable."""
    return _fichier(os.dup(fd))


def _fichier(fd):
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise Refus("lien")
        if st.st_size > FICHIER_MAX:
            raise Refus("trop_gros")
        f = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    f.seek(0)
    return f


def lire_fichiers(fichiers, connues=frozenset(), plafond=PLAFOND, progres=None, lecture=None):
    """Reads 1 to 4 exports (open binary files, seekable): .zip archives, or the .json of one. Returns a
    Lecture, or raises Refus. connues: the keys of what an earlier import already took."""
    lecture = lecture or Lecture(connues, plafond)
    for f in fichiers:
        f.seek(0, os.SEEK_END)
        taille = f.tell()
        f.seek(0)
        if taille > FICHIER_MAX:
            raise Refus("trop_gros")
        lecture.empreintes.append(empreinte(f))
        tete = f.read(4)
        f.seek(0)
        try:
            if tete[:2] == b"PK":
                _lire_zip(f, lecture, progres)
                continue
            if taille > LU_MAX:
                raise Refus("trop_gros")
            nom = _flairer(f)
        except Refus as e:
            # Given with the rest, a file of no use is left: Claude's account data, or one of an
            # export the browser unzipped (user.json...). Refused before anything of it was read.
            if e.code not in LAISSES or len(fichiers) == 1:
                raise
            lecture.compte["compte_seul" if e.code == "compte" else "laisses"] += 1
            continue
        try:
            _lire_entree(nom, _texte_de(f, _Etat(taille, progres)), lecture)
        except (UnicodeDecodeError, ValueError):
            raise Refus("illisible")
    if lecture.source == "chatgpt" or lecture.contexte or lecture.bio or lecture.instructions:
        lecture.fin_chatgpt()
    if not lecture.compte["conversations"] and not lecture.memoires and not lecture.compte["deja"]:
        raise Refus("compte" if lecture.compte["compte_seul"] == len(fichiers) else
                    "illisible" if lecture.compte["illisibles"] else "pas_un_export")
    return lecture


def lire_collage(textes, connues=frozenset()):
    """The answer(s) copied from ChatGPT or Claude, as blocks of at most COLLAGE_BLOC characters (code
    fences dropped). Refus when it is too long or holds nothing."""
    texte = "\n\n".join(t for t in textes if isinstance(t, str))
    if len(texte) > COLLAGE_MAX:
        raise Refus("collage_long")
    t = re.sub(r"(?m)^[ \t]*(?:`{3,}|~{3,})[^\n]*$", "", texte.replace("\r\n", "\n").replace("\r", "\n"))
    t, caches = propre(t)
    lecture = Lecture(connues)
    t, n = masquer(t)
    lecture.compte["masques"] += n
    t = re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", t)).strip()
    if not t:
        raise Refus("vide")
    lecture.source = "collage"
    lecture.empreintes.append(hashlib.sha256(t.encode("utf-8")).hexdigest())
    morceaux, cur = [], []
    for ligne in t.split("\n"):
        for k in range(0, max(len(ligne), 1), COLLAGE_BLOC):
            bout = ligne[k:k + COLLAGE_BLOC]
            if cur and sum(len(x) + 1 for x in cur) + len(bout) > COLLAGE_BLOC:
                morceaux.append("\n".join(cur))
                cur = []
            cur.append(bout)
    morceaux.append("\n".join(cur))
    for i, m in enumerate(m for m in morceaux if m.strip()):
        k = cle("collage", lecture.empreintes[0], i)
        if k in connues:
            lecture.compte["deja"] += 1   # the part of a paste an earlier import already sorted
            continue
        lecture.memoires.append({"genre": "collage", "titre": "réponse collée", "date": None, "cle": k,
                                 "texte": m.strip(), "caches": caches if i == 0 else 0, "quand": 0})
    lecture.compte["souvenirs"] = len([l for l in t.split("\n") if l.strip()])
    return lecture
