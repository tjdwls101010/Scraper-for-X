"""Defaults and the one non-bypassable guardrail (see plan §9).

Everything here is a soft, overridable default except ``MIN_REQUEST_PAUSE_SECONDS``,
which ``clamp_request_pause`` enforces regardless of what the caller asks for.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import platformdirs

APP_NAME = "agentic-x"

DEFAULT_PROFILE_NAME = "default"

#: Non-bypassable floor: 0-delay reads are both the most ban-inducing setting
#: and the thing that makes this a mass-scraping tool rather than a personal one.
#: A per-process floor only (plan §9) — not a global rate ceiling.
MIN_REQUEST_PAUSE_SECONDS = 0.5

DEFAULT_HUMAN_PAUSE = (1.0, 3.0)

DEFAULT_MAX_REQUESTS = 500

ENV_PROFILE_DIR = "SFX_PROFILE_DIR"


def profile_dir(
    profile: str = DEFAULT_PROFILE_NAME, *, profile_dir_override: str | None = None
) -> Path:
    """Resolve the session credential store location for ``profile`` (plan §7).

    Precedence: an explicit ``profile_dir_override`` wins if given, else the
    ``SFX_PROFILE_DIR`` env var, else the platformdirs default. This function only
    resolves the path — it does not create the directory or set permissions
    (that is ``auth.py``'s job).
    """
    if profile_dir_override is not None:
        base = Path(profile_dir_override)
    else:
        env_override = os.environ.get(ENV_PROFILE_DIR)
        if env_override:
            base = Path(env_override)
        else:
            base = Path(platformdirs.user_data_dir(APP_NAME)) / "profiles"
    return base / profile


def browsers_dir() -> Path:
    """Isolated Playwright browser cache — never shared with any other tool's
    browser install (plan §14)."""
    return Path(platformdirs.user_data_dir(APP_NAME)) / "browsers"


def default_output_dir() -> Path:
    """Never cwd, never a repo — captured tweets carry third-party PII (plan §10)."""
    return Path(platformdirs.user_data_dir(APP_NAME)) / "output"


def clamp_request_pause(min_s: float) -> float:
    """Enforce the non-bypassable minimum inter-request delay.

    A value at or below the floor (including exactly 0) is silently raised to
    it, with a stderr note — this is the one hard limit in the tool, so it must
    actually apply no matter how the value arrives (CLI flag, env, or direct
    API call).
    """
    if min_s <= MIN_REQUEST_PAUSE_SECONDS:
        print(
            f"agentic-x: --min-request-pause {min_s} raised to {MIN_REQUEST_PAUSE_SECONDS} "
            f"(minimum is {MIN_REQUEST_PAUSE_SECONDS}s)",
            file=sys.stderr,
        )
        return MIN_REQUEST_PAUSE_SECONDS
    return min_s
