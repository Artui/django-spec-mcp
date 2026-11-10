from __future__ import annotations

import re

from django_spec_mcp.registry.types.resource_binding import ResourceBinding
from django_spec_mcp.types.ui_resource_mime_type import UI_RESOURCE_MIME_TYPE

_TEMPLATE_VAR = re.compile(r"\{([^}]+)\}")

# What one ``{var}`` may not contain: ``/`` ends a segment, and a control
# character (Unicode ``Cc``: C0, DEL and C1) is no part of a name. Without the
# second exclusion ``reports://\n`` would fit ``reports://{id}`` as ``id='\n'``
# (``test_control_characters_in_uris.py``).
_VALUE_CHAR = r"[^/\x00-\x1f\x7f-\x9f]"


def _template_to_pattern(uri_template: str, *, lazy: bool = False) -> re.Pattern[str]:
    """Compile an RFC 6570 (subset) URI template to a regex, used with ``fullmatch``."""
    quantifier: str = "+?" if lazy else "+"
    parts: list[str] = []
    last: int = 0
    for match in _TEMPLATE_VAR.finditer(uri_template):
        parts.append(re.escape(uri_template[last : match.start()]))
        parts.append(f"(?P<{match.group(1)}>{_VALUE_CHAR}{quantifier})")
        last = match.end()
    parts.append(re.escape(uri_template[last:]))
    return re.compile("".join(parts))


class ResourceRegistry:
    """URI or URI-template to resource binding lookup."""

    def __init__(self) -> None:
        self._bindings: list[ResourceBinding] = []
        # Each template's greedy and lazy patterns, compiled once at registration.
        self._patterns: dict[str, tuple[re.Pattern[str], re.Pattern[str]]] = {}

    def register(self, binding: ResourceBinding) -> None:
        # A view is fetched by exact URI, so a templated one is a resource no host can ask for.
        if binding.mime_type == UI_RESOURCE_MIME_TYPE and binding.is_template:
            raise ValueError(
                f"MCP resource {binding.name!r} has the view mime type "
                f"{UI_RESOURCE_MIME_TYPE!r} and the templated URI "
                f"{binding.uri_template!r}. A view's URI must be concrete: a host "
                "fetches it by the exact URI a tool's ui= link names and expands "
                "nothing. Register one view per URI."
            )
        for existing in self._bindings:
            if existing.uri_template == binding.uri_template:
                raise ValueError(f"Duplicate MCP resource URI: {binding.uri_template!r}")
        self._bindings.append(binding)
        self._patterns[binding.uri_template] = (
            _template_to_pattern(binding.uri_template),
            _template_to_pattern(binding.uri_template, lazy=True),
        )

    def resolve(self, uri: str) -> tuple[ResourceBinding, dict[str, str]] | None:
        """The binding ``uri`` resolves to, or ``None`` when nothing matches."""
        for binding in (*self.concrete(), *self.templates()):
            greedy, lazy = self._patterns[binding.uri_template]
            match: re.Match[str] | None = greedy.fullmatch(uri)
            if match is None:
                continue
            variables: dict[str, str] = match.groupdict()
            shortest: dict[str, str] = (lazy.fullmatch(uri) or match).groupdict()
            if shortest != variables:
                raise ValueError(
                    f"Ambiguous resource URI {uri!r}: the template "
                    f"{binding.uri_template!r} splits it more than one way, as "
                    f"{variables!r} or as {shortest!r}."
                )
            return binding, variables
        return None

    def by_uri_template(self, uri_template: str) -> ResourceBinding | None:
        """Exact lookup on the registered template string."""
        for binding in self._bindings:
            if binding.uri_template == uri_template:
                return binding
        return None

    def all(self) -> list[ResourceBinding]:
        return list(self._bindings)

    def concrete(self) -> list[ResourceBinding]:
        return [b for b in self._bindings if not b.is_template]

    def templates(self) -> list[ResourceBinding]:
        return [b for b in self._bindings if b.is_template]

    def __len__(self) -> int:
        return len(self._bindings)


__all__ = ["ResourceRegistry"]
