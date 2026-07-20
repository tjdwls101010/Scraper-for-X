import pytest

from scraper_for_x import EnvelopeParseError, ScraperForXError, parse


def _rest_id(raw_tweet: dict) -> str:
    """`walk_instructions` deliberately hands back the raw node AS-IS --
    unwrapping a `TweetWithVisibilityResults` node (whose `rest_id` lives one
    level down, under `.tweet`) is model.py's job, not parse.py's."""
    if raw_tweet.get("__typename") == "TweetWithVisibilityResults":
        return raw_tweet["tweet"]["rest_id"]
    return raw_tweet["rest_id"]


def test_user_tweets_envelope_root_locates_instructions(load_fixture):
    body = load_fixture("user_tweets.json")
    raw_tweets, cursor = parse.walk_instructions(body, "UserTweets")
    # 7 TimelineAddEntries tweets + 1 TimelinePinEntry tweet = 8.
    assert len(raw_tweets) == 8
    assert cursor == "CURSOR_BOTTOM_PAGE1"


def test_pinned_tweet_comes_from_timeline_pin_entry_not_add_entries(load_fixture):
    """G-pin-instruction: the pinned tweet lives in a separate TimelinePinEntry
    instruction, not inside TimelineAddEntries -- a parser that only reads
    TimelineAddEntries silently drops it."""
    body = load_fixture("user_tweets.json")
    raw_tweets, _ = parse.walk_instructions(body, "UserTweets")
    pinned = [(t, p) for t, p in raw_tweets if p]
    assert len(pinned) == 1
    assert _rest_id(pinned[0][0]) == "9999"

    non_pinned_ids = {_rest_id(t) for t, p in raw_tweets if not p}
    assert "9999" not in non_pinned_ids


def test_all_seven_add_entries_tweets_are_non_pinned(load_fixture):
    body = load_fixture("user_tweets.json")
    raw_tweets, _ = parse.walk_instructions(body, "UserTweets")
    non_pinned_ids = {_rest_id(t) for t, p in raw_tweets if not p}
    assert non_pinned_ids == {"1001", "1002", "1003", "1004", "1005", "1006", "1007"}


def test_search_timeline_envelope_root_and_cursor(load_fixture):
    body = load_fixture("search_timeline.json")
    raw_tweets, cursor = parse.walk_instructions(body, "SearchTimeline")
    assert {t["rest_id"] for t, _ in raw_tweets} == {"5001", "5002"}
    assert cursor == "CURSOR_BOTTOM_SEARCH_PAGE1"


def test_tweet_detail_envelope_root_incl_conversationthread_prefix(load_fixture):
    body = load_fixture("tweet_detail.json")
    raw_tweets, cursor = parse.walk_instructions(body, "TweetDetail")
    # Both the "tweet-" focal entry AND the "conversationthread-" reply entry
    # must be picked up -- only matching "tweet-" would silently drop replies.
    assert {t["rest_id"] for t, _ in raw_tweets} == {"6000", "6001"}
    assert cursor is None  # this fixture carries no cursor entry


def test_envelope_parse_error_on_missing_instructions():
    """A response that doesn't even have the anchored instructions[] path is a
    structural failure (query-id/response-shape drift), not an empty result --
    the CLI maps this to exit 4, never to a silent empty list (plan §11)."""
    with pytest.raises(parse.EnvelopeParseError):
        parse.walk_instructions({"data": {}}, "UserTweets")


def test_envelope_parse_error_is_a_public_scraper_for_x_error():
    """Regression guard: a library user doing `except ScraperForXError:` around
    an XScraper read (the documented pattern, per errors.py's module
    docstring) must actually catch a query-id-drift failure -- it previously
    only subclassed the bare `Exception`, and wasn't re-exported from the
    package root at all."""
    assert issubclass(EnvelopeParseError, ScraperForXError)
    assert parse.EnvelopeParseError is EnvelopeParseError  # same class, not a shadow copy


