# Python API Reference

Everything in this page is importable from the top-level `agentic_x` package. If you only need the CLI, see [CLI Reference](CLI-Reference.md) instead — this page is for embedding scraping into your own Python code.

Read [DISCLAIMER.md](../../DISCLAIMER.md) before writing anything that calls this on an account you care about.

```python
from agentic_x import (
    XScraper, Tweet, User, Media, Status, RetrieveResult,
    AgenticXError, LoginRequiredError, SessionExpiredError, RateLimitedError,
    ProfileUnavailableError, NotFoundError, InvalidCookieError, InvalidIdentifierError,
    NotEnteredError, SessionClosedError, EnvelopeParseError,
    TransactionIdError, GatedOpRejectedError, BrowserFallbackError,
)
```

## Contents

- [Full example](#full-example)
- [XScraper](#xscraper)
- [Establishing a session: three ways](#establishing-a-session-three-ways)
- [status()](#status)
- [fetch_user_tweets()](#fetch_user_tweets)
- [iter_user_tweets()](#iter_user_tweets)
- [fetch_home()](#fetch_home)
- [search()](#search)
- [fetch_tweet()](#fetch_tweet)
- [The social graph: fetch_following() / fetch_followers() / fetch_retweeters()](#the-social-graph)
- [Tweet / User / Media](#tweet--user--media)
- [Exceptions](#exceptions)

## Full example

```python
from datetime import date

from agentic_x import XScraper, Status
from agentic_x.errors import (
    LoginRequiredError, SessionExpiredError, RateLimitedError,
    ProfileUnavailableError, NotFoundError, InvalidIdentifierError,
)

PROFILE = "default"
TARGET = "nasa"

x = XScraper(profile=PROFILE)
if x.status() is not Status.LOGGED_IN:
    print("Not logged in yet — opening a browser window...")
    if not x.login():
        raise SystemExit("Login failed; check the browser window and try again.")

with XScraper(profile=PROFILE) as x:
    try:
        tweets = x.fetch_user_tweets(TARGET, limit=50, since=date(2026, 1, 1))
    except InvalidIdentifierError:
        raise SystemExit(f"Not a valid handle/id/URL: {TARGET}")
    except LoginRequiredError:
        raise SystemExit("No saved session — call login() first.")
    except SessionExpiredError:
        raise SystemExit("Session expired — log in again.")
    except RateLimitedError as exc:
        raise SystemExit(f"Rate-limited; resets at epoch {exc.reset_at}.")
    except ProfileUnavailableError:
        raise SystemExit(f"Profile unavailable: {TARGET}")

    result = x.last_result
    print(f"Fetched {len(tweets)} tweets ({result.stop_reason}, "
          f"since_target_crossed={result.since_target_crossed})")
    for tweet in tweets:
        author = tweet.author.screen_name if tweet.author else "?"
        print(tweet.created_at, f"@{author}", tweet.text[:60])
```

Note the two separate `XScraper(profile=PROFILE)` instances above are deliberate: `status()`/`login()` don't need to happen inside a `with` block (they don't return anything that depends on the session staying open), while `fetch_user_tweets()`/`iter_user_tweets()`/`search()`/`fetch_tweet()` should always be scoped to one.

## XScraper

```python
class XScraper:
    def __init__(
        self,
        profile: str = "default",
        *,
        profile_dir: str | Path | None = None,
        min_request_pause: float | None = None,
        max_requests: int | None = None,
    ) -> None: ...
```

Read-only X/Twitter client: a stealth-browser login (or cookie import) harvests a session once; every read afterward goes over `httpx`, no browser involved. One instance = one persisted login profile + one set of read settings. It's a context manager — reads require the context to be open:

```python
with XScraper(profile="default") as x:
    tweets = x.fetch_user_tweets("nasa", limit=30)
```

Entering the `with` block loads the persisted session (raising [`LoginRequiredError`](#loginrequirederror) if there isn't one) and opens the underlying `httpx` client; exiting closes that client and marks the instance closed for good — there's no re-entering it (see [`SessionClosedError`](#sessionclosederror)).

### Constructor parameters

| Parameter | Default | Meaning |
|---|---|---|
| `profile` | `"default"` | Name of the persisted login profile to use. Maps to a directory under this tool's data dir (see [Configuration](Configuration.md#login-profiles)) unless `profile_dir` overrides it. Passed positionally or by keyword. |
| `profile_dir` | `None` | Explicit override for where the profile (session credential + browser data) lives on disk. `None` means "resolve from `profile` using the normal lookup" (env var, then the platform data directory). Accepts a `str` or a `pathlib.Path`. |
| `min_request_pause` | `None` | Minimum seconds to pace between reads. `None` uses the tool's default human-pause floor. Any value at or below `0.5s` is silently clamped up to `0.5s` (with a stderr note) — this is the one non-bypassable guardrail in the tool. |
| `max_requests` | `None` | Hard cap on GraphQL requests made by any single read call (`fetch_user_tweets`, `iter_user_tweets`, `search`, `fetch_tweet`). `None` falls back to a default budget of 500. Hitting the cap mid-pagination stops with `stop_reason == "max_requests"` rather than raising. |

Two more things worth knowing about the instance:

- `x.last_result` starts as `None` and is set to the [`RetrieveResult`](#retrieveresult) from the most recent `fetch_user_tweets()`/`search()`/`fetch_tweet()` call — useful for inspecting `stop_reason`/`since_target_crossed`/`requests_made` afterward without threading extra return values through your own code. `iter_user_tweets()` does **not** set `last_result` (it's a streaming generator with no single return value to capture).
- Construction itself never touches the network — nothing happens until you call `login()`, `status()`, or enter the `with` block.

## Establishing a session: three ways

All three persist a session credential (`auth_token` + `ct0` cookies, plus a user agent) to the profile directory. Reads (`fetch_user_tweets`, etc.) always load whatever was last persisted for that profile — they don't care which of the three put it there.

### 1. `login()` — headed stealth-browser login

```python
def login(self) -> bool
```

Instance method only — no classmethod shim. Opens a real, headed browser window (requires the `[browser]` extra), waits for you to log in to X by hand, harvests the `auth_token`/`ct0` cookies plus whatever GraphQL query-ids/features got captured along the way, and persists all of it to this instance's `profile`/`profile_dir`.

Returns `True` if the `auth_token`/`ct0` cookies were found after you press Enter, `False` otherwise. It does not raise on a failed login attempt — check the return value.

```python
if not XScraper(profile="default").login():
    print("Login didn't go through — check the browser window and try again.")
```

### 2. `from_cookies()` — import cookies directly

```python
@classmethod
def from_cookies(
    cls,
    *,
    auth_token: str,
    ct0: str,
    profile: str = "default",
    profile_dir: str | Path | None = None,
) -> XScraper
```

No browser required. Validates both values against a basic hex-shape check (raises [`InvalidCookieError`](#invalidcookieerror) if either fails), then persists them and returns a ready (but not yet entered) `XScraper` instance for that profile.

```python
x = XScraper.from_cookies(auth_token="a1b2...", ct0="c3d4...", profile="default")
with x:
    tweets = x.fetch_user_tweets("nasa", limit=10)
```

### 3. `from_cookie_file()` — import a cookie export

```python
@classmethod
def from_cookie_file(
    cls,
    path: str | Path,
    profile: str = "default",
    *,
    profile_dir: str | Path | None = None,
) -> XScraper
```

No browser required. Auto-detects a Netscape cookie file, a JSON array of `{name, value, ...}` cookie objects, or a raw `Cookie:` header / cURL `-H "Cookie: ..."` paste; extracts `auth_token`/`ct0`, validates their shape, persists, and returns a ready `XScraper` instance. Raises [`InvalidCookieError`](#invalidcookieerror) on any parse failure or a missing cookie. Prints a one-line reminder to stderr that the source export file still contains a live, password-less session.

```python
x = XScraper.from_cookie_file("/path/to/cookies.txt", profile="default")
with x:
    tweets = x.fetch_user_tweets("nasa", limit=10)
```

## status()

```python
def status(self) -> Status
```

Makes one cheap authenticated GraphQL read and reports which of three states the persisted session is in. Raises [`LoginRequiredError`](#loginrequirederror) if no session has ever been saved for this profile.

| `Status` member | Meaning |
|---|---|
| `Status.LOGGED_IN` | Session is valid; reads should work. |
| `Status.EXPIRED` | X rejected the session (401), or degraded it to a soft-locked "logged out" response on an otherwise-200 body. Fix: call `login()` again. |
| `Status.RATE_LIMITED` | The status check itself got a 429. Try again later. |

```python
from agentic_x import XScraper, Status

x = XScraper(profile="default")
if x.status() is not Status.LOGGED_IN:
    x.login()
```

## fetch_user_tweets()

```python
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
) -> list[Tweet]
```

A profile's tweets (or tweets + replies), deep via cursor pagination. Materializes the full run and returns a plain `list[Tweet]`. Must be called inside the owning `with` block.

**Parameters:**

- `identifier` — `@handle`, bare username, numeric user id, or a full `x.com`/`twitter.com` profile URL. Normalized and validated before any request is made; an unparseable value raises [`InvalidIdentifierError`](#invalididentifiererror) immediately.
- `replies` — when `True`, reads the profile's tweets **and replies** via `UserTweetsAndReplies` instead of `UserTweets`. This is one of the three operations behind the [transaction-id wall](Transaction-ID.md), so it is the least reliable parameter on this method: it can raise [`TransactionIdError`](#transactioniderror) or fall back to the browser (first page only) if X invalidates the generator.
- `limit` — maximum number of (non-pinned) tweets to return. `None` means no count limit (bounded only by `max_requests`/feed exhaustion). A pinned tweet, if present, is always included and never counts against `limit`.
- `since` / `until` — inclusive date bounds, either an ISO `"YYYY-MM-DD"` string or a `datetime.date`. A malformed string raises `ValueError` (strict `date.fromisoformat` parsing). `since` stops pagination once a tweet older than the bound is seen; check `x.last_result.since_target_crossed` to confirm the bound was actually reached rather than merely inferred from `limit_reached`/`max_requests`.
- `by` — disambiguates an all-digit `identifier` that would otherwise default to a numeric user id. Pass `by="screen_name"` to force treating an all-digit string as a handle instead.
- `wait_on_limit` — when `True` and a 429 is hit, sleep until `x-rate-limit-reset` (capped by `max_wait` if given) and retry instead of stopping with `stop_reason == "rate_limited"`.
- `max_wait` — caps how long a single `wait_on_limit` sleep can last, in seconds. Ignored if `wait_on_limit` is `False`.
- `raw` — when `True`, each `Tweet` (and any nested `retweeted_tweet`/`quoted_tweet`) also carries its raw captured GraphQL node in `Tweet.raw`, for debugging.

Sets `self.last_result` to the [`RetrieveResult`](#retrieveresult) for this call.

### Full error-handling example

```python
from datetime import date
from agentic_x import XScraper
from agentic_x.errors import (
    LoginRequiredError, SessionExpiredError, RateLimitedError,
    ProfileUnavailableError, SessionClosedError, InvalidIdentifierError,
)

with XScraper(profile="default") as x:
    try:
        tweets = x.fetch_user_tweets("nasa", limit=30, since=date(2026, 1, 1))
    except InvalidIdentifierError:
        print("That doesn't look like a valid handle/id/URL.")
    except LoginRequiredError:
        print("No saved session for this profile yet — run login() first.")
    except SessionExpiredError:
        print("Session expired — log in again.")
    except RateLimitedError as exc:
        print(f"Rate-limited; resets at epoch {exc.reset_at}.")
    except ProfileUnavailableError:
        print("Target account is suspended, protected, or doesn't exist.")
    except SessionClosedError:
        print("Called after the `with` block exited — this shouldn't happen here.")
    else:
        print(f"Got {len(tweets)} tweets; stopped because {x.last_result.stop_reason}")
```

## iter_user_tweets()

```python
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
) -> Iterator[Tweet]
```

Same parameters as `fetch_user_tweets()` — this is the streaming form, including `replies=True` and its [transaction-id](Transaction-ID.md) caveat. Unlike the FB sibling's `iter_profile()`, **this one genuinely streams**: each page's tweets are yielded as soon as they're parsed, one cursor-paginated network round trip at a time, rather than fully materializing the whole run before yielding anything. Breaking out of your loop early *does* save the remaining requests, which matters for a deep `limit=1000`-style pull where each page costs its own round trip plus pacing delay.

Two things to know:

**It must be consumed inside the owning `with` block.** Advancing it (the first `next()`, e.g. by starting a `for` loop over it) after the block has exited raises [`SessionClosedError`](#sessionclosederror). Because it's a generator, this check can't run at call time — calling `iter_user_tweets(...)` itself never raises, even on an already-closed instance; only actually advancing it does:

```python
with XScraper(profile="default") as x:
    gen = x.iter_user_tweets("nasa", limit=10)
# `with` block has exited here — `gen` was never advanced.
next(gen)  # raises SessionClosedError now, on first advance.
```

**It does not set `self.last_result`.** Since it's a generator with no single return value, there's nothing to capture `stop_reason`/`since_target_crossed` into. If you need those, use `fetch_user_tweets()` instead, or drive `retrieve.iter_user_tweets()`'s own `RunState` directly (lower-level, not part of this page).

```python
with XScraper(profile="default") as x:
    for tweet in x.iter_user_tweets("nasa", limit=1000):
        print(tweet.id, tweet.created_at)
        # Each tweet is yielded as its page is parsed — breaking here
        # skips the requests that would have fetched later pages.
```

## fetch_home()

```python
def fetch_home(
    self,
    *,
    limit: int | None = None,
    since: str | date | None = None,
    until: str | date | None = None,
    wait_on_limit: bool = False,
    max_wait: float | None = None,
    raw: bool = False,
) -> list[Tweet]
```

Your own home timeline. **Takes no identifier** — the feed belongs to the session, which is why this is the one read with nothing to target. Needs no transaction id. Promoted entries are dropped before they reach you.

`limit`, `since`, `until`, `wait_on_limit`, `max_wait`, `raw` all mean the same as on [`fetch_user_tweets()`](#fetch_user_tweets). Sets `self.last_result`.

```python
with XScraper(profile="default") as x:
    for tweet in x.fetch_home(limit=20):
        print(tweet.author.screen_name if tweet.author else "?", tweet.text[:60])
```

## search()

One of the three methods behind the [transaction-id wall](Transaction-ID.md): it works, but on a reverse-engineered header X can invalidate at any time, with a browser fallback (first page only) behind it.

```python
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
) -> list[Tweet]
```

Tweets matching a query, including X's advanced search operators (`from:`, `since:`, filters, etc. — anything X's own search bar accepts). Materializes the full run, cursor-paginated the same way as `fetch_user_tweets()`.

- `query` — the raw search string, passed through to X as-is.
- `product` — `"Latest"` or `"Top"` (X's own timeline-tab names; note the capitalization — this differs from the CLI's `--product latest|top` flag, which lowercases and capitalizes internally before calling this same method).
- `limit`, `since`, `until`, `wait_on_limit`, `max_wait`, `raw` — same meaning as [`fetch_user_tweets()`](#fetch_user_tweets).

If nothing matches, `stop_reason` on `self.last_result` is `"no_matches"` (distinct from `"feed_exhausted"`, which `fetch_user_tweets()` uses for the analogous case) — the returned list is simply empty rather than an exception being raised.

```python
with XScraper(profile="default") as x:
    tweets = x.search('from:nasa "artemis"', product="Latest", limit=20)
```

`quoted_tweet_id:<id>` is how you get a tweet's quote-tweets — there is no separate method for that, because X's own /quotes tab is a search too.

## fetch_tweet()

```python
def fetch_tweet(
    self,
    identifier: str,
    *,
    replies: bool = False,
    wait_on_limit: bool = False,
    max_wait: float | None = None,
    raw: bool = False,
) -> list[Tweet]
```

One tweet plus its reply/conversation thread. Returns a **list**, not a single `Tweet` — X's `TweetDetail` operation always returns the surrounding thread in the same envelope.

- `identifier` — a tweet URL or a bare numeric tweet id. Anything that doesn't normalize to a tweet id (e.g. a handle) raises [`InvalidIdentifierError`](#invalididentifiererror).
- `replies` — `False` (default) fetches just the first page and filters the result down to the focal tweet alone (a `list` with one `Tweet` in it). `True` paginates the full thread instead, returning the focal tweet plus every reply page found.
- `wait_on_limit`, `max_wait`, `raw` — same meaning as [`fetch_user_tweets()`](#fetch_user_tweets).

Raises [`NotFoundError`](#notfounderror) if the tweet doesn't exist (deleted, or thread unavailable) — this is checked before returning, not left for the caller to infer from an empty list.

```python
with XScraper(profile="default") as x:
    thread = x.fetch_tweet("https://x.com/nasa/status/1234567890123456789", replies=True)
    focal = thread[0]
    print(focal.text, "-", len(thread) - 1, "more tweet(s) in thread")
```

## The social graph

```python
def fetch_following(self, identifier: str, *, by=None, limit=None,
                    wait_on_limit=False, max_wait=None) -> list[User]
def fetch_followers(self, identifier: str, *, by=None, limit=None,
                    wait_on_limit=False, max_wait=None) -> list[User]
def fetch_retweeters(self, identifier: str, *, limit=None,
                     wait_on_limit=False, max_wait=None) -> list[User]
```

**These return `list[User]`, not `list[Tweet]`** — the only methods that do. `fetch_following`/`fetch_followers` take a profile identifier (with the same `by=` disambiguation as `fetch_user_tweets()`); `fetch_retweeters` takes a tweet URL or id.

They set **`self.last_user_result`**, not `self.last_result` — a [`UserResult`](#userresult), because a run returning accounts has no tweets to put in a `RetrieveResult`. There is no `since`/`until`: a follow list carries no dates to filter on.

`fetch_followers` is gated by the [transaction-id wall](Transaction-ID.md) and, unlike `search`/`replies`, has **no browser fallback**. `fetch_following` and `fetch_retweeters` are ungated.

```python
with XScraper(profile="default") as x:
    following = x.fetch_following("nasa", limit=100)
    if x.last_user_result.stop_reason == "empty_pages":
        print(f"Partial: X stopped giving accounts after {len(following)}.")
```

**Watch `stop_reason == "empty_pages"`.** X pads some follow lists with cursor-only pages forever; the run gives up after three consecutive account-less pages rather than burning its whole budget. It means *we stopped*, not *the list ended* — reporting such a result as somebody's complete follower list is simply wrong. See [CLI Reference](CLI-Reference.md#the-empty_pages-stop-reason).

There is deliberately no `fetch_likers()`: X removed the likers list from its product entirely, so there is nothing to call.

## Tweet / User / Media

All three are plain `@dataclass`es with a `to_dict()` method producing JSON-serializable output (`datetime` fields become UTC ISO-8601 strings ending in `Z`; nested dataclasses recurse). This is exactly the shape the CLI writes to its output files. See [Output Schema](Output-Schema.md) for the full JSON-level walkthrough, including edge cases (`created_at` can legitimately be `None`, note-tweet long-form text, retweet text resolution, etc.).

### Tweet

| Field | Type | Meaning |
|---|---|---|
| `id` | `str` | `rest_id` — stable dedup/merge key. |
| `url` | `str \| None` | `https://x.com/<handle>/status/<id>`, or an `/i/web/status/<id>` form if the author couldn't be resolved. |
| `created_at` | `datetime \| None` | UTC. **May be `None`** — an unparseable/missing timestamp never raises. |
| `text` | `str` | Full tweet text. For a retweet, resolved from the retweeted original (the outer `"RT @…"` stub is not used). For a note-tweet (long-form), the long-form text. |
| `lang` | `str \| None` | BCP-47-ish language code as reported by X. |
| `author` | `User \| None` | The posting account. `None` if the node couldn't be resolved. |
| `is_reply` | `bool` | Whether this tweet is a reply. |
| `in_reply_to_id` | `str \| None` | The tweet id this replies to, if any. |
| `conversation_id` | `str \| None` | X's thread/conversation id. |
| `reply_count` | `int \| None` | |
| `retweet_count` | `int \| None` | |
| `quote_count` | `int \| None` | |
| `like_count` | `int \| None` | `legacy.favorite_count`. |
| `bookmark_count` | `int \| None` | |
| `view_count` | `int \| None` | `None` if X didn't include a view count on this node. |
| `media` | `list[Media]` | Photos/videos/GIFs attached to the resolved text node. |
| `urls` | `list[str]` | Expanded (not t.co-shortened) URLs from the tweet's entities. |
| `hashtags` | `list[str]` | Hashtag text, without the `#`. |
| `is_note_tweet` | `bool` | Whether `text` came from long-form note-tweet content rather than `legacy.full_text`. |
| `is_pinned` | `bool` | Whether this tweet was delivered as a profile's pinned entry. |
| `retweeted_tweet` | `Tweet \| None` | The retweeted original, one level deep, if this is a retweet. |
| `quoted_tweet` | `Tweet \| None` | The quoted tweet, one level deep, if this is a quote-tweet. |
| `is_restricted` | `bool` | Whether X wrapped this tweet as visibility-limited (subscriber-only/limited-visibility). |
| `captured_at` | `datetime` | UTC timestamp of when this package captured/parsed the tweet (not from X — always set). |
| `raw` | `dict \| None` | The raw GraphQL node, only present if `raw=True` was passed to the call that produced it. |

### User

| Field | Type | Meaning |
|---|---|---|
| `id` | `str` | `rest_id` — stable. |
| `screen_name` | `str` | The `@handle`, without the `@`. |
| `name` | `str \| None` | Display name. |
| `created_at` | `datetime \| None` | Account creation date, UTC. |
| `followers_count` | `int \| None` | |
| `following_count` | `int \| None` | `legacy.friends_count`. |
| `tweet_count` | `int \| None` | `legacy.statuses_count`. |
| `is_blue_verified` | `bool \| None` | |
| `description` | `str \| None` | Bio text. |
| `url` | `str \| None` | The profile's "website" link, if set. |

### Media

| Field | Type | Meaning |
|---|---|---|
| `kind` | `str` | `"photo"`, `"video"`, `"animated_gif"`, or `"unknown"`. |
| `url` | `str` | `pbs.twimg.com`/`video.twimg.com` URL. **These expire** — download promptly if you need to keep the actual file; see the README for the expiry caveat. |
| `width` | `int \| None` | |
| `height` | `int \| None` | |
| `alt_text` | `str \| None` | |

## Exceptions

All exceptions live in `agentic_x.errors` and are also re-exported from the top-level package. All of them ultimately subclass `AgenticXError`, so `except AgenticXError:` catches anything this package raises on purpose.

```
AgenticXError (base)
├── LoginRequiredError
├── SessionExpiredError
├── RateLimitedError
├── ProfileUnavailableError
├── NotFoundError
├── InvalidCookieError (also subclasses ValueError)
├── InvalidIdentifierError (also subclasses ValueError)
├── NotEnteredError
├── SessionClosedError
├── EnvelopeParseError
├── TransactionIdError
├── GatedOpRejectedError
├── BrowserFallbackError
└── FeatureNotImplementedError  (deprecated — never raised)
```

#### `AgenticXError`

Base class for every error this package raises on purpose. Catch this if you just want to distinguish "this package failed in a known way" from an unexpected exception.

#### `LoginRequiredError`

No persisted session exists for this profile — entering the `with` block (or calling `status()`) found nothing on disk. **Fix:** call `login()`, or `from_cookies()`/`from_cookie_file()`, or `agentic-x login` from the CLI.

#### `SessionExpiredError`

A persisted session exists but X now rejects it (401) or has soft-locked it — X can silently degrade a stale session to an HTTP 200 with an empty/limited timeline rather than a clean 401, and both cases surface as this error. **Fix:** call `login()` again.

#### `RateLimitedError`

A request hit X's 429 rate limit and `wait_on_limit` was `False` (or the wait itself was capped by `max_wait` and still didn't clear it). Carries `reset_at` — the unix epoch from the `x-rate-limit-reset` header (may be `None`) — so callers can decide how long to wait before retrying.

```python
except RateLimitedError as exc:
    print(f"Resets at epoch {exc.reset_at}")
```

#### `ProfileUnavailableError`

The target user (from `fetch_user_tweets()`) is suspended, protected, or does not exist. Distinct from `NotFoundError`, which is about tweets, not users.

#### `NotFoundError`

The target tweet or thread (from `fetch_tweet()`) does not exist — deleted, or the thread is unavailable. Distinct from `ProfileUnavailableError`, which is about users, not tweets.

#### `InvalidCookieError`

Raised by `from_cookies()`/`from_cookie_file()` when a cookie value fails a basic hex-shape check, or when a cookie export can't be parsed / is missing `auth_token`/`ct0`. Also subclasses `ValueError`.

#### `InvalidIdentifierError`

The `identifier` passed to `fetch_user_tweets()`, `iter_user_tweets()`, or `fetch_tweet()` failed normalize-then-validate — not a recognized handle/id/URL shape, an unsupported host, or (for `fetch_tweet()`) something that isn't a tweet identifier at all. Also subclasses `ValueError`. Raised immediately, before any request is made.

#### `NotEnteredError`

A read method was called on an `XScraper` instance that was never entered via `with`. **Fix:** wrap reads in `with XScraper(...) as x:`.

#### `SessionClosedError`

A read was attempted on an `XScraper` instance whose `with` block has already exited, or an `iter_user_tweets()` generator was advanced after that point. **Fix:** don't hold onto an `XScraper` instance (or a generator from it) past its `with` block.

#### `EnvelopeParseError`

The GraphQL response envelope couldn't be located at all (e.g. `data.user.result.timeline...` for `UserTweets`) — a structural parse failure, distinct from a page that parsed fine but had zero tweets. Almost always means X rotated a query-id or changed the response shape. **Fix:** run `agentic-x doctor --refresh` (or `XScraper(...).status()` followed by a fresh `login()`) to re-anchor query-ids, then retry.

#### `TransactionIdError`

A fresh `x-client-transaction-id` could not be generated: x.com no longer serves one of the three ingredients the algorithm needs. Affects `search()`, `fetch_user_tweets(replies=True)` and `fetch_followers()` only — everything else is untouched. **Fix:** upgrade the package; the repair ships as a release, not as a setting. See [Transaction-ID](Transaction-ID.md).

#### `GatedOpRejectedError`

An id *was* minted and X refused the request anyway — the generator still runs but no longer produces something X accepts. This is what triggers the browser fallback for `search()`/`replies`; you will normally only see it escape if the fallback is unavailable.

#### `BrowserFallbackError`

The browser fallback itself could not produce a response — the `[browser]` extra is missing, the operation has no fallback page defined, or the page loaded without ever firing the operation (usually a browser profile that is logged out even though the stored cookies are not).

#### `FeatureNotImplementedError`

**Deprecated; nothing raises this any more.** Through v0.2.0 it was raised by `search()` and `fetch_user_tweets(replies=True)`, which could not work at all before the transaction id was generated per request. It stays exported so existing `except FeatureNotImplementedError:` clauses keep importing, and will be removed in the next major version.

## RetrieveResult

```python
@dataclass
class RetrieveResult:
    tweets: list[Tweet]
    stop_reason: str
    requests_made: int
    since_target_crossed: bool = False
```

Set as `x.last_result` after `fetch_user_tweets()`, `search()`, or `fetch_tweet()` (not `iter_user_tweets()` — see above). `stop_reason` is one of: `"limit_reached"`, `"since_crossed"`, `"feed_exhausted"`, `"no_matches"` (search only), `"max_requests"`, `"rate_limited"`, `"soft_locked"`, or `"browser_observed"` (the [browser fallback](Transaction-ID.md#the-browser-fallback) served this run — **one page only**). The full table, with what each means for completeness, is in the [CLI Reference](CLI-Reference.md#stop-reasons). `since_target_crossed` tells you whether a `since=` bound was actually confirmed reached, as opposed to the run stopping first for another reason (`limit_reached`/`max_requests`) while a `since` bound was still requested but unconfirmed.

## UserResult

```python
@dataclass
class UserResult:
    users: list[User]
    stop_reason: str
    requests_made: int
```

Set as `x.last_user_result` after `fetch_following()`/`fetch_followers()`/`fetch_retweeters()`. A separate type from `RetrieveResult` on purpose: these runs return accounts, and putting them in a field called `tweets` would make the type lie. `stop_reason` here is one of `"limit_reached"`, `"feed_exhausted"`, `"max_requests"`, `"rate_limited"`, or `"empty_pages"` — there is no `since_crossed`, since there are no dates to cross.

See [Configuration](Configuration.md) for how `profile`/`profile_dir` resolution and pacing/request-budget tuning work in more depth.
