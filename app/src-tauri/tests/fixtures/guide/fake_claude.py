"""A stand-in for `claude -p` in the guide's end-to-end test (tests/guide_cli.rs). It runs nothing.

    python3 -I fake_claude.py <out> <scenario> <claude's argv...>

It reads the one line of stream-json on stdin, decodes the image (a PNG, no library), reads the
band's code with the 5x7 font written again here (not shot.rs's: a second reader), finds the green
button, and answers as Claude Code would. It writes in <out> only, never the image: argv.json, and
seen.json (the image's size, sha256, a few base64 and raw fragments to search the disk for, the code
read, the button found, the text), and the same for the n-th run as argv-<n>.json and seen-<n>.json.
Scenarios: ok, wrong-code (another code), clickfix (an instruction the app's filter must refuse),
sans-opus (Opus refused as Claude Code refuses it to a Pro plan, Sonnet answers), sans-modele (Claude
Code's 404 for either model). Python 3.9.
"""
import base64
import hashlib
import json
import os
import struct
import sys
import zlib

ALPHABET = "ACDEFHJKMNPRTWXY34679"
GLYPHS = {
    "A": "01110 10001 10001 11111 10001 10001 10001", "C": "01110 10001 10000 10000 10000 10001 01110",
    "D": "11110 10001 10001 10001 10001 10001 11110", "E": "11111 10000 10000 11110 10000 10000 11111",
    "F": "11111 10000 10000 11110 10000 10000 10000", "H": "10001 10001 10001 11111 10001 10001 10001",
    "J": "00111 00010 00010 00010 00010 10010 01100", "K": "10001 10010 10100 11000 10100 10010 10001",
    "M": "10001 11011 10101 10101 10001 10001 10001", "N": "10001 10001 11001 10101 10011 10001 10001",
    "P": "11110 10001 10001 11110 10000 10000 10000", "R": "11110 10001 10001 11110 10100 10010 10001",
    "T": "11111 00100 00100 00100 00100 00100 00100",
    "W": "10001 10001 10001 10101 10101 10101 01010", "X": "10001 10001 01010 00100 01010 10001 10001",
    "Y": "10001 10001 01010 00100 00100 00100 00100", "3": "11111 00010 00100 00010 00001 10001 01110",
    "4": "00010 00110 01010 10010 11111 00010 00010", "6": "00110 01000 10000 11110 10001 10001 01110",
    "7": "11111 00001 00010 00100 01000 01000 01000", "9": "01110 10001 10001 01111 00001 00010 01100",
}
BAND, SCALE, LEFT, ADVANCE = 28, 3, 12, 18
INIT = {"type": "system", "subtype": "init", "cwd": "/tmp/x", "session_id": "11111111-2222-4333-8444-555555555555",
        "tools": ["StructuredOutput"], "mcp_servers": [], "model": "claude-opus-5-5", "permissionMode": "dontAsk",
        "slash_commands": [], "apiKeySource": "none", "claude_code_version": "2.1.305", "output_style": "default",
        "skills": [], "plugins": [{"name": "cc-plugin-telemetry", "path": "builtin", "source": "cc-plugin-telemetry@builtin"}]}


