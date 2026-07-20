import argparse

from scraper_for_x import cli
from scraper_for_x.errors import LoginRequiredError, TransactionIdError


def test_cmd_doctor_handles_unexpected_exception_cleanly(monkeypatch):
    """Regression guard: every other subcommand handler has a last-resort
    `except Exception` boundary that prints a clean, redaction-scrubbed
    message and returns exit 1 -- `_cmd_doctor` previously had none, so e.g.
    a network failure during `--refresh`'s re-anchor step would crash with a
    raw Python traceback instead."""

    def _boom(profile, *, profile_dir_override=None, refresh=False):
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(cli.session, "run_doctor", _boom)
    args = argparse.Namespace(profile="default", profile_dir=None, refresh=True)
    assert cli._cmd_doctor(args) == 1


def test_cmd_doctor_reports_ok_status(monkeypatch):
    monkeypatch.setattr(
        cli.session,
        "run_doctor",
        lambda profile, **kwargs: (True, "OK - authenticated round-trip succeeded"),
    )
    args = argparse.Namespace(profile="default", profile_dir=None, refresh=False)
    assert cli._cmd_doctor(args) == 0


def test_cmd_doctor_reports_failure_status(monkeypatch):
    monkeypatch.setattr(
        cli.session,
        "run_doctor",
        lambda profile, **kwargs: (False, "session check failed: expired"),
    )
    args = argparse.Namespace(profile="default", profile_dir=None, refresh=False)
    assert cli._cmd_doctor(args) == 1


# --- v0.2.0 schema subcommand + dead-path coupling (plan §10a) -----------------

import json  # noqa: E402

import pytest  # noqa: E402


def _help_text(capsys, argv):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(argv)
    return capsys.readouterr().out


def test_schema_exits_0_and_lists_all_three_objects(capsys):
    assert cli.main(["schema"]) == 0
    out = capsys.readouterr().out
    # section headers for all three object types
    assert "Tweet — " in out and "User — " in out and "Media — " in out
    # rendered field lines, not just headers -- guards _print_schema_object's field loop
    assert "id : string" in out
    assert "captured_at : string" in out  # non-null, no --raw note
    assert "created_at : string | null" in out
    assert "raw : object (only present with --raw)" in out
    # the raw-only note must never leak onto an always-present field
    assert "id : string (only present with --raw)" not in out


def test_schema_json_exits_0_and_is_valid_json(capsys):
    assert cli.main(["schema", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["title"] == "Tweet"
    assert set(doc["$defs"]) == {"User", "Media"}


def _fail_if_called(*args, **kwargs):
    raise AssertionError("dead path must not load a session or hit the network")


def _raise_login_required(*args, **kwargs):
    raise LoginRequiredError("no session")


def test_search_help_no_longer_marks_not_implemented(capsys):
    """v0.3.0 generates the transaction id, so search works. The help must not
    still claim otherwise -- but it must stay honest that the id is
    reverse-engineered and can break."""
    out = _help_text(capsys, ["search", "--help"])
    assert "NOT IMPLEMENTED" not in out
    # argparse re-wraps the help text, so assert on a token that cannot break.
    assert "transaction" in out


def test_search_now_reaches_the_session(monkeypatch):
    """Inverted from v0.2.0, where search was rejected AHEAD of the session load
    so a logged-out user got exit 1 ("no such feature"). It is a real command
    now, so a logged-out user must get exit 2 ("log in") like every other read."""
    monkeypatch.setattr(cli.auth, "load_session", _raise_login_required)
    assert cli.main(["search", "artemis"]) == 2


def test_fetch_replies_help_no_longer_marks_not_implemented(capsys):
    out = _help_text(capsys, ["fetch", "--help"])
    assert "NOT IMPLEMENTED" not in out
    # argparse re-wraps the help text, so assert on a token that cannot break.
    assert "transaction" in out


def test_fetch_replies_now_reaches_the_session(monkeypatch):
    monkeypatch.setattr(cli.auth, "load_session", _raise_login_required)
    assert cli.main(["fetch", "@nasa", "--replies"]) == 2


def test_tweet_replies_is_a_working_path_not_marked_not_implemented(capsys):
    # Positive control: tweet --replies WORKS (TweetDetail needs no txid), so its
    # help must NOT carry the not-implemented marker.
    out = _help_text(capsys, ["tweet", "--help"])
    assert "NOT IMPLEMENTED" not in out
    assert "reply/conversation thread" in out


def test_feed_help_lists_its_options_and_takes_no_target(capsys):
    out = _help_text(capsys, ["feed", "--help"])
    assert "NOT IMPLEMENTED" not in out
    assert "--limit" in out


def test_feed_reaches_the_session(monkeypatch):
    monkeypatch.setattr(cli.auth, "load_session", _raise_login_required)
    assert cli.main(["feed"]) == 2


def test_transaction_id_failure_maps_to_exit_4():
    """A rotted generator is "what X serves no longer matches what we expect" --
    the same class of failure as an unparseable envelope, so the same exit
    code, distinguished by the message rather than by a new code."""
    args = argparse.Namespace(profile="default", verbose=False)
    code = cli._handle_common_errors(TransactionIdError("no verification meta tag"), args)
    assert code == 4
