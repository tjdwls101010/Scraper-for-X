import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    """Load a fixture as a parsed dict -- X's GraphQL responses are JSON bodies
    (unlike the FB sibling's NDJSON/@defer captures), so a fixture is just one
    JSON document."""

    def _load(name: str) -> dict:
        return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))

    return _load
