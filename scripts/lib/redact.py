#!/usr/bin/env python3
"""Mask secret-shaped text before claude-reflect stores or prints it.

The capture hook sees every prompt, and the extractors read whole session
files. Without this, a correction like "no, use postgres://app:hunter2@db"
lands verbatim in the learnings queue, its PreCompact backups, and from
there can be proposed for a CLAUDE.md or AGENTS.md that gets committed.

The patterns start from reflect-mod's SECRET list (mod/hooks/register.tsx)
so both ways of running claude-reflect agree on the clear cases. reflect-mod
drops a secret-shaped prompt; here only the secret is replaced, so the rest
of the learning survives ("use DATABASE_URL=[REDACTED] from .env" is still
a useful rule). Because what is left becomes a CLAUDE.md rule, the rules
favour precision: "the API key is loaded from .env", `token: string`, git
SHAs, UUIDs and model ids are left alone, at the cost of missing secrets
that look like ordinary words.

Set CLAUDE_REFLECT_REDACT=false to turn redaction off.
"""
import os
import re
from typing import Callable, List, Optional, Pattern, Tuple, Union

REDACTED = "[REDACTED]"

_Match = "re.Match[str]"


def _has_digit(s: str) -> bool:
    return any(c.isdigit() for c in s)


def _has_letter(s: str) -> bool:
    return any(c.isalpha() for c in s)


# --- Prefixed API keys -------------------------------------------------------

# Vendor prefixes that are keys whatever follows them.
_VENDOR_KEY = re.compile(
    r"\b(?:sk-ant-|sk-proj-|(?:sk|pk|rk)_(?:live|test)_|gh[pousr]_|github_pat_"
    r"|xox[abprs]-|glpat-)[A-Za-z0-9_-]{8,}"
)

# Bare sk/pk/rk prefixes are also ordinary identifiers (pk_orders_customer_id,
# a DynamoDB sk_...), so the remainder must look random.
_GENERIC_KEY = re.compile(r"\b(?:sk|pk|rk)[-_]([A-Za-z0-9_-]{16,})")


def _generic_key(m: _Match) -> str:
    rest = m.group(1)
    mixed_case = any(c.isupper() for c in rest) and any(c.islower() for c in rest)
    if (_has_digit(rest) and _has_letter(rest)) or (len(rest) >= 20 and mixed_case):
        return REDACTED
    return m.group(0)


# --- Bearer tokens -----------------------------------------------------------

_BEARER = re.compile(r"\b(Bearer\s+)([A-Za-z0-9._~+/-]{8,}=*)", re.IGNORECASE)


def _bearer(m: _Match) -> str:
    # "Bearer authentication" is prose; a token has a digit.
    if _has_digit(m.group(2)):
        return m.group(1) + REDACTED
    return m.group(0)


# --- URL passwords -----------------------------------------------------------

# scheme://user:password@host. The user may be empty (redis://:pw@host). The
# password is greedy, so it backtracks to the LAST @ before the host and a
# password containing @ is masked whole.
_URL_PASSWORD = re.compile(r"(?<=://)([^/\s:@]*:)([^/\s]+)(?=@)")


def _url_password(m: _Match) -> str:
    return m.group(1) + REDACTED


# --- key = value -------------------------------------------------------------

# A key (optionally JSON-quoted), a separator, and a quoted or bare value.
# The key is the whole identifier, taken atomically ((?=(...))\N, since
# Python 3.8 has no possessive quantifiers) and capped at 64 characters, and
# the value is capped too, so long runs of word characters stay linear.
_KEY_VALUE = re.compile(
    r"(?<!\w)(?P<q>[\"']?)"
    r"(?P<key>(?:api|access|secret|private)[ ]key|(?=(?P<ident>[^\W\d][\w.-]{0,63}))(?P=ident))"
    r"(?P=q)"
    r"(?P<sep>\s*(?:[:=]|\bis\b)\s*)"
    r"(?:(?P<quote>[\"'])(?P<qval>[^\"'\n]{1,200})(?P=quote)"
    r"|(?P<val>[^\s\"'`,;&]{1,200}))",
    re.IGNORECASE,
)

