"""Unit tests for the x-client-transaction-id port.

Everything here runs against a **synthetic** page assembled in-process, never a
captured x.com response: the real page is ~270 KB of logged-in HTML and has no
business in a fixture directory. That means these tests pin the *port* (the
extraction and the maths), not X's current algorithm -- only a live request can
tell you the algorithm still matches, which is why `transaction`'s docstring
carries a last-verified date.
"""

from __future__ import annotations

import base64

import pytest

from agentic_x import transaction

# key_bytes = 0..47, so the derived indices are easy to follow:
#   key_bytes[5] % 4  == 1  -> frame 1 is the one read
#   key_bytes[24] % 16 == 8  -> row 8 of that frame's second path
KEY_BYTES = list(range(48))
KEY = base64.b64encode(bytes(KEY_BYTES)).decode()

ROW_INDEX_KEY = 24
BYTE_INDICES = [29, 31, 22]

# 11 numbers per segment: 3 "from" colour, 3 "to" colour, 1 rotation, 4 curves.
_SEGMENTS = [" ".join(str((row * 11 + n) % 256) for n in range(11)) for row in range(12)]
# The first 9 characters are sliced off before splitting on "C" (upstream's
# `d[9:]`), so the prefix just has to be 9 characters of anything path-shaped.
_PATH_D = "M0 0 C 0 " + "C".join(_SEGMENTS)


def _page() -> str:
    frames = "".join(
        f'<svg id="loading-x-anim-{i}"><g><path d="M1 1"/><path d="{_PATH_D}"/></g></svg>'
        for i in range(4)
    )
    return (
        "<html><head>"
        f'<meta name="twitter-site-verification" content="{KEY}"/>'
        "</head><body>"
        f"{frames}"
        '<script>e={,59924:"ondemand.s",59924:"deadbeef"}</script>'
        "</body></html>"
    )


_ONDEMAND_JS = (
    "function d(a){return parseInt(a[24], 16)+parseInt(a[29], 16)"
    "+parseInt(a[31], 16)+parseInt(a[22], 16)}"
)


def test_extract_frame_paths_reads_all_four_frames():
    frames = transaction.extract_frame_paths(_page())
    assert sorted(frames) == [0, 1, 2, 3]
    # Both <path> children of the first <g>, in document order -- the caller
    # indexes [1], matching upstream's list(...)[1].
    assert frames[1] == ["M1 1", _PATH_D]


def test_extract_frame_paths_ignores_unrelated_elements():
    html = '<div id="not-a-frame"><g><path d="X"/></g></div>' + _page()
    frames = transaction.extract_frame_paths(html)
    assert sorted(frames) == [0, 1, 2, 3]


def test_indices_regex_finds_key_byte_indices():
    indices = [int(m.group(2)) for m in transaction._INDICES_RE.finditer(_ONDEMAND_JS)]
    assert indices == [ROW_INDEX_KEY, *BYTE_INDICES]


def test_compute_animation_key_is_deterministic():
    frames = transaction.extract_frame_paths(_page())
    first = transaction.compute_animation_key(KEY_BYTES, frames, ROW_INDEX_KEY, BYTE_INDICES)
    second = transaction.compute_animation_key(KEY_BYTES, frames, ROW_INDEX_KEY, BYTE_INDICES)
    assert first == second
    # Hex-ish, punctuation stripped by _animate's final re.sub.
    assert first and all(c in "0123456789abcdef" for c in first)


def test_compute_animation_key_rejects_a_frame_without_two_paths():
    frames = {1: ["only-one"]}
    with pytest.raises(transaction.TransactionIdError, match="expected >= 2"):
        transaction.compute_animation_key(KEY_BYTES, frames, ROW_INDEX_KEY, BYTE_INDICES)


def _prepared(monkeypatch, *, now: float = 1_800_000_000.0, noise: int = 7):
    """A ClientTransaction with its ingredients pre-loaded, so no network runs."""
    client = transaction.ClientTransaction("token", "ct0", "ua")
    client._key_bytes = KEY_BYTES
    client._animation_key = "abc123"
    monkeypatch.setattr(transaction.time, "time", lambda: now)
    monkeypatch.setattr(transaction.random, "randint", lambda a, b: noise)
    return client


def test_generate_is_stable_for_a_fixed_time_and_noise(monkeypatch):
    client = _prepared(monkeypatch)
    first = client.generate("GET", "/i/api/graphql/QID/SearchTimeline")
    second = client.generate("GET", "/i/api/graphql/QID/SearchTimeline")
    assert first == second


def test_generate_signs_the_path_so_two_ops_differ(monkeypatch):
    client = _prepared(monkeypatch)
    search = client.generate("GET", "/i/api/graphql/QID/SearchTimeline")
    replies = client.generate("GET", "/i/api/graphql/QID/UserTweetsAndReplies")
    assert search != replies


def test_generate_signs_the_method(monkeypatch):
    client = _prepared(monkeypatch)
    assert client.generate("GET", "/p") != client.generate("POST", "/p")


def test_generate_varies_with_the_random_noise_byte(monkeypatch):
    first = _prepared(monkeypatch, noise=7).generate("GET", "/p")
    second = _prepared(monkeypatch, noise=200).generate("GET", "/p")
    assert first != second


def test_generate_produces_an_unpadded_base64_id(monkeypatch):
    client = _prepared(monkeypatch)
    txid = client.generate("GET", "/i/api/graphql/QID/SearchTimeline")
    assert not txid.endswith("=")
    # 1 noise + 48 key + 4 time + 16 hash + 1 constant = 70 bytes -> 94 chars.
    assert len(txid) == 94
    base64.b64decode(txid + "==")


def test_gated_ops_are_exactly_the_three_probed_walls():
    """Live-probed 2026-07-20. Widening this set makes ungated ops send an
    unnecessary header; narrowing it re-breaks a walled op."""
    assert transaction.GATED_OPS == {"SearchTimeline", "UserTweetsAndReplies", "Followers"}


def test_ungated_ops_get_no_txid_header():
    from agentic_x import client as client_module

    read_client = client_module.ReadClient("token", "ct0", "ua")
    try:
        for operation in ("UserTweets", "TweetDetail", "HomeTimeline", "Following"):
            header = read_client._txid_header(operation, f"https://x.com/i/api/{operation}", "GET")
            assert header == {}
    finally:
        read_client.close()


def test_gated_ops_get_a_txid_header(monkeypatch):
    from agentic_x import client as client_module

    read_client = client_module.ReadClient("token", "ct0", "ua")
    monkeypatch.setattr(
        read_client._transaction, "generate", lambda method, path: f"TXID:{method}:{path}"
    )
    try:
        header = read_client._txid_header(
            "SearchTimeline", "https://x.com/i/api/graphql/QID/SearchTimeline?variables=1", "GET"
        )
        # Signs the path only -- query string excluded.
        assert header == {"x-client-transaction-id": "TXID:GET:/i/api/graphql/QID/SearchTimeline"}
    finally:
        read_client.close()
