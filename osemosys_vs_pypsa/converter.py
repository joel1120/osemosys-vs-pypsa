# ruff: noqa: ANN401, T201  # model.json is untyped JSON; Any is honest. CLI prints.

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pypsa

logger = logging.getLogger(__name__)

HOURS_PER_YEAR = 8760.0
MW_PER_GW = 1e3
MWH_PER_TWH = 1e6

_UNTRANSLATED_TECHNOLOGY_FIELDS = (
    "availability_factor",
    "capacity_factor_annual_min",
    "capacity_gross_max",
    "capacity_gross_min",
    "capacity_additional_max",
    "capacity_additional_min",
    "capacity_additional_max_growth_rate",
    "capacity_additional_max_floor",
    "capacity_additional_min_growth_rate",
    "activity_annual_max",
    "activity_annual_min",
    "activity_total_max",
    "activity_total_min",
    "capacity_one_tech_unit",
    "production_target_min",
    "production_target_max",
    "include_in_joint_reserve_margin",
)

# Fields tz-osemosys requires but simplify_model.py sets to a no-op value.
_NEUTRAL_TECHNOLOGY_VALUES = {"availability_factor": 1.0}

_UNTRANSLATED_TRADE_FIELDS = (
    "availability_factor",
    "capacity_factor_annual_min",
    "activity_annual_max",
    "activity_annual_min",
    "capacity_additional_max",
)


def unwrap(value: Any) -> Any:
    """Unwrap an OSeMOSYSData ``{"is_composed": ..., "data": ...}`` envelope."""
    if isinstance(value, dict) and set(value) == {"is_composed", "data"}:
        return value["data"]
    return value


def _by_year(mapping: Any, years: list[int], default: float | None = None) -> pd.Series | None:
    """Coerce a ``{year: value}`` dict (str or int keys) to a Series over ``years``."""
    mapping = unwrap(mapping)
    if mapping is None:
        return None if default is None else pd.Series(default, index=years, dtype=float)
    series = pd.Series({int(k): float(v) for k, v in mapping.items()}, dtype=float)
    return series.reindex(years)


def _over_snapshots(annual: pd.Series, snapshots: pd.MultiIndex) -> pd.Series:
    """Broadcast a per-year Series across the snapshots of each year."""
    return pd.Series(annual.reindex(snapshots.get_level_values(0)).to_numpy(), index=snapshots)


def _is_neutral(field: str, value: Any) -> bool:
    """True if ``value`` is the no-op setting for ``field``.

    ``simplify_model.py`` neutralises rather than drops the fields tz-osemosys
    requires (``availability_factor = 1.0``), so a set-but-inert field is not
    worth warning about.
    """
    neutral = _NEUTRAL_TECHNOLOGY_VALUES.get(field)
    if neutral is None:
        return False
    return all(v == neutral for by_year in value.values() for v in by_year.values())


def _annual_capacity(annual: pd.Series, years: list[int]) -> list[tuple[int, float]]:
    """Yield ``(year, value)`` for each year the Series carries capacity.

    One entry per year, so each becomes its own component with ``lifetime = 1``.
    Zero years are dropped -- they contribute no capacity.
    """
    return [(year, float(annual[year])) for year in years if annual[year] > 0]


def _is_active(build_year: int, life: float, years: list[int]) -> bool:
    return any(build_year <= y < build_year + life for y in years)


