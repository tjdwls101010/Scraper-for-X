---
name: X retrieval
description: Read X/Twitter via the scrape-x CLI — your home feed, a profile's tweets or replies, a tweet and its thread, search, or the social graph (following/followers/retweeters) — and chain those results to answer multi-hop questions. Use whenever the user wants something off X/Twitter, however they phrase it: "what has <person> been tweeting", "check my timeline", "who retweeted this", "what is <person>'s circle talking about", "search X for <topic>", "look up <name> on Twitter". Also use when the user hands over an x.com or twitter.com URL and wants its contents. NOT for developing or testing the scraper-for-x package itself (that is ordinary repo work), and not for any other social network.
allowed-tools: Bash(scrape-x:*), Bash(uv:*), Bash(pipx:*), Bash(curl:*), Read
---

# X retrieval

`scrape-x` gives you fast, structured retrieval. **You supply the navigation.** The CLI is deliberately a set of single-target primitives with no `crawl` command — deciding which handle to follow next is your job, and it is the whole reason this skill exists.

## Step 1 — get the tool, and get the *current* one

This package tracks a moving target. X rotates its GraphQL query-ids every 2–4 weeks, and the `x-client-transaction-id` that three operations depend on is reverse-engineered and breaks whenever X ships a client build. **Both fixes arrive only as new releases.** So running an old version is not a minor staleness problem here — it is this package's single most likely failure mode, and it presents as "X is broken" rather than as "you're out of date."

That is why you check the version rather than waiting for a symptom. A successful command proves its output is correct *for the build you have*; it says nothing about whether that is the build you should have. Checking costs about 40ms:

```bash
scrape-x catalog | python3 -c "import json,sys; print(json.load(sys.stdin)['version'])"
curl -s https://pypi.org/simple/scraper-for-x/ | grep -oE 'scraper_for_x-[0-9]+\.[0-9]+\.[0-9]+' | sed 's/.*-//' | sort -V | tail -1
```

