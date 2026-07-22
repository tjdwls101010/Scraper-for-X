# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 0.3.x | ✅ |
| < 0.3 | ❌ |

Only the latest release gets fixes. That is not a formality here: this package reads X through GraphQL operations whose ids rotate every few weeks, and one header it depends on is reverse-engineered and can be invalidated by any X client deploy. **An old install does not degrade gracefully — it stops working.** See [docs/wiki/Transaction-ID.md](docs/wiki/Transaction-ID.md).

## Reporting a vulnerability

**Do not open a public issue for a security report.**

Use GitHub's private vulnerability reporting on this repository: **Security → Report a vulnerability**. It is private to the maintainer and gives us a place to discuss a fix before anything is public.

If that form is unavailable to you, contact the maintainer through their GitHub profile ([@tjdwls101010](https://github.com/tjdwls101010)) and ask for a private channel before sending details.

### What to include

- What the issue is and what an attacker gets out of it.
- Steps to reproduce, and the affected version (`agentic-x --version`).
- Any output you are pasting: run it through the tool's own redaction first. **Never paste `--raw --no-redact` output** — it contains live session fragments and other people's tweet text in the clear. See [docs/wiki/Security-and-Privacy.md](docs/wiki/Security-and-Privacy.md).

### What to expect

This is a single-maintainer project, so no response-time guarantee is offered — one that could not be kept would be worse than none. Reports are read and acknowledged as soon as the maintainer sees them, and coordinated disclosure is the default: a fix ships first, details after.

## What counts as a vulnerability here

This project's sharpest security surface is **the session credential on your own disk**. `session.json` holds a live, password-less X login; its `0600`/`0700` permissions are the entire enforcement mechanism, not encryption. Anything that leaks it, widens its permissions, writes it somewhere unexpected, or exposes it through output or diagnostics is in scope — as is a redaction path that fails to scrub what it claims to scrub.

Out of scope, because they are the documented design rather than defects:

- That the tool violates X's Terms of Service, and that using it risks your account. That is the entire premise — see [DISCLAIMER.md](DISCLAIMER.md).
- That scraped output contains other people's personal data. It does, by definition; the handling guidance is in [docs/wiki/Security-and-Privacy.md](docs/wiki/Security-and-Privacy.md).
- That `--no-redact` produces unredacted output. That is what the flag is for, and it warns every time.