def build_network(
    model: dict[str, Any],
    *,
    build_year_step: int = 1,
    name: str | None = None,
) -> pypsa.Network:
    """Translate a simplified tz-osemosys ``model.json`` dict into a PyPSA network.

    Parameters
    ----------
    model
        Parsed ``model.json``, already run through ``simplify_model.py``.
    build_year_step
        Investment decisions are offered every ``build_year_step`` years. ``1``
        matches OSeMOSYS's ``NewCapacity[r,t,y]`` at the cost of one PyPSA
        component per vintage; larger steps trade fidelity for problem size.
    name
        Network name. Defaults to the model id.
    """
    if model.get("storage"):
        raise ValueError("model still contains storage -- run simplify_model.py first")
    # simplify_model.py neutralises reserve_margin to 1.0 rather than dropping it
    # (tz-osemosys requires the field), so only a real margin is an error here.
    reserve_margin = unwrap(model.get("reserve_margin"))
    if reserve_margin is not None and not all(
        value == 1.0 for by_year in reserve_margin.values() for value in by_year.values()
    ):
        raise ValueError("model still carries a reserve margin -- run simplify_model.py first")

    time_definition = model["time_definition"]
    years: list[int] = sorted(int(y) for y in time_definition["years"])
    timeslices: list[str] = list(time_definition["timeslices"])
    year_split = {k: float(v) for k, v in time_definition["year_split"].items()}
    regions = [r["id"] for r in model["regions"]]
    y0 = years[0]

    discount_rates = {r: float(v) for r, v in unwrap(model["discount_rate"]).items()}
    if len(set(discount_rates.values())) != 1:
        raise NotImplementedError(
            "PyPSA applies one investment-period weighting to the whole network; "
            f"this model has region-specific discount rates: {discount_rates}"
        )
    discount_rate = next(iter(discount_rates.values()))

    n = pypsa.Network(name=name or f"{model['id']}--pypsa")

    snapshots = pd.MultiIndex.from_product([years, timeslices], names=["period", "timestep"])
    n.set_snapshots(snapshots)
    n.investment_periods = years

    hours = pd.Series(
        [year_split[ts] * HOURS_PER_YEAR for _, ts in snapshots], index=snapshots, dtype=float
    )
    for column in ("objective", "generators", "stores"):
        n.snapshot_weightings[column] = hours

    n.investment_period_weightings["years"] = 1.0
    n.investment_period_weightings["objective"] = [(1 + discount_rate) ** -(y - y0) for y in years]

    n.add("Carrier", "electricity")
    n.add("Bus", regions, carrier="electricity")
    _add_loads(n, model, regions, years, snapshots, year_split)

    build_years = years[::build_year_step]
    cost_of_capital_all = unwrap(model.get("cost_of_capital")) or {}
    for technology in model["technologies"]:
        _add_technology(
            n,
            technology,
            regions=regions,
            years=years,
            snapshots=snapshots,
            build_years=build_years,
            cost_of_capital_all=cost_of_capital_all,
            discount_rate=discount_rate,
        )

    for trade in model.get("trade") or []:
        if trade.get("commodity") != "electricity":
            logger.warning("skipping non-electricity trade object %s", trade["id"])
            continue
        _add_trade_links(n, trade, years, snapshots, build_years, discount_rate)

    for unsupported in ("renewable_production_target", "region_group_renewable_production_target"):
        if unwrap(model.get(unsupported)) is not None:
            logger.warning("%s is set but not translated", unsupported)

    logger.info(
        "built %s: %d buses, %d generators, %d links, %d snapshots (%d periods x %d timeslices)",
        n.name,
        len(n.buses),
        len(n.generators),
        len(n.links),
        len(n.snapshots),
        len(years),
        len(timeslices),
    )
    return n


def _add_loads(
    n: pypsa.Network,
    model: dict[str, Any],
    regions: list[str],
    years: list[int],
    snapshots: pd.MultiIndex,
    year_split: dict[str, float],
) -> None:
    electricity = next(c for c in model["commodities"] if c["id"] == "electricity")
    demand_annual = unwrap(electricity["demand_annual"]) or {}
    demand_profile = unwrap(electricity["demand_profile"]) or {}

    names, buses, p_set = [], [], {}
    for region in regions:
        if region not in demand_annual or region not in demand_profile:
            logger.warning("no electricity demand for %s", region)
            continue
        annual = _by_year(demand_annual[region], years)
        profile = demand_profile[region]
        load = f"electricity:{region}"
        p_set[load] = pd.Series(
            [
                annual[y]
                * float(profile[str(y)][ts])
                * MWH_PER_TWH
                / (year_split[ts] * HOURS_PER_YEAR)
                for y, ts in snapshots
            ],
            index=snapshots,
        )
        names.append(load)
        buses.append(region)

    if names:
        n.add("Load", names, bus=buses, p_set=pd.DataFrame(p_set))


