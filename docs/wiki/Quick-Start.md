# Quick Start

This page walks through the whole flow once, slowly, with real terminal output at each step. If you just want the condensed version, see the [main README](../../README.md#quick-start). If you haven't installed the tool yet, start with [Installation](Installation.md) instead — this page assumes `agentic-x` is already on your PATH.

Before any of this: **read [DISCLAIMER.md](../../DISCLAIMER.md).** This automates a read-only session against X/Twitter's Terms of Service, and it's worth knowing what that means before you log anything in.

Four steps: `login` → `status` → a read command → look at the output.

## 1. Log in

Two ways to get a session on disk. Pick one.

**Headed stealth browser (needs the `[browser]` extra: `pip install "agentic-twitter[browser]"`):**

```bash
$ agentic-x login
```

```
A browser window should now be open. Log in to X there, then press Enter here to continue...
```

A real, visible Chromium window opens. Log in exactly like you would in any browser — type your credentials, clear 2FA, solve a captcha if X throws one at you. Nothing about this step is automated on purpose. Once you're looking at your home timeline in that window, go back to the terminal and press Enter.

`agentic-x` then reads back the `auth_token`/`ct0` cookies from that browser session. If both are present, it saves them:

```
Logged in. Profile saved: 'default'
```

If it can't find them (you pressed Enter before finishing login, or the window closed early), it says so and exits 1 — just run `agentic-x login` again, it's idempotent.

**Cookie import (no browser, works on the base install):**

If you already have a logged-in X session in a real browser, export its cookies (a devtools "copy as JSON" cookie list, a Netscape cookie file, or a raw `Cookie:` header paste all work) and import that file directly:

```bash
$ agentic-x login --cookies export.txt
```

```
agentic-x: export.txt still contains a live, password-less X session — delete or secure it now that it has been imported
Cookie import succeeded. Profile saved: 'default'
```

Either way, what got saved is `auth_token` + `ct0` + a user-agent string, as a single `session.json` file under a profile directory (`chmod 0700`, the file itself `0600`). There's no password stored, but anyone who can read that file can act as your logged-in X session just as well as you can — see [DISCLAIMER.md §8](../../DISCLAIMER.md) before you treat it casually. Don't back it up to iCloud/Dropbox/Time Machine, don't commit it, and if the machine is ever lost or compromised, revoke the session from x.com itself (Settings → Security and account access → Apps and sessions), not just by deleting the folder.

**Use a dedicated or throwaway X account for this, not your main one.** Automating any X account — including "automating" by just reading what a real logged-in session sees — is against X's Terms of Service, and enforcement shows up as rate-limits, soft-locks, or account suspension. Read [../DISCLAIMER.md](../../DISCLAIMER.md) in full; this isn't boilerplate legal filler, it covers real account and data risk.

## 2. Check your session: `status`

```bash
$ agentic-x status
```

```
status: logged_in
```

`status` loads the persisted session and makes one cheap authenticated GraphQL read, then classifies the result as `logged_in`, `expired` (exit 2 — run `agentic-x login` again), or `rate_limited` (exit 3). Add `--json` for a script-friendly line:

```bash
$ agentic-x status --json
```

```json
{"status": "logged_in"}
```

If something's not working and you want a deeper check (does query-id-dependent parsing still work, not just "is the cookie accepted"), reach for `agentic-x doctor` instead — see [CLI Reference](CLI-Reference.md) for what it checks and what `--refresh` does.

## 3. Your first fetch

Pull a profile's tweets:

```bash
$ agentic-x fetch nasa --limit 50 --format json
```

```
50 tweets, range 2026-05-02..2026-07-04, stop reason: limit_reached. Saved to /Users/you/Library/Application Support/agentic-x/output/nasa-20260705T031813123456Z.json
```

`nasa` here can be a bare username, `@nasa`, a numeric user id (pass `--by id` if a bare numeric string should be read as a screen name instead — otherwise an all-digit token is assumed to be an id), or a full profile URL like `https://x.com/nasa`. `--since 2026-06-01`/`--until 2026-06-30` bound the date range, `--wait-on-limit` (optionally with `--max-wait SECONDS`) sleeps out a rate-limit instead of stopping, and `--format ndjson` gives one JSON object per line instead of a single array. `--replies` reads the profile's tweets *and* replies — a different X operation, and one of three that depend on a [generated transaction id](Transaction-ID.md), so it is the least reliable flag here.

Notice where the file landed: **not** your current directory, and not stdout. By default, output goes under this tool's own per-user data directory (via [`platformdirs`](https://pypi.org/project/platformdirs/) — on macOS, `~/Library/Application Support/agentic-x/output/`), named `<identifier>-<UTC timestamp>.json`. That's deliberate: captured tweets contain other people's names, text, and media URLs (real third-party personal data — see [../DISCLAIMER.md §4–5](../../DISCLAIMER.md)), and a default that lands quietly in your current directory is a default that eventually gets `git add`ed by accident. Pass `--output some/path.json` if you want it somewhere specific.

The one-line summary on stderr always tells you three things, so a partial run is never mistaken for a complete one: **how many tweets** were retrieved, the **date range** actually observed (oldest..newest), and the **stop reason** — why the fetch stopped (`limit_reached` here, because `--limit 50` was hit). **Read it — several stop reasons mean "partial" while still exiting 0**, notably `empty_pages` and `browser_observed`. The full table is in the [CLI Reference](CLI-Reference.md#stop-reasons).

### Your own feed

The shortest command in the tool, because it takes no target at all — the feed belongs to your session:

```bash
$ agentic-x feed --limit 20
```

```
20 tweets, range 2026-07-19..2026-07-20, stop reason: limit_reached. Saved to /Users/you/Library/Application Support/agentic-x/output/home-20260720T133040191608Z.json
```

### Search

```bash
$ agentic-x search "artemis" --limit 20 --product latest
```

Anything X's own search box accepts works here — `from:`, `since:`, `-filter:replies`, quoted phrases. `--product top` switches from chronological to X's ranking.

Search is one of three commands that depend on a [generated transaction id](Transaction-ID.md); if it ever exits 4, that page explains what happened and what still works.

### The social graph

```bash
$ agentic-x following nasa --limit 100
```

```
100 accounts, stop reason: limit_reached. Saved to /Users/you/.../following-nasa-20260720T141713348460Z.json
```

`following`, `followers` and `retweeters` write **`User`** objects rather than tweets — see [Output Schema](Output-Schema.md#user-as-a-top-level-result).

### One tweet plus its thread

```bash
$ agentic-x tweet https://x.com/nasa/status/1234567890123456789 --replies
```

```
15 tweets, range 2026-07-01..2026-07-01, stop reason: feed_exhausted. Saved to /Users/you/Library/Application Support/agentic-x/output/https-x-com-nasa-status-1234567890123456789-20260705T032011789012Z.json
```

Accepts a full tweet URL or a bare numeric tweet id. Without `--replies` you just get the one tweet; with it, the saved file also includes replies in that conversation thread.

## 4. A quick look at the output

Open the file and you'll see a JSON array of tweet objects (or one JSON object per line, if you used `--format ndjson`):

```json
[
  {
    "id": "1234567890123456789",
    "url": "https://x.com/nasa/status/1234567890123456789",
    "created_at": "2026-07-01T14:32:10Z",
    "text": "Artemis II is go for launch prep. Full mission timeline: ...",
    "lang": "en",
    "author": {
      "id": "11348282",
      "screen_name": "NASA",
      "name": "NASA",
      "created_at": "2007-12-19T20:20:32Z",
      "followers_count": 96000000,
      "following_count": 400,
      "tweet_count": 75000,
      "is_blue_verified": true,
      "description": "Exploring the universe and our home planet.",
      "url": "https://t.co/abc123"
    },
    "is_reply": false,
    "in_reply_to_id": null,
    "conversation_id": "1234567890123456789",
    "reply_count": 412,
    "retweet_count": 1830,
    "quote_count": 96,
    "like_count": 15200,
    "bookmark_count": 640,
    "view_count": 890000,
    "media": [],
    "urls": [],
    "hashtags": ["Artemis"],
    "is_note_tweet": false,
    "is_pinned": false,
    "retweeted_tweet": null,
    "quoted_tweet": null,
    "is_restricted": false,
    "captured_at": "2026-07-05T03:18:13.385206Z"
  }
]
```

This page won't repeat the full field list — see [Output Schema](Output-Schema.md) for what every field means, including the `media`, `retweeted_tweet`, and `quoted_tweet` shapes.

## Python API equivalent

The same fetch, from Python instead of shelling out to the CLI:

```python
from agentic_x import XScraper

with XScraper(profile="default") as x:
    tweets = x.fetch_user_tweets("nasa", limit=50)

for tweet in tweets:
    print(tweet.id, tweet.text[:80])
```

`XScraper` requires an active session (`x.login()`, or `XScraper.from_cookie_file(path)` — see step 1) and must be used inside a `with` block; reads outside of one raise `NotEnteredError`. `fetch_tweet()` mirrors the CLI's `tweet` subcommand, and `iter_user_tweets()` is a streaming generator form of `fetch_user_tweets()` for consuming tweets incrementally instead of waiting for the whole list. `fetch_home()` mirrors `feed`, `search()` mirrors `search`, and `fetch_following()`/`fetch_followers()`/`fetch_retweeters()` return `User` objects instead of tweets — see the [Python API Reference](Python-API-Reference.md).

## Exit codes

`agentic-x` returns a specific exit code for each outcome, so scripts can branch without parsing stderr text: `0` success, `2` login needed/expired (run `agentic-x login`), `3` rate-limited, `4` what X served no longer matches what this package expects (query-id drift — run `agentic-x doctor --refresh` — or a [transaction-id failure](Transaction-ID.md)), `5` target unavailable (suspended/protected/doesn't exist), `7` finished without confirming `--since` was actually reached. See [CLI Reference](CLI-Reference.md#exit-codes) for the full table.

## What's next

- **[CLI Reference](CLI-Reference.md)** — every flag on every subcommand, the full exit-code table, and what each stop reason implies.
- **[Python API Reference](Python-API-Reference.md)** — the full `XScraper` surface, including `iter_user_tweets` and `from_cookies`/`from_cookie_file`.
- **[Configuration](Configuration.md)** — multiple login profiles, environment variables, and request pacing (`min_request_pause`, `max_requests`) without tripping the non-bypassable floor.
- **[Transaction-ID](Transaction-ID.md)** — worth reading before you depend on `search`, `fetch --replies` or `followers` in anything automated.
- If something looks wrong or a fetch returns 0 tweets, check [FAQ & Troubleshooting](FAQ-and-Troubleshooting.md) before filing an issue.
