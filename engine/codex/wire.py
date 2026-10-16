"""The kit's lines in Codex's own files, for both installers (docs/proposals/codex.md, §3.2, §3.3,
§3.8): its hooks merged into hooks.json, their approval added to config.toml, its block appended to
the global AGENTS.md. What the user has there is merged into or left alone, never replaced:
hooks.json and config.toml are backed up (0600) first and written in one step.

    python3 -I engine/codex/wire.py check|merge|approve|restore|global <codex home> <arg>
    python -I engine\\codex\\wire.py --ps1 <python.exe> <the same>

<arg>: the hooks' folder (check, merge, approve), what approve kept or "-" (restore), Claude Code's
CLAUDE.md template (global). install.sh's lines are English. With --ps1, install.ps1's: French in
ASCII, and the hooks in the form Codex runs on Windows, through PowerShell (step 0):
& '<python.exe>' '<hook>' --agent codex, in commandWindows and command alike.
"""
import hashlib
import json
import ntpath
import os
import re
import shlex
import sys
import tempfile
import time

PS1 = sys.argv[1:2] == ["--ps1"]
PY = sys.argv[2] if PS1 else None
op, home, arg = sys.argv[3:6] if PS1 else sys.argv[1:4]
H = os.path.expanduser("~")
MARK = "<!-- brain-in-a-box -->"
EVENTS = (("UserPromptSubmit", "user_prompt_submit", ("correction-detector.py",)),
          ("Stop", "stop", ("session-logger.py", "session-indexer.py", "session-recap.py")),
          ("SessionStart", "session_start", ("gbrain-update-check.py", "controle-sessionstart.py")))
HJ, CT = os.path.join(home, "hooks.json"), os.path.join(home, "config.toml")
TEXT = {
    "stop": ("%s %s. It was left untouched: fix it, then re-run ./install.sh",
             "%s %s. Laisse tel quel : corrige-le, puis relance la meme commande d'installation."),
    "utf8": ("is not UTF-8 text (line %d)", "n'est pas du texte UTF-8 (ligne %d)"),
    "json": ("is not valid JSON (line %d, column %d: %s)", "n'est pas du JSON valide (ligne %d, colonne %d : %s)"),
    "deep": ("is nested too deep to be read", "est imbrique trop profondement pour etre lu"),
    "shape": ("is JSON, but not a hooks file with a \"hooks\" object",
              "est du JSON, mais pas un fichier de hooks avec un objet \"hooks\""),
    "groups": ("has a \"hooks\".\"%s\" that is not a list of hook groups",
               "a un \"hooks\".\"%s\" qui n'est pas une liste de groupes de hooks"),
    "there": ("%s: the kit's hooks already there", "%s : les hooks du kit y sont deja"),
    "added": ("%s: the kit's hooks added%s", "%s : hooks du kit ajoutes%s"),
    "python": ("%s: the kit's hooks now run with %s%s", "%s : les hooks du kit tournent maintenant avec %s%s"),
    "previous": (" (previous version: %s)", " (version precedente : %s)"),
    "notfile": ("%s is not a file", "%s n'est pas un fichier"),
    "notutf8": ("%s is not UTF-8 text", "%s n'est pas du texte UTF-8"),
    "form": ("%s has a [hooks…] table of another form", "%s a une table [hooks...] d'une autre forme"),
    "top": ("%s sets hooks at its top", "%s regle hooks en tete de fichier"),
    "unsafe": ("%s is not a file the kit can add to safely", "%s n'est pas un fichier ou le kit peut ajouter sans risque"),
    "kept": ("%s: already has the kit's block (kept as is)", "%s : a deja le bloc du kit (garde tel quel)"),
    "moved": ("%s: no longer has the line \"%s\": the kit's block for Codex was not written",
              "%s : n'a plus la ligne \"%s\" : le bloc du kit pour Codex n'est pas ecrit"),
    "appended": ("%s: the kit's block appended (your content kept)%s", "%s : bloc du kit ajoute (ton contenu est garde)%s"),
    "override": ("; Codex reads this file instead of AGENTS.md", " ; Codex lit ce fichier a la place d'AGENTS.md"),
}


def t(key):
    return TEXT[key][1 if PS1 else 0]


def shown(p):
    return "~" + p[len(H):] if p.startswith(H + os.sep) else p


def out(*lines):
    sys.stdout.buffer.write("".join(l + "\n" for l in lines).encode("utf-8"))


def stop(why, p=HJ):
    out(t("stop") % (p, why))
    sys.exit(1)


def keep(p, raw):   # p.bak-<date>, 0600
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak, n = "%s.bak-%s" % (p, stamp), 1
    while os.path.lexists(bak):
        bak, n = "%s.bak-%s-%d" % (p, stamp, n), n + 1
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(bak, flags, 0o600), "wb") as f:
        f.write(raw)
    return bak


