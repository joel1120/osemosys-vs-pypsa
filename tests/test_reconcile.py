"""The objective reconciliation is the claim most likely to break silently.

If a future tz-osemosys adds the dropped constant back to the objective, or PyPSA
starts charging fixed O&M on non-extendable components, these tests fail rather
than the notebook quietly reporting a wrong gap.
"""

from __future__ import annotations

import pytest

from osemosys_vs_pypsa.reconcile import (
    check_objective_identity,
    compare,
    osemosys_costs,
    pypsa_costs,
    residual_fixed_om,
)

# From ce_pypsa_benchmarking/REPORT.md and the solver logs.
OSEMOSYS_LP_OBJECTIVE = 5.9745680454e05
PYPSA_LP_OBJECTIVE_MILLIONS = 607472.990711
REPORTED_GAP_PCT = 1.68


def test_saved_total_is_not_the_lp_objective(gurobi_run):
    """The trap itself: the saved variable is ~61% above what was minimised."""
    costs = osemosys_costs(gurobi_run.osemosys_solution)
    assert costs["total_discounted_cost"] == pytest.approx(961739.2, rel=1e-5)
    assert costs["total_discounted_cost"] > 1.5 * costs["lp_objective"]


def test_subtracting_the_constant_recovers_the_solver_objective(gurobi_run):
    discrepancy = check_objective_identity(gurobi_run.osemosys_solution, OSEMOSYS_LP_OBJECTIVE)
    assert discrepancy < 1e-3, f"reconciliation drifted by {discrepancy} $m"


def test_residual_fom_convention_ordering(gurobi_run):
    """PyPSA's start-of-year discounting values the same cost (1+r)^0.5 higher."""
    solution = gurobi_run.osemosys_solution
    osemosys_style = residual_fixed_om(solution, "osemosys")
    pypsa_style = residual_fixed_om(solution, "pypsa")
    assert osemosys_style == pytest.approx(364282.4, rel=1e-5)
    assert pypsa_style == pytest.approx(369706.2, rel=1e-5)
    assert 1.0 < pypsa_style / osemosys_style < 1.02


def test_pypsa_objective_carries_no_constant(gurobi_run):
    """The residual fleet is non-extendable, so it never enters the objective."""
    network = gurobi_run.pypsa_network
    assert pypsa_costs(network)["objective_constant"] == 0.0
    residual = network.generators[~network.generators.p_nom_extendable]
    assert (residual.fom_cost == 0).all()


def test_gap_matches_the_report(gurobi_run):
    result = compare(gurobi_run.osemosys_solution, gurobi_run.pypsa_network)
    assert result["osemosys"]["lp_objective"] == pytest.approx(OSEMOSYS_LP_OBJECTIVE, rel=1e-6)
    assert result["pypsa"]["lp_objective"] == pytest.approx(PYPSA_LP_OBJECTIVE_MILLIONS, rel=1e-6)
    assert result["difference_pct"] == pytest.approx(REPORTED_GAP_PCT, abs=0.01)
