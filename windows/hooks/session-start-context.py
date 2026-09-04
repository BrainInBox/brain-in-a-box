#!/usr/bin/env python3
# SessionStart hook - charge le Brain PERSO dans CHAQUE session Claude Code,
# quel que soit le dossier de travail.
#
# Ferme la boucle "le brain ecrit mais ne se relit jamais" : l'original
# brain-in-a-box comptait sur une INSTRUCTION dans Brain/CLAUDE.md (chargee
# seulement si on lance claude depuis le vault). Ce hook, lui, s'execute partout
# (settings utilisateur global) et injecte le contexte via additionalContext.
#
# Charge : soul (identite/style) + memory (contexte recent) + lessons
# (corrections) + regles de code. PAS les journaux (trop gros, cherchables via gbq).

import json, sys
from pathlib import Path

PROFILE = Path.home() / "Documents" / "Brain" / "Profile"


def read(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def file_section(title, filename, tail=None):
    """Un bloc de contexte depuis Profile/<filename>. tail=N -> N dernieres lignes."""
    txt = read(PROFILE / filename)
    if not txt:
        return None
    if tail:
        txt = chr(10).join(txt.splitlines()[-tail:])
    return f"### {title}" + chr(10) + txt


def code_rules_section():
    """code-quality.md condense : titres `##` + lignes **Regle** (sans le detail)."""
    txt = read(PROFILE / "code-quality.md")
    if not txt:
        return None
    keep = [s for s in (l.strip() for l in txt.splitlines())
            if s.startswith("## ") or s.startswith("**Regle**") or s.startswith("**Règle**")]
    if not keep:
        return None
    return "### Regles de qualite de code (detail: Profile/code-quality.md)" + chr(10) + chr(10).join(keep)


def build_context():
    blocks = [
        file_section("Qui je suis / valeurs / style - Profile/soul.md", "soul.md"),
        file_section("Memoire : decisions actives + contexte recent - Profile/memory.md", "memory.md", tail=40),
        file_section("Lecons passees (corrections a NE JAMAIS repeter) - Profile/lessons.md", "lessons.md", tail=40),
        code_rules_section(),
    ]
    blocks = [b for b in blocks if b]
    if not blocks:
        return ""
    head = "BRAIN - contexte perso auto-charge au demarrage. A prendre en compte AVANT d'agir."
    return head + chr(10) + chr(10) + (chr(10) + chr(10)).join(blocks)


ctx = build_context()
if ctx:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}))
sys.exit(0)
