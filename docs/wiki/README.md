# scraper-for-x wiki

`scrape-x` is a read-only X/Twitter scraper built on a **harvest-then-replay hybrid**: a stealth browser (or a cookie import) logs you in once and harvests the session, then every read afterward is a plain `httpx` GraphQL request — no browser in the loop, no `x-client-transaction-id` replay.

> **Read [../DISCLAIMER.md](../DISCLAIMER.md) before you use this.** Using this tool violates X's Terms of Service, publishing it exposes its maintainer, and scraping other people's tweets can make *you* a data controller over their personal data under GDPR. Use a dedicated/throwaway account, not your primary one.

## Getting started

- [Installation](Installation.md) — base vs. `[browser]` extra, platform notes, upgrading, uninstalling
- [Quick Start](Quick-Start.md) — logging in, running your first fetch, reading the output

## Reference

- [CLI Reference](CLI-Reference.md) — every subcommand, every flag, every exit code
- [Python API Reference](Python-API-Reference.md) — `XScraper`, exceptions, usage inside your own code
- [Output Schema](Output-Schema.md) — every `Tweet` / `User` / `Media` field explained
- [Configuration](Configuration.md) — profiles, environment variables, request pacing and limits
- [FAQ and Troubleshooting](FAQ-and-Troubleshooting.md) — common errors, exit codes, "why did it stop early"
- [Security and Privacy](Security-and-Privacy.md) — the full threat model behind [../DISCLAIMER.md](../DISCLAIMER.md)

## Project

- [Contributing](Contributing.md) — dev setup, running tests, release process

## Elsewhere

- [Main README](../README.md)
- [PyPI package](https://pypi.org/project/scraper-for-x/)

---

This `wiki/` is a regular, tracked-in-git folder, not GitHub's separate Wiki feature — edits to it go through normal commits and pull requests like the rest of the codebase.
