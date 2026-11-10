from __future__ import annotations

import re
from typing import Any

import pytest

from django_spec_mcp.ui.build_app_document import build_app_document

BODY = '<div id="rows">Waiting.</div>'


def _html_tag(document: str) -> str:
    """The root element's open tag."""
    match = re.search(r"<html[^>]*>", document)
    assert match is not None
    return match.group(0)


@pytest.fixture
def document() -> str:
    return build_app_document(BODY, title="Invoices")


class TestDocumentShape:
    def test_the_three_implied_elements_are_written_out(self, document: str) -> None:
        """HTML5 infers ``html``/``head``/``body`` and browsers do not care."""
        assert document.startswith('<!doctype html>\n<html lang="en">')
        assert "<head>" in document
        assert "<body>" in document
        assert document.rstrip().endswith("</html>")

    def test_the_fragment_is_inserted_verbatim_inside_the_content_element(
        self, document: str
    ) -> None:
        """The root is not decoration: the bridge measures it rather than documentElement."""
        assert f'<div id="mcp-app-root">{BODY}</div>' in document

    def test_the_title_is_escaped(self) -> None:
        document = build_app_document(BODY, title='Invoices & "co" <b>')
        assert "<title>Invoices &amp; &quot;co&quot; &lt;b&gt;</title>" in document

    def test_the_bridge_is_inlined_ahead_of_the_body(self, document: str) -> None:
        """A fragment assigns ``mcpApp.onToolResult`` while the parser is still running."""
        assert "window.mcpApp = mcpApp;" in document
        assert document.index("window.mcpApp") < document.index("<body>")


class TestNothingIsFetched:
    """The property that keeps a view bootable with an empty CSP."""

    def test_no_external_subresource(self, document: str) -> None:
        assert not re.search(r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//""", document)

    def test_no_module_import_or_css_import(self, document: str) -> None:
        assert "@import" not in document
        assert not re.search(r"""\bfrom\s+["']https?://""", document)


class TestTheShellStaysOutOfTheWay:
    def test_it_paints_no_background(self, document: str) -> None:
        """prefersBorder asks the host for a frame; the host draws its own chrome."""
        assert "background: transparent;" in document

    def test_it_names_no_colour_of_its_own(self, document: str) -> None:
        """The same view is drawn in light and dark hosts."""
        assert "color-scheme: light dark;" in document
        assert not re.search(r"#[0-9a-fA-F]{3,6}\b", document.split("</style>")[0])


class TestDiagnosticsFollowDebug:
    """Whether a protocol failure is written into the document, or only logged."""

    @pytest.mark.parametrize(
        ("debug", "diagnostics", "shown"),
        [
            (False, None, False),
            (True, None, True),
            (False, True, True),
            (True, False, False),
        ],
        ids=["off-outside-debug", "on-under-debug", "explicit-true-wins", "explicit-false-wins"],
    )
    def test_the_default_follows_debug_and_an_explicit_value_wins(
        self, settings: Any, debug: bool, diagnostics: bool | None, shown: bool
    ) -> None:
        settings.DEBUG = debug
        document = build_app_document(BODY, title="t", diagnostics=diagnostics)
        assert _html_tag(document) == (
            '<html lang="en" data-mcp-diagnostics="1">' if shown else '<html lang="en">'
        )