_PASSWORD_WORDS = {"password", "passwd", "pass", "pwd", "passphrase", "пароль"}
_SECRET_WORDS = _PASSWORD_WORDS | {"secret", "token", "apikey", "токен", "ключ"}
_KEY_QUALIFIERS = {"api", "access", "secret", "private", "client", "signing", "encryption", "master"}

# Values that describe a secret rather than being one.
_STOPWORDS = {
    "true", "false", "none", "null", "nil", "undefined", "yes", "no", "on", "off",
    "string", "str", "number", "int", "integer", "bool", "boolean", "float", "object",
    "any", "bytes", "optional", "required", "empty", "unset", "missing", "invalid",
    "secret", "password", "token", "redacted",
}
# Words with digits that are not secrets ("the secret is base64-encoded").
_TECH_WORDS = {
    "base32", "base64", "sha1", "sha256", "sha384", "sha512", "md5", "utf8", "utf16",
    "oauth1", "oauth2", "http2", "http3", "ipv4", "ipv6", "s3", "ec2", "x509", "pkcs8",
    "pkcs12", "aes128", "aes256", "rsa2048", "rsa4096", "ed25519", "argon2", "bcrypt2",
    "1password", "2fa", "mfa2",
}
_SYMBOLS = set("!@#$%^&*+=~/\\|?:<>")

_NUMBER = re.compile(r"[-+]?\d[\d_,]*(?:\.\d+)?$")
_NUMBER_WITH_UNIT = re.compile(r"\d+(?:\.\d+)?[A-Za-z]{1,3}$")  # 100k, 30s, 10MB
_VERSION = re.compile(r"v?\d+(?:\.\d+)+[\w.-]*$", re.IGNORECASE)
_CODE_REF = re.compile(
    r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?:\(.*|\[.*)$"  # call or subscript: getenv(, Optional[str]
    r"|[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$"  # dotted name: settings.SECRET_KEY
)
_CAMEL = re.compile(r"([a-z0-9])([A-Z])")


def _key_segments(key: str) -> List[str]:
    """'AWS_SECRET_ACCESS_KEY' -> ['aws', 'secret', 'access', 'key']; camelCase too."""
    return [s.lower() for s in re.split(r"[\s_.-]+", _CAMEL.sub(r"\1 \2", key)) if s]


def _key_kind(key: str) -> Optional[str]:
    """'password' or 'secret' when the key names one, else None.

    The secret word must END the key (optionally followed by _key/_value):
    max_token and client_secret name a secret, token_type and
    password_policy do not.
    """
    segs = _key_segments(key)
    if not segs:
        return None
    last = segs[-1]
    if last in _PASSWORD_WORDS:
        return "password"
    if last in _SECRET_WORDS:
        return "secret"
    if len(segs) >= 2 and last in ("key", "value"):
        # SECRET_KEY, AWS_SECRET_ACCESS_KEY, api_key, PASSWORD_VALUE
        if segs[-2] in _PASSWORD_WORDS:
            return "password"
        if segs[-2] in _SECRET_WORDS or (last == "key" and segs[-2] in _KEY_QUALIFIERS):
            return "secret"
    return None


def _value_is_secret(value: str, kind: str, quoted: bool, is_form: bool) -> bool:
    v = value.strip()
    if len(v) < 4 or v.lower() in _STOPWORDS:
        return False
    # References and placeholders: $VAR, ${{ secrets.X }}, <your-token>, %s, [REDACTED].
    if v[0] in "$<{%[(`*":
        return False
    # Paths and URLs (a URL's own password has its own rule).
    if "://" in v or v[0] in "/~" or v.startswith(("./", "../")) or re.match(r"[A-Za-z]:\\", v):
        return False
    if _NUMBER.match(v):
        return kind == "password"
    if _NUMBER_WITH_UNIT.match(v) or _VERSION.match(v) or _CODE_REF.match(v):
        return False
    if re.split(r"[-_./]", v.lower())[0] in _TECH_WORDS:
        return False
    if quoted:
        if is_form or kind == "password":
            # A quoted password may be one plain word or a passphrase.
            return len(v) >= 6 or _has_digit(v) or any(c in _SYMBOLS or c.isspace() for c in v)
        return _has_digit(v) or any(c in _SYMBOLS or c.isspace() for c in v)
    if is_form:
        # "password is required" / "token is stored in keychain" are prose.
        return _has_digit(v) and _has_letter(v)
    return (_has_digit(v) and _has_letter(v)) or any(c in _SYMBOLS for c in v)


def _key_value_span(m: _Match) -> Optional[Tuple[int, int]]:
    """The span of the value to mask, or None when this is not a secret."""
    kind = _key_kind(m.group("key"))
    if kind is None:
        return None
    is_form = m.group("sep").strip().lower() == "is"
    if m.group("qval") is not None:
        if not _value_is_secret(m.group("qval"), kind, True, is_form):
            return None
        return m.span("qval")
    core = m.group("val").rstrip(".,:;)]}>!?")
    if not _value_is_secret(core, kind, False, is_form):
        return None
    return m.start("val"), m.start("val") + len(core)


def _redact_key_values(text: str) -> str:
    """Like _KEY_VALUE.sub, but after a rejected match the search resumes
    just past its key, so "use:password=hunter2x" is not swallowed whole by
    the "use:" match."""
    out = []
    pos = 0
    search_from = 0
    while True:
        m = _KEY_VALUE.search(text, search_from)
        if not m:
            break
        span = _key_value_span(m)
        if span is None:
            search_from = m.end("key")
            continue
        out.append(text[pos:span[0]])
        out.append(REDACTED)
        pos = search_from = span[1]
    out.append(text[pos:])
    return "".join(out)


# --- Long random tokens ------------------------------------------------------

# 32+ token characters standing alone: not part of a path (/x/<tok>), a dotted
# name or a file name (<tok>.json).
_LONG_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_\-./\\])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-]|\.[A-Za-z0-9]|/)"
)
_HEX = re.compile(r"[0-9a-fA-F-]+$")


