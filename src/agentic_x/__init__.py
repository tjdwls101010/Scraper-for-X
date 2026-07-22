"""Public Python API (plan §5). See README.md for the full picture.

MUST NOT eagerly import scrapling (plan §14 G-lazy-import) -- everything
imported here (``session``, ``auth``, ``client``, ``retrieve``, ``model``,
``errors``) is base-install-safe; the scrapling-touching code lives inside
``session.run_login``/``run_setup`` and is only reached when those are
actually called.
"""

from __future__ import annotations

__version__ = "0.4.0"

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

from . import auth
from . import client as client_module
from . import retrieve as retrieve_module
from . import session as session_module
from .config import DEFAULT_PROFILE_NAME
from .errors import (
    AgenticXError,
    BrowserFallbackError,
    FeatureNotImplementedError,
    GatedOpRejectedError,
    InvalidCookieError,
    InvalidIdentifierError,
    LoginRequiredError,
    NotEnteredError,
    NotFoundError,
    ProfileUnavailableError,
    RateLimitedError,
    SessionClosedError,
    SessionExpiredError,
    TransactionIdError,
)
from .model import Media, Tweet, User
from .parse import EnvelopeParseError
from .retrieve import RetrieveResult
from .session import Status

__all__ = [
    "XScraper",
    "Tweet",
    "User",
    "Media",
    "Status",
    "RetrieveResult",
    "AgenticXError",
    "LoginRequiredError",
    "SessionExpiredError",
    "RateLimitedError",
    "ProfileUnavailableError",
    "NotFoundError",
    "InvalidCookieError",
    "InvalidIdentifierError",
    "NotEnteredError",
    "SessionClosedError",
    "EnvelopeParseError",
    "FeatureNotImplementedError",
    "GatedOpRejectedError",
    "BrowserFallbackError",
    "TransactionIdError",
]


def _to_date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)  # strict YYYY-MM-DD


def _parse_since(value: str | date | None) -> datetime | None:
    """--since D stops once a tweet is from *before* D (plan §11) -- D itself
    is the inclusive boundary, so compare against the START of day D."""
    if value is None:
        return None
    day = _to_date(value)
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _parse_until(value: str | date | None) -> datetime | None:
    """--until D skips tweets *newer than* D (plan §11) -- D itself is
    included, so compare against the END of day D, not its start (otherwise
    every tweet from D itself would be wrongly skipped as "newer than D")."""
    if value is None:
        return None
    day = _to_date(value)
    return datetime(day.year, day.month, day.day, 23, 59, 59, 999999, tzinfo=UTC)


