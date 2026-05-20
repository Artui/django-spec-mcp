from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.registry.types.tool_binding import ToolBinding


def test_service_property_returns_spec_callable() -> None:
    def svc() -> None: ...

    binding = ToolBinding(name="t", description=None, spec=ServiceSpec(service=svc))
    assert binding.service is svc


def test_rejects_schema_on_with_content_off_at_construction() -> None:
    """The spec-violating combo is caught immediately when both fields are explicit."""

    def svc() -> None: ...

    with pytest.raises(ImproperlyConfigured) as excinfo:
        ToolBinding(
            name="bad",
            description=None,
            spec=ServiceSpec(service=svc),
            include_output_schema=True,
            include_structured_content=False,
        )
    assert "bad" in str(excinfo.value)


def test_allows_schema_off_with_content_on() -> None:
    """SEP-1624: structuredContent without outputSchema is spec-allowed."""

    def svc() -> None: ...

    binding = ToolBinding(
        name="t",
        description=None,
        spec=ServiceSpec(service=svc),
        include_output_schema=False,
        include_structured_content=True,
    )
    assert binding.include_output_schema is False
    assert binding.include_structured_content is True