def _add_technology(
    n: pypsa.Network,
    technology: dict[str, Any],
    *,
    regions: list[str],
    years: list[int],
    snapshots: pd.MultiIndex,
    build_years: list[int],
    cost_of_capital_all: dict[str, dict[str, float]],
    discount_rate: float,
) -> None:
    """Add every (region, vintage) generator for one technology in a single call."""
    tech_id = technology["id"]

    for unsupported in _UNTRANSLATED_TECHNOLOGY_FIELDS:
        value = unwrap(technology.get(unsupported))
        if value is not None and not _is_neutral(unsupported, value):
            logger.warning("%s: %s is set but not translated", tech_id, unsupported)

    capex_all = unwrap(technology.get("capex")) or {}
    opex_fixed_all = unwrap(technology.get("opex_fixed")) or {}
    operating_life_all = unwrap(technology.get("operating_life")) or {}
    residual_all = unwrap(technology.get("residual_capacity")) or {}
    capacity_factor_all = unwrap(technology.get("capacity_factor")) or {}
    ctau_all = unwrap(technology.get("capacity_activity_unit_ratio")) or {}

    names: list[str] = []
    static: dict[str, list[Any]] = {
        "bus": [],
        "p_nom": [],
        "p_nom_extendable": [],
        "p_nom_min": [],
        "p_nom_max": [],
        "build_year": [],
        "lifetime": [],
        "overnight_cost": [],
        "discount_rate": [],
        "fom_cost": [],
        "efficiency": [],
    }
    p_max_pu: dict[str, pd.Series] = {}
    marginal_cost: dict[str, pd.Series] = {}

    def record(name: str, bus: str, availability: pd.Series, cost: pd.Series, **attrs: Any) -> None:
        names.append(name)
        static["bus"].append(bus)
        for key, values in static.items():
            if key != "bus":
                values.append(attrs[key])
        p_max_pu[name] = availability
        marginal_cost[name] = cost

    for region in regions:
        if region not in capex_all or region not in operating_life_all:
            continue

        ctau = float(ctau_all.get(region, 8.76))
        if not np.isclose(ctau, 8.76):
            logger.warning(
                "%s/%s has CapacityToActivityUnit=%s; the GW<->TWh mapping assumes 8.76",
                tech_id,
                region,
                ctau,
            )

        life = float(operating_life_all[region])
        rate = float(cost_of_capital_all.get(region, {}).get(tech_id, discount_rate))
        capex = _by_year(capex_all[region], years, default=0.0)
        opex_fixed = _by_year(opex_fixed_all.get(region), years, default=0.0).fillna(0.0)
        residual = _by_year(residual_all.get(region), years, default=0.0).fillna(0.0)

        cf_region = capacity_factor_all.get(region)
        capacity_factor = (
            pd.Series(1.0, index=snapshots)
            if cf_region is None
            else pd.Series([float(cf_region[str(y)][ts]) for y, ts in snapshots], index=snapshots)
        )

        cost, efficiency = _mode_economics(technology, region, years, snapshots)

        # ResidualCapacity is exogenous: the retirement schedule is already
        # baked into its year-by-year values upstream, so it is not something
        # the LP should reason about. One non-extendable generator per year,
        # with lifetime 1 so it is active in that year alone, makes p_nom in
        # year y exactly residual[y] without the optimiser seeing a
        # retirement decision.
        for year, level in _annual_capacity(residual, years):
            record(
                f"{tech_id}:{region}:residual:{year}",
                region,
                capacity_factor,
                cost,
                p_nom=level * MW_PER_GW,
                p_nom_extendable=False,
                p_nom_min=0.0,
                p_nom_max=np.inf,
                build_year=year,
                lifetime=1,
                overnight_cost=0.0,
                discount_rate=rate,
                fom_cost=0.0,
                efficiency=efficiency,
            )

        for build_year in build_years:
            if not _is_active(build_year, life, years):
                continue
            record(
                f"{tech_id}:{region}:{build_year}",
                region,
                capacity_factor,
                cost,
                p_nom=0.0,
                p_nom_extendable=True,
                p_nom_min=0.0,
                p_nom_max=np.inf,
                build_year=build_year,
                lifetime=life,
                overnight_cost=capex[build_year] * MW_PER_GW,
                discount_rate=rate,
                fom_cost=opex_fixed[build_year] * MW_PER_GW,
                efficiency=efficiency,
            )

    if not names:
        logger.warning("%s: no region has capex and operating_life -- skipped", tech_id)
        return

    n.add(
        "Generator",
        names,
        carrier="electricity",
        type=tech_id,
        p_max_pu=pd.DataFrame(p_max_pu),
        marginal_cost=pd.DataFrame(marginal_cost),
        **static,
    )


