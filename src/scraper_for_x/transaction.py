"""Generate X's per-request ``x-client-transaction-id`` header (plan §4).

**This module is fragile by design and by nature.** Every other module here
reads a documented-ish JSON envelope; this one reproduces an obfuscated,
reverse-engineered client-side algorithm that X can change on any deploy. It is
the one place in this package where "it worked yesterday, it 404s today" is an
expected failure mode rather than a bug. When it rots, the symptom is a 404
with an empty body from a gated op -- exactly what the wall looks like -- and
the fix is to re-port the current public implementation, not to debug this file.

**Last verified working: 2026-07-20** (a generated id took ``SearchTimeline``
from 404 to 200 with 22 entries, live, on a throwaway account).

Why generation and not capture: the header is **single use**. Replaying a real
captured id -- intercepted from X's own client -- 404s, because X's client
already spent it (live-proven 2026-07-20). So there is no "harvest once, replay"
path for the gated ops, the way there is for cookies and query-ids; a fresh id
must be minted per request.

Only three ops need it (``GATED_OPS``). Every other op must NOT send the header:
it is unnecessary there, and one more thing that can break.

---

Algorithm ported from **iSarabjitDhiman/XClientTransaction**::

    MIT License. Copyright (c) 2025 Sarabjit Dhiman

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

The maths (cubic curve, interpolation, rotation matrix, float->hex, the byte
layout) is kept **verbatim** from that implementation -- deliberately, so a
future re-port can diff against upstream rather than reverse-engineer this file.
The one intentional divergence: upstream reaches into the page with
BeautifulSoup, this uses stdlib ``html.parser``, because this package's base
install is ``httpx`` + ``platformdirs`` and must not grow a ``bs4`` dependency.

Pure ``httpx`` -- no browser, no scrapling (``tests/test_no_scrapling_import.py``
guards this).
"""

from __future__ import annotations

import base64
import hashlib
import math
import random
import re
import time
from functools import reduce
from html.parser import HTMLParser

import httpx

from .errors import TransactionIdError

#: The only ops that need the header, live-probed 2026-07-20. Everything else
#: (``UserTweets``, ``TweetDetail``, ``HomeTimeline``, ``UserByScreenName``,
#: ``Following``) answers 200 over plain httpx without it and must not be sent
#: one. Note the asymmetry: ``Followers`` is gated but ``Following`` is not --
#: proven, not assumed (three different variable shapes all 404'd).
GATED_OPS = frozenset({"SearchTimeline", "UserTweetsAndReplies", "Followers"})

_ADDITIONAL_RANDOM_NUMBER = 3
_DEFAULT_KEYWORD = "obfiowerehiring"
_TOTAL_TIME = 4096
#: X's own epoch offset for the timestamp bytes (2023-05-01T07:00:00Z).
_EPOCH_OFFSET_SECONDS = 1682924400

_ON_DEMAND_FILE_URL = "https://abs.twimg.com/responsive-web/client-web/ondemand.s.{filename}a.js"
_ON_DEMAND_FILE_RE = re.compile(r""",(\d+):["']ondemand\.s["']""")
_ON_DEMAND_HASH_PATTERN = r',{}:"([0-9a-f]+)"'
_INDICES_RE = re.compile(r"""(\(\w{1}\[(\d{1,2})\],\s*16\))+""")
_VERIFICATION_RE = re.compile(
    r"""<meta[^>]+name=["']twitter-site-verification["'][^>]+content=["']([^"']+)["']"""
)


class _FrameParser(HTMLParser):
    """Collect, per ``loading-x-anim-N`` element, the ``d`` attributes of the
    element children of its FIRST element child.

    Upstream reaches the same node with
    ``list(list(frame.children)[0].children)[1].get("d")``; this records that
    whole child list so the caller can index it identically.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.frames: dict[int, list[str]] = {}
        self._active: int | None = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if self._active is None:
            match = re.fullmatch(r"loading-x-anim-(\d+)", attributes.get("id") or "")
            if match:
                self._active = int(match.group(1))
                self.frames[self._active] = []
                self._depth = 0
            return
        self._depth += 1
        if self._depth == 2:
            self.frames[self._active].append(attributes.get("d") or "")

    def handle_endtag(self, tag: str) -> None:
        if self._active is None:
            return
        if self._depth == 0:
            self._active = None
            return
        self._depth -= 1


def extract_frame_paths(html: str) -> dict[int, list[str]]:
    """Parse the four loading-animation frames out of x.com's HTML."""
    parser = _FrameParser()
    parser.feed(html)
    return parser.frames


