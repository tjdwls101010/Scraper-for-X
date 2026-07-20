"""Walk one GraphQL response envelope into raw tweet dicts + the next cursor.

Per-op envelope roots, "parse ALL instructions" (incl. the pin-entry
gotcha), and cursor extraction are pinned in plan §8; see also §17
G-pin-instruction and G-cursor-eof.

This module does NOT build ``Tweet`` dataclasses (that's ``model.py``) and does
NOT drive pagination (that's ``retrieve.py``) -- it handles exactly one response
body per call and hands raw ``tweet_results.result`` dicts onward.
"""

from __future__ import annotations

from .errors import ScraperForXError

# Per-op path to the instructions[] list, pinned exactly per plan §8's
# "Per-op envelope roots" subsection.
ENVELOPE_ROOTS: dict[str, tuple[str, ...]] = {
    "UserTweets": ("data", "user", "result", "timeline", "timeline", "instructions"),
    "UserTweetsAndReplies": ("data", "user", "result", "timeline", "timeline", "instructions"),
    "SearchTimeline": (
        "data",
        "search_by_raw_query",
        "search_timeline",
        "timeline",
        "instructions",
    ),
    "TweetDetail": ("data", "threaded_conversation_with_injections_v2", "instructions"),
    # LIVE-CAPTURED 2026-07-20: the home feed nests one level deeper than the
    # profile timelines, under `home.home_timeline_urt` rather than
    # `user.result.timeline.timeline`.
    "HomeTimeline": ("data", "home", "home_timeline_urt", "instructions"),
}


class EnvelopeParseError(ScraperForXError):
    """``instructions[]`` could not be located at all for this operation.

    Distinct from an empty-but-valid page (zero tweet entries): this is a
    structural failure -- the anchored path was missing or the wrong shape,
    signaling real query-id/response-shape drift (plan §11, §12). This is the
    condition the CLI maps to exit code 4; an empty result list returned from
    ``walk_instructions`` without this exception means "parsed fine, nothing
    there," which is exit 0, not exit 4.

    Subclasses ``ScraperForXError`` (not just ``Exception``) and is re-exported
    from the package root, so a library caller doing
    ``except ScraperForXError:`` around ``XScraper`` reads -- the documented
    pattern (see ``errors.py``'s module docstring) -- actually catches this,
    rather than needing to reach into the private ``scraper_for_x.parse``
    submodule.
    """


# Per-op path for the social-graph ops, which return Users rather than Tweets.
# LIVE-CAPTURED 2026-07-20. Following/Followers reuse the profile-timeline root;
# Retweeters has its own.
USER_ENVELOPE_ROOTS: dict[str, tuple[str, ...]] = {
    "Following": ("data", "user", "result", "timeline", "timeline", "instructions"),
    "Followers": ("data", "user", "result", "timeline", "timeline", "instructions"),
    "Retweeters": ("data", "retweeters_timeline", "timeline", "instructions"),
}


def _get_path(d: dict, path: tuple[str, ...]) -> object | None:
    """Safe nested-dict walk. Returns None if any hop is missing or not a dict --
    never raises.
    """
    current: object = d
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def walk_instructions(response: dict, operation: str) -> tuple[list[tuple[dict, bool]], str | None]:
    """Parse one raw GraphQL response body for ``operation``.

    Returns ``(raw_tweets, bottom_cursor)`` where ``raw_tweets`` is a list of
    ``(raw_tweet_result_dict, is_pinned)`` tuples, in page order, and
    ``bottom_cursor`` is the ``TimelineTimelineCursor`` / ``Bottom`` value for
    this page, or ``None`` if this page carried none.

    Raises ``EnvelopeParseError`` if ``instructions[]`` cannot be located at
    all for ``operation`` -- never returns an empty list silently on a
    structural failure (plan §11).
    """
    root_path = ENVELOPE_ROOTS[operation]
    instructions = _get_path(response, root_path)
    if not isinstance(instructions, list):
        raise EnvelopeParseError(
            f"could not locate instructions[] for operation {operation!r} at path {root_path!r}"
        )

    raw_tweets: list[tuple[dict, bool]] = []
    bottom_cursor: str | None = None

    for instruction in instructions:
        if not isinstance(instruction, dict):
            continue
        instruction_type = instruction.get("type")

        if instruction_type == "TimelineAddEntries":
            entries = instruction.get("entries")
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                entry_id = entry.get("entryId")
                content = entry.get("content")
                if not isinstance(content, dict):
                    continue

                if isinstance(entry_id, str) and (
                    entry_id.startswith("tweet-") or entry_id.startswith("conversationthread-")
                ):
                    result = _get_path(entry, ("content", "itemContent", "tweet_results", "result"))
                    if isinstance(result, dict):
                        raw_tweets.append((result, False))
                    continue

                if (
                    content.get("entryType") == "TimelineTimelineCursor"
                    and content.get("cursorType") == "Bottom"
                ):
                    value = content.get("value")
                    if isinstance(value, str):
                        bottom_cursor = value

        elif instruction_type == "TimelinePinEntry":
            # G-pin-instruction: the pinned tweet lives in a SINGULAR `entry`,
            # not a list, and not inside TimelineAddEntries -- must be unwrapped
            # separately or it is silently dropped.
            entry = instruction.get("entry")
            if isinstance(entry, dict):
                result = _get_path(entry, ("content", "itemContent", "tweet_results", "result"))
                if isinstance(result, dict):
                    raw_tweets.append((result, True))

    return raw_tweets, bottom_cursor


def walk_user_instructions(response: dict, operation: str) -> tuple[list[dict], str | None]:
    """Parse one social-graph response body into raw user dicts + next cursor.

    The Tweet-returning ops and the User-returning ops share an envelope
    *shape* -- ``TimelineAddEntries``, a Bottom cursor, per-entry
    ``itemContent`` -- but not the entry contents: here each entry is
    ``user-<id>`` carrying ``content.itemContent.user_results.result``, which
    ``model.build_user`` reads unchanged.

    Kept as a sibling of ``walk_instructions`` rather than a flag on it: the
    two return different types, and a shared function that returns "either
    tweets or users" would push that branch onto every caller.
    """
    root_path = USER_ENVELOPE_ROOTS[operation]
    instructions = _get_path(response, root_path)
    if not isinstance(instructions, list):
        raise EnvelopeParseError(
            f"could not locate instructions[] for operation {operation!r} at path {root_path!r}"
        )

    raw_users: list[dict] = []
    bottom_cursor: str | None = None

    for instruction in instructions:
        if not isinstance(instruction, dict) or instruction.get("type") != "TimelineAddEntries":
            continue
        entries = instruction.get("entries")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_id = entry.get("entryId")
            content = entry.get("content")
            if not isinstance(content, dict):
                continue

            if isinstance(entry_id, str) and entry_id.startswith("user-"):
                result = _get_path(entry, ("content", "itemContent", "user_results", "result"))
                if isinstance(result, dict):
                    raw_users.append(result)
                continue

            if (
                content.get("entryType") == "TimelineTimelineCursor"
                and content.get("cursorType") == "Bottom"
            ):
                value = content.get("value")
                if isinstance(value, str):
                    bottom_cursor = value

    return raw_users, bottom_cursor
