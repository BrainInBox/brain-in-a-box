"""The person's language: the one Profile/soul.md names (Profil/ in an older brain), else the system's.

Used by daily-reflection.py (the language of the journal it writes, and its titles) and by
controle-sessionstart.py (the language each session is told to speak). Reads one note and the
system's settings (the environment; else Windows' display language, macOS' first preferred
language): never the network, never a model. What leaves this module is a code of LANGUES or
None, never text read from the note.

Not a hook: installed next to them, never registered in settings.json.
"""
import os
import re
import subprocess
import sys

from _vault_write import profile_dir

# code: (its name, as said to a model; the words that name it in a note)
LANGUES = {
    "fr": ("French", ("français", "francais", "french", "française", "francaise")),
    "en": ("English", ("english", "anglais")),
    "es": ("Spanish", ("español", "espanol", "spanish", "espagnol")),
    "pt": ("Portuguese", ("português", "portugues", "portuguese", "portugais")),
    "de": ("German", ("deutsch", "german", "allemand")),
    "it": ("Italian", ("italiano", "italian", "italien")),
    "nl": ("Dutch", ("nederlands", "dutch", "néerlandais", "neerlandais")),
    "ar": ("Arabic", ("العربية", "arabic", "arabe")),
}
WORDS = {w: code for code, (_, words) in LANGUES.items() for w in words}
# Windows: the primary language of a LANGID (its low 10 bits).
WINDOWS = {0x0c: "fr", 0x09: "en", 0x0a: "es", 0x16: "pt", 0x07: "de", 0x10: "it", 0x13: "nl", 0x01: "ar"}
# The word for "language" in soul.md, as the onboarding may have translated it: in a heading ("## Ta
# langue", "## 🗣️ Langue"), else as a field ("- Langue : français", "**Language**: French").
WORD = re.compile(r"(?<!\w)(language|langue|idioma|sprache|lingua|língua|lengua|taal|اللغة|لغة)s?(?!\w)", re.I)
HEADING = re.compile(r"#{1,6}\s")


def nom(code):
    """The language's name for a model ("French"), or None."""
    return LANGUES[code][0] if code in LANGUES else None


def _field(line):
    """The value of a line "Langue : français" (a bullet, bold, up to 3 words before the colon), or None."""
    key, colon, value = line.strip().lstrip("-*+ ").partition(":")
    key = key.strip(" *_")
    if colon and value.strip(" *_") and len(key.split()) <= 3 and not key.startswith((">", "#")) and WORD.search(key):
        return value.strip(" *_")
    return None


def de_soul(vault):
    """What Profile/soul.md says of its language: the first line under a heading with the word for it
    (Language, Langue, Idioma...), else the first field that names it ("- Langue : français"). The code
    of the first language that line names, "?" for a language this module does not know, None when it
    names none (still {{LANGUAGE}}, no such heading nor field, no file)."""
    try:
        with open(os.path.join(str(vault), profile_dir(vault), "soul.md"), encoding="utf-8", errors="replace") as f:
            lines = f.read(20000).splitlines()
    except OSError:
        return None
    for i, line in enumerate(lines):
        if HEADING.match(line.strip()) and WORD.search(line):
            value = next((l.strip() for l in lines[i + 1:] if l.strip()), "")
            break
    else:
        value = next((v for v in map(_field, lines) if v), "")
    if not value or value.startswith("#") or "{{" in value:
        return None
    for word in re.findall(r"\w+", value.casefold()):
        if word in WORDS:
            return WORDS[word]
    return "?"


def _code(value):
    m = re.match(r"([A-Za-z]{2,3})(?:[_.@-]|$)", value.strip())
    code = m.group(1).lower() if m else None
    return code if code in LANGUES else None


def du_systeme():
    """The system's language as a code of LANGUES, or None: LC_ALL, LC_MESSAGES or LANG (a
    terminal sets them, Git Bash from Windows' own), else Windows' display language, else macOS'
    first preferred one (launchd and the Task Scheduler start programs without those variables)."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "")
        if value and value.split(".")[0] not in ("C", "POSIX"):
            return _code(value)
    try:
        if os.name == "nt":
            import ctypes
            return WINDOWS.get(ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF)
        if sys.platform == "darwin":
            out = subprocess.run(["/usr/bin/defaults", "read", "-g", "AppleLanguages"], capture_output=True,
                                 timeout=5, stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace").stdout
            m = re.search(r"\(\s*\"?([A-Za-z]{2,3})\b", out)
            return _code(m.group(1)) if m else None
    except Exception:
        return None
    return None
