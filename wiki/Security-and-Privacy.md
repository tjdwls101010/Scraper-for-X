# Security and Privacy

> **Start with [DISCLAIMER.md](../DISCLAIMER.md).** That file is the authoritative, plain-language summary of the risks you take on by using this tool, and nothing here supersedes it. This page exists to go one level deeper — into the actual mechanics of what's stored, what's scrubbed, and what isn't — for anyone who wants to understand the threat model before pointing this tool at their real account.

None of this is legal advice. If any of it matters to your situation, talk to a lawyer.

## Your session credential is a live, password-less login

`scrape-x login` opens a headed stealth browser, you log in by hand, and the tool harvests the resulting session — `auth_token`, `ct0`, the browser's user-agent, plus the query-ids/features it read off the page — into a small JSON file, `session.json`. `scrape-x login --cookies <file>` (or `XScraper.from_cookies`/`from_cookie_file`) does the same thing without a browser, by importing an already-established session's cookies.

Either way, the result is the same on-disk shape: `{auth_token, ct0, user_agent, query_ids, features}`. Unlike the FB sibling — which persists an entire Playwright browser profile (cookies plus local storage/IndexedDB) — X's credential really is just this one small file. `auth_token` and `ct0` are what matter: whoever holds them has an authenticated, password-less X session, no 2FA prompt required, because your original login already cleared both and this file is just the resulting session state.

**Where it lives:** `platformdirs.user_data_dir("scraper-for-x")/profiles/<profile>/session.json` (macOS: `~/Library/Application Support/scraper-for-x/profiles/default/session.json`). Override with `--profile-dir`/`XScraper(profile_dir=...)` or the `SFX_PROFILE_DIR` env var (see [Configuration](Configuration.md)).

### The 0700/0600 enforcement

Because the credential here is a single file rather than a whole browser-profile directory, `auth.py` hardens both levels:

- `ensure_profile_dir` creates the profile directory (and its `profiles/` parent) at **0700** — owner read/write/execute only. It sets `umask(0o077)` *before* calling `mkdir(parents=True)`, so every directory created in that call is born at 0700 directly rather than sitting briefly at the ambient umask (often 0755, world-readable) before a later `chmod` tightens it. An explicit `chmod` afterward is a second, belt-and-suspenders pass that also corrects a directory a prior run left loose.
- `save_session` writes `session.json` itself at **0600** — owner read/write only — via `os.open(..., os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)`, so the file is never briefly world/group-readable between creation and a later chmod. A follow-up `os.chmod` re-asserts 0600 on every write, independent of umask, on both the login path and the cookie-import path.

Neither of these helps against anyone with root, physical disk access, or a full-disk backup — same limitation as the FB sibling's profile directory. `0700`/`0600` protects against *other unprivileged local users*, nothing more.

### What to actually do about it

- **Never sync, back up, or version this file.** No Time Machine, no iCloud Drive/Dropbox/Google Drive folder, no `git add`. Each of those either encrypts-at-rest with keys you don't control, doesn't encrypt at all, or hands a copy of your live session to a third party — any of which defeats the point of keeping it local and 0600.
- **If the device or disk is lost, stolen, or compromised, don't just delete the file** — deleting your local copy does nothing to a session X's servers still consider valid. Revoke it from X's side: **x.com → Settings → Security and account access → Apps and sessions** (or equivalent), and end that session remotely. That's the only action that actually invalidates the credential.
- If you redirect the store via `--profile-dir`/`SFX_PROFILE_DIR`, the tool still applies 0700/0600 there, but also prints a one-line stderr warning that the credential now lives outside the default protected location — the same "don't sync/share it" rule applies wherever it ends up.
- Multiple profiles (`--profile NAME`) are independent live sessions; the same rules apply to each one individually.

## The cookie-import contract

`scrape-x login --cookies <file>` and `XScraper.from_cookie_file`/`from_cookies` exist so you can carry over a session you already established in a real browser, without ever running this tool's browser. `auth.py` treats that input as untrusted until proven otherwise:

1. **Parse.** `parse_cookie_file` auto-detects three export shapes, in order: a JSON array of `{name, value}` cookie objects (browser-devtools "copy as JSON" / most export extensions), a Netscape cookie file (tab-separated, one cookie per line), or a raw `Cookie:` header / cURL `-H "Cookie: ..."` paste. Whichever shape it lands on, it extracts `auth_token` and `ct0` — nothing else from the export is kept.
2. **Validate token shape before storing.** `validate_token_shapes` checks both values against a permissive-but-real hex-string pattern (`^[0-9a-f]{32,160}$`) before anything is written to disk or fired at X. A value that doesn't look like a real token raises `InvalidCookieError` — the tool never stores or attempts a "best-effort" credential.
3. **Never retain the source file.** Only the extracted `{auth_token, ct0}` (plus a default user-agent, since a cookie-only import has no browser session to harvest a UA from) get written into the 0700/0600 store. The original export file is never copied, moved, or referenced again.
4. **Warn the source is still live.** After a successful import, the tool prints a one-line reminder to stderr that the export file you just pointed it at *still contains a live, password-less X session* and should be deleted or secured — importing doesn't neutralize it.
5. **Parse/validation errors are redaction-safe.** A malformed export doesn't get echoed back at you. Errors report structural/positional context only — "malformed Netscape cookie line 3", "auth_token failed shape check (expected a hex string)" — and if any raw line must appear in a message at all, it first passes through `redact_cookie_parse_error`, which replaces any `auth_token=`/`ct0=`/`bearer `/`cookie:`-shaped key-value pair (in `key=value`, `"key":"value"`, or Netscape-TSV form) with a `<redacted>` marker. The rest of the file's other cookies (session/tracking cookies unrelated to auth) are never dumped either way.

## The redaction system

Everything the tool prints or writes as a *diagnostic* — not the tweets you asked for — goes through one shared scrubbing path in `redact.py`, by design: the module's own docstring calls out that every diagnostic surface must route through it, precisely so a sensitive value doesn't leak through some path someone forgot to scrub. Unlike the FB sibling, this package has two distinct leak surfaces, because it has an input side the FB package never had (cookie-import parsing, above) in addition to the usual response side.

**What routes through redaction:**

- `-v`/`--verbose` diagnostic output
- error messages (login failures, `status`/`doctor` failures, `setup` failures, unexpected errors during `fetch`/`search`/`tweet`)
- `--raw` per-tweet debug output (the raw captured GraphQL `tweet_results.result` node attached to each tweet, including nested `retweeted_tweet`/`quoted_tweet` raw nodes), **by default** — `--raw` alone gives you the scrubbed version
- cookie-import parse/validation errors (see above)
- any other message printed to stdout/stderr by the CLI

**What does NOT route through redaction — deliberately:**

- **Your actual `--output` file.** The tweets you asked for are written out full and unredacted, on purpose — that's the tool's whole reason to exist. A scrubbed version would defeat the point. See [DISCLAIMER.md §5](../DISCLAIMER.md) and treat that file as sensitive from the moment it's written.
- `--raw` output when combined with `--no-redact` — this disables the scrub path entirely for that debug field and prints an on-screen warning (`WARNING: --no-redact leaves --raw output unscrubbed...`) whenever you use it. Only use this locally, for debugging a parser problem, never in a shared terminal or screen recording.

### What it actually scrubs

Reading `redact.py`, the module does the following, all structural/pattern-based rather than semantic:

1. **Session/token-shaped keys.** A fixed set of field names — `auth_token`, `ct0`, `bearer`, `authorization`, `x-csrf-token`, `cookie`, `csrf_token` — are replaced with `[REDACTED]` wherever they appear as a dict key, or as a `key=value`/`"key":"value"` pair inside a raw text blob (for when a whole response body ends up dumped into an error message). These are exactly the fields that would let someone replay your session.
2. **Signed CDN URLs.** `pbs.twimg.com`/`video.twimg.com` media URLs carry a signed, viewer-scoped, time-limited query string — anyone holding one can fetch that specific media as you, until it expires. `redact_url` strips the query string off any URL matching those hosts, leaving the bare path. The host match is domain-boundary-anchored (`(?:^|\.)pbs\.twimg\.com$`, not a bare substring check), so a lookalike host like `evilpbs.twimg.com.evil.tld` isn't falsely treated as trusted, and the real host is never accidentally under-matched and left unredacted.
3. **Free-text fields.** Known text-bearing keys (`text`, `full_text`, `name`, `screen_name`, `description`) longer than 40 characters get truncated to `first 40 chars...[redacted N more chars]` — so a diagnostic dump doesn't reproduce someone's full tweet or full name in your terminal scrollback or a pasted bug report.
4. **Recursive structural scrubbing.** `redact()` walks dicts/lists/strings recursively, applying the above rule-by-rule to every nested value, not just a top-level pass — including the nested raw nodes of retweets and quote-tweets attached to a parent tweet.
5. **Input-side raw-line scrubbing.** `redact_cookie_parse_error` (see cookie-import section above) covers the one shape response-side redaction can't: an unstructured raw line from a cookie export, before it's even parsed into named fields.

### Be honest about its limits

This is pattern matching against a known, fixed set of keys and URL shapes — **it is not a certification that every sensitive value is caught.** If X adds a new token-shaped field with a name this list doesn't know about, or a sensitive value shows up somewhere other than a recognized key/URL shape, it passes through unscrubbed. `--no-redact` (combined with `--raw`) exists precisely because sometimes you need the truly raw node to debug a parser issue — treat anything produced that way as sensitive, ephemeral, and not for sharing. Redaction reduces the chance of an *accidental* leak into a bug report, a terminal screenshot, or scrollback history; it does not make output safe to publish or share.