# --- maths, verbatim from upstream (see module docstring) --------------------


def _js_round(num: float) -> float:
    """JavaScript's ``Math.round``, which differs from Python's banker's
    rounding on exact .5 -- the generated key is wrong if this is not matched."""
    x = math.floor(num)
    if (num - x) >= 0.5:
        x = math.ceil(num)
    return math.copysign(x, num)


def _is_odd(num: float) -> float:
    return -1.0 if num % 2 else 0.0


def _float_to_hex(x: float) -> str:
    result: list[str] = []
    quotient = int(x)
    fraction = x - quotient
    while quotient > 0:
        quotient = int(x / 16)
        remainder = int(x - (float(quotient) * 16))
        result.insert(0, chr(remainder + 55) if remainder > 9 else str(remainder))
        x = float(quotient)
    if fraction == 0:
        return "".join(result)
    result.append(".")
    while fraction > 0:
        fraction *= 16
        integer = int(fraction)
        fraction -= float(integer)
        result.append(chr(integer + 55) if integer > 9 else str(integer))
    return "".join(result)


def _cubic_value(curves: list[float], t: float) -> float:
    start_gradient = end_gradient = 0.0
    start, mid, end = 0.0, 0.0, 1.0
    if t <= 0.0:
        if curves[0] > 0.0:
            start_gradient = curves[1] / curves[0]
        elif curves[1] == 0.0 and curves[2] > 0.0:
            start_gradient = curves[3] / curves[2]
        return start_gradient * t
    if t >= 1.0:
        if curves[2] < 1.0:
            end_gradient = (curves[3] - 1.0) / (curves[2] - 1.0)
        elif curves[2] == 1.0 and curves[0] < 1.0:
            end_gradient = (curves[1] - 1.0) / (curves[0] - 1.0)
        return 1.0 + end_gradient * (t - 1.0)

    def calculate(a: float, b: float, m: float) -> float:
        return 3.0 * a * (1 - m) * (1 - m) * m + 3.0 * b * (1 - m) * m * m + m * m * m

    while start < end:
        mid = (start + end) / 2
        x_estimate = calculate(curves[0], curves[2], mid)
        if abs(t - x_estimate) < 0.00001:
            return calculate(curves[1], curves[3], mid)
        if x_estimate < t:
            start = mid
        else:
            end = mid
    return calculate(curves[1], curves[3], mid)


def _interpolate(from_list: list[float], to_list: list[float], f: float) -> list[float]:
    return [a * (1 - f) + b * f for a, b in zip(from_list, to_list, strict=True)]


def _rotation_matrix(rotation: float) -> list[float]:
    rad = math.radians(rotation)
    return [math.cos(rad), -math.sin(rad), math.sin(rad), math.cos(rad)]


def _solve(value: float, min_val: float, max_val: float, rounding: bool) -> float:
    result = value * (max_val - min_val) / 255 + min_val
    return math.floor(result) if rounding else round(result, 2)


def _animate(frame_row: list[int], target_time: float) -> str:
    from_color = [float(item) for item in [*frame_row[:3], 1]]
    to_color = [float(item) for item in [*frame_row[3:6], 1]]
    to_rotation = [_solve(float(frame_row[6]), 60.0, 360.0, True)]
    curves = [
        _solve(float(item), _is_odd(counter), 1.0, False)
        for counter, item in enumerate(frame_row[7:])
    ]
    value = _cubic_value(curves, target_time)
    color = [max(0, min(255, item)) for item in _interpolate(from_color, to_color, value)]
    rotation = _interpolate([0.0], to_rotation, value)
    matrix = _rotation_matrix(rotation[0])

    parts = [format(round(item), "x") for item in color[:-1]]
    for item in matrix:
        rounded = abs(round(item, 2))
        hex_value = _float_to_hex(rounded)
        if hex_value.startswith("."):
            parts.append(f"0{hex_value}".lower())
        else:
            parts.append(hex_value or "0")
    parts.extend(["0", "0"])
    return re.sub(r"[.-]", "", "".join(parts))


