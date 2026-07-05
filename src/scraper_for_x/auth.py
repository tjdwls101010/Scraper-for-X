"""Session credential store + cookie-import contract + identifier normalize/validate (plan §7, §10).

Two distinct things live here, mirroring the FB sibling's ``profiles.py`` split:

- ``ensure_profile_dir``/``save_session``/``load_session``: where a *login* session
  credential (``auth_token``/``ct0``/UA, plus harvested query-ids/features) is
  stored on disk.
- ``normalize_identifier``: validating and normalizing a *target* username/id/URL
  before it ever reaches the read client.

Unlike the FB sibling (a browser-profile *directory* only), X's credential is a
single small JSON *file* inside that directory — a live, password-less session —
so this module hardens both the dir (0700) and the file (0600, §7).
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import config
from .errors import InvalidCookieError, InvalidIdentifierError, LoginRequiredError
from .redact import redact_cookie_parse_error

# --- Session credential storage ---------------------------------------------

#: The profile directory holds a live, logged-in X session — anyone who can
#: read it has authenticated account access with no password. 0700 so only the
#: owning user can read it (see DISCLAIMER.md).
_PROFILE_DIR_MODE = 0o700

#: The session file itself additionally gets 0600 (plan §7): the dir alone
#: isn't enough defense-in-depth for a single-file credential store.
_SESSION_FILE_MODE = 0o600

SESSION_FILENAME = "session.json"

#: Reasonable modern desktop Chrome UA — used when a cookie-only import has no
#: harvested UA of its own (there was no browser session to harvest one from),
#: and as session.py's fallback if the browser's own UA can't be read back.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class SessionCredential:
    """The on-disk session shape: cookies + UA, plus harvested query-ids/features.

    ``query_ids``/``features`` are persisted alongside the cookies so a
    cookie-import install can carry forward whatever ``session.py`` harvested
    (or re-anchored via ``queryids.py``) without a separate store.
    """

    auth_token: str
    ct0: str
    user_agent: str
    query_ids: dict | None = None
    features: dict | None = None


def ensure_profile_dir(path: Path) -> Path:
    """Create the profile directory (and parents) with 0700 permissions, idempotently.

    Adapted from the FB sibling's ``ensure_profile_dir``: a restrictive umask
    during ``mkdir`` means every directory it creates — including the shared
    "profiles" root, via ``parents=True`` — is born at 0700 directly, rather
    than briefly sitting at the ambient umask-determined mode before a later
    chmod tightens it. The explicit chmod calls afterward are a second layer:
    they also correct a root directory left loose by a prior run.
    """
    old_umask = os.umask(0o077)
    try:
        path.mkdir(parents=True, exist_ok=True)
    finally:
        os.umask(old_umask)
    os.chmod(path, _PROFILE_DIR_MODE)
    if path.parent != path:
        try:
            os.chmod(path.parent, _PROFILE_DIR_MODE)
        except OSError:
            pass
    return path


def save_session(
    profile: str,
    credential: SessionCredential,
    *,
    profile_dir_override: str | None = None,
) -> Path:
    """Persist ``credential`` as ``session.json`` under the profile dir, 0600.

    The file is created via ``os.open`` with an explicit ``0o600`` mode so it
    is never briefly world/group-readable between creation and a later chmod
    (atomic at creation, not post-hoc-only); the follow-up ``os.chmod`` is a
    belt-and-suspenders pass that also corrects a file left loose by a prior
    run. Returns the file path.
    """
    directory = ensure_profile_dir(
        config.profile_dir(profile, profile_dir_override=profile_dir_override)
    )
    session_path = directory / SESSION_FILENAME
    payload = json.dumps(asdict(credential)).encode("utf-8")

    fd = os.open(session_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _SESSION_FILE_MODE)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    os.chmod(session_path, _SESSION_FILE_MODE)
    return session_path


def load_session(profile: str, *, profile_dir_override: str | None = None) -> SessionCredential:
    """Load the persisted session credential for ``profile``.

    Raises ``LoginRequiredError`` if no session has been saved yet.
    """
    session_path = (
        config.profile_dir(profile, profile_dir_override=profile_dir_override) / SESSION_FILENAME
    )
    if not session_path.exists():
        raise LoginRequiredError(f"no session for profile {profile!r}: run `scrape-x login`")
    data = json.loads(session_path.read_text(encoding="utf-8"))
    return SessionCredential(**data)


# --- Cookie-import contract (plan §7) ---------------------------------------

#: Permissive-but-real: X's auth_token is typically 40 lowercase hex chars,
#: ct0 a longer hex string whose exact length has moved over time (32-160
#: covers observed real values with headroom). Erring toward accepting
#: real-but-unusual values over false-rejecting, while still catching
#: obviously-wrong input (empty string, a URL, a pasted JSON blob).
_TOKEN_SHAPE_RE = re.compile(r"^[0-9a-f]{32,160}$")


def validate_token_shapes(auth_token: str, ct0: str) -> None:
    """Raise ``InvalidCookieError`` unless both tokens pass a basic hex-shape check."""
    if not _TOKEN_SHAPE_RE.match(auth_token):
        raise InvalidCookieError("auth_token failed shape check (expected a hex string)")
    if not _TOKEN_SHAPE_RE.match(ct0):
        raise InvalidCookieError("ct0 failed shape check (expected a hex string)")


def _parse_cookie_json(text: str) -> dict[str, str] | None:
    """Try the JSON-array-of-cookie-objects shape; return None if not that shape."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    cookies: dict[str, str] = {}
    for item in data:
        if not isinstance(item, dict):
            return None
        name, value = item.get("name"), item.get("value")
        # Require both as actual strings -- a non-string value (an export
        # that serializes e.g. a numeric/boolean cookie value as JSON int/
        # bool) would otherwise reach validate_token_shapes()'s regex match
        # and raise an unhandled TypeError instead of a clean
        # InvalidCookieError (the whole point of this validation step).
        if not isinstance(name, str) or not isinstance(value, str):
            return None
        cookies[name] = value
    return cookies


