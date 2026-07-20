# scraper-for-x — v0.3.0 expansion plan (feed · search · replies · social graph)

**Status:** PLAN (written 2026-07-20). Implementation happens in a *separate* Claude session.
**Companion docs:** [`2026-07-20-recon-findings.md`](2026-07-20-recon-findings.md) (live-captured evidence this plan rests on) and [`IMPLEMENTATION-KICKOFF.md`](IMPLEMENTATION-KICKOFF.md) (the paste-in prompt for the implementation session).

---

## 0. TL;DR / positioning

scraper-for-x (PyPI v0.2.0) is already a **harvest-then-replay** scraper: a stealth-browser (or cookie-import) login harvests the session once (`auth_token`/`ct0`/UA + query-ids/features), then every read is a plain `httpx` GraphQL request — no browser in the hot path. This is the architecture scraper-for-**fb** is only now planning; X is ahead.

The v0.3.0 goal is **not** "rebuild" — it is **unblock the walled ops and widen the surface** so a future `.claude/skills/x-fetch` can chain fast, clean-schema primitives to explore X the way a human does (feed → a post → its author → their replies → search → the social graph). The CLI stays a set of **primitives**; the multi-hop navigation lives in the skill (same division scraper-for-fb settled on).

**The one hard problem** is `x-client-transaction-id` (txid): `SearchTimeline` and `UserTweetsAndReplies` reject any request without a fresh, **single-use** txid header. Everything else (profile timeline, single tweet + thread, and — newly confirmed — the **home feed**) works over plain httpx with no txid. Live recon on 2026-07-20 (see companion doc) proved all of this end-to-end.

---

## 1. Locked decisions (agreed via Q&A, 2026-07-20)

