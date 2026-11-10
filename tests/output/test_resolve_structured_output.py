from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from django_spec_mcp.output.resolve_structured_output import resolve_structured_output


def _resolve(
    *,
    schema: bool | None,
    content: bool | None,
    name: str = "t",
    default_schema: bool = True,
    default_content: bool = True,
) -> tuple[bool, bool]:
    """Resolve the tri-state overrides against the server-level defaults."""
    return resolve_structured_output(
        include_output_schema_override=schema,
        include_structured_content_override=content,
        binding_name=name,
        default_output_schema=default_schema,
        default_structured_content=default_content,
    )


# ---------- the collapse rule ----------


# ``None`` inherits the server-level default; ``True`` / ``False`` win over it. Each
@pytest.mark.parametrize(
    ("schema", "content", "default_schema", "default_content", "resolved"),
    [
        (False, True, True, False, (False, True)),
        (False, False, True, True, (False, False)),
        (False, None, True, True, (False, True)),
        (None, None, False, False, (False, False)),
        (False, True, True, True, (False, True)),
        (None, True, True, True, (True, True)),
        (None, True, False, True, (False, True)),
        (None, None, True, True, (True, True)),
        (None, None, False, True, (False, True)),
    ],
)
def test_overrides_win_and_none_inherits_the_default(
    schema: bool | None,
    content: bool | None,
    default_schema: bool,
    default_content: bool,
    resolved: tuple[bool, bool],
) -> None:
    assert (
        _resolve(
            schema=schema,
            content=content,
            default_schema=default_schema,
            default_content=default_content,
        )
        == resolved
    )


# ---------- spec invariant: a schema is never advertised without the content ----------


def test_raises_when_schema_advertised_but_content_suppressed() -> None:
    with pytest.raises(ImproperlyConfigured) as excinfo:
        _resolve(
            schema=None,
            content=None,
            name="weather",
            default_schema=True,
            default_content=False,
        )
    msg = str(excinfo.value)
    assert "weather" in msg
    assert "outputSchema" in msg
    assert "structuredContent" in msg


def test_raises_when_per_binding_schema_forced_on_with_content_off() -> None:
    """Per-binding overrides clash with each other even when the defaults agree."""
    with pytest.raises(ImproperlyConfigured):
        _resolve(schema=True, content=False)