#: Netscape cookie-file signature: the conventional header comment, present in
#: virtually every real export (curl/wget/browser-extension writers all emit it).
_NETSCAPE_HEADER_RE = re.compile(r"#\s*(Netscape|HTTP Cookie File)", re.IGNORECASE)


def _parse_cookie_netscape(text: str) -> dict[str, str] | None:
    """Try the tab-separated Netscape cookie-file shape; return None if not that shape."""
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        return None
    has_header = bool(_NETSCAPE_HEADER_RE.search(text))
    fields_per_line = [line.split("\t") for line in lines]
    # Netscape format is exactly 7 tab-separated fields per line (domain, flag,
    # path, secure, expiry, name, value). Require either the header comment or
    # every non-comment line matching that shape, so a single-line raw-header
    # paste (no tabs) doesn't get misdetected as Netscape.
    if not (has_header or all(len(fields) == 7 for fields in fields_per_line)):
        return None
    cookies: dict[str, str] = {}
    for line_number, fields in enumerate(fields_per_line, start=1):
        if len(fields) != 7:
            # Report only the one offending line (redacted), never the whole
            # file -- the rest of the export may hold unrelated live cookies
            # that have nothing to do with this line's malformed shape (§21).
            raise InvalidCookieError(
                f"malformed Netscape cookie line {line_number}: "
                f"{redact_cookie_parse_error(lines[line_number - 1])}"
            )
        name, value = fields[5], fields[6]
        cookies[name] = value
    return cookies


def _parse_cookie_header(text: str) -> dict[str, str]:
    """Parse a raw ``Cookie:`` header / cURL ``-H "Cookie: ..."`` paste.

    Semicolon-separated ``key=value`` pairs on one line (a leading ``Cookie:``
    or ``-H`` prefix, if present, is stripped first).
    """
    line = text.strip()
    # Strip a leading cURL `-H` flag and/or `Cookie:` header name, if present.
    line = re.sub(r"^-H\s+", "", line, flags=re.IGNORECASE)
    line = line.strip("'\"")
    line = re.sub(r"^Cookie:\s*", "", line, flags=re.IGNORECASE)

    cookies: dict[str, str] = {}
    for part in line.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise InvalidCookieError(
                f"malformed cookie header segment: {redact_cookie_parse_error(part)}"
            )
        name, _, value = part.partition("=")
        cookies[name.strip()] = value.strip()
    return cookies


