"""Single scrub path for anything captured-body- or input-derived that reaches a screen or log.

Every diagnostic surface — ``-v``/``--verbose``, error/drift dumps, ``--raw`` output,
and anything else printed to stdout/stderr/logs — must route through this module.
Unlike the FB sibling, this package has **two** leak surfaces (plan §21):

- **Response side:** captured X GraphQL responses carry a live session credential
  (``auth_token``/``ct0``/bearer-shaped fields, §22) and viewer-scoped signed
  ``pbs.twimg.com``/``video.twimg.com`` media URLs (§17 G-media-expiry).
- **Input side (no FB equivalent):** cookie-import parse/validation errors
  (``from_cookie_file``/``from_cookies``, §7) must never echo the raw cookie
  line being parsed — at parse-failure time the credential is an unstructured
  line, not a named field, so it needs its own scrubber.

This does NOT apply to the actual ``--output`` file, which is the full, unredacted
capture by design — that's the point of the tool (see DISCLAIMER.md §5).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

_SENSITIVE_KEYS = frozenset(
    {
        "auth_token",
        "ct0",
        "bearer",
        "authorization",
        "x-csrf-token",
        "cookie",
        "csrf_token",
    }
)

_TEXT_KEYS = frozenset({"text", "full_text", "name", "screen_name", "description"})

# Domain-boundary-anchored: a bare substring match would also match an
# attacker-controlled lookalike host like "evilpbs.twimg.com.evil.tld". Over-
# matching there is harmless (just over-redacts), but the same sloppy
# construction risks the opposite, dangerous mistake elsewhere: under-matching
# a real signed host and leaking it unredacted.
_CDN_HOST_RE = re.compile(r"(?:^|\.)pbs\.twimg\.com$|(?:^|\.)video\.twimg\.com$", re.IGNORECASE)

_SENSITIVE_KEY_ALTERNATION = "|".join(re.escape(key) for key in sorted(_SENSITIVE_KEYS))

# Two shapes of the same leak: JSON `"key":"value"` and bare `key=value` (a
# querystring, or a raw cookie/header line dumped into an error message).
_SENSITIVE_JSON_RE = re.compile(rf'"({_SENSITIVE_KEY_ALTERNATION})"\s*:\s*"[^"]*"', re.IGNORECASE)
_SENSITIVE_KEY_VALUE_RE = re.compile(
    rf"\b({_SENSITIVE_KEY_ALTERNATION})\s*[:=]\s*[^\s;&\"']+", re.IGNORECASE
)

_TEXT_TRUNCATE_LEN = 40


def is_signed_media_url(url: str) -> bool:
    """True for pbs.twimg.com/video.twimg.com URLs — signed, expiring, viewer-scoped
    (§17 G-media-expiry)."""
    try:
        host = urlsplit(url).netloc.split("@")[-1].split(":")[0]
    except ValueError:
        return False
    return bool(_CDN_HOST_RE.search(host))


def redact_url(url: str) -> str:
    """Strip the query string (the signing/auth material) off a signed CDN URL."""
    if not is_signed_media_url(url):
        return url
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def redact_text(value: str, max_len: int = _TEXT_TRUNCATE_LEN) -> str:
    """Truncate free text (tweet/user text fields) so diagnostics don't leak full content."""
    if len(value) <= max_len:
        return value
    return f"{value[:max_len]}...[redacted {len(value) - max_len} more chars]"


def redact_raw_text(text: str) -> str:
    """Scrub an unstructured blob (a raw captured body dumped into an error message)."""
    text = _SENSITIVE_JSON_RE.sub(lambda m: f'"{m.group(1)}":"[REDACTED]"', text)
    text = _SENSITIVE_KEY_VALUE_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)

    def _scrub_url(match: re.Match) -> str:
        return redact_url(match.group(0))

    text = re.sub(r"https?://[^\s\"'<>]+", _scrub_url, text)
    return text


