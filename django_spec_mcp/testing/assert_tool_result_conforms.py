from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# JSON's type vocabulary, in the order a value has to be tested against it.
# ``bool`` leads because Python's ``bool`` is an ``int`` while JSON's boolean is
# not a number: testing ``int`` first would report ``True`` as an integer, in a
# message whose entire job is to name the type that arrived.
_JSON_TYPE_NAMES: tuple[tuple[type, str], ...] = (
    (bool, "boolean"),
    (int, "integer"),
    (float, "number"),
    (str, "string"),
    (list, "array"),
    (dict, "object"),
)

# Long enough to recognise the value that arrived, short enough that a failure
# against a thousand-row page is still something a person reads.
_VALUE_REPR_LIMIT: int = 120


def _json_type_name(value: Any) -> str:
    """Name ``value``'s type in JSON's vocabulary rather than Python's."""
    if value is None:
        return "null"
    for python_type, name in _JSON_TYPE_NAMES:
        if isinstance(value, python_type):
            return name
    return type(value).__name__


def _abbreviate(value: Any) -> str:
    """``repr(value)``, truncated with its full length named."""
    text: str = repr(value)
    if len(text) <= _VALUE_REPR_LIMIT:
        return text
    return f"{text[:_VALUE_REPR_LIMIT]}... ({len(text)} chars)"


def _instance_path(error: Any) -> str:
    """Where in the payload the error sits, as a path expression."""
    parts: list[str] = ["$"]
    for token in error.absolute_path:
        parts.append(f"[{token}]" if isinstance(token, int) else f".{token}")
    return "".join(parts)


def _describe(error: Any) -> str:
    """One line naming the property, the claim, and what actually arrived."""
    location: str = _instance_path(error)
    if error.validator in ("type", "format"):
        return (
            f"{location}: advertised {error.validator} {error.validator_value!r}, "
            f"got {_json_type_name(error.instance)} {_abbreviate(error.instance)}"
        )
    return f"{location}: {error.message}"


def assert_tool_result_conforms(tool: Mapping[str, Any], result: Mapping[str, Any]) -> None:
    """Assert ``result``'s ``structuredContent`` satisfies ``tool``'s ``outputSchema``.

    Args:
        tool: One entry from a ``tools/list`` response -- the dict carrying
            ``name`` and ``outputSchema``, not the response that wraps them.
        result: The ``tools/call`` result for that tool: the ``result`` member
            of the JSON-RPC envelope, carrying ``structuredContent``.

    Raises:
        AssertionError: If the payload does not conform, naming every property
            that disagrees, what the schema advertised, and what arrived. Also
            if the tool advertises no schema, or advertises one and returns no
            ``structuredContent`` -- both would otherwise make this assertion
            pass for any result at all, which is the failure mode it exists to
            remove.
        ImportError: If ``jsonschema`` is not installed.

    ```python
    from django_spec_mcp.testing import assert_tool_result_conforms

    page = server.list_tools(user=user)
    tool = next(entry for entry in page["tools"] if entry["name"] == "invoices.list")
    result = await server.acall_tool("invoices.list", {}, user=user)

    assert_tool_result_conforms(tool, result)
    ```

    Both arguments are plain mappings straight off the wire, so this works
    against any way of reaching a tool -- the in-process transport above, an
    HTTP round trip, or a client library -- with no fixture of its own.

    Formats are checked, not only types, and how thoroughly depends on the
    install: jsonschema registers a format checker only when the library that
    performs it is importable, so the ``test`` extra pulls those in. Without
    them ``"format": "date-time"`` is not checked and this still passes.
    """
    try:
        from jsonschema import FormatChecker
        from jsonschema.validators import Draft202012Validator, validator_for
    except ImportError as exc:
        raise ImportError(
            "assert_tool_result_conforms needs `jsonschema` to check a result "
            "against the schema the server advertised. It is a test-only "
            "dependency and deliberately not a runtime one -- install it with "
            "`pip install django-spec-mcp[test]`."
        ) from exc

    name: Any = tool.get("name", "<unnamed>")
    schema: Any = tool.get("outputSchema")
    if schema is None:
        raise AssertionError(
            f"Tool {name!r} advertises no 'outputSchema', so there is nothing to "
            "conform to and this assertion would pass for any result at all. "
            "Either the binding sets include_output_schema=False (or "
            "SPEC_MCP['INCLUDE_OUTPUT_SCHEMA'] is False), or its spec "
            "declares no output for a schema to be derived from."
        )
    if "structuredContent" not in result:
        detail: str = ""
        if result.get("isError"):
            detail = f" The call returned an error result: {_abbreviate(result.get('content'))}"
        raise AssertionError(
            f"Tool {name!r} advertises an 'outputSchema' but its result carries no "
            "'structuredContent'. The MCP spec requires conforming "
            f"structuredContent whenever outputSchema is declared.{detail}"
        )

    validator_cls: Any = validator_for(schema, default=Draft202012Validator)
    validator: Any = validator_cls(schema, format_checker=FormatChecker())
    errors: list[Any] = sorted(
        validator.iter_errors(result["structuredContent"]), key=_instance_path
    )
    if not errors:
        return
    problems: str = "\n".join(f"  - {_describe(error)}" for error in errors)
    counted: str = f"{len(errors)} problem" if len(errors) == 1 else f"{len(errors)} problems"
    raise AssertionError(
        f"Tool {name!r} returned 'structuredContent' that does not conform to the "
        f"'outputSchema' it advertises ({counted}):\n{problems}"
    )


__all__ = ["assert_tool_result_conforms"]
