# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-07-07

CLI self-description, so a consumer (e.g. the `x-fetch` skill) can read the flag and output-field surface straight from the installed binary instead of hand-copying it.

### Added
- `scrape-x schema` subcommand: prints the `fetch`/`tweet` output object schema — `Tweet`, its nested `User` (`author`), and `Media` — offline, no session or network, always exit 0. `--json` emits JSON Schema (draft 2020-12) with `$defs` for `User`/`Media` and a self-referencing `Tweet` for `retweeted_tweet`/`quoted_tweet`.
- Every `--help` flag now carries help text and its human default. Previously most flags on `fetch`/`search`/`tweet` printed bare.

### Changed
- `scrape-x search` and `scrape-x fetch --replies` now exit **1** ("not implemented") immediately, **regardless of login state**. Previously a logged-out user hit the session gate first and got exit 2 — told to run `scrape-x login` for a feature that does not exist. The ops remain unimplemented (X requires a single-use `x-client-transaction-id` per request for `SearchTimeline`/`UserTweetsAndReplies`); `scrape-x tweet <id> --replies` is the working path for a thread. This is the only behavior change in 0.2.0.
- The `search` subcommand help and `fetch --replies` help now mark these dead paths **NOT IMPLEMENTED**, coupled by a test to their actual exit-1 rejection so the marker can't go stale.

## [0.1.0] - 2026-07-05

### Added
- Initial release: `scrape-x login` / `status` / `setup` / `doctor` / `fetch` / `search` / `tweet`.
- Logged-in X/Twitter reads via a harvest-then-replay hybrid (stealth-browser login, `httpx` GraphQL replay). Confirmed live: `UserTweets` and `TweetDetail` need no `x-client-transaction-id`.
- `--limit`, `--since`/`--until` retrieval with stop-reason reporting.
- JSON and NDJSON output formats.
- Python API: `XScraper`, `Tweet`, `User`, `Media`.

### Known limitations
- `scrape-x search` and `scrape-x fetch --replies` (and the Python API's `search()`/`fetch_user_tweets(replies=True)`/`iter_user_tweets(replies=True)`) are **not yet implemented** — live-verified 2026-07-05 that `SearchTimeline`/`UserTweetsAndReplies` require a single-use `x-client-transaction-id` per request, which this package's harvest-then-replay architecture doesn't reproduce. Both fail fast with a clear `FeatureNotImplementedError` (exit code 1). See [wiki/FAQ-and-Troubleshooting.md](wiki/FAQ-and-Troubleshooting.md).

[Unreleased]: https://github.com/tjdwls101010/Scraper-for-X/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/tjdwls101010/Scraper-for-X/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/tjdwls101010/Scraper-for-X/releases/tag/v0.1.0
