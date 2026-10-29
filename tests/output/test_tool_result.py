from __future__ import annotations

import json

import pytest
from django_service_specs import UNSET

from django_spec_mcp.output.tool_result import build_tool_result


@pytest.mark.parametrize(
    ("payload", "text"),
    [
        ({"a": 1}, '{\n  "a": 1\n}'),
        # A uniform list of objects, the payload a second format was once chosen for.
        ([{"a": 1}, {"a": 2}], '[\n  {\n    "a": 1\n  },\n  {\n    "a": 2\n  }\n]'),
    ],
    ids=["object", "uniform-list"],
)
def test_the_text_block_is_the_payload_as_json(payload: object, text: str) -> None:
    res = build_tool_result(payload)
    assert res.structured_content == payload
    assert [block.to_dict() for block in res.content] == [{"type": "text", "text": text}]


def test_build_tool_result_marks_error() -> None:
    res = build_tool_result({"err": "x"}, is_error=True)
    assert res.is_error is True


def test_build_tool_result_omits_structured_content_when_disabled() -> None:
    res = build_tool_result({"a": 1}, include_structured_content=False)
    assert res.structured_content is UNSET
    # Text payload still carries the data.
    assert json.loads(res.content[0].text or "") == {"a": 1}
    # ``to_dict`` drops the field entirely so the wire payload is leaner.
    assert "structuredContent" not in res.to_dict()


def test_build_tool_result_includes_structured_content_by_default() -> None:
    res = build_tool_result({"a": 1})
    assert res.to_dict()["structuredContent"] == {"a": 1}


def test_tool_result_carries_per_call_meta() -> None:
    """``_meta`` on the *result envelope* is per-call, so it is a parameter.

    A tool's static ``_meta`` is already advertised on its ``tools/list``
    entry; nothing sources this from the binding.
    """
    result = build_tool_result({"ok": True}, meta={"example.com/trace": "abc"})
    assert result.to_dict()["_meta"] == {"example.com/trace": "abc"}


def test_tool_result_omits_meta_by_default() -> None:
    assert "_meta" not in build_tool_result({"ok": True}).to_dict()


# ---------- a null payload is a value, not an absent structured channel ----------


def test_a_null_payload_is_emitted_as_an_empty_object() -> None:
    """``include_structured_content=True`` must be distinguishable from ``False``.

    Omitting the key for a genuine ``None`` collapsed "this tool has nothing to
    present" into "this tool has no structured channel", so a client branching
    on the key's presence was told the wrong thing about the server's
    capability. The value is ``{}`` rather than ``null``, because MCP requires
    structured content to be an object, and the text block carries the same
    JSON.
    """
    res = build_tool_result(None, include_structured_content=True)
    wire = res.to_dict()
    assert "structuredContent" in wire
    assert wire["structuredContent"] == {}
    assert wire["content"][0]["text"] == "{}"


def test_opting_out_still_omits_the_key_entirely() -> None:
    res = build_tool_result(None, include_structured_content=False)
    assert "structuredContent" not in res.to_dict()


def test_a_resource_link_result_makes_the_same_distinction(monkeypatch) -> None:
    from django_spec_mcp.types.tool_content_kind import ToolContentKind

    res = build_tool_result(
        {"uri": "https://example.test/doc", "name": "doc"},
        content_kind=ToolContentKind.RESOURCE_LINK,
        include_structured_content=False,
    )
    assert "structuredContent" not in res.to_dict()
