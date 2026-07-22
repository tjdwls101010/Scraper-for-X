"""Browser-observe fallback for the transaction-id-gated ops (plan §4, D1).

The primary path for ``SearchTimeline``/``UserTweetsAndReplies`` is a generated
``x-client-transaction-id`` over plain httpx (see ``transaction.py``). That
algorithm is reverse-engineered and **will** eventually rot. When it does, X
answers a gated op with a 404 even though a header was sent -- and this module
is the safety net: drive the stealth browser to the page that fires the op
naturally, and read the response X's own client received.

**This is a fallback, not a second implementation.** It returns exactly one
page, because a browser page-load fires the op once; there is no cursor to
follow without simulating scroll. So it degrades deep pagination to "the first
page, but working" -- which beats returning nothing while the generator is
being re-ported. Callers must surface that limitation rather than hide it.

Requires the ``[browser]`` extra. scrapling is imported lazily, inside the
function, exactly like ``session.py`` does (plan §14 G-lazy-import) -- importing
this module on a base install must stay harmless.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from . import auth, config
from .errors import BrowserFallbackError

#: Which page makes X's own client fire each gated op. `Followers` is
#: deliberately absent: it is gated too (probed 2026-07-20), but the plan scoped
#: the browser fallback to the two ops that had no working path at all in
#: v0.2.0. A `Followers` outage degrades to "generator needs re-porting".
_OP_PAGES = {
    "SearchTimeline": lambda target: f"https://x.com/search?q={quote(target)}&f=live",
    # `target` is whatever the user gave `fetch`. A numeric id works too: X
    # redirects /i/user/<id> to the canonical profile, so we can reach
    # /<handle>/with_replies without a separate handle-lookup op.
    "UserTweetsAndReplies": lambda target: (
        f"https://x.com/i/user/{target}" if target.isdigit() else f"https://x.com/{target}"
    ),
}


def _settle(page) -> None:
    """Give the page a few seconds to fire its GraphQL calls.

    Deliberately does NOT scroll. A `page.mouse.wheel` call is what an earlier
    recon script hung on for 25 minutes, and the first page of results fires on
    load anyway -- there is nothing to gain by scrolling and a hang to lose.
    """
    page.wait_for_timeout(6000)


def observe(
    operation: str,
    target: str,
    *,
    profile: str,
    profile_dir_override: str | None = None,
) -> dict:
    """Return one raw GraphQL response body for ``operation``, via the browser.

    Raises :class:`BrowserFallbackError` if the ``[browser]`` extra is missing,
    if the op has no known page, or if the page never fired the op.
    """
    url = page_url_for(operation, target)

    # Imported here, not at module top level: `from . import session` is cheap,
    # but the scrapling import inside _build_stealth_session is not, and a base
    # install does not have it.
    from . import session as session_module

    profile_dir = auth.ensure_profile_dir(
        config.profile_dir(profile, profile_dir_override=profile_dir_override)
    )

    try:
        with session_module._build_stealth_session(profile_dir, headless=True) as browser:
            response = browser.fetch(url, page_action=_settle, timeout=60000)
    except ImportError as exc:  # scrapling absent
        raise BrowserFallbackError(
            "the browser fallback needs the [browser] extra: pip install 'agentic-twitter[browser]'"
        ) from exc

    body = pick_body(response.captured_xhr, operation)
    if body is None:
        raise BrowserFallbackError(
            f"the browser loaded {url} but never captured a {operation} response "
            "(the session may be logged out in the browser profile -- try: agentic-x login)"
        )
    return body


def pick_body(captured_xhr, operation: str) -> dict | None:
    """Find the one captured response that belongs to ``operation``.

    A page load fires a dozen unrelated GraphQL calls (sidebar
    recommendations, feature flags, ...), so the operation name in the URL is
    the filter. Bodies that are missing, non-JSON, or carry no ``data`` are
    skipped rather than returned -- an error envelope is not a result.
    """
    for captured in captured_xhr or []:
        if operation not in (getattr(captured, "url", "") or ""):
            continue
        body = getattr(captured, "body", None)
        if not body:
            continue
        try:
            parsed = json.loads(body)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict) and parsed.get("data"):
            return parsed
    return None


def page_url_for(operation: str, target: str) -> str:
    """The x.com page whose load makes X's own client fire ``operation``."""
    builder = _OP_PAGES.get(operation)
    if builder is None:
        raise BrowserFallbackError(f"no browser fallback is defined for {operation}")
    url = builder(target)
    return f"{url}/with_replies" if operation == "UserTweetsAndReplies" else url