Note `catalog` takes **no `--json` flag** — it always emits JSON. (`schema` does take one. The asymmetry is a wart; don't let it convince you the command is missing.)

If the installed version is behind, say so in one line and upgrade before doing the user's actual work — don't ask, and don't do it silently, because a mid-task version change has to be diagnosable if results look strange:

> scrape-x 0.3.0 is installed, 0.4.1 is on PyPI — upgrading first, since this package's fixes for X's rotations ship as releases.

```bash
uv tool install --upgrade --no-cache scraper-for-x    # or: pipx upgrade scraper-for-x
```

Do the check **once per task**, not before every command.

Read the PyPI version from the **simple index** as above, not from `pypi.org/pypi/scraper-for-x/json`. Measured: minutes after a release, the JSON endpoint still reported the *previous* version while the simple index was already correct. A check that trusts the JSON API alone can conclude "already latest" about a release that demonstrably exists.

**If it isn't installed at all** (`command not found`):

```bash
uv tool install scraper-for-x        # or: pipx install scraper-for-x
scrape-x setup                       # only if you'll need the browser (login, or the fallback below)
```

Use `uv tool` or `pipx`, **not** `pip install` into a shared virtualenv. The `[browser]` extra depends on `scrapling[fetchers]`, which pins exact Playwright/patchright versions — dropping that into a shared environment can fail to resolve or silently break another Playwright-based tool living there. If neither `uv` nor `pipx` exists, install one rather than reaching for bare `pip`.

A repo checkout and the installed CLI are **different things**: `PYTHONPATH=src python -m scraper_for_x.cli` can be a completely different version from whatever `scrape-x` on PATH resolves to. The catalog's version is the one that counts.

## Step 2 — ask the CLI what it can do

```bash
scrape-x catalog
```

One call gives you every command, its real flags with types and defaults, the exit-code contract, and which object type each command emits. **Work from what it says.**

This file deliberately does not restate that list. The catalog is generated from the CLI's own argument parser, so it is correct for the version actually installed; a table copied into this file would silently describe the wrong version the moment the package updates, and you'd trust the copy over the truth. Anything you need in order to *call* a command comes from the catalog. What follows is only what the catalog cannot carry: how to decide what to call, and how to read what comes back.

If a command you expect is rejected as an `invalid choice`, that is an out-of-date install, not a missing feature — go back to Step 1. Never work around it.

## Step 3 — check the session

```bash
scrape-x status          # exit 0 = ready; exit 2 = needs login
```

If exit 2: **`scrape-x login` opens a real browser window and needs a human to log in.** You cannot complete it; ask the user, then re-check. The account must be a throwaway — see Ban risk.

## The one thing that will trip you up

**Every retrieval command writes its results to a JSON *file* and prints only a one-line summary to stderr. Nothing useful goes to stdout.**

```bash
scrape-x feed --limit 10 --output /tmp/x-feed.json
# stderr: "10 tweets, range 2026-07-19..2026-07-20, stop reason: limit_reached. Saved to /tmp/x-feed.json"
```

Then `Read /tmp/x-feed.json`. Always pass `--output` with a path you choose; without it the file lands under the platform data directory with a timestamped name you'd have to hunt for.

## Two object types, not one

Most commands emit **`Tweet`**. The three social-graph commands — `following`, `followers`, `retweeters` — emit **`User`**. Check which you're holding before you index into it; a `User` has no `text` and a `Tweet` has no `screen_name` at the top level (its author is nested under `author`).

Run `scrape-x schema` for the full field list of both. Prefer that over assuming — it's generated from the code, so it can't drift the way a copy here would.

Two fields that mislead if you skim: `captured_at` is when *you* scraped it, never a sort or dedup key — dedup on `id`, sort on `created_at`. And `created_at` can be `null`, so filter before comparing dates.

## What each primitive is *for*

The catalog gives you the flags; this is the judgment about which to reach for.

- **`fetch <profile>`** — one account's timeline. The only tweet surface with real date filters (`--since`/`--until`), so any "what did X post in <period>" starts here. `--replies` switches to a different operation that also includes their replies.
- **`feed`** — your own home timeline. Takes **no target**: the feed belongs to the logged-in session. Use for "what's happening", never for a question about a specific person.
- **`tweet <url|id>`** — one tweet; `--replies` adds its conversation thread. This is the right tool for a thread, and unlike `fetch --replies` it needs no transaction id.
- **`search <query>`** — discovery, and X's advanced operators work here (`from:`, `since:`, `quoted_tweet_id:` …).
- **`following` / `followers` / `retweeters`** — the social graph, emitting `User`.

## Chaining — the actual work

Every `Tweet` carries `id`, `url` and a nested `author` with `id` and `screen_name`. Every `User` carries `id` and `screen_name`. Those are the handles:

- a tweet's **`author.screen_name`** → `fetch`, `following`, `followers`
- a tweet's **`id`** → `tweet`, `retweeters`
- a `User`'s **`screen_name`** → `fetch`

**"What is X's circle discussing?"** → `fetch X` → collect the `author.screen_name` of accounts they retweet → `fetch` each with a `--limit`.

**"Who amplified this, and what are they into?"** → `retweeters <id> --limit 20` → collect `screen_name` → `fetch` each `--limit 5`.

**"Who quoted this tweet?"** → `search "quoted_tweet_id:<id>"`. There is no `quoters` command because X's own /quotes tab is just a search.

Two rules that keep a chain from becoming a crawl. **Bound the fan-out before you start it** — decide "the top 5 retweeters", not "everyone", because every hop is a real request from a real account (see Ban risk). And **report the shape of what you did**: which hops you took, how many you skipped, and why. A chain that silently sampled 5 of 200 retweeters but presents itself as "what the retweeters think" is a wrong answer wearing a confident summary.

## `stop_reason` — how complete is this result?

The stderr summary ends with a `stop_reason`, and it is the difference between "here is the answer" and "here is part of the answer". Never report a result without having read it.

- **`limit_reached`** — your `--limit` stopped it. There is more.
- **`feed_exhausted`** — genuinely the end of what X will give.
- **`no_matches`** — a search with no hits. Real, report it as such.
- **`empty_pages`** *(graph commands only)* — **we gave up, we did not finish.** X kept handing back cursors with no accounts on them. Real case: `following` on @X returns one account and then empty pages forever. Report the list as incomplete; never present it as someone's full following list.
- **`browser_observed`** — the transaction id was refused and the browser fallback ran. **This is the first page only** — see below.
- **`rate_limited`** / **`max_requests`** — stopped by a budget, not by the data.

Exit **7** is the related trap: it means `--since` was requested but the run stopped before confirming it reached that date. You have *some* tweets in the range; you cannot claim they are all of them.

## Ban risk — why this stays slow

X is more aggressive about bans and legal action than most platforms, and automating an account violates its Terms of Service. Use a **throwaway account**, never a primary one. The package clamps a **≥0.5s floor between requests**, in code, un-bypassable — don't try to work around it, and don't fabricate concurrency by launching several `scrape-x` processes at once, which defeats the floor just as effectively as disabling it.

Rate budgets are per-operation and differ by more than an order of magnitude — roughly 50 per 15 minutes for `fetch`, `search` and `followers`, ~150 for `tweet`, ~500 for `feed`, `following` and `retweeters`. So a deep `search` chain exhausts its budget while the same number of `feed` calls costs almost nothing. Prefer a `--limit` that answers the question over one that exhausts the source, and when a user asks for something genuinely large, say what it will cost before starting rather than discovering it halfway.

If a command returns exit **3** (rate-limited), stop that line of work rather than retrying in a loop. `--wait-on-limit` exists for when waiting is genuinely the right move.

## Third-party data — why the output is sensitive

Scraped output is other people's personal data: display names, handles, bios, follower counts, full tweet text. Collecting it can make the *user* a data controller under GDPR/CCPA. The social-graph commands are the sharp edge — they collect people who never interacted with the user at all.

So: **write output to a temp path, not into the repo**, and never `git add` it. Retrieve the narrowest thing that answers the question. When the task is done, say the files can be deleted, and delete any you created as intermediate steps. Quote individuals only when the question actually needs the quote.

`--raw` embeds the unparsed node and is redacted by default; `--no-redact` disables even that. Neither belongs in normal use — they are debugging aids for working on the scraper itself.

## When something fails

`scrape-x catalog` prints what each exit code *means*. This is what to *do* — and the theme is that most failures here are informative, not transient. **Retrying the same command is rarely the fix.**

**Exit 4 on `search`, `fetch --replies`, or `followers` — the transaction-id generator has rotted.** These three operations are the only ones behind X's `x-client-transaction-id` wall, and the header is generated by reverse-engineered code that X can invalidate with any client deploy. This is the expected end-of-life for a given release, not a bug in your usage.

Do this, in order: **upgrade** (Step 1 — a fix, if one exists, ships as a release); if you're already current, fall back. `search` and `fetch --replies` fall back automatically to driving the browser, if the `[browser]` extra is installed — **that returns the first page only**, and you must say so rather than presenting a truncated result as complete. `followers` has no fallback. Meanwhile everything else keeps working, so re-plan the task around ungated commands: `tweet --replies` still gets you a thread, `following` still works even when `followers` doesn't.

**Exit 4 on anything else** — query-id drift. `scrape-x doctor --refresh` re-anchors the ids from x.com without a browser and usually fixes it in one step.

**Exit 2 — session expired.** Routine; sessions die. Needs a human at a browser (`scrape-x login`). Note that X reports a soft-locked session as HTTP 200 with an empty body, not a clean 401 — so `status` inspects the response, and if it says expired while a browser looks logged in, trust `status`.

**Exit 5 — the account or tweet is unavailable** (suspended, protected, deleted). Not retryable; report it.

**Exit 1 with "invalid choice"** — an out-of-date install. Step 1.

**Not a bug, though it looks like one:** an empty `retweeters` result on a tweet with a visible retweet count. X only exposes a subset, and a tweet with retweets can legitimately return an empty timeline.

**There is no `likers` command, and there will not be.** X removed the surface entirely — `/status/<id>/likes` redirects to the tweet, and the operation appears in none of the JavaScript x.com serves. If the user asks who liked something, say it is no longer available rather than reaching for a workaround; `retweeters` is the closest real signal.
