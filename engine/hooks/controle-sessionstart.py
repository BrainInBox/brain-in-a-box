#!/usr/bin/env python3
"""SessionStart: one line about the night check and gbrain's updates, at most.

Reads two state files: etat.json, written by controle.py in
~/.brain-in-a-box/controle (or $BRAIN_CONTROLE_DIR), and last-update.json,
written by gbrain_update.py in ~/.gbrain (or $GBRAIN_HOME/.gbrain). Never the
vault, never the network, never Claude. The line is made of fixed sentences
with numbers and checked version strings in them: no text from a note, nor
from Claude, can reach the session through it.

The dead man's switches are computed here, from the time of each job's last
success: a job that stopped running is seen even when nothing runs to say so.
For a job that never succeeded, the time its script was installed next to this
hook counts. The update's switch is off without a gbrain clone in ~/DEV/gbrain
or beside a gbrain-selfupdate.sh of the user's own, as in gbrain-update-check.py.

A green or orange line is shown once per run, a red one once a day while its
cause lasts (shown.json, next to etat.json). Silent in a headless run
(claude -p), when the session is resumed, cleared or compacted, and on any error.
"""
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

CHECK_DEAD_H = 48
UPDATE_DEAD_H = 72
LATE_DAYS = 14
# Matched whole (fullmatch: no newline after it), ASCII digits only.
VERSION = re.compile(r"v?\d+(\.\d+){2,3}", re.A)
RUN_ID = re.compile(r"\d{8}-\d{4}(-\d{1,2})?", re.A)
REPORTS = ("Profile/controle.md", "Profil/controle.md")
HERE = os.path.dirname(os.path.abspath(__file__))


