import pytest

from agentic_x.auth import normalize_identifier, normalize_tweet_identifier
from agentic_x.errors import InvalidIdentifierError


def test_bare_all_digit_token_defaults_to_id():
    assert normalize_identifier("123456") == ("id", "123456")


def test_at_prefixed_all_digit_token_forces_screen_name():
    """A leading `@` unambiguously signals "this is a handle", even if the
    rest of the token happens to be all digits (plan §5, §10)."""
    assert normalize_identifier("@123456") == ("screen_name", "123456")


def test_by_screen_name_forces_handle_interpretation_of_all_digit_token():
    assert normalize_identifier("123456", by="screen_name") == ("screen_name", "123456")


def test_by_id_accepts_a_numeric_token():
    assert normalize_identifier("123456", by="id") == ("id", "123456")


def test_by_id_rejects_a_non_numeric_token():
    """Regression guard: `--by id` must not be a silent no-op that falls
    through to handle detection for a non-numeric token -- it should reject,
    since the caller explicitly said "this must be an id"."""
    with pytest.raises(InvalidIdentifierError):
        normalize_identifier("nasa", by="id")


def test_bare_handle():
    assert normalize_identifier("nasa") == ("screen_name", "nasa")


def test_at_prefixed_handle():
    assert normalize_identifier("@nasa") == ("screen_name", "nasa")


def test_profile_url_x_dot_com():
    assert normalize_identifier("https://x.com/nasa") == ("screen_name", "nasa")


def test_profile_url_twitter_dot_com_and_www_subdomain():
    assert normalize_identifier("https://twitter.com/nasa") == ("screen_name", "nasa")
    assert normalize_identifier("https://www.x.com/nasa") == ("screen_name", "nasa")


def test_bare_profile_url_no_scheme():
    assert normalize_identifier("x.com/nasa") == ("screen_name", "nasa")


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/nasa/status/123456789",
        "http://x.com/nasa/status/123456789",
        "x.com/nasa/status/123456789",
        "https://twitter.com/nasa/status/123456789",
        "https://www.x.com/nasa/status/123456789",
        "https://mobile.twitter.com/nasa/status/123456789",
        "https://m.x.com/nasa/status/123456789",
        "https://x.com/i/web/status/123456789",
        "https://x.com/nasa/status/123456789?s=20",
        "https://x.com/nasa/status/123456789/photo/1",
        "https://x.com/nasa/status/123456789/video/1",
        "https://x.com/nasa/status/123456789/analytics",
    ],
)
def test_tweet_url_variants_all_resolve_to_same_id(url):
    assert normalize_identifier(url) == ("tweet_id", "123456789")


def test_rejects_non_x_host_as_anti_ssrf_guard():
    """Plan §10: the host allowlist is what makes the identifier surface
    safe against SSRF -- accepting an arbitrary host would let a caller aim
    the "authenticated" read client at an attacker-controlled URL."""
    with pytest.raises(InvalidIdentifierError):
        normalize_identifier("https://evil.tld/nasa/status/123456789")


def test_rejects_x_dot_com_as_a_path_component_of_another_host():
    with pytest.raises(InvalidIdentifierError):
        normalize_identifier("https://evil.tld/x.com/nasa/status/123456789")


def test_rejects_unsupported_scheme():
    with pytest.raises(InvalidIdentifierError):
        normalize_identifier("ftp://x.com/nasa/status/123456789")


def test_rejects_tweet_url_with_non_numeric_id():
    with pytest.raises(InvalidIdentifierError):
        normalize_identifier("https://x.com/nasa/status/not-a-number")


def test_rejects_url_with_multiple_segments_and_no_status():
    with pytest.raises(InvalidIdentifierError):
        normalize_identifier("https://x.com/nasa/media")


def test_rejects_unrecognized_trailing_suffix():
    with pytest.raises(InvalidIdentifierError):
        normalize_identifier("https://x.com/nasa/status/123456789/some-other-thing")


def test_rejects_empty_identifier():
    with pytest.raises(InvalidIdentifierError):
        normalize_identifier("")
    with pytest.raises(InvalidIdentifierError):
        normalize_identifier("   ")


def test_rejects_handle_over_15_chars():
    with pytest.raises(InvalidIdentifierError):
        normalize_identifier("a" * 16)


def test_rejects_handle_with_invalid_characters():
    with pytest.raises(InvalidIdentifierError):
        normalize_identifier("not a handle!")


# --- normalize_tweet_identifier (the `tweet` subcommand's own identifier
# space -- a URL or a bare numeric id, never a handle; plan §1, §5) ----------


def test_tweet_identifier_bare_numeric_id():
    """Regression guard: `agentic-x tweet 123456789` (a bare numeric tweet id,
    exactly what --help documents as accepted) must actually work -- it
    previously always failed because normalize_identifier defaults a bare
    digit string to a *user* id (kind "id"), not a tweet id."""
    assert normalize_tweet_identifier("123456789") == "123456789"


def test_tweet_identifier_from_url():
    assert normalize_tweet_identifier("https://x.com/nasa/status/123456789") == "123456789"


def test_tweet_identifier_rejects_profile_url():
    with pytest.raises(InvalidIdentifierError):
        normalize_tweet_identifier("https://x.com/nasa")


def test_tweet_identifier_rejects_bare_handle():
    with pytest.raises(InvalidIdentifierError):
        normalize_tweet_identifier("nasa")


def test_tweet_identifier_rejects_non_x_host():
    with pytest.raises(InvalidIdentifierError):
        normalize_tweet_identifier("https://evil.tld/nasa/status/123456789")


def test_tweet_identifier_rejects_empty():
    with pytest.raises(InvalidIdentifierError):
        normalize_tweet_identifier("")