def compute_animation_key(
    key_bytes: list[int], frames: dict[int, list[str]], row_index_key: int, byte_indices: list[int]
) -> str:
    """Derive the animation key from the page's four SVG frames.

    Split out from :class:`ClientTransaction` so it can be unit-tested against a
    synthetic page without any network.
    """
    frame_paths = frames.get(key_bytes[5] % 4) or []
    if len(frame_paths) < 2:
        raise TransactionIdError(
            f"loading-x-anim frame {key_bytes[5] % 4} has {len(frame_paths)} paths, expected >= 2"
        )
    rows = [
        [int(number) for number in re.sub(r"[^\d]+", " ", segment).strip().split()]
        for segment in frame_paths[1][9:].split("C")
    ]
    row_index = key_bytes[row_index_key] % 16
    frame_time = reduce(lambda a, b: a * b, [key_bytes[i] % 16 for i in byte_indices])
    frame_time = _js_round(frame_time / 10) * 10
    return _animate(rows[row_index], float(frame_time) / _TOTAL_TIME)


class ClientTransaction:
    """Mints fresh transaction ids for one session.

    The three page-derived ingredients (verification key, animation frames,
    key-byte indices) are fetched **once** on first use and cached for the life
    of this object; each id is then computed locally. That keeps the hot path
    free of extra round trips while still honoring the single-use rule -- what
    must be fresh per request is the id, not the ingredients.
    """

    def __init__(self, auth_token: str, ct0: str, user_agent: str) -> None:
        self._auth_token = auth_token
        self._ct0 = ct0
        self._user_agent = user_agent
        self._key_bytes: list[int] | None = None
        self._animation_key: str | None = None

    def _load_ingredients(self) -> None:
        """Fetch x.com and the ondemand chunk, and derive the cached inputs.

        Uses a cookie-only client, not the GraphQL header set: x.com's plain
        page 401s when sent the ``authorization``/``x-twitter-*`` headers (the
        same reason ``queryids.reanchor_via_main_js`` builds its own client).
        """
        with httpx.Client(
            cookies={"auth_token": self._auth_token, "ct0": self._ct0},
            headers={"user-agent": self._user_agent},
            follow_redirects=True,
            timeout=30,
        ) as http:
            html = http.get("https://x.com/home").text

            key_match = _VERIFICATION_RE.search(html)
            if key_match is None:
                raise TransactionIdError(
                    "x.com served no twitter-site-verification meta tag -- the transaction-id "
                    "algorithm has changed or the session could not load the page"
                )
            key_bytes = list(base64.b64decode(key_match.group(1).encode()))

            frames = extract_frame_paths(html)
            if len(frames) < 4:
                raise TransactionIdError(
                    f"x.com served {len(frames)} loading-x-anim frames, expected 4"
                )

            chunk_match = _ON_DEMAND_FILE_RE.search(html)
            if chunk_match is None:
                raise TransactionIdError("x.com served no ondemand.s chunk reference")
            hash_match = re.search(_ON_DEMAND_HASH_PATTERN.format(chunk_match.group(1)), html)
            if hash_match is None:
                raise TransactionIdError(
                    f"no content hash for ondemand.s chunk {chunk_match.group(1)}"
                )
            ondemand = http.get(_ON_DEMAND_FILE_URL.format(filename=hash_match.group(1))).text

        indices = [int(match.group(2)) for match in _INDICES_RE.finditer(ondemand)]
        if len(indices) < 2:
            raise TransactionIdError("ondemand.s chunk carried no key-byte indices")

        self._key_bytes = key_bytes
        self._animation_key = compute_animation_key(key_bytes, frames, indices[0], indices[1:])

    def generate(self, method: str, path: str) -> str:
        """Return a fresh id for one request. Never cache or replay the result.

        ``path`` is the URL path only, e.g.
        ``/i/api/graphql/<query_id>/SearchTimeline`` -- it is part of the signed
        payload, so an id minted for one op is not valid for another.
        """
        if self._key_bytes is None or self._animation_key is None:
            self._load_ingredients()
        assert self._key_bytes is not None and self._animation_key is not None  # noqa: S101

        time_now = math.floor((time.time() * 1000 - _EPOCH_OFFSET_SECONDS * 1000) / 1000)
        time_bytes = [(time_now >> (i * 8)) & 0xFF for i in range(4)]
        digest = hashlib.sha256(
            f"{method}!{path}!{time_now}{_DEFAULT_KEYWORD}{self._animation_key}".encode()
        ).digest()
        payload = [
            *self._key_bytes,
            *time_bytes,
            *list(digest)[:16],
            _ADDITIONAL_RANDOM_NUMBER,
        ]
        noise = random.randint(0, 255)
        out = bytearray([noise, *[item ^ noise for item in payload]])
        return base64.b64encode(out).decode().strip("=")
