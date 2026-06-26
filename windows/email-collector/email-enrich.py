#!/usr/bin/env python3
# email-enrich.py - Affiliation LLM des REPONSES clients -> 1 note par mail dans BrainCo.
#
# Couche LATENTE de la recette email-to-brain : le code filtre (deterministe)
# les reponses entrantes de clients, puis un agent (claude -p = login Claude
# Code, PAS de cle API) cree une note par mail dans BrainCo/mails/ et l'affilie
# au bon client via [[clients/<slug>]] (-> croise avec meetings dans le graphe).
#
# Lance par le nightly APRES le collecteur. Idempotent : ne recree pas une note
# dont l'id mail est deja present dans mails/.
#
# SRP : une fonction = une responsabilite ; main() orchestre.

import json, sys, time, os, subprocess, shutil
from pathlib import Path

BRAINCO = Path.home() / "Documents" / "BrainCo"
DATA = Path.home() / ".gbrain" / "email-collector" / "data"
LOGS = Path.home() / ".claude" / "logs"
MAILS_DIR = BRAINCO / "mails"
SKILL = Path.home() / ".claude" / "skills" / "mail-affiliator" / "SKILL.md"
DAY = time.strftime("%Y-%m-%d")


def log_err(msg):
    LOGS.mkdir(parents=True, exist_ok=True)
    (LOGS / "email-enrich-errors.log").open("a", encoding="utf-8").write(
        f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}" + chr(10))


def read(path):
    # Lecture UTF-8 robuste (le cp1252 par defaut de Windows casse les accents).
    return path.read_text(encoding="utf-8", errors="replace")


def no_mcp_config():
    """Fichier de config MCP VIDE pour les `claude -p` d'automatisation.
    Sans ca, claude -p charge toute la flotte MCP (Playwright, n8n, Gmail...)
    -> serveurs qui pendent en headless (le nightly se bloque) et fuient en
    zombies (constate 2026-06-22). Avec --strict-mcp-config + ce fichier : 0 MCP."""
    p = Path.home() / ".gbrain" / "no-mcp-config.json"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"mcpServers":{}}', encoding="utf-8")
    return str(p)


def load_messages():
    """Charge les mails collectes du jour. None si le fichier n'existe pas."""
    f = DATA / "messages" / f"{DAY}.json"
    if not f.exists():
        return None
    try:
        return json.loads(read(f))
    except (OSError, ValueError) as e:   # ValueError couvre JSONDecodeError
        log_err(f"lecture messages: {e}")
        sys.exit(1)


def has_external_recipient(to):
    """True si au moins un destinataire est hors @citymood.io (mail vers un externe)."""
    return any("@" in part and "@citymood.io" not in part
               for part in (to or "").lower().split(","))


def is_client_mail(m):
    """Mail d'une conversation client, dans LES DEUX sens :
      - sortant : notre envoi/relance vers un destinataire externe ;
      - entrant : reponse d'un externe (sujet Re:/Tr:).
    Exclut le bruit et l'interne citymood<->citymood."""
    if m.get("noise"):
        return False
    if m.get("direction") == "outbound":
        return has_external_recipient(m.get("to"))
    # entrant : reponse d'un externe
    if "@citymood.io" in (m.get("from") or "").lower():
        return False
    subj = (m.get("subject") or "").strip().lower()
    return subj.startswith("re:") or subj.startswith("re ") or subj.startswith("tr:")


def drop_already_noted(replies):
    """Idempotence : retire les mails dont l'id figure deja dans une note mails/."""
    if not MAILS_DIR.exists():
        return replies
    seen = set()
    for f in MAILS_DIR.glob("*.md"):
        txt = read(f)
        seen.update(m["id"] for m in replies if m["id"] in txt)
    return [m for m in replies if m["id"] not in seen]


def load_clients():
    """Liste 'slug : titre' des fiches clients, pour guider l'affiliation par le LLM."""
    cdir = BRAINCO / "clients"
    if not cdir.exists():
        return []
    out = []
    for f in sorted(cdir.glob("*.md")):
        if f.name.startswith("_") or f.name.lower() == "readme.md":
            continue
        title = f.stem
        for line in read(f).splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        out.append(f"{f.stem} : {title}")
    return out


def load_skill():
    """Charge les instructions de classement (le skill mail-affiliator). Fail loud si absent."""
    if not SKILL.exists():
        log_err(f"skill mail-affiliator introuvable: {SKILL}")
        sys.exit(1)
    txt = read(SKILL)
    # retire le frontmatter YAML (--- ... ---) -> ne garder que le corps des instructions
    if txt.startswith("---"):
        end = txt.find(chr(10) + "---", 3)
        if end != -1:
            txt = txt[end + 4:]
    return txt.strip()


def build_prompt(mails, clients):
    """Assemble le prompt : instructions du skill (mail-affiliator) + donnees (clients + mails)."""
    client_list = chr(10).join(clients)
    mails_block = chr(10).join(
        f'- id={m["id"]} | {m.get("direction", "?")} | date={m["date"]} | from={m["from"]} | to={m.get("to", "")} | subject={m["subject"]} | gmail={m["link"]}'
        + chr(10) + f'  contenu: {(m.get("body") or m.get("snippet") or "")[:3000]}'
        for m in mails
    )
    data = (f"CLIENTS EXISTANTS (slug : nom) :{chr(10)}{client_list}{chr(10)}{chr(10)}"
            f"MAILS A RANGER (inbound=recu, outbound=envoye) :{chr(10)}{mails_block}")
    return load_skill() + chr(10) + chr(10) + data


def run_agent(prompt):
    """Appelle claude -p (cwd=BrainCo, acceptEdits) pour creer les notes. Exit 1 si echec reel."""
    claude_bin = (shutil.which("claude.cmd") or shutil.which("claude")
                  or os.path.expandvars(r"%APPDATA%\npm\claude.cmd"))
    if not claude_bin or not Path(claude_bin).exists():
        log_err(f"claude introuvable: {claude_bin}")
        sys.exit(1)
    MAILS_DIR.mkdir(exist_ok=True)
    try:
        # Windows: prompt via STDIN (argv plafonne a ~32k), cmd /c pour le .cmd.
        # --strict-mcp-config + config vide => AUCUN serveur MCP (pas de hang/zombie).
        subprocess.run(
            ["cmd", "/c", claude_bin, "-p",
             "--strict-mcp-config", "--mcp-config", no_mcp_config(),
             "--permission-mode", "acceptEdits"],
            input=prompt, text=True, encoding="utf-8",
            cwd=str(BRAINCO), timeout=600, check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:   # TimeoutExpired inclus
        log_err(str(e))
        sys.exit(1)


def main():
    msgs = load_messages()
    if not msgs:
        return
    mails = drop_already_noted([m for m in msgs if is_client_mail(m)])
    if not mails:
        return
    run_agent(build_prompt(mails, load_clients()))


if __name__ == "__main__":
    main()
