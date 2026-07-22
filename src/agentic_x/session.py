"""Stealth-browser login + httpx-based status/doctor checks (plan §7, §14, §16).

Two very different halves live here, and they have different dependency
footprints on purpose:

- ``run_login``/``run_setup`` launch a real (stealth) browser and therefore
  import ``scrapling`` -- but only *inside* those functions, lazily, never at
  module top level (plan §14 G-lazy-import). A base install (no ``[browser]``
  extra) must still be able to ``import agentic_x`` and use the
  cookie-import path; a top-level ``import scrapling`` here would break that.
- ``run_status``/``run_doctor``/``check_session_status`` are a "cheap
  authenticated read" over the *httpx* read client (plan §7) -- they import
  no browser dependency at all, so they work in a base install too.
"""

from __future__ import annotations

import os
import subprocess
import sys
from enum import Enum
from pathlib import Path

from . import auth, client, config, errors, gql, queryids

_STEALTH_INIT_SCRIPT = Path(__file__).parent / "_stealth_init.js"

#: The GraphQL XHR pattern to capture during login, for query-id harvesting
#: (plan §8, §12) -- same idea as the FB sibling's CAPTURE_XHR_PATTERN.
_CAPTURE_XHR_PATTERN = r"/graphql/"

#: A stable, always-existing handle used for the "cheap authenticated read"
#: (plan §7) that both `status`/`doctor` and retrieve.py's soft-lock probe
#: rely on -- we don't care about the *content* of the response, only whether
#: X answers it as a logged-in session would.
_HEALTHCHECK_SCREEN_NAME = "x"

#: Public profile pages visited (best-effort) right after a manual login, to
#: fire a couple more GraphQL ops than the home timeline alone would, so
#: `harvest_from_browser` has more to observe (plan §7, §8, §12). Fixed,
#: X-owned handles -- this doesn't depend on (or touch) any particular user's
#: data.
#:
#: `with_replies` fires UserTweetsAndReplies (not just UserTweets); a plain
#: profile/search visit alone never triggers it or TweetDetail (see
#: `_wait_then_harvest`, which additionally opens a tweet found on one of
#: these pages) -- without this, those two ops would NEVER get harvested
#: during a normal login, no matter how many times you log in, and would
#: silently keep falling back to the shipped placeholder query-ids forever.
_HARVEST_NAV_URLS = (
    "https://x.com/X",
    "https://x.com/X/with_replies",
    "https://x.com/search?q=x&f=live",
)


class Status(Enum):
    LOGGED_IN = "logged_in"
    EXPIRED = "expired"
    RATE_LIMITED = "rate_limited"


def browser_profile_dir(profile_dir: Path) -> Path:
    """The stealth browser's own persistent-context directory, nested under the
    profile dir alongside ``session.json`` (plan §7) -- not a separate
    top-level location, so one 0700 tree covers both.
    """
    return profile_dir / "browser"


def query_ids_for(credential: auth.SessionCredential) -> tuple[dict, dict]:
    """The credential's own harvested query-ids/features, falling back to the
    shipped defaults if this session was never harvested/re-anchored (plan §8).

    Merged **per op**, not all-or-nothing: a session harvested before a new op
    existed carries a map that simply lacks it, and returning that map bare
    would make the op unresolvable for every caller. Harvested values still win
    -- they are live, the defaults are only a fallback (§8, §12).
    """
    query_ids = dict(queryids.DEFAULT_QUERY_IDS)
    query_ids.update(credential.query_ids or {})
    features = credential.features or dict(gql.DEFAULT_FEATURES)
    return query_ids, features


def check_session_status(read_client: client.ReadClient, query_ids: dict, features: dict) -> Status:
    """Make one cheap authenticated read and classify the session (plan §7).

    Shared by ``agentic-x status``/``doctor`` and ``retrieve.py``'s pre-exit-4
    soft-lock probe (§11), so all three agree on what "logged in" means.
    """
    query_id = query_ids.get("UserByScreenName", queryids.DEFAULT_QUERY_IDS["UserByScreenName"])
    try:
        body = read_client.get(
            query_id,
            "UserByScreenName",
            gql.user_by_screen_name_variables(_HEALTHCHECK_SCREEN_NAME),
            features,
        )
    except errors.RateLimitedError:
        return Status.RATE_LIMITED
    except errors.SessionExpiredError:
        return Status.EXPIRED

    user_result = (((body or {}).get("data") or {}).get("user") or {}).get("result")
    if not isinstance(user_result, dict) or not user_result.get("rest_id"):
        # A stale/soft-locked session degrades to HTTP 200 with an
        # empty/malformed body rather than a clean 401 (plan §7 G-soft-lock).
        return Status.EXPIRED
    return Status.LOGGED_IN


def run_status(profile: str, *, profile_dir_override: str | None = None) -> Status:
    """Load the persisted session and check it. Raises ``LoginRequiredError``
    if no session has ever been saved for this profile."""
    credential = auth.load_session(profile, profile_dir_override=profile_dir_override)
    query_ids, features = query_ids_for(credential)
    read_client = client.ReadClient(
        credential.auth_token, credential.ct0, credential.user_agent, max_requests=1
    )
    try:
        return check_session_status(read_client, query_ids, features)
    finally:
        read_client.close()


