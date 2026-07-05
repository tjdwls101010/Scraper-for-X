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
# LIVE-CAPTURED 2026-07-05 (browser network capture, real logged-in session)
# against three real ops (UserTweetsAndReplies, SearchTimeline, TweetDetail)
# -- identical across all three, confirming X's web client sends one shared,
# comprehensive feature bundle rather than a per-op-tailored one. Still a
# fallback (not the source of truth) relative to queryids.py's harvested/
# re-anchored value (plan §8, §12), but no longer a guess: this is the exact
# dict a real browser sent.
DEFAULT_FEATURES: dict = {
    "rweb_video_screen_enabled": False,
    "rweb_cashtags_enabled": True,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": False,
    "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "rweb_cashtags_composer_attachment_enabled": True,
    "responsive_web_jetfuel_frame": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "responsive_web_grok_annotations_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "rweb_conversational_replies_downvote_enabled": False,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "content_disclosure_indicator_enabled": True,
    "content_disclosure_ai_generated_indicator_enabled": True,
    "responsive_web_grok_show_grok_translated_post": True,
    "responsive_web_grok_analysis_button_from_backend": True,
    "post_ctas_fetch_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": False,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}

# `fieldToggles` -- a THIRD query param some ops require alongside
# variables/features (plan §8/§12 didn't anticipate this; found live
# 2026-07-05 debugging a 404 on UserTweetsAndReplies/TweetDetail). Per-op,
# unlike features -- SearchTimeline needs none at all.
USER_TWEETS_AND_REPLIES_FIELD_TOGGLES: dict = {"withArticlePlainText": False}
TWEET_DETAIL_FIELD_TOGGLES: dict = {
    "withArticleRichContentState": True,
    "withArticlePlainText": False,
    "withArticleSummaryText": True,
    "withArticleVoiceOver": True,
    "withGrokAnalyze": False,
    "withDisallowedReplyControls": False,
}


def user_tweets_variables(
    user_id: str,
    *,
    cursor: str | None = None,
    count: int = 40,
    include_replies: bool = False,
) -> dict:
    """Build `variables` for `UserTweets` / `UserTweetsAndReplies`.

    LIVE-CAPTURED 2026-07-05: the two variants genuinely differ.
    `withQuickPromoteEligibilityTweetFields` appears ONLY on plain `UserTweets`
    (proven live: present and working); the real `UserTweetsAndReplies`
    request omits it entirely and adds `withCommunity` instead -- sending
    the extra field there previously caused a 404 (X's persisted query
    validates variables strictly against what it declares).
    """
    variables: dict = {
        "userId": user_id,
        "count": count,
        "includePromotedContent": True,
        "withVoice": True,
    }
    if not include_replies:
        variables["withQuickPromoteEligibilityTweetFields"] = True
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
    """Build `variables` for `SearchTimeline`. `product` is "Latest" or "Top" (plan §1).

    LIVE-CAPTURED 2026-07-05: `withGrokTranslatedBio`/
    `withQuickPromoteEligibilityTweetFields` are both required -- omitting
    them previously caused a 404.
    """
    variables: dict = {
        "rawQuery": query,
        "count": count,
        "querySource": "typed_query",
        "product": product,
        "withGrokTranslatedBio": True,
        "withQuickPromoteEligibilityTweetFields": False,
    }
    if cursor is not None:
        variables["cursor"] = cursor
    return variables


def tweet_detail_variables(tweet_id: str, *, cursor: str | None = None) -> dict:
    """Build `variables` for `TweetDetail`.

    LIVE-CAPTURED 2026-07-05, field-for-field, incl. `rankingMode` (missing
    from the original guess).
    """
    variables: dict = {
        "focalTweetId": tweet_id,
        "with_rux_injections": False,
        "rankingMode": "Relevance",
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
