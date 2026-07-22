"""Typed errors so library callers can branch on failure mode instead of parsing messages."""

from __future__ import annotations


class AgenticXError(Exception):
    """Base class for every error this package raises."""


class LoginRequiredError(AgenticXError):
    """No persisted session exists for this profile.

    Fix: ``agentic-x login``.
    """


class SessionExpiredError(AgenticXError):
    """A persisted session exists but X now rejects it or has soft-locked it.

    Covers both the explicit case (401 / logged-out marker) and the silent case
    (X returns HTTP 200 with an empty/limited timeline for a stale session).
    Fix: ``agentic-x login``.
    """


class RateLimitedError(AgenticXError):
    """The request hit X's 429 rate limit.

    Carries ``reset_at``, the unix epoch from the ``x-rate-limit-reset`` header,
    so callers can decide how long to wait before retrying.
    """

    def __init__(self, *args: object, reset_at: int | None = None) -> None:
        super().__init__(*args)
        self.reset_at = reset_at


class ProfileUnavailableError(AgenticXError):
    """The target user is suspended, protected, or does not exist.

    Distinct from :class:`NotFoundError`, which is about tweets, not users.
    """


class NotFoundError(AgenticXError):
    """The target tweet or thread does not exist.

    Distinct from :class:`ProfileUnavailableError`, which is about users, not
    tweets.
    """


class InvalidCookieError(AgenticXError, ValueError):
    """A cookie-import value did not match the expected ``auth_token``/``ct0`` shape."""


class InvalidIdentifierError(AgenticXError, ValueError):
    """The username/id/URL identifier failed normalize-then-validate."""


class NotEnteredError(AgenticXError):
    """A read was attempted on an ``XScraper`` instance that was never entered.

    Reads require the context to be open. Use ``with XScraper(...) as x:`` before
    calling any ``fetch_*``/``iter_*``/``search`` method.
    """


class SessionClosedError(AgenticXError):
    """An ``iter_*`` generator was advanced after its owning ``with`` block exited.

    The generator drives the read client; it can only make progress while the
    session is open. Consume it inside the ``with XScraper(...) as x:`` block.
    """


class TransactionIdError(AgenticXError):
    """A fresh ``x-client-transaction-id`` could not be generated.

    The three ops behind X's transaction-id wall (``SearchTimeline``,
    ``UserTweetsAndReplies``, ``Followers``) need a freshly minted header on
    every request. Minting it means reproducing an obfuscated client-side
    algorithm from x.com's own page -- the one reverse-engineered, rot-prone
    part of this package. This is raised when an ingredient that algorithm
    needs is no longer served, i.e. X changed it.

    Distinct from a 404 on a gated op *after* a header was generated: that
    means the id was minted but rejected. See ``transaction.py``.
    """


class GatedOpRejectedError(AgenticXError):
    """A transaction-id-gated op rejected the request despite a minted header.

    This is the specific signal that the reverse-engineered generator has
    rotted: the header was produced (a generation failure would have raised
    :class:`TransactionIdError` first) and X still refused. It is the trigger
    for the browser-observe fallback, not a reason to re-port the generator
    on the spot.
    """


class BrowserFallbackError(AgenticXError):
    """The browser-observe fallback could not produce a response.

    Covers the ``[browser]`` extra being absent, an op with no known page to
    drive, and a page that loaded but never fired the operation (usually a
    browser profile that is logged out even though the stored cookies are not).
    """


class FeatureNotImplementedError(AgenticXError):
    """No shipped operation raises this any more; retained for compatibility.

    Through v0.2.0 ``search()`` and ``fetch_user_tweets(replies=True)``/
    ``iter_user_tweets(replies=True)`` raised this, because X's transaction-id
    header is single-use and could not be harvested-and-replayed the way
    session cookies and query-ids are. v0.3.0 generates the header per request
    instead (see ``transaction.py``), so all three now work.

    Kept exported so ``except FeatureNotImplementedError`` in existing callers
    keeps importing; it is simply never raised. It will be removed in the next
    major version.
    """
