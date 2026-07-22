# Installation

## Two install profiles

`agentic-x` splits into a base install and an optional `[browser]` extra, on purpose (see below).

**Base — reads only, no browser:**

```bash
pip install agentic-twitter
```

Pulls in `httpx` and `platformdirs` only. This is enough to:

- import a session via cookie export (`agentic-x login --cookies ...`) or `XScraper.from_cookies()`/`from_cookie_file()`
- run all read commands (`fetch`, `feed`, `search`, `tweet`, `following`, `followers`, `retweeters`, `status`)
- re-anchor query-ids browser-free (`agentic-x doctor --refresh`), since that goes over `x.com`'s `main.js` via plain `httpx`, not a browser

**With browser — adds the login path:**

```bash
pip install "agentic-twitter[browser]"
```

Adds `scrapling[fetchers]` (and the Playwright/patchright browser engine it drives), which is what `agentic-x login` (without `--cookies`) and `agentic-x setup` need to open a real, stealth-configured browser window for you to log in through.

## Why the split exists

Everything that touches a browser lives behind a lazy import: `scrapling` is only ever imported inside the functions that actually launch a browser (`session.run_login`, `session.run_setup`), never at module level. A base install must be able to `import agentic_x` and do cookie-import reads without `scrapling` — or its Playwright/patchright pin — ever being installed or touched.

This matters because `scrapling[fetchers]` pins exact Playwright/patchright versions to match the browser build it drives. If every install pulled that in unconditionally, a plain `pip install agentic-twitter` into a shared or general-purpose environment could collide with a different Playwright version some other project in that same environment needs. Keeping it an opt-in extra means the base install never carries that risk — it stays two pure-Python HTTP dependencies, full stop.

If you only ever import sessions via cookies, you never need `[browser]` at all.

## Set up the browser

If you installed `[browser]`, provision the login browser next:

```bash
agentic-x setup
```

This provisions an isolated Chromium/Playwright cache into a directory this tool owns exclusively — under `agentic-x`'s own `platformdirs` user-data directory, set via `PLAYWRIGHT_BROWSERS_PATH` — so it never shares or gets confused with a Playwright/patchright browser cache any other tool on your machine manages.

To force a clean reinstall (e.g. it got corrupted, or you're troubleshooting):

```bash
agentic-x setup --force
```

## Verify it worked

```bash
agentic-x --version
```

Confirms the CLI is on your `PATH` and importable.

```bash
agentic-x doctor
```

`doctor` is an authenticated round-trip check, not a version stand-in: it loads your saved session, makes one real GraphQL read against X, and reports whether the session is actually live. It requires a session to already exist (`agentic-x login` first) — it does not launch a browser itself and does not check `setup`'s browser provisioning directly. Add `--refresh` to also re-anchor query-ids via `x.com`'s `main.js` (browser-free — pure `httpx`, works in a base install):

```bash
agentic-x doctor --refresh
```

Exit code `0` means the session is logged in and healthy. Non-zero prints what failed — see [FAQ & Troubleshooting](FAQ-and-Troubleshooting.md).

See [Quick Start](Quick-Start.md) for the full first-run flow (`login` → `doctor` → `fetch`).

## Upgrading

**Staying current matters more here than for most packages.** X rotates the GraphQL query-ids this tool depends on every 2–4 weeks, and the `x-client-transaction-id` header behind `search`/`fetch --replies`/`followers` is reverse-engineered and can be invalidated by any X client deploy. Both repairs ship as **releases**, so an old install does not degrade gently — it stops working, and the symptom looks like "X is broken" rather than "you are out of date."

```bash
pip install --upgrade agentic-twitter
```

To see whether you are behind before something fails:

```bash
agentic-x --version                                       # what you have
curl -s https://pypi.org/simple/agentic-twitter/ \
  | grep -oE 'agentic_twitter-[0-9]+\.[0-9]+\.[0-9]+' | sed 's/.*-//' | sort -V | tail -1
```

Read the installed version from `--version`, which every release has had — not from `agentic-x catalog`, which only exists from 0.3.0 onward and therefore fails on exactly the old installs you are checking for.

Read the published version from the **simple index** shown above rather than `pypi.org/pypi/agentic-twitter/json`. Measured: minutes after a release the JSON endpoint still reported the previous version while the simple index was already correct, so a JSON-only check can call a brand-new release "already latest". The same propagation lag can leave an upgrade one release short — verify with `agentic-x --version` afterwards rather than assuming it landed, and re-run if it did not.

### Installing as a standalone tool

If you only want the CLI and not a library in one of your own environments, install it as an isolated tool instead of into a shared virtualenv:

```bash
uv tool install --upgrade agentic-twitter     # or: pipx install agentic-twitter
```

This matters specifically for the `[browser]` extra: `scrapling[fetchers]` pins exact Playwright/patchright versions, and dropping that into an environment shared with another Playwright-based tool can fail to resolve or quietly break the other one.

## Platform support

| Platform | Status |
|---|---|
| macOS | Tested, first-class target (v1). This is what's actually been validated against a live, logged-in X session. |
| Linux / other | The `httpx` + cookie-import path (base install, no `[browser]`) is plain Python and OS-agnostic in principle, but untested against a live X session outside macOS. |
| Windows | Unsupported in v1. |

## Python version

Requires Python 3.11, 3.12, or 3.13.

---

Next: [Quick Start](Quick-Start.md) walks through first login and your first fetch. For the risks you're taking on before you go further, read [../DISCLAIMER.md](../../DISCLAIMER.md).
