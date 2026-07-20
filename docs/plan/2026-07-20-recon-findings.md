# scraper-for-x — live recon findings (2026-07-20)

Empirical grounding for [`2026-07-20-active-mode-expansion-plan.md`](2026-07-20-active-mode-expansion-plan.md). Captured against a **throwaway** X account (profile `recon`) on 2026-07-20. **Query-ids rotate every 2–4 weeks — re-capture in Phase 0 before relying on any id below.**

Recon scripts are preserved under `scratch/` (gitignored): `recon_login.py`, `recon_probe.py`, `recon_txid_ingredients.py`, `recon_txid_capture.py`.

---

## 1. Fresh query-ids (captured live, 2026-07-20)

Harvested from the browser XHR stream at login. **Bold** = changed vs the shipped `queryids.DEFAULT_QUERY_IDS`, or previously a *placeholder*.

| Operation | query-id (2026-07-20) | vs v0.2.0 default |
|---|---|---|
| `UserTweets` | **`6r5OLCC_wFH4CpRyXKuAmQ`** | rotated (was `hr4gzZONlq23okjU8fIe_A`) |
| `TweetDetail` | **`rZA6K31W4E90vZKBmxXV3g`** | was an **unverified placeholder** — now real |
| `UserTweetsAndReplies` | **`klja8a2iJX_3to5RdfVlgw`** | was an **unverified placeholder** (copied UserTweets id) — now real |
| `SearchTimeline` | **`hz_94eVAtrtQo_vO3my7Rw`** | rotated (was `Bcw3RzK-PatNAmbnw54hFw`) |
| `UserByScreenName` | `2qvSHpkWTMS9i0zJAwDNiA` | unchanged |
| `HomeTimeline` | `gKia-nBM9kwuDEfSDeWMfQ` | shipped default; **not** freshly captured but **worked** (see §3) |
| `TweetResultByRestId` | `4hhGRbehkcUVTKf8n0f0xw` | bonus — a lighter single-tweet fetch than TweetDetail |

The harvest-at-login path (`queryids.harvest_from_browser`) works: 13 query-ids observed in one login. The `_HARVEST_NAV_URLS` visits (`/X`, `/X/with_replies`, `/search?q=…&f=live`, + open a tweet) successfully triggered `SearchTimeline`, `UserTweetsAndReplies`, and `TweetDetail` captures — the exact ops whose ids were placeholders. **Add a home-feed and a followers/following nav to `_HARVEST_NAV_URLS`** so those ids also get harvested.

---

## 2. The txid wall — per-op probe (httpx, NO x-client-transaction-id)

Fired each op over plain `httpx` with the fresh query-ids and the standard header set (`authorization: Bearer <public>`, `cookie: auth_token/ct0`, `x-csrf-token=ct0`, `x-twitter-*`), **no txid**:

| Op | HTTP | `data`? | verdict |
|---|---|---|---|
| `UserByScreenName` (@X → rest_id `783214`) | **200** | `{user}` | ✅ works |
| `UserTweets` | **200** | `{user}` | ✅ works |
| `TweetDetail` | **200** | `{threaded_conversation_with_injections_v2}` | ✅ works (placeholder id now proven) |
| `HomeTimeline` (GET) | **200** | `{home}` | ✅ works |
| `HomeTimeline` (POST) | **200** | `{home}` | ✅ works |
| `UserTweetsAndReplies` | **404** | empty body | ❌ **txid-gated** |
| `SearchTimeline` | **404** | empty body | ❌ **txid-gated** |

Observed `x-rate-limit-remaining` at probe time: UserByScreenName 146, UserTweets 48, SearchTimeline 48, TweetDetail 148, HomeTimeline 499, UserTweetsAndReplies 498. (The 404s still decremented their own buckets.)

**Conclusion:** the wall is exactly `SearchTimeline` + `UserTweetsAndReplies`, and it is **the txid, not a stale query-id** (fresh ids still 404). Everything else — including the home feed — is open over plain httpx.

