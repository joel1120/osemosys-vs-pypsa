"""Restrict OSeMOSYS investment to the years PyPSA is offered.

``converter.build_network`` offers investment in ``years[::build_year_step]``.
OSeMOSYS offers ``NewCapacity[r,t,y]`` in every year regardless, so a run with
``build_year_step > 1`` compares a coarse PyPSA against a fine OSeMOSYS and the
solve-time comparison is meaningless. This module closes that gap by forbidding
new capacity outside the shared build years.
"""

from __future__ import annotations

from typing import Any


def build_years_for(years: list[int], step: int) -> list[int]:
    """The build years ``converter.build_network`` offers at ``build_year_step = step``."""
    return years[::step]


def _zero_years(forbidden: list[int]) -> dict[str, float]:
    """Years absent from the mapping come out as NaN.

    tz-osemosys masks the investment constraint on
    ``TotalAnnualMaxCapacityInvestment >= 0``, so a sparse mapping leaves the
    build years genuinely unconstrained rather than bounding them at some large
    finite number that would widen the RHS range.
    """
    return {str(year): 0.0 for year in forbidden}


def _forbid_ry(regions: list[str], forbidden: list[int]) -> dict[str, Any]:
    """An ``OSeMOSYSData.RY`` envelope pinning the given years to zero."""
    return {
        "is_composed": True,
        "data": {region: _zero_years(forbidden) for region in regions},
    }


def _forbid_rry(route_pairs: dict[str, list[str]], forbidden: list[int]) -> dict[str, Any]:
    """An ``OSeMOSYSData.RRY`` envelope over the model's own region pairs."""
    return {
        "is_composed": True,
        "data": {
            region: {other: _zero_years(forbidden) for other in others}
            for region, others in route_pairs.items()
        },
    }


def _route_pairs(trade: dict[str, Any]) -> dict[str, list[str]]:
    routes = trade.get("trade_routes")
    data = routes["data"] if isinstance(routes, dict) and "data" in routes else routes
    return {region: list(others) for region, others in (data or {}).items()}


def restrict_build_years(model: dict[str, Any], step: int) -> dict[str, Any]:
    """Forbid technology and trade investment outside ``years[::step]``.

    Mutates and returns ``model``. A ``step`` of 1 is a no-op.
    """
    if step < 1:
        raise ValueError(f"step must be >= 1, got {step}")
    if step == 1:
        return model

    years = [int(y) for y in model["time_definition"]["years"]]
    regions = [region["id"] for region in model["regions"]]
    allowed = set(build_years_for(years, step))
    forbidden = [y for y in years if y not in allowed]

    for technology in model["technologies"]:
        if technology.get("capacity_additional_max") is not None:
            raise ValueError(
                f"{technology['id']} already sets capacity_additional_max -- "
                "restricting build years would overwrite it"
            )
        technology["capacity_additional_max"] = _forbid_ry(regions, forbidden)

    for trade in model.get("trade") or []:
        if trade.get("capacity_additional_max") is not None:
            raise ValueError(
                f"trade {trade.get('commodity')} already sets capacity_additional_max"
            )
        trade["capacity_additional_max"] = _forbid_rry(_route_pairs(trade), forbidden)

    return model
