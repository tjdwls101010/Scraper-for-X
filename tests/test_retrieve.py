from datetime import UTC, datetime

from agentic_x import errors, gql, retrieve, session


def _tweet_entry(rest_id: str, created_at: str, *, text: str = "synthetic") -> dict:
    return {
        "__typename": "Tweet",
        "rest_id": rest_id,
        "core": {
            "user_results": {"result": {"rest_id": "1", "core": {"screen_name": "u"}, "legacy": {}}}
        },
        "legacy": {"full_text": text, "created_at": created_at, "entities": {}},
    }


def _page(tweets: list[dict], cursor: str | None) -> dict:
    entries = [
        {
            "entryId": f"tweet-{t['rest_id']}",
            "content": {"itemContent": {"tweet_results": {"result": t}}},
        }
        for t in tweets
    ]
    if cursor is not None:
        entries.append(
            {
                "entryId": "cursor-bottom",
                "content": {
                    "entryType": "TimelineTimelineCursor",
                    "cursorType": "Bottom",
                    "value": cursor,
                },
            }
        )
    return {
        "data": {
            "user": {
                "result": {
                    "timeline": {
                        "timeline": {
                            "instructions": [{"type": "TimelineAddEntries", "entries": entries}]
                        }
                    }
                }
            }
        }
    }


class FakeReadClient:
    """Returns canned pages in order; raises if asked for more than scripted."""

    def __init__(self, pages: list[dict | Exception]):
        self._pages = list(pages)
        self.requests_made = 0
        self.methods_used: list[str] = []
        #: (operation, variables, field_toggles) per call, so tests can assert
        #: an op was addressed with the exact request shape X requires.
        self.calls: list[tuple[str, dict, dict | None]] = []

    def get(self, query_id, operation, variables, features, field_toggles=None):
        self.methods_used.append("GET")
        self.calls.append((operation, variables, field_toggles))
        return self._next_page()

    def post(self, query_id, operation, variables, features, field_toggles=None):
        self.methods_used.append("POST")
        self.calls.append((operation, variables, field_toggles))
        return self._next_page()

    def _next_page(self):
        if not self._pages:
            raise AssertionError("FakeReadClient exhausted -- test scripted too few pages")
        self.requests_made += 1
        item = self._pages.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


DAY1 = "Tue Jul 01 12:00:00 +0000 2026"
DAY2 = "Mon Jun 30 12:00:00 +0000 2026"
DAY3 = "Sun Jun 29 12:00:00 +0000 2026"


def _build_variables(cursor):
    return {"cursor": cursor}


def test_cursor_pagination_walks_multiple_pages_with_dedup():
    page1 = _page([_tweet_entry("1", DAY1), _tweet_entry("2", DAY1)], cursor="C1")
    page2 = _page(
        [_tweet_entry("2", DAY1), _tweet_entry("3", DAY2)], cursor="C1"
    )  # non-advancing -> EOF
    client = FakeReadClient([page1, page2])
    result = retrieve.paginate(client, "UserTweets", "qid", {}, _build_variables)
    assert [t.id for t in result.tweets] == ["1", "2", "3"]  # "2" deduped, not doubled
    assert result.stop_reason == "feed_exhausted"
    assert result.requests_made == 2


def test_non_advancing_cursor_is_the_sole_eof_signal_not_thin_page():
    """A thin (empty) page with a NEW cursor must continue, never stop (plan
    §8 G-cursor-eof) -- only a repeated cursor value signals EOF."""
    page1 = _page([_tweet_entry("1", DAY1)], cursor="C1")
    page2 = _page([], cursor="C2")  # thin but cursor advanced -> must continue
    page3 = _page([_tweet_entry("2", DAY2)], cursor="C2")  # same cursor as sent -> EOF after this
    client = FakeReadClient([page1, page2, page3])
    result = retrieve.paginate(client, "UserTweets", "qid", {}, _build_variables)
    assert [t.id for t in result.tweets] == ["1", "2"]
    assert result.requests_made == 3
    assert result.stop_reason == "feed_exhausted"


def test_limit_stops_before_exhausting_feed():
    page1 = _page(
        [_tweet_entry("1", DAY1), _tweet_entry("2", DAY2), _tweet_entry("3", DAY3)], cursor="C1"
    )
    client = FakeReadClient([page1])
    result = retrieve.paginate(client, "UserTweets", "qid", {}, _build_variables, limit=2)
    assert [t.id for t in result.tweets] == ["1", "2"]
    assert result.stop_reason == "limit_reached"


