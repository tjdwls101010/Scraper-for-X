# Disclaimer — read before you use this

This is not legal advice. It is a plain-language summary of risks you take on by using this tool. If any of this matters to your situation, talk to a lawyer.

## 1. This violates X's Terms of Service

X's Terms of Service prohibit scraping and automated access without written permission. X enforces this via account suspension/termination and, historically, litigation. Using this tool is entirely at your own risk. Use a **dedicated or throwaway account**, not your primary one, and keep volume low (§9 of the design doc; the CLI's non-bypassable inter-request floor and per-run scope exist because of this).

## 2. X is notably litigious about scraping — but read the outcomes honestly

X Corp has pursued scraping-related litigation (e.g. *X Corp v. Bright Data*), but the documented outcomes and enforcement have skewed toward **commercial mass-scrapers and data brokers**, not solo personal-scale read use. The probability of that landing on any one individual doing low-volume personal reads is uncertain; the exposure is nonetheless real, and this is named here so the choice stays informed rather than assumed away.

## 3. Publishing this tool exposes its maintainer, not just its users

This package is named `agentic-x`, published under a real GitHub identity, and distributed via PyPI Trusted Publishing, which binds each release to a named GitHub repository and account (`tjdwls101010/Agentic-X`). That is a deliberate, informed choice by the maintainer — but it means the maintainer is identifiable in a way an anonymous or unpublished tool would not be. This exposure exists regardless of how the tool is actually used by others, and is recorded here so the choice stays informed.

## 4. You may become a "data controller" for other people's data

Tweets you scrape belong to other people — authors, repliers, anyone quoted or mentioned. Collecting identifiable personal data about other people can make *you* a **data controller under GDPR**, with real obligations: a lawful basis for processing, honoring data-subject access/deletion requests, and limiting retention. "I did this for personal use" is not automatically a lawful basis.

The **CCPA/CPRA** is different in kind: it regulates for-profit "businesses" that meet statutory thresholds (e.g. revenue or data-volume tests) and generally exempts purely personal/household activity — so it typically does *not* attach to a solo, personal-scale scraper. Even so, jurisdiction-specific privacy-tort and publication-of-private-facts risk can still apply outside of CCPA/CPRA's scope. Minimize what you keep, and delete captured output once you're done with it. The MIT license on this code says nothing about, and does not excuse, privacy-law obligations around the *data* you collect with it. *(Non-legal-advice.)*

## 5. Output files are not scrubbed — treat them as sensitive

Captured tweets contain third-party names, tweet text, and media URLs. This tool:
- never writes output to a location you'd casually commit to git (default `--output` is outside any repo; see README),
- never redacts the *output* files themselves (only diagnostic/verbose logs go through redaction — see below),
- relies on you to delete output you no longer need.

Don't commit scraped output to a public (or even private) git repository, and don't share it beyond what you'd be comfortable being responsible for under §4.

## 6. Diagnostics are redacted — but redaction is not a certification

`-v`/`--verbose` output, error dumps, and anything printed to your terminal are passed through a single redaction path that strips session-token-shaped fields, `pbs.twimg.com`/`video.twimg.com` query strings, and truncates tweet text and names. This includes **cookie-import parse errors** — a malformed cookie export file is reported by line/field position ("line 3 malformed", "ct0 failed shape check"), never by echoing the raw cookie value. This reduces accidental leakage into terminal scrollback, bug reports, or screenshots — it is **not** a guarantee that every sensitive value is caught, and it does not apply to the actual `--output` file, which is the full, unredacted capture by design (that's the point of the tool). `--raw --no-redact` disables this path entirely for debugging; only use it locally.

## 7. Account-ban risk, and how to reduce it

Automating an X account — even just reading, via a real logged-in session — violates X's Terms of Service and X is aggressive about flagging automation. To reduce (not eliminate) the risk of a suspension or challenge:
- Use a **dedicated or throwaway account**, never your primary one.
- Keep volume low: shallow, recent fetches are safer than deep archival pulls. Deeper `--since`/`--limit` runs make more requests and raise both rate-limit and account-flag risk.
- Run `agentic-x` from the **same network/IP** where the session was originally established (`agentic-x login`, or wherever you exported cookies from). An abrupt IP or client change against an existing session — especially pairing a cookie-imported session with a datacenter/VPN IP — is exactly the kind of signal X's abuse systems weight, and can soft-lock the session even without an outright ban.
- Never run this in a loop or scheduler; there is no built-in scheduler/daemon, and none will be added.

## 8. Your session credential is a live, password-less login — protect it

`agentic-x login` (or cookie import) persists your X session as `{auth_token, ct0, user-agent}` to a directory on disk, permissioned `0700` with the credential file itself `0600`. Anyone who can read that file has authenticated, password-less access to your X account — no password or 2FA required, because the session already satisfied both. Concretely:
- **Do not** back this up to Time Machine, sync it via iCloud/Dropbox, or commit it anywhere.
- If you import cookies from an exported file, that source file *also* still contains a live session after import — delete or secure it; this tool never deletes it for you.
- If the machine or disk is lost or compromised, **revoke the session immediately** by logging out of that session on x.com (Settings → Security and account access → Apps and sessions, or equivalent), not just by deleting the local directory.
- A printed reminder from this tool (at login, or on cookie import) is exactly that — a reminder. **It is not a technical control.** Nothing in this tool encrypts the credential at rest or prevents you from copying it elsewhere; the `0700`/`0600` permissions and the reminder are the entire enforcement mechanism.

## 9. No warranty

This software is provided "as is" under the MIT License, without warranty of any kind. See [LICENSE](LICENSE). X's internal API can and does change without notice — query-ids rotate, response shapes drift — and this tool may stop working, or silently return incomplete data, at any time (see the design doc's durability section).
