"""OSeMOSYS vs PyPSA capacity-expansion comparison on identical input data.

The re-exports are lazy (PEP 562): importing this package does not import its
submodules, so ``python -m osemosys_vs_pypsa.converter`` executes the module
once instead of once here and again as ``__main__``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from osemosys_vs_pypsa.build_years import restrict_build_years
    from osemosys_vs_pypsa.converter import build_network
    from osemosys_vs_pypsa.reconcile import compare
    from osemosys_vs_pypsa.runio import Run
    from osemosys_vs_pypsa.simplify_model import simplify
    from osemosys_vs_pypsa.solverlog import parse

__all__ = ["Run", "build_network", "compare", "parse", "restrict_build_years", "simplify"]

_LAZY = {
    "Run": "runio",
    "build_network": "converter",
    "compare": "reconcile",
    "parse": "solverlog",
    "restrict_build_years": "build_years",
    "simplify": "simplify_model",
}


def __getattr__(name: str) -> Any:  # noqa: ANN401  # PEP 562 hook: the attribute type varies
    """Import the submodule owning ``name`` on first access."""
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(f"{__name__}.{module_name}"), name)


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
