"""Tables and charts that compare two solved runs.

The notebook is deliberately thin: every number and every chart it shows comes
from a function here, so the analysis can be tested without executing a notebook.

Unit conventions throughout: OSeMOSYS is GW / TWh / $m, PyPSA is MW / MWh / $.
Everything returned by this module is already in the OSeMOSYS units (GW, TWh, $m).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from osemosys_vs_pypsa.reconcile import compare

if TYPE_CHECKING:
    import pypsa
    import xarray as xr
    from matplotlib.axes import Axes

    from osemosys_vs_pypsa.solverlog import SolveLog

MW_PER_GW = 1e3
MWH_PER_TWH = 1e6

OSEMOSYS = "OSeMOSYS"
PYPSA = "PyPSA"

# Categorical slots 1 and 2 of the reference palette. Validated as an adjacent
# pair in both modes; identity is also carried by the legend and axis labels, so
# colour is never the only channel.
COLORS = {OSEMOSYS: "#2a78d6", PYPSA: "#eb6834"}
DIVERGING = {"positive": "#2a78d6", "negative": "#d03b3b", "midpoint": "#f0efec"}

SOLVER_NAMES = {"highs": "HiGHS", "gurobi": "Gurobi"}

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"


def apply_theme() -> None:
    """Recessive grid, hairline axes, muted tick labels. Call once per notebook."""
    import matplotlib as mpl

    mpl.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.edgecolor": BASELINE,
            "axes.labelcolor": INK_SECONDARY,
            "axes.titlecolor": INK,
            "axes.titlesize": 12,
            "axes.titlelocation": "left",
            "axes.titlepad": 12,
            "axes.labelsize": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": GRIDLINE,
            "grid.linewidth": 0.8,
            "text.color": INK,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "legend.frameon": False,
            "legend.fontsize": 9,
            "lines.linewidth": 2.0,
            "lines.markersize": 5,
            "figure.dpi": 110,
        }
    )


# --------------------------------------------------------------------------- #
# tables
# --------------------------------------------------------------------------- #


def objective_table(solution: xr.Dataset, network: pypsa.Network) -> pd.DataFrame:
    """The objective reconciliation of ``reconcile.compare`` as a labelled table."""
    result = compare(solution, network)
    osemosys, pypsa_side = result["osemosys"], result["pypsa"]
    rows = [
        ("TotalDiscountedCost, as saved in solution.nc", osemosys["total_discounted_cost"], np.nan),
        ("less fixed O&M on ResidualCapacity", -osemosys["residual_fixed_om"], np.nan),
        ("LP objective (what the solver minimised)", osemosys["lp_objective"], pypsa_side["lp_objective"]),
        ("objective constant PyPSA adds", np.nan, pypsa_side["objective_constant"]),
    ]
    table = pd.DataFrame(rows, columns=["term", f"{OSEMOSYS} ($m)", f"{PYPSA} ($m)"]).set_index("term")
    table.attrs["difference_millions"] = result["difference"]
    table.attrs["difference_pct"] = result["difference_pct"]
    return table


def _osemosys_new_capacity(solution: xr.Dataset) -> pd.DataFrame:
    """NewCapacity in GW, indexed (technology, year)."""
    series = solution.NewCapacity.sum("REGION").to_series()
    return series.rename(OSEMOSYS).reset_index().rename(
        columns={"TECHNOLOGY": "technology", "YEAR": "year"}
    )


def _pypsa_new_capacity(network: pypsa.Network) -> pd.DataFrame:
    """Optimal extendable p_nom in GW, indexed (technology, build year)."""
    extendable = network.generators[network.generators.p_nom_extendable]
    grouped = extendable.groupby(["type", "build_year"]).p_nom_opt.sum() / MW_PER_GW
    return grouped.rename(PYPSA).reset_index().rename(
        columns={"type": "technology", "build_year": "year"}
    )


def new_capacity_by_technology(solution: xr.Dataset, network: pypsa.Network) -> pd.DataFrame:
    """New capacity built over the horizon, per technology, in GW."""
    left = _osemosys_new_capacity(solution).groupby("technology")[OSEMOSYS].sum()
    right = _pypsa_new_capacity(network).groupby("technology")[PYPSA].sum()
    table = pd.concat([left, right], axis=1).fillna(0.0)
    table["difference"] = table[PYPSA] - table[OSEMOSYS]
    return table.sort_values(OSEMOSYS, ascending=False)


def new_capacity_by_year(solution: xr.Dataset, network: pypsa.Network) -> pd.DataFrame:
    """New capacity per build year, summed over technologies, in GW."""
    left = _osemosys_new_capacity(solution).groupby("year")[OSEMOSYS].sum()
    right = _pypsa_new_capacity(network).groupby("year")[PYPSA].sum()
    return pd.concat([left, right], axis=1).fillna(0.0).sort_index()


def generation_by_technology(solution: xr.Dataset, network: pypsa.Network) -> pd.DataFrame:
    """Generation over the horizon, per technology, in TWh.

    PyPSA dispatch is weighted by ``snapshot_weightings.objective`` (the hours
    each timeslice represents), which is how the OSeMOSYS year split enters the
    PyPSA side in the first place.
    """
    left = solution.ProductionByTechnology.sum(("REGION", "TIMESLICE", "FUEL", "YEAR")).to_series()
    left.index.name = "technology"
    left = left.rename(OSEMOSYS)

    hours = network.snapshot_weightings.objective
    energy = network.generators_t.p.mul(hours, axis=0).sum()
    right = (energy.groupby(network.generators["type"]).sum() / MWH_PER_TWH).rename(PYPSA)
    right.index.name = "technology"

    table = pd.concat([left, right], axis=1).fillna(0.0)
    table["difference"] = table[PYPSA] - table[OSEMOSYS]
    return table.sort_values(OSEMOSYS, ascending=False)


def agreement_summary(
    capacity: pd.DataFrame, generation: pd.DataFrame
) -> dict[str, float]:
    """Headline agreement metrics between the two solved runs."""
    return {
        "capacity_total_osemosys_GW": capacity[OSEMOSYS].sum(),
        "capacity_total_pypsa_GW": capacity[PYPSA].sum(),
        "capacity_max_abs_diff_GW": capacity["difference"].abs().max(),
        "generation_total_osemosys_TWh": generation[OSEMOSYS].sum(),
        "generation_total_pypsa_TWh": generation[PYPSA].sum(),
        "generation_max_abs_diff_TWh": generation["difference"].abs().max(),
        "generation_max_abs_diff_pct": 100.0
        * generation["difference"].abs().max()
        / generation[OSEMOSYS].sum(),
    }


# Ratios of these rows would be nonsense: the objectives are in different units
# ($m against $), and a cost range is a tuple.
_NOT_RATIOABLE = frozenset({"objective", "cost_range", "solver", "status", "crossover_status"})


def lp_comparison(osemosys_log: SolveLog, pypsa_log: SolveLog) -> pd.DataFrame:
    """Side-by-side solver-log summary, with the PyPSA/OSeMOSYS ratio."""
    table = pd.DataFrame({OSEMOSYS: osemosys_log.summary(), PYPSA: pypsa_log.summary()})
    ratio = []
    for name in table.index:
        left, right = table.loc[name, OSEMOSYS], table.loc[name, PYPSA]
        comparable = (
            name not in _NOT_RATIOABLE
            and isinstance(left, (int, float))
            and isinstance(right, (int, float))
            and bool(left)
        )
        ratio.append(right / left if comparable else np.nan)
    table["PyPSA / OSeMOSYS"] = ratio
    return table


# --------------------------------------------------------------------------- #
# charts
# --------------------------------------------------------------------------- #


def _hide_value_spine(ax: Axes, *, horizontal: bool) -> None:
    ax.grid(axis="x" if horizontal else "y", visible=horizontal)
    ax.grid(axis="y" if horizontal else "x", visible=not horizontal)
    ax.set_axisbelow(True)


def plot_generation_by_technology(
    generation: pd.DataFrame, ax: Axes, *, min_twh: float = 1.0
) -> Axes:
    """Grouped horizontal bars, one pair per technology.

    The two bars sitting on top of each other is the finding: at this resolution
    the frameworks pick the same dispatch.
    """
    data = generation[generation[OSEMOSYS] >= min_twh].iloc[::-1]
    positions = np.arange(len(data))
    height = 0.38

    ax.barh(positions + height / 2 + 0.02, data[OSEMOSYS], height, label=OSEMOSYS,
            color=COLORS[OSEMOSYS])
    ax.barh(positions - height / 2 - 0.02, data[PYPSA], height, label=PYPSA,
            color=COLORS[PYPSA])

    ax.set_yticks(positions, data.index)
    ax.set_xlabel("generation over 2023-2050 (TWh)")
    ax.set_title("Both frameworks pick the same dispatch")
    _hide_value_spine(ax, horizontal=True)
    ax.legend(loc="lower right")

    largest = data[OSEMOSYS].idxmax()
    row = data.loc[largest]
    ax.annotate(
        f"{row[OSEMOSYS]:,.0f} TWh",
        xy=(row[OSEMOSYS], list(data.index).index(largest)),
        xytext=(6, 0),
        textcoords="offset points",
        va="center",
        fontsize=9,
        color=INK_SECONDARY,
    )
    return ax


def plot_generation_difference(
    generation: pd.DataFrame, ax: Axes, *, min_twh: float = 1.0
) -> Axes:
    """Diverging bars: PyPSA minus OSeMOSYS, per technology."""
    data = generation[generation[OSEMOSYS] >= min_twh].iloc[::-1]
    colors = [
        DIVERGING["positive"] if value >= 0 else DIVERGING["negative"]
        for value in data["difference"]
    ]

    ax.barh(np.arange(len(data)), data["difference"], 0.62, color=colors)
    ax.axvline(0.0, color=BASELINE, linewidth=1.0)
    ax.set_yticks(np.arange(len(data)), data.index)
    ax.set_xlabel("PyPSA minus OSeMOSYS (TWh)")
    total = generation[OSEMOSYS].sum()
    worst = data["difference"].abs().max()
    ax.set_title(
        f"Disagreement is {worst:.1f} TWh at worst, on a {total:,.0f} TWh system"
    )
    _hide_value_spine(ax, horizontal=True)
    return ax


def plot_new_capacity_by_year(capacity_by_year: pd.DataFrame, ax: Axes) -> Axes:
    """New capacity per build year, one line per framework.

    The lines land on top of each other, so PyPSA is drawn dashed and thinner
    over a solid OSeMOSYS -- the dash pattern keeps the two distinguishable where
    colour alone would be hidden by overplotting.
    """
    data = capacity_by_year
    marker = "o" if len(data) <= 12 else None
    ax.plot(data.index, data[OSEMOSYS], label=OSEMOSYS, color=COLORS[OSEMOSYS],
            linewidth=2.6, marker=marker)
    ax.plot(data.index, data[PYPSA], label=PYPSA, color=COLORS[PYPSA],
            linewidth=1.6, linestyle=(0, (4, 2)), marker=marker)

    ax.set_xlabel("build year")
    ax.set_ylabel("new capacity (GW)")
    ax.set_title("Investment lands in the same years")
    _hide_value_spine(ax, horizontal=False)
    ax.margins(y=0.12)
    ax.legend(loc="best")
    return ax


def plot_wall_clock(osemosys_log: SolveLog, pypsa_log: SolveLog, ax: Axes) -> Axes:
    """Two bars: wall clock per framework, on the same solver."""
    labels, values, colors = [], [], []
    for name, log in ((OSEMOSYS, osemosys_log), (PYPSA, pypsa_log)):
        labels.append(name)
        values.append(log.wall_clock_seconds or np.nan)
        colors.append(COLORS[name])

    positions = np.arange(len(labels))
    ax.barh(positions, values, 0.5, color=colors)
    ax.set_yticks(positions, labels)
    ax.set_xlabel("wall clock (s)")
    solver = SOLVER_NAMES.get(osemosys_log.solver, osemosys_log.solver)
    ax.set_title(f"Time to solve the same system on {solver}")
    _hide_value_spine(ax, horizontal=True)

    for position, value, log in zip(positions, values, (osemosys_log, pypsa_log), strict=True):
        suffix = "" if log.completed else "  (did not finish)"
        ax.annotate(
            f"{value:,.0f} s{suffix}",
            xy=(value, position),
            xytext=(6, 0),
            textcoords="offset points",
            va="center",
            fontsize=9,
            color=INK_SECONDARY,
        )
    ax.margins(x=0.45)
    return ax


def as_table(frame: pd.DataFrame, decimals: int = 2) -> Any:
    """Round for display without mutating the caller's frame."""
    return frame.round(decimals)