---

## 3. Home feed (`HomeTimeline`) — envelope shape

Confirmed structurally, brand-new throwaway account, POST with `{variables, features, queryId}`:

- **Path to instructions:** `data.home.home_timeline_urt.instructions`
- `instructions` = `["TimelineAddEntries"]`
- **37 entries:** 34 `TimelineTimelineItem` (each with `content.itemContent.tweet_results` — the same node `build_tweet` reads), 2 `TimelineTimelineCursor`, 1 `TimelineTimelineModule`.
- Cursor present (Bottom) → pagination works with the existing loop.

**Parser impact:** add one line — `ENVELOPE_ROOTS["HomeTimeline"] = ("data","home","home_timeline_urt","instructions")`. `walk_instructions` already handles `TimelineAddEntries` + `tweet-`/`conversationthread-` entryIds + Bottom cursor; `TimelineTimelineModule` (e.g. who-to-follow) is skipped, which is fine. Variables that worked:

```json
{"count": 20, "includePromotedContent": true, "latestControlAvailable": true,
 "requestContext": "launch", "withCommunity": true}
```

X's real client uses **POST**; **GET also worked** today. Recommend matching X (POST) for durability — needs a small `ReadClient.post()`.

---

## 4. txid is single-use — capture/replay is dead (decisive)

Intercepted a **real** `x-client-transaction-id` (length 94) that X's own client sent on a live `SearchTimeline` request, then replayed that *exact* request URL over httpx **with** the captured txid:

- **Replay WITH captured txid → HTTP 404** (empty body).
- Control WITHOUT txid, same URL → HTTP 404.

The browser already consumed that txid on its own request, so the replay is the 2nd use → rejected. This **confirms single-use live**: "harvest a txid once and replay" cannot work. **Fresh per-request generation (option A) is the only httpx path** — which is exactly why the plan needs `transaction.py`, not a captured-token store.

---

## 5. txid generation ingredients — present on x.com today (option A is feasible)

Fetched `https://x.com` (270 KB) over a cookie-only httpx client and checked the inputs the public reverse-engineered generator needs:

| Ingredient | Found? |
|---|---|
| `<meta name="twitter-site-verification">` (verification key) | ✅ `tRIfm8WqSu71mQ9ktFwfRMIv…` |
| `loading-x-anim-{0..3}` SVG frames | ✅ 4 frames (ids 0,1,2,3), 10 long `<path d=…>` candidates |
| `ondemand.s` chunk reference | ✅ present (webpack chunk `59924:"ondemand.s"`) |

The naive `ondemand.s.<hash>a.js` URL regex missed (the real URL is built from the webpack chunk-hash map — the public libs resolve chunk `59924` → its content hash). That's an implementation detail the known generator handles. **All three ingredient families exist today ⇒ porting/vendoring the public pure-Python txid generator is viable.** What is *not* yet proven end-to-end is "a Python-generated txid → 200" — that is the Phase-2 spike-first gate.

---

## 6. Session/login notes

- The pre-existing `imported` profile session was **expired/soft-locked** (`status` → `expired`) — X's soft-lock returns HTTP-200-empty, not a clean 401; `status`/`check_session_status` catch it correctly.
- Fresh login via `scratch/recon_login.py` worked: headed stealth Chrome (`real_chrome=True`, `init_script` overriding `navigator.webdriver`), **cookie-polling instead of `input()`** (detected login in ~78s with no terminal hang). The v0.2.0 `session.run_login` still uses a blocking `input()` — consider adopting the cookie-poll UX (same fix scraper-for-fb is planning).
- `init_script` **must be an absolute path** (scrapling validates this) — a gotcha for the login code.

---

## 7. Durability

Everything here is a 2026-07-20 snapshot. Query-ids rotate every 2–4 weeks; the txid algorithm can change on any X client deploy. Phase 0 of implementation **must** re-capture query-ids and re-run the txid-wall probe before writing feature code. Treat every id/shape above as "last known good," never as a constant.
