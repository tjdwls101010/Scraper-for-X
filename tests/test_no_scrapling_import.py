"""Regression guard for G-lazy-import (plan §14): importing scraper_for_x, and
using its base-install-safe surface, must never pull in scrapling.

The real-world consequence this protects against is a base (no `[browser]`
extra) install crashing on `import scraper_for_x` -- everything from
`scrape-x --version` to a cookie-import login depends on this staying true.
This is a pytest-level regression test alongside the equivalent CI job
(`.github/workflows/ci.yml`'s `build-and-smoke`, which additionally verifies
it against a real built wheel in a clean venv) -- this test catches the same
regression locally and fast, without needing to build/install a wheel.

Runs in a subprocess with a fresh interpreter so it reflects whatever is
actually importable in *this* environment, not just whether some other test
file happened to import scrapling first and pollute `sys.modules`.
"""

from __future__ import annotations

import subprocess
import sys


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)


def test_import_does_not_pull_in_scrapling():
    result = _run("import scraper_for_x, sys; assert 'scrapling' not in sys.modules")
    assert result.returncode == 0, result.stderr


def test_cli_module_import_does_not_pull_in_scrapling():
    result = _run("import scraper_for_x.cli, sys; assert 'scrapling' not in sys.modules")
    assert result.returncode == 0, result.stderr


def test_auth_and_session_modules_do_not_pull_in_scrapling():
    """auth.py and session.py are the two modules the login/cookie-import path
    touches most -- session.py in particular is where the lazy import lives
    (inside `_build_stealth_session`/`run_setup`), so importing it bare must
    not trigger it."""
    result = _run(
        "import scraper_for_x.auth, scraper_for_x.session, sys; "
        "assert 'scrapling' not in sys.modules"
    )
    assert result.returncode == 0, result.stderr
