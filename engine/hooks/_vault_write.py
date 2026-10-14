"""Writing into the vault, shared by the hooks that put text from elsewhere in
a note: daily-reflection (Claude's answer) and session-recap (a session's first
prompt, its folder, the files it touched).

That text can come from a web page or a document. Obsidian runs some markdown
on view (Dataview's inline `$= ...`, Templater's <% %>), loads HTML and images,
and a line break could forge a heading or another program's block:
clean_item() makes it one line of plain text, secrets masked. write_atomic()
replaces a note in one step, so a reader never sees half of it.

Not a hook: installed next to them, never registered in settings.json.
"""
import os
import re
import stat
import tempfile
import time
import unicodedata

from _redact import redact


def clean_item(text, limit):
    """One line of plain text, safe to put in a note, or ""."""
    if not isinstance(text, str):
        return ""
    # Line breaks become spaces; invisible, bidi, tag and private-use characters go.
    s = "".join(" " if unicodedata.category(c) in ("Cc", "Zl", "Zp")
                else "" if unicodedata.category(c) in ("Cf", "Cs", "Co") else c for c in text)
    s = re.sub(r"<!--.*?-->", " ", s).replace("<!--", " ").replace("-->", "->")  # no marker block of anyone
    # No HTML (Obsidian renders it, <img> loads on view) nor Templater command (<% %>, run when a new
    # note is created, JavaScript included).
    s = re.sub(r"<(?=[A-Za-z/!?%])", "‹", s)
    s = s.replace("![", "[")                   # no image or embed: they load on view too
    # No backquote: an inline `$= ...` is Dataview JavaScript, run on view; a fence would hide what
    # follows. No ~~~ fence nor Obsidian comment (%%) either.
    s = re.sub(r"~{3,}", "~~", s.replace("`", "'")).replace("%%", "%")
    s = re.sub(r"\s+", " ", s).strip()
    while True:  # our own "- " comes first: no list mark, heading, rule, frontmatter fence or quote
        t = re.sub(r"^(?:[-*+]\s+|\d{1,3}[.)]\s+|#+(?:\s+|$)|-{3,}\s*|={3,}\s*|>+\s*)", "", s)
        if t == s:
            break
        s = t
    s = redact(s)[0]  # before cutting: a key cut short no longer matches its pattern
    if len(s) > limit:
        s = s[:limit - 1].rstrip() + "…"
    return redact(s)[0]


def write_atomic(path, lines, note):
    """Temporary file next to it, then os.replace: a reader sees the old file or the new one."""
    data = (("\ufeff" if note and note.bom else "") + "".join(lines)).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode) if path.exists() else 0o644)
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                # Windows: an antivirus, the indexer or a sync client holding the file for a moment.
                if os.name != "nt" or attempt == 4:
                    raise
                time.sleep(0.5)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