def test_since_stops_once_crossed_and_excludes_the_crossing_tweet():
    since = datetime(2026, 6, 30, tzinfo=UTC)  # start of DAY2
    page1 = _page(
        [_tweet_entry("1", DAY1), _tweet_entry("2", DAY2), _tweet_entry("3", DAY3)], cursor="C1"
    )
    client = FakeReadClient([page1])
    result = retrieve.paginate(client, "UserTweets", "qid", {}, _build_variables, since=since)
    # tweet 3 (DAY3, before the since boundary) triggers the stop and is excluded;
    # tweet 2 (exactly DAY2, the inclusive boundary) is kept.
    assert [t.id for t in result.tweets] == ["1", "2"]
    assert result.stop_reason == "since_crossed"
    assert result.since_target_crossed is True


def test_until_skips_newer_tweets_without_stopping():
    until = datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC)  # end of DAY2
    page1 = _page(
        [_tweet_entry("1", DAY1), _tweet_entry("2", DAY2), _tweet_entry("3", DAY3)], cursor=None
    )
    client = FakeReadClient([page1])
    result = retrieve.paginate(client, "UserTweets", "qid", {}, _build_variables, until=until)
    # tweet 1 (DAY1, newer than until) is SKIPPED, not a stop trigger -- 2 and 3 still collected.
    assert [t.id for t in result.tweets] == ["2", "3"]
    assert result.stop_reason == "feed_exhausted"


def test_limit_and_since_compose_first_trigger_wins():
    since = datetime(2026, 6, 29, tzinfo=UTC)
    page1 = _page([_tweet_entry("1", DAY1), _tweet_entry("2", DAY2)], cursor="C1")
    client = FakeReadClient([page1])
    result = retrieve.paginate(
        client, "UserTweets", "qid", {}, _build_variables, limit=1, since=since
    )
    assert [t.id for t in result.tweets] == ["1"]
    assert result.stop_reason == "limit_reached"  # limit hit before since crossed


def test_pinned_tweet_always_included_and_never_drives_stop():
    since = datetime(2026, 7, 1, tzinfo=UTC)  # only DAY1 or later would normally pass
    pinned = _tweet_entry("999", "Wed Jan 01 00:00:00 +0000 2020")  # ancient
    page = {
        "data": {
            "user": {
                "result": {
                    "timeline": {
                        "timeline": {
                            "instructions": [
                                {
                                    "type": "TimelineAddEntries",
                                    "entries": [
                                        {
                                            "entryId": "tweet-1",
                                            "content": {
                                                "itemContent": {
                                                    "tweet_results": {
                                                        "result": _tweet_entry("1", DAY1)
                                                    }
                                                }
                                            },
                                        },
                                    ],
                                },
                                {
                                    "type": "TimelinePinEntry",
                                    "entry": {
                                        "entryId": "tweet-999-pin",
                                        "content": {
                                            "itemContent": {"tweet_results": {"result": pinned}}
                                        },
                                    },
                                },
                            ]
                        }
                    }
                }
            }
        }
    }
    client = FakeReadClient([page])
    result = retrieve.paginate(client, "UserTweets", "qid", {}, _build_variables, since=since)
    ids = {t.id for t in result.tweets}
    assert "999" in ids  # ancient pinned tweet still returned
    assert result.stop_reason == "feed_exhausted"  # not "since_crossed" -- pin didn't trigger it


def test_max_requests_budget_stops_the_run():
    pages = [_page([_tweet_entry(str(i), DAY1)], cursor=f"C{i}") for i in range(5)]
    client = FakeReadClient(pages)
    result = retrieve.paginate(client, "UserTweets", "qid", {}, _build_variables, max_requests=2)
    assert result.requests_made == 2
    assert result.stop_reason == "max_requests"


def test_rate_limited_without_wait_on_limit_stops_with_partial_result():
    page1 = _page([_tweet_entry("1", DAY1)], cursor="C1")
    client = FakeReadClient([page1, errors.RateLimitedError(reset_at=123)])
    result = retrieve.paginate(client, "UserTweets", "qid", {}, _build_variables)
    assert [t.id for t in result.tweets] == ["1"]
    assert result.stop_reason == "rate_limited"


def test_search_empty_result_is_no_matches_not_feed_exhausted():
    empty = {
        "data": {
            "search_by_raw_query": {
                "search_timeline": {
                    "timeline": {"instructions": [{"type": "TimelineAddEntries", "entries": []}]}
                }
            }
        }
    }
    client = FakeReadClient([empty])
    result = retrieve.paginate(client, "SearchTimeline", "qid", {}, _build_variables)
    assert result.tweets == []
    assert result.stop_reason == "no_matches"


