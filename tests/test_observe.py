"""Unit tests for the browser-observe fallback's pure parts.

The browser drive itself is not unit-testable (it needs Chrome and a live
session) and was exercised live instead -- see the plan's Phase 3b gate. What
IS testable, and worth pinning, is which page each op maps to and how the right
response is picked out of the dozen a page load fires.
"""

from __future__ import annotations

import json

import pytest

from scraper_for_x import observe
from scraper_for_x.errors import BrowserFallbackError


class _Captured:
    """Stands in for one scrapling-captured XHR (it exposes .url and .body)."""

    def __init__(self, url: str, body: str | None) -> None:
        self.url = url
        self.body = body


def _xhr(operation: str, payload: dict) -> _Captured:
    return _Captured(
        f"https://x.com/i/api/graphql/QID/{operation}?variables=%7B%7D", json.dumps(payload)
    )


def test_search_page_url_encodes_the_query():
    url = observe.page_url_for("SearchTimeline", "space news")
    assert url == "https://x.com/search?q=space%20news&f=live"


def test_replies_page_url_from_a_handle():
    assert observe.page_url_for("UserTweetsAndReplies", "nasa") == (
        "https://x.com/nasa/with_replies"
    )


def test_replies_page_url_from_a_numeric_id():
    """X redirects /i/user/<id> to the canonical profile, so a numeric target
    reaches the same page without a separate handle-lookup op."""
    assert observe.page_url_for("UserTweetsAndReplies", "783214") == (
        "https://x.com/i/user/783214/with_replies"
    )


def test_unknown_op_has_no_page():
    with pytest.raises(BrowserFallbackError, match="no browser fallback"):
        observe.page_url_for("HomeTimeline", "x")


def test_pick_body_selects_the_matching_operation():
    """A real page load fires many unrelated ops; only the requested one counts."""
    captured = [
        _xhr("SidebarUserRecommendations", {"data": {"wrong": 1}}),
        _xhr("SearchTimeline", {"data": {"search_by_raw_query": {"right": 1}}}),
    ]
    body = observe.pick_body(captured, "SearchTimeline")
    assert body == {"data": {"search_by_raw_query": {"right": 1}}}


def test_pick_body_returns_none_when_the_op_never_fired():
    captured = [_xhr("ExploreSidebar", {"data": {"x": 1}})]
    assert observe.pick_body(captured, "SearchTimeline") is None


def test_pick_body_skips_an_error_envelope():
    """A 200 carrying only `errors` is not a result -- taking it would report
    an empty run as a successful one."""
    captured = [
        _xhr("SearchTimeline", {"errors": [{"message": "nope"}]}),
        _xhr("SearchTimeline", {"data": {"search_by_raw_query": {"right": 1}}}),
    ]
    assert observe.pick_body(captured, "SearchTimeline") == {
        "data": {"search_by_raw_query": {"right": 1}}
    }


def test_pick_body_skips_empty_and_non_json_bodies():
    captured = [
        _Captured("https://x.com/i/api/graphql/QID/SearchTimeline", None),
        _Captured("https://x.com/i/api/graphql/QID/SearchTimeline", "<html>not json"),
        _xhr("SearchTimeline", {"data": {"ok": 1}}),
    ]
    assert observe.pick_body(captured, "SearchTimeline") == {"data": {"ok": 1}}


def test_pick_body_tolerates_no_capture_at_all():
    assert observe.pick_body(None, "SearchTimeline") is None


def test_followers_has_no_browser_fallback():
    """Followers is gated too, but the fallback was scoped to the two ops that
    had no working path at all in v0.2.0 -- pinned so widening it is a
    deliberate choice, not an accident."""
    assert "Followers" not in observe._OP_PAGES
