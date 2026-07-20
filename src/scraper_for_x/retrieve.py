"""Retrieval orchestration: limit/since/until composition, cursor pagination,
stop-reason tracking, and the pre-exit-4 soft-lock probe (plan §8, §9, §11).

Pure orchestration -- no CLI/exit-code knowledge here. Callers (``cli.py``,
``__init__.py``'s ``XScraper``) map ``RetrieveResult.stop_reason`` (and
``EnvelopeParseError``/typed errors propagating out of this module) onto the
plan §10 exit codes.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from . import errors, gql, model, parse, queryids, session

#: The full stop-reason vocabulary a `RetrieveResult` can carry (plan §9, §11).
STOP_REASONS = frozenset(
    {
        "limit_reached",
        "since_crossed",
        "feed_exhausted",
        "no_matches",
        "max_requests",
        "rate_limited",
        "soft_locked",
        # Only reachable via the browser-observe fallback: a browser page-load
        # fires its GraphQL op once, so the run necessarily stops after one
        # page. Distinct from "feed_exhausted" (there IS more, we just can't
        # reach it without the transaction id) and from "max_requests" (no
        # budget was hit).
        "browser_observed",
        # Social-graph only: X kept advancing the cursor while returning no
        # accounts. NOT "feed_exhausted" -- we did not reach the end, we gave
        # up. See _EMPTY_USER_PAGE_LIMIT.
        "empty_pages",
    }
)

#: How many consecutive account-less pages end a social-graph run.
#: LIVE-OBSERVED 2026-07-20: @X's `Following` returns one account and then
#: cursor-only pages indefinitely, each with a NEW cursor. The tweet loop's
#: rule -- only a non-advancing cursor means EOF (§17 G-cursor-eof) -- therefore
#: never fires here and burns the whole request budget (~250s) for one account.
#: Deliberately scoped to the User-returning ops, where this was actually
#: observed; the tweet loop is untouched.
_EMPTY_USER_PAGE_LIMIT = 3

#: Fallback per-run request budget when the caller doesn't set one (plan §9).
_DEFAULT_MAX_REQUESTS = 500


@dataclass
class RetrieveResult:
    tweets: list[model.Tweet]
    stop_reason: str
    requests_made: int
    since_target_crossed: bool = False
    raw_tweet_count: int = field(default=0, repr=False)


@dataclass
class RunState:
    """Mutable out-parameter ``paginate_iter`` fills in as it runs, since a
    generator can't also ``return`` a value the way a normal function can --
    read this only *after* the generator is exhausted."""

    stop_reason: str = "feed_exhausted"
    since_target_crossed: bool = False
    raw_tweet_count: int = 0
    any_yielded: bool = False


def _below_since(tweet: model.Tweet, since: datetime | None) -> bool:
    """True once a (non-pinned, dated) tweet has crossed below --since.

    A ``created_at=None`` tweet never participates in this comparison (plan
    §11) -- it neither triggers nor blocks the --since stop condition.
    """
    return since is not None and tweet.created_at is not None and tweet.created_at < since


def _above_until(tweet: model.Tweet, until: datetime | None) -> bool:
    """True if a (non-pinned, dated) tweet is newer than --until and should be
    skipped (not a stop trigger -- plan §11)."""
    return until is not None and tweet.created_at is not None and tweet.created_at > until


def paginate_iter(
    read_client,
    operation: str,
    query_id: str,
    features: dict,
    build_variables,
    *,
    field_toggles: dict | None = None,
    query_ids: dict | None = None,
    method: str = "GET",
    limit: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    max_requests: int | None = None,
    wait_on_limit: bool = False,
    max_wait: float | None = None,
    raw: bool = False,
    state: RunState,
) -> Iterator[model.Tweet]:
    """Drive one cursor-paginated read, yielding each ``Tweet`` as soon as it's
    parsed -- the streaming core both ``paginate()`` (materializes a list) and
    ``XScraper.iter_user_tweets`` (a true incremental generator, plan §5) build on.

    ``build_variables(cursor)`` builds the ``variables`` dict for the next
    request (``cursor=None`` for the first page). ``field_toggles`` is a
    third, op-specific query param some ops require (plan §8 -- found live;
    omitted entirely, not just empty, for ops that don't use it). ``method``
    selects the wire shape: "GET" for every op X's client reads with query
    params, "POST" for ``HomeTimeline``, which X's own client posts. Raises
    ``parse.EnvelopeParseError`` un-caught if the response envelope can't be
    located at all (plan §11: that is a structural failure, not an empty
    result, and callers map it to exit 4). ``state`` is filled in as a side
    effect -- only meaningful to read once this generator is exhausted.
    """
    seen_ids: set[str] = set()
    non_pinned_yielded = 0
    cursor: str | None = None
    budget = _DEFAULT_MAX_REQUESTS if max_requests is None else max_requests
    captured_at = datetime.now(UTC)
    send = read_client.post if method == "POST" else read_client.get

    while True:
        if read_client.requests_made >= budget:
            state.stop_reason = "max_requests"
            break

        try:
            body = send(query_id, operation, build_variables(cursor), features, field_toggles)
        except errors.RateLimitedError as exc:
            if wait_on_limit and exc.reset_at is not None:
                wait_seconds = max(0.0, exc.reset_at - time.time())
                if max_wait is not None:
                    wait_seconds = min(wait_seconds, max_wait)
                print(
                    f"scrape-x: waiting {int(wait_seconds)}s until rate-limit reset",
                    file=sys.stderr,
                )
                time.sleep(wait_seconds)
                continue
            state.stop_reason = "rate_limited"
            break

        raw_tweets, next_cursor = parse.walk_instructions(body, operation)
        state.raw_tweet_count += len(raw_tweets)

        stop_now = False
        for raw_tweet, is_pinned in raw_tweets:
            tweet = model.build_tweet(
                raw_tweet, is_pinned=is_pinned, captured_at=captured_at, raw=raw
            )
            if tweet is None or tweet.id in seen_ids:
                continue
            seen_ids.add(tweet.id)

            if is_pinned:
                # Always returned regardless of the window; never drives the
                # stop decision (plan §11).
                state.any_yielded = True
                yield tweet
                continue

            if _below_since(tweet, since):
                state.since_target_crossed = True
                state.stop_reason = "since_crossed"
                stop_now = True
                break

            if _above_until(tweet, until):
                continue  # newer than --until: skip, not a stop trigger

            state.any_yielded = True
            yield tweet
            non_pinned_yielded += 1

            if limit is not None and non_pinned_yielded >= limit:
                state.stop_reason = "limit_reached"
                stop_now = True
                break

        if stop_now:
            break

        # Non-advancing cursor (incl. no cursor at all) is the SOLE positive
        # EOF signal -- a thin/empty page with a NEW cursor means continue,
        # never stop (plan §8, §17 G-cursor-eof).
        if next_cursor is None or next_cursor == cursor:
            state.stop_reason = "feed_exhausted"
            break
        cursor = next_cursor

    if not state.any_yielded and state.stop_reason == "feed_exhausted":
        if operation == "SearchTimeline":
            state.stop_reason = "no_matches"
        else:
            # Pre-exit-4 soft-lock probe (plan §7, §11): a stale/soft-locked
            # session degrades to 200-empty rather than a clean 401, so an
            # empty-but-parsed result isn't necessarily "genuinely empty" --
            # confirm the session itself is still good before calling it that.
            status = session.check_session_status(
                read_client, query_ids or dict(queryids.DEFAULT_QUERY_IDS), features
            )
            if status is not session.Status.LOGGED_IN:
                state.stop_reason = "soft_locked"


def paginate(
    read_client,
    operation: str,
    query_id: str,
    features: dict,
    build_variables,
    *,
    field_toggles: dict | None = None,
    query_ids: dict | None = None,
    method: str = "GET",
    limit: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    max_requests: int | None = None,
    wait_on_limit: bool = False,
    max_wait: float | None = None,
    raw: bool = False,
) -> RetrieveResult:
    """Materialized form of ``paginate_iter`` -- drives pagination to completion
    and returns the full result at once (what ``fetch_user_tweets``/``search``/
    ``fetch_tweet`` use; plan §5's "fetch_* return materialized list[Tweet]").
    """
    state = RunState()
    tweets = list(
        paginate_iter(
            read_client,
            operation,
            query_id,
            features,
            build_variables,
            field_toggles=field_toggles,
            query_ids=query_ids,
            method=method,
            limit=limit,
            since=since,
            until=until,
            max_requests=max_requests,
            wait_on_limit=wait_on_limit,
            max_wait=max_wait,
            raw=raw,
            state=state,
        )
    )
    return RetrieveResult(
        tweets=tweets,
        stop_reason=state.stop_reason,
        requests_made=read_client.requests_made,
        since_target_crossed=state.since_target_crossed,
        raw_tweet_count=state.raw_tweet_count,
    )


def resolve_user_id(read_client, query_ids: dict, features: dict, kind: str, value: str) -> str:
    """Resolve a target to a numeric user id (plan §10).

    A no-op passthrough if ``kind`` is already ``"id"`` -- ``UserTweets``
    accepts a numeric id directly, no lookup needed. Otherwise resolves a
    screen_name via ``UserByScreenName`` and raises ``ProfileUnavailableError``
    if the account is suspended/protected/nonexistent (plan §11 exit 5).
    """
    if kind == "id":
        return value
    query_id = query_ids.get("UserByScreenName", queryids.DEFAULT_QUERY_IDS["UserByScreenName"])
    body = read_client.get(
        query_id, "UserByScreenName", gql.user_by_screen_name_variables(value), features
    )
    user_result = (((body or {}).get("data") or {}).get("user") or {}).get("result")
    if not isinstance(user_result, dict) or not user_result.get("rest_id"):
        raise errors.ProfileUnavailableError(
            f"@{value} is unavailable (suspended, protected, or does not exist)"
        )
    return str(user_result["rest_id"])


def _user_tweets_op(
    read_client, query_ids: dict, features: dict, kind: str, value: str, *, replies: bool
):
    """Shared setup for `fetch_user_tweets`/`iter_user_tweets`: resolve the
    target to a user id (raises `ProfileUnavailableError` eagerly if it
    can't be), then build the (operation, query_id, build_variables,
    field_toggles) `paginate`/`paginate_iter` need.

    The two variants are genuinely different ops with different variables and
    different `field_toggles` (plain `UserTweets` needs none). `replies=True`
    also crosses the transaction-id wall -- `client.ReadClient` mints the
    header for `UserTweetsAndReplies` automatically, so nothing here has to
    know about it.
    """
    user_id = resolve_user_id(read_client, query_ids, features, kind, value)
    operation = "UserTweetsAndReplies" if replies else "UserTweets"
    query_id = query_ids.get(operation, queryids.DEFAULT_QUERY_IDS[operation])
    field_toggles = gql.USER_TWEETS_AND_REPLIES_FIELD_TOGGLES if replies else None

    def build_variables(cursor: str | None) -> dict:
        return gql.user_tweets_variables(user_id, cursor=cursor, include_replies=replies)

    return operation, query_id, build_variables, field_toggles


def fetch_user_tweets(
    read_client,
    query_ids: dict,
    features: dict,
    identifier_kind: str,
    identifier_value: str,
    *,
    replies: bool = False,
    limit: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    max_requests: int | None = None,
    wait_on_limit: bool = False,
    max_wait: float | None = None,
    raw: bool = False,
) -> RetrieveResult:
    """A profile's tweets/replies/media, deep via cursor pagination (plan §1)."""
    operation, query_id, build_variables, field_toggles = _user_tweets_op(
        read_client, query_ids, features, identifier_kind, identifier_value, replies=replies
    )
    return paginate(
        read_client,
        operation,
        query_id,
        features,
        build_variables,
        field_toggles=field_toggles,
        query_ids=query_ids,
        limit=limit,
        since=since,
        until=until,
        max_requests=max_requests,
        wait_on_limit=wait_on_limit,
        max_wait=max_wait,
        raw=raw,
    )


def iter_user_tweets(
    read_client,
    query_ids: dict,
    features: dict,
    identifier_kind: str,
    identifier_value: str,
    *,
    replies: bool = False,
    limit: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    max_requests: int | None = None,
    wait_on_limit: bool = False,
    max_wait: float | None = None,
    raw: bool = False,
    state: RunState | None = None,
) -> Iterator[model.Tweet]:
    """Streaming form of `fetch_user_tweets` -- yields incrementally, one page's
    tweets at a time, rather than materializing the whole run first (plan §5:
    "iter_* yield incrementally"; matters for a deep `limit=1000`-style archival
    pull, where each page is its own network round trip + pacing delay).

    A plain function, not a generator itself, so the target-resolution
    `ProfileUnavailableError` (if any) raises immediately when called rather
    than being deferred to the first `next()` -- only the underlying page
    loop is lazy. Pass `state` to inspect `stop_reason`/etc. after the
    returned iterator is exhausted.
    """
    operation, query_id, build_variables, field_toggles = _user_tweets_op(
        read_client, query_ids, features, identifier_kind, identifier_value, replies=replies
    )
    if state is None:
        state = RunState()
    return paginate_iter(
        read_client,
        operation,
        query_id,
        features,
        build_variables,
        field_toggles=field_toggles,
        query_ids=query_ids,
        limit=limit,
        since=since,
        until=until,
        max_requests=max_requests,
        wait_on_limit=wait_on_limit,
        max_wait=max_wait,
        raw=raw,
        state=state,
    )


class _SingleBodyClient:
    """Replays one already-captured body through the normal pagination loop.

    Lets the browser-observe fallback reuse `paginate`'s parsing, dedup,
    since/until/limit filtering and pinned-tweet handling verbatim, instead of
    growing a second copy of that logic that could drift from the real one.
    """

    def __init__(self, body: dict) -> None:
        self._body = body
        self.requests_made = 0

    def get(self, query_id, operation, variables, features, field_toggles=None) -> dict:
        self.requests_made += 1
        return self._body

    post = get


def from_observed_body(
    body: dict,
    operation: str,
    *,
    limit: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    raw: bool = False,
) -> RetrieveResult:
    """Turn ONE browser-observed response body into a `RetrieveResult`.

    The result carries `stop_reason="browser_observed"` unless a filter stopped
    it first -- callers must surface that this is a single page, not a
    completed run.
    """
    result = paginate(
        _SingleBodyClient(body),
        operation,
        "",
        {},
        lambda cursor: {},
        max_requests=1,
        limit=limit,
        since=since,
        until=until,
        raw=raw,
    )
    # "soft_locked" is included deliberately: an empty page sends `paginate`
    # into its soft-lock probe, which re-reads this same body and can conclude
    # the session is dead. It isn't -- the browser just captured a live
    # response -- so that verdict must not survive.
    if result.stop_reason in ("max_requests", "feed_exhausted", "soft_locked"):
        result.stop_reason = "browser_observed"
    return result


def fetch_home(
    read_client,
    query_ids: dict,
    features: dict,
    *,
    limit: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    max_requests: int | None = None,
    wait_on_limit: bool = False,
    max_wait: float | None = None,
    raw: bool = False,
) -> RetrieveResult:
    """The logged-in account's home feed (`HomeTimeline`).

    Needs no `x-client-transaction-id` -- live-verified 2026-07-20 that the
    home feed is NOT behind the wall that gates `SearchTimeline`/
    `UserTweetsAndReplies`. Takes no target: the feed is a property of the
    logged-in session itself, which is why this is the one read with no
    identifier argument.

    Sent as a POST to match X's own client (`client.ReadClient.post`).
    """
    operation = "HomeTimeline"
    query_id = query_ids.get(operation, queryids.DEFAULT_QUERY_IDS[operation])

    def build_variables(cursor: str | None) -> dict:
        return gql.home_timeline_variables(cursor=cursor)

    return paginate(
        read_client,
        operation,
        query_id,
        features,
        build_variables,
        query_ids=query_ids,
        method="POST",
        limit=limit,
        since=since,
        until=until,
        max_requests=max_requests,
        wait_on_limit=wait_on_limit,
        max_wait=max_wait,
        raw=raw,
    )


def search(
    read_client,
    query_ids: dict,
    features: dict,
    query: str,
    *,
    product: str = "Latest",
    limit: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    max_requests: int | None = None,
    wait_on_limit: bool = False,
    max_wait: float | None = None,
    raw: bool = False,
) -> RetrieveResult:
    """Tweets matching a query / advanced operators (plan §1).

    `product` is "Latest" (reverse-chronological) or "Top" (X's ranking).

    `SearchTimeline` is behind the transaction-id wall; `client.ReadClient`
    mints the header for it automatically, so this reads like any other op.
    """
    operation = "SearchTimeline"
    query_id = query_ids.get(operation, queryids.DEFAULT_QUERY_IDS[operation])

    def build_variables(cursor: str | None) -> dict:
        return gql.search_timeline_variables(query, cursor=cursor, product=product)

    return paginate(
        read_client,
        operation,
        query_id,
        features,
        build_variables,
        query_ids=query_ids,
        limit=limit,
        since=since,
        until=until,
        max_requests=max_requests,
        wait_on_limit=wait_on_limit,
        max_wait=max_wait,
        raw=raw,
    )


def fetch_tweet(
    read_client,
    query_ids: dict,
    features: dict,
    tweet_id: str,
    *,
    replies: bool = False,
    max_requests: int | None = None,
    wait_on_limit: bool = False,
    max_wait: float | None = None,
    raw: bool = False,
) -> RetrieveResult:
    """One tweet plus its reply/conversation thread (plan §1).

    ``replies=False`` (the CLI default, matching the ``[--replies]`` flag
    being opt-in) fetches just the first page and filters the result down to
    the focal tweet alone -- ``TweetDetail`` doesn't have a separate
    "no replies" operation the way ``UserTweets``/``UserTweetsAndReplies`` do,
    it always returns the surrounding thread in the same envelope, so
    ``replies=True`` paginates that thread fully instead of filtering it out.
    """
    query_id = query_ids.get("TweetDetail", queryids.DEFAULT_QUERY_IDS["TweetDetail"])

    def build_variables(cursor: str | None) -> dict:
        return gql.tweet_detail_variables(tweet_id, cursor=cursor)

    result = paginate(
        read_client,
        "TweetDetail",
        query_id,
        features,
        build_variables,
        field_toggles=gql.TWEET_DETAIL_FIELD_TOGGLES,
        query_ids=query_ids,
        max_requests=1 if not replies else max_requests,
        wait_on_limit=wait_on_limit,
        max_wait=max_wait,
        raw=raw,
    )
    if not replies:
        result.tweets = [t for t in result.tweets if t.id == tweet_id]

    if not result.tweets and result.stop_reason in ("feed_exhausted", "max_requests"):
        raise errors.NotFoundError(f"tweet {tweet_id} not found (deleted, or thread unavailable)")
    return result


@dataclass
class UserResult:
    """A social-graph run's output. Parallel to `RetrieveResult`, not a variant
    of it: these ops return `User` objects, and folding them into a field named
    `tweets` would make the type lie."""

    users: list[model.User]
    stop_reason: str
    requests_made: int


def paginate_users(
    read_client,
    operation: str,
    query_id: str,
    features: dict,
    build_variables,
    *,
    limit: int | None = None,
    max_requests: int | None = None,
    wait_on_limit: bool = False,
    max_wait: float | None = None,
) -> UserResult:
    """Cursor-paginate a User-returning op.

    A deliberate sibling of `paginate` rather than a parameter on it: the two
    differ in parse function, element type, and stop conditions (there is no
    since/until on a follower list, and no pinned entry), so sharing would be
    a branch on every line rather than reuse.
    """
    seen_ids: set[str] = set()
    users: list[model.User] = []
    cursor: str | None = None
    budget = _DEFAULT_MAX_REQUESTS if max_requests is None else max_requests
    stop_reason = "feed_exhausted"
    empty_pages = 0

    while True:
        if read_client.requests_made >= budget:
            stop_reason = "max_requests"
            break

        try:
            body = read_client.get(query_id, operation, build_variables(cursor), features)
        except errors.RateLimitedError as exc:
            if wait_on_limit and exc.reset_at is not None:
                wait_seconds = max(0.0, exc.reset_at - time.time())
                if max_wait is not None:
                    wait_seconds = min(wait_seconds, max_wait)
                print(
                    f"scrape-x: waiting {int(wait_seconds)}s until rate-limit reset",
                    file=sys.stderr,
                )
                time.sleep(wait_seconds)
                continue
            stop_reason = "rate_limited"
            break

        raw_users, next_cursor = parse.walk_user_instructions(body, operation)

        stop_now = False
        added = 0
        for raw_user in raw_users:
            user = model.build_user(raw_user)
            if user is None or user.id in seen_ids:
                continue
            seen_ids.add(user.id)
            users.append(user)
            added += 1
            if limit is not None and len(users) >= limit:
                stop_reason = "limit_reached"
                stop_now = True
                break

        if stop_now:
            break

        empty_pages = 0 if added else empty_pages + 1
        if empty_pages >= _EMPTY_USER_PAGE_LIMIT:
            stop_reason = "empty_pages"
            break

        # Otherwise the same EOF rule as the tweet loop: only a non-advancing
        # cursor ends it.
        if next_cursor is None or next_cursor == cursor:
            stop_reason = "feed_exhausted"
            break
        cursor = next_cursor

    return UserResult(users=users, stop_reason=stop_reason, requests_made=read_client.requests_made)


def fetch_social_graph(
    read_client,
    query_ids: dict,
    features: dict,
    operation: str,
    identifier_kind: str,
    identifier_value: str,
    *,
    limit: int | None = None,
    max_requests: int | None = None,
    wait_on_limit: bool = False,
    max_wait: float | None = None,
) -> UserResult:
    """Who follows / is followed by a user, or who retweeted a tweet.

    `Following` and `Retweeters` need no transaction id; `Followers` does and
    gets one automatically from `client.ReadClient`. `Favoriters` (likers) is
    deliberately absent: probed live 2026-07-20, X no longer exposes a likers
    list at all -- /likes redirects to the tweet, and the op name appears in
    none of its 685 JS chunks. Quoters are reachable through `search` with a
    `quoted_tweet_id:` query, which is what X's own /quotes tab does.
    """
    if operation == "Retweeters":
        target_id = identifier_value
    else:
        target_id = resolve_user_id(
            read_client, query_ids, features, identifier_kind, identifier_value
        )
    query_id = query_ids.get(operation, queryids.DEFAULT_QUERY_IDS[operation])

    def build_variables(cursor: str | None) -> dict:
        return gql.social_graph_variables(target_id, operation=operation, cursor=cursor)

    return paginate_users(
        read_client,
        operation,
        query_id,
        features,
        build_variables,
        limit=limit,
        max_requests=max_requests,
        wait_on_limit=wait_on_limit,
        max_wait=max_wait,
    )
