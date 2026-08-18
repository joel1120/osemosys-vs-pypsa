#!/usr/bin/env python3
# ruff: noqa: T201  # CLI prints
"""Solve one capacity-expansion model with both frameworks and save everything.

Runs the full chain in one command: simplify the tz-osemosys ``model.json``,
build the matching PyPSA network, solve both sides on the same solver, and write
the solved artefacts, solver logs and a manifest into an output directory that
``comparison.ipynb`` reads directly.

    python run_benchmark.py artifacts/model.json --outdir runs/step5

Read the runtime table in README.md before choosing ``--build-year-step 1``.
"""

from __future__ import annotations

import argparse
import json
import logging
import platform
import sys
import time
from importlib import metadata
from pathlib import Path
from typing import Any

from osemosys_vs_pypsa.build_years import build_years_for, restrict_build_years
from osemosys_vs_pypsa.converter import build_network
from osemosys_vs_pypsa.simplify_model import simplify, verify

logger = logging.getLogger("run_benchmark")

PACKAGES = ("pypsa", "linopy", "tz-osemosys", "highspy", "gurobipy", "xarray", "pandas", "numpy")


class Stage:
    """Time a stage and report it as it runs."""

    def __init__(self, label: str, timings: dict[str, float]) -> None:
        self.label = label
        self.timings = timings

    def __enter__(self) -> Stage:
        print(f"[{self.label}] starting", flush=True)
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.elapsed = time.perf_counter() - self.start
        self.timings[self.label] = self.elapsed
        outcome = "failed" if exc[0] is not None else "done"
        print(f"[{self.label}] {outcome} in {self.elapsed:.1f} s", flush=True)


