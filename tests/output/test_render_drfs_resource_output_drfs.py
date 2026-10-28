"""``render_drfs_resource_output`` — the output serializer a drf-services resource renders through.

The render half of what ``build_resource_contents`` did before the encoding was
split from it, so a view's read could encode without drf-services. Each case
renders and then encodes, as ``read_drfs_resource`` does; the encoding alone is
asked in ``test_build_resource_contents.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from django_service_specs import SelectorKind
from rest_framework import serializers

from django_spec_mcp.output.build_resource_contents import build_resource_contents
from django_spec_mcp.output.render_drfs_resource_output import render_drfs_resource_output
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.resource_contents import ResourceContents
from django_spec_mcp.registry.types.resource_binding import ResourceBinding


class _Out(serializers.Serializer):
    label = serializers.CharField()


@dataclass
class _DCOut:
    label: str
    qty: int = 0


def _binding(**overrides: Any) -> ResourceBinding:
    defaults: dict[str, Any] = {
        "name": "thing",
        "uri_template": "things://x",
        "description": None,
        "selector": lambda: None,
        "kind": SelectorKind.RETRIEVE,
    }
    return ResourceBinding(**{**defaults, **overrides})


def _read(binding: ResourceBinding, raw: Any) -> ResourceContents | JsonRpcError:
    payload = render_drfs_resource_output(binding, raw, view=None, request=None)
    return build_resource_contents(binding=binding, uri="things://x", raw=payload)


class TestRendering:
    def test_no_output_serializer_hands_the_value_back_as_it_is(self) -> None:
        raw = {"a": 1}
        assert render_drfs_resource_output(_binding(), raw, view=None, request=None) is raw

    def test_output_serializer_renders_a_retrieve_as_one_object(self) -> None:
        binding = _binding(output_serializer=_Out, kind=SelectorKind.RETRIEVE)
        contents = _read(binding, {"label": "one"})

        assert not isinstance(contents, JsonRpcError)
        assert contents.text == '{\n  "label": "one"\n}'

    def test_output_serializer_renders_a_list_as_many(self) -> None:
        binding = _binding(output_serializer=_Out, kind=SelectorKind.LIST)
        contents = _read(binding, [{"label": "one"}])

        assert not isinstance(contents, JsonRpcError)
        assert contents.text == '[\n  {\n    "label": "one"\n  }\n]'

    def test_a_raw_dataclass_output_renders_a_retrieve(self) -> None:
        """A dataclass declared as the output renders through the class drf-services
        resolves for it. Instantiated as declared, the dataclass's own ``__init__``
        received ``many=`` and raised on every read."""
        binding = _binding(output_serializer=_DCOut, kind=SelectorKind.RETRIEVE)
        contents = _read(binding, _DCOut(label="one", qty=2))

        assert not isinstance(contents, JsonRpcError)
        assert contents.text == '{\n  "label": "one",\n  "qty": 2\n}'

    def test_a_raw_dataclass_output_renders_a_list_as_many(self) -> None:
        binding = _binding(output_serializer=_DCOut, kind=SelectorKind.LIST)
        contents = _read(binding, [_DCOut(label="one"), _DCOut(label="two")])

        assert not isinstance(contents, JsonRpcError)
        assert [row["label"] for row in json.loads(contents.text)] == ["one", "two"]

    def test_the_serializer_gets_the_baseline_context(self) -> None:
        seen: dict[str, Any] = {}

        class _Reads(serializers.Serializer):
            label = serializers.SerializerMethodField()

            def get_label(self, _: Any) -> str:
                seen.update(self.context)
                return "x"

        view, request = object(), object()
        render_drfs_resource_output(
            _binding(output_serializer=_Reads), {}, view=view, request=request
        )

        assert seen["view"] is view
        assert seen["request"] is request