def parse_cookie_file(path: Path) -> tuple[str, str]:
    """Parse a cookie export and extract ``(auth_token, ct0)``.

    Auto-detects, in order: (1) a JSON array of ``{name, value, ...}`` cookie
    objects (browser-devtools "copy as JSON" / most export extensions); (2) a
    Netscape cookie-file (tab-separated, one cookie per line, usually preceded
    by a ``# Netscape HTTP Cookie File`` header); (3) a raw ``Cookie:`` HTTP
    header string or cURL ``-H "Cookie: ..."`` paste (semicolon-separated
    ``key=value`` pairs on one line). Raises ``InvalidCookieError`` on any
    parse failure or missing ``auth_token``/``ct0``, with the offending raw
    text scrubbed via ``redact.redact_cookie_parse_error`` — the raw cookie
    value must never appear directly in the exception message.
    """
    text = path.read_text(encoding="utf-8")

    cookies = _parse_cookie_json(text)
    if cookies is None:
        cookies = _parse_cookie_netscape(text)
    if cookies is None:
        cookies = _parse_cookie_header(text)

    auth_token = cookies.get("auth_token")
    ct0 = cookies.get("ct0")
    if not auth_token or not ct0:
        # Report which cookie NAMES are present/missing, never a dump of the
        # file's content -- names aren't secret, but other cookies the export
        # carries (session/tracking cookies unrelated to auth_token/ct0) are
        # not covered by the sensitive-key redaction and must never be echoed.
        missing = [name for name, value in (("auth_token", auth_token), ("ct0", ct0)) if not value]
        found = ", ".join(sorted(cookies)) or "none"
        raise InvalidCookieError(
            f"cookie export is missing required cookie(s): {', '.join(missing)} (found: {found})"
        )
    return auth_token, ct0


def from_cookie_file(
    path: Path, profile: str = "default", *, profile_dir_override: str | None = None
) -> SessionCredential:
    """Import a cookie export, validate it, persist it, and return the credential.

    Prints a one-line reminder to stderr that the source export file still
    contains a live, password-less session and should be deleted/secured.
    Does not copy or retain the source file anywhere.
    """
    auth_token, ct0 = parse_cookie_file(path)
    validate_token_shapes(auth_token, ct0)
    credential = SessionCredential(auth_token=auth_token, ct0=ct0, user_agent=DEFAULT_USER_AGENT)
    save_session(profile, credential, profile_dir_override=profile_dir_override)
    print(
        f"scrape-x: {path} still contains a live, password-less X session — "
        "delete or secure it now that it has been imported",
        file=sys.stderr,
    )
    return credential


# --- Target identifier validation (plan §10) --------------------------------

_ALLOWED_HOSTS = frozenset({"x.com", "twitter.com"})
_STRIP_SUBDOMAINS = ("www.", "mobile.", "m.")
_HANDLE_RE = re.compile(r"^@?[A-Za-z0-9_]{1,15}$")
_NUMERIC_RE = re.compile(r"^[0-9]+$")
_TWEET_ID_RE = re.compile(r"^\d+$")
_TRAILING_SUFFIXES = ("photo", "video", "analytics")


def normalize_identifier(raw: str, *, by: str | None = None) -> tuple[str, str]:
    """Normalize-then-validate a username/id/URL identifier (plan §10).

    Returns ``(kind, value)`` where ``kind`` is ``"screen_name"``, ``"id"``, or
    ``"tweet_id"``. URLs are validated against an ``x.com``/``twitter.com``
    host allowlist before their path is trusted for anything. A bare all-digit
    token defaults to ``("id", token)`` unless ``by="screen_name"`` is passed
    or the token carries a literal ``@`` prefix (which unambiguously signals a
    handle even if all-digit). ``by="id"`` makes that default explicit and
    additionally REJECTS a non-numeric token (rather than silently falling
    through to handle detection, which would make ``--by id`` a no-op).
    """
    raw = raw.strip()
    if not raw:
        raise InvalidIdentifierError("identifier is empty")

    if "://" in raw or raw.startswith(("x.com/", "twitter.com/", "www.", "mobile.", "m.")):
        return _normalize_url(raw)

    if raw.startswith("@"):
        token = raw[1:]
        if not _HANDLE_RE.match(raw):
            raise InvalidIdentifierError(f"invalid handle {raw!r}")
        return "screen_name", token

    if by == "screen_name":
        if not _HANDLE_RE.match(raw):
            raise InvalidIdentifierError(f"invalid handle {raw!r}")
        return "screen_name", raw

    if by == "id":
        if not _NUMERIC_RE.match(raw):
            raise InvalidIdentifierError(f"invalid numeric id {raw!r} (--by id was given)")
        return "id", raw

    if _NUMERIC_RE.match(raw):
        return "id", raw

    if _HANDLE_RE.match(raw):
        return "screen_name", raw

    raise InvalidIdentifierError(
        f"invalid identifier {raw!r}: expected a handle, a numeric id, or a "
        "full x.com/twitter.com URL"
    )


