"""Read client: fires X's internal GraphQL reads over ``httpx`` (plan §8).

The proven, live-tested request shape only -- no retry/backoff logic. Pacing and
the request budget stop-reason decision belong to ``retrieve.py``; this module
just executes one read and reports what happened.

Most ops need no ``x-client-transaction-id``. The three that do
(``transaction.GATED_OPS``) get a freshly minted one per request, injected here;
every other op must NOT send the header (see ``transaction`` for why).
"""

from __future__ import annotations

import json
import time
from urllib.parse import urlsplit

import httpx

from . import config, errors, gql, transaction


class ReadClient:
    """A paced ``httpx.Client`` wrapper for X's GraphQL read endpoints."""

    def __init__(
        self,
        auth_token: str,
        ct0: str,
        user_agent: str,
        *,
        min_pause: float | None = None,
        max_requests: int | None = None,
    ) -> None:
        if min_pause is None:
            self.min_pause = config.clamp_request_pause(config.DEFAULT_HUMAN_PAUSE[0])
        else:
            self.min_pause = config.clamp_request_pause(min_pause)
        self.max_requests = max_requests

        self.requests_made = 0
        self.last_rate_limit_remaining: int | None = None
        self.last_rate_limit_reset: int | None = None

        # Built lazily on the first gated op, so a run that only touches
        # ungated ops never pays for the extra page fetches.
        self._transaction = transaction.ClientTransaction(auth_token, ct0, user_agent)

        self._client = httpx.Client(
            headers={
                "authorization": f"Bearer {gql.BEARER_TOKEN}",
                "cookie": f"auth_token={auth_token}; ct0={ct0}",
                "x-csrf-token": ct0,  # must equal the ct0 cookie value or X 403s (§17 G-ct0-csrf)
                "x-twitter-auth-type": "OAuth2Session",
                "x-twitter-active-user": "yes",
                "x-twitter-client-language": "en",
                "user-agent": user_agent,
                # x-client-transaction-id is deliberately NOT set here: it is
                # per-request and single-use, so it is injected per call in
                # _txid_header() for gated ops only -- never as a shared default.
            },
        )

    def _txid_header(self, operation: str, url: str, method: str) -> dict[str, str]:
        """A freshly minted transaction-id header, or ``{}`` for ungated ops.

        The id signs the request path, so it must be minted per call and never
        reused -- X spends it on first use.
        """
        if operation not in transaction.GATED_OPS:
            return {}
        return {"x-client-transaction-id": self._transaction.generate(method, urlsplit(url).path)}

    def get(
        self,
        query_id: str,
        operation: str,
        variables: dict,
        features: dict,
        field_toggles: dict | None = None,
    ) -> dict:
        """Fire one GraphQL read and return the parsed JSON body.

        ``field_toggles`` is a third query param some ops require alongside
        ``variables``/``features`` (found live 2026-07-05 -- omitting it for
        an op that needs it causes a 404, the same failure mode as a wrong
        query-id); omitted entirely (not sent as `{}`) when ``None``, since
        some ops (e.g. ``SearchTimeline``) are never sent one at all.
        """
        if self.requests_made > 0:
            time.sleep(self.min_pause)

        url = gql.build_url(query_id, operation)
        params = {"variables": json.dumps(variables), "features": json.dumps(features)}
        if field_toggles is not None:
            params["fieldToggles"] = json.dumps(field_toggles)
        response = self._client.get(
            url, params=params, headers=self._txid_header(operation, url, "GET")
        )
        self.requests_made += 1
        return self._handle(response, operation)

    def post(
        self,
        query_id: str,
        operation: str,
        variables: dict,
        features: dict,
        field_toggles: dict | None = None,
    ) -> dict:
        """Fire one GraphQL read as a POST and return the parsed JSON body.

        Same contract as ``get()``, different wire shape: the payload travels
        as a JSON body (with ``queryId`` repeated inside it) rather than as
        query params. ``HomeTimeline`` is the op that needs this -- GET works
        for it today (verified live 2026-07-20), but X's own web client sends
        a POST, and matching the real client is the durable choice for an op
        that is not currently walled.
        """
        if self.requests_made > 0:
            time.sleep(self.min_pause)

        url = gql.build_url(query_id, operation)
        payload: dict = {"variables": variables, "features": features, "queryId": query_id}
        if field_toggles is not None:
            payload["fieldToggles"] = field_toggles
        response = self._client.post(
            url, json=payload, headers=self._txid_header(operation, url, "POST")
        )
        self.requests_made += 1
        return self._handle(response, operation)

    def _handle(self, response: httpx.Response, operation: str) -> dict:
        """Shared post-request handling for ``get()``/``post()``: record the
        rate-limit headers, map the failure statuses onto typed errors, and
        catch X's 200-but-logged-out shape."""
        remaining = response.headers.get("x-rate-limit-remaining")
        self.last_rate_limit_remaining = int(remaining) if remaining is not None else None
        reset = response.headers.get("x-rate-limit-reset")
        self.last_rate_limit_reset = int(reset) if reset is not None else None

        if response.status_code == 429:
            reset_at = int(response.headers.get("x-rate-limit-reset", 0)) or None
            raise errors.RateLimitedError(reset_at=reset_at)
        if response.status_code == 401:
            raise errors.SessionExpiredError()
        if response.status_code != 200:
            if operation in transaction.GATED_OPS:
                # A header WAS minted (generation failure raises earlier) and X
                # still refused -- the generator has rotted. Typed separately so
                # callers can fall back to browser-observe (plan §4).
                raise errors.GatedOpRejectedError(
                    f"{operation} rejected a generated x-client-transaction-id "
                    f"(HTTP {response.status_code})"
                )
            raise errors.AgenticXError(f"unexpected status {response.status_code} for {operation}")
        body = response.json()
        if _has_auth_error(body):
            raise errors.SessionExpiredError()
        return body

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ReadClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _has_auth_error(body: object) -> bool:
    """Detect X's logged-out error shape on an otherwise-200 response.

    X reports a stale/soft-locked session as HTTP 200 with a top-level
    ``errors`` array containing ``{"code": 32, "message": "Could not
    authenticate you."}`` -- checking the parsed error code (rather than a raw
    substring scan of the response text) avoids a false positive on ordinary
    tweet content that happens to contain a matching string.
    """
    if not isinstance(body, dict):
        return False
    error_list = body.get("errors")
    if not isinstance(error_list, list):
        return False
    return any(isinstance(item, dict) and item.get("code") == 32 for item in error_list)