def read_json(path):
    """The object, or None when the file does not exist. Anything else (unreadable,
    not JSON, not an object) raises: the hook then says nothing at all."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    if not isinstance(data, dict):
        raise ValueError(path)
    return data


def num(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 100000 else 0


def version(value):
    if isinstance(value, str) and VERSION.fullmatch(value):
        return value if value.startswith("v") else "v" + value
    return None


def parsed(value, fmt, tz=None):
    try:
        return datetime.strptime(value, fmt).replace(tzinfo=tz)
    except (TypeError, ValueError):
        return None


def mtime(name, tz=None):
    """When the installed script was placed: the base of a dead man's switch
    before its job ever succeeded."""
    try:
        return datetime.fromtimestamp(os.path.getmtime(os.path.join(HERE, name)), tz)
    except OSError:
        return None


def days_since(then, now):
    return int((now - then).total_seconds() // 86400)


def s(n, word="s"):
    return word if n > 1 else ""


def own_updater():
    """A gbrain-selfupdate.sh of the user's own (kept by the installer) runs at night
    instead of gbrain_update.py: its checks are not ours to time."""
    try:
        with open(os.path.join(HERE, "gbrain-selfupdate.sh"), encoding="utf-8", errors="replace") as f:
            return "gbrain_update.py" not in f.read()
    except FileNotFoundError:  # Windows: the nightly calls gbrain_update.py itself
        return False


def check_lines(etat, now):
    """(reds, line of the run or None, run id) from etat.json."""
    reds, report = [], (etat or {}).get("rapport")
    report = report if report in REPORTS else REPORTS[0]
    last = parsed((etat or {}).get("dernier_succes"), "%Y-%m-%dT%H:%M:%S") if etat else None
    base = last or mtime("controle.py")
    if base and (now - base).total_seconds() > CHECK_DEAD_H * 3600:
        n = days_since(base, now)
        reds.append(("controle", "Le contrôle de nuit n'a pas tourné depuis %d jour%s (ordinateur éteint la nuit ?)"
                                 " — tape /controle maintenant." % (n, s(n))))
    if not etat:
        return reds, None, None
    who = "Contrôle de cette nuit" if etat.get("mode") == "nuit" else "Dernier contrôle"
    secrets = num(etat.get("secrets_a_changer"))
    if secrets == 1:
        reds.insert(0, ("secret", "%s : un mot de passe ou une clé était écrit en clair et déjà enregistré. Je l'ai "
                                  "masqué, mais change-le (détail : %s)." % (who, report)))
    elif secrets > 1:
        reds.insert(0, ("secret", "%s : %d mots de passe ou clés étaient écrits en clair et déjà enregistrés. Je les "
                                  "ai masqués, mais change-les (détail : %s)." % (who, secrets, report)))
    run = etat.get("run") if isinstance(etat.get("run"), str) and RUN_ID.fullmatch(etat["run"]) else None
    if not run or etat.get("rapport_reecrit") is not True or any(k == "controle" for k, _ in reds):
        return reds, None, None   # a quiet night rewrites no report, and says nothing
    fixed, look = num(etat.get("corrections")), num(etat.get("a_voir"))
    alerts = [a for a in etat.get("alertes") or [] if isinstance(a, dict) and a.get("code") != "gbrain"]
    if fixed and look:
        line = ("%s : %d correction%s faite%s (annulable%s), %d point%s à regarder. Détail : %s — tape /controle "
                "pour en parler." % (who, fixed, s(fixed), s(fixed), s(fixed), look, s(look), report))
    elif fixed:
        line = "%s : %d correction%s faite%s (annulable%s). Détail : %s." % (who, fixed, s(fixed), s(fixed), s(fixed),
                                                                            report)
    elif look:
        line = "%s : %d point%s à regarder. Détail : %s — tape /controle pour en parler." % (who, look, s(look),
                                                                                              report)
    elif alerts:
        line = ("%s : rien de corrigé, mais un point de santé est à regarder. Détail : %s — tape /controle pour en "
                "parler." % (who, report))
    else:
        line = None
    return reds, line, run


def update_lines(info, now):
    """Reds from last-update.json, and the update's own dead man's switch."""
    reds = []
    if info:
        status, late = info.get("status"), num(info.get("retard_jours"))
        if status == "broken":
            reds.append(("gbrain:broken", "gbrain ne démarre plus après une mise à jour ratée : relance l'installation."))
        elif status == "rolled_back":
            tag, cur = version(info.get("tag")), version(info.get("after"))
            reds.append(("gbrain:rolled_back",
                         "gbrain : la mise à jour vers %s a échoué, retour arrière fait, tu restes en %s — tape "
                         "/controle gbrain." % (tag, cur) if tag and cur else
                         "gbrain : la dernière mise à jour a échoué, retour arrière fait — tape /controle gbrain."))
        elif late > LATE_DAYS:
            reds.append(("gbrain:late", "gbrain a %d jours de retard sur ses mises à jour — tape /controle gbrain."
                         % late))
    home = os.path.expanduser("~")
    if os.path.isdir(os.path.join(home, "DEV", "gbrain", ".git")) and not own_updater():
        base = parsed((info or {}).get("derniere_verif_ok"), "%Y-%m-%dT%H:%M:%SZ", timezone.utc) \
            or mtime("gbrain_update.py", timezone.utc)
        if base and (now - base).total_seconds() > UPDATE_DEAD_H * 3600:
            n = days_since(base, now)
            reds.append(("maj", "La vérification des mises à jour de gbrain ne tourne plus depuis %d jour%s — tape "
                                "/controle gbrain." % (n, s(n))))
    return reds


def write_shown(path, shown):
    fd, tmp = tempfile.mkstemp(prefix=".shown-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(shown, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main():
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT") == "sdk-cli":
        return
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    if not isinstance(payload, dict) or payload.get("source") != "startup":
        return
    home = os.path.expanduser("~")
    state = os.environ.get("BRAIN_CONTROLE_DIR") or os.path.join(home, ".brain-in-a-box", "controle")
    gbrain = os.path.join(os.environ.get("GBRAIN_HOME", "").strip() or home, ".gbrain")
    etat = read_json(os.path.join(state, "etat.json"))
    info = read_json(os.path.join(gbrain, "last-update.json"))
    reds, line, run = check_lines(etat, datetime.now())
    reds += update_lines(info, datetime.now(timezone.utc))

    shown_path = os.path.join(state, "shown.json")
    shown = read_json(shown_path) or {}
    seen = shown.get("rouge") if isinstance(shown.get("rouge"), dict) else {}
    today = datetime.now().strftime("%Y-%m-%d")
    parts = [(k, text) for k, text in reds if seen.get(k) != today]
    if line and shown.get("run") != run:
        parts.append(("run", line))
    parts = parts[:2]
    if not parts:
        return
    for k, _ in parts:
        if k == "run":
            shown["run"] = run
        else:
            seen[k] = today
    shown["rouge"] = {k: v for k, v in seen.items() if k in {r for r, _ in reds}}
    os.makedirs(state, mode=0o700, exist_ok=True)
    write_shown(shown_path, shown)

    text = " · ".join(t for _, t in parts)
    out = {"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "Contrôle du cerveau. Commence ta première réponse par cette ligne, telle quelle, puis "
                             "réponds normalement à la demande : « %s ». N'agis pas dessus sans que la personne "
                             "le demande." % text}}
    if any(k != "run" for k, _ in parts):
        out["systemMessage"] = text   # shown even if Claude forgets the line
    sys.stdout.write(json.dumps(out) + "\n")


if __name__ == "__main__":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        main()
    except Exception:
        pass
    sys.exit(0)
