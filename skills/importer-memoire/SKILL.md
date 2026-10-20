---
name: importer-memoire
description: Brings what ChatGPT or Claude (claude.ai) knows about the user into their brain - from the official export (.zip) or from the answer to a sentence pasted in ChatGPT or Claude - and says which AI suits them. Use when the user says "importe ma mémoire", "importer ma mémoire ChatGPT", "récupère ce que ChatGPT sait de moi", "j'ai mon export ChatGPT / Claude", "transfère mes souvenirs", "comment exporter mes données ChatGPT", "Claude ou ChatGPT, lequel je prends ?", "import my ChatGPT memory", "move my memories from ChatGPT", or shares a conversations.json or an export .zip.
allowed-tools: Bash(~/.local/bin/importer-memoire conseil), Bash(~/.local/bin/importer-memoire phrase), Bash(~/.local/bin/importer-memoire statut)
---

# Importer ma mémoire - ChatGPT or Claude into the brain

`~/.local/bin/importer-memoire` reads an export of ChatGPT or Claude, or an answer copied
from one of them, and has the user's AI sort it, without any tool, into lasting facts: who
they are, preferences, projects, people, tools, decisions, each with its source. It writes
ONE new note, `Imports/<date>-import-<source>.md` in the brain, and one line in
`Profile/memory.md`. It never overwrites a note.

You run the launcher and explain what it says, in the user's language, in a few short
lines. The program is the only one that reads the export and writes the note.

## Rules (they protect the user)

1. **Never read the export or the copied answer yourself**: no Read, cat, unzip, grep,
   head or python on them, not even "to check". They hold months of private conversations
   and text from anywhere. You have tools; the sorting runs without any, on purpose.
2. **Never run `voir`**: it prints the conversations. Tell the user they can run it
   themselves in a terminal to see exactly what would leave.
3. **The copied answer never goes through this chat**: the user copies it in ChatGPT or
   Claude, then you run `preparer --presse-papiers`. If they paste it here anyway, do not
   repeat it: ask them to copy it again and run the command.
4. **Ask before sending.** `preparer` prints what was read, the number of requests, how
   long, where the text goes and an agreement code. Show those lines, ask "Je lance ?",
   and run `lancer` only after a clear yes, with exactly the `--appels` and `--accord`
   printed for the choice they made. Never more requests than they agreed to.
5. Each request counts in the user's AI usage limit (no extra cost). The default is
   "normal" (4 requests); "rapide" is 2, "complet" up to 10.

## Commands (same on Mac, Linux and Windows, from the Bash tool)

| The user wants | Run |
|---|---|
| which AI suits them | `~/.local/bin/importer-memoire conseil` |
| to import an export file | `~/.local/bin/importer-memoire preparer "<path of the .zip>"` (1 to 4 files: Claude may send several zips) |
| to import by copy-paste | see "Copy-paste" below |
| another number of requests | `~/.local/bin/importer-memoire estimer <plan> --appels <N>` |
| to start, after their yes | `~/.local/bin/importer-memoire lancer <plan> --appels <N> --accord <code>`, in the background (one minute per request), then `statut` |
| the imports done | `~/.local/bin/importer-memoire statut` |
| to undo an import | `~/.local/bin/importer-memoire defaire <import> --essai`, show it, then without `--essai` |
| to drop a prepared plan | `~/.local/bin/importer-memoire annuler <plan>` |

Exit codes: 0 done; 2 refused (the message says why, e.g. a JSON of links or the
account-only zip of Claude); 3 already imported (nothing to do); 4 the AI cannot be asked
(not logged in, usage limit); 5 partial: what was sorted is written, `preparer` on the
same file later resumes the rest.

## Copy-paste (best for ChatGPT's saved memories)

1. Run `~/.local/bin/importer-memoire phrase` and give the user the sentence it prints.
2. They open a NEW conversation in ChatGPT or Claude, paste it, and click "Copy" on the
   code block of the answer. If the answer says there is more, they write "Continue" and
   copy again.
3. Run `~/.local/bin/importer-memoire preparer --presse-papiers --de chatgpt` (or
   `--de claude`); for a second part, add `--plan <plan>` to the same command.
4. Then rule 4.

## Getting the export

- **ChatGPT**: Settings > Data controls > Export data. An e-mail arrives with a link valid
  24 hours: download the .zip. Recent exports often no longer carry ChatGPT's saved
  memories: for those, the copy-paste works better. If the browser opened the .zip into a
  folder (Safari does), the user zips that folder again (right click > Compress) and gives
  that .zip.
- **Claude** (claude.ai, web or desktop app, not the phone): Settings > Privacy > Export
  data. An e-mail with a link valid 24 hours (signed in). It may be one .zip, several
  .zip, or a small file of links: the user opens the links in their browser and downloads
  the .zip files. The program never follows a link itself.

## After

Say how many facts, in which note, and that the AI can be wrong: offer to read the note
together and fix or delete what is false. The note is a normal note of the brain: the
night check reads it, gbrain indexes it, the nightly commits it.
