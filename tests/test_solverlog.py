from __future__ import annotations


def test_highs_osemosys_log(highs_run):
    log = highs_run.osemosys_log
    assert log.solver == "highs"
    assert log.completed
    assert (log.rows, log.cols, log.nonzeros) == (576828, 499184, 8909136)
    assert log.presolve_rows == 450062
    assert log.ipm_iterations == 90
    assert log.dual_pushes == 180374
    assert log.crossover_status == "optimal"
    assert log.total_runtime_seconds == 964.60


def test_highs_pypsa_log_is_truncated(highs_run):
    log = highs_run.pypsa_log
    assert not log.completed
    assert (log.rows, log.cols, log.nonzeros) == (13264304, 6622352, 26842400)
    assert log.ipm_iterations == 151
    assert log.dual_pushes == 6024255
    assert log.crossover_status == "imprecise"
    # No final runtime, so wall clock falls back to the last simplex timestamp.
    assert log.total_runtime_seconds is None
    assert log.wall_clock_seconds == 23720.5
    assert any("excessively large costs" in w for w in log.warnings)


def test_gurobi_logs_are_understood(gurobi_run):
    osemosys, pypsa = gurobi_run.osemosys_log, gurobi_run.pypsa_log
    assert osemosys.solver == pypsa.solver == "gurobi"
    assert osemosys.completed and pypsa.completed
    assert osemosys.rows == 576828
    assert pypsa.rows == 13264304
    assert osemosys.total_runtime_seconds == 35.84
    assert pypsa.total_runtime_seconds == 908.29


def test_cost_range_shows_the_conditioning_gap(highs_run):
    assert highs_run.osemosys_log.cost_range == (3e-3, 2e4)
    assert highs_run.pypsa_log.cost_range == (3e1, 2e7)
