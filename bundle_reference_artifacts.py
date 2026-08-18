#!/usr/bin/env python3
# ruff: noqa: T201
"""Repackage the original benchmark artefacts into the ``run_benchmark.py`` layout.

Maintainer script, run once to produce the bundle that ``fetch_artifacts.sh``
downloads. The original runs predate ``run_benchmark.py``, so their manifests are
reconstructed here from the solver logs and the saved artefacts, and flagged
``reconstructed: true``.

Two reference runs are published, because no single original run has both halves:

* ``step1-gurobi`` -- both sides solved to optimality. Use for results agreement.
* ``step1-highs``  -- the solve-time comparison in REPORT.md. OSeMOSYS finished;
  PyPSA did not, so it has a log and no solved network.

    python bundle_reference_artifacts.py --source /path/to/ce_pypsa_benchmarking \
        --raw-model /path/to/local-runs/builds/testing/model.json --outdir artifacts
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from osemosys_vs_pypsa.solverlog import parse

REFERENCE_VERSIONS = {
    "python": "3.13",
    "pypsa": "1.2.4",
    "linopy": "0.9.0",
    "tz-osemosys": "0.4.0",
    "highspy": "1.15.1",
    "gurobipy": "13.0.2",
    "xarray": "2025.3.1",
    "pandas": "2.2.3",
    "numpy": "2.3.3",
}

RUNS = {
    "step1-gurobi": {
        "solver": "gurobi",
        "note": "Both sides optimal. Use this run for results agreement.",
        "osemosys": {"solution": "osemosys/gurobi/model.solution.nc", "log": "osemosys/gurobi/gurobi.log"},
        "pypsa": {
            "base": "pypsa/gurobi/ce_base_network.nc",
            "solved": "pypsa/gurobi/ce_solved_network.nc",
            "log": "pypsa/gurobi/gurobi.log",
        },
    },
    "step1-highs": {
        "solver": "highs",
        "note": (
            "The REPORT.md solve-time comparison. OSeMOSYS optimal in 964.6 s; "
            "PyPSA truncated at 23,720 s, still in simplex cleanup, so it has no "
            "solved network."
        ),
        "osemosys": {
            "solution": "osemosys/highs/model.solution.nc",
            "log": "osemosys/highs/osemosys_ce_highs.log",
        },
        "pypsa": {"base": "pypsa/highs/ce_base_network.nc", "log": "pypsa/highs/highs.log"},
    },
}


def _copy(source: Path, destination: Path) -> int:
    if not source.exists():
        raise FileNotFoundError(f"missing source artefact: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    size = destination.stat().st_size
    print(f"  {destination}  ({size / 1e6:.1f} MB)")
    return size


def build_run(name: str, spec: dict[str, Any], source: Path, outdir: Path) -> None:
    run_dir = outdir / "reference" / name
    print(f"\n{name}: {spec['note']}")

    logs: dict[str, Any] = {}
    for side in ("osemosys", "pypsa"):
        paths = spec[side]
        if "solution" in paths:
            _copy(source / paths["solution"], run_dir / side / "solution.nc")
        if "base" in paths:
            _copy(source / paths["base"], run_dir / side / "base_network.nc")
        if "solved" in paths:
            _copy(source / paths["solved"], run_dir / side / "solved_network.nc")
        _copy(source / paths["log"], run_dir / side / "solver.log")
        logs[side] = parse(run_dir / side / "solver.log").summary()

    manifest = {
        "reconstructed": True,
        "note": spec["note"],
        "build_year_step": 1,
        "solver": spec["solver"],
        "mock_solve": False,
        "versions": REFERENCE_VERSIONS,
        "parsed_from_logs": logs,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"  wrote {name}/manifest.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True, help="original ce_pypsa_benchmarking directory")
    parser.add_argument("--raw-model", type=Path, required=True, help="raw tz-osemosys model.json")
    parser.add_argument("--simplified-model", type=Path, default=None, help="the model.json actually solved")
    parser.add_argument("--outdir", type=Path, default=Path("artifacts"))
    args = parser.parse_args(argv)

    args.outdir.mkdir(parents=True, exist_ok=True)
    print("input model:")
    _copy(args.raw_model, args.outdir / "model.json")
    if args.simplified_model:
        _copy(args.simplified_model, args.outdir / "reference" / "model_simplified.json")

    for name, spec in RUNS.items():
        build_run(name, spec, args.source, args.outdir)

    total = sum(p.stat().st_size for p in args.outdir.rglob("*") if p.is_file())
    print(f"\nbundle total: {total / 1e6:.1f} MB in {args.outdir}")
    print("The 0.9 GB and 5.8 GB .lp files are deliberately NOT bundled --")
    print("regenerate with run_benchmark.py --write-lp.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
