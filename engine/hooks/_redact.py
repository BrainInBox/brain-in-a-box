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
