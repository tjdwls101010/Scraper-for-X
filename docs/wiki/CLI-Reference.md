# CLI Reference

The complete, flag-by-flag reference for `scrape-x`. The [README](../../README.md) has a condensed version of this; this page is the authoritative one — every default and every exit code here is read directly out of `build_parser()`, `_finish()`, and `_handle_common_errors()` in `src/scraper_for_x/cli.py`, not copied from memory.

If you haven't run either of these yet, do them in this order first: [Installation](Installation.md), then `scrape-x setup`, then `scrape-x login`.

## Global

```
scrape-x --version
```

Prints `scrape-x <version>` and exits 0. This is the only thing you can do without a subcommand — `scrape-x` with no arguments (or an unrecognized one) is a usage error (see [Exit codes](#exit-codes) below).

Every subcommand below also accepts `-h`/`--help`.

## `login`

One-time interactive login. Opens a real, visible Chromium window (a stealth `scrapling` browser session) at `https://x.com/home` and prints `A browser window should now be open. Log in to X there, then press Enter here to continue...`, then waits. Once you press Enter, it makes a few best-effort navigations (X's own profile, its replies tab, a live search, and whatever tweet it finds a link to on that search page) purely to harvest GraphQL query-ids/features from what gets captured, then extracts `auth_token`/`ct0` from the browser's cookie jar and persists the session (cookies, user agent, harvested query-ids/features) to disk under the named profile.

An operation not observed during that login simply falls back to the query-id shipped with the package, so a login that captures fewer ops than usual is not a failure. Most ids can also be refreshed later without a browser at all (`doctor --refresh`), with one exception: `Retweeters` lives in a lazily-loaded JavaScript chunk rather than X's main bundle, so it is reachable only by a browser login or the shipped default.

Alternatively, `--cookies PATH` imports an already-exported cookie file instead of opening a browser at all — no `scrapling`/browser dependency needed for this path.

| Flag | Default | Meaning |
|---|---|---|
| `--profile NAME` | `default` | Name of the login profile to create/overwrite. |
| `--profile-dir PATH` | none (falls back to the platform data dir, or `$SFX_PROFILE_DIR`) | Override where this profile's session is stored on disk. See [Configuration](Configuration.md) for the resolution order. |
| `--cookies PATH` | none | Import a Netscape/JSON/cURL cookie export instead of opening a browser. Auto-detects the export format; see [below](#--cookies-import). |

**Example (browser login):**

```bash
scrape-x login
```

```
Logged in. Profile saved: 'default'
```

If the harvest doesn't find both an `auth_token` and a `ct0` cookie (e.g. you closed the window without actually logging in):

```
Could not verify login (no auth_token/ct0 cookie found). Try again: scrape-x login
```

A second profile, e.g. for a throwaway account kept separate from your main one:

```bash
scrape-x login --profile burner
```

**Example (`--cookies` import):**

```bash
scrape-x login --cookies ~/exports/x-cookies.json
```

```
Cookie import succeeded. Profile saved: 'default'
```

`--cookies` also prints a one-line reminder to stderr that the source export file still contains a live, password-less session and should be deleted/secured — this happens on success, right before the "succeeded" line.

**Exit codes:** `0` on confirmed login (browser path) or successful import (`--cookies` path); `2` if the browser path completes but no `auth_token`/`ct0` cookie was found (try again); `1` on any other failure — a malformed cookie export (`InvalidCookieError`), an unreadable `--cookies` path (`OSError`), or a browser/login exception of any other kind. All `1`-exit messages are redaction-scrubbed before printing.

### `--cookies` import

Three export shapes are auto-detected, in this order: (1) a JSON array of `{name, value, ...}` cookie objects (browser-devtools "copy as JSON" / most export extensions); (2) a Netscape cookie file (tab-separated, one cookie per line, usually preceded by a `# Netscape HTTP Cookie File` header); (3) a raw `Cookie:` HTTP header string or cURL `-H "Cookie: ..."` paste (semicolon-separated `key=value` pairs on one line). Whichever shape is detected, both `auth_token` and `ct0` must be present, and each must pass a hex-shape check (`^[0-9a-f]{32,160}$`) — anything else raises `InvalidCookieError` before anything is saved. Parse/validation failures never echo the raw cookie line; offending text is scrubbed via the same redaction path used everywhere else.

## `status`

Checks whether a profile's persisted session is still logged in, via one cheap authenticated GraphQL read (a `UserByScreenName` lookup) — no browser involved.

| Flag | Default | Meaning |
|---|---|---|
| `--profile NAME` | `default` | Which profile to check. |
| `--profile-dir PATH` | none | Same override as `login`. |
| `--json` | off | Emit a single JSON object to stdout instead of a human-readable line to stderr. |

**Example (human-readable):**

```bash
scrape-x status
```

```
status: logged_in
```

**Example (`--json`, for scripting):**

```bash
scrape-x status --json
```

```json
{"status": "logged_in"}
```

If no session has ever been saved for the profile:

```
no session for profile 'default': run `scrape-x login` Run: scrape-x login --profile default
```

(with `--json`: `{"status": "not_logged_in", "error": "no session for profile 'default': run \`scrape-x login\`"}`)

**Exit codes:** `0` = `logged_in`, `2` = `expired` (also covers a not-yet-logged-in profile — `LoginRequiredError`), `3` = `rate_limited` (the status check's own probe request got a 429), `1` = the status check itself failed unexpectedly for some other reason.

## `setup`

Provisions the isolated Playwright/Chromium browser install this tool's `login` command uses. Shells out to `scrapling`'s own install mechanism into this package's own cache directory (`PLAYWRIGHT_BROWSERS_PATH` pointed at `config.browsers_dir()`), kept separate from any other tool's Playwright install (see [Configuration](Configuration.md)). Requires the `[browser]` extra to be installed.

You normally run this exactly once, right after installing the package, before your first `login`.

| Flag | Default | Meaning |
|---|---|---|
| `--force` | off | Reinstall even if a browser is already provisioned. |

**Example:**

```bash
scrape-x setup
```

```
Browser provisioned.
```

**Exit codes:** `0` on success, `1` if provisioning fails (the underlying `subprocess.run` raised — network error, disk space, unsupported platform; message on stderr).

## `doctor`

Loads a profile's persisted session and makes an authenticated round-trip read over plain `httpx` (no browser) to confirm the session is actually usable — broader than `status` in spirit only in that it also offers `--refresh`, but the underlying check is the same cheap `UserByScreenName` probe.

Run this after `login`, and any time `fetch`/`search`/`tweet` behaves strangely (e.g. exit 4) and you want to re-anchor query-ids before suspecting a deeper problem.

| Flag | Default | Meaning |
|---|---|---|
| `--profile NAME` | `default` | Which profile's session to exercise. |
| `--profile-dir PATH` | none | Same override as `login`/`status`. |
| `--refresh` | off | Also re-anchor query-ids by fetching x.com's `main.js` over `httpx` (no browser) and persisting the freshly-extracted query-ids/features back onto the profile. |

**Example:**

```bash
scrape-x doctor
```

```
OK - authenticated round-trip succeeded
```

**Example (`--refresh`):**

```bash
scrape-x doctor --refresh
```

```
OK - authenticated round-trip succeeded; re-anchored 5 query-id(s)
```

If the session isn't usable, or no session exists yet:

```
session check failed: expired (run `scrape-x login`)
```

```
no session for profile 'default': run `scrape-x login`
```

**Exit codes:** `0` if the round trip (and, with `--refresh`, the re-anchor) succeeds, `1` otherwise. Note `doctor` never exits 2/3 itself — a failed session check or missing session both come back as exit `1`, with the reason in the message. The message is always redaction-scrubbed before printing.

## `fetch`

A profile's tweets/replies/media.

```
scrape-x fetch <identifier> [flags]
```

`<identifier>` is required and positional — a `@handle`, a bare username, a numeric id, or a full profile/tweet URL on `x.com`/`twitter.com` (any subdomain among `www.`/`mobile.`/`m.` is stripped first). Anything that doesn't match one of those shapes, or a URL on a different host, is rejected before any network request is made (exit code `1`; see [Exit codes](#exit-codes)).

A bare all-digit token is treated as a numeric user **id** by default (X's `rest_id`s are numeric and all-digit handles are vanishingly rare). Force the handle reading with a literal `@` prefix or `--by screen_name`.

### Flags

| Flag | Default | Meaning |
|---|---|---|
| `--replies` | off | Read the profile's tweets **and replies** (`UserTweetsAndReplies`) instead of tweets only. A genuinely different operation with a different request shape — and one of the three behind the [transaction-id wall](Transaction-ID.md), so it is the least reliable flag on this command. |
| `--limit N` | none (unbounded) | Stop after this many (non-pinned) tweets. |
| `--since YYYY-MM-DD` | none | Stop once a tweet older than this date is seen. Best-effort — see [`--since`/`--until` semantics](#--since--until-semantics-and-exit-code-7) below. |
| `--until YYYY-MM-DD` | none | Skip tweets newer than this date (does not itself stop the run). |
| `--by screen_name\|id` | none (auto-detected) | Force how `<identifier>` is interpreted, overriding the all-digit-means-id default. |
| `--format json\|ndjson` | `json` | Output format. `json` is a single array; `ndjson` is one JSON object per line. |
| `--output PATH` | a generated path under this tool's data directory (see below) | Where to write the result. |
| `--wait-on-limit` | off | On a 429, sleep until the rate-limit reset instead of stopping. See [below](#--wait-on-limit--max-wait). |
| `--max-wait SECONDS` | none (wait the full reset interval) | Caps how long `--wait-on-limit` will sleep in one go. |
| `--profile NAME` | `default` | Which login profile's session to use. |
| `--profile-dir PATH` | none | Override where that profile is stored. |
| `--raw` | off | Include the raw captured GraphQL tweet node on each tweet, under a `raw` key (and recursively on any nested `retweeted_tweet`/`quoted_tweet`). See [`--raw`/`--no-redact`](#--raw-and---no-redact) below. |
| `--no-redact` | off | Only has an effect combined with `--raw`: disables PII scrubbing of the raw node(s) before they're written to the output file. Prints an on-screen warning every time. |
| `-v`, `--verbose` | off | On an unexpected error, print the full (redaction-scrubbed) exception text instead of just the exception type name. |

If `--output` is omitted, the file is written under this package's own data directory (never your current working directory), named `<sanitized-identifier>-<UTC timestamp>.<json|ndjson>` — e.g. `nasa-20260705T031813385206Z.json`. This is deliberate: captured tweets contain other people's data and a live-session-adjacent `raw` payload if `--raw` is used, and a default that lands in a repo you might `git add .` in is the wrong default.

### Example invocations

```bash
# Last 30 tweets, defaults everywhere else.
scrape-x fetch nasa --limit 30

# Everything since a date, as NDJSON to a specific file.
scrape-x fetch @nasa --since 2026-04-01 --format ndjson --output ~/x-export.ndjson

# A numeric-id profile.
scrape-x fetch 11348282 --limit 10

# Force handle interpretation for an all-digit vanity name.
scrape-x fetch 123456 --by screen_name

# A profile URL, waiting out a rate limit instead of stopping partway.
scrape-x fetch https://x.com/nasa --wait-on-limit --max-wait 300

# Debugging a suspected parser issue.
scrape-x fetch nasa --limit 5 --raw -v
```

**Example stderr summary (success):**

```
30 tweets, range 2026-04-02..2026-07-04, stop reason: limit_reached. Saved to /Users/you/Library/Application Support/scraper-for-x/output/nasa-20260705T031813385206Z.json
```

**Example stderr summary (`--since` not confirmed reached — see below):**

```
12 tweets, range 2026-05-14..2026-07-04, stop reason: max_requests (requested --since NOT confirmed reached). Saved to /Users/you/.../nasa-....json
```

## `feed`

Your own home timeline (`HomeTimeline`).

```
scrape-x feed [flags]
```

**Takes no identifier** — the feed belongs to the logged-in session, which is what makes it the one read with nothing to target. Needs no transaction id. Promoted/advertisement entries are present in X's response and are dropped before anything is written.

### Flags

`--limit`, `--since`, `--until`, `--format`, `--output`, `--wait-on-limit`, `--max-wait`, `--profile`, `--profile-dir`, `--raw`, `--no-redact`, `-v` — all with the same meaning and defaults as [`fetch`](#fetch). There is no `--replies`/`--by`, since there is no target to interpret.

```bash
scrape-x feed --limit 20
scrape-x feed --since 2026-07-01 --format ndjson --output ~/feed.ndjson
```

```
20 tweets, range 2026-07-19..2026-07-20, stop reason: limit_reached. Saved to /Users/you/Library/Application Support/scraper-for-x/output/home-20260720T133040191608Z.json
```

The default filename uses `home` in place of an identifier.

## `search`

Tweets matching a query, including X's advanced search operators.

**This is one of the three commands behind the [transaction-id wall](Transaction-ID.md)** — it works, but it depends on a reverse-engineered header that X can invalidate at any time. It falls back to the browser (first page only) when that happens, if the `[browser]` extra is installed.

```
scrape-x search <query> [flags]
```

`<query>` is required and positional — passed through verbatim to X's `SearchTimeline` GraphQL operation, so anything X's own search box accepts (`from:`, `since:`, `-filter:replies`, quoted phrases, etc.) works here too.

### Flags

| Flag | Default | Meaning |
|---|---|---|
| `--product latest\|top` | `latest` | Which search product to query (`Latest` or `Top`, capitalized internally). |
| `--limit N` | none (unbounded) | Stop after this many tweets. |
| `--since YYYY-MM-DD` | none | Same semantics as `fetch --since`. |
| `--until YYYY-MM-DD` | none | Same semantics as `fetch --until`. |
| `--format json\|ndjson` | `json` | Same as `fetch`. |
| `--output PATH` | generated path, same rule as `fetch` | Same as `fetch`. |
| `--wait-on-limit` | off | Same as `fetch`. |
| `--max-wait SECONDS` | none | Same as `fetch`. |
| `--profile NAME` | `default` | Same as `fetch`. |
| `--profile-dir PATH` | none | Same as `fetch`. |
| `--raw` | off | Same as `fetch`. |
| `--no-redact` | off | Same as `fetch`. |
| `-v`, `--verbose` | off | Same as `fetch`. |

There is no `--replies`/`--by` for `search` — those are `fetch`-only, since a search query has no target-identifier concept.

### Example invocations

```bash
# Latest tweets matching a query.
scrape-x search "anthropic claude" --limit 50

# Top/algorithmic ranking instead of chronological.
scrape-x search "spacex starship" --product top --limit 20

# Advanced operators, bounded to a date window.
scrape-x search "from:nasa since:2026-01-01" --since 2026-01-01 --until 2026-03-31
```

A query that legitimately matches nothing is not an error — stop reason `no_matches`, exit `0`, an empty (or `[]`) output file.

`quoted_tweet_id:<id>` is worth knowing specifically: it is how you get a tweet's quote-tweets, and it is exactly what X's own /quotes tab issues. There is no separate `quoters` command for that reason.

## `following` / `followers` / `retweeters`

The social graph. **These three emit `User` objects, not `Tweet`** — the only commands that do. See [Output Schema](Output-Schema.md#user-as-a-top-level-result).

```
scrape-x following <identifier> [flags]
scrape-x followers <identifier> [flags]
scrape-x retweeters <identifier> [flags]
```

- `following <identifier>` / `followers <identifier>` — accounts a user follows / accounts following them. `<identifier>` is a profile identifier, same forms and same `--by` disambiguation as [`fetch`](#fetch).
- `retweeters <identifier>` — accounts that retweeted a tweet. `<identifier>` is a **tweet** URL or numeric tweet id, same forms as [`tweet`](#tweet); there is no `--by`.

`followers` is behind the [transaction-id wall](Transaction-ID.md) and has **no browser fallback**; `following` and `retweeters` are not gated. That asymmetry is X's, not a design choice here.

### Flags

`--limit`, `--format`, `--output`, `--wait-on-limit`, `--max-wait`, `--profile`, `--profile-dir`, `--raw`, `--no-redact`, `-v` — same meaning as [`fetch`](#fetch), except `--limit` counts **accounts**. There is no `--since`/`--until`: a follow list carries no dates to filter on.

### Example invocations

```bash
scrape-x following nasa --limit 100
scrape-x followers nasa --limit 50 --format ndjson
scrape-x retweeters https://x.com/nasa/status/1234567890123456789 --limit 20
```

```
50 accounts, stop reason: limit_reached. Saved to /Users/you/.../followers-nasa-20260720T141730659793Z.json
```

The summary line counts **accounts**, not tweets, and carries no date range — there is nothing meaningful to report one for.

### The `empty_pages` stop reason

X pads some follow lists with pages that carry a fresh cursor and no accounts, indefinitely. Left alone, the ordinary "keep going until the cursor stops advancing" rule never terminates and burns the whole request budget collecting almost nothing — measured: `following` on `@X` returns one account, then empty pages until the budget runs out, about 250 seconds later.

So these three commands stop after three consecutive account-less pages, with stop reason **`empty_pages`**. Read it as *we gave up*, not *we reached the end* — the distinction is the whole reason it isn't reported as `feed_exhausted`. A list that ends this way is incomplete and should be described that way.

## `catalog`

Prints a machine-readable JSON description of the whole CLI — every command, every flag with its type and default, the exit-code contract, and which object type each command emits.

```bash
scrape-x catalog
```

Offline, no session, no network, always exit `0`. `--json` is accepted for symmetry with `schema --json` but does nothing: this command has no non-JSON form.

It is **generated from the argument parser itself**, so it describes the version you actually have installed rather than the version whoever wrote the docs had. That makes it the right thing for a script or an agent to read — this page can go stale between releases; the catalog cannot. Pair it with [`schema`](#schema) (what comes back) — catalog is how to call it.

## `schema`

Prints the output object schema — `Tweet`, its nested `User` and `Media` — as an annotated listing, or as JSON Schema (draft 2020-12) with `--json`. Offline, no session, always exit `0`. See [Output Schema](Output-Schema.md) for the same material in prose.

## `tweet`

One tweet plus (optionally) its reply/conversation thread.

```
scrape-x tweet <identifier> [flags]
```

`<identifier>` is required and positional — a tweet URL (`.../status/<id>`, with or without a leading handle, `/i/web/status/<id>` included) or a bare numeric tweet id. Anything else — including a bare handle or profile URL — is rejected with exit `1` (`tweet` only accepts a tweet-shaped identifier, unlike `fetch`, which also accepts profile identifiers).

### Flags

| Flag | Default | Meaning |
|---|---|---|
| `--replies` | off | Paginate the full reply/conversation thread. Without it, only the first `TweetDetail` page is fetched and the result is filtered down to just the focal tweet. |
| `--format json\|ndjson` | `json` | Same as `fetch`. |
| `--output PATH` | generated path, same rule as `fetch` | Same as `fetch`. |
| `--wait-on-limit` | off | Same as `fetch`. |
| `--max-wait SECONDS` | none | Same as `fetch`. |
| `--profile NAME` | `default` | Same as `fetch`. |
| `--profile-dir PATH` | none | Same as `fetch`. |
| `--raw` | off | Same as `fetch`. |
| `--no-redact` | off | Same as `fetch`. |
| `-v`, `--verbose` | off | Same as `fetch`. |

There is no `--limit`/`--since`/`--until`/`--by` for `tweet` — those don't apply to a single-tweet lookup.

### Example invocations

```bash
# Just the tweet itself.
scrape-x tweet https://x.com/nasa/status/1234567890123456789

# The tweet plus its full reply thread.
scrape-x tweet 1234567890123456789 --replies

# A trailing /photo/1 suffix is tolerated.
scrape-x tweet https://x.com/nasa/status/1234567890123456789/photo/1
```

If the tweet doesn't exist (deleted, or the thread is otherwise unavailable), and the underlying pagination stopped with `feed_exhausted` or `max_requests` having yielded nothing matching the requested id, this is `NotFoundError` → exit `5`.

## `--wait-on-limit`/`--max-wait`

Every read command (`fetch`, `feed`, `search`, `tweet`, `following`, `followers`, `retweeters`) hits X's per-endpoint 15-minute rate limits eventually on a long pull. Those limits are **per operation** and differ by more than an order of magnitude, so "a deep pull" costs very different amounts depending on which command it is — see the table in [FAQ and Troubleshooting](FAQ-and-Troubleshooting.md#im-hitting-rate-limits-constantly). What happens next depends on `--wait-on-limit`:

- **Without `--wait-on-limit` (default):** a 429 immediately stops the run with stop reason `rate_limited`, and whatever was collected so far is still written to the output file. Exit code `3`.
- **With `--wait-on-limit`:** if the 429 response carried an `x-rate-limit-reset` header (a unix timestamp), the run instead prints `scrape-x: waiting <N>s until rate-limit reset` to stderr, sleeps until that reset time, then retries the same request and continues pagination — the run does not stop or lose progress. If the 429 carried no reset timestamp at all, `--wait-on-limit` has nothing to wait for and the run stops the same as if the flag were absent (stop reason `rate_limited`, exit `3`).
- **`--max-wait SECONDS`** caps a single wait: the actual sleep is `min(time_until_reset, max_wait)`. If the reset is further away than `--max-wait`, the run wakes up early and retries anyway (X may 429 it again immediately, in which case the wait/retry repeats). Without `--max-wait`, the wait is however long is left until the real reset — potentially close to 15 minutes.

This retry loop is the only automatic retry anywhere in the tool — every other error (401/expired, structural parse failure, unavailable profile) stops the run immediately rather than retrying blind.

## `--raw` and `--no-redact`

`--raw` adds the full captured GraphQL tweet node to each tweet's output, under `raw` — and, recursively, under `raw` on any nested `retweeted_tweet`/`quoted_tweet` as well, since `Tweet.to_dict()` serializes those recursively too. It's meant for debugging — e.g. figuring out why a field parsed wrong, or filing an issue about an X response-shape change.

By default, `--raw` output is **redacted before it's written to the output file**: every tweet's `raw` node (and any nested `retweeted_tweet`/`quoted_tweet` raw node) is passed through the same scrubbing path used for diagnostics — sensitive keys (`auth_token`, `ct0`, `bearer`, `authorization`, `x-csrf-token`, `cookie`, `csrf_token`) are replaced with `[REDACTED]`, free-text fields (`text`, `full_text`, `name`, `screen_name`, `description`) are truncated to 40 characters with a `...[redacted N more chars]` suffix, and signed `pbs.twimg.com`/`video.twimg.com` media URLs have their query string (the signing material) stripped.

`--no-redact` disables that scrubbing for the `--raw` node(s) specifically, and prints this every time:

```
WARNING: --no-redact leaves --raw output unscrubbed. The saved file will contain an unredacted live session fragment and full tweet text. See DISCLAIMER.md.
```

Note this is the reverse of every other redaction path in the tool: normal `-v`/error/diagnostic output is *always* scrubbed with no way to turn it off, but `--raw`'s node is written to the *output file* — which is unredacted by design everywhere else — so `--no-redact` exists to let `--raw` opt into that same "fully raw" behavior on purpose, deliberately, with a warning attached. Only use it locally when you specifically need the untouched node; the resulting file is exactly as sensitive as [DISCLAIMER.md](../../DISCLAIMER.md) describes.

## `--since`/`--until` semantics, and exit code 7

Both bounds compare against a tweet's `created_at`; a tweet with `created_at=None` never participates in either comparison — it neither triggers a `--since` stop nor gets skipped by `--until`.

- **`--since D`** stops the run once a (non-pinned) tweet is seen that is **strictly before the start of day D** (`00:00:00.000000 UTC` on `D`) — i.e. day `D` itself is the inclusive boundary and is kept. The first such too-old tweet is not yielded; the run stops there with stop reason `since_crossed`.
- **`--until D`** skips (does not yield, does not stop the run) any tweet **newer than the end of day D** (`23:59:59.999999 UTC` on `D`) — i.e. day `D` itself is fully included. Unlike `--since`, crossing `--until` never itself ends the run; X's timelines are newest-first, so tweets newer than `--until` are simply skipped until older, in-window tweets start appearing.
- A **pinned tweet** is always returned regardless of `--since`/`--until`/`--limit`, and never drives the stop decision either way.

`--limit` and `--since` compose: whichever condition is hit first, on a given page, wins. The pagination loop checks `--limit` before `--since` on every batch, which is exactly why exit code `7` is scoped the way it is below.

Internally, `retrieve.py` tracks one stop reason per run — the full vocabulary is in [Stop reasons](#stop-reasons) below. Whether `--since` was actually **confirmed reached** is judged only from `since_target_crossed` (set when stop reason is `since_crossed`) — deliberately not inferred from `limit_reached`, because hitting `--limit` first proves nothing about whether `--since` would also have been reached had the run kept going.

The CLI then makes its own judgment call on top of that: hitting `--limit` is still reported as a full, ordinary success (exit `0`), even when `--since` was never independently confirmed — you got exactly what you asked for, on purpose. Exit code `7` is reserved for the narrower, genuinely uncertain case: `--since` was requested, the run did **not** confirm crossing it, and the run's own stop reason was `limit_reached` or `max_requests` (i.e. something other than actually crossing the date or the feed running dry). In that case the tool honestly doesn't know whether your requested date was reached, so it says so instead of guessing.

Concretely (`_finish()` in `cli.py`):

```python
since_inconclusive = (
    since_arg is not None
    and not result.since_target_crossed
    and result.stop_reason in ("limit_reached", "max_requests")
)
```

- `--limit 30` only, pagination hits the limit first → stop reason `limit_reached` → **exit 0**.
- `--since 2020-01-01` only, and pagination actually crosses that date or the feed runs dry first → stop reason `since_crossed` or `feed_exhausted` → **exit 0**.
- `--since 2020-01-01` (deep history), but the 500-request budget (or a rate limit, or any other stop) runs out before getting anywhere near 2020 → stop reason `max_requests` → **exit 7**, with `(requested --since NOT confirmed reached)` in the stderr summary.
- `--limit 30 --since 2020-01-01` together, and the limit is hit first (the common case, since `--limit` is checked first) → stop reason `limit_reached` → **exit 0**, even though `--since` was never verified. This is intentional, not a bug — see above.
- `--since 2020-01-01`, but the run is `rate_limited` or `soft_locked` before reaching it → those stop reasons take priority over the `since`-inconclusive check entirely (see the exit-code table below): exit `3` or `2` respectively, not `7`.

If you're scripting against this, exit `7` is your signal to either raise the request budget, narrow `--since`, retry with `--wait-on-limit`, or accept the partial result — the stderr line always states the actual tweet count and observed date range either way, so a partial run is never silently indistinguishable from a complete one.

## Stop reasons

Every read command's stderr summary ends with a stop reason. It answers a question the tweet count alone cannot: **is this all of it?** Treat it as part of the result, not as decoration — several of these mean "partial" while looking like success (exit `0`).

| Stop reason | What actually happened | Is the result complete? |
|---|---|---|
| `limit_reached` | Your `--limit` was hit. | No — there is more behind it. |
| `since_crossed` | A tweet older than `--since` was seen, so the run stopped there. | Yes, for the window you asked for. |
| `feed_exhausted` | X ran out of things to give. | Yes. |
| `no_matches` | A search matched nothing at all. | Yes — genuinely empty, not an error. |
| `max_requests` | The per-run request budget (500 by default) ran out. | No. |
| `rate_limited` | A 429 stopped the run; whatever was collected is still written. | No. Exit `3`. |
| `soft_locked` | Nothing came back **and** a probe found the session no longer valid. | No — this is a session problem. Exit `2`. |
| `browser_observed` | The transaction id was refused and the [browser fallback](Transaction-ID.md#the-browser-fallback) served the request. | **No — one page only.** |
| `empty_pages` | A social-graph run gave up after three consecutive account-less pages. | **No — we stopped, X didn't.** |

The last two are the ones most likely to be misread, because both produce a normal-looking result and exit `0`. Neither means the list is finished.

## Exit codes

### `login`

| Code | Meaning |
|---|---|
| 0 | Confirmed login (browser path), or successful `--cookies` import. |
| 1 | Any other failure: malformed cookie export, unreadable `--cookies` path, or any other browser/login exception. |
| 2 | Browser path completed but no `auth_token`/`ct0` cookie was found — the session couldn't be verified. |

### `status`

| Code | Meaning |
|---|---|
| 0 | `logged_in` — session is valid. |
| 1 | The status check itself failed unexpectedly for a reason other than session state. |
| 2 | `expired`, or no session has ever been saved for this profile (`LoginRequiredError`) — both map here. Run `scrape-x login`. |
| 3 | `rate_limited` — the status probe itself hit a 429. |

### `setup`

| Code | Meaning |
|---|---|
| 0 | Browser provisioned successfully. |
| 1 | Provisioning failed (network, disk, unsupported platform). |

### `doctor`

| Code | Meaning |
|---|---|
| 0 | Authenticated round-trip (and, with `--refresh`, the re-anchor) succeeded. |
| 1 | No session exists, the session check failed, or the round-trip otherwise didn't succeed. |

### `catalog` / `schema`

| Code | Meaning |
|---|---|
| 0 | Always. Both are offline and take no session. |

### Read commands (`fetch`, `feed`, `search`, `tweet`, `following`, `followers`, `retweeters`)

| Code | Meaning | Where it comes from |
|---|---|---|
| 0 | Success — `--limit` satisfied, `--since`/`--until` window fully covered, the feed/search was genuinely exhausted, or (search) legitimately matched nothing. | Default, unless the `--since`-inconclusive case below applies. |
| 1 | Invalid identifier, or any other/unexpected error. | `InvalidIdentifierError` on the positional argument (including `tweet` being given a non-tweet identifier). Also the catch-all fallback when the raised exception isn't one of the typed errors below. Also: **any argparse usage error** (bad/missing flag, unknown subcommand) — see note below. |
| 2 | Login required, session expired, or soft-locked. | `LoginRequiredError` (no session for this profile) / `SessionExpiredError` (explicit 401/logged-out marker, or a soft-locked session detected by the pre-exit-4 probe — see below) from `retrieve.*`. Message includes `Run: scrape-x login --profile <name>`. |
| 3 | Rate-limited before completion. | `RateLimitedError` — a 429 with `--wait-on-limit` not set (or set but the response carried no reset timestamp to wait on). Partial result is still written. |
| 4 | What X served no longer matches what this package expects. | Two distinct causes, distinguished by the message. **Query-id drift** — `EnvelopeParseError` from the parser: the `instructions`/`entries`/`cursor` anchors are not locatable. Fix locally with `scrape-x doctor --refresh`. **Transaction-id failure** — `TransactionIdError` (the generator could not run) or a gated op refusing a minted header, affecting only `search`/`fetch --replies`/`followers`; see [Transaction-ID](Transaction-ID.md#when-it-breaks-two-different-failures). Neither is ever raised for a merely-empty but structurally valid result; see the soft-lock probe below. |
| 5 | Target (profile or tweet) unavailable. | `ProfileUnavailableError` (`fetch`/`following`/`followers`: suspended/protected/nonexistent account) or `NotFoundError` (`tweet`: deleted tweet or unavailable thread). |
| 7 | Partial: `--since` requested but not confirmed reached. | `args.since is not None`, `since_target_crossed` is `False`, and stop reason is `limit_reached` or `max_requests`. See [above](#--since--until-semantics-and-exit-code-7). |

**A genuinely empty (but structurally parsed) result is not automatically exit 4.** When a run yields nothing at all, `retrieve.py` runs a pre-exit-4 soft-lock probe (the same cheap `UserByScreenName` check `status`/`doctor` use) before accepting that the emptiness is real: for `search`, an empty result is `no_matches` (exit `0`); for everything else, if the probe says the session is no longer logged in, the stop reason becomes `soft_locked` and the CLI raises `SessionExpiredError` (exit `2`) instead of silently reporting an empty success. Only a genuine structural parse failure (the envelope itself can't be walked) reaches exit `4`.

**Argparse usage errors are exit code 1, not argparse's usual 2.** This CLI overrides `argparse.ArgumentParser.error()` (the `_ArgumentParser` class in `cli.py`) specifically so that a typo'd flag or missing required argument exits `1`, not `2` — because exit `2` already has a specific, different meaning in this CLI's contract ("login required, expired, or soft-locked"). Without this override, a script checking `if exit_code == 2: run login` could be fooled by an unrelated CLI typo into thinking the session had expired. So: `scrape-x fetch` with no identifier, an unknown flag, or `scrape-x bogus-subcommand` all exit `1`, printing usage to stderr — same code as "other/unexpected error," on purpose.

## See also

- [Quick Start](Quick-Start.md) — a walkthrough of `setup` → `login` → `fetch` for a first-time user.
- [Transaction-ID](Transaction-ID.md) — why `search`, `fetch --replies` and `followers` are the fragile three.
- [Configuration](Configuration.md) — profile storage resolution order, environment variables, browser cache location.
- [Output Schema](Output-Schema.md) — what actually ends up in the `--output` file.
- [Security & Privacy](Security-and-Privacy.md) — the full redaction/threat model referenced throughout this page.
- [FAQ & Troubleshooting](FAQ-and-Troubleshooting.md)
- [../DISCLAIMER.md](../../DISCLAIMER.md)
