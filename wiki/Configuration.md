# Configuration

This page covers login profiles, where things are stored on disk, and the pacing/budget defaults that keep this tool from turning into a mass-scraper. For the full flag list see [CLI Reference](CLI-Reference.md); this page is about *why* the defaults are what they are and when to change them.

## Login profiles

A "profile" is a persisted, logged-in X session — cookies (`auth_token`/`ct0`) and a user-agent, harvested once via `scrape-x login` (or imported via `--cookies`) and saved to disk. Every command that touches a session takes `--profile NAME`:

```bash
scrape-x login   --profile work
scrape-x status  --profile work
scrape-x doctor  --profile work
scrape-x fetch   --profile work someuser
```

If you don't pass `--profile`, everything uses a profile named `default`. You only need more than one profile if you're maintaining sessions for more than one X account.

Profiles are stored under a platformdirs-managed data directory, one subdirectory per name. On macOS that's:

```
~/Library/Application Support/scraper-for-x/profiles/<name>/session.json
```

The session credential itself is a single JSON file (`session.json`) inside the profile directory, holding `auth_token`, `ct0`, the user-agent, and whatever query-ids/features were harvested at login time. The profile *directory* is created at `0700` (owner read/write/execute only), and the `session.json` file inside it is additionally hardened to `0600`. That file **is** an authenticated X session with no password attached — anyone who can read it can act as your account. Read [DISCLAIMER.md](../DISCLAIMER.md) before you back it up, sync it, or copy it anywhere.

If you log in with a real browser (`scrape-x login`, no `--cookies`), the stealth browser's own persistent context is stored alongside the credential, at `<profile-dir>/browser/` — nested under the same `0700` tree rather than a separate location.

## Overriding where profiles live: `--profile-dir` and `SFX_PROFILE_DIR`

If you don't want profiles under the default platformdirs path, override the root directory profiles are stored under, two ways:

- `--profile-dir PATH` on the command line (`login`, `status`, `doctor`, `fetch`, `search`, and `tweet` all accept it)
- the `SFX_PROFILE_DIR` environment variable

The precedence, exactly (from `config.profile_dir`):

1. `--profile-dir PATH`, if given, always wins.
2. Otherwise, the `SFX_PROFILE_DIR` environment variable, if set.
3. Otherwise, the platformdirs default (`.../scraper-for-x/profiles/`).

Whichever root is in effect, the actual profile still lives at `<root>/<name>`, so `--profile` and `--profile-dir`/`SFX_PROFILE_DIR` compose normally:

```bash
export SFX_PROFILE_DIR=/Volumes/secure/x-profiles
scrape-x login --profile work
# -> session stored at /Volumes/secure/x-profiles/work/session.json
```

A `--profile-dir` passed on the command line overrides `SFX_PROFILE_DIR` for that invocation only, without unsetting the environment variable. Note that overriding the profile directory is silent — there's no stderr warning printed when you do this (unlike the non-bypassable pacing floor below, which does warn).

The same override is available from the Python API as the `profile_dir` keyword on `XScraper(...)`:

```python
from scraper_for_x import XScraper

with XScraper("work", profile_dir="/Volumes/secure/x-profiles") as x:
    tweets = x.fetch_user_tweets("nasa", limit=50)
```

## The isolated browser cache

Separately from login profiles, this tool keeps its own **Chromium install** isolated from every other Playwright-based tool on your machine, via `config.browsers_dir()`:

```
~/Library/Application Support/scraper-for-x/browsers/
```

