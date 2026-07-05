from datetime import UTC, datetime

from scraper_for_x import model, parse


def _tweets_by_id(load_fixture, name: str, operation: str) -> dict:
    body = load_fixture(name)
    raw_tweets, _ = parse.walk_instructions(body, operation)
    built = {}
    for raw, is_pinned in raw_tweets:
        tweet = model.build_tweet(raw, is_pinned=is_pinned, captured_at=datetime.now(UTC))
        assert tweet is not None
        built[tweet.id] = tweet
    return built


def test_normal_tweet_fields(load_fixture):
    tweets = _tweets_by_id(load_fixture, "user_tweets.json", "UserTweets")
    t = tweets["1001"]
    assert (
        t.text
        == "This is a normal synthetic tweet with a hashtag #synthtag and a link https://example.test/article"
    )
    assert t.is_note_tweet is False
    assert t.view_count == 42
    assert t.like_count == 5
    assert t.hashtags == ["synthtag"]
    assert t.urls == ["https://example.test/article"]
    assert len(t.media) == 1
    assert t.media[0].kind == "photo"
    assert t.media[0].url == "https://media.example.test/synthetic1.jpg"
    assert t.author is not None
    assert t.author.id == "9001"
    assert t.author.screen_name == "synth_author"
    assert t.author.name == "Synthetic Author"
    assert t.author.followers_count == 100
    assert t.author.is_blue_verified is True
    assert t.url == "https://x.com/synth_author/status/1001"
    assert t.is_reply is False
    assert t.is_pinned is False
    assert t.is_restricted is False


def test_retweet_text_resolved_from_original_not_truncated_stub(load_fixture):
    """G-retweet-text + G-note-tweet: the outer node's full_text is the
    truncated "RT @…" stub with no note_tweet -- text/media/is_note_tweet must
    come from the retweeted ORIGINAL node's own note_tweet-preferred text."""
    tweets = _tweets_by_id(load_fixture, "user_tweets.json", "UserTweets")
    t = tweets["1002"]
    assert t.text.startswith("This is a long synthetic tweet that exceeds")
    assert "carries the full, untruncated text" in t.text
    assert t.is_note_tweet is True
    assert len(t.media) == 1
    assert t.media[0].url == "https://media.example.test/synthetic_original.jpg"
    assert t.hashtags == ["longform"]

    # But identity/counts/author/created_at come from the OUTER (retweeting) node.
    assert t.id == "1002"
    assert t.author.screen_name == "synth_retweeter"
    assert t.retweet_count == 9

    # The retweeted original is itself attached, one level, with its own fields.
    assert t.retweeted_tweet is not None
    assert t.retweeted_tweet.id == "1002000"
    assert t.retweeted_tweet.author.screen_name == "synth_original"
    assert t.retweeted_tweet.is_note_tweet is True


def test_view_count_none_when_views_object_absent(load_fixture):
    """G-view-count-none: `views` missing entirely must not crash the parse."""
    tweets = _tweets_by_id(load_fixture, "user_tweets.json", "UserTweets")
    assert tweets["1003"].view_count is None


def test_view_count_none_when_count_field_absent():
    raw = {"rest_id": "1", "legacy": {"full_text": "x"}, "views": {}}
    tweet = model.build_tweet(raw, captured_at=datetime.now(UTC))
    assert tweet.view_count is None


def test_view_count_parsed_from_string():
    raw = {"rest_id": "1", "legacy": {"full_text": "x"}, "views": {"count": "123"}}
    tweet = model.build_tweet(raw, captured_at=datetime.now(UTC))
    assert tweet.view_count == 123


def test_visibility_wrapper_unwrapped_and_flagged_restricted(load_fixture):
    """G-visibility-wrapper: TweetWithVisibilityResults wraps the real tweet
    under `.tweet` -- unwrap it and set is_restricted=True."""
    tweets = _tweets_by_id(load_fixture, "user_tweets.json", "UserTweets")
    t = tweets["1004"]
    assert t.is_restricted is True
    assert (
        t.text
        == "A synthetic subscriber-only/restricted tweet wrapped in TweetWithVisibilityResults."
    )
    assert t.author.screen_name == "synth_restricted"


