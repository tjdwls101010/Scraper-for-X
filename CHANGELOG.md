# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-07-05

### Added
- Initial release: `scrape-x login` / `status` / `setup` / `doctor` / `fetch` / `search` / `tweet`.
- Logged-in X/Twitter reads via a harvest-then-replay hybrid (stealth-browser login, `httpx` GraphQL replay). Confirmed live: `UserTweets` and `TweetDetail` need no `x-client-transaction-id`.
- `--limit`, `--since`/`--until` retrieval with stop-reason reporting.
- JSON and NDJSON output formats.
- Python API: `XScraper`, `Tweet`, `User`, `Media`.

### Known limitations
- `scrape-x search` and `scrape-x fetch --replies` (and the Python API's `search()`/`fetch_user_tweets(replies=True)`/`iter_user_tweets(replies=True)`) are **not yet implemented** — live-verified 2026-07-05 that `SearchTimeline`/`UserTweetsAndReplies` require a single-use `x-client-transaction-id` per request, which this package's harvest-then-replay architecture doesn't reproduce. Both fail fast with a clear `FeatureNotImplementedError` (exit code 1). See [wiki/FAQ-and-Troubleshooting.md](wiki/FAQ-and-Troubleshooting.md).

[Unreleased]: https://github.com/tjdwls101010/Scraper-for-X/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tjdwls101010/Scraper-for-X/releases/tag/v0.1.0