`scrape-x setup` installs Chromium into that path (by shelling out to scrapling's install mechanism with `PLAYWRIGHT_BROWSERS_PATH` pointed at it); `scrape-x login` reads from it whenever it launches the stealth browser.

This isolation is deliberate: Playwright and patchright pin exact browser build versions, and different tools on your system can easily want different versions of the same browser. Sharing a cache means one tool's install can silently break another's. Keeping `scraper-for-x`'s Chromium in its own directory means this tool never touches, and is never touched by, anyone else's Playwright browser cache.

There's no flag or environment variable for this path — it's not user-configurable. `scrape-x setup --force` reinstalls into it if it ever needs to be redone.

## The non-bypassable pacing floor: `MIN_REQUEST_PAUSE_SECONDS`

Every read (`fetch`, `search`, `tweet`) goes over `httpx` as a paced sequence of GraphQL requests, not a browser doing anything visibly human — there's no scrolling to slow down here, just request pacing. `ReadClient` sleeps `min_pause` seconds before every request after the first.

**One floor is non-bypassable: the inter-request pause cannot go below `0.5` seconds no matter what you pass.** `config.clamp_request_pause` enforces this in code, not just as documented advice:

```python
MIN_REQUEST_PAUSE_SECONDS = 0.5
```

Passing something at or below the floor doesn't error; it gets silently raised, with a note on stderr telling you what actually got used:

```
scrape-x: --min-request-pause 0.0 raised to 0.5 (minimum is 0.5s)
```

This applies no matter how the value arrives — there is no flag, environment variable, or config file that disables it. As the source comment puts it, zero-delay reads are "both the most ban-inducing setting and the thing that makes this a mass-scraping tool rather than a personal one." It's a per-process floor on how fast *this tool* fires requests — not a rate-limiting ceiling, ban-avoidance guarantee, or a substitute for respecting X's own rate limits (which still apply and still return HTTP 429 regardless of your pacing).

**There is no `--min-request-pause` or `--max-requests` CLI flag.** Pacing and per-run request budget are configurable only from the Python API, as keyword arguments to `XScraper`:

```python
from scraper_for_x import XScraper

with XScraper("default", min_request_pause=1.5, max_requests=200) as x:
    tweets = x.fetch_user_tweets("nasa", limit=100)
```

- `min_request_pause` (default `None`, which resolves to `DEFAULT_HUMAN_PAUSE[0]` = `1.0` second, then clamped to the `0.5`s floor) — seconds to sleep before each request after the first.
- `max_requests` (default `None`, which resolves to a `500`-request budget) — a hard cap on how many GraphQL requests this `XScraper` instance (its underlying `ReadClient`) will make across its whole lifetime, regardless of `--limit`.

`DEFAULT_HUMAN_PAUSE = (1.0, 3.0)` is defined in `config.py`, but only its first element (`1.0`) is actually used as the default pause — there's no randomized min/max range the way the FB sibling randomizes scroll pauses; X reads are a fixed pause per request, not a scroll loop.

Separately, `fetch`/`search`/`tweet`'s own pagination loop (in `retrieve.py`) tracks its own per-call request budget, defaulting to the same `500` figure — this is what actually produces the `max_requests` stop-reason you'll see in a run's summary line and exit code. In practice both budgets apply together: whichever is smaller stops the run first.

**When it's reasonable to raise `min_request_pause`:** if you're already seeing `RateLimitedError`/exit code 3 often, or you want a given profile to look less like an automated client hammering the read API at a steady 1-second cadence.

**When you should not lower it below the default:** essentially never — `min_request_pause` is already clamped at `0.5`s regardless, so there's no way to make requests fire faster than that; if you're tempted to try, that's a sign you're scaling this beyond what a single logged-in session should be doing, which is exactly the account-safety line this floor exists to hold. See [DISCLAIMER.md](../DISCLAIMER.md).

## Handling rate limits: `--wait-on-limit` and `--max-wait`

These **are** CLI flags (also available as `wait_on_limit`/`max_wait` kwargs on every `XScraper` read method). When X returns HTTP 429, the default behavior is to stop the run with `stop_reason="rate_limited"` (exit code 3). Passing `--wait-on-limit` instead sleeps until the rate-limit window resets (per X's own `x-rate-limit-reset` header) and then continues the same run:

```bash
scrape-x fetch nasa --limit 200 --wait-on-limit --max-wait 300
```

`--max-wait SECONDS` caps how long a single wait is allowed to run before giving up and stopping anyway — without it, the wait is however long X's own reset window says. Without `--wait-on-limit` at all, `--max-wait` has no effect: a plain 429 always stops the run immediately.

## Default output location and `--output`

`fetch`/`search`/`tweet` write captured tweets to a file — never to stdout, and never to your current directory by default. The default path comes from `config.default_output_dir()`:

```
~/Library/Application Support/scraper-for-x/output/<identifier>-<timestamp>.<ext>
```

(`<identifier>` is the sanitized handle/id/query you passed in; `<ext>` is `json` or `ndjson` depending on `--format`.) This default is deliberate: captured tweets carry other people's handles, names, and tweet text (see [DISCLAIMER.md](../DISCLAIMER.md)), and a default that lands outside any git-tracked path makes it harder to accidentally commit someone else's data.

Pass `--output PATH` to write somewhere else instead:

```bash
scrape-x fetch nasa --limit 30 --output ./out.json
```

`--output` is a plain path override — it doesn't change where profiles or the browser cache live, only where the fetched tweets get written. Whatever directory you point it at, you're responsible for keeping that file as secure as its contents warrant.
