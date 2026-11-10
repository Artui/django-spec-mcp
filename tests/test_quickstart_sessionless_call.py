from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest
from django.test import Client

_PAGE = Path(__file__).resolve().parents[1] / "docs" / "quickstart.md"


def _sessionless_curl() -> list[str]:
    """The words of the page's ``curl`` that names ``Mcp-Method``."""
    fences = re.findall(r"```bash\n(.*?)```", _PAGE.read_text(), flags=re.DOTALL)
    (source,) = [f for f in fences if "Mcp-Method: tools/call" in f]
    return shlex.split(source.replace("\\\n", " "))


@pytest.mark.django_db(transaction=True)
def test_the_quickstarts_sessionless_call_is_served_with_no_session() -> None:
    words = _sessionless_curl()
    headers = dict(words[i + 1].split(": ", 1) for i, word in enumerate(words) if word == "-H")
    body = words[words.index("-d") + 1]

    response = Client().post(
        "/mcp/", data=body, content_type=headers.pop("Content-Type"), headers=headers
    )

    assert response.status_code == 200, response.content
    assert "Mcp-Session-Id" not in response.headers
    result = response.json()["result"]
    assert not result.get("isError"), result
    assert result["structuredContent"]["number"] == "INV-1"
    assert json.loads(body)["params"]["_meta"] == {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28"
    }
