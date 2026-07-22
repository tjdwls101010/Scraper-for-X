# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] — Renamed to Agentic X

Renamed from the old identity (formerly PyPI `scraper-for-x`, import
`scraper_for_x`, CLI `scrape-x`, repo `Scraper-for-X`) to **Agentic X**.
Behaviour is unchanged. Breaking: the import path, CLI command, PyPI package
name, and base exception class (`ScraperForXError` → `AgenticXError`) all
changed. Install `agentic-x`; the old package is a tombstone pointing here.

## [0.3.1] - 2026-07-20

### Fixed
- `agentic-x catalog` now accepts `--json`. It always emitted JSON and took no flags, while its sibling `agentic-x schema` takes `--json` — so `catalog --json`, the obvious thing to type next to it, failed with an argparse error. That reads as "this command doesn't exist" rather than "that flag is redundant", and it broke the first version-check script written against the catalog. The flag is a no-op; `catalog` has no non-JSON form.

## [0.3.0] - 2026-07-20

Widens the read surface from "one profile's tweets" to "the home feed, search, replies and the social graph", and gets past the `x-client-transaction-id` wall that made two of those impossible in 0.2.0.

### Added
- `agentic-x feed` — your home feed (`HomeTimeline`). Takes no target: the feed belongs to the session. Needs no transaction id. Promoted/ad entries are dropped.
- `agentic-x following` / `followers` / `retweeters` — the social graph. These emit **`User`** objects rather than `Tweet`, the first new output shape since 0.1.0.
- `agentic-x catalog` — a machine-readable JSON description of every command, argument and exit code, **derived from the argument parser itself** so it cannot drift from the real CLI. Complements `agentic-x schema --json` (how to call it vs. what comes back).
- `transaction.py` — generates X's per-request `x-client-transaction-id` in pure Python. Algorithm ported from the MIT-licensed [XClientTransaction](https://github.com/iSarabjitDhiman/XClientTransaction); attribution and licence in the module docstring.
- A browser-observe fallback for `search` / `fetch --replies` (requires the `[browser]` extra): if X ever refuses a generated transaction id, the stealth browser loads the page and the response its own client receives is parsed instead. Returns the **first page only**, reported as the new `browser_observed` stop reason.
- New typed errors, all exported: `TransactionIdError` (the generator could not run), `GatedOpRejectedError` (a header was minted and X still refused), `BrowserFallbackError`.
- `XScraper.fetch_home`, `.fetch_following`, `.fetch_followers`, `.fetch_retweeters`; `search()` and `fetch_user_tweets(replies=True)` now work.

### Changed
- **`agentic-x search` and `agentic-x fetch --replies` now work.** They were exit-1 "not implemented" in 0.2.0. A logged-out user now gets exit **2** ("log in") rather than exit 1, because the feature exists. Their `--help` no longer says NOT IMPLEMENTED, but does say the transaction id is reverse-engineered and can break.
- `session.query_ids_for` now merges the shipped defaults **per operation** under the session's harvested query-ids. Previously it returned the harvested map whole, so any operation harvested before it existed was simply missing.
- Refreshed every default query-id against a live session. `TweetDetail` and `UserTweetsAndReplies` shipped as unverified *placeholder* guesses in 0.1.0/0.2.0; both are now real, live-observed ids.
- `HomeTimeline` is sent as a POST, matching X's own client (`ReadClient.post`).

### Fixed
- A social-graph run could burn its whole 500-request budget (~250s) collecting one account: X returns cursor-only pages indefinitely for some follow lists, and the tweet loop's "only a non-advancing cursor is EOF" rule never fires. Three consecutive account-less pages now end the run with the new `empty_pages` stop reason — deliberately *not* `feed_exhausted`, since giving up and reaching the end are different facts.

### Not implemented, deliberately
- **Likers.** X has removed the likers list: `/status/<id>/likes` redirects to the tweet, and the operation name appears in none of the 685 JavaScript chunks x.com serves (checked 2026-07-20). For quoters, use `agentic-x search "quoted_tweet_id:<id>"` — what X's own /quotes tab does.
- `FeatureNotImplementedError` is no longer raised by anything. It stays exported so existing `except` clauses keep importing, and will be removed in the next major version.

## [0.2.0] - 2026-07-07

CLI self-description, so a consumer (e.g. the `x-fetch` skill) can read the flag and output-field surface straight from the installed binary instead of hand-copying it.

### Added
- `agentic-x schema` subcommand: prints the `fetch`/`tweet` output object schema — `Tweet`, its nested `User` (`author`), and `Media` — offline, no session or network, always exit 0. `--json` emits JSON Schema (draft 2020-12) with `$defs` for `User`/`Media` and a self-referencing `Tweet` for `retweeted_tweet`/`quoted_tweet`.
- Every `--help` flag now carries help text and its human default. Previously most flags on `fetch`/`search`/`tweet` printed bare.

### Changed
- `agentic-x search` and `agentic-x fetch --replies` now exit **1** ("not implemented") immediately, **regardless of login state**. Previously a logged-out user hit the session gate first and got exit 2 — told to run `agentic-x login` for a feature that does not exist. The ops remain unimplemented (X requires a single-use `x-client-transaction-id` per request for `SearchTimeline`/`UserTweetsAndReplies`); `agentic-x tweet <id> --replies` is the working path for a thread. This is the only behavior change in 0.2.0.
- The `search` subcommand help and `fetch --replies` help now mark these dead paths **NOT IMPLEMENTED**, coupled by a test to their actual exit-1 rejection so the marker can't go stale.

## [0.1.0] - 2026-07-05

### Added
- Initial release: `agentic-x login` / `status` / `setup` / `doctor` / `fetch` / `search` / `tweet`.
- Logged-in X/Twitter reads via a harvest-then-replay hybrid (stealth-browser login, `httpx` GraphQL replay). Confirmed live: `UserTweets` and `TweetDetail` need no `x-client-transaction-id`.
- `--limit`, `--since`/`--until` retrieval with stop-reason reporting.
- JSON and NDJSON output formats.
- Python API: `XScraper`, `Tweet`, `User`, `Media`.

### Known limitations
- `agentic-x search` and `agentic-x fetch --replies` (and the Python API's `search()`/`fetch_user_tweets(replies=True)`/`iter_user_tweets(replies=True)`) are **not yet implemented** — live-verified 2026-07-05 that `SearchTimeline`/`UserTweetsAndReplies` require a single-use `x-client-transaction-id` per request, which this package's harvest-then-replay architecture doesn't reproduce. Both fail fast with a clear `FeatureNotImplementedError` (exit code 1). See [wiki/FAQ-and-Troubleshooting.md](wiki/FAQ-and-Troubleshooting.md).

[Unreleased]: https://github.com/tjdwls101010/Agentic-X/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/tjdwls101010/Agentic-X/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/tjdwls101010/Agentic-X/releases/tag/v0.1.0
