"""Query-id/feature-flag defaults and browser-free re-anchor (plan §8, §12).

X rotates GraphQL query-ids roughly every 2-4 weeks as an anti-scraping
measure; a hardcoded id then fails silently or returns an empty parse. This
module ships a known-good fallback per op and two ways to refresh it:

- ``harvest_from_browser``: called by ``scrape-x login`` with the XHR
  requests scrapling captured during the browser session.
- ``reanchor_via_main_js``: the **browser-free** path (`doctor --refresh` /
  `harvest_queryids.py`) -- fetches x.com's public `main.js` bundle over a
  minimal cookie-only ``httpx.Client`` it builds itself (NOT the caller's
  GraphQL ``ReadClient`` -- see the function's docstring for why that
  specifically breaks this fetch) and regex-extracts the current query-id/
  feature map. No browser required.

Shipped defaults are a fallback only, never the source of truth (§8, §12).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from .gql import DEFAULT_FEATURES

# Live query-ids re-captured 2026-07-20 against a logged-in X session. Every
# id below was observed in a real browser XHR or x.com's main.js bundle AND
# fired over httpx in the same session, so none is a guess any more -- the
# 2026-07-05 `TweetDetail`/`UserTweetsAndReplies` placeholders are gone.
#
# "Verified" here means "X routed the request", NOT "the op is reachable":
# `SearchTimeline`, `UserTweetsAndReplies` and `Followers` answer 404 to a
# correct id without an `x-client-transaction-id` (see transaction.py). Ids
# still rotate every 2-4 weeks -- these remain a fallback, never the source of
# truth (§8, §12).
DEFAULT_QUERY_IDS: dict[str, str] = {
    "UserTweets": "6r5OLCC_wFH4CpRyXKuAmQ",
    "HomeTimeline": "gKia-nBM9kwuDEfSDeWMfQ",
    "UserByScreenName": "2qvSHpkWTMS9i0zJAwDNiA",
    "SearchTimeline": "hz_94eVAtrtQo_vO3my7Rw",
    "UserTweetsAndReplies": "klja8a2iJX_3to5RdfVlgw",
    "TweetDetail": "rZA6K31W4E90vZKBmxXV3g",
    "Following": "PEIBUtChvR2i_NZCxbK3fA",
    "Followers": "18SNsfvwgu2CYIweeUVHAw",
}


@dataclass
class QueryIdSet:
    """One bundle of query-ids + the features map that must accompany them."""

    query_ids: dict[str, str]
    features: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_FEATURES))


def load_default_query_ids() -> QueryIdSet:
    """Return the shipped fallback defaults (not the source of truth, §8)."""
    return QueryIdSet(query_ids=dict(DEFAULT_QUERY_IDS))


# Matches .../graphql/<queryId>/<OperationName> in a captured XHR URL.
_URL_QUERY_ID_RE = re.compile(r"/graphql/([A-Za-z0-9_-]+)/([A-Za-z0-9_]+)")


def harvest_from_browser(captured_requests: list[Any]) -> QueryIdSet:
    """Build a `QueryIdSet` from scrapling's captured XHR responses at login.

    ``captured_requests`` is whatever ``response.captured_xhr`` handed back
    (a list of scrapling ``Response``-like objects, each with a ``.url``
    attribute -- see ``scraper_for_facebook.session``/``retrieve`` for the
    reference shape this mirrors). Any op observed live overrides the
    fallback; any op not seen during this session falls back to
    ``DEFAULT_QUERY_IDS``.
    """
    observed: dict[str, str] = {}
    for request in captured_requests:
        url = getattr(request, "url", None)
        if not isinstance(url, str):
            continue
        match = _URL_QUERY_ID_RE.search(url)
        if match is None:
            continue
        query_id, operation = match.group(1), match.group(2)
        observed[operation] = query_id

    query_ids = dict(DEFAULT_QUERY_IDS)
    query_ids.update(observed)
    return QueryIdSet(query_ids=query_ids)


# Locates the current main JS bundle referenced by x.com's HTML, e.g.
# <script src="https://abs.twimg.com/responsive-web/client-web/main.abc123.js">
_MAIN_JS_URL_RE = re.compile(
    r"https://abs\.twimg\.com/responsive-web/client-web[\w./-]*/main\.[\w-]+\.js"
)

# Best-effort: X's minified bundle embeds each op as adjacent object-literal
# fields, e.g. `queryId:"hr4gzZONlq23okjU8fIe_A",operationName:"UserTweets"`.
# NEEDS VERIFICATION against a live main.js bundle -- the live probe did not
# directly capture the bundle's exact key order/quoting/adjacency, so this
# regex is a best guess and may need adjustment during implementation testing
# (§8, §12). It tolerates either field-order (queryId first or operationName
# first) within a short window of characters.
_BUNDLE_QUERY_ID_THEN_OP_RE = re.compile(
    r'queryId:"([A-Za-z0-9_-]+)"[^{}]{0,80}?operationName:"(\w+)"'
)
_BUNDLE_OP_THEN_QUERY_ID_RE = re.compile(
    r'operationName:"(\w+)"[^{}]{0,80}?queryId:"([A-Za-z0-9_-]+)"'
)


def reanchor_via_main_js(auth_token: str, ct0: str, user_agent: str) -> QueryIdSet:
    """Browser-free re-anchor: fetch x.com's main.js bundle and regex the ids.

    Builds its OWN minimal ``httpx.Client`` -- cookies + user-agent, nothing
    else -- rather than accepting the caller's GraphQL ``ReadClient``. This
    is deliberate, not an arbitrary restriction: X's plain web page
    (``https://x.com``, and the CDN-hosted JS bundle it references) returns
    **401 with an empty body** when the request carries the GraphQL-endpoint-
    only headers (``authorization: Bearer ...``, ``x-twitter-auth-type``,
    ``x-csrf-token``) -- confirmed live. A real browser loading the homepage
    never sends those headers either; only cookies + user-agent are needed
    here, matching that. Any op found in the bundle overrides the fallback;
    any op not matched falls back to ``DEFAULT_QUERY_IDS``.
    """
    observed: dict[str, str] = {}

    with httpx.Client(
        cookies={"auth_token": auth_token, "ct0": ct0},
        headers={"user-agent": user_agent},
        follow_redirects=True,
    ) as http_client:
        html_response = http_client.get("https://x.com")
        main_js_match = _MAIN_JS_URL_RE.search(html_response.text)
        if main_js_match is not None:
            bundle_response = http_client.get(main_js_match.group(0))
            bundle_text = bundle_response.text
            for query_id, operation in _BUNDLE_QUERY_ID_THEN_OP_RE.findall(bundle_text):
                observed[operation] = query_id
            for operation, query_id in _BUNDLE_OP_THEN_QUERY_ID_RE.findall(bundle_text):
                observed.setdefault(operation, query_id)

    query_ids = dict(DEFAULT_QUERY_IDS)
    query_ids.update(observed)
    return QueryIdSet(query_ids=query_ids)