def _mode_economics(
    technology: dict[str, Any],
    region: str,
    years: list[int],
    snapshots: pd.MultiIndex,
) -> tuple[pd.Series, float]:
    """Per-snapshot marginal cost [$/MWh] and efficiency for one technology/region.

    Multi-mode technologies collapse to the cheapest mode per year: every mode
    outputs 1.0 electricity per unit activity and they share one capacity, so
    the LP optimum is unchanged.
    """
    per_mode_cost: dict[str, pd.Series] = {}
    per_mode_efficiency: dict[str, pd.Series] = {}

    for mode in technology["operating_modes"]:
        mode_id = mode["id"]

        output = (unwrap(mode.get("output_activity_ratio")) or {}).get(region, {})
        electricity_ratio = _by_year(output.get("electricity"), years, default=0.0)
        if not np.allclose(electricity_ratio.fillna(0.0), 1.0):
            raise NotImplementedError(
                f"{technology['id']}/{region}/{mode_id}: OutputActivityRatio[electricity] is not "
                "1.0 -- the GW<->MW mapping assumes activity == electricity output"
            )
        for commodity in output:
            if commodity != "electricity":
                raise NotImplementedError(
                    f"{technology['id']}/{region}/{mode_id} co-produces {commodity}; "
                    "this comparison expects an electricity-only model"
                )

        per_mode_cost[mode_id] = _by_year(
            (unwrap(mode.get("opex_variable")) or {}).get(region), years, default=0.0
        ).fillna(0.0)

        inputs = (unwrap(mode.get("input_activity_ratio")) or {}).get(region, {})
        if len(inputs) > 1:
            raise NotImplementedError(
                f"{technology['id']}/{region}/{mode_id} consumes {sorted(inputs)}; "
                "PyPSA's Generator.efficiency takes a single input carrier"
            )
        efficiency = pd.Series(1.0, index=years)
        for by_year in inputs.values():
            efficiency = 1.0 / _by_year(by_year, years, default=1.0).replace(0.0, np.nan).fillna(
                1.0
            )
        per_mode_efficiency[mode_id] = efficiency

    cost_frame = pd.DataFrame(per_mode_cost)
    cheapest = cost_frame.idxmin(axis=1)
    efficiency_frame = pd.DataFrame(per_mode_efficiency)
    annual_efficiency = pd.Series(
        [efficiency_frame.loc[y, cheapest[y]] for y in years], index=years, dtype=float
    )
    if not annual_efficiency.eq(annual_efficiency.iloc[0]).all():
        logger.warning(
            "%s/%s: efficiency varies by year (%.3f-%.3f); using the final-year value",
            technology["id"],
            region,
            annual_efficiency.min(),
            annual_efficiency.max(),
        )

    return _over_snapshots(cost_frame.min(axis=1), snapshots), float(annual_efficiency.iloc[-1])