def test_envelope_parse_error_when_instructions_is_wrong_type():
    bad_response = {
        "data": {"user": {"result": {"timeline": {"timeline": {"instructions": "not-a-list"}}}}}
    }
    with pytest.raises(parse.EnvelopeParseError):
        parse.walk_instructions(bad_response, "UserTweets")


def test_unrecognized_entry_shapes_are_skipped_not_fatal(load_fixture):
    body = load_fixture("user_tweets.json")
    # Inject a garbage entry alongside the real ones and confirm it's ignored
    # rather than raising.
    instructions = body["data"]["user"]["result"]["timeline"]["timeline"]["instructions"]
    instructions[0]["entries"].append(
        {"entryId": "who-knows-1", "content": {"entryType": "Mystery"}}
    )
    raw_tweets, _ = parse.walk_instructions(body, "UserTweets")
    assert len(raw_tweets) == 8  # unchanged -- the garbage entry contributed nothing


def test_empty_but_valid_envelope_returns_empty_list_not_an_error():
    empty_response = {
        "data": {
            "user": {
                "result": {
                    "timeline": {
                        "timeline": {
                            "instructions": [{"type": "TimelineAddEntries", "entries": []}]
                        }
                    }
                }
            }
        }
    }
    raw_tweets, cursor = parse.walk_instructions(empty_response, "UserTweets")
    assert raw_tweets == []
    assert cursor is None


def test_thin_page_with_new_cursor_is_not_conflated_with_eof(load_fixture):
    """A page with zero tweet entries but a genuinely new cursor means
    "continue", never "stop" (plan §8 G-cursor-eof) -- this module only
    reports what it saw; the EOF *decision* belongs to retrieve.py, but the
    cursor value itself must still be extracted correctly from a thin page."""
    thin_page = {
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
                                            "entryId": "cursor-bottom-thin",
                                            "content": {
                                                "entryType": "TimelineTimelineCursor",
                                                "cursorType": "Bottom",
                                                "value": "CURSOR_STILL_ADVANCING",
                                            },
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                }
            }
        }
    }
    raw_tweets, cursor = parse.walk_instructions(thin_page, "UserTweets")
    assert raw_tweets == []
    assert cursor == "CURSOR_STILL_ADVANCING"


def test_home_timeline_envelope_root_and_cursor(load_fixture):
    """The home feed nests one level deeper than the profile timelines
    (`home.home_timeline_urt`), so it needs its own ENVELOPE_ROOTS entry --
    without it `walk_instructions` raises rather than silently returning [].
    """
    body = load_fixture("home_timeline.json")
    raw_tweets, cursor = parse.walk_instructions(body, "HomeTimeline")
    assert {t["rest_id"] for t, _ in raw_tweets} == {"7001", "7002"}
    assert cursor == "CURSOR_BOTTOM_HOME_PAGE1"


def test_home_timeline_promoted_entries_are_excluded(load_fixture):
    """LIVE-OBSERVED 2026-07-20: a real home feed page carried 7 `promoted-*`
    ad entries alongside 28 real `tweet-*` ones. They are dropped for free by
    the `tweet-`/`conversationthread-` entryId prefix check -- pinned here as a
    contract so a future widening of that check can't start leaking ads into
    the output.
    """
    body = load_fixture("home_timeline.json")
    raw_tweets, _ = parse.walk_instructions(body, "HomeTimeline")
    assert "7003" not in {t["rest_id"] for t, _ in raw_tweets}


def test_home_timeline_module_entries_are_skipped(load_fixture):
    """A `TimelineTimelineModule` (who-to-follow etc.) carries no
    `tweet_results` and must not break the walk."""
    body = load_fixture("home_timeline.json")
    raw_tweets, _ = parse.walk_instructions(body, "HomeTimeline")
    assert len(raw_tweets) == 2
