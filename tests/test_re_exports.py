"""The kernel boundary: this package does not re-export the kernel's API.

A kernel tool is registered from ``django_service_specs`` names: the spec
classes, ``SelectorKind``, ``Parameter`` / ``Parameters``, a ``PermissionCheck``,
the dataclass Validator and Presenter, ``PoolSeeds``. Each is importable from the
kernel's own root, and a re-export from here would pin this package's public API
to that layout, so a move there would break an import from here. No fresh spec
package re-exports a kernel name from its root.
"""

from __future__ import annotations

import django_service_specs
import pytest

import django_spec_mcp

KERNEL = [
    "Affordance",
    "DataclassPresenter",
    "DataclassValidator",
    "DEFAULT_POOL_SEEDS",
    "Parameter",
    "Parameters",
    "PermissionCheck",
    "PoolSeeds",
    "SelectorKind",
    "SelectorSpec",
    "ServerArguments",
    "ServiceSpec",
    "SpecRegistry",
    "Unrestricted",
]


@pytest.mark.parametrize("name", KERNEL)
def test_a_kernel_symbol_is_not_re_exported(name: str) -> None:
    assert not hasattr(django_spec_mcp, name)
    assert name not in django_spec_mcp.__all__


@pytest.mark.parametrize("name", KERNEL)
def test_it_is_importable_from_the_kernels_root(name: str) -> None:
    """The import a registration writes, asserted rather than described."""
    assert name in django_service_specs.__all__


def test_no_kernel_name_is_re_exported_from_the_root() -> None:
    shared = set(django_spec_mcp.__all__) & set(django_service_specs.__all__)

    assert shared - {"__version__"} == set()