class XScraper:
    """Read-only X/Twitter client: harvest-then-replay hybrid (plan §3).

    A stealth-browser login (or cookie import) harvests a session once; all
    reads afterward go over `httpx`. Most ops need no `x-client-transaction-id`;
    the three that do get one generated per request (see `transaction.py`).
    See DISCLAIMER.md first.
    """

    def __init__(
        self,
        profile: str = DEFAULT_PROFILE_NAME,
        *,
        profile_dir: str | Path | None = None,
        min_request_pause: float | None = None,
        max_requests: int | None = None,
    ) -> None:
        self.profile = profile
        self._profile_dir_override = str(profile_dir) if profile_dir is not None else None
        self._min_request_pause = min_request_pause
        self._max_requests = max_requests
        self.last_result: RetrieveResult | None = None
        #: Set by the social-graph reads, which return Users rather than
        #: Tweets and so cannot share `last_result`.
        self.last_user_result: retrieve_module.UserResult | None = None

        self._entered = False
        self._closed = False
        self._read_client: client_module.ReadClient | None = None
        self._query_ids: dict | None = None
        self._features: dict | None = None

    def __enter__(self) -> XScraper:
        credential = auth.load_session(
            self.profile, profile_dir_override=self._profile_dir_override
        )
        self._query_ids, self._features = session_module.query_ids_for(credential)
        self._read_client = client_module.ReadClient(
            credential.auth_token,
            credential.ct0,
            credential.user_agent,
            min_pause=self._min_request_pause,
            max_requests=self._max_requests,
        )
        self._entered = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._read_client is not None:
            self._read_client.close()
        self._closed = True

    def _require_entered(self) -> None:
        if self._closed:
            raise SessionClosedError("XScraper is closed; use it inside a fresh `with` block")
        if not self._entered:
            raise NotEnteredError("use `with XScraper(...) as x:` for reads")

    # --- session setup: side-effecting persisters (plan §5) -----------------

    def login(self) -> bool:
        """Headed stealth-browser login (requires the `[browser]` extra)."""
        return session_module.run_login(
            self.profile, profile_dir_override=self._profile_dir_override
        )

    @classmethod
    def from_cookies(
        cls,
        *,
        auth_token: str,
        ct0: str,
        profile: str = DEFAULT_PROFILE_NAME,
        profile_dir: str | Path | None = None,
    ) -> XScraper:
        """Import an already-established session's cookies directly (plan §7).
        No browser required."""
        auth.validate_token_shapes(auth_token, ct0)
        credential = auth.SessionCredential(
            auth_token=auth_token, ct0=ct0, user_agent=auth.DEFAULT_USER_AGENT
        )
        auth.save_session(
            profile, credential, profile_dir_override=str(profile_dir) if profile_dir else None
        )
        return cls(profile=profile, profile_dir=profile_dir)

    @classmethod
    def from_cookie_file(
        cls,
        path: str | Path,
        profile: str = DEFAULT_PROFILE_NAME,
        *,
        profile_dir: str | Path | None = None,
    ) -> XScraper:
        """Import a Netscape/JSON/cURL cookie export (plan §7). No browser required."""
        auth.from_cookie_file(
            Path(path),
            profile,
            profile_dir_override=str(profile_dir) if profile_dir else None,
        )
        return cls(profile=profile, profile_dir=profile_dir)

    def status(self) -> Status:
        return session_module.run_status(
            self.profile, profile_dir_override=self._profile_dir_override
        )

    # --- reads: require an entered context (plan §5) ------------------------

    def fetch_user_tweets(
        self,
        identifier: str,
        *,
        replies: bool = False,
        limit: int | None = None,
        since: str | date | None = None,
        until: str | date | None = None,
        by: str | None = None,
        wait_on_limit: bool = False,
        max_wait: float | None = None,
        raw: bool = False,
    ) -> list[Tweet]:
        """A profile's tweets/replies/media (plan §1)."""
        self._require_entered()
        kind, value = auth.normalize_identifier(identifier, by=by)
        result = retrieve_module.fetch_user_tweets(
            self._read_client,
            self._query_ids,
            self._features,
            kind,
            value,
            replies=replies,
            limit=limit,
            since=_parse_since(since),
            until=_parse_until(until),
            wait_on_limit=wait_on_limit,
            max_wait=max_wait,
            raw=raw,
        )
        self.last_result = result
        return result.tweets

    def iter_user_tweets(
        self,
        identifier: str,
        *,
        replies: bool = False,
        limit: int | None = None,
        since: str | date | None = None,
        until: str | date | None = None,
        by: str | None = None,
        wait_on_limit: bool = False,
        max_wait: float | None = None,
        raw: bool = False,
    ) -> Iterator[Tweet]:
        """Streaming form of `fetch_user_tweets` (plan §5) -- yields incrementally.

        Must be consumed inside the owning `with` block; advancing it after
        the block exited raises `SessionClosedError`. Since this is a
        generator, that check can't run at call time -- only advancing it does
        (calling `iter_user_tweets()` on an already-closed instance never
        raises by itself; the first `next()` does).
        """
        if self._closed:
            raise SessionClosedError(
                "iter_user_tweets() was advanced after its `with` block exited"
            )
        self._require_entered()
        kind, value = auth.normalize_identifier(identifier, by=by)
        state = retrieve_module.RunState()
        stream = retrieve_module.iter_user_tweets(
            self._read_client,
            self._query_ids,
            self._features,
            kind,
            value,
            replies=replies,
            limit=limit,
            since=_parse_since(since),
            until=_parse_until(until),
            wait_on_limit=wait_on_limit,
            max_wait=max_wait,
            raw=raw,
            state=state,
        )
        for tweet in stream:
            if self._closed:
                raise SessionClosedError(
                    "iter_user_tweets() was advanced after its `with` block exited"
                )
            yield tweet

    def fetch_home(
        self,
        *,
        limit: int | None = None,
        since: str | date | None = None,
        until: str | date | None = None,
        wait_on_limit: bool = False,
        max_wait: float | None = None,
        raw: bool = False,
    ) -> list[Tweet]:
        """The logged-in account's home feed (plan §1).

        Takes no identifier -- the feed belongs to the session itself.
        """
        self._require_entered()
        result = retrieve_module.fetch_home(
            self._read_client,
            self._query_ids,
            self._features,
            limit=limit,
            since=_parse_since(since),
            until=_parse_until(until),
            wait_on_limit=wait_on_limit,
            max_wait=max_wait,
            raw=raw,
        )
        self.last_result = result
        return result.tweets

    def _social_graph(
        self,
        operation: str,
        identifier: str,
        *,
        by: str | None,
        limit: int | None,
        wait_on_limit: bool,
        max_wait: float | None,
    ) -> list[User]:
        self._require_entered()
        if operation == "Retweeters":
            kind, value = "id", auth.normalize_tweet_identifier(identifier)
        else:
            kind, value = auth.normalize_identifier(identifier, by=by)
        result = retrieve_module.fetch_social_graph(
            self._read_client,
            self._query_ids,
            self._features,
            operation,
            kind,
            value,
            limit=limit,
            wait_on_limit=wait_on_limit,
            max_wait=max_wait,
        )
        self.last_user_result = result
        return result.users

    def fetch_following(
        self,
        identifier: str,
        *,
        by: str | None = None,
        limit: int | None = None,
        wait_on_limit: bool = False,
        max_wait: float | None = None,
    ) -> list[User]:
        """Accounts a user follows. Returns `User`, not `Tweet`."""
        return self._social_graph(
            "Following",
            identifier,
            by=by,
            limit=limit,
            wait_on_limit=wait_on_limit,
            max_wait=max_wait,
        )

    def fetch_followers(
        self,
        identifier: str,
        *,
        by: str | None = None,
        limit: int | None = None,
        wait_on_limit: bool = False,
        max_wait: float | None = None,
    ) -> list[User]:
        """Accounts following a user. Needs a generated transaction id."""
        return self._social_graph(
            "Followers",
            identifier,
            by=by,
            limit=limit,
            wait_on_limit=wait_on_limit,
            max_wait=max_wait,
        )

    def fetch_retweeters(
        self,
        identifier: str,
        *,
        limit: int | None = None,
        wait_on_limit: bool = False,
        max_wait: float | None = None,
    ) -> list[User]:
        """Accounts that retweeted a tweet.

        There is no `fetch_likers`: X no longer exposes a likers list at all
        (probed live 2026-07-20). For quoters, use
        `search("quoted_tweet_id:<id>")` -- which is what X's own /quotes tab
        does.
        """
        return self._social_graph(
            "Retweeters",
            identifier,
            by=None,
            limit=limit,
            wait_on_limit=wait_on_limit,
            max_wait=max_wait,
        )

    def search(
        self,
        query: str,
        *,
        product: str = "Latest",
        limit: int | None = None,
        since: str | date | None = None,
        until: str | date | None = None,
        wait_on_limit: bool = False,
        max_wait: float | None = None,
        raw: bool = False,
    ) -> list[Tweet]:
        """Tweets matching a query / advanced operators (plan §1)."""
        self._require_entered()
        result = retrieve_module.search(
            self._read_client,
            self._query_ids,
            self._features,
            query,
            product=product,
            limit=limit,
            since=_parse_since(since),
            until=_parse_until(until),
            wait_on_limit=wait_on_limit,
            max_wait=max_wait,
            raw=raw,
        )
        self.last_result = result
        return result.tweets

    def fetch_tweet(
        self,
        identifier: str,
        *,
        replies: bool = False,
        wait_on_limit: bool = False,
        max_wait: float | None = None,
        raw: bool = False,
    ) -> list[Tweet]:
        """One tweet plus its reply/conversation thread (plan §1)."""
        self._require_entered()
        value = auth.normalize_tweet_identifier(identifier)
        result = retrieve_module.fetch_tweet(
            self._read_client,
            self._query_ids,
            self._features,
            value,
            replies=replies,
            wait_on_limit=wait_on_limit,
            max_wait=max_wait,
            raw=raw,
        )
        self.last_result = result
        return result.tweets
