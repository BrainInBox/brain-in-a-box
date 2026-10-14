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
    # Services' key shapes (PostHog personal key, Neon, GitLab, Hugging Face,
    # Groq, xAI, SendGrid, DigitalOcean, Notion, Linear, Tavily, Stripe webhook,
    # Voyage): a real vault held one of them in clear, 2026-10-14.
    ("service", re.compile(
        r"\b(?P<v>phx_[A-Za-z0-9]{30,}|npg_[A-Za-z0-9]{12,}|glpat-[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{30,}|"
        r"gsk_[A-Za-z0-9]{40,}|xai-[A-Za-z0-9]{40,}|SG\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|dop_v1_[a-f0-9]{60,}|"
        r"ntn_[A-Za-z0-9]{40,}|lin_api_[A-Za-z0-9]{30,}|tvly-[A-Za-z0-9-]{30,}|whsec_[A-Za-z0-9+/=]{24,}|"
        r"pa-[A-Za-z0-9_]{40,})(?![A-Za-z0-9_-])")),
    # The keyword alone or inside a name (TOKEN_KEY, DB_PASSWORD, JWT_SECRET).
    ("mot_de_passe", re.compile(
        r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9]+_)*(?P<k>password|passwd|pwd|mot de passe|mdp|secret|token|api[_ -]?key|"
        r"client[_-]?secret|private[_-]?key)(?:_(?:key|secret|token|value|pass|pwd|password))?[*_\"'`\\]{0,3}\s{0,2}[:=]\s{0,2}[*_\"'`\\]{0,3}"
        r"(?P<v>[^\s\"'`,;]+)", re.I)),
]
_PEM = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[ \t]*\r?\n(?P<v>(?:.*\n){1,200}?)"
                  r"[ \t]*-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----")
_PLACEHOLDERS = ("[secret", "[redacted", "${", "{{", "<", "***", "xxx", "voir", "see", "todo", "...", "…")
_EXAMPLE_WORDS = {"changeme", "change-me", "example", "exemple", "password", "motdepasse", "mot-de-passe",
                  "pass", "secret", "token", "null", "none", "true", "false", "tbd", "redacted", "masked"}
_HEX64 = re.compile(r"(?:0x)?[0-9a-fA-F]{64}")
STRONG_KEYWORDS = re.compile(r"password|passwd|pwd|mot de passe|mdp|api[_ -]?key|client[_-]?secret|private[_-]?key", re.I)


# What follows a keyword without being a value (calibrated on a real vault,
# 2026-10-14, where 55 of the 59 values masked were one of these): an environment
# variable's NAME, a reference to one, code that reads it, a path, an ARN, a date.
_REFERENCE = re.compile(
    r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$|^\$[{(]?[A-Za-z_]"
    r"|^(?:process\.env|import\.meta|os\.environ|env|config|this|self|req|res|body|ctx|secrets|vars|github|Joi)[.\[(]"
    r"|^[A-Za-z_][\w$]*(?:\.[A-Za-z_$][\w$]*)+(?::\d+(?:-\d+)?)?$|^[A-Za-z_]\w*=\w+$|[()]|\|\||=>|^arn:"
    r"|^/?[\w.<>-]+(?:/[\w.<>-]+)+(?::\d+(?:-\d+)?)?$|\$\{|&\w+=|^\d{4}-\d{2}-\d{2}"
    # A secret's NAME (pg-ledger-master-key, shop-api-token-key), not its value.
    r"|^[a-z0-9]+(?:[-_][a-z0-9]+)*[-_](?:key|keys|secret|token|pw|pwd|password|creds|seed|cert|pem)$")
_FAKE_WORDS = ("example", "exemple", "changeme", "placeholder", "dummy", "fake", "sample", "your-", "your_", "votre", "xxxx",
               "…", "...")


def _is_example(value, after_keyword=False):
    low = value.lower().strip("*_.)")
    if not low or low.startswith(_PLACEHOLDERS + ("[",)) or low in _EXAMPLE_WORDS or any(w in low for w in _FAKE_WORDS) \
            or set(low) <= set("x*•.-_#"):
        return True
    bare = value.strip("*_.)}]\\").rstrip("=")
    return after_keyword and bool(_REFERENCE.search(bare)) and not (
        "/" in bare and not re.search(r"(?:^|/)[a-z]{3,}(?:[./:]|$)", bare))


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
                token = re.split(r"[\s\"'`(]", text[max(0, m.start() - 120):m.start()])[-1]
                if token.startswith("arn:"):
                    continue  # ...:secret:name-AbC123 names a secret in an ARN, it is not one
                start, end = _value_span(m)
                value = text[start:end]
                strong = bool(STRONG_KEYWORDS.fullmatch(m.group("k")))
                # A quoted value after a password keyword is a literal ("Password": "...").
                literal = strong and text[start - 1:start] in ("\"", "'")
                if _is_example(value, after_keyword=not literal):
                    continue
                if _HEX64.fullmatch(value):
                    found.append((start, end, kind, False))
                elif len(value) >= 8 and _classes(value) >= 2:
                    # One or two plain words ("Keychain", "non-retryable"), or a lowercase
                    # name after "secret" or "token" (pg-ledger-master): a label more
                    # often than a password; reported, never masked.
                    label = (len(value) < 16 and re.fullmatch(r"[^\W\d_]+(?:[-_][^\W\d_]+){0,2}", value)) or \
                        (not strong and re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+){2,}", value))
                    found.append((start, end, kind, bool(literal or not label)))
                elif len(value) >= 4 and (len(value) < 8 or strong) \
                        and not re.fullmatch(r"[a-zà-ÿ]+(?:-[a-zà-ÿ]+)*|[A-Z]{2,6}|\d{1,7}", value):
                    # A short lowercase word after "token =" is prose, a short capital one a
                    # coin ("token": "USDT"), a few digits a count.
                    found.append((start, end, kind, False))
                continue
            start, end = m.span("v")
            value = text[start:end]
            if _is_example(value, after_keyword=kind in ("url", "bearer")) or (kind in ("url", "bearer") and len(value) < 3):
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
