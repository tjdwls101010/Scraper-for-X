# Output Schema

Every tweet `scrape-x fetch`/`feed`/`search`/`tweet` writes — whether to the default JSON file, `--format ndjson`, or a `Tweet` object from the [Python API](Python-API-Reference.md) — follows the same shape: one `Tweet` per top-level result, with a nested `User` for the author, a list of `Media`, and (optionally) one level of nested `Tweet` for a retweet or quote.

**Three commands write something else entirely.** `following`, `followers` and `retweeters` produce an array of `User` objects at the top level, with no `Tweet` anywhere in the file — see [User as a top-level result](#user-as-a-top-level-result).

This page is a field-by-field reference generated from `src/scraper_for_x/model.py`. If a field here ever disagrees with what you actually get out of the tool, that's a bug.

**Pre-1.0 stability promise** (from `model.py`'s module docstring): fields may be *added* in minor versions, but an existing field's meaning will not be silently reinterpreted. There are also no field defaults except `Tweet.raw` — every field is always present in `to_dict()` output (as `null` if unknown), so you can rely on the key existing even when the value doesn't.

## Contents

- [Tweet](#tweet)
- [User](#user)
- [Media](#media)
- [JSON vs NDJSON output](#json-vs-ndjson-output)
- [Full example: a normal tweet](#full-example-a-normal-tweet)
- [retweeted_tweet and quoted_tweet](#retweeted_tweet-and-quoted_tweet)
- [The raw field](#the-raw-field)
- [Schema gotchas](#schema-gotchas)
- [Datetime fields](#datetime-fields)

## Tweet

| Field | Type | Null? | Meaning |
|---|---|---|---|
| `id` | `string` | never | The tweet's `rest_id`. Stable across fetches — use it as your dedup/merge key. |
| `url` | `string` or `null` | when the author couldn't be resolved | `https://x.com/<screen_name>/status/<id>`. Falls back to `https://x.com/i/web/status/<id>` if there's no author to build a handle-based URL from. |
| `created_at` | `datetime` or `null` | when the timestamp is missing or unparseable | When the tweet was posted, parsed from `legacy.created_at` (X's classic RFC-2822-ish format). See [Schema gotchas](#schema-gotchas) and [Datetime fields](#datetime-fields). |
| `text` | `string` | never (empty string `""` if genuinely empty) | The tweet's full text. Prefers the long-form `note_tweet` text over the legacy 280-char `full_text` when both exist — see `is_note_tweet` below. For a retweet, this is the **original** tweet's text, not the outer `"RT @…"` truncated stub. |
| `lang` | `string` or `null` | when the payload didn't include a language tag | BCP-47-ish language code X detected for the tweet, e.g. `"en"`. |
| `author` | `User` or `null` | when the author node couldn't be resolved (e.g. suspended/deleted account) | The tweet's author. See [User](#user). |
| `is_reply` | `boolean` | never | Whether this tweet is a reply (`legacy.in_reply_to_status_id_str` is set). |
| `in_reply_to_id` | `string` or `null` | when not a reply | The tweet id this one replies to. |
| `conversation_id` | `string` or `null` | when the payload didn't include one | The root tweet id of the thread this tweet belongs to. |
| `reply_count` | `integer` or `null` | when the count couldn't be located in the payload | Number of replies. |
| `retweet_count` | `integer` or `null` | same as above | Number of retweets/reposts. |
| `quote_count` | `integer` or `null` | same as above | Number of quote tweets. |
| `like_count` | `integer` or `null` | same as above | From `legacy.favorite_count`. |
| `bookmark_count` | `integer` or `null` | same as above | Number of bookmarks. |
| `view_count` | `integer` or `null` | when X didn't expose a view count for this tweet | From `views.count`. **X does not always send this** — see [Schema gotchas](#schema-gotchas). |
| `media` | `list[Media]` | never (empty list `[]` if no media) | Photos/videos/GIFs attached to the tweet. For a retweet, this is the **original** tweet's media. See [Media](#media). |
| `urls` | `list[string]` | never (empty list `[]` if no links) | Expanded (not `t.co`-shortened) URLs from the tweet's entities. |
| `hashtags` | `list[string]` | never (empty list `[]` if none) | Hashtag text, without the leading `#`. |
| `is_note_tweet` | `boolean` | never | Whether `text` came from X's long-form `note_tweet` field rather than the legacy 280-char `full_text`. Doesn't imply anything about length by itself — just which source field won. |
| `is_pinned` | `boolean` | never | Whether the tweet was delivered as a profile's pinned tweet (a `TimelinePinEntry` instruction), as opposed to appearing in the normal timeline. |
| `retweeted_tweet` | `Tweet` or `null` | when this tweet isn't a retweet | The retweeted original, as a **full nested `Tweet` object** — same schema, recursively, one level deep only. See [retweeted_tweet and quoted_tweet](#retweeted_tweet-and-quoted_tweet). |
| `quoted_tweet` | `Tweet` or `null` | when this tweet doesn't quote another tweet | The quoted tweet, one level deep only. Same nesting rule as `retweeted_tweet`. |
| `is_restricted` | `boolean` | never | Whether the tweet arrived wrapped in `TweetWithVisibilityResults` — X's marker for subscriber-only/limited-visibility tweets. The wrapper is transparently unwrapped either way; this flag just tells you it was present. |
| `captured_at` | `datetime` | never | When *this tool* captured the GraphQL response containing this tweet — not when the tweet was posted. See [Datetime fields](#datetime-fields). |
| `raw` | `object` or `null` | present only when `--raw`/`raw=True` was requested | The raw `tweet_results.result` node this `Tweet` was parsed from (post-`TweetWithVisibilityResults`-unwrap). Absent (not just `null` — the key doesn't exist in the dict) on a normal run. See [The raw field](#the-raw-field). |

## User

| Field | Type | Null? | Meaning |
|---|---|---|---|
| `id` | `string` | never | The user's `rest_id`. Stable across fetches. |
| `screen_name` | `string` | never (empty string `""` if genuinely missing) | The `@handle`, without the `@`. |
| `name` | `string` or `null` | when the payload didn't include a display name | Display name. |
| `created_at` | `datetime` or `null` | when unparseable/missing | When the account was created. |
| `followers_count` | `integer` or `null` | when the count couldn't be located | Follower count. |
| `following_count` | `integer` or `null` | same as above | From `legacy.friends_count`. |
| `tweet_count` | `integer` or `null` | same as above | From `legacy.statuses_count`. |
| `is_blue_verified` | `boolean` or `null` | when the payload didn't include the flag | Whether the account has X Premium/Blue verification. |
| `description` | `string` or `null` | when there's no bio | Profile bio text. |
| `url` | `string` or `null` | when there's no profile link | The website link on the profile, if any. |

## Media

| Field | Type | Null? | Meaning |
|---|---|---|---|
| `kind` | `string` | never | One of `"photo"`, `"video"`, `"animated_gif"`, or `"unknown"`. |
| `url` | `string` | never (empty string `""` in the rare case neither URL field is present) | The `pbs.twimg.com`/`video.twimg.com` media URL. **This URL is signed and expires** — treat it as sensitive and expect it to stop working after some time. See [DISCLAIMER.md](../../DISCLAIMER.md) and [Security & Privacy](Security-and-Privacy.md). |
| `width` | `integer` or `null` | when the payload didn't include a width | Pixel width, if known. |
| `height` | `integer` or `null` | same as above | Pixel height, if known. |
| `alt_text` | `string` or `null` | when the media has no alt text | Image description text, if the poster added one. |

## JSON vs NDJSON output

`--format json` (the default) writes a single top-level JSON **array** of `Tweet` objects:

```json
[
  { "id": "1001", "...": "..." },
  { "id": "1002", "...": "..." }
]
```

`--format ndjson` writes one `Tweet` object per line instead (no enclosing array, no trailing comma) — useful for streaming/`jq`-per-line processing of large captures:

```
{"id": "1001", "...": "..."}
{"id": "1002", "...": "..."}
```

Both are produced by `_write_output()` in `cli.py` from the exact same `Tweet.to_dict()` — there is no content difference between the two formats beyond the array wrapper vs. newline-delimited framing.

## Full example: a normal tweet

```json
{
  "id": "1001",
  "url": "https://x.com/synth_author/status/1001",
  "created_at": "2026-07-01T12:00:00Z",
  "text": "This is a normal synthetic tweet with a hashtag #synthtag and a link https://example.test/article",
  "lang": "en",
  "author": {
    "id": "9001",
    "screen_name": "synth_author",
    "name": "Synthetic Author",
    "created_at": "2018-01-01T00:00:00Z",
    "followers_count": 100,
    "following_count": 10,
    "tweet_count": 500,
    "is_blue_verified": true,
    "description": "synthetic bio",
    "url": null
  },
  "is_reply": false,
  "in_reply_to_id": null,
  "conversation_id": "1001",
  "reply_count": 1,
  "retweet_count": 2,
  "quote_count": 0,
  "like_count": 5,
  "bookmark_count": 3,
  "view_count": 42,
  "media": [
    {
      "kind": "photo",
      "url": "https://media.example.test/synthetic1.jpg",
      "width": 800,
      "height": 600,
      "alt_text": "a synthetic photo"
    }
  ],
  "urls": ["https://example.test/article"],
  "hashtags": ["synthtag"],
  "is_note_tweet": false,
  "is_pinned": false,
  "retweeted_tweet": null,
  "quoted_tweet": null,
  "is_restricted": false,
  "captured_at": "2026-07-05T03:18:13.385206Z"
}
```

## retweeted_tweet and quoted_tweet

Both are full `Tweet` objects, nested **one level only** — a `retweeted_tweet` or `quoted_tweet` that is itself a retweet/quote of some other tweet will have its own `retweeted_tweet`/`quoted_tweet` as `null`, even if X's actual payload nests deeper.

For a retweet specifically: the outer tweet's own `legacy.full_text` is just a truncated `"RT @handle: …"` stub, so `text`, `media`, `urls`, `hashtags`, and `is_note_tweet` on the **outer** `Tweet` are all resolved from the retweeted original instead — not from the stub. Fields that are inherently about the outer retweet action itself (`id`, `author`, `created_at`, `retweet_count`, etc.) still come from the outer node. Concretely:

```json
{
  "id": "1002",
  "text": "This is a long synthetic tweet that exceeds the classic two hundred eighty character limit and would normally be truncated, but the note_tweet field carries the full, untruncated text instead of the legacy.full_text stub -- this is the text that must win for both the original tweet AND for anything retweeting it.",
  "is_note_tweet": true,
  "author": { "screen_name": "synth_retweeter", "...": "..." },
  "retweet_count": 9,
  "retweeted_tweet": {
    "id": "1002000",
    "text": "This is a long synthetic tweet that exceeds the classic two hundred eighty character limit and would normally be truncated, but the note_tweet field carries the full, untruncated text instead of the legacy.full_text stub -- this is the text that must win for both the original tweet AND for anything retweeting it.",
    "author": { "screen_name": "synth_original", "...": "..." },
    "retweeted_tweet": null,
    "quoted_tweet": null,
    "...": "..."
  },
  "quoted_tweet": null,
  "...": "..."
}
```

Note the outer tweet's `text` and its `retweeted_tweet.text` are identical here — both resolved from the same original — while `id`, `author`, and `retweet_count` differ between the two levels.

`quoted_tweet` is simpler: the quoting tweet keeps its own `text` (whatever the quoter wrote), and `quoted_tweet` holds the quoted tweet's own fields untouched.

## The raw field

`raw` only appears in the JSON when `--raw` (CLI) or `raw=True` (Python API) was passed. When present, it's the raw `tweet_results.result` node (after unwrapping `TweetWithVisibilityResults`, if applicable) exactly as X's GraphQL response sent it — including fields this tool doesn't otherwise expose. `retweeted_tweet.raw` and `quoted_tweet.raw` are populated the same way, independently, since nested `Tweet`s are built with the same `raw` flag.

By default, `--raw` output still has session-token-shaped fields and signed media-URL query strings scrubbed out of `raw` before it's written (see `redact.py` and [Security & Privacy](Security-and-Privacy.md)). `--raw --no-redact` disables that scrubbing entirely — see [DISCLAIMER.md](../../DISCLAIMER.md) before using it. Treat `raw` as sensitive either way.

## Schema gotchas

- **`view_count` can be `null`.** X doesn't always include a `views` object on a tweet, and when it does, `views.count` is sometimes absent or non-numeric. Rather than crash on a malformed page, this tool treats all of those cases as `view_count: null`. Don't assume `null` means zero views — it means X didn't tell you.
- **`created_at` can be `null`.** This is rare, but the raw timestamp is occasionally missing or in a shape `email.utils.parsedate_to_datetime` can't parse. Every other field on the same tweet still parses normally — a bad `created_at` never drops the whole tweet.
- **`is_pinned`** is `true` only for a tweet delivered via a dedicated pin instruction (a profile's pinned tweet), not a general "popular/important" signal.
- **`is_restricted`** is `true` when X wrapped the tweet in `TweetWithVisibilityResults` (subscriber-only or otherwise limited-visibility content). The tweet is still unwrapped and parsed normally; this is just a signal that access to it was gated in some way.
- **`is_note_tweet`** is `true` when the long-form `note_tweet` field won over the legacy 280-char `full_text` for `text`. It says which field the text came from, not that the tweet is unusually long in absolute terms.

## Datetime fields

All datetime fields (`created_at` on both `Tweet` and `User`, plus `captured_at`) are serialized as **ISO 8601 in UTC, with a `Z` suffix** — e.g. `"2026-06-30T09:15:36Z"`. There is no local-timezone output; everything is normalized to UTC before serialization.

`created_at` and `captured_at` answer different questions and are easy to conflate:

- **`created_at`** — when the tweet was posted (or the account was created, for `User.created_at`). Can be `null` — see [Schema gotchas](#schema-gotchas) above.
- **`captured_at`** — when *this tool* captured the GraphQL response that contained this tweet. Never `null`, and typically carries sub-second precision (e.g. `"2026-07-05T03:18:13.385206Z"`) since it's generated locally at parse time rather than read off a payload field. A tweet from 2019 fetched today has a 2019 `created_at` and today's `captured_at`.

If you're deduplicating or diffing across repeated fetches, use `id`, not `captured_at` — `captured_at` differs on every run even for a tweet you've already seen.

## `User` as a top-level result

`following`, `followers` and `retweeters` write a JSON array of [`User`](#user) objects (or one per line with `--format ndjson`) — the same `User` shape that appears nested under `Tweet.author`, just promoted to the top level:

```json
[
  {
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
  }
]
```

Two consequences worth planning for:

- **Check which type you're holding before indexing into it.** A `User` has no `text`; a `Tweet` has no `screen_name` at the top level (its author is nested under `author`). If you merge results from several commands into one pile, the presence of `text` is the cheapest discriminator.
- **A `User` is a handle, not an answer.** It carries who the account is, never what it posted. To get their tweets, feed `screen_name` back into `fetch`.

There is no separate schema command for this shape — `scrape-x schema` already documents `User` in full, because it is the same dataclass either way.

## See also

- [CLI Reference](CLI-Reference.md) — how `--since`/`--until`/`--raw`/`--format` map onto this schema
- [Security & Privacy](Security-and-Privacy.md) — why `media[].url` and `raw` are sensitive
- [../README.md](../../README.md) — the short version of this page, in "Example output"