def test_none_created_at_never_triggers_since_or_until(monkeypatch):
    """plan §11: a created_at=None tweet never participates in a --since/--until
    comparison -- it must not accidentally trigger (or block) the stop condition."""
    since = datetime(2026, 7, 1, tzinfo=UTC)
    none_dated = {
        "rest_id": "1",
        "core": {
            "user_results": {"result": {"rest_id": "1", "core": {"screen_name": "u"}, "legacy": {}}}
        },
        "legacy": {"full_text": "no date", "entities": {}},  # created_at missing entirely
    }
    page = _page([none_dated, _tweet_entry("2", DAY1)], cursor=None)
    client = FakeReadClient([page])
    result = retrieve.paginate(client, "UserTweets", "qid", {}, _build_variables, since=since)
    assert {t.id for t in result.tweets} == {"1", "2"}
    assert result.stop_reason == "feed_exhausted"  # never "since_crossed"


def test_empty_non_search_result_probes_soft_lock(monkeypatch):
    """plan §7, §11: a genuinely empty (but parsed-fine) non-search result must
    be distinguished from a soft-locked session via a cheap viewer-lookup probe,
    not assumed "just empty" outright."""
    empty = _page([], cursor=None)

    def fake_check(read_client, query_ids, features):
        return session.Status.EXPIRED

    monkeypatch.setattr(session, "check_session_status", fake_check)
    client = FakeReadClient([empty])
    result = retrieve.paginate(client, "UserTweets", "qid", {}, _build_variables)
    assert result.stop_reason == "soft_locked"


def test_empty_non_search_result_genuinely_logged_in_is_feed_exhausted(monkeypatch):
    empty = _page([], cursor=None)

    def fake_check(read_client, query_ids, features):
        return session.Status.LOGGED_IN

    monkeypatch.setattr(session, "check_session_status", fake_check)
    client = FakeReadClient([empty])
    result = retrieve.paginate(client, "UserTweets", "qid", {}, _build_variables)
    assert result.stop_reason == "feed_exhausted"


def test_iter_user_tweets_streams_incrementally_not_all_at_once():
    """plan §5: iter_* yield incrementally -- confirm the generator produces a
    tweet before the SECOND page's request has even been made."""
    page1 = _page([_tweet_entry("1", DAY1)], cursor="C1")
    page2 = _page([_tweet_entry("2", DAY2)], cursor="C1")
    client = FakeReadClient([page1, page2])
    state = retrieve.RunState()
    stream = retrieve.paginate_iter(client, "UserTweets", "qid", {}, _build_variables, state=state)
    first = next(stream)
    assert first.id == "1"
    assert client.requests_made == 1  # page 2 not yet fetched
    rest = list(stream)
    assert [t.id for t in rest] == ["2"]
    assert client.requests_made == 2


def _search_page(tweets: list[dict], cursor: str | None) -> dict:
    """Same entries as `_page`, wrapped in SearchTimeline's envelope."""
    inner = _page(tweets, cursor)
    instructions = inner["data"]["user"]["result"]["timeline"]["timeline"]["instructions"]
    return {
        "data": {
            "search_by_raw_query": {"search_timeline": {"timeline": {"instructions": instructions}}}
        }
    }


def test_search_returns_tweets():
    """Inverted from v0.2.0, where this asserted FeatureNotImplementedError.
    SearchTimeline is still behind X's transaction-id wall -- the difference is
    that v0.3.0 mints the header per request (in client.ReadClient) instead of
    giving up, so retrieve.search() is now an ordinary paginated read."""
    client = FakeReadClient([_search_page([_tweet_entry("1", DAY1)], None)])
    result = retrieve.search(client, {}, {}, "hello")
    assert [t.id for t in result.tweets] == ["1"]
    assert client.calls[0][0] == "SearchTimeline"


def test_search_passes_the_product_through():
    client = FakeReadClient([_search_page([_tweet_entry("1", DAY1)], None)])
    retrieve.search(client, {}, {}, "hello", product="Top")
    assert client.calls[0][1]["product"] == "Top"


def test_search_empty_result_is_no_matches_not_soft_locked():
    """An empty search is a legitimate "nothing matched" (exit 0), not the
    soft-lock probe path the profile timelines take."""
    client = FakeReadClient([_search_page([], None)])
    result = retrieve.search(client, {}, {}, "zzzz")
    assert result.tweets == []
    assert result.stop_reason == "no_matches"


