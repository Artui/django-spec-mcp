from __future__ import annotations

import re

from django_spec_mcp.registry.types.resource_binding import ResourceBinding

_TEMPLATE_VAR = re.compile(r"\{([^}]+)\}")

# What one ``{var}`` may not contain: ``/`` ends a segment, and a control
# character (Unicode ``Cc``: C0, DEL and C1) is no part of a name. Without the
# second exclusion ``reports://\n`` would fit ``reports://{id}`` as ``id='\n'``
# (``test_control_characters_in_uris.py``).
_VALUE_CHAR = r"[^/\x00-\x1f\x7f-\x9f]"


def _template_to_pattern(uri_template: str, *, lazy: bool = False) -> re.Pattern[str]:
    """Compile an RFC 6570 (subset) URI template to a regex, used with ``fullmatch``.

    Simple ``{var}`` placeholders only — enough for the ``scheme://{lookup}``
    shape this package emits. Each variable matches one or more characters
    other than ``/`` and control characters; ``lazy`` makes every one of them
    as short as the rest of the URI allows instead of as long, which is how
    ``resolve`` finds out whether a URI fits the template more than one way.
    """
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
    """URI or URI-template to
    [`ResourceBinding`][django_spec_mcp.registry.types.resource_binding.ResourceBinding]
    lookup.

    Concrete resources are matched by exact URI, templates by a regex derived
    from the template. ``resolve`` returns the binding plus the variables
    extracted from the URI.

    **Specificity, not registration order.** A template's ``{var}`` matches any
    single segment free of control characters, so ``reports://{report_id}``
    also matches ``reports://all-tenants-summary``. Resolving in registration order would
    make *which permission stack guards a URI* a function of the order the two
    were registered in — and the wrong answer is the permissive one, since the
    template is the general case. Concrete URIs are therefore tried first, and
    only then templates.

    **A URI the template splits more than one way is refused, not guessed
    at.** Where the literal text between two variables also occurs inside a
    value, ``things://{a}-{b}`` fits ``things://1-2-3`` as ``a='1-2', b='3'``
    and as ``a='1', b='2-3'``, and either answer would hand the selector
    arguments the client may not have meant. ``resolve`` matches each URI with
    every variable as long as it can be and again with every variable as short
    as it can be; the first is the largest split by length in variable order,
    the second the smallest, so they agree exactly when only one split exists.
    When they differ, ``resolve`` raises ``ValueError`` naming both, and the
    callers answer it: ``resources/read`` with ``-32602``, a subscription by
    dropping the URI. The template keeps serving every URI it splits one way
    (``test_the_same_template_still_serves_a_uri_it_splits_one_way``).
    """

    def __init__(self) -> None:
        self._bindings: list[ResourceBinding] = []
        # Each template's greedy and lazy patterns, compiled once at
        # registration; see the class docstring for why both.
        self._patterns: dict[str, tuple[re.Pattern[str], re.Pattern[str]]] = {}

    def register(self, binding: ResourceBinding) -> None:
        for existing in self._bindings:
            if existing.uri_template == binding.uri_template:
                raise ValueError(f"Duplicate MCP resource URI: {binding.uri_template!r}")
        self._bindings.append(binding)
        self._patterns[binding.uri_template] = (
            _template_to_pattern(binding.uri_template),
            _template_to_pattern(binding.uri_template, lazy=True),
        )

    def resolve(self, uri: str) -> tuple[ResourceBinding, dict[str, str]] | None:
        """The binding ``uri`` resolves to and the variables read from it, or
        ``None`` when nothing matches.

        Raises ``ValueError`` when the first template ``uri`` matches splits it
        more than one way (see the class docstring). The refusal stops there
        rather than trying later templates: which binding answers a URI must
        not turn on whether its values happen to repeat a template's literals.
        """
        # Concrete before template — see the class docstring. Within each group
        # registration order still decides, which is only reachable for two
        # templates whose patterns overlap.
        for binding in (*self.concrete(), *self.templates()):
            greedy, lazy = self._patterns[binding.uri_template]
            match: re.Match[str] | None = greedy.fullmatch(uri)
            if match is None:
                continue
            variables: dict[str, str] = match.groupdict()
            # Both patterns accept exactly the same URIs, since only the
            # quantifiers differ, so the lazy match exists whenever the greedy
            # one does; ``or match`` only tells the type checker so.
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
        """Exact lookup on the registered template string.

        A caller holding the template itself — the completion API's
        ``ref/resource``, say — must use this rather than ``resolve``:
        ``things://{pk}`` satisfies its own pattern with ``pk="{pk}"``, so
        ``resolve`` would answer, plausibly and wrongly.
        """
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
