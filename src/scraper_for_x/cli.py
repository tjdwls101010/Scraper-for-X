"""``scrape-x`` command-line entry point (plan §10)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from . import __version__, auth, client, redact, retrieve, session
from .config import DEFAULT_PROFILE_NAME, default_output_dir
from .errors import (
    InvalidCookieError,
    InvalidIdentifierError,
    LoginRequiredError,
    NotFoundError,
    ProfileUnavailableError,
    RateLimitedError,
    SessionExpiredError,
)
from .model import Tweet
from .parse import EnvelopeParseError


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from None


def _since_datetime(value: date | None) -> datetime | None:
    """--since D stops once a tweet is from *before* D (plan §11) -- D itself
    is the inclusive boundary, so compare against the START of day D."""
    if value is None:
        return None
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def _until_datetime(value: date | None) -> datetime | None:
    """--until D skips tweets *newer than* D (plan §11) -- D itself is
    included, so compare against the END of day D, not its start (otherwise
    every tweet from D itself would be wrongly skipped as "newer than D")."""
    if value is None:
        return None
    return datetime(value.year, value.month, value.day, 23, 59, 59, 999999, tzinfo=UTC)


class _ArgumentParser(argparse.ArgumentParser):
    """Usage errors exit 1, not argparse's default 2 -- 2 already means
    "login required/expired/soft-locked" in this CLI's exit-code contract
    (plan §10); conflating the two would make a scripted exit-code check lie.
    """

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def _add_common_fetch_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=["json", "ndjson"], default="json")
    parser.add_argument("--output", default=None)
    parser.add_argument("--wait-on-limit", action="store_true")
    parser.add_argument("--max-wait", type=float, default=None)
    parser.add_argument("--profile", default=DEFAULT_PROFILE_NAME)
    parser.add_argument("--profile-dir", default=None)
    parser.add_argument("--raw", action="store_true")
    parser.add_argument(
        "--no-redact",
        action="store_true",
        help="Disable PII scrubbing on --raw output (prints an on-screen warning).",
    )
    parser.add_argument("-v", "--verbose", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="scrape-x", description="Read-only X/Twitter scraper.")
    parser.add_argument("--version", action="version", version=f"scrape-x {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login_p = subparsers.add_parser(
        "login", help="Log in (headed stealth browser by default, or --cookies)."
    )
    login_p.add_argument("--profile", default=DEFAULT_PROFILE_NAME)
    login_p.add_argument("--profile-dir", default=None)
    login_p.add_argument(
        "--cookies",
        default=None,
        help="Import a Netscape/JSON/cURL cookie export instead of opening a browser.",
    )

    status_p = subparsers.add_parser("status", help="Check whether a profile is logged in.")
    status_p.add_argument("--profile", default=DEFAULT_PROFILE_NAME)
    status_p.add_argument("--profile-dir", default=None)
    status_p.add_argument("--json", action="store_true")

    setup_p = subparsers.add_parser(
        "setup", help="Provision the login browser into an isolated cache."
    )
    setup_p.add_argument(
        "--force", action="store_true", help="Reinstall even if already provisioned."
    )

    doctor_p = subparsers.add_parser(
        "doctor", help="Authenticated round-trip + query-id freshness check."
    )
    doctor_p.add_argument("--profile", default=DEFAULT_PROFILE_NAME)
    doctor_p.add_argument("--profile-dir", default=None)
    doctor_p.add_argument(
        "--refresh",
        action="store_true",
        help="Also re-anchor query-ids via x.com's main.js (browser-free).",
    )

    fetch_p = subparsers.add_parser("fetch", help="A profile's tweets/replies/media.")
    fetch_p.add_argument("identifier", help="@handle, bare username, numeric id, or profile URL.")
    fetch_p.add_argument("--replies", action="store_true")
    fetch_p.add_argument("--limit", type=int, default=None)
    fetch_p.add_argument("--since", type=_parse_iso_date, default=None)
    fetch_p.add_argument("--until", type=_parse_iso_date, default=None)
    fetch_p.add_argument("--by", choices=["screen_name", "id"], default=None)
    _add_common_fetch_args(fetch_p)

    search_p = subparsers.add_parser("search", help="Tweets matching a query / advanced operators.")
    search_p.add_argument("query")
    search_p.add_argument("--product", choices=["latest", "top"], default="latest")
    search_p.add_argument("--limit", type=int, default=None)
    search_p.add_argument("--since", type=_parse_iso_date, default=None)
    search_p.add_argument("--until", type=_parse_iso_date, default=None)
    _add_common_fetch_args(search_p)

    tweet_p = subparsers.add_parser("tweet", help="One tweet plus its reply/conversation thread.")
    tweet_p.add_argument("identifier", help="Tweet URL or numeric tweet id.")
    tweet_p.add_argument("--replies", action="store_true")
    _add_common_fetch_args(tweet_p)

    return parser


def _cmd_login(args: argparse.Namespace) -> int:
    if args.cookies:
        try:
            auth.from_cookie_file(
                Path(args.cookies), args.profile, profile_dir_override=args.profile_dir
            )
        except InvalidCookieError as exc:
            print(f"invalid cookie export: {exc}", file=sys.stderr)
            return 1
        except OSError as exc:
            print(redact.redact_raw_text(f"could not read {args.cookies}: {exc}"), file=sys.stderr)
            return 1
        print(f"Cookie import succeeded. Profile saved: {args.profile!r}", file=sys.stderr)
        return 0

    try:
        logged_in = session.run_login(args.profile, profile_dir_override=args.profile_dir)
    except Exception as exc:  # noqa: BLE001 - last-resort CLI boundary
        print(redact.redact_raw_text(f"login failed: {exc}"), file=sys.stderr)
        return 1
    if logged_in:
        print(f"Logged in. Profile saved: {args.profile!r}", file=sys.stderr)
        return 0
    print(
        "Could not verify login (no auth_token/ct0 cookie found). Try again: scrape-x login",
        file=sys.stderr,
    )
    return 2


_STATUS_EXIT_CODES = {
    session.Status.LOGGED_IN: 0,
    session.Status.EXPIRED: 2,
    session.Status.RATE_LIMITED: 3,
}


def _cmd_status(args: argparse.Namespace) -> int:
    try:
        status = session.run_status(args.profile, profile_dir_override=args.profile_dir)
    except LoginRequiredError as exc:
        if args.json:
            print(json.dumps({"status": "not_logged_in", "error": str(exc)}))
        else:
            print(f"{exc} Run: scrape-x login --profile {args.profile}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - last-resort CLI boundary
        print(redact.redact_raw_text(f"status check failed: {exc}"), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"status": status.value}))
    else:
        print(f"status: {status.value}", file=sys.stderr)
    return _STATUS_EXIT_CODES[status]


def _cmd_setup(args: argparse.Namespace) -> int:
    try:
        session.run_setup(force=args.force)
    except Exception as exc:  # noqa: BLE001 - last-resort CLI boundary
        print(redact.redact_raw_text(f"setup failed: {exc}"), file=sys.stderr)
        return 1
    print("Browser provisioned.", file=sys.stderr)
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    try:
        ok, message = session.run_doctor(
            args.profile, profile_dir_override=args.profile_dir, refresh=args.refresh
        )
    except Exception as exc:  # noqa: BLE001 - last-resort CLI boundary
        print(redact.redact_raw_text(f"doctor check failed: {exc}"), file=sys.stderr)
        return 1
    print(redact.redact_raw_text(message), file=sys.stderr)
    return 0 if ok else 1


def _redact_raw_recursive(tweet: Tweet) -> None:
    """Scrub ``tweet.raw`` AND every nested ``retweeted_tweet``/``quoted_tweet``
    raw node -- ``Tweet.to_dict()`` serializes both recursively, so their own
    raw nodes reach the output file just as directly as the top-level one.
    """
    if tweet.raw is not None:
        tweet.raw = redact.redact(tweet.raw)
    if tweet.retweeted_tweet is not None:
        _redact_raw_recursive(tweet.retweeted_tweet)
    if tweet.quoted_tweet is not None:
        _redact_raw_recursive(tweet.quoted_tweet)


def _default_output_path(identifier: str, fmt: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + "Z"
    safe_identifier = re.sub(r"[^A-Za-z0-9]+", "-", identifier).strip("-") or "x"
    ext = "ndjson" if fmt == "ndjson" else "json"
    return default_output_dir() / f"{safe_identifier}-{timestamp}.{ext}"


def _write_output(tweets: list[Tweet], path: Path, fmt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "ndjson":
        with path.open("w", encoding="utf-8") as fh:
            for tweet in tweets:
                fh.write(json.dumps(tweet.to_dict(), ensure_ascii=False))
                fh.write("\n")
    else:
        with path.open("w", encoding="utf-8") as fh:
            json.dump([t.to_dict() for t in tweets], fh, ensure_ascii=False, indent=2)


def _finish(result: retrieve.RetrieveResult, identifier: str, args: argparse.Namespace) -> int:
    """Shared output-writing + exit-code logic for fetch/search/tweet (plan §10, §11)."""
    if args.raw:
        if args.no_redact:
            print(
                "WARNING: --no-redact leaves --raw output unscrubbed. The saved file "
                "will contain an unredacted live session fragment and full tweet text. "
                "See DISCLAIMER.md.",
                file=sys.stderr,
            )
        else:
            for tweet in result.tweets:
                _redact_raw_recursive(tweet)

    output_path = (
        Path(args.output) if args.output else _default_output_path(identifier, args.format)
    )
    _write_output(result.tweets, output_path, args.format)

    since_arg = getattr(args, "since", None)
    since_inconclusive = (
        since_arg is not None
        and not result.since_target_crossed
        and result.stop_reason in ("limit_reached", "max_requests")
    )
    if result.stop_reason == "rate_limited":
        exit_code = 3
    elif result.stop_reason == "soft_locked":
        exit_code = 2
    elif since_inconclusive:
        exit_code = 7
    else:
        exit_code = 0

    dated = [t.created_at for t in result.tweets if t.created_at is not None]
    oldest = min(dated).date().isoformat() if dated else "unknown"
    newest = max(dated).date().isoformat() if dated else "unknown"
    reached_note = " (requested --since NOT confirmed reached)" if exit_code == 7 else ""
    print(
        f"{len(result.tweets)} tweets, range {oldest}..{newest}, stop reason: "
        f"{result.stop_reason}{reached_note}. Saved to {output_path}",
        file=sys.stderr,
    )
    return exit_code


def _handle_common_errors(exc: Exception, args: argparse.Namespace) -> int:
    """Maps the shared error surface onto exit codes (plan §10). Returns -1 if
    ``exc`` isn't one of the mapped types, signaling the caller to fall
    through to its own last-resort handling."""
    if isinstance(exc, (LoginRequiredError, SessionExpiredError)):
        print(f"{exc} Run: scrape-x login --profile {args.profile}", file=sys.stderr)
        return 2
    if isinstance(exc, RateLimitedError):
        print(f"rate-limited: {exc}", file=sys.stderr)
        return 3
    if isinstance(exc, EnvelopeParseError):
        print(
            f"response envelope could not be parsed (possible query-id drift): {exc}. "
            "Try: scrape-x doctor --refresh",
            file=sys.stderr,
        )
        return 4
    if isinstance(exc, (ProfileUnavailableError, NotFoundError)):
        print(str(exc), file=sys.stderr)
        return 5
    if isinstance(exc, InvalidIdentifierError):
        print(f"invalid identifier: {exc}", file=sys.stderr)
        return 1
    return -1


def _load_read_client(args: argparse.Namespace) -> tuple[client.ReadClient, dict, dict] | int:
    """Loads the persisted session and builds a `ReadClient`, or returns the
    exit code to use if that fails."""
    try:
        credential = auth.load_session(args.profile, profile_dir_override=args.profile_dir)
    except LoginRequiredError as exc:
        print(f"{exc} Run: scrape-x login --profile {args.profile}", file=sys.stderr)
        return 2
    query_ids, features = session.query_ids_for(credential)
    read_client = client.ReadClient(credential.auth_token, credential.ct0, credential.user_agent)
    return read_client, query_ids, features


def _cmd_fetch(args: argparse.Namespace) -> int:
    try:
        kind, value = auth.normalize_identifier(args.identifier, by=args.by)
    except InvalidIdentifierError as exc:
        print(f"invalid identifier: {exc}", file=sys.stderr)
        return 1

    loaded = _load_read_client(args)
    if isinstance(loaded, int):
        return loaded
    read_client, query_ids, features = loaded

    try:
        result = retrieve.fetch_user_tweets(
            read_client,
            query_ids,
            features,
            kind,
            value,
            replies=args.replies,
            limit=args.limit,
            since=_since_datetime(args.since),
            until=_until_datetime(args.until),
            wait_on_limit=args.wait_on_limit,
            max_wait=args.max_wait,
            raw=args.raw,
        )
    except Exception as exc:  # noqa: BLE001 - dispatched by type below
        exit_code = _handle_common_errors(exc, args)
        if exit_code != -1:
            return exit_code
        if args.verbose:
            print(redact.redact_raw_text(f"unexpected error: {exc}"), file=sys.stderr)
        else:
            print(
                f"unexpected error: {type(exc).__name__} (rerun with -v for details)",
                file=sys.stderr,
            )
        return 1
    finally:
        read_client.close()

    return _finish(result, args.identifier, args)


def _cmd_search(args: argparse.Namespace) -> int:
    loaded = _load_read_client(args)
    if isinstance(loaded, int):
        return loaded
    read_client, query_ids, features = loaded

    try:
        result = retrieve.search(
            read_client,
            query_ids,
            features,
            args.query,
            product=args.product.capitalize(),
            limit=args.limit,
            since=_since_datetime(args.since),
            until=_until_datetime(args.until),
            wait_on_limit=args.wait_on_limit,
            max_wait=args.max_wait,
            raw=args.raw,
        )
    except Exception as exc:  # noqa: BLE001 - dispatched by type below
        exit_code = _handle_common_errors(exc, args)
        if exit_code != -1:
            return exit_code
        if args.verbose:
            print(redact.redact_raw_text(f"unexpected error: {exc}"), file=sys.stderr)
        else:
            print(
                f"unexpected error: {type(exc).__name__} (rerun with -v for details)",
                file=sys.stderr,
            )
        return 1
    finally:
        read_client.close()

    return _finish(result, args.query, args)


def _cmd_tweet(args: argparse.Namespace) -> int:
    try:
        value = auth.normalize_tweet_identifier(args.identifier)
    except InvalidIdentifierError as exc:
        print(f"invalid identifier: {exc}", file=sys.stderr)
        return 1

    loaded = _load_read_client(args)
    if isinstance(loaded, int):
        return loaded
    read_client, query_ids, features = loaded

    try:
        result = retrieve.fetch_tweet(
            read_client,
            query_ids,
            features,
            value,
            replies=args.replies,
            wait_on_limit=args.wait_on_limit,
            max_wait=args.max_wait,
            raw=args.raw,
        )
    except Exception as exc:  # noqa: BLE001 - dispatched by type below
        exit_code = _handle_common_errors(exc, args)
        if exit_code != -1:
            return exit_code
        if args.verbose:
            print(redact.redact_raw_text(f"unexpected error: {exc}"), file=sys.stderr)
        else:
            print(
                f"unexpected error: {type(exc).__name__} (rerun with -v for details)",
                file=sys.stderr,
            )
        return 1
    finally:
        read_client.close()

    return _finish(result, args.identifier, args)


_HANDLERS = {
    "login": _cmd_login,
    "status": _cmd_status,
    "setup": _cmd_setup,
    "doctor": _cmd_doctor,
    "fetch": _cmd_fetch,
    "search": _cmd_search,
    "tweet": _cmd_tweet,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return _HANDLERS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