def _long_token(m: _Match) -> str:
    tok = m.group(0)
    if not (_has_digit(tok) and _has_letter(tok)):
        return tok
    # Git SHAs, digests, UUIDs.
    if _HEX.match(tok):
        return tok
    # A slug like claude-opus-4-5-20251101-with-extended-thinking is words and
    # numbers; a random token mixes letters and digits within a long run.
    if not any(
        len(seg) >= 8 and _has_digit(seg) and _has_letter(seg)
        for seg in re.split(r"[-_]", tok)
    ):
        return tok
    return REDACTED


# (pattern, replacement). Order matters: specific shapes first, so the
# generic rules see [REDACTED] instead of half a key.
_Replacement = Union[str, Callable[[_Match], str], None]
_SECRET_PATTERNS: List[Tuple[Pattern[str], _Replacement]] = [
    # A PEM private key, through its END line when present.
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"), REDACTED),
    (_VENDOR_KEY, REDACTED),
    (_GENERIC_KEY, _generic_key),
    # AWS access key id (long-term and temporary).
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), REDACTED),
    # Google API key.
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"), REDACTED),
    # A JWT, signature included.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]+)?"), REDACTED),
    (_BEARER, _bearer),
    (_URL_PASSWORD, _url_password),
    (_KEY_VALUE, None),  # applied by _redact_key_values
    (_LONG_TOKEN, _long_token),
]


def redaction_enabled() -> bool:
    """On unless CLAUDE_REFLECT_REDACT is set to false/0/no/off."""
    value = os.environ.get("CLAUDE_REFLECT_REDACT", "true").strip().lower()
    return value not in ("false", "0", "no", "off")


def redact_secrets(text: Optional[str]) -> Optional[str]:
    """Return text with secret-shaped substrings replaced by [REDACTED]."""
    if not text or not isinstance(text, str) or not redaction_enabled():
        return text
    for pattern, replacement in _SECRET_PATTERNS:
        if replacement is None:
            text = _redact_key_values(text)
        else:
            text = pattern.sub(replacement, text)
    return text
