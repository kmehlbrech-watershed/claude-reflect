#!/usr/bin/env python3
"""Mask secret-shaped text before claude-reflect stores or prints it.

The capture hook sees every prompt, and the extractors read whole session
files. Without this, a correction like "no, use postgres://app:hunter2@db"
lands verbatim in the learnings queue, its PreCompact backups, and from
there can be proposed for a CLAUDE.md or AGENTS.md that gets committed.

The patterns mirror reflect-mod's SECRET list (mod/hooks/register.tsx) so
both ways of running claude-reflect agree on what counts as a secret, plus
passwords embedded in URLs. reflect-mod drops secret-shaped prompts; here
only the secret itself is replaced, so the rest of the learning survives
("use DATABASE_URL=[REDACTED] from .env" is still a useful rule).

Set CLAUDE_REFLECT_REDACT=false to turn redaction off.
"""
import os
import re
from typing import List, Optional, Pattern, Tuple

REDACTED = "[REDACTED]"

# (pattern, group to mask). Group 0 masks the whole match; a numbered group
# masks only that part, so "password: hunter2" keeps "password: ".
# Order matters: specific shapes first, the generic long-token rule last.
_SECRET_PATTERNS: List[Tuple[Pattern[str], int]] = [
    # A PEM private key, through its END line when present.
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"), 0),
    # Prefixed API keys: OpenAI/Anthropic/Stripe (sk-, pk-, rk-), GitHub, Slack, GitLab.
    (re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghs|github_pat|xox[abprs]|glpat)[-_][A-Za-z0-9_-]{8,}", re.IGNORECASE), 0),
    # AWS access key id.
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), 0),
    # Google API key.
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"), 0),
    # A JWT, signature included.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]+)?"), 0),
    # The password in scheme://user:password@host.
    (re.compile(r"(?<=://)[^/\s:@]+:([^/\s@]+)(?=@)"), 1),
    # "password: x", "token = x", "api key is x" (and the Russian words reflect-mod knows).
    (re.compile(
        r"(?:password|passwd|passphrase|secret|token|api[ _-]?key|пароль|токен|ключ)"
        r"\s*(?:\bis\b|:|=)\s*(\S{4,})",
        re.IGNORECASE,
    ), 1),
    # A long token: 32+ letters, digits, - or _, with at least one of each
    # letter and digit. Paths and dotted names don't qualify.
    (re.compile(
        r"(?<![A-Za-z0-9_-])(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{32,}"
    ), 0),
]


def redaction_enabled() -> bool:
    """On unless CLAUDE_REFLECT_REDACT is set to false/0/no/off."""
    value = os.environ.get("CLAUDE_REFLECT_REDACT", "true").strip().lower()
    return value not in ("false", "0", "no", "off")


def _mask_group(match: "re.Match[str]", group: int) -> str:
    whole = match.group(0)
    start = match.start(group) - match.start(0)
    end = match.end(group) - match.start(0)
    return whole[:start] + REDACTED + whole[end:]


def redact_secrets(text: Optional[str]) -> Optional[str]:
    """Return text with secret-shaped substrings replaced by [REDACTED]."""
    if not text or not isinstance(text, str) or not redaction_enabled():
        return text
    for pattern, group in _SECRET_PATTERNS:
        if group == 0:
            text = pattern.sub(REDACTED, text)
        else:
            text = pattern.sub(lambda m, g=group: _mask_group(m, g), text)
    return text