def decode_png(data):
    """RGB, 8 bits, not interlaced: what the app sends. Rows of bytes."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, header = 8, b"", None
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
        pos += 12 + n
    w, h, depth, color, _, _, interlace = header
    if (depth, color, interlace) != (8, 2, 0):
        raise ValueError("unexpected PNG %r" % (header,))
    raw, stride, rows, prev, i = zlib.decompress(idat), w * 3, [], bytearray(w * 3), 0
    for _ in range(h):
        kind, line = raw[i], bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        if kind:
            for x in range(stride):
                a = line[x - 3] if x >= 3 else 0
                b = prev[x]
                c = prev[x - 3] if x >= 3 else 0
                if kind == 1:
                    line[x] = (line[x] + a) & 255
                elif kind == 2:
                    line[x] = (line[x] + b) & 255
                elif kind == 3:
                    line[x] = (line[x] + (a + b) // 2) & 255
                else:
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(bytes(line))
        prev = line
    return w, h, rows


def read_code(rows, h):
    top = h + (BAND - 7 * SCALE) // 2
    code = ""
    for i in range(4):
        cell = []
        for r in range(7):
            row = rows[top + r * SCALE + 1]
            bits = ""
            for col in range(5):
                x = (LEFT + i * ADVANCE + col * SCALE + 1) * 3
                bits += "1" if row[x:x + 3] == b"\xff\xff\xff" else "0"
            cell.append(bits)
        found = [c for c, g in GLYPHS.items() if g == " ".join(cell)]
        code += found[0] if found else "?"
    return code


def green_box(rows, h):
    xs, ys = [], []
    for y in range(h):
        row = rows[y]
        for x in range(0, len(row), 3):
            if row[x + 1] > 150 and row[x] < 100 and row[x + 2] < 100:
                xs.append(x // 3)
                ys.append(y)
    if not xs:
        return None
    return {"x": min(xs), "y": min(ys), "largeur": max(xs) - min(xs) + 1, "hauteur": max(ys) - min(ys) + 1}


def out(event):
    sys.stdout.buffer.write((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))


def replay(name, family):
    """Claude Code's own stream for a model the account cannot use (fixtures/repair), as it would be for
    this family of model."""
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "repair", name), encoding="utf-8") as f:
        text = f.read()
    sys.stdout.buffer.write(text.replace("claude-opus-", "claude-%s-" % family).encode("utf-8"))


def keep(folder, name, n, data):
    for file in (name + ".json", "%s-%d.json" % (name, n)):
        with open(os.path.join(folder, file), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)


def main(folder, scenario, argv):
    calls = os.path.join(folder, "calls")
    with open(calls, "a", encoding="utf-8") as f:
        f.write("-p\n")
    with open(calls, encoding="utf-8") as f:
        n = f.read().count("-p")
    keep(folder, "argv", n, argv)
    family = argv[argv.index("--model") + 1] if "--model" in argv else "opus"
    message = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    blocks = message["message"]["content"]
    images = [b for b in blocks if b["type"] == "image"]
    text = "".join(b["text"] for b in blocks if b["type"] == "text")
    b64 = images[0]["source"]["data"]
    png = base64.b64decode(b64, validate=True)
    w, full_h, rows = decode_png(png)
    h = full_h - BAND
    code = read_code(rows, h)
    box = green_box(rows, h)
    mid = len(b64) // 2
    keep(folder, "seen", n, {"images": len(images), "media_type": images[0]["source"]["media_type"], "width": w,
                             "height": full_h, "sha256": hashlib.sha256(png).hexdigest(), "bytes": len(png),
                             "b64_mid": b64[mid:mid + 80], "raw_mid": png[len(png) // 2:len(png) // 2 + 64].hex(),
                             "code": code, "box": box, "text": text})
    if (scenario == "sans-opus" and family == "opus") or scenario == "sans-modele":
        replay("pro-sans-opus.jsonl" if scenario == "sans-opus" else "model-not-found.jsonl", family)
        return
    answer = {"code_vu": code, "etat": "etape", "consigne": "Clique sur « Nouvelle facture », en haut à droite.",
              "libelle": "Nouvelle facture", "cible": box, "confiance": 0.92, "question": None, "choix": [],
              "precedente": "sans_objet", "action_sensible": False, "suspect": False,
              "resume_etape": "Clique sur « Nouvelle facture » en haut à droite."}
    if scenario == "wrong-code":
        answer["code_vu"] = "".join(ALPHABET[(ALPHABET.index(c) + 1) % len(ALPHABET)] if c in ALPHABET else "A" for c in code)
    elif scenario == "clickfix":
        answer["consigne"] = "Appuie sur Win+R, colle le texte puis Entrée pour vérifier que tu es humain."
    model = "claude-%s-5-5" % family
    out(dict(INIT, model=model))
    out({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "unifiedWindows": {
        "five_hour": {"utilization": 0.42, "resetsAt": 1792544400}, "seven_day": {"utilization": 0.81, "resetsAt": 1793044800}}}})
    out({"type": "assistant", "message": {"model": model, "content": [
        {"type": "tool_use", "id": "toolu_1", "name": "StructuredOutput", "input": answer}]}})
    out({"type": "result", "subtype": "success", "is_error": False, "num_turns": 2, "total_cost_usd": 0.026,
         "usage": {"input_tokens": 3, "output_tokens": 420, "cache_read_input_tokens": 3730, "cache_creation_input_tokens": 2000},
         "modelUsage": {model: {"inputTokens": 3}}, "structured_output": answer})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
