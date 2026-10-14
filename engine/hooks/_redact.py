"""Secret masking shared by the hooks that copy session text into the vault
(session-recap) or send it to a model (daily-reflection).

The vault is committed and pushed every night, and the reflection ships whole
transcripts to `claude -p`: a key pasted in a chat must not travel with them.
Safety net, not a guarantee: a secret in an unusual format, or written as
plain prose, passes through.

Not a hook: installed next to them, never registered in settings.json.
"""
import re

MASK = "[REDACTED]"

_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S), MASK),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b"), MASK),
    (re.compile(r"\bsk-(?:ant-|or-|proj-)?[A-Za-z0-9_-]{20,}"), MASK),
    (re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b"), MASK),
    (re.compile(r"\bxox[abpors]-[A-Za-z0-9-]{10,}"), MASK),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), MASK),
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"), MASK),  # Telegram bot token
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), MASK),
    (re.compile(r"https://discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+"), MASK),
    # Raw 32-byte keys (EVM / Solana-style private keys). Also hides sha256
    # digests, an acceptable loss in a journal.
    (re.compile(r"\b(?:0x)?[0-9a-fA-F]{64}\b"), MASK),
    # Below, group 1 (the context) is kept and only the value is masked.
    (re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]{20,}"), r"\1" + MASK),
    (re.compile(r"(://)[^:\s/\"'@]+:[^@\s\"'/]+(?=@)"), r"\1" + MASK),
    (re.compile(r"(?i)((?<![a-z])(?:password|passwd|pwd|secret|api_key|apikey|api-key|token|access_key|client_secret|private_key)"
                r"[\"'\\]{0,3}\s{0,2}[:=]\s{0,2}[\"'\\]{0,3})[^\s\"'\\,;&{}]{4,}"), r"\1" + MASK),
]


def redact(text):
    """Returns (masked_text, number_of_masks)."""
    total = 0
    for rx, repl in _PATTERNS:
        text, n = rx.subn(repl, text)
        total += n
    return text, total


# The night check (controle.py) MASKS values inside the vault, so a false
# positive edits a note: only shapes that are secrets on sight. Group "v" is the
# value, the rest of the match stays in the note.
VAULT_SECRET_PATTERNS = [
    ("aws", re.compile(r"\b(?P<v>(?:AKIA|ASIA)[0-9A-Z]{16})\b")),
    ("github", re.compile(r"\b(?P<v>gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b")),
    ("ia", re.compile(r"(?<![A-Za-z0-9_-])(?P<v>sk-(?:ant-|or-|proj-)?[A-Za-z0-9_-]{20,})")),
    ("stripe", re.compile(r"\b(?P<v>[sr]k_live_[A-Za-z0-9]{16,})\b")),
    ("slack", re.compile(r"\b(?P<v>xox[abposr]-[A-Za-z0-9-]{10,})")),
    ("google", re.compile(r"\b(?P<v>AIza[0-9A-Za-z_-]{35})(?![0-9A-Za-z_-])")),
    ("telegram", re.compile(r"(?<![A-Za-z0-9_-])(?P<v>\d{8,10}:[A-Za-z0-9_-]{35})(?![A-Za-z0-9_-])")),
    ("jwt", re.compile(r"(?<![A-Za-z0-9_-])(?P<v>eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})")),
    ("discord", re.compile(r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/(?P<v>[A-Za-z0-9_-]{20,})")),
    ("url", re.compile(r"\b[a-z][a-z0-9+.-]{1,20}://[^:\s/\"'@<>]+:(?P<v>[^@\s\"'/<>]+)@", re.I)),
    ("bearer", re.compile(r"\bbearer\s+(?P<v>[A-Za-z0-9._~+/=-]{20,})", re.I)),
    ("mot_de_passe", re.compile(
        r"(?<![A-Za-z0-9_])(?:password|passwd|pwd|mot de passe|mdp|secret|token|api[_ -]?key|"
        r"client[_-]?secret|private[_-]?key)[*_\"'`\\]{0,3}\s{0,2}[:=]\s{0,2}[*_\"'`\\]{0,3}"
        r"(?P<v>[^\s\"'`,;]+)", re.I)),
]
_PEM = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[ \t]*\r?\n(?P<v>(?:.*\n){1,200}?)"
                  r"[ \t]*-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----")
_PLACEHOLDERS = ("[secret", "[redacted", "${", "{{", "<", "***", "xxx", "voir", "see", "todo", "...", "…")
_EXAMPLE_WORDS = {"changeme", "change-me", "example", "exemple", "password", "motdepasse", "mot-de-passe",
                  "pass", "secret", "token", "null", "none", "true", "false", "tbd", "redacted", "masked"}
_HEX64 = re.compile(r"(?:0x)?[0-9a-fA-F]{64}")
STRONG_KEYWORDS = re.compile(r"password|passwd|pwd|mot de passe|mdp|api[_ -]?key|client[_-]?secret|private[_-]?key", re.I)


def _is_example(value):
    low = value.lower().strip("*_.)")
    return (not low or low.startswith(_PLACEHOLDERS) or low in _EXAMPLE_WORDS
            or "example" in low or "exemple" in low or "changeme" in low or set(low) <= set("x*•.-_#"))


def _classes(value):
    return sum((any(c.islower() for c in value), any(c.isupper() for c in value),
                any(c.isdigit() for c in value), any(not c.isalnum() for c in value)))


def _jwt_header_has_alg(token):
    import base64
    head = token.split(".", 1)[0]
    try:
        return b'"alg"' in base64.urlsafe_b64decode(head + "=" * (-len(head) % 4))
    except (ValueError, TypeError):
        return False


def _value_span(m):
    """The value of a keyword match without the trailing markdown or prose punctuation."""
    start, end = m.span("v")
    while end > start and m.string[end - 1] in "*_.)":
        end -= 1
    return start, end


def _scan(text):
    """(start, end, kind, sure) for every candidate value, overlaps removed."""
    found = []
    for m in _PEM.finditer(text):
        pos = m.start("v")
        for line in m.group("v").split("\n"):
            body = line.strip()
            if body:
                s = pos + line.index(body)
                found.append((s, s + len(body), "cle_privee", True))
            pos += len(line) + 1
    for kind, rx in VAULT_SECRET_PATTERNS:
        for m in rx.finditer(text):
            if kind == "mot_de_passe":
                start, end = _value_span(m)
                value = text[start:end]
                if _is_example(value):
                    continue
                if _HEX64.fullmatch(value):
                    found.append((start, end, kind, False))
                elif len(value) >= 8 and _classes(value) >= 2:
                    found.append((start, end, kind, True))
                elif len(value) >= 4 and (len(value) < 8 or STRONG_KEYWORDS.match(m.group(0))):
                    found.append((start, end, kind, False))
                continue
            start, end = m.span("v")
            value = text[start:end]
            if kind in ("url", "bearer") and (_is_example(value) or len(value) < 3):
                continue
            if kind == "jwt" and not _jwt_header_has_alg(value):
                continue
            found.append((start, end, kind, True))
    found.sort(key=lambda f: (f[0], -f[1]))
    kept = []
    for f in found:
        if kept and f[0] < kept[-1][1]:
            continue
        kept.append(f)
    return kept


def find_secrets(text):
    """[(start, end, kind)] of the secret VALUES in text, high precision only."""
    return [(s, e, k) for s, e, k, sure in _scan(text) if sure]


def find_doubtful_secrets(text):
    """[(start, end, kind)] of values that may be secrets (short value after a
    keyword, a 64-hex digest next to one): reported, never masked."""
    return [(s, e, k) for s, e, k, sure in _scan(text) if not sure]
