from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from django_spec_mcp.protocol.types.resource_contents import ResourceContents


@dataclass(frozen=True)
class ToolContentBlock:
    """One entry in a tool result's ``content`` array."""

    type: str
    text: str | None = None
    data: str | None = None
    mime_type: str | None = None
    # A ``resource_link`` is a ``Resource`` with a ``type``: descriptive fields, # no payload.
    uri: str | None = None
    name: str | None = None
    description: str | None = None
    resource: ResourceContents | None = None
    annotations: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None

    @classmethod
    def text_block(
        cls,
        text: str,
        *,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ToolContentBlock:
        """A plain text block — what every tool result carries today."""
        return cls(type="text", text=text, annotations=annotations, meta=meta)

    @classmethod
    def image(
        cls,
        data: bytes | str,
        *,
        mime_type: str,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ToolContentBlock:
        """An image block."""
        return cls(
            type="image",
            data=_encode_base64(data),
            mime_type=mime_type,
            annotations=annotations,
            meta=meta,
        )

    @classmethod
    def audio(
        cls,
        data: bytes | str,
        *,
        mime_type: str,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ToolContentBlock:
        """An audio block — the same contract as ``image``."""
        return cls(
            type="audio",
            data=_encode_base64(data),
            mime_type=mime_type,
            annotations=annotations,
            meta=meta,
        )

    @classmethod
    def resource_link(
        cls,
        uri: str,
        *,
        name: str,
        description: str | None = None,
        mime_type: str | None = None,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ToolContentBlock:
        """A pointer to a resource the client can read separately."""
        return cls(
            type="resource_link",
            uri=uri,
            name=name,
            description=description,
            mime_type=mime_type,
            annotations=annotations,
            meta=meta,
        )

    @classmethod
    def embedded_resource(
        cls,
        resource: ResourceContents,
        *,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ToolContentBlock:
        """A resource's contents inlined into the result."""
        return cls(type="resource", resource=resource, annotations=annotations, meta=meta)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type}
        if self.text is not None:
            out["text"] = self.text
        if self.data is not None:
            out["data"] = self.data
        if self.uri is not None:
            out["uri"] = self.uri
        if self.name is not None:
            out["name"] = self.name
        if self.description is not None:
            out["description"] = self.description
        if self.mime_type is not None:
            out["mimeType"] = self.mime_type
        if self.resource is not None:
            out["resource"] = self.resource.to_dict()
        if self.annotations is not None:
            out["annotations"] = self.annotations
        if self.meta:
            out["_meta"] = self.meta
        return out


def _encode_base64(data: bytes | str) -> str:
    """Base64-encode binary payloads; pass strings through unchanged."""
    if isinstance(data, str):
        return data
    return base64.b64encode(data).decode("ascii")


__all__ = ["ToolContentBlock"]
