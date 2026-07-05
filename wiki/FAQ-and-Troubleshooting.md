# FAQ and Troubleshooting

Questions people actually ask about this tool, and the errors people actually hit. If your issue isn't here, check [open issues](https://github.com/tjdwls101010/Scraper-for-X/issues) before filing a new one.

## FAQ

### Will my account get banned?

It can. Read [../DISCLAIMER.md](../DISCLAIMER.md) — it's not boilerplate. Automating any X account, including just driving a real logged-in session to read what it can already see, is against X's Terms of Service, and X enforces that with suspensions, permanent bans, and account-security challenges.

The guardrails in this tool reduce risk, they don't remove it:

- The inter-request pause floor (`MIN_REQUEST_PAUSE_SECONDS`, 0.5s, non-bypassable — a `0` or negative value is silently raised with a stderr note) keeps reads from firing at inhuman speed.
- One target per invocation, no batch mode, no built-in scheduler or daemon loop — nothing here is built to run unattended at volume.
- Deeper `--since`/`--limit` runs make more requests, which raises both rate-limit and account-flag risk in the same breath.
- `scrape-x doctor` lets you check session health without guessing.

None of that makes this safe for your primary account. **Use a dedicated or throwaway X account**, not the one you actually care about. See [DISCLAIMER.md §1 and §7](../DISCLAIMER.md#1-this-violates-xs-terms-of-service).

### Is this legal? Does it violate X's ToS?

It violates X's Terms of Service — that part isn't in question. Whether that's *illegal* depends on your jurisdiction, what you do with the data afterward, and facts specific to your situation, so no honest answer fits in a wiki FAQ. This is not legal advice, and [DISCLAIMER.md](../DISCLAIMER.md) says so explicitly.

Read the whole thing, particularly [§2](../DISCLAIMER.md#2-x-is-notably-litigious-about-scraping--but-read-the-outcomes-honestly) on X's litigation history (enforcement has skewed toward commercial mass-scrapers and data brokers, not solo personal-scale reads — though the exposure is still real) and [§4](../DISCLAIMER.md#4-you-may-become-a-data-controller-for-other-peoples-data) on becoming a "data controller" over other people's data once you've captured it, which carries real weight under GDPR independent of whether scraping itself is illegal where you live. If it matters to your situation, talk to a lawyer.

### Can I run this on a server or VPS?

You can, but it works against you in a specific way: **run `scrape-x` from the same network/IP the session was established on.** If you logged in via `scrape-x login` on your laptop, replay from that same laptop/network. If you imported cookies exported from a browser session on your home connection, replaying that session from a datacenter or VPN IP is exactly the kind of signal X's abuse systems weight — an abrupt IP or client change against an existing session can soft-lock it (`status: expired`) or trigger a security challenge, even without an outright ban. This is X-specific, not a general scraping-tool caution — see [DISCLAIMER.md §7](../DISCLAIMER.md#7-account-ban-risk-and-how-to-reduce-it) and plan §17's `G-ip-origin`.

If you need this running somewhere other than where you logged in, the safer version of "somewhere else" is a machine on the *same* network/egress IP as the login, not a different one.

### How do I revoke access?

Two things, not one:

1. **Log out of that session on x.com itself** — Settings → Security and account access → Apps and sessions (or wherever X currently surfaces active sessions), and end the session associated with this tool. This is the only step that actually invalidates the `auth_token`/`ct0` X-side; everything else is just cleaning up your local copy.
2. **Delete the local profile directory** — `scrape-x` stores the session credential as `session.json` inside your profile directory (default under `platformdirs`' user-data path, or wherever `--profile-dir`/`SFX_PROFILE_DIR` pointed). Deleting it removes the file from disk, but does **not** revoke it on X's side — a copy made before deletion (backup, sync, another machine) would still work until you do step 1.

Do both if the machine or disk holding the credential was ever lost, shared, or possibly compromised. See [DISCLAIMER.md §8](../DISCLAIMER.md#8-your-session-credential-is-a-live-password-less-login--protect-it) — the on-disk file is a live, password-less login, and `0700`/`0600` permissions are the entire enforcement mechanism, not encryption.

## Troubleshooting

Organized by what you're actually seeing.

### `scrape-x login` opens a browser but X won't let me log in

X actively blocks logins from browsers that look automated (`navigator.webdriver == true`), which is why `scrape-x login` uses a stealth-configured real Chrome (`channel="chrome"`, `--disable-blink-features=AutomationControlled`, an init script overriding `navigator.webdriver`, patchright's engine underneath `scrapling`'s `StealthySession`) instead of a vanilla Playwright browser. This config was validated live against X's login flow and should get you through a normal login without friction.

If X still blocks or challenges the login (CAPTCHA, "unusual activity," a phone/email re-verification prompt) even with the stealth config in place, that's worth reporting — it could mean X changed how it fingerprints automated Chrome since this was last validated. Open a GitHub issue with what you saw (the challenge type, not any codes/tokens X showed you) at <https://github.com/tjdwls101010/Scraper-for-X/issues>.

Also make sure you're actually finishing the login: `scrape-x login` opens a real, headed Chrome window and then **blocks on a terminal prompt** — "Log in to X there, then press Enter here to continue..." — waiting for you to finish by hand. It does not time out or close the browser on its own. If you don't see the prompt, check that you're looking at the terminal that ran `scrape-x login`, not just the browser window.

### I get exit code 4 / a parse error

Exit code 4 means the response envelope couldn't be parsed at all — the `instructions`/`entries`/cursor anchors this tool looks for weren't where they're expected, which almost always means **X rotated its GraphQL query-ids**. This is not a bug so much as the operating reality of this entire category of tool: X rotates query-ids roughly **every 2-4 weeks** as a deliberate anti-scraping measure, and any client hardcoding them (this one included, as a fallback) eventually gets a 404 or an empty/malformed response back.

Fix:

```bash
scrape-x doctor --refresh
```

This re-anchors query-ids by fetching X's own `main.js` bundle over plain `httpx` (no browser needed, works in a base install) and regex-extracting the current query-id/feature map for each operation, then persists it into your session. Retry whatever command gave you the 4 afterward.

If `doctor --refresh` itself fails or the 4 persists after refreshing, that's real drift beyond query-ids — X changed the *response shape*, not just the ids — and is worth a GitHub issue with `-v` output attached.

Note this is distinct from a **zero-result** run: an empty feed that parsed fine (envelope located, zero tweets in it) is exit `0` (`feed_exhausted`/`no_matches`), not exit 4. Exit 4 only fires on a genuine structural parse failure.

### `status` says expired but I just logged in

This is X's **soft-lock** behavior: a stale or flagged session doesn't always come back as a clean 401 — it can return HTTP 200 with an empty or malformed body instead, which looks identical to "logged out" from the outside. `scrape-x status`/`doctor` (and the pre-exit-4 probe inside `fetch`/`search`/`tweet`) both check for this specifically, so if `status` reports `expired` right after a login that seemed to succeed, take it at face value rather than assuming it's a false positive — it usually means the session actually degraded (a security challenge fired silently, the account got flagged, or something about the request context, like an IP change, triggered a soft-lock).

Fix is the same as any expired session:

```bash
scrape-x login --profile <name>
```

If this keeps happening right after every fresh login, check whether you're replaying from a different network/IP than the one you logged in on (see "Can I run this on a server or VPS?" above) — that's the most common self-inflicted cause.

### I'm hitting rate limits constantly

X's per-15-minute limits on the read operations this tool uses are genuinely tight:

| Operation | Limit per 15 min |
|---|---|
| `UserTweets` / `UserTweetsAndReplies` | 50 |
| `SearchTimeline` | 50 |
| `TweetDetail` | 150 |
| `UserByScreenName` | 95 |

A single deep `fetch --since` pull against a prolific account can burn through 50 `UserTweets` calls faster than expected, since each page of the timeline is one request. `scrape-x` honors `x-rate-limit-remaining`/`x-rate-limit-reset` and never blind-retries a 429 — by default it stops cleanly with a partial result and exit code `3`. If you'd rather it wait out the window automatically:

```bash
scrape-x fetch nasa --since 2025-01-01 --wait-on-limit --max-wait 900
```

`--wait-on-limit` sleeps until the reset (printing `waiting <N>s until rate-limit reset` so you can tell "waiting" from "hung"); `--max-wait` bounds that single wait. Either way, the real fix for hitting this "constantly" is to lower request frequency and depth: shallower `--since`/smaller `--limit`, and more time between separate invocations. There's no way to raise these limits — they're X's, not this tool's.

### Can I run `scrape-x fetch`/`search` in a loop or cron job?

Don't. There's no built-in scheduler or daemon mode, and none will be added — that's a deliberate structural choice (plan §9), not a missing feature. Wrapping repeated invocations in your own loop/cron defeats the per-run request budget and inter-request pause floor's entire purpose, which is keeping a single invocation from looking like a mass-scraping client. If you need periodic checks, run them manually, infrequently, and watch for exit code `3`/soft-locks as a signal to slow down rather than a queue to retry through.

### `scrape-x setup` fails / can't download the browser

`scrape-x setup` provisions Chromium into this tool's own isolated `PLAYWRIGHT_BROWSERS_PATH` (never a browser install any other tool manages), by shelling out to `scrapling`'s own install mechanism. Failures here are almost always:

- **Network/firewall issue** reaching the Playwright/Chromium download servers — check connectivity, proxy settings, or a corporate firewall blocking the download.
- **Partial or corrupted previous install** — re-run with `--force` to reinstall regardless of what's already there:
  ```bash
  scrape-x setup --force
  ```
- **`scrape-x setup` itself missing** — `setup` (and `login` without `--cookies`) require the `[browser]` extra (`pip install "scraper-for-x[browser]"`). A base install only supports the cookie-import login path.

If it still fails after `--force` with a clean network connection, that's worth a GitHub issue with whatever it printed attached.

### Filing a bug report

Open an issue at <https://github.com/tjdwls101010/Scraper-for-X/issues>. Useful things to include:

- Your OS and Python version, and the `scrape-x --version` output.
- The exact command you ran and its exit code.
- The `-v`/`--verbose` stderr output. This is safe to paste as-is — diagnostics are routed through this tool's single redaction path, which strips session-token-shaped fields, `pbs.twimg.com`/`video.twimg.com` query strings, and truncates tweet text and names before anything reaches your terminal.

**Never paste raw captured tweet output**, and never run with `--raw --no-redact` to generate a bug report. `--raw` alone still redacts the captured tweet's raw node by default; `--no-redact` turns that off entirely and prints an on-screen warning for a reason — the result is other people's tweet text, names, and session fragments in the clear. If you need to show a maintainer what a malformed capture looks like, use the default redacted `--raw` output, not `--no-redact`, and still review it yourself before posting publicly. See [DISCLAIMER.md §6](../DISCLAIMER.md#6-diagnostics-are-redacted--but-redaction-is-not-a-certification) for exactly what redaction does and doesn't guarantee.
