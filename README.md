# scraper-for-x

Read-only scraping of logged-in **X/Twitter** data via a **harvest-then-replay hybrid**: a stealth browser (or a cookie import) logs you in once and harvests the session, then every read afterward is a plain `httpx` GraphQL request — no browser in the loop, no `x-client-transaction-id` replay.

> **Read [DISCLAIMER.md](DISCLAIMER.md) before using this.** Using this tool violates X's Terms of Service, publishing it exposes its maintainer, and scraping other people's tweets can make *you* a data controller over their personal data under GDPR. Use a dedicated/throwaway account, not your primary one.

## Installation

Base install — cookie-import login only, **no browser dependency**:

```bash
pip install scraper-for-x
```

The `[browser]` extra — adds a stealth browser for `scrape-x login`:

```bash
pip install "scraper-for-x[browser]"
```

If you only ever import cookies from a session you already have (e.g. exported from your own logged-in browser), the base install is all you need.

## Quick Start

```bash
# 1. One-time interactive login — opens a real browser window, you log in by hand.
scrape-x login

# 2. Fetch a profile's tweets.
scrape-x fetch nasa --limit 50
```

## CLI overview

| Command | Purpose |
|---|---|
| `scrape-x login` | One-time login: headed stealth browser by default, or `--cookies FILE` to import an existing session |
| `scrape-x status` | Check whether the persisted session is logged in, expired, or rate-limited |
| `scrape-x setup` | Provision the login browser into an isolated cache (requires `[browser]`) |
| `scrape-x doctor` | Authenticated round-trip + query-id freshness check (`--refresh` re-anchors query-ids from x.com's `main.js`, browser-free) |
| `scrape-x fetch <identifier>` | A profile's tweets/media (`--limit`, `--since`, `--until`, `--by screen_name\|id`) — **`--replies` not yet implemented, see below** |
| `scrape-x search <query>` | **Not yet implemented, see below** |
| `scrape-x tweet <identifier>` | A single tweet plus its reply/conversation thread (`--replies`) |

`fetch`/`search`/`tweet` all share `--format json|ndjson`, `--output PATH`, `--profile NAME`, `--profile-dir PATH`, `--wait-on-limit`, `--max-wait`, `--raw` (+ `--no-redact`), and `-v/--verbose`. See the [CLI Reference](wiki/CLI-Reference.md) for every flag and exit code.

### Known limitation: `search` and `fetch --replies` (v0.1.0)

Live-verified 2026-07-05: X's `SearchTimeline` and `UserTweetsAndReplies` GraphQL operations require a fresh, **single-use** `x-client-transaction-id` header on every request — unlike plain profile fetches and single-tweet lookups, which this package proved work over plain `httpx` replay with no such header. A captured transaction-id cannot be harvested once and reused like a session cookie or query-id; reproducing X's generator for it is exactly the fragility this package's harvest-then-replay architecture was built to avoid (see [twikit#408](https://github.com/d60/twikit/issues/408)).

Both `scrape-x search` and `scrape-x fetch --replies` fail fast with a clear `FeatureNotImplementedError` (exit code 1) rather than a confusing network error. A browser-observe fallback for these two operations specifically (the same approach `scrape-fb` uses) is on the roadmap — see [wiki/FAQ-and-Troubleshooting.md](wiki/FAQ-and-Troubleshooting.md).

## Python API

```python
from scraper_for_x import XScraper

XScraper(profile="default").login()  # one-time, opens a headed browser

with XScraper(profile="default") as x:
    tweets = x.fetch_user_tweets("nasa", limit=50)
    for tweet in x.iter_user_tweets("nasa", limit=50):
        ...  # must be consumed inside the `with` block

    x.fetch_tweet("https://x.com/nasa/status/1234567890")
    # x.search(...) raises FeatureNotImplementedError -- see "Known limitation" above.
```

## Documentation

This README covers the essentials. For everything else, see the **[wiki](wiki/README.md)**:

- [Installation](wiki/Installation.md)
- [Quick Start](wiki/Quick-Start.md)
- [CLI Reference](wiki/CLI-Reference.md) — every flag, every exit code
- [Python API Reference](wiki/Python-API-Reference.md)
- [Configuration](wiki/Configuration.md) — profiles, environment variables
- [Output Schema](wiki/Output-Schema.md) — every `Tweet`/`User`/`Media` field
- [Security and Privacy](wiki/Security-and-Privacy.md) — the full threat model behind [DISCLAIMER.md](DISCLAIMER.md)
- [FAQ and Troubleshooting](wiki/FAQ-and-Troubleshooting.md)
- [Contributing](wiki/Contributing.md)

## License

MIT — see [LICENSE](LICENSE). The license covers the code; it does not cover what you do with the data you collect (see [DISCLAIMER.md](DISCLAIMER.md)).
