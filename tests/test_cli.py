import argparse

from scraper_for_x import cli


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
