from __future__ import annotations

from pathlib import Path

import pytest

REFERENCE = Path(__file__).resolve().parents[1] / "artifacts" / "reference"


def _run_or_skip(name: str):
    from osemosys_vs_pypsa.runio import Run

    root = REFERENCE / name
    if not root.is_dir():
        pytest.skip(f"reference artefacts not fetched: {root} (run fetch_artifacts.sh)")
    return Run(root)


@pytest.fixture(scope="session")
def gurobi_run():
    """The reference run where both sides solved to optimality."""
    return _run_or_skip("step1-gurobi")


@pytest.fixture(scope="session")
def highs_run():
    """The reference run REPORT.md's solve-time comparison is based on."""
    return _run_or_skip("step1-highs")