def _versions() -> dict[str, str]:
    out = {"python": platform.python_version(), "platform": platform.platform()}
    for package in PACKAGES:
        try:
            out[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            out[package] = "not installed"
    return out


def _lp_shape(model: Any) -> dict[str, int]:
    """Variable and constraint counts from a built linopy model."""
    return {
        "variables": int(getattr(model, "nvars", -1)),
        "constraints": int(getattr(model, "ncons", -1)),
    }


def _solver_options(solver: str, threads: int, crossover: bool) -> dict[str, Any]:
    """Solver options matching the reference runs.

    ``solver="hipo"`` is what the reference HiGHS runs logged -- the new
    interior-point solver in HiGHS 1.15.1. ("ipm" routes to the same code path,
    but the reference invocation is reproduced verbatim here.)
    """
    if solver == "highs":
        options: dict[str, Any] = {"threads": threads, "solver": "hipo", "run_crossover": "on"}
        if not crossover:
            options["run_crossover"] = "off"
        return options
    if solver == "gurobi":
        return {"Threads": threads, "Method": 2, "Crossover": -1 if crossover else 0}
    return {}


def run_pypsa(
    model: dict[str, Any],
    outdir: Path,
    *,
    build_year_step: int,
    solver: str,
    solver_options: dict[str, Any],
    write_lp: bool,
    mock_solve: bool,
    timings: dict[str, float],
) -> dict[str, Any]:
    """Build, solve and save the PyPSA side."""
    outdir.mkdir(parents=True, exist_ok=True)

    with Stage("pypsa.build", timings):
        network = build_network(model, build_year_step=build_year_step)
        network.export_to_netcdf(outdir / "base_network.nc")

    if hasattr(network, "sanitize"):
        network.sanitize()

    with Stage("pypsa.create_model", timings):
        network.optimize.create_model(
            multi_investment_periods=True,
            include_objective_constant=True,
        )
    shape = _lp_shape(network.model)
    print(f"  LP: {shape['variables']:,} variables, {shape['constraints']:,} constraints")

    solve_kwargs: dict[str, Any] = {
        "solver_name": solver,
        "log_fn": str(outdir / "solver.log"),
        "mock_solve": mock_solve,
    }
    if write_lp:
        solve_kwargs |= {
            "problem_fn": str(outdir / "problem.lp"),
            "io_api": "lp",
            "explicit_coordinate_names": True,
            "keep_files": True,
        }

    with Stage("pypsa.solve", timings):
        status, condition = network.optimize.solve_model(**solve_kwargs, **solver_options)

    network.export_to_netcdf(outdir / "solved_network.nc")
    objective = float(network.objective) if status == "ok" else None
    print(f"  status={status} condition={condition} objective={objective}")

    return {
        "status": status,
        "termination_condition": condition,
        "objective_dollars": objective,
        "lp_shape": shape,
        "generators": int(len(network.generators)),
        "extendable_generators": int(network.generators.p_nom_extendable.sum()),
        "links": int(len(network.links)),
        "snapshots": int(len(network.snapshots)),
        "investment_periods": [int(y) for y in network.investment_periods],
    }


def run_osemosys(
    model: dict[str, Any],
    outdir: Path,
    *,
    build_year_step: int,
    solver: str,
    solver_options: dict[str, Any],
    write_lp: bool,
    mock_solve: bool,
    timings: dict[str, float],
) -> dict[str, Any]:
    """Restrict build years to match PyPSA, then build, solve and save."""
    from tz.osemosys import Model

    # tz-osemosys 0.4.0 exposes the built linopy model, the solution dataset and
    # the objective only as private attributes; there is no public accessor. The
    # version is pinned in requirements.txt for exactly this reason.
    outdir.mkdir(parents=True, exist_ok=True)

    restricted = restrict_build_years(json.loads(json.dumps(model)), build_year_step)
    if build_year_step > 1:
        (outdir / "model_restricted.json").write_text(json.dumps(restricted))

    with Stage("osemosys.validate", timings):
        osemosys_model = Model.model_validate(restricted)

    solve_kwargs: dict[str, Any] = {
        "solver_name": solver,
        "log_fn": str(outdir / "solver.log"),
        "mock_solve": mock_solve,
    }
    if write_lp:
        solve_kwargs |= {
            "problem_fn": str(outdir / "problem.lp"),
            "io_api": "lp",
            "explicit_coordinate_names": True,
            "keep_files": True,
        }

    with Stage("osemosys.build_and_solve", timings):
        status, condition = osemosys_model.solve(solver_options=solver_options, **solve_kwargs)

    shape = _lp_shape(osemosys_model._m)
    print(f"  LP: {shape['variables']:,} variables, {shape['constraints']:,} constraints")
    print(f"  status={status} condition={condition}")

    objective = None
    if status == "ok":
        osemosys_model._solution.to_netcdf(outdir / "solution.nc")
        objective = float(osemosys_model._objective)

    return {
        "status": status,
        "termination_condition": condition,
        "total_discounted_cost_millions": objective,
        "lp_shape": shape,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_benchmark.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("model", type=Path, help="raw tz-osemosys model.json")
    parser.add_argument("--outdir", type=Path, required=True, help="run directory to write")
    parser.add_argument(
        "--build-year-step",
        type=int,
        default=5,
        metavar="N",
        help=(
            "offer investment every N years on BOTH sides. 1 reproduces the "
            "headline run and needs Gurobi -- see README.md (default: 5)"
        ),
    )
    parser.add_argument("--solver", default="highs", choices=("highs", "gurobi"))
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument(
        "--no-crossover",
        action="store_true",
        help="stop after the interior-point solve; no basic solution",
    )
    parser.add_argument("--skip-pypsa", action="store_true")
    parser.add_argument("--skip-osemosys", action="store_true")
    parser.add_argument(
        "--write-lp",
        action="store_true",
        help="also write labelled .lp files -- 0.9 GB (OSeMOSYS) and 5.8 GB (PyPSA) at step 1",
    )
    parser.add_argument(
        "--mock-solve",
        action="store_true",
        help="build both LPs and skip the solve; validates the chain in minutes",
    )
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)
    if args.build_year_step < 1:
        parser.error("--build-year-step must be >= 1")
    if args.skip_pypsa and args.skip_osemosys:
        parser.error("nothing to do: both sides skipped")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    args.outdir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}

    with Stage("simplify", timings):
        raw = json.loads(args.model.read_text())
        simplified, report = simplify(raw)
        verify(simplified)
        (args.outdir / "model_simplified.json").write_text(json.dumps(simplified))
    for key, value in report.items():
        print(f"  {key}: {value}")

    years = [int(y) for y in simplified["time_definition"]["years"]]
    build_years = build_years_for(years, args.build_year_step)
    print(f"  years: {years[0]}-{years[-1]}  build years: {len(build_years)} ({args.build_year_step}-yearly)")

    solver_options = _solver_options(args.solver, args.threads, not args.no_crossover)
    shared = {
        "build_year_step": args.build_year_step,
        "solver": args.solver,
        "solver_options": solver_options,
        "write_lp": args.write_lp,
        "mock_solve": args.mock_solve,
        "timings": timings,
    }

    manifest: dict[str, Any] = {
        "model": str(args.model),
        "build_year_step": args.build_year_step,
        "build_years": build_years,
        "solver": args.solver,
        "solver_options": solver_options,
        "mock_solve": args.mock_solve,
        "simplify_report": report,
        "versions": _versions(),
    }

    if not args.skip_pypsa:
        manifest["pypsa"] = run_pypsa(simplified, args.outdir / "pypsa", **shared)
    if not args.skip_osemosys:
        manifest["osemosys"] = run_osemosys(simplified, args.outdir / "osemosys", **shared)

    manifest["timings_seconds"] = timings
    (args.outdir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"\nwrote {args.outdir}/manifest.json")
    for label, seconds in timings.items():
        print(f"  {label:28s} {seconds:9.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
