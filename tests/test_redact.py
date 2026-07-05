from scraper_for_x import redact


def test_sensitive_keys_dropped_entirely():
    scrubbed = redact.redact({"auth_token": "deadbeef" * 8, "ct0": "cafef00d" * 8, "ok": "fine"})
    assert scrubbed["auth_token"] == "[REDACTED]"
    assert scrubbed["ct0"] == "[REDACTED]"
    assert scrubbed["ok"] == "fine"


def test_sensitive_key_matching_is_case_insensitive():
    scrubbed = redact.redact({"Authorization": "Bearer abc123"})
    assert scrubbed["Authorization"] == "[REDACTED]"


def test_text_keys_truncated_not_dropped():
    long_text = "x" * 100
    scrubbed = redact.redact({"text": long_text})
    assert scrubbed["text"] != long_text
    assert scrubbed["text"].startswith("x" * 40)
    assert "redacted 60 more chars" in scrubbed["text"]


def test_short_text_key_passes_through_unchanged():
    scrubbed = redact.redact({"text": "short"})
    assert scrubbed["text"] == "short"


def test_signed_media_url_query_string_stripped():
    url = "https://pbs.twimg.com/media/abc123.jpg?format=jpg&name=large&sig=deadbeef"
    scrubbed = redact.redact({"url": url})
    assert scrubbed["url"] == "https://pbs.twimg.com/media/abc123.jpg"


def test_video_twimg_url_also_stripped():
    url = "https://video.twimg.com/amplify_video/123/vid/720x1280/abc.mp4?tag=12"
    scrubbed = redact.redact({"url": url})
    assert "?" not in scrubbed["url"]


def test_lookalike_host_is_not_treated_as_signed_cdn():
    """Domain-boundary anchoring: `evilpbs.twimg.com.evil.tld` must not match --
    a naive substring check on "pbs.twimg.com" would incorrectly strip an
    unrelated URL's query string (or, worse in the other direction, fail to
    catch a real host with an extra subdomain prefix)."""
    url = "https://evil.tld/pbs.twimg.com?keep=this"
    scrubbed = redact.redact({"url": url})
    assert scrubbed["url"] == url  # unchanged -- not a real twimg host


def test_ordinary_non_cdn_url_passes_through_unchanged():
    url = "https://example.test/article?utm_source=x"
    scrubbed = redact.redact({"url": url})
    assert scrubbed["url"] == url


def test_redact_recurses_into_nested_dicts_and_lists():
    scrubbed = redact.redact({"outer": {"inner": [{"ct0": "abc" * 20}]}})
    assert scrubbed["outer"]["inner"][0]["ct0"] == "[REDACTED]"


def test_redact_raw_text_scrubs_json_shaped_sensitive_fields():
    blob = '{"ct0": "abcdef0123456789", "ok": "fine"}'
    scrubbed = redact.redact_raw_text(blob)
    assert "abcdef0123456789" not in scrubbed
    assert '"ct0":"[REDACTED]"' in scrubbed


def test_redact_raw_text_scrubs_key_value_and_url_shapes():
    blob = "auth_token=deadbeefcafe0123 and see https://pbs.twimg.com/x.jpg?sig=abc"
    scrubbed = redact.redact_raw_text(blob)
    assert "deadbeefcafe0123" not in scrubbed
    assert "sig=abc" not in scrubbed


def test_redact_cookie_parse_error_scrubs_key_value_shape():
    line = "Cookie: auth_token=deadbeefcafe0123; ct0=cafebabe9876"
    scrubbed = redact.redact_cookie_parse_error(line)
    assert "deadbeefcafe0123" not in scrubbed
    assert "cafebabe9876" not in scrubbed
    assert "auth_token=<redacted>" in scrubbed
    assert "ct0=<redacted>" in scrubbed


def test_redact_cookie_parse_error_scrubs_netscape_tsv_shape():
    line = ".x.com\tTRUE\t/\tTRUE\t0\tauth_token\tdeadbeefcafe0123deadbeefcafe0123"
    scrubbed = redact.redact_cookie_parse_error(line)
    assert "deadbeefcafe0123deadbeefcafe0123" not in scrubbed
    assert "<redacted>" in scrubbed


def test_redact_cookie_parse_error_leaves_non_sensitive_content_alone():
    line = "some_unrelated_cookie=hello"
    assert redact.redact_cookie_parse_error(line) == line
