from __future__ import annotations

from functools import lru_cache
from importlib.resources import files

from django.conf import settings
from django.utils.html import escape

# Deliberately close to nothing. The view is drawn inside the host's own chrome,
# in a light or dark theme this document does not choose, and `prefersBorder`
# already asks the host for a frame -- so a shell that painted a background, a
# card or a padding box would be a second frame inside the first, which is the
# note every consumer eventually writes back to us about.
#
# `color-scheme` is what makes an unstyled view legible in both themes without
# naming a single colour: it hands the UA's own `Canvas` / `CanvasText` pair to
# the document. Host-supplied CSS variables win where they exist, and the view's
# own `<style>` comes later in the document, so it wins over everything here.
_BASE_STYLES = """
:root { color-scheme: light dark; }
html[data-theme="light"] { color-scheme: light; }
html[data-theme="dark"] { color-scheme: dark; }
body {
  margin: 0;
  background: transparent;
  color: var(--text-primary, CanvasText);
  font-family: var(--font-family, system-ui, -apple-system, "Segoe UI", sans-serif);
  font-size: var(--font-size, 13px);
  line-height: 1.5;
}
#mcp-app-error {
  margin: 0 0 8px;
  padding: 8px 10px;
  white-space: pre-wrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  border: 1px solid var(--border, currentColor);
  border-radius: 4px;
}
"""


def build_app_document(body: str, *, title: str, diagnostics: bool | None = None) -> str:
    """Wrap a view's markup in a complete MCP Apps document."""
    show = settings.DEBUG if diagnostics is None else diagnostics
    flag = ' data-mcp-diagnostics="1"' if show else ""
    return (
        "<!doctype html>\n"
        f'<html lang="en"{flag}>\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(title)}</title>\n"
        f"<style>{_BASE_STYLES}</style>\n"
        f"<script>{_bridge_source()}</script>\n"
        "</head>\n"
        "<body>\n"
        f'<div id="mcp-app-root">{body}</div>\n'
        "</body>\n"
        "</html>\n"
    )


@lru_cache(maxsize=1)
def _bridge_source() -> str:
    """The bridge, read from the packaged file once per process."""
    return files("django_spec_mcp.ui").joinpath("bridge.js").read_text(encoding="utf-8")


__all__ = ["build_app_document"]
