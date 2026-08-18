"""Make the two solved objectives comparable.

The headline trap: ``model.solution.nc``'s ``TotalDiscountedCost`` is **not** the
OSeMOSYS LP objective. linopy drops the objective's constant term (see the TODO
in ``tz.osemosys.Model.solve``), so the solver minimises everything *except*
fixed O&M on ``ResidualCapacity``, while the saved variable includes it. For the
Japan benchmark that is 961,739 $m saved against a 597,457 $m solver objective.

PyPSA excludes the same term for an unrelated reason. ``define_objective``
draws its capital and fixed-cost terms from ``c.extendables``, and the residual
fleet is modelled as *non-extendable* components -- so it is outside that index
entirely and pays no ``fom_cost`` (verified: all 2,007 residual generators carry
``fom_cost == 0``). The separate ``include_objective_constant`` term credits
already-built ``p_nom`` on extendable components, and every extendable vintage
here starts at ``p_nom = 0``, so ``n.objective_constant`` is 0.0.

Both sides therefore exclude residual fixed O&M, and the LP objectives are
directly comparable. What remains between them is the two documented convention
differences (annuity-due, mid-year opex discounting).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import pypsa
    import xarray as xr

DOLLARS_PER_MILLION = 1e6

Convention = Literal["osemosys", "pypsa"]


def _discount_factor(solution: xr.Dataset, convention: Convention) -> xr.DataArray:
    """Per-(region, year) discount factor for an operating cost.

    OSeMOSYS discounts opex to mid-year, PyPSA to the start of the period.
    """
    offset = 0.5 if convention == "osemosys" else 0.0
    first_year = int(solution.YEAR.min())
    return (1.0 + solution.DiscountRate) ** -((solution.YEAR - first_year) + offset)


def residual_fixed_om(solution: xr.Dataset, convention: Convention = "osemosys") -> float:
    """Discounted fixed O&M charged on ``ResidualCapacity``, in $m.

    This is the term neither LP objective contains. It is a pure constant --
    residual capacity is exogenous, so no dispatch or investment decision moves
    with it -- but it must be added to both sides, or neither, before comparing
    against a cost total that includes it.
    """
    charge = solution.FixedCost * solution.ResidualCapacity * _discount_factor(solution, convention)
    return float(charge.sum())


def osemosys_costs(solution: xr.Dataset) -> dict[str, float]:
    """Discounted cost components from a solved OSeMOSYS dataset, in $m."""
    total = float(solution.TotalDiscountedCost.sum())
    residual_fom = residual_fixed_om(solution)
    return {
        "capital_investment": float(solution.DiscountedCapitalInvestment.sum()),
        "fixed_om": float(solution.DiscountedFixedOperatingCost.sum()),
        "variable_om": float(solution.DiscountedVariableOperatingCost.sum()),
        "salvage_value": float(solution.DiscountedSalvageValue.sum()),
        "trade": float(solution.TotalDiscountedCostTrade.sum()),
        "total_discounted_cost": total,
        "residual_fixed_om": residual_fom,
        "lp_objective": total - residual_fom,
    }


def pypsa_costs(network: pypsa.Network) -> dict[str, float]:
    """PyPSA objective in $m, with the constant PyPSA is known to omit."""
    constant = float(getattr(network, "objective_constant", 0.0) or 0.0)
    return {
        "lp_objective": float(network.objective) / DOLLARS_PER_MILLION,
        "objective_constant": constant / DOLLARS_PER_MILLION,
    }


def compare(solution: xr.Dataset, network: pypsa.Network) -> dict[str, Any]:
    """Comparable objectives for both frameworks, in $m.

    ``difference_pct`` is what the convention differences in ``METHODOLOGY.md``
    have to explain -- annuity-due (PyPSA charges ``(1 + r_idv)x`` OSeMOSYS's
    capital cost) and start-of-year opex discounting (``(1 + r)^0.5`` higher).
    """
    osemosys = osemosys_costs(solution)
    pypsa_side = pypsa_costs(network)
    left, right = osemosys["lp_objective"], pypsa_side["lp_objective"]
    return {
        "osemosys": osemosys,
        "pypsa": pypsa_side,
        "difference": right - left,
        "difference_pct": 100.0 * (right - left) / left,
    }


def check_objective_identity(solution: xr.Dataset, solver_objective: float) -> float:
    """Assert the saved dataset reproduces the solver's objective.

    Returns the absolute discrepancy in $m. A nonzero result means the
    constant-term accounting above no longer matches tz-osemosys.
    """
    derived = osemosys_costs(solution)["lp_objective"]
    return abs(derived - solver_objective)
