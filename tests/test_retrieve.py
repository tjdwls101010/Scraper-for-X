from datetime import UTC, datetime

from scraper_for_x import errors, retrieve, session


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

    def get(self, query_id, operation, variables, features):
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