- **D1 — txid strategy: A (pure-Python generation) primary + B (browser-observe) fallback.** Generate a fresh txid per request in Python for the walled ops; if X changes the algorithm and generation breaks (detectable: those ops 404), fall back to driving the stealth browser and reading the organic GraphQL response for *those ops only*. Mirrors scraper-for-fb's "active primary + passive fallback." Maintenance tax on A is real and accepted.
- **D2 — surface scope:** **home feed**, **search + replies/threads**, **social graph** (followers/following, and a tweet's likers/retweeters/quoters). Bookmarks / Lists / Notifications are **out of scope** for v0.3.0.
- **D3 — recon now:** live re-verification was done *this* planning session (throwaway account), not deferred. Findings are in the companion doc and are the empirical basis for every feasibility claim here.
- **D4 — version 0.3.0 (minor) + README reposition.** Additive commands; reposition the README from "harvest-then-replay, search/replies not implemented" to "…now with home feed + search + replies via a generated txid, browser-observe as fallback."
- **D5 — throwaway account, bans acceptable, keep the rate floor.** Same posture as scraper-for-fb. The existing non-bypassable inter-request pause floor (`MIN_REQUEST_PAUSE_SECONDS`, 0.5s) and the "one target per invocation, no daemon/cron" structure stay. Do not add batch/scheduler modes.
- **D6 — parser/schema stay transport-agnostic.** `parse.py`/`model.py` are dict-in, dataclass-out and already generalize. New surfaces add *envelope roots* and *variables builders*, not new parsers, wherever the entries are tweets. (Social graph is the one exception — it returns Users, needing a new output path; see §6/§8.)
- **D7 — the skill (`x-fetch`) is a later, separate session,** after v0.3.0 ships to PyPI. `schema --json` is already "the delegated contract the x-fetch skill reads" (per `pyproject.toml`), so keep the schema subcommand authoritative.

---

## 2. Current state, grounded in recon (2026-07-20)

| Surface | X GraphQL op | Works over httpx **without** txid? | Status in v0.2.0 |
|---|---|---|---|
| Profile timeline | `UserTweets` | ✅ yes | ✅ `fetch` |
| Single tweet + thread | `TweetDetail` | ✅ yes | ✅ `tweet` (query-id was a *placeholder* — now verified, see §recon) |
| Handle → id | `UserByScreenName` | ✅ yes | ✅ internal (resolve/health) |
| **Home feed** | `HomeTimeline` | ✅ **yes (GET *and* POST), 200 `data.home`** | ❌ **no command yet** |
| **Search** | `SearchTimeline` | ❌ **404 (txid wall)** | ❌ `search` errors out (exit 1) |
| **Replies** | `UserTweetsAndReplies` | ❌ **404 (txid wall)** | ❌ `fetch --replies` errors out (exit 1) |
| Social graph | `Followers`/`Following`/`Favoriters`/`Retweeters`/quote-search | ❓ **unprobed** (query-ids not harvested; assume txid-gated until Phase 0 proves otherwise) | ❌ none |

**Two plan-shaping facts from recon:**
1. **The home feed is not behind the wall.** `HomeTimeline` returns `data.home.home_timeline_urt.instructions[TimelineAddEntries].entries[]` — 34 tweets + cursors on a brand-new throwaway account — over plain httpx with the *shipped* query-id. It is the cheapest, highest-value win in the whole plan.
2. **The txid is genuinely single-use.** Replaying a *real captured* txid over httpx still 404s (it was already consumed by the browser's own request). So "capture once, replay" is dead; **fresh per-request generation (option A) is the only httpx path.** The generation *ingredients* (verification meta, 4 loading-animation SVG frames, the `ondemand.s` chunk) are all present on x.com today, so A is feasible.

---

## 3. Architecture

```
                 ┌─────────────────────────────────────────────┐
   login (once)  │  auth.py / session.py                        │
   ─────────────►│  harvest cookies + query-ids/features        │
   browser OR    │  → session.json (0600)                       │
   cookie import └─────────────────────────────────────────────┘
                                    │
      ┌─────────────────────────────┴───────────────────────────┐
      │                 read path (per op)                       │
      │                                                          │
      │  op needs txid?  ──no──►  ReadClient (httpx GET/POST)    │  ← UserTweets, TweetDetail,
      │        │                     │                           │    HomeTimeline, UserByScreenName
      │       yes                    ▼                           │
      │        │              parse.walk_instructions            │
      │        ▼                     │                           │
      │  transaction.py (A)          ▼                           │
      │  generate fresh txid   model.build_tweet                 │
      │        │  (fails? →B)        │                           │
      │        ▼                     ▼                           │
      │  ReadClient + txid    RetrieveResult (list[Tweet])       │  ← SearchTimeline,
      │        │                                                 │    UserTweetsAndReplies
      │   (B fallback) browser-observe → same parser             │
      └──────────────────────────────────────────────────────────┘
```

**One shared parser, one shared pagination loop, one shared schema.** The only genuinely new subsystem is `transaction.py` (txid generation) plus a thin browser-observe fallback. Everything else is "add an envelope root + a variables builder + a fetch_* wrapper + a CLI subcommand."

---

## 4. The txid module (`transaction.py`) — the crux

**Responsibility:** given `(method, path)` for a GraphQL request, return a fresh `x-client-transaction-id` string.

**Design (option A):**
- On first use per session, fetch `https://x.com` HTML over a **cookie-only** httpx client (exactly the pattern `queryids.reanchor_via_main_js` already uses — reuse it; the GraphQL-endpoint headers 401 the plain page). Extract the three inputs:
  1. `<meta name="twitter-site-verification" content="…">` → the verification key (base64).
  2. The four `loading-x-anim-{0..3}` SVG frames (cubic-bezier path data).
  3. The `ondemand.s` chunk → its content-hashed URL → fetch it → regex the `KEY_BYTE_INDICES` (`(\w[<d>], 16)`).
- Cache those three inputs **once per session** (they're stable for a while); generate the per-request txid **locally** from `(method, path, time, animation_key)`. One HTML fetch → many txids → keeps the hot path fast.
- **Do not hand-roll the crypto from scratch.** Port/vendor a known, MIT-licensed pure-Python implementation (the public `x-client-transaction-id` algorithm; twikit integrates the same). Keep it in one file with a clear "reverse-engineered, may rot" docstring and a version stamp of when it was last verified. This is the one module allowed to be "fragile by nature."
- **Single-use is inherent:** generate a new txid for *every* `ReadClient` request to a gated op. Never cache/replay a txid.

**Wiring:** `ReadClient.get()`/`.post()` gain an optional `needs_txid: bool`. When true, call `transaction.generate(method, path)` and set the header. A small `GATED_OPS = {"SearchTimeline", "UserTweetsAndReplies", …probed social-graph ops}` set decides. **Ungated ops must NOT send a txid** (unnecessary, and one more thing to break).

**Fallback (option B), only for gated ops when A fails:** detect failure as an HTTP 404/empty-body from a gated op *after* a successful txid generation (i.e. the header was sent but rejected). Fall back to `StealthySession`: navigate to the op's page (`/search?q=…&f=live`, `/<handle>/with_replies`), capture the organic GraphQL response via `capture_xhr`, hand the *same* body to `parse.walk_instructions`. This reuses the fb-style browser-observe path and the shared parser. Gate B behind the `[browser]` extra; if it's not installed, fail with a clear "install scraper-for-x[browser] or retry later" message.

**Phase-2 gate (critical):** the FIRST implementation step for txid is a throwaway spike — generate one txid in Python and fire ONE `SearchTimeline` → expect HTTP 200 + parseable body. **If that spike can't be made to 200 within a bounded time-box, flip search/replies to B-primary** and file A as a follow-up. Do not sink days into reproducing X's generator.

---

## 5. New / changed modules (surgical)

| File | Change |
|---|---|
| **`transaction.py`** (new) | Option-A txid generator (§4). Pure functions + a small session-scoped cache. Unit-testable against a saved x.com HTML fixture. |
| **`gql.py`** | Add `home_timeline_variables(...)`, `followers/following_variables(...)`, `favoriters/retweeters_variables(...)`. Confirm `SearchTimeline`/`UserTweetsAndReplies` builders (already present, live-captured 2026-07-05) still match Phase-0 re-capture. |
| **`parse.py`** | Add `ENVELOPE_ROOTS["HomeTimeline"] = ("data","home","home_timeline_urt","instructions")`. Add roots for search/replies are already present. Add a **`walk_user_instructions`** for social-graph ops (entryId `user-…`, `content.itemContent.user_results.result`) → returns `(raw_users, cursor)`. |
| **`retrieve.py`** | Remove the two `FeatureNotImplementedError` raises (`search`, `_user_tweets_op(replies=True)`) once txid works. Add `fetch_home(...)`, and `fetch_followers/following(...)` + `fetch_likers/retweeters(...)` driving a **User-list** variant of the pagination loop. `paginate`/`paginate_iter` are already generic; the user-list path is a parallel thin loop (or a `parse_kind` param). |
| **`client.py`** | Add a `post()` method (HomeTimeline's real shape is POST; GET works today but match X for durability) and an optional `needs_txid`/txid-injection hook. Keep the "NOT setting txid by default" invariant for ungated ops. |
| **`session.py`** | `_HARVEST_NAV_URLS`: add a home-feed and a followers/following page so those query-ids get harvested at login. Keep the query-id harvest + re-anchor flow. |
| **`cli.py`** | New subcommands: `feed` (home), unblock `search` and `fetch --replies`, add `following`/`followers`/`likers`/`retweeters` (or a single `graph` subcommand — decide in Phase 4). Wire the new exit-code paths (search/replies no longer exit-1-by-design). |
| **`__init__.py`** (`XScraper`) | Add `fetch_home`, `search` (now real), `fetch_user_tweets(replies=True)` (now real), and the social-graph methods. Keep the `with`-block/iterator contract. |
| **`model.py`** | Likely unchanged for tweets. Social graph outputs **`User`** objects (the dataclass already exists) — add a `user_schema_fields()`-backed output path and, if `feed`/`search` provenance matters, an optional `Tweet.surface` field (see §8). |
| **`README.md` / wiki / `CHANGELOG.md`** | Reposition (D4). Update the "Known limitation" section — search/replies now work; document the txid module + its fragility + the B fallback + the `[browser]`-extra requirement for B. |

---

## 6. Command surface (target)

| Command | Op | txid? | Notes |
|---|---|---|---|
| `scrape-x feed` | `HomeTimeline` | no | **new.** `--limit`, `--format`, cursor pagination. The literal "home feed." |
| `scrape-x fetch <id>` | `UserTweets` | no | unchanged. |
| `scrape-x fetch <id> --replies` | `UserTweetsAndReplies` | **yes** | **unblocked.** |
| `scrape-x search <q>` | `SearchTimeline` | **yes** | **unblocked.** `--product latest\|top`, `--since/--until`. |
| `scrape-x tweet <id> [--replies]` | `TweetDetail` | no | unchanged; deeper thread pagination already supported. |
| `scrape-x following <id>` / `followers <id>` | `Following`/`Followers` | probe | **new**, User-list output. |
| `scrape-x likers <tweetid>` / `retweeters <tweetid>` | `Favoriters`/`Retweeters` | probe | **new**, User-list output. |

Keep every command a **single-target primitive**. The skill chains them (e.g. `feed` → for each author `fetch` → `followers`).

---

## 7. Schema

- **Tweets:** `Tweet`/`User`/`Media` are already rich and transport-agnostic. `feed`/`search`/`replies` all emit `list[Tweet]` — no schema change. HomeTimeline entries carry the same `tweet_results.result` node `build_tweet` already reads.
- **Provenance (optional, decide in Phase 1):** consider adding `Tweet.surface` (`"user_tweets" | "home" | "search" | "thread"`) so a skill chaining outputs knows where a tweet came from. Additive ⇒ still a minor bump (per `model.py`'s stated policy). Only add if the skill genuinely needs it; otherwise skip (CLAUDE.md §2).
- **Social graph:** emit `User` objects. Add a `users` output mode (`--format json|ndjson` of `User.to_dict()`), and extend `schema`/`schema --json` to describe the `User`-list output. This is the one genuinely new output shape.

---

## 8. Safety, rate, ToS

- Keep the **non-bypassable 0.5s inter-request pause floor** and the per-run request budget. Do **not** add batch/daemon/cron (`FAQ` and plan §9 already commit to this — honor it).
- Per-op 15-min rate limits are tight (`UserTweets`/`Search` ≈ 50, `TweetDetail` ≈ 150, `UserByScreenName` ≈ 95; re-confirm `HomeTimeline`/social-graph in Phase 0). `feed`/`search` deep pulls burn these fast — surface exit code 3 + `--wait-on-limit` as-is.
- **X is more litigious and ban-happy than FB.** Keep `DISCLAIMER.md` prominent; the reposition must not soften it. Throwaway account only. Same-IP-as-login guidance stays.
- **PII:** `scratch/`, `*.raw.json`, `output/`, `profiles/` are gitignored. New unit fixtures under `tests/fixtures/*.json` must be **synthetic/scrubbed** (there's already `scripts/check_fixtures_pii.py` — keep new fixtures passing it). Never commit a real capture.

---

## 9. Testing strategy

- **Parity fixtures:** save one real (scrubbed) response per new op under `tests/fixtures/` (`home_timeline.json`, plus refreshed `search_timeline.json`/`user_tweets_and_replies.json`, `followers.json`). Assert `walk_instructions`/`walk_user_instructions` + `build_tweet`/`build_user` produce the expected typed objects. This is the existing test pattern — extend it.
- **txid unit test:** save an x.com HTML fixture; assert `transaction.generate` extracts the three inputs and produces a stable, correctly-shaped txid for a fixed `(method, path, time)`. Mark the fixture with its capture date (it rots).
- **No-network invariant:** `tests/test_no_scrapling_import.py` guards that base-install imports don't pull scrapling. Keep it green — `transaction.py` must be pure-httpx (no browser); only the B fallback imports scrapling, lazily.
- **CLI contract tests:** the `search`/`fetch --replies` "not implemented" markers are currently coupled to exit-1 by a test — that test must be **inverted** (they now work) as part of un-blocking, not left to rot.
- **Run:** `PYTHONPATH=src .venv/bin/python -m pytest -q tests -p no:cacheprovider` (package isn't installed in the venv; the console-script shebang is stale — same workarounds as scraper-for-fb. `git commit --no-verify` if the pre-commit hook can't launch).

---

## 10. Phased implementation (each phase has a verify gate — loop until it passes)

- **Phase 0 — re-verify live (query-ids rotate).** Re-capture fresh query-ids (`scratch/recon_login.py`), re-run the txid-wall probe (`scratch/recon_probe.py`), and **probe the social-graph ops' query-ids + txid-gating** (unprobed this session). Update `queryids.DEFAULT_QUERY_IDS` + `gql` builders from the capture. **Gate:** fresh query-ids in hand; per-op gating table confirmed for today.
- **Phase 1 — home feed (`feed`).** Zero txid, parser reuse. Add envelope root + `home_timeline_variables` + `fetch_home` + `feed` subcommand + fixture/test. **Gate:** `scrape-x feed --limit 20` writes ≥1 tweet; parity test green. *(Cheapest win — do it first to lock the "add a surface" pattern.)*
- **Phase 2 — txid core (`transaction.py`).** Spike first (§4 gate): generate one txid → one `SearchTimeline` 200. Then build the module + `ReadClient` wiring + unit test. **Gate:** spike 200s (or decision recorded to go B-primary); unit test green.
- **Phase 3 — unblock `search` + `fetch --replies`.** Remove the two `FeatureNotImplementedError` raises; wire txid; invert the CLI contract tests; add B fallback behind `[browser]`. **Gate:** `scrape-x search news --limit 20` and `scrape-x fetch <id> --replies --limit 20` both return tweets; fallback path exercised at least once.
- **Phase 4 — social graph.** `walk_user_instructions` + User-list output + `following`/`followers`/`likers`/`retweeters` + `schema` update + fixtures. **Gate:** each returns ≥1 `User`; schema describes the user output.
- **Phase 5 — reposition + polish.** README/wiki/CHANGELOG (D4), `--version` → 0.3.0, enrich `--help`. **Gate:** `scrape-x --version` = 0.3.0; docs describe txid + fallback + new commands honestly (incl. fragility).
- **Phase 6 — the `x-fetch` skill (separate session, post-PyPI).** Installs the published package, drives the primitives, does multi-hop navigation. Not in this repo's v0.3.0 scope.

---

## 11. Risks & honest caveats

| Risk | Likelihood | Mitigation |
|---|---|---|
| **txid generator won't 200 in pure Python today** (algorithm moved since the public impls) | medium — 6mo since my knowledge cutoff; ingredients present but unconfirmed end-to-end | Phase-2 spike-first gate; B (browser-observe) fallback is the safety net; worst case search/replies ship B-primary. |
| **txid generation rots later** (X changes it) | high over time | It's the one "fragile by nature" module; version-stamp it; B fallback keeps the feature alive while A is fixed; `doctor` could grow a txid-health check. |
| Query-id rotation (every 2–4 wks) | certain | Already handled: harvest-at-login + `doctor --refresh`. Phase 0 re-anchors. |
| Social-graph ops gated/different-shaped than assumed | medium (unprobed) | Phase 0 probes them before Phase 4 commits. |
| Home feed thin/empty on a fresh account | low | Confirmed 34 tweets on a brand-new account; parses even when empty (exit 0, not 4). |
| Ban/soft-lock mid-work | medium (X is aggressive) | Throwaway account; keep the rate floor; `status`/`doctor` detect soft-lock; re-login flow exists. |

---

## 12. Pointers for the implementation session

- Recon scripts are preserved under **`scratch/`** (gitignored): `recon_login.py` (headed login w/ cookie-polling, avoids the `input()` hang), `recon_probe.py` (txid-wall probe), `recon_txid_ingredients.py`, `recon_txid_capture.py` (proves single-use).
- A live `recon` profile session may still be valid; else re-login (throwaway).
- Read `2026-07-20-recon-findings.md` for exact query-ids, envelope paths, and the per-op evidence before writing code.
- Honor CLAUDE.md: minimal, surgical, no speculative abstraction. Every new command traces to D2's scope; anything beyond (bookmarks/lists/notifications) is out.
