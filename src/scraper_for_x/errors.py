"""Typed errors so library callers can branch on failure mode instead of parsing messages."""

from __future__ import annotations


class ScraperForXError(Exception):
    """Base class for every error this package raises."""


class LoginRequiredError(ScraperForXError):
    """No persisted session exists for this profile.

    Fix: ``scrape-x login``.
    """


class SessionExpiredError(ScraperForXError):
    """A persisted session exists but X now rejects it or has soft-locked it.

    Covers both the explicit case (401 / logged-out marker) and the silent case
    (X returns HTTP 200 with an empty/limited timeline for a stale session).
    Fix: ``scrape-x login``.
    """


class RateLimitedError(ScraperForXError):
    """The request hit X's 429 rate limit.

    Carries ``reset_at``, the unix epoch from the ``x-rate-limit-reset`` header,
    so callers can decide how long to wait before retrying.
    """

    def __init__(self, *args: object, reset_at: int | None = None) -> None:
        super().__init__(*args)
        self.reset_at = reset_at


class ProfileUnavailableError(ScraperForXError):
    """The target user is suspended, protected, or does not exist.

    Distinct from :class:`NotFoundError`, which is about tweets, not users.
    """


class NotFoundError(ScraperForXError):
    """The target tweet or thread does not exist.

    Distinct from :class:`ProfileUnavailableError`, which is about users, not
    tweets.
    """


class InvalidCookieError(ScraperForXError, ValueError):
    """A cookie-import value did not match the expected ``auth_token``/``ct0`` shape."""


class InvalidIdentifierError(ScraperForXError, ValueError):
    """The username/id/URL identifier failed normalize-then-validate."""


class NotEnteredError(ScraperForXError):
    """A read was attempted on an ``XScraper`` instance that was never entered.

    Reads require the context to be open. Use ``with XScraper(...) as x:`` before
    calling any ``fetch_*``/``iter_*``/``search`` method.
    """


class SessionClosedError(ScraperForXError):
    """An ``iter_*`` generator was advanced after its owning ``with`` block exited.

    The generator drives the read client; it can only make progress while the
    session is open. Consume it inside the ``with XScraper(...) as x:`` block.
    """