def run_doctor(
    profile: str, *, profile_dir_override: str | None = None, refresh: bool = False
) -> tuple[bool, str]:
    """Authenticated round-trip + (with ``refresh``) a browser-free query-id
    re-anchor (plan §8, §12, §16). Pure httpx -- no browser involved.
    """
    try:
        credential = auth.load_session(profile, profile_dir_override=profile_dir_override)
    except errors.LoginRequiredError as exc:
        return False, str(exc)

    query_ids, features = query_ids_for(credential)
    read_client = client.ReadClient(
        credential.auth_token, credential.ct0, credential.user_agent, max_requests=2
    )
    try:
        status = check_session_status(read_client, query_ids, features)
        if status is not Status.LOGGED_IN:
            return False, f"session check failed: {status.value} (run `agentic-x login`)"

        message = "OK - authenticated round-trip succeeded"
        if refresh:
            fresh = queryids.reanchor_via_main_js(
                credential.auth_token, credential.ct0, credential.user_agent
            )
            credential.query_ids = fresh.query_ids
            credential.features = fresh.features
            auth.save_session(profile, credential, profile_dir_override=profile_dir_override)
            message += f"; re-anchored {len(fresh.query_ids)} query-id(s)"
        return True, message
    finally:
        read_client.close()


def _build_stealth_session(profile_dir: Path, *, headless: bool):
    """Construct a `scrapling.fetchers.StealthySession` matching the exact
    config validated live against X's login flow on 2026-07-05 (plan §7, §17
    G-webdriver-login):

    - ``real_chrome=True`` -> launches with ``channel="chrome"``.
    - patchright-backed (StealthySession's own engine) + scrapling's built-in
      ``STEALTH_ARGS`` (includes ``--disable-blink-features=
      AutomationControlled``) + ``ignore_default_args`` set to scrapling's
      ``HARMFUL_ARGS`` (includes ``--enable-automation``) -- both already
      scrapling defaults, not passed explicitly here.
    - ``init_script`` overrides ``navigator.webdriver`` (the one piece
      scrapling does *not* do by default -- see ``_stealth_init.js``).

    Imports scrapling lazily -- called only from ``run_login``/``run_setup``,
    never at module import time (plan §14 G-lazy-import).
    """
    from scrapling.fetchers import StealthySession

    browser_dir = browser_profile_dir(profile_dir)
    auth.ensure_profile_dir(browser_dir)
    return StealthySession(
        real_chrome=True,
        headless=headless,
        user_data_dir=str(browser_dir),
        capture_xhr=_CAPTURE_XHR_PATTERN,
        init_script=str(_STEALTH_INIT_SCRIPT),
    )


def run_login(profile: str, *, profile_dir_override: str | None = None) -> bool:
    """Headed stealth-browser login, then harvest + persist the session (plan §7).

    The wait for the user to actually finish logging in has to happen INSIDE
    ``page_action`` -- the session closes the page the instant ``fetch()``
    returns, so prompting for input *after* ``fetch()`` would be prompting
    over a window that already closed (same constraint as the FB sibling's
    ``run_login``).
    """
    profile_dir = auth.ensure_profile_dir(
        config.profile_dir(profile, profile_dir_override=profile_dir_override)
    )

    def _wait_then_harvest(page) -> None:
        input(
            "A browser window should now be open. Log in to X there, "
            "then press Enter here to continue... "
        )
        # Best-effort: a few quick, fixed-target navigations so more GraphQL
        # ops (UserTweets/UserTweetsAndReplies/SearchTimeline/UserByScreenName)
        # fire and get captured -- not required (unobserved ops fall back to
        # shipped defaults), just makes the harvest more complete (plan §8, §12).
        for url in _HARVEST_NAV_URLS:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2500)
            except Exception:
                continue

        # Best-effort: also fire TweetDetail. There is no fixed X-owned tweet
        # URL to hardcode (a specific tweet id can get deleted), so instead
        # open whatever tweet link is on the current (search results) page --
        # we only need the query-id this triggers, not any particular tweet's
        # content.
        try:
            href = page.eval_on_selector('a[href*="/status/"]', "el => el.getAttribute('href')")
            if href:
                detail_url = href if href.startswith("http") else f"https://x.com{href}"
                page.goto(detail_url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2500)
        except Exception:
            pass

    with _build_stealth_session(profile_dir, headless=False) as session:
        response = session.fetch(
            "https://x.com/home", page_action=_wait_then_harvest, timeout=60000
        )

    cookie_jar = {
        cookie["name"]: cookie["value"]
        for cookie in response.cookies
        if isinstance(cookie, dict) and "name" in cookie and "value" in cookie
    }
    auth_token = cookie_jar.get("auth_token")
    ct0 = cookie_jar.get("ct0")
    if not auth_token or not ct0:
        return False

    user_agent = response.request_headers.get("user-agent") or auth.DEFAULT_USER_AGENT
    harvested = queryids.harvest_from_browser(response.captured_xhr)

    credential = auth.SessionCredential(
        auth_token=auth_token,
        ct0=ct0,
        user_agent=user_agent,
        query_ids=harvested.query_ids,
        features=harvested.features,
    )
    auth.save_session(profile, credential, profile_dir_override=profile_dir_override)
    return True


def run_setup(*, force: bool = False) -> None:
    """Provision the browser into our isolated `PLAYWRIGHT_BROWSERS_PATH` (plan §14).

    Shells out to scrapling's own install mechanism rather than importing
    scrapling in-process (keeps this function's failure mode -- "scrapling
    not installed, run `pip install agentic-x[browser]`" -- a clean
    ImportError at the call site, mirroring the FB sibling's ``run_setup``).
    """
    config.browsers_dir().mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(config.browsers_dir())
    args = ["install"]
    if force:
        args.append("--force")
    subprocess.run(
        [sys.executable, "-c", "from scrapling.cli import main; main()", *args],
        env=env,
        check=True,
    )
