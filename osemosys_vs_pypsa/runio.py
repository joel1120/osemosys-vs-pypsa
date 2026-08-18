"""Load a run directory written by ``run_benchmark.py``.

One run directory holds both sides of a comparison::

    <run>/manifest.json
    <run>/pypsa/{base_network.nc,solved_network.nc,solver.log}
    <run>/osemosys/{solution.nc,solver.log}

Any piece may be missing -- a PyPSA solve that ran out of wall clock leaves a
log and no solved network -- so every accessor returns ``None`` rather than
raising, and ``describe()`` reports what is actually present.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

from osemosys_vs_pypsa.solverlog import SolveLog, parse

if TYPE_CHECKING:
    import pypsa
    import xarray as xr


@dataclass
class Run:
    """A single ``run_benchmark.py`` output directory."""

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        if not self.root.is_dir():
            raise FileNotFoundError(f"no such run directory: {self.root}")

    @cached_property
    def manifest(self) -> dict[str, Any]:
        path = self.root / "manifest.json"
        return json.loads(path.read_text()) if path.exists() else {}

    @property
    def label(self) -> str:
        manifest = self.manifest
        step = manifest.get("build_year_step", "?")
        solver = manifest.get("solver", "?")
        return f"{self.root.name} (step {step}, {solver})"

    def _maybe(self, *parts: str) -> Path | None:
        path = self.root.joinpath(*parts)
        return path if path.exists() else None

    @cached_property
    def osemosys_solution(self) -> xr.Dataset | None:
        """Solved OSeMOSYS dataset, or None if the solve produced none."""
        import xarray as xr

        path = self._maybe("osemosys", "solution.nc")
        return xr.open_dataset(path) if path else None

    @cached_property
    def pypsa_network(self) -> pypsa.Network | None:
        """Solved PyPSA network, falling back to None if the solve never finished."""
        import pypsa

        path = self._maybe("pypsa", "solved_network.nc")
        return pypsa.Network(str(path)) if path else None

    @cached_property
    def pypsa_base_network(self) -> pypsa.Network | None:
        """Unsolved PyPSA network as built from the model.json."""
        import pypsa

        path = self._maybe("pypsa", "base_network.nc")
        return pypsa.Network(str(path)) if path else None

    @cached_property
    def osemosys_log(self) -> SolveLog | None:
        path = self._maybe("osemosys", "solver.log")
        return parse(path) if path else None

    @cached_property
    def pypsa_log(self) -> SolveLog | None:
        path = self._maybe("pypsa", "solver.log")
        return parse(path) if path else None

    def describe(self) -> dict[str, Any]:
        """What this run directory contains."""
        manifest = self.manifest
        return {
            "run": self.root.name,
            "build_year_step": manifest.get("build_year_step"),
            "solver": manifest.get("solver"),
            "mock_solve": manifest.get("mock_solve", False),
            "osemosys_solution": self._maybe("osemosys", "solution.nc") is not None,
            "pypsa_solved_network": self._maybe("pypsa", "solved_network.nc") is not None,
            "osemosys_log": self._maybe("osemosys", "solver.log") is not None,
            "pypsa_log": self._maybe("pypsa", "solver.log") is not None,
        }
