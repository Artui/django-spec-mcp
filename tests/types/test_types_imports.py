"""``types/`` imports nothing else in the package.

Every subpackage reads these values, and each subpackage's ``__init__``
re-exports its whole surface, so a ``types/`` module importing one of them would
make the first import of that subpackage a cycle. Walked from the source rather
than from ``sys.modules``, so a name imported only under some condition is still
seen.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import django_spec_mcp.types

TYPES = Path(django_spec_mcp.types.__file__).parent


def _package_imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
    return [name for name in found if name.split(".")[0] == "django_spec_mcp"]


@pytest.mark.parametrize("path", sorted(TYPES.glob("*.py")), ids=lambda path: path.name)
def test_a_types_module_imports_only_types(path: Path) -> None:
    outside = [
        name for name in _package_imports(path) if not name.startswith("django_spec_mcp.types.")
    ]
    assert outside == []


def test_the_walk_sees_the_package() -> None:
    # A glob that found nothing would pass every row above.
    assert len(list(TYPES.glob("*.py"))) > 40