def write(target, data):   # in one step; a file that was there keeps its mode, a new one is 0600
    d = os.path.dirname(target)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(target) + ".")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(target):
            os.chmod(tmp, os.stat(target).st_mode & 0o7777)
        os.replace(tmp, target)
    except BaseException:
        os.unlink(tmp)
        raise


def ps_quote(s):
    """A PowerShell single-quoted string: all literal ($, `, spaces), the quote written twice. PowerShell
    also takes the typographic quotes for one: each is doubled too (app/src-tauri/src/steps.rs, ps_quote)."""
    return "'" + "".join(c * 2 if c in "'‘’‚‛" else c for c in s) + "'"


def command(name):
    if PS1:
        return "& %s %s --agent codex" % (ps_quote(PY), ps_quote(ntpath.join(arg, name)))
    return shlex.quote(os.path.join(arg, name)) + " --agent codex"


def entry(name):
    """One hook of the kit's, as it writes it. Windows: Codex runs commandWindows (command elsewhere)."""
    if PS1:
        return {"type": "command", "command": command(name), "commandWindows": command(name), "timeout": 30}
    return {"type": "command", "command": command(name), "timeout": 30}


def kit_command(c, name):
    """c is the kit's command for the hook `name`, as it writes it, whatever the Python (Windows: the
    one an earlier install found; Python moves when another version is installed)."""
    if not PS1:
        return c == command(name)
    tail = " %s --agent codex" % ps_quote(ntpath.join(arg, name))
    return (isinstance(c, str) and c.startswith("& ") and c.endswith(tail)
            and re.fullmatch("'(?:[^'‘’‚‛]|''|‘‘|’’|‚‚|‛‛)*'", c[2:len(c) - len(tail)]) is not None)


def kit_entry(h, name):   # an entry exactly as the kit writes it, whatever the Python
    return (set(h) == set(entry(name)) and h["type"] == "command" and h["timeout"] == 30
            and h.get("commandWindows", h["command"]) == h["command"] and kit_command(h["command"], name))


def ours(h, name):   # an entry of the kit's, as the kit wrote it or since changed by the user
    if not PS1:
        return isinstance(h.get("command"), str) and "/.claude/hooks/brain/" + name in h["command"]
    want = "/.claude/hooks/brain/" + name.lower()
    return any(isinstance(h.get(k), str) and want in h[k].replace("\\", "/").lower()
               for k in ("command", "commandWindows", "command_windows"))


def hooks_json():
    target, raw, d = os.path.realpath(HJ), None, {}   # a symlinked hooks.json stays a symlink
    if os.path.exists(target):
        with open(target, "rb") as f:
            raw = f.read()
        try:
            text = raw.decode("utf-8-sig")
            d = json.loads(text) if text.strip() else {}
        except UnicodeDecodeError as e:
            stop(t("utf8") % (raw[:e.start].count(b"\n") + 1))
        except json.JSONDecodeError as e:
            stop(t("json") % (e.lineno, e.colno, e.msg))
        except RecursionError:
            stop(t("deep"))
        if not isinstance(d, dict) or not isinstance(d.get("hooks", {}), dict):
            stop(t("shape"))
        for evt, _, _ in EVENTS:
            arr = d.get("hooks", {}).get(evt, [])
            if not (isinstance(arr, list) and all(isinstance(g, dict) and isinstance(g.get("hooks", []), list)
                                                  and all(isinstance(h, dict) for h in g.get("hooks", [])) for g in arr)):
                stop(t("groups") % evt)
    return target, raw, d