def test_fetch_user_tweets_replies_uses_the_replies_op():
    """Inverted from v0.2.0's FeatureNotImplementedError guard. The replies
    variant is a genuinely different op: different name, different variables,
    and it needs fieldToggles that plain UserTweets must not send."""
    client = FakeReadClient([_page([_tweet_entry("1", DAY1)], None)])
    result = retrieve.fetch_user_tweets(client, {}, {}, "id", "123", replies=True)
    assert [t.id for t in result.tweets] == ["1"]
    operation, variables, field_toggles = client.calls[0]
    assert operation == "UserTweetsAndReplies"
    assert field_toggles == gql.USER_TWEETS_AND_REPLIES_FIELD_TOGGLES
    # Live-captured: the replies variant adds withCommunity and must NOT carry
    # withQuickPromoteEligibilityTweetFields, or X 404s it.
    assert variables["withCommunity"] is True
    assert "withQuickPromoteEligibilityTweetFields" not in variables


def test_fetch_user_tweets_without_replies_sends_no_field_toggles():
    """Negative control for the above -- plain UserTweets 404s if sent them."""
    client = FakeReadClient([_page([_tweet_entry("1", DAY1)], None)])
    retrieve.fetch_user_tweets(client, {}, {}, "id", "123", replies=False)
    operation, variables, field_toggles = client.calls[0]
    assert operation == "UserTweets"
    assert field_toggles is None
    assert variables["withQuickPromoteEligibilityTweetFields"] is True


def test_iter_user_tweets_replies_streams():
    client = FakeReadClient([_page([_tweet_entry("1", DAY1)], None)])
    stream = retrieve.iter_user_tweets(client, {}, {}, "id", "123", replies=True)
    assert [t.id for t in stream] == ["1"]
    assert client.calls[0][0] == "UserTweetsAndReplies"


def test_fetch_user_tweets_without_replies_is_unaffected():
    page = _page([_tweet_entry("1", DAY1)], cursor=None)
    client = FakeReadClient([page])
    result = retrieve.fetch_user_tweets(client, {}, {}, "id", "123", replies=False)
    assert [t.id for t in result.tweets] == ["1"]


def _home_page(tweets: list[dict], cursor: str | None) -> dict:
    """Same entries as `_page`, wrapped in the home feed's deeper envelope."""
    inner = _page(tweets, cursor)
    instructions = inner["data"]["user"]["result"]["timeline"]["timeline"]["instructions"]
    return {"data": {"home": {"home_timeline_urt": {"instructions": instructions}}}}


def test_fetch_home_paginates_and_returns_tweets():
    client = FakeReadClient(
        [
            _home_page([_tweet_entry("1", DAY1), _tweet_entry("2", DAY1)], "CURSOR1"),
            _home_page([_tweet_entry("3", DAY1)], None),
        ]
    )
    result = retrieve.fetch_home(client, {"HomeTimeline": "QID"}, {})
    assert [t.id for t in result.tweets] == ["1", "2", "3"]
    assert result.stop_reason == "feed_exhausted"


def test_fetch_home_uses_post_not_get():
    """X's own client POSTs HomeTimeline. GET works today too, but matching the
    real client is the durable choice for an op that is not currently walled --
    pinned here so the wire shape can't silently regress to GET."""
    client = FakeReadClient([_home_page([_tweet_entry("1", DAY1)], None)])
    retrieve.fetch_home(client, {"HomeTimeline": "QID"}, {})
    assert client.methods_used == ["POST"]


def test_fetch_home_respects_limit():
    client = FakeReadClient(
        [_home_page([_tweet_entry(str(i), DAY1) for i in range(1, 6)], "CURSOR1")]
    )
    result = retrieve.fetch_home(client, {"HomeTimeline": "QID"}, {}, limit=2)
    assert [t.id for t in result.tweets] == ["1", "2"]
    assert result.stop_reason == "limit_reached"


def test_from_observed_body_builds_a_result_from_one_page():
    """The browser fallback yields exactly one page, so the run reports
    `browser_observed` -- not `feed_exhausted` (there IS more, we just cannot
    reach it) and not `max_requests` (no budget was hit)."""
    body = _search_page([_tweet_entry("1", DAY1), _tweet_entry("2", DAY1)], "CURSOR1")
    result = retrieve.from_observed_body(body, "SearchTimeline")
    assert [t.id for t in result.tweets] == ["1", "2"]
    assert result.stop_reason == "browser_observed"


