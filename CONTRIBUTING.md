# Contributing to agentic-x

Thanks for looking. The full contributor guide — development setup, running the tests, how the synthetic test fixtures work and why they must stay synthetic, and the release process — lives in the wiki:

**→ [docs/wiki/Contributing.md](docs/wiki/Contributing.md)**

The short version:

```bash
git clone https://github.com/tjdwls101010/Agentic-X.git
cd Agentic-X
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
PYTHONPATH=src .venv/bin/python -m pytest -q tests
.venv/bin/python -m ruff check src tests && .venv/bin/python -m ruff format --check src tests
```

Three things worth knowing before your first pull request, because they are specific to this project rather than general good practice:

- **Never commit a real capture.** Every fixture under `tests/fixtures/` is hand-authored synthetic data, not a real X response with the names changed — a lightly-edited real capture still carries other people's handles, tweet text and signed media URLs. `scripts/check_fixtures_pii.py` enforces the mechanical part of this; the judgment part is yours.
- **The base install must not import `scrapling`.** Browser code is lazily imported so that a base install stays dependency-light. `tests/test_no_scrapling_import.py` guards this, and CI checks it against a real built wheel.
- **Read [DISCLAIMER.md](DISCLAIMER.md) before testing against a live account.** Use a throwaway one.

Bugs and feature requests: <https://github.com/tjdwls101010/Agentic-X/issues>. Security issues go through [SECURITY.md](SECURITY.md) instead, never a public issue.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