## Third-party data and "you may become a data controller"

Every tweet you capture belongs to someone else — the author, and often repliers, quoted authors, or anyone mentioned. [DISCLAIMER.md §4](../DISCLAIMER.md) frames this correctly: collecting identifiable personal data about other people can make *you* a data controller under GDPR, with real, not hypothetical, obligations attached — a lawful basis for processing, honoring data-subject access/deletion requests, and limiting retention. The DISCLAIMER also covers where CCPA/CPRA differs (it generally exempts purely personal/household activity, so it typically doesn't attach to a solo scraper) and where jurisdiction-specific privacy-tort risk can still apply regardless. That full legal framing lives in the DISCLAIMER, not here — this page only restates the practical consequence:

- **Retention:** don't keep captured output longer than you actually need it for.
- **Deletion:** delete output files once you're done with whatever you captured them for. There's no built-in expiry or cleanup — the tool writes a file to `--output` (or the default `platformdirs` output directory) and steps away; the deletion decision is yours, on an ongoing basis.
- **Not sharing outputs:** because `--output` is deliberately full and unredacted, never post it, attach it to an issue, paste it into a chat, or otherwise hand it to anyone else — even for something as reasonable-sounding as "can you help me debug this parse." If you need to share a capture for debugging, redact it by hand first, or reduce it to a synthetic/anonymized minimal repro.
- **"Personal use" is not automatically a lawful basis.** The MIT license on this code says nothing about, and does not excuse, whatever privacy-law obligations attach to the *data* you collect with it.

## Account-ban risk, and how to reduce it

Automating an X account — even read-only, over a real logged-in session — violates X's Terms of Service ([DISCLAIMER.md §1](../DISCLAIMER.md)), and X is aggressive about flagging automation. Nothing here eliminates that risk; the tool's guardrails only reduce it:

- **Use a dedicated or throwaway account, never your primary one.** This is the single highest-leverage mitigation, and the DISCLAIMER leads with it for a reason.
- **The non-bypassable pacing floor.** `config.py` enforces a minimum **0.5s** delay between GraphQL reads within a single run — `clamp_request_pause` silently raises anything at or below that floor back up to it, with a stderr note, no matter how the value arrives. This is a *per-process* floor, not a global rate ceiling: nothing stops N separate invocations from running concurrently, and there is no multi-account rotation, proxy-for-scale, or CAPTCHA-solving code to make that a practical mass-scraping path — the guardrail is what discourages bursting from a single invocation and protects your own account, not a hard cap on aggregate throughput. On top of that floor, the read loop adds a randomized human-like pause between reads by default, and a per-run request budget (`DEFAULT_MAX_REQUESTS = 500`) so one invocation can't walk an entire prolific account unattended.
- **Keep volume low.** Shallow, recent fetches are safer than deep archival pulls. A deep `--since`/`--limit` run makes more requests via cursor pagination and raises both rate-limit and account-flag risk — the safe operating point (shallow, recent, low-frequency) and a deep-archival pull pull in opposite directions; that's a real tradeoff, not a bug to fix.
- **Run from the same network/IP where the session was established.** Prefer running `scrape-x` from the same network/egress IP as wherever you ran `scrape-x login`, or wherever you originally exported cookies from. An abrupt IP or client change against an existing session — especially pairing a cookie-imported session with a datacenter/VPN IP — is exactly the kind of signal X's abuse systems weight, and it can soft-lock the session (X returns HTTP 200 with an empty/limited timeline instead of a clean 401) even without an outright ban. `scrape-x status`/`doctor` and the retrieval layer's pre-exit-4 viewer-lookup probe exist partly to detect this state and map it to exit code 2 (`scrape-x login`) rather than a misleading "empty result" or "parse drift" signal.
- **Never run this in a loop or scheduler.** There is no built-in scheduler/daemon, and none will be added — see [DISCLAIMER.md §7](../DISCLAIMER.md).

## If you find a security issue

Open a GitHub issue on the repo (`tjdwls101010/Scraper-for-X`).

Worth keeping in perspective: this is a scraper against a login-walled personal account, not a hosted service. There's no server this project runs, no multi-tenant data store, no API this project exposes to anyone else — it runs entirely on your own machine, against your own already-authenticated session, and writes output only to your own disk. The threat surface is narrow and self-contained by construction; most of what can go wrong is covered above (session-credential exposure, incomplete redaction, or account-ban risk from the automated read pattern itself), not a remote attack on some service this project operates, because no such service exists.
