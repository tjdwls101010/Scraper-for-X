# The transaction-id wall

Why three commands (`search`, `fetch --replies`, `followers`) are less reliable than the rest of this tool, what `agentic-x` does about it, and what to do when it breaks. Read this if one of those three started failing, or before you build anything that depends on them.

Everything else in `agentic-x` reads X over plain HTTP with a session cookie and nothing clever. These three do not, and that difference is worth understanding rather than discovering at 2am.

## What the wall is

X's web client sends an `x-client-transaction-id` header on every GraphQL request. Most read operations do not care whether it is there. Three of them reject the request outright without it:

| Operation | Command | Without the header |
|---|---|---|
| `SearchTimeline` | `agentic-x search` | HTTP 404, empty body |
| `UserTweetsAndReplies` | `agentic-x fetch --replies` | HTTP 404, empty body |
| `Followers` | `agentic-x followers` | HTTP 404, empty body |

The 404 is not a "not found" in any meaningful sense — the target exists, the query-id is current, the session is valid. X simply declines to answer.

**The asymmetry is real and was measured, not assumed.** `Following` sits right next to `Followers` in X's own UI and needs no header at all; `tweet --replies` reads a thread through `TweetDetail`, which is also ungated. Three variable shapes were tried against `Followers` and all three 404'd identically while `Following` answered 200 with the first of them (verified 2026-07-20). So this is a per-operation decision on X's side, not a category like "anything about followers" or "anything paginated."

## Why it cannot be harvested once

`agentic-x`'s whole design is harvest-then-replay: log in once with a browser, keep the cookies and query-ids, then never open a browser again. Session cookies last weeks. Query-ids last two to four weeks. Both are worth harvesting.

The transaction id is **single-use**. A real header, intercepted from X's own client mid-request, was replayed over plain HTTP immediately afterwards and returned 404 — X had already spent it on the request it came from. Capture-and-replay is therefore not a slower or riskier option for these three ops; it is not an option.

That leaves generating a fresh one per request, which is what v0.3.0 added.

## How `agentic-x` generates one

The algorithm is reconstructed from X's own client code. `agentic-x` fetches `https://x.com/home` once per session with cookies only, and takes three ingredients out of the page:

1. a `twitter-site-verification` meta tag, base64-decoded into key bytes,
2. four `loading-x-anim-*` SVG frames, whose path geometry drives an animation key,
3. the `ondemand.s` JavaScript chunk, which carries the byte indices the key derivation uses.

Those three are cached for the life of the session. Each request then mixes them with the HTTP method, the request path, and the current time into a fresh id. The ingredients are stable for a while; **the id is never reused**, because reusing it is exactly what does not work.

The implementation is ported from [iSarabjitDhiman/XClientTransaction](https://github.com/iSarabjitDhiman/XClientTransaction) (MIT). Its maths is kept deliberately close to the original so that a future re-port can be diffed against upstream instead of re-derived. Last verified working: **2026-07-20**.

## Be honest with yourself about the reliability

This is the one part of `agentic-x` that is reverse-engineered rather than merely undocumented. X can invalidate it with any client deploy, without notice and without anything resembling a deprecation. It has no stability guarantee and cannot be given one.

What that means practically:

- **The other commands are unaffected.** `feed`, `fetch` (without `--replies`), `tweet`, `following` and `retweeters` do not touch this code path at all. A transaction-id failure is not an outage of the tool.
- **"It worked yesterday" is not evidence it works today.** If you are automating something on top of these three, handle their failure as a normal state rather than an exception.
- **Fixes arrive as releases.** When the algorithm changes, the repair is a new version of this package, not a setting you can flip. Keep the install current — see [Installation](Installation.md#upgrading).

## When it breaks: two different failures

Both surface as **exit code 4**, and they are not the same problem.

### The generator could not run

`TransactionIdError`. x.com no longer serves an ingredient the algorithm needs — the meta tag, the animation frames, or the chunk indices. The message names which one.

```
could not generate an x-client-transaction-id: x.com served no twitter-site-verification meta tag.
This affects search / fetch --replies / followers only; other commands still work.
```

Nothing local fixes this. Check for a newer release; if you are already current, the port needs updating and that is an issue worth filing.

### The id was minted and X refused it

`GatedOpRejectedError`. The header was generated and sent, and X returned 404 anyway — meaning the algorithm still runs but no longer produces something X accepts.

This is the trigger for the fallback below. If the fallback is unavailable, it also ends as exit 4.

### Not to be confused with query-id rotation

Query-id drift also exits 4, and it is a different, far more routine problem with a local fix:

| | Transaction-id failure | Query-id rotation |
|---|---|---|
| Affects | `search`, `fetch --replies`, `followers` | potentially every read |
| Frequency | whenever X changes the algorithm | every 2–4 weeks, reliably |
| Fix | a new release of this package | `agentic-x doctor --refresh`, locally |

If `fetch` and `feed` are failing too, it is rotation, not this. Run `agentic-x doctor --refresh` first — see [FAQ and Troubleshooting](FAQ-and-Troubleshooting.md#i-get-exit-code-4--a-parse-error).

## The browser fallback

When a generated id is refused, `agentic-x search` and `agentic-x fetch --replies` fall back automatically: they drive the stealth browser to the page that fires the operation naturally (`/search?q=…&f=live`, `/<handle>/with_replies`) and parse the response X's own client received. Same parser, same output shape, no special handling required by the caller.

```
SearchTimeline: X refused the generated x-client-transaction-id (the generator has likely rotted).
Falling back to the browser — this returns only the FIRST page.
```

Three constraints worth knowing before you rely on it:

- **It returns one page.** A browser page-load fires the operation once, and there is no cursor to follow without simulating scroll. The run reports `stop_reason: browser_observed` precisely so a truncated result is never mistaken for a complete one — see [Output Schema](Output-Schema.md) and the stop-reason table in the [CLI Reference](CLI-Reference.md#stop-reasons).
- **It needs the `[browser]` extra.** On a base install the fallback cannot run and the command exits 4 with a message saying so.
- **`followers` has no fallback.** The plan scoped the fallback to the two operations that had no working path at all before v0.3.0. If `followers` breaks, `following` is unaffected and is often the question you were actually asking.

## Next

- [CLI Reference](CLI-Reference.md) — the three affected commands in full, and the exit-code table.
- [FAQ and Troubleshooting](FAQ-and-Troubleshooting.md) — what to do when a command fails.
- [Installation](Installation.md) — keeping the install current, which is the real mitigation.

[← Back to the wiki index](README.md)
