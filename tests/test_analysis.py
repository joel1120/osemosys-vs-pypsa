"""The frameworks agreeing is the premise of the whole comparison."""

from __future__ import annotations

import pytest

from osemosys_vs_pypsa import analysis as A


@pytest.fixture(scope="session")
def solved(gurobi_run):
    return gurobi_run.osemosys_solution, gurobi_run.pypsa_network


def test_new_capacity_agrees(solved):
    capacity = A.new_capacity_by_technology(*solved)
    assert capacity[A.OSEMOSYS].sum() == pytest.approx(103.474, rel=1e-4)
    assert capacity["difference"].abs().max() < 1e-5


def test_generation_agrees_to_within_a_hundredth_of_a_percent(solved):
    generation = A.generation_by_technology(*solved)
    summary = A.agreement_summary(A.new_capacity_by_technology(*solved), generation)
    assert summary["generation_total_osemosys_TWh"] == pytest.approx(27440.9, rel=1e-4)
    assert summary["generation_max_abs_diff_pct"] < 0.05


def test_investment_lands_in_the_same_years(solved):
    by_year = A.new_capacity_by_year(*solved)
    built = by_year[by_year.sum(axis=1) > 0]
    assert len(built) > 1
    # Per-year splits can move between adjacent vintages; the total cannot.
    assert built[A.OSEMOSYS].sum() == pytest.approx(built[A.PYPSA].sum(), rel=1e-4)


def test_lp_comparison_blanks_incomparable_ratios(highs_run):
    table = A.lp_comparison(highs_run.osemosys_log, highs_run.pypsa_log)
    ratios = table["PyPSA / OSeMOSYS"]
    assert ratios["rows"] == pytest.approx(22.995, rel=1e-3)
    assert ratios["dual_pushes"] == pytest.approx(33.399, rel=1e-3)
    # Objectives are in different units ($m vs $), so a ratio would be nonsense.
    assert ratios[["objective", "cost_range", "solver", "status"]].isna().all()


def test_charts_render(solved):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    A.apply_theme()
    generation = A.generation_by_technology(*solved)
    fig, axes = plt.subplots(1, 2)
    A.plot_generation_by_technology(generation, axes[0])
    A.plot_generation_difference(generation, axes[1])
    plt.close(fig)
