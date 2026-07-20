"""``scrape-x`` command-line entry point (plan §10)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from . import __version__, auth, client, observe, redact, retrieve, session
from .config import DEFAULT_PROFILE_NAME, default_output_dir
from .errors import (
    BrowserFallbackError,
    GatedOpRejectedError,
    InvalidCookieError,
    InvalidIdentifierError,
    LoginRequiredError,
    NotFoundError,
    ProfileUnavailableError,
    RateLimitedError,
    SessionExpiredError,
    TransactionIdError,
)
from .model import (
    Tweet,
    json_schema,
    media_schema_fields,
    tweet_schema_fields,
    user_schema_fields,
)
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


_PROFILE_DIR_HELP = (
    "Override where this profile's session credential lives "
    "(default: platform data dir, or $SFX_PROFILE_DIR)."
)

#: Shown both in the top-level subcommand list (help=) and in `scrape-x search
#: --help` (description=). These two ops sit behind X's transaction-id wall; the
#: header is generated per request (see transaction.py), which is the one
#: reverse-engineered, rot-prone part of this package — so the help says so
#: rather than promising more reliability than these commands have.
_SEARCH_HELP = (
    "Tweets matching a query / advanced operators. Needs a generated "
    "x-client-transaction-id (reverse-engineered — may break when X changes it)."
)

_FETCH_REPLIES_HELP = (
    "Include the profile's replies (UserTweetsAndReplies). Needs a generated "
    "x-client-transaction-id (reverse-engineered — may break when X changes it)."
)


class _ArgumentParser(argparse.ArgumentParser):
    """Usage errors exit 1, not argparse's default 2 -- 2 already means
    "login required/expired/soft-locked" in this CLI's exit-code contract
    (plan §10); conflating the two would make a scripted exit-code check lie.
    """

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def _add_common_fetch_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        choices=["json", "ndjson"],
        default="json",
        help="A single JSON array, or one NDJSON object per line (default: json).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Where to write results (default: a timestamped file under the "
            "platform data dir, not cwd)."
        ),
    )
    parser.add_argument(
        "--wait-on-limit",
        action="store_true",
        help="On a 429, sleep until the rate-limit window resets instead of exiting 3.",
    )
    parser.add_argument(
        "--max-wait",
        type=float,
        default=None,
        help="Cap a --wait-on-limit sleep, in seconds (default: unbounded).",
    )
    parser.add_argument(
        "--profile",
        default=DEFAULT_PROFILE_NAME,
        help="Named login session to use (default: 'default').",
    )
    parser.add_argument("--profile-dir", default=None, help=_PROFILE_DIR_HELP)
    parser.add_argument(
        "--raw",
        action="store_true",
        help=(
            "Attach the raw tweet_results.result node to each tweet (redacted unless --no-redact)."
        ),
    )
    parser.add_argument(
        "--no-redact",
        action="store_true",
        help="Disable PII scrubbing on --raw output (prints an on-screen warning).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help=(
            "Print the full (still redaction-scrubbed) error text instead of "
            "just the exception type name."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="scrape-x", description="Read-only X/Twitter scraper.")
    parser.add_argument("--version", action="version", version=f"scrape-x {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login_p = subparsers.add_parser(
        "login", help="Log in (headed stealth browser by default, or --cookies)."
    )
    login_p.add_argument(
        "--profile",
        default=DEFAULT_PROFILE_NAME,
        help="Named login session to save (default: 'default').",
    )
    login_p.add_argument("--profile-dir", default=None, help=_PROFILE_DIR_HELP)
    login_p.add_argument(
        "--cookies",
        default=None,
        help="Import a Netscape/JSON/cURL cookie export instead of opening a browser.",
    )

    status_p = subparsers.add_parser("status", help="Check whether a profile is logged in.")
    status_p.add_argument(
        "--profile",
        default=DEFAULT_PROFILE_NAME,
        help="Named login session to check (default: 'default').",
    )
    status_p.add_argument("--profile-dir", default=None, help=_PROFILE_DIR_HELP)
    status_p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON to stdout instead of a human summary to stderr.",
    )

    setup_p = subparsers.add_parser(
        "setup", help="Provision the login browser into an isolated cache."
    )
    setup_p.add_argument(
        "--force", action="store_true", help="Reinstall even if already provisioned."
    )

    doctor_p = subparsers.add_parser(
        "doctor", help="Authenticated round-trip + query-id freshness check."
    )
    doctor_p.add_argument(
        "--profile",
        default=DEFAULT_PROFILE_NAME,
        help="Named login session to check (default: 'default').",
    )
    doctor_p.add_argument("--profile-dir", default=None, help=_PROFILE_DIR_HELP)
    doctor_p.add_argument(
        "--refresh",
        action="store_true",
        help="Also re-anchor query-ids via x.com's main.js (browser-free).",
    )

    schema_p = subparsers.add_parser(
        "schema",
        help="Print the fetch/tweet output object schema (offline, no login needed).",
    )
    schema_p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON Schema (draft 2020-12) instead of a plain annotated listing.",
    )

    fetch_p = subparsers.add_parser("fetch", help="A profile's tweets/replies/media.")
    fetch_p.add_argument("identifier", help="@handle, bare username, numeric id, or profile URL.")
    fetch_p.add_argument("--replies", action="store_true", help=_FETCH_REPLIES_HELP)
    fetch_p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after this many tweets (default: unbounded).",
    )
    fetch_p.add_argument(
        "--since",
        type=_parse_iso_date,
        default=None,
        help=(
            "Keep tweets on/after this date YYYY-MM-DD; best-effort — if the run stops on "
            "--limit or the request budget before reaching it, exit 7."
        ),
    )
    fetch_p.add_argument(
        "--until",
        type=_parse_iso_date,
        default=None,
        help="Keep tweets on/before this date YYYY-MM-DD.",
    )
    fetch_p.add_argument(
        "--by",
        choices=["screen_name", "id"],
        default=None,
        help="How to read an all-digit identifier: numeric user id (default) or screen_name.",
    )
    _add_common_fetch_args(fetch_p)

    feed_p = subparsers.add_parser(
        "feed",
        help="The logged-in account's home feed (takes no target).",
    )
    feed_p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after this many tweets (default: unbounded).",
    )
    feed_p.add_argument(
        "--since",
        type=_parse_iso_date,
        default=None,
        help=(
            "Keep tweets on/after this date YYYY-MM-DD; best-effort — if the run stops on "
            "--limit or the request budget before reaching it, exit 7."
        ),
    )
    feed_p.add_argument(
        "--until",
        type=_parse_iso_date,
        default=None,
        help="Keep tweets on/before this date YYYY-MM-DD.",
    )
    _add_common_fetch_args(feed_p)

    search_p = subparsers.add_parser(
        "search",
        help=_SEARCH_HELP,
        description=_SEARCH_HELP,
    )
    search_p.add_argument("query")
    search_p.add_argument("--product", choices=["latest", "top"], default="latest")
    search_p.add_argument("--limit", type=int, default=None)
    search_p.add_argument("--since", type=_parse_iso_date, default=None)
    search_p.add_argument("--until", type=_parse_iso_date, default=None)
    _add_common_fetch_args(search_p)

    # Social graph. These emit User objects, not Tweets -- see `scrape-x schema`.
    for name, help_text, target_help in (
        ("following", "Accounts a user follows.", "@handle, username, numeric id, or profile URL."),
        (
            "followers",
            "Accounts following a user. Needs a generated x-client-transaction-id "
            "(reverse-engineered — may break when X changes it).",
            "@handle, username, numeric id, or profile URL.",
        ),
        ("retweeters", "Accounts that retweeted a tweet.", "Tweet URL or numeric tweet id."),
    ):
        graph_p = subparsers.add_parser(name, help=help_text, description=help_text)
        graph_p.add_argument("identifier", help=target_help)
        graph_p.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Stop after this many accounts (default: unbounded).",
        )
        if name != "retweeters":
            graph_p.add_argument(
                "--by",
                choices=["screen_name", "id"],
                default=None,
                help=(
                    "How to read an all-digit identifier: numeric user id (default) or screen_name."
                ),
            )
        _add_common_fetch_args(graph_p)

    tweet_p = subparsers.add_parser("tweet", help="One tweet plus its reply/conversation thread.")
    tweet_p.add_argument("identifier", help="Tweet URL or numeric tweet id.")
    tweet_p.add_argument(
        "--replies", action="store_true", help="Include the reply/conversation thread."
    )
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


def _print_schema_object(title: str, where: str, fields: list[dict]) -> None:
    print(f"{title} — {where}:\n")
    for field in fields:
        note = "" if field["always_present"] else " (only present with --raw)"
        print(f"  {field['name']} : {field['type']}{note}")
        print(f"      {field['description']}")
    print()


def _cmd_schema(args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps(json_schema(), indent=2))
        return 0
    print(
        "scrape-x output schema — `fetch`/`tweet` write a JSON array of Tweet objects "
        "(one per line with --format ndjson).\n"
        "Nested objects: Tweet.author is a User; Tweet.media[] are Media; "
        "Tweet.retweeted_tweet / Tweet.quoted_tweet are nested Tweets.\n"
    )
    _print_schema_object("Tweet", "one element of the output array", tweet_schema_fields())
    _print_schema_object("User", "Tweet.author", user_schema_fields())
    _print_schema_object("Media", "an element of Tweet.media", media_schema_fields())
    return 0


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
    if isinstance(exc, TransactionIdError):
        # Same exit code as an unparseable envelope, and for the same reason:
        # what X serves no longer matches what this package expects. The
        # message distinguishes the two, since the fix is different (re-port
        # the generator vs re-anchor the query-ids).
        print(
            f"could not generate an x-client-transaction-id: {exc}. This affects "
            "search / fetch --replies / followers only; other commands still work.",
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


def _try_browser_fallback(
    operation: str, target: str, args: argparse.Namespace
) -> retrieve.RetrieveResult | None:
    """Recover a gated op via the browser after the generated txid was refused.

    Returns ``None`` if the fallback itself is unavailable, so the caller can
    report the original failure rather than a confusing second one.
    """
    print(
        f"{operation}: X refused the generated x-client-transaction-id "
        "(the generator has likely rotted). Falling back to the browser — "
        "this returns only the FIRST page.",
        file=sys.stderr,
    )
    try:
        body = observe.observe(
            operation,
            target,
            profile=args.profile,
            profile_dir_override=args.profile_dir,
        )
    except BrowserFallbackError as exc:
        print(f"browser fallback unavailable: {exc}", file=sys.stderr)
        return None
    return retrieve.from_observed_body(
        body,
        operation,
        limit=args.limit,
        since=_since_datetime(args.since),
        until=_until_datetime(args.until),
        raw=args.raw,
    )


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
    except GatedOpRejectedError:
        # Only --replies can land here; plain `fetch` is not a gated op.
        fallback = _try_browser_fallback("UserTweetsAndReplies", value, args)
        if fallback is None:
            return 4
        result = fallback
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


def _cmd_feed(args: argparse.Namespace) -> int:
    loaded = _load_read_client(args)
    if isinstance(loaded, int):
        return loaded
    read_client, query_ids, features = loaded

    try:
        result = retrieve.fetch_home(
            read_client,
            query_ids,
            features,
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

    # "home" stands in for the identifier the other commands name their output
    # file after -- the feed's target is the session itself, not an argument.
    return _finish(result, "home", args)


#: CLI name -> X operation, for the three User-returning commands. `likers` is
#: absent on purpose: probed live 2026-07-20, X no longer exposes a likers list
#: (/likes redirects to the tweet, and the op appears in none of its 685 JS
#: chunks). For quoters, use: scrape-x search "quoted_tweet_id:<id>".
_GRAPH_OPERATIONS = {
    "following": "Following",
    "followers": "Followers",
    "retweeters": "Retweeters",
}


def _write_users(users: list, path: Path, fmt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "ndjson":
        with path.open("w", encoding="utf-8") as fh:
            for user in users:
                fh.write(json.dumps(user.to_dict(), ensure_ascii=False))
                fh.write("\n")
    else:
        with path.open("w", encoding="utf-8") as fh:
            json.dump([u.to_dict() for u in users], fh, ensure_ascii=False, indent=2)


def _cmd_social_graph(args: argparse.Namespace) -> int:
    operation = _GRAPH_OPERATIONS[args.command]
    try:
        if operation == "Retweeters":
            kind, value = "id", auth.normalize_tweet_identifier(args.identifier)
        else:
            kind, value = auth.normalize_identifier(args.identifier, by=args.by)
    except InvalidIdentifierError as exc:
        print(f"invalid identifier: {exc}", file=sys.stderr)
        return 1

    loaded = _load_read_client(args)
    if isinstance(loaded, int):
        return loaded
    read_client, query_ids, features = loaded

    try:
        result = retrieve.fetch_social_graph(
            read_client,
            query_ids,
            features,
            operation,
            kind,
            value,
            limit=args.limit,
            wait_on_limit=args.wait_on_limit,
            max_wait=args.max_wait,
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

    output_path = (
        Path(args.output)
        if args.output
        else _default_output_path(f"{args.command}-{args.identifier}", args.format)
    )
    _write_users(result.users, output_path, args.format)

    exit_code = 3 if result.stop_reason == "rate_limited" else 0
    print(
        f"{len(result.users)} accounts, stop reason: {result.stop_reason}. Saved to {output_path}",
        file=sys.stderr,
    )
    return exit_code


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
            # argparse takes the flag lowercase for ergonomics; X's variables
            # want it capitalised ("Latest"/"Top").
            product=args.product.capitalize(),
            limit=args.limit,
            since=_since_datetime(args.since),
            until=_until_datetime(args.until),
            wait_on_limit=args.wait_on_limit,
            max_wait=args.max_wait,
            raw=args.raw,
        )
    except GatedOpRejectedError:
        fallback = _try_browser_fallback("SearchTimeline", args.query, args)
        if fallback is None:
            return 4
        result = fallback
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
    "schema": _cmd_schema,
    "fetch": _cmd_fetch,
    "feed": _cmd_feed,
    "search": _cmd_search,
    "following": _cmd_social_graph,
    "followers": _cmd_social_graph,
    "retweeters": _cmd_social_graph,
    "tweet": _cmd_tweet,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return _HANDLERS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