def test_missing_created_at_is_none_not_a_crash(load_fixture):
    tweets = _tweets_by_id(load_fixture, "user_tweets.json", "UserTweets")
    assert tweets["1005"].created_at is None
    # Every other field on the same tweet still parses fine.
    assert tweets["1005"].text == "A synthetic tweet whose created_at field is missing entirely."


def test_quote_tweet_one_level_deep_with_its_own_text(load_fixture):
    tweets = _tweets_by_id(load_fixture, "user_tweets.json", "UserTweets")
    t = tweets["1006"]
    assert t.text == "Quoting a synthetic tweet, see below."  # the quoting tweet's OWN text
    assert t.quoted_tweet is not None
    assert t.quoted_tweet.id == "2006"
    assert t.quoted_tweet.text == "This is the synthetic quoted tweet's own text."
    assert t.quoted_tweet.author.screen_name == "synth_quoted"


def test_pinned_flag_passed_through(load_fixture):
    tweets = _tweets_by_id(load_fixture, "user_tweets.json", "UserTweets")
    assert tweets["9999"].is_pinned is True
    assert tweets["1001"].is_pinned is False


def test_retweet_of_a_visibility_wrapped_original(load_fixture):
    """A retweet whose ORIGINAL is itself wrapped in TweetWithVisibilityResults
    (a retweeted subscriber-only/restricted tweet) -- both the text-resolution
    path (_resolve_text_node) and the nested retweeted_tweet object (built via
    a recursive build_tweet call, which does its own unwrap) must independently
    see through the wrapper. Regression guard for an interaction the plan's
    model.py agent flagged as uncertain (plan §6, §20)."""
    tweets = _tweets_by_id(load_fixture, "user_tweets.json", "UserTweets")
    t = tweets["1007"]
    assert t.text == "The original restricted tweet's text, retweeted by someone else."
    assert t.retweeted_tweet is not None
    assert t.retweeted_tweet.id == "1007000"
    assert t.retweeted_tweet.is_restricted is True
    assert t.retweeted_tweet.author.screen_name == "synth_restricted_original"
    assert (
        t.retweeted_tweet.text == "The original restricted tweet's text, retweeted by someone else."
    )


def test_build_tweet_returns_none_for_missing_rest_id():
    assert model.build_tweet({"legacy": {"full_text": "x"}}) is None


def test_build_user_returns_none_for_missing_rest_id():
    assert model.build_user({"core": {"screen_name": "x"}}) is None
    assert model.build_user(None) is None


def test_to_dict_serializes_datetimes_as_iso_utc(load_fixture):
    tweets = _tweets_by_id(load_fixture, "user_tweets.json", "UserTweets")
    d = tweets["1001"].to_dict()
    assert d["created_at"] == "2026-07-01T12:00:00Z"
    assert d["author"]["created_at"] == "2018-01-01T00:00:00Z"
    assert d["retweeted_tweet"] is None
    assert d["quoted_tweet"] is None
    assert "raw" not in d  # opt-in only


def test_to_dict_none_created_at_serializes_as_null(load_fixture):
    tweets = _tweets_by_id(load_fixture, "user_tweets.json", "UserTweets")
    assert tweets["1005"].to_dict()["created_at"] is None


def test_raw_opt_in_attaches_original_node(load_fixture):
    body = load_fixture("user_tweets.json")
    raw_tweets, _ = parse.walk_instructions(body, "UserTweets")
    raw_node, is_pinned = next((r, p) for r, p in raw_tweets if r["rest_id"] == "1001")
    tweet = model.build_tweet(raw_node, is_pinned=is_pinned, raw=True)
    assert tweet.raw is not None
    assert tweet.raw["rest_id"] == "1001"
    assert tweet.to_dict()["raw"]["rest_id"] == "1001"
