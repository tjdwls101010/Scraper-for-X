"""Static shape of X's GraphQL read requests.

This module defines endpoint templates, the public bearer constant, and
per-operation `variables`/`features` builders for X's internal GraphQL API.

It does NOT make HTTP requests (see `client.py`) and does NOT know the
*current* query-id values (see `queryids.py`) -- callers pass a `query_id`
string into `build_url()`. This module is pure request-shape-building logic.
"""

from __future__ import annotations

# X's well-known PUBLIC static web bearer token, shipped as a literal by
# X's own web client and virtually every OSS X-scraper (twikit, twscrape,
# gallery-dl). It is NOT account-specific and NOT secret -- unlike the
# `ct0`/`auth_token` session cookies, which are (plan §8, §17 G-bearer-static).
BEARER_TOKEN = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

GRAPHQL_BASE = "https://x.com/i/api/graphql"


def build_url(query_id: str, operation: str) -> str:
    """Build the GraphQL endpoint URL for a given query id and operation name."""
    return f"{GRAPHQL_BASE}/{query_id}/{operation}"


# Shared features flag-map sent as the `features` query param on every read.
# This is a FALLBACK default only -- the real source of truth is the
# harvested/re-anchored value from `queryids.py` (plan §8, §12). Every key
# below is a best-effort guess as of 2026, NOT probe-verified against a live
# capture. Flag it for validation during implementation testing.
DEFAULT_FEATURES: dict = {
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}


def user_tweets_variables(
    user_id: str,
    *,
    cursor: str | None = None,
    count: int = 40,
    include_replies: bool = False,
) -> dict:
    """Build `variables` for `UserTweets` / `UserTweetsAndReplies`.

    NOTE: this variable set was NOT exhaustively probe-verified against a
    live capture -- verify it during implementation testing (plan §8, §12).
    Uncertain keys: `withQuickPromoteEligibilityTweetFields` (name/presence
    guessed by analogy to other X GraphQL ops) and `withCommunity` (guessed
    as the extra key the replies variant adds; not confirmed live).
    """
    variables: dict = {
        "userId": user_id,
        "count": count,
        "includePromotedContent": True,
        "withQuickPromoteEligibilityTweetFields": True,
        "withVoice": True,
    }
    if cursor is not None:
        variables["cursor"] = cursor
    if include_replies:
        variables["withCommunity"] = True
    return variables


def search_timeline_variables(
    query: str,
    *,
    cursor: str | None = None,
    count: int = 20,
    product: str = "Latest",
) -> dict:
    """Build `variables` for `SearchTimeline`. `product` is "Latest" or "Top" (plan §1)."""
    variables: dict = {
        "rawQuery": query,
        "count": count,
        "querySource": "typed_query",
        "product": product,
    }
    if cursor is not None:
        variables["cursor"] = cursor
    return variables


def tweet_detail_variables(tweet_id: str, *, cursor: str | None = None) -> dict:
    """Build `variables` for `TweetDetail`."""
    variables: dict = {
        "focalTweetId": tweet_id,
        "with_rux_injections": False,
        "includePromotedContent": True,
        "withCommunity": True,
        "withQuickPromoteEligibilityTweetFields": True,
        "withBirdwatchNotes": True,
        "withVoice": True,
    }
    if cursor is not None:
        variables["cursor"] = cursor
    return variables


def user_by_screen_name_variables(screen_name: str) -> dict:
    """Build `variables` for `UserByScreenName` (handle -> rest_id resolution)."""
    return {
        "screen_name": screen_name,
        "withSafetyModeUserFields": True,
    }
