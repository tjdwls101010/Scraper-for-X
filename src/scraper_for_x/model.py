"""Output schema (plan §6). Decided pre-1.0: additive fields later are a minor
bump, but reinterpreting an existing field's meaning is a breaking change.

No field defaults except ``Tweet.raw`` (opt-in) — the builder must decide
every field explicitly rather than silently defaulting a forgotten one.

Pure dict-in, dataclass-out: no network/IO. ``parse.py`` walks the GraphQL
instructions envelope and hands this module the raw ``tweet_results.result``
node; this module only normalizes that node into typed objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_created_at(value: str | None) -> datetime | None:
    """Parse X's classic timestamp format, e.g. "Wed Oct 10 20:19:24 +0000 2018".

    RFC-2822-ish; ``email.utils.parsedate_to_datetime`` handles it directly
    (plan §14: no ``dateutil``). Missing or unparseable -> None, never raise
    (plan §11 explicitly designs around ``created_at`` being legitimately None).
    """
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass
class Media:
    kind: str  # "photo" | "video" | "animated_gif" | "unknown"
    #: pbs.twimg.com / video.twimg.com URL (see G-media-expiry, §17)
    url: str
    width: int | None = None
    height: int | None = None
    alt_text: str | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "url": self.url,
            "width": self.width,
            "height": self.height,
            "alt_text": self.alt_text,
        }


@dataclass
class User:
    id: str  # rest_id (stable)
    screen_name: str  # core.screen_name (@handle)
    name: str | None  # core.name
    created_at: datetime | None
    followers_count: int | None  # legacy.followers_count
    following_count: int | None  # legacy.friends_count
    tweet_count: int | None  # legacy.statuses_count
    is_blue_verified: bool | None
    description: str | None
    url: str | None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "screen_name": self.screen_name,
            "name": self.name,
            "created_at": _iso(self.created_at),
            "followers_count": self.followers_count,
            "following_count": self.following_count,
            "tweet_count": self.tweet_count,
            "is_blue_verified": self.is_blue_verified,
            "description": self.description,
            "url": self.url,
        }


@dataclass
class Tweet:
    id: str  # rest_id (stable dedup/merge key)
    url: str | None  # https://x.com/<handle>/status/<id>
    created_at: datetime | None  # legacy.created_at (X ts format), UTC — MAY be None (§11)
    text: str  # long/full text (see extraction rule below)
    lang: str | None
    author: User | None  # core.user_results.result
    is_reply: bool
    in_reply_to_id: str | None  # legacy.in_reply_to_status_id_str
    conversation_id: str | None  # legacy.conversation_id_str
    reply_count: int | None
    retweet_count: int | None
    quote_count: int | None
    like_count: int | None  # legacy.favorite_count
    bookmark_count: int | None
    view_count: int | None  # views.count IF present, else None
    media: list[Media]  # legacy.extended_entities.media (of the resolved text node)
    urls: list[str]  # legacy.entities.urls[].expanded_url
    hashtags: list[str]
    is_note_tweet: bool  # resolved text came from note_tweet (long form)
    is_pinned: bool  # delivered via a TimelinePinEntry instruction (§8)
    retweeted_tweet: Tweet | None  # legacy.retweeted_status_result (one level)
    quoted_tweet: Tweet | None  # quoted_status_result (one level)
    is_restricted: bool  # __typename == "TweetWithVisibilityResults" (subscriber/limited)
    captured_at: datetime  # UTC
    raw: dict | None = None  # only if raw=True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "created_at": _iso(self.created_at),
            "text": self.text,
            "lang": self.lang,
            "author": self.author.to_dict() if self.author is not None else None,
            "is_reply": self.is_reply,
            "in_reply_to_id": self.in_reply_to_id,
            "conversation_id": self.conversation_id,
            "reply_count": self.reply_count,
            "retweet_count": self.retweet_count,
            "quote_count": self.quote_count,
            "like_count": self.like_count,
            "bookmark_count": self.bookmark_count,
            "view_count": self.view_count,
            "media": [m.to_dict() for m in self.media],
            "urls": self.urls,
            "hashtags": self.hashtags,
            "is_note_tweet": self.is_note_tweet,
            "is_pinned": self.is_pinned,
            "retweeted_tweet": (
                self.retweeted_tweet.to_dict() if self.retweeted_tweet is not None else None
            ),
            "quoted_tweet": self.quoted_tweet.to_dict() if self.quoted_tweet is not None else None,
            "is_restricted": self.is_restricted,
            "captured_at": _iso(self.captured_at),
            **({"raw": self.raw} if self.raw is not None else {}),
        }


# --- normalization: raw tweet_results.result dict -> Tweet ---------------------


def build_media(raw_media_item: dict) -> Media:
    kind = raw_media_item.get("type") if isinstance(raw_media_item.get("type"), str) else "unknown"
    return Media(
        kind=kind,
        url=raw_media_item.get("media_url_https") or raw_media_item.get("media_url") or "",
        width=raw_media_item.get("original_info", {}).get("width"),
        height=raw_media_item.get("original_info", {}).get("height"),
        alt_text=raw_media_item.get("ext_alt_text"),
    )


def build_user(raw_user_result: dict | None) -> User | None:
    """G-user-core: ``name``/``screen_name``/``created_at`` are under ``.core``
    (2026), NOT the older ``legacy.name``/``legacy.screen_name`` twikit reads —
    ``legacy`` still supplies the counts/description/url.
    """
    if not isinstance(raw_user_result, dict):
        return None
    rest_id = raw_user_result.get("rest_id")
    if not rest_id:
        return None

    core = raw_user_result.get("core") or {}
    legacy = raw_user_result.get("legacy") or {}

    return User(
        id=str(rest_id),
        screen_name=core.get("screen_name") or "",
        name=core.get("name"),
        created_at=_parse_created_at(core.get("created_at")),
        followers_count=legacy.get("followers_count"),
        following_count=legacy.get("friends_count"),
        tweet_count=legacy.get("statuses_count"),
        is_blue_verified=raw_user_result.get("is_blue_verified"),
        description=legacy.get("description"),
        url=legacy.get("url"),
    )


def _resolve_text_node(node: dict) -> dict:
    """Return the node text/media/urls/is_note_tweet should be resolved from.

    G-visibility-wrapper applies recursively here too: a retweeted original can
    itself be wrapped in ``TweetWithVisibilityResults``.
    """
    if node.get("__typename") == "TweetWithVisibilityResults":
        inner = node.get("tweet")
        if isinstance(inner, dict):
            return inner
    return node


def _extract_text(node: dict) -> tuple[str, bool]:
    """G-note-tweet: prefer the long-form note_tweet text over legacy.full_text."""
    note_tweet = node.get("note_tweet") or {}
    note_result = (note_tweet.get("note_tweet_results") or {}).get("result") or {}
    note_text = note_result.get("text")
    if isinstance(note_text, str):
        return note_text, True
    legacy = node.get("legacy") or {}
    return legacy.get("full_text") or "", False


def _extract_media(node: dict) -> list[Media]:
    legacy = node.get("legacy") or {}
    extended_entities = legacy.get("extended_entities") or {}
    items = extended_entities.get("media") or []
    return [build_media(item) for item in items if isinstance(item, dict)]


def _extract_urls(node: dict) -> list[str]:
    legacy = node.get("legacy") or {}
    entities = legacy.get("entities") or {}
    urls = entities.get("urls") or []
    return [u["expanded_url"] for u in urls if isinstance(u, dict) and u.get("expanded_url")]


def _extract_hashtags(node: dict) -> list[str]:
    legacy = node.get("legacy") or {}
    entities = legacy.get("entities") or {}
    hashtags = entities.get("hashtags") or []
    return [h["text"] for h in hashtags if isinstance(h, dict) and h.get("text")]


def _extract_view_count(node: dict) -> int | None:
    """G-view-count-none: ``views.count`` is a string and often absent; a naive
    ``int(...)`` would crash an otherwise-valid page.
    """
    views = node.get("views")
    if not views:
        return None
    count = views.get("count")
    if count and isinstance(count, str) and count.isdigit():
        return int(count)
    return None


def build_tweet(
    raw_tweet_result: dict,
    *,
    is_pinned: bool = False,
    captured_at: datetime | None = None,
    raw: bool = False,
) -> Tweet | None:
    """Normalize one ``tweet_results.result`` dict into a ``Tweet``.

    Returns None if the node has no ``rest_id`` — structurally unusable. This
    is the signal callers use to skip a malformed/undisplayable entry (e.g. a
    tombstone) rather than crash the whole page.
    """
    is_restricted = False
    node = raw_tweet_result
    if node.get("__typename") == "TweetWithVisibilityResults":
        is_restricted = True
        inner = node.get("tweet")
        if not isinstance(inner, dict):
            return None
        node = inner

    tweet_id = node.get("rest_id")
    if not tweet_id:
        return None
    tweet_id = str(tweet_id)

    legacy = node.get("legacy") or {}

    # G-retweet-text: a retweet's outer full_text is a truncated "RT @…" stub —
    # resolve text/media/urls/is_note_tweet from the retweeted original node.
    raw_retweeted = legacy.get("retweeted_status_result") or {}
    retweeted_result = raw_retweeted.get("result")
    text_source = (
        _resolve_text_node(retweeted_result) if isinstance(retweeted_result, dict) else node
    )

    text, is_note_tweet = _extract_text(text_source)
    media = _extract_media(text_source)
    urls = _extract_urls(text_source)
    hashtags = _extract_hashtags(text_source)

    quoted_tweet = None
    raw_quoted = node.get("quoted_status_result")
    if isinstance(raw_quoted, dict):
        quoted_result = raw_quoted.get("result")
        if isinstance(quoted_result, dict):
            quoted_tweet = build_tweet(quoted_result, captured_at=captured_at, raw=raw)

    retweeted_tweet = None
    if isinstance(retweeted_result, dict):
        retweeted_tweet = build_tweet(retweeted_result, captured_at=captured_at, raw=raw)

    author = build_user((node.get("core") or {}).get("user_results", {}).get("result"))

    url = (
        f"https://x.com/{author.screen_name}/status/{tweet_id}"
        if author is not None
        else f"https://x.com/i/web/status/{tweet_id}"
    )

    conversation_id = legacy.get("conversation_id_str")

    return Tweet(
        id=tweet_id,
        url=url,
        created_at=_parse_created_at(legacy.get("created_at")),
        text=text,
        lang=legacy.get("lang"),
        author=author,
        is_reply=legacy.get("in_reply_to_status_id_str") is not None,
        in_reply_to_id=legacy.get("in_reply_to_status_id_str"),
        conversation_id=conversation_id,
        reply_count=legacy.get("reply_count"),
        retweet_count=legacy.get("retweet_count"),
        quote_count=legacy.get("quote_count"),
        like_count=legacy.get("favorite_count"),
        bookmark_count=legacy.get("bookmark_count"),
        view_count=_extract_view_count(node),
        media=media,
        urls=urls,
        hashtags=hashtags,
        is_note_tweet=is_note_tweet,
        is_pinned=is_pinned,
        retweeted_tweet=retweeted_tweet,
        quoted_tweet=quoted_tweet,
        is_restricted=is_restricted,
        captured_at=captured_at,
        raw=node if raw else None,
    )