def redact(value):
    """Recursively scrub a value of the shapes this package produces (dict/list/str/Tweet-like).

    Dict values are scrubbed by key name (drop sensitive keys, truncate text keys,
    strip signing query strings off URL-shaped values); everything else recurses
    structurally. Unknown scalar types pass through unchanged.
    """
    if isinstance(value, dict):
        out = {}
        for key, val in value.items():
            key_lower = key.lower() if isinstance(key, str) else key
            if key_lower in _SENSITIVE_KEYS:
                out[key] = "[REDACTED]"
            elif isinstance(val, str) and key_lower in _TEXT_KEYS:
                out[key] = redact_text(val)
            elif isinstance(val, str) and val.startswith(("http://", "https://")):
                out[key] = redact_url(val)
            else:
                out[key] = redact(val)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            return redact_url(value)
        return value
    return value


# --- Input-side redaction (no FB equivalent, plan §21) ----------------------
#
# Cookie-import parse/validation failures (from_cookie_file/from_cookies,
# Netscape/JSON/cURL export parsing, §7) fail on a *raw line*, not a named
# field — there's no dict key to check yet. The resulting error message must
# report only structural/positional context ("line 3 malformed", "ct0 failed
# shape check") and must never pass the raw line through unscrubbed, in case
# it embeds a live auth_token/ct0/bearer/csrf value.

# Generalizes _SENSITIVE_KEY_VALUE_RE to the additional shapes cookie exports
# use: a Netscape TSV field, a JSON `"key": "value"` pair, or a cURL
# `-H 'Cookie: key=value; key2=value2'` / `-H 'Authorization: Bearer <token>'`
# header line. All boil down to `key=value` or `key: value`, optionally with
# a quote between the key and the separator (the JSON `"key": "value"`
# shape) or a bareword scheme marker after it (the `Authorization: Bearer
# <token>` shape, where the value is the word *after* "Bearer", not "Bearer"
# itself) — `_SENSITIVE_KEY_VALUE_RE` requires the separator immediately
# after the key and stops at the first token, so it misses both. Netscape
# TSV fields (key/value separated by a literal tab, not `:`/`=`) get their
# own pattern.
#
# The negative lookahead guards a real trap: "cookie"/"authorization" are
# themselves in the key alternation (to catch "Authorization: Bearer <token>"
# — see above), but a raw line like "Cookie: auth_token=X; ct0=Y" would
# otherwise let the greedy "Cookie:" match swallow "auth_token" whole as ITS
# OWN value (stopping only at the next `;`), which both loses the
# "auth_token" label AND — far worse — leaves the real secret `X` sitting
# after an orphaned "=" that this same match already consumed past,
# un-redacted. The lookahead makes a label-style key refuse to match when
# another sensitive key+separator immediately follows it, so "Cookie:" is
# left alone (harmless — it's a label, not a secret) and "auth_token"/"ct0"
# get matched as their own independent, correctly-redacted pairs instead.
_COOKIE_KEY_VALUE_RE = re.compile(
    rf'\b({_SENSITIVE_KEY_ALTERNATION})\b\s*"?\s*[:=]\s*"?'
    rf'(?!(?:{_SENSITIVE_KEY_ALTERNATION})\b\s*"?\s*[:=])'
    rf'(?:bearer\s+)?[^\s;&"\']+',
    re.IGNORECASE,
)
_COOKIE_NETSCAPE_FIELD_RE = re.compile(
    rf"\t({_SENSITIVE_KEY_ALTERNATION})\t[^\t\r\n]*", re.IGNORECASE
)


def redact_cookie_parse_error(raw_line: str) -> str:
    """Scrub a raw cookie-export line before it can appear in a parse/validation error.

    Used by the cookie-import parser (Netscape/JSON/cURL, §7) when a line is
    malformed or a token fails shape validation. Never pass ``raw_line``
    through unscrubbed — this replaces any auth_token/ct0/bearer/csrf-shaped
    key-value pair with a ``<redacted>`` marker, covering the `key=value`
    (querystring/cURL-header) and Netscape-TSV (`key<TAB>value`) shapes.

    Callers should still prefer reporting only structural/positional context
    (e.g. "line 3 malformed") over this scrubbed line where possible — this
    function is the last line of defense when some form of the raw line must
    appear in a message.
    """
    text = _COOKIE_NETSCAPE_FIELD_RE.sub(lambda m: f"\t{m.group(1)}\t<redacted>", raw_line)
    text = _COOKIE_KEY_VALUE_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)
    return text