def test_from_observed_body_still_honours_limit():
    body = _search_page([_tweet_entry(str(i), DAY1) for i in range(1, 5)], "CURSOR1")
    result = retrieve.from_observed_body(body, "SearchTimeline", limit=2)
    assert [t.id for t in result.tweets] == ["1", "2"]
    assert result.stop_reason == "limit_reached"


def test_from_observed_body_never_reports_soft_locked():
    """An empty page sends paginate into its soft-lock probe, which would
    re-read this same body and could call the session dead. The browser just
    captured a live response, so that verdict must not survive."""
    result = retrieve.from_observed_body(_page([], None), "UserTweetsAndReplies")
    assert result.tweets == []
    assert result.stop_reason == "browser_observed"


def test_browser_observed_is_a_declared_stop_reason():
    assert "browser_observed" in retrieve.STOP_REASONS


def _user_entry(rest_id: str, screen_name: str) -> dict:
    return {
        "entryId": f"user-{rest_id}",
        "content": {
            "itemContent": {
                "user_results": {
                    "result": {
                        "rest_id": rest_id,
                        "core": {"screen_name": screen_name},
                        "legacy": {},
                    }
                }
            }
        },
    }


def _user_page(users: list[tuple[str, str]], cursor: str | None) -> dict:
    entries = [_user_entry(uid, name) for uid, name in users]
    if cursor is not None:
        entries.append(
            {
                "entryId": "cursor-bottom",
                "content": {
                    "entryType": "TimelineTimelineCursor",
                    "cursorType": "Bottom",
                    "value": cursor,
                },
            }
        )
    return {
        "data": {
            "user": {
                "result": {
                    "timeline": {
                        "timeline": {
                            "instructions": [{"type": "TimelineAddEntries", "entries": entries}]
                        }
                    }
                }
            }
        }
    }


def test_paginate_users_walks_pages_and_dedups():
    client = FakeReadClient(
        [
            _user_page([("1", "a"), ("2", "b")], "C1"),
            _user_page([("2", "b"), ("3", "c")], None),
        ]
    )
    result = retrieve.paginate_users(
        client, "Following", "qid", {}, lambda cursor: {"cursor": cursor}
    )
    assert [u.id for u in result.users] == ["1", "2", "3"]
    assert result.stop_reason == "feed_exhausted"


def test_paginate_users_respects_limit():
    client = FakeReadClient([_user_page([(str(i), f"u{i}") for i in range(1, 6)], "C1")])
    result = retrieve.paginate_users(
        client, "Following", "qid", {}, lambda cursor: {"cursor": cursor}, limit=2
    )
    assert [u.id for u in result.users] == ["1", "2"]
    assert result.stop_reason == "limit_reached"


def test_paginate_users_stops_after_consecutive_empty_pages():
    """LIVE-OBSERVED 2026-07-20: @X's Following returns one account and then
    cursor-only pages forever, each with a NEW cursor. Without this stop the run
    burns the whole 500-request budget (~250s) for one account."""
    pages = [_user_page([("1", "a")], "C1")]
    pages += [_user_page([], f"C{i}") for i in range(2, 8)]
    client = FakeReadClient(pages)
    result = retrieve.paginate_users(
        client, "Following", "qid", {}, lambda cursor: {"cursor": cursor}
    )
    assert [u.id for u in result.users] == ["1"]
    assert result.stop_reason == "empty_pages"
    # 1 page with the account + the empty-page limit, then it gives up.
    assert client.requests_made == 1 + retrieve._EMPTY_USER_PAGE_LIMIT


def test_empty_pages_is_not_reported_as_feed_exhausted():
    """ "We gave up" and "we reached the end" are different facts; reporting the
    first as the second would quietly overstate completeness."""
    assert "empty_pages" in retrieve.STOP_REASONS


def test_a_page_of_only_duplicates_counts_as_empty():
    """Dedup can empty a page that was not empty on the wire -- that must still
    advance the give-up counter, or a repeating page loops forever."""
    pages = [_user_page([("1", "a")], f"C{i}") for i in range(1, 8)]
    client = FakeReadClient(pages)
    result = retrieve.paginate_users(
        client, "Following", "qid", {}, lambda cursor: {"cursor": cursor}
    )
    assert [u.id for u in result.users] == ["1"]
    assert result.stop_reason == "empty_pages"