def normalize_tweet_identifier(raw: str) -> str:
    """Normalize-then-validate a TWEET identifier: a URL or a bare numeric id.

    Unlike ``normalize_identifier`` (which disambiguates a *user* handle vs a
    *user* id), a tweet identifier has no such ambiguity -- it is always
    either a ``/status/<id>`` URL or a bare tweet id, never a handle. A
    dedicated function avoids ``normalize_identifier``'s default-to-``"id"``
    behavior being interpreted as a *user* id lookup by a caller that only
    wants a tweet id (plan §1, §5 -- ``fetch_tweet``/``scrape-x tweet``).
    """
    raw = raw.strip()
    if not raw:
        raise InvalidIdentifierError("identifier is empty")
    if "://" in raw or raw.startswith(("x.com/", "twitter.com/", "www.", "mobile.", "m.")):
        kind, value = _normalize_url(raw)
        if kind != "tweet_id":
            raise InvalidIdentifierError(
                f"expected a tweet URL (.../status/<id>), got a profile URL for {value!r}"
            )
        return value
    if _TWEET_ID_RE.match(raw):
        return raw
    raise InvalidIdentifierError(
        f"invalid tweet identifier {raw!r}: expected a numeric tweet id or a "
        "full x.com/twitter.com tweet URL"
    )


def _normalize_url(raw: str) -> tuple[str, str]:
    # Default the scheme to https (permit http) — a bare host/path paste like
    # "x.com/nasa/status/123" has no scheme at all.
    candidate = raw if "://" in raw else f"https://{raw}"
    parts = urlsplit(candidate)
    if parts.scheme not in ("http", "https"):
        raise InvalidIdentifierError(
            f"unsupported scheme {parts.scheme!r}: only http/https accepted"
        )

    host = parts.netloc.split("@")[-1].split(":")[0].lower()
    for prefix in _STRIP_SUBDOMAINS:
        if host.startswith(prefix):
            host = host[len(prefix) :]
            break
    if host not in _ALLOWED_HOSTS:
        raise InvalidIdentifierError(f"unsupported host {host!r}: must be x.com or twitter.com")

    # Query string and fragment are discarded entirely (plan §10) — they are
    # never needed to locate a tweet id and are not trusted for anything.
    segments = [seg for seg in parts.path.split("/") if seg]
    if not segments:
        raise InvalidIdentifierError("URL has no path")

    try:
        status_index = segments.index("status")
    except ValueError:
        # No /status/ segment: this is a profile URL, not a tweet URL (plan §5
        # accepts "a full profile/tweet URL" -- both shapes must resolve, not
        # just the tweet one). A bare single handle-shaped segment is the only
        # profile-URL shape recognized; anything else (multiple segments,
        # reserved paths like /home or /i/...) is rejected rather than guessed at.
        if len(segments) == 1 and _HANDLE_RE.match(segments[0]):
            return "screen_name", segments[0]
        raise InvalidIdentifierError(
            f"unsupported URL path {parts.path!r}: expected a profile URL "
            "(/<handle>) or a /status/<id> tweet URL"
        ) from None

    # Accepts both /<handle>/status/<id> and the handle-less /i/web/status/<id>
    # — the handle segment (or "i/web") before "status" is not otherwise used.
    if status_index + 1 >= len(segments):
        raise InvalidIdentifierError(f"unsupported URL path {parts.path!r}: missing tweet id")
    id_segment = segments[status_index + 1]
    if not _TWEET_ID_RE.match(id_segment):
        raise InvalidIdentifierError(f"unsupported URL path {parts.path!r}: non-numeric tweet id")

    # Ignore known trailing suffixes after the id (/photo/N, /video/N, /analytics).
    remaining = segments[status_index + 2 :]
    if remaining and remaining[0] not in _TRAILING_SUFFIXES:
        raise InvalidIdentifierError(
            f"unsupported URL path {parts.path!r}: unrecognized trailing segment {remaining[0]!r}"
        )

    return "tweet_id", id_segment