def merge():
    target, raw, d = hooks_json()
    before = json.dumps(d, sort_keys=True)
    added = False
    for evt, _, names in EVENTS:
        arr = d.setdefault("hooks", {}).setdefault(evt, [])
        for g in arr:   # the kit's own entries move to the Python of this install, as Claude's in settings.json do
            if "hooks" in g:
                g["hooks"] = [next((entry(n) for n in names if kit_entry(h, n)), h) for h in g["hooks"]]
        missing = [n for n in names if not any(ours(h, n) for g in arr for h in g.get("hooks", []))]
        if missing:   # into the group of the kit's that is there, else one of its own at the end
            grp = next((g for g in arr if "matcher" not in g and any(ours(h, n) for h in g.get("hooks", []) for n in names)), None)
            if grp is None:
                grp = {"hooks": []}
                arr.append(grp)
            grp.setdefault("hooks", []).extend(entry(n) for n in missing)
            added = True
    if json.dumps(d, sort_keys=True) == before:
        out(t("there") % shown(HJ))
        sys.exit(0)
    bak = keep(HJ, raw) if raw is not None else None
    write(target, (json.dumps(d, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    was = t("previous") % shown(bak) if bak else ""
    out(t("added") % (shown(HJ), was) if added else t("python") % (shown(HJ), PY, was))


def trust(label, cmd):
    """Codex's trusted_hash of a hook: the sha256 of its normalized form, whose command is the one it
    runs, commandWindows on Windows (codex-rs/hooks/src/engine/discovery.rs, hook_hash, 0.162.1)."""
    ident = {"event_name": label, "hooks": [{"async": False, "command": cmd, "timeout": 30, "type": "command"}]}
    return "sha256:" + hashlib.sha256(json.dumps(ident, sort_keys=True, separators=(",", ":"),
                                                 ensure_ascii=False).encode("utf-8")).hexdigest()


def approve():
    # Codex runs a hook of hooks.json once [hooks.state."<hooks.json>:<event>:<group>:<entry>"] in
    # config.toml holds its trusted_hash: the sha256 of its normalized form, whose command is the one
    # it runs, commandWindows on Windows (codex-rs/hooks/src/engine/discovery.rs, hook_hash, 0.162.1).
    # <hooks.json> is the path Codex reaches it by: CODEX_HOME as given when unset, its real path when
    # set; both are written when they differ.
    _, _, d = hooks_json()
    join = ntpath.join if PS1 else os.path.join
    bases = [home] + ([os.path.realpath(home)] if os.path.realpath(home) != home else [])
    want, names = [], set()
    for evt, label, evnames in EVENTS:
        for gi, g in enumerate(d.get("hooks", {}).get(evt, [])):
            for hi, h in enumerate(g.get("hooks", []) if "matcher" not in g else []):
                n = next((n for n in evnames if h == entry(n)), None)
                if n is None:
                    continue
                names.add(n)
                want += [("%s:%s:%d:%d" % (join(b, "hooks.json"), label, gi, hi), trust(label, command(n)), n, label) for b in bases]
    if not want:
        out("none", "0", "-", "-")
        sys.exit(0)
    target, raw = os.path.realpath(CT), None
    if os.path.lexists(target) and not os.path.isfile(target):
        out("refused", t("notfile") % shown(CT), "-", "-")
        sys.exit(0)
    if os.path.exists(target):
        with open(target, "rb") as f:
            raw = f.read()
    try:
        text = raw.decode("utf-8") if raw is not None else ""
    except UnicodeDecodeError:
        out("refused", t("notutf8") % shown(CT), "-", "-")
        sys.exit(0)
    # No TOML reader in Python 3.9: tables [hooks.state."<key>"] are added, at the end, one per key of the
    # kit's that has none, with the command they approve as a comment. A table already there is the
    # user's say in /hooks, or a newer Codex's, and stays as it is, but for one the kit wrote for a command
    # of its own that is no longer there (the Python moved): its hash, then its comment, change. hooks in
    # any other form, and the file is not touched.
    lines, tables, root, quote = text.split("\n"), {}, True, None
    for i, line in enumerate(lines):
        s = line.strip()
        if quote:
            quote = None if line.count(quote) % 2 else quote
            continue
        if s.startswith("["):
            root = False
            if re.match(r"\[\[?\s*(hooks|\"hooks\"|'hooks')\s*[.\]]", s):
                m = re.fullmatch(r'\[hooks\.state\.("(?:[^"\\\x00-\x1f]|\\.)*")\]', s)
                if not m:
                    out("refused", t("form") % shown(CT), "-", "-")
                    sys.exit(0)
                tables.setdefault(json.loads(m.group(1)), []).append(i)
        elif root and re.match(r"(hooks|\"hooks\"|'hooks')\s*[.=]", s):
            out("refused", t("top") % shown(CT), "-", "-")
            sys.exit(0)
        if not s.startswith("#"):
            quote = next((q for q in ('"""', "'''") if line.count(q) % 2), None)
    if text.startswith("﻿") or quote:
        out("refused", t("unsafe") % shown(CT), "-", "-")
        sys.exit(0)

    def body(at):   # a table's settings and comments, by line
        end = next((j for j in range(at + 1, len(lines)) if lines[j].strip().startswith("[")), len(lines))
        rows = [(j, lines[j].strip()) for j in range(at + 1, end) if lines[j].strip()]
        return [r for r in rows if not r[1].startswith("#")], [r for r in rows if r[1].startswith("#")]

    def turned_off(key, label, digest):
        """A table of the same event elsewhere that turns this hook off: by its hash, or one that turns a
        hook off without trusting any. Codex knows a hook by its place: one of the user's put before the
        kit's moves it, and a table at its new place would turn it back on."""
        here = tuple("%s:%s:" % (join(b, "hooks.json"), label) for b in bases)
        for k, at in tables.items():
            if k == key or not k.startswith(here):
                continue
            rows = [r for _, r in body(at[0])[0]]
            hashes = [r for r in rows if re.match(r"trusted_hash\s*=", r)]
            if (rows != ['trusted_hash = "%s"' % digest] and 'trusted_hash = "%s"' % digest in rows) \
                    or (not hashes and any(re.fullmatch(r"enabled\s*=\s*false", r) for r in rows)):
                return True
        return False

    def comment(cmd):   # TOML takes no control character in a comment
        return None if re.search(r"[\x00-\x08\x0a-\x1f\x7f]", cmd) else "# command: " + cmd

    def table(key, digest, n):
        c = comment(command(n))
        return '[hooks.state.%s]\ntrusted_hash = "%s"\n' % (json.dumps(key, ensure_ascii=False), digest) + (c + "\n" if c else "")

    new, kept, moved = [], set(), {}
    for key, digest, n, label in want:
        at = tables.get(key)
        if at is None:
            if turned_off(key, label, digest):
                kept.add(n)
            else:
                new.append((key, digest, n))
            continue
        rows, notes = body(at[0])
        if len(at) == 1 and [r for _, r in rows] == ['trusted_hash = "%s"' % digest]:
            continue
        old = [c[len("# command: "):] for _, c in notes if c.startswith("# command: ")]
        if len(at) == 1 and len(rows) == 1 and len(old) == 1 and kit_command(old[0], n) \
                and rows[0][1] == 'trusted_hash = "%s"' % trust(label, old[0]):
            moved[rows[0][0]] = 'trusted_hash = "%s"' % digest   # the kit's own, for its command of before
            moved[next(j for j, c in notes if c.startswith("# command: "))] = comment(command(n))
            continue
        kept.add(n)
    if not new and not moved:
        out("unchanged", str(len(names - kept)), "-", ",".join(sorted(kept)) or "-")
        sys.exit(0)
    result = "\n".join(moved.get(j, l) for j, l in enumerate(lines) if moved.get(j, l) is not None)
    if new:
        result += ("\n" if result and not result.endswith("\n") else "") + ("\n" if result else "")
        result += "# brain-in-a-box: the kit's Codex hooks, approved for you by %s (see or turn them off with /hooks)\n" \
                  % ("install.ps1" if PS1 else "install.sh")
        result += "\n".join(table(k, v, n) for k, v, n in new)
    bak = keep(CT, raw) if raw is not None else "-"
    write(target, result.encode("utf-8"))
    out("written", str(len(names - kept)), bak, ",".join(sorted(kept)) or "-")


def restore():   # what approve replaced, back as it was; nothing there before: removed
    target = os.path.realpath(CT)
    if arg == "-":
        os.unlink(target)
    else:
        with open(arg, "rb") as f:
            write(target, f.read())


def global_block():
    # The kit's block in the global AGENTS.md: Claude Code's CLAUDE.md template, its few lines about
    # Claude's own file turned into Codex's. Codex reads AGENTS.override.md instead when it has text.
    over, main = os.path.join(home, "AGENTS.override.md"), os.path.join(home, "AGENTS.md")

    def text_of(p):
        try:
            with open(p, "rb") as f:
                return f.read().decode("utf-8", "replace")
        except OSError:
            return ""
    target = over if os.path.isfile(over) and text_of(over).strip() else main
    if MARK in text_of(target):
        out(t("kept") % shown(target))
        sys.exit(0)
    with open(arg, encoding="utf-8") as f:
        block = f.read()
    for old, new in (("# CLAUDE.md — Global directives", "# AGENTS.md — Global directives"),
                     ("If you already had a `~/.claude/CLAUDE.md`, this block", "If you already had a `%s`, this block" % shown(target)),
                     ("Keep what serves you.\n", "Keep what serves you.\n\nCodex does not load `CLAUDE.md` files. In a folder that "
                      "has a `CLAUDE.md` and no `AGENTS.md` (the team brain `~/Documents/BrainCo`, a project set up for Claude Code), "
                      "read its `CLAUDE.md` before anything else and follow it.\n")):
        if block.count(old) != 1:
            out(t("moved") % (arg, old.strip()))
            sys.exit(1)
        block = block.replace(old, new)
    with open(target, "ab") as f:
        f.write(("\n" + MARK + "\n" + block).encode("utf-8"))
    out(t("appended") % (shown(target), t("override") if target == over else ""))


if op == "check":
    hooks_json()
else:
    {"merge": merge, "approve": approve, "restore": restore, "global": global_block}[op]()
