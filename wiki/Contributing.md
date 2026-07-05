# Contributing

Thanks for considering it. This is a solo-maintained, personal-scale tool, so the bar for contributions is less "does this scale" and more "is this correct, and does it not make the account-ban/PII risk in [../DISCLAIMER.md](../DISCLAIMER.md) worse."

## Dev environment setup

```bash
git clone https://github.com/tjdwls101010/Scraper-for-X.git
cd Scraper-for-X
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pre-commit install
```

`.[dev]` pulls in `pytest`, `ruff`, `pre-commit`, and `build` (see `pyproject.toml`'s `[project.optional-dependencies]`). It does **not** install a browser — that's the separate `[browser]` extra (`uv pip install -e ".[browser,dev]"`), which pulls in `scrapling[fetchers]` and is only needed if you're going to run `scrape-x login`/`setup` (see [Installation](Installation.md)) or exercise live integration tests against a real, logged-in session. Everything else (unit tests, lint, the fixture scan) runs against synthetic fixtures and needs no browser at all.

`pre-commit install` wires up the hooks in `.pre-commit-config.yaml` so lint/format/PII issues get caught locally, before CI does:

- `ruff` (`--fix`) and `ruff-format`, against every file.
- A local `fixture-pii-scan` hook, scoped to `tests/fixtures/*.json`, that runs `scripts/check_fixtures_pii.py` (see below).

## Running tests

```bash
pytest
```

That's it — no flags, no environment variables, no browser. All 81 tests run against the fixtures in `tests/fixtures/*.json`.

### Why fixtures are synthetic, not real captures

Every `.json` file in `tests/fixtures/` (`search_timeline.json`, `tweet_detail.json`, `user_tweets.json`) is **hand-authored** — a synthetic skeleton built to exercise a specific shape `parse.py` needs to handle, not a real X capture with names swapped out. That distinction matters mechanically, not just ethically: `parse.py`'s field paths were confirmed by probing a real, logged-in session and reading what actually came back — the fixtures encode the *shapes* that probing discovered, as inert synthetic data, so the test suite can pin those shapes down without ever holding onto a real capture. If a fixture were "real data with the names changed," it would still contain whatever else was inline in that response — other people's handles, tweet text, signed media URLs — which is exactly what [DISCLAIMER.md](../DISCLAIMER.md) says you should never casually hold onto.

Unlike the fixture rules on some sibling projects, `tests/fixtures/*.json` is **not** gitignored-then-un-ignored-by-name — `.gitignore` only excludes `*.raw.json` (real captures, written under `scratch/`), so a committed fixture and an unsafe raw capture can never collide under one glob. You don't need to touch `.gitignore` to add a new fixture.

### The fixture PII/secret scan

```bash
python scripts/check_fixtures_pii.py
```

This runs automatically in the pre-commit hook and in CI, scoped to every `tests/fixtures/*.json` file. It's a coarse, allowlist-based gate that flags lines matching:

- a real `pbs.twimg.com`/`video.twimg.com` CDN host
- a token-shaped auth field (`auth_token`, `ct0`, `bearer`, `csrf`, `authorization`, `cookie`)
- an email-shaped string
- a phone-shaped string
- a high-entropy (Shannon entropy ≥ 4.0), 40+ character base64/hex-ish run — the shape of a real signed token

**Its own docstring states the honest limitation plainly:** this has no detector for free-text PII. A real person's actual handle, or sensitive tweet content, with none of the patterns above anywhere on the line, passes this scan silently. It exists to catch structural artifacts of a real capture leaking in by accident — not to certify a fixture is safe. **Human review of every fixture diff is still required** before merge; if you're adding or changing a fixture, read the whole diff yourself and make sure nothing in it reads like it came from an actual account.

If the scan flags something you're confident is a deliberately-fake placeholder (e.g. a made-up-but-plausible-looking token string), don't disable the check — change the fixture to use a more obviously-fake value instead.

## Re-anchoring the parser after an X response-shape change

X's internal GraphQL response shape isn't a stable contract — query-ids rotate and field paths can drift without notice (see `scrape-x doctor --refresh` in the [CLI Reference](CLI-Reference.md)). When `fetch`/`search`/`tweet` start returning zero tweets or missing fields on a shape they used to handle, you'll want a real capture to work from: log in with `scrape-x login`, reproduce the call with `--raw` against your own session, and inspect the saved output locally to see what actually changed. Never commit anything captured this way — hand-author a new synthetic fixture (or edit an existing one) in `tests/fixtures/` that reproduces just the shape that broke, the same way the existing fixtures were built; never derive a committed fixture by lightly editing a real capture.

## CI

Every push to `main` and every pull request runs `.github/workflows/ci.yml`, on both `macos-latest` and `ubuntu-latest` (neither leg installs an actual browser binary):

1. Install pinned dev dependencies from `requirements-dev.lock`, then install the package itself with `--no-deps` (dependencies are already pinned).
2. `ruff check .`
3. `ruff format --check .`
4. `python scripts/check_fixtures_pii.py`
5. `pytest`

A separate `build-and-smoke` job (same OS matrix) builds a wheel, installs it into a clean venv, and runs `scrape-x --version` / `scrape-x --help` against it — catching a broken entry point or import error that the fixture-based tests above can't see.

Nothing in CI touches a live X session; nothing in CI can, since there's no logged-in profile available there.

## Release process

Releases are the one part of this workflow where getting the order wrong actually breaks things. Follow this exactly:

1. **Bump the version and changelog.** Edit `version` in `pyproject.toml`, and add a dated entry to `CHANGELOG.md` under a new heading (see the existing `[0.1.0] - 2026-07-05` entry for the format — this project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/)).
2. **Commit and push to `main`.**
3. **Create and publish a GitHub Release** for a tag matching the new version exactly (`vX.Y.Z`, e.g. `v0.2.0` for `pyproject.toml`'s `0.2.0`) — either through the GitHub web UI ("Releases" → "Draft a new release" → pick or create the tag → "Publish release") or with the CLI:

   ```bash
   gh release create v0.2.0
   ```

**Publishing the Release is what triggers the build** — `.github/workflows/publish.yml` fires on the `release: published` event, not on a bare `git push --tags`. Pushing a tag without turning it into a published Release does nothing; the workflow never sees it.

Once triggered, the workflow:

1. Runs `scripts/check_tag_version.py "<tag>"`, which parses `pyproject.toml` and fails the whole run immediately, loudly, if the tag doesn't match — before any build or upload happens. There's no silent partial-publish path; a mismatch is a hard CI failure with an explicit `::error::tag 'vX.Y.Z' does not match pyproject.toml version 'A.B.C'` message.
2. Builds the sdist and wheel (`python -m build`) and uploads them as a workflow artifact.
3. Publishes to PyPI via [`pypa/gh-action-pypi-publish`](https://github.com/pypa/gh-action-pypi-publish), pinned to a specific commit SHA (not a floating version tag) since it runs with publish credentials.

Publishing uses **PyPI Trusted Publishing (OIDC)** — the `publish` job requests `id-token: write` permission and mints a short-lived OIDC token itself. **There is no stored PyPI API token anywhere in this repo**, in any secret or workflow file. If you're setting up a fork or a new maintainer account, configure Trusted Publishing on the PyPI project settings side (linking this GitHub repo and the `publish.yml` workflow), not by adding a token.

If you get the version bump wrong (forgot to bump it, or the tag doesn't match), the workflow fails at step 1 above and nothing gets uploaded — fix `pyproject.toml`/the tag and try the Release again.

## Code style

[`ruff`](https://github.com/astral-sh/ruff) handles both lint and formatting, configured in `pyproject.toml`:

- Line length 100.
- Target Python 3.11.
- Lint rule sets: `E`, `F`, `I`, `UP`, `B`.

```bash
ruff check .
ruff format .
```

`pre-commit install` runs both automatically on every commit; CI runs `ruff check .` and `ruff format --check .` again regardless, so a skipped or bypassed local hook still gets caught.

---

Questions before you send a PR are welcome — open an issue. See [the wiki index](README.md) for the rest of the wiki, or [../README.md](../README.md) for the project overview.
