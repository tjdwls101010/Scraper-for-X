import json

import pytest

from agentic_x.auth import parse_cookie_file, validate_token_shapes
from agentic_x.errors import InvalidCookieError

AUTH_TOKEN = "a" * 40
CT0 = "b" * 64


def test_validate_token_shapes_accepts_hex_strings():
    validate_token_shapes(AUTH_TOKEN, CT0)  # must not raise


def test_validate_token_shapes_rejects_non_hex():
    with pytest.raises(InvalidCookieError):
        validate_token_shapes("not-hex!", CT0)


def test_validate_token_shapes_rejects_empty():
    with pytest.raises(InvalidCookieError):
        validate_token_shapes("", CT0)


def test_parse_cookie_file_json_array(tmp_path):
    path = tmp_path / "cookies.json"
    path.write_text(
        json.dumps([{"name": "auth_token", "value": AUTH_TOKEN}, {"name": "ct0", "value": CT0}])
    )
    assert parse_cookie_file(path) == (AUTH_TOKEN, CT0)


def test_parse_cookie_file_json_array_with_non_string_value_is_rejected_cleanly(tmp_path):
    """Regression guard: a cookie export whose JSON value is a non-string
    (e.g. a number, from a hand-edited or unusual export tool) must be
    rejected as InvalidCookieError -- not crash with an unhandled TypeError
    when the malformed value later reaches validate_token_shapes's regex."""
    path = tmp_path / "cookies.json"
    path.write_text(
        json.dumps([{"name": "auth_token", "value": 12345}, {"name": "ct0", "value": CT0}])
    )
    with pytest.raises(InvalidCookieError):
        parse_cookie_file(path)


def test_parse_cookie_file_netscape_format(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        f".x.com\tTRUE\t/\tTRUE\t0\tauth_token\t{AUTH_TOKEN}\n"
        f".x.com\tTRUE\t/\tTRUE\t0\tct0\t{CT0}\n"
    )
    assert parse_cookie_file(path) == (AUTH_TOKEN, CT0)


def test_parse_cookie_file_raw_header_format(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(f"auth_token={AUTH_TOKEN}; ct0={CT0}")
    assert parse_cookie_file(path) == (AUTH_TOKEN, CT0)


def test_parse_cookie_file_missing_required_cookie_reports_names_not_values(tmp_path):
    path = tmp_path / "cookies.json"
    path.write_text(json.dumps([{"name": "auth_token", "value": AUTH_TOKEN}]))
    with pytest.raises(InvalidCookieError) as exc_info:
        parse_cookie_file(path)
    message = str(exc_info.value)
    assert "ct0" in message
    assert AUTH_TOKEN not in message  # never echo a real cookie value, even a present one