def _add_trade_links(
    n: pypsa.Network,
    trade: dict[str, Any],
    years: list[int],
    snapshots: pd.MultiIndex,
    build_years: list[int],
    discount_rate: float,
) -> None:
    """OSeMOSYS trade routes -> one PyPSA Link per directed pair per vintage.

    OSeMOSYS bounds ``Export[r,rr,l] / (CTAU * YearSplit[l]) <= Cap * (1-loss)``
    and charges the exporter ``Export/(1-loss)``: sender-side power is bounded
    by the capacity and the loss falls on delivery, which is a PyPSA Link with
    ``p_nom = Cap`` and ``efficiency = 1 - loss``.
    """
    routes = unwrap(trade.get("trade_routes")) or {}
    loss_all = unwrap(trade.get("trade_loss")) or {}
    residual_all = unwrap(trade.get("residual_capacity")) or {}
    capex_all = unwrap(trade.get("capex")) or {}
    life_all = unwrap(trade.get("operating_life")) or {}
    coc_all = unwrap(trade.get("cost_of_capital")) or {}

    for unsupported in _UNTRANSLATED_TRADE_FIELDS:
        if unwrap(trade.get(unsupported)) is not None:
            logger.warning("trade %s: %s is set but not translated", trade["id"], unsupported)

    names: list[str] = []
    static: dict[str, list[Any]] = {
        "bus0": [],
        "bus1": [],
        "p_nom": [],
        "p_nom_extendable": [],
        "p_nom_min": [],
        "p_nom_max": [],
        "build_year": [],
        "lifetime": [],
        "overnight_cost": [],
        "discount_rate": [],
    }
    p_max_pu: dict[str, pd.Series] = {}
    efficiency: dict[str, pd.Series] = {}

    def record(
        name: str, source: str, target: str, availability: pd.Series, eta: pd.Series, **attrs: Any
    ) -> None:
        names.append(name)
        static["bus0"].append(source)
        static["bus1"].append(target)
        for key, values in static.items():
            if key not in ("bus0", "bus1"):
                values.append(attrs[key])
        p_max_pu[name] = availability
        efficiency[name] = eta

    ones = pd.Series(1.0, index=snapshots)

    for source, targets in routes.items():
        for target, active_by_year in targets.items():
            if not any(bool(v) for v in active_by_year.values()):
                continue

            loss = _by_year(loss_all.get(source, {}).get(target), years, default=0.0).fillna(0.0)
            eta = _over_snapshots(1.0 - loss, snapshots)
            residual = _by_year(
                residual_all.get(source, {}).get(target), years, default=0.0
            ).fillna(0.0)

            rate = float(coc_all.get(source, {}).get(target, discount_rate))

            # Existing interconnection: exogenous per year, same as generators.
            for year, level in _annual_capacity(residual, years):
                record(
                    f"transmission:{source}->{target}:residual:{year}",
                    source,
                    target,
                    ones,
                    eta,
                    p_nom=level * MW_PER_GW,
                    p_nom_extendable=False,
                    p_nom_min=0.0,
                    p_nom_max=np.inf,
                    build_year=year,
                    lifetime=1,
                    overnight_cost=0.0,
                    discount_rate=rate,
                )

            capex = _by_year(capex_all.get(source, {}).get(target), years)
            life_series = _by_year(life_all.get(source, {}).get(target), years)
            if capex is None or life_series is None:
                continue
            life = float(life_series.iloc[0])

            for build_year in build_years:
                if not _is_active(build_year, life, years):
                    continue
                record(
                    f"transmission:{source}->{target}:{build_year}",
                    source,
                    target,
                    ones,
                    eta,
                    p_nom=0.0,
                    p_nom_extendable=True,
                    p_nom_min=0.0,
                    p_nom_max=np.inf,
                    build_year=build_year,
                    lifetime=life,
                    overnight_cost=capex[build_year] * MW_PER_GW,
                    discount_rate=rate,
                )

    if names:
        n.add(
            "Link",
            names,
            carrier="electricity",
            p_max_pu=pd.DataFrame(p_max_pu),
            efficiency=pd.DataFrame(efficiency),
            **static,
        )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m osemosys_vs_pypsa.converter",
        description="Build a PyPSA network from a simplified tz-osemosys model.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "model",
        type=Path,
        help="serialised tz-osemosys model.json, already run through simplify_model.py",
    )
    parser.add_argument(
        "output",
        type=Path,
        help="destination for the PyPSA network (netCDF, .nc)",
    )
    parser.add_argument(
        "--build-year-step",
        type=int,
        default=1,
        metavar="N",
        help="offer investment decisions every N years; 1 matches NewCapacity[r,t,y]",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="network name (default: the model id)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="log build progress to stderr",
    )
    args = parser.parse_args(argv)
    if args.build_year_step < 1:
        parser.error("--build-year-step must be >= 1")
    return args


def main(argv: list[str] | None = None) -> int:
    """Read a model.json, build the network, write it to netCDF."""
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    model = json.loads(args.model.read_text())
    n = build_network(model, build_year_step=args.build_year_step, name=args.name)
    n.export_to_netcdf(args.output)

    print(f"read  {args.model}  ({args.model.stat().st_size / 1e6:.1f} MB)")
    print(f"  network:    {n.name}")
    print(f"  snapshots:  {len(n.snapshots)}  over {len(n.investment_periods)} periods")
    for component, label in (
        ("Bus", "buses"),
        ("Load", "loads"),
        ("Generator", "generators"),
        ("Link", "links"),
    ):
        print(f"  {label + ':':11} {len(n.components[component].static)}")
    print(f"wrote {args.output}  ({args.output.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
