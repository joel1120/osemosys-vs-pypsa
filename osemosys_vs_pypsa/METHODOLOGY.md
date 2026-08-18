# OSeMOSYS vs PyPSA — capacity expansion on identical inputs

Builds a PyPSA network directly from a serialised `tz-osemosys` `model.json`, so both
frameworks solve the same system at the same temporal resolution. No TSAM, no re-clustering:
the OSeMOSYS timeslices *are* the PyPSA snapshots.

## Why no TSAM

The `model.json` time definition is already a typical-period aggregation produced upstream by
the platform: **24 seasons (typical days) × 4 variable-length daily time brackets = 96
timeslices**, over 28 years (2023–2050). Verified consistent — every season's four brackets sum
to 24 h, `year_split × 8760 / bracket_hours` gives the same integer day count for all four
brackets of a season, and the day counts total 365.

Re-clustering with TSAM would make the comparison a comparison of clusterings.

## Usage

Normally you want `run_benchmark.py` in the repo root, which chains all of this and
solves both sides — see the top-level README. The two modules below are the pieces it
calls, and are usable on their own:

```bash
python -m osemosys_vs_pypsa.simplify_model \
    artifacts/model.json  model_basic.json

python -m osemosys_vs_pypsa.converter \
    model_basic.json  network_basic.nc
```

`--build-year-step N` offers investment decisions every `N` years instead of every year
(fewer vintage components, lower fidelity), `--name` overrides the network name, and `-v`
logs the build. Or call `build_network(model_dict)` directly for the same thing in-process.

Solve the same `model_basic.json` with `tz.osemosys.Model` for the other side. If you
used `--build-year-step N` with `N > 1`, pass the model through
`build_years.restrict_build_years(model, N)` first, or OSeMOSYS gets investment
opportunities PyPSA was never offered and the comparison is unfair.

## What `simplify_model.py` removes

Everything PyPSA has no native equivalent for is removed from the OSeMOSYS model rather than
approximated on the PyPSA side, so both sides solve the identical reduced system.

| Removed | Why |
| --- | --- |
| `storage` + the two storage-coupled technologies | OSeMOSYS's storage recursion has no PyPSA analogue: `NetCharge` uses `YearSplit`, so the level swings by the whole typical-day *block*, not one day |
| `include_in_joint_reserve_margin` | spans all generators in a region |
| `capacity_additional_max_growth_rate` / `_floor` | `NewCapacity[y] ≤ rate·GrossCapacity[y−1] + floor` spans vintages |
| `capacity_factor_annual_min` | CapacityAdequacyB is an annual energy cap spanning vintages. Per-timeslice CapacityAdequacyA survives as `p_max_pu` |
| all impacts, penalties and `emission_activity_ratio` | no carbon pricing, no emission accounting |

Two of these cannot be dropped, because tz-osemosys declares them non-Optional
(`OSeMOSYSData.RY`, not `| None`) and the OSeMOSYS side would fail to validate. They are
neutralised to a no-op value instead:

| Neutralised | To | Why that is a no-op |
| --- | --- | --- |
| `Technology.availability_factor` | `1.0` | CapacityAdequacyB caps annual activity at `AvailabilityFactor × capacity × CapacityToActivityUnit`; CapacityAdequacyA already caps each timeslice at `CapacityFactor[l] ≤ 1`, whose year-split-weighted sum cannot exceed that bound |
| `Model.reserve_margin` | `1.0` | `reserve_margin_fully_defined()` only objects to values `!= 1` |

**`reserve_margin = 1.0` alone is not neutral** — it makes the model infeasible. RM3 is
`ReserveMargin × DemandNeedingReserveMargin ≤ TotalCapacityInReserveMargin`, where the capacity
side is masked by `ReserveMarginTagTechnology` and the demand side by `ReserveMarginTagFuel`.
Dropping `include_in_joint_reserve_margin` leaves the tech tag all-NaN, so the capacity side
contributes **0 terms** — while `electricity` carries an explicit fuel tag, so the demand side
contributes 532 224. RM3 then reads `Σ production ≤ 0`. `simplify_model.py` therefore also sets
every commodity's `include_in_joint_reserve_margin` to `false`, which empties the demand side and
leaves RM3 with no terms at all. `verify()` asserts this.

The input `model.json` is expected to be electricity-only — no `dmyco2e_*` commodities and no
CO2 transport chain — so nothing is pruned on the commodity side. Result: one commodity
(`electricity`), no storage, no impacts.

The constraint records in the run's `parameters.json` are ignored entirely.

## Mapping

| OSeMOSYS | PyPSA |
| --- | --- |
| `timeslices` (96) × `years` (28) | `snapshots`, MultiIndex `(period, timestep)` |
| `year_split[l] × 8760` | `snapshot_weightings.objective` (hours) |
| `years` | `investment_periods` |
| `(1 + DiscountRate)^-(y-y0)` | `investment_period_weightings.objective` |
| region | Bus |
| `demand_annual × demand_profile[l]` | `Load.p_set` (energy ÷ timeslice hours → MW) |
| technology × region × build year | Generator (`build_year` + `lifetime` reproduce `AccumulatedNewCapacity`) |
| `residual_capacity[y]` | one non-extendable Generator per year, `p_nom = ResidualCapacity[y]`, `build_year = y`, `lifetime = 1` |
| `capacity_factor[r,t,l,y]` | `p_max_pu` |
| trade route (directed) × build year | Link, `efficiency = 1 − trade_loss` |
| `capex` | `overnight_cost` |
| `cost_of_capital` (`DiscountRateIdv`) | `discount_rate` |
| `operating_life` | `lifetime` |
| `opex_fixed` | `fom_cost` |
| `opex_variable` | `marginal_cost` |
| `1 / input_activity_ratio` | `efficiency` |

Costs go into PyPSA's native attributes, so PyPSA does its own annuitisation —
`annuity(discount_rate, lifetime) × overnight_cost + fom_cost` charged in every active period.

Units: OSeMOSYS is GW / TWh / $m (`CapacityToActivityUnit = 8.76`), PyPSA is MW / MWh / $ —
capex and opex_fixed ×1e3, opex_variable ×1, energy ×1e6.

## Convention differences to expect in the results

Both are genuine framework differences, not translation errors:

1. **Annuity vs annuity-due.** PyPSA's `annuity(r, N) = r / (1−(1+r)^-N)`; OSeMOSYS's
   `CapitalRecoveryFactor = (1−(1+r)^-1) / (1−(1+r)^-N)`, i.e. the same divided by `(1+r)`.
   PyPSA charges `(1+r_idv)×` OSeMOSYS's capital cost: +3.1% at `r_idv = 0.031`, +7.0% at 0.07.
   End-of-horizon treatment does match — PyPSA truncating the annuity at the last modelled
   period is equivalent to OSeMOSYS's sinking-fund salvage credit.
2. **Mid-year vs start-of-year operating costs.** OSeMOSYS discounts opex by
   `(1+r)^-(y-y0+0.5)`, PyPSA by `(1+r)^-(y-y0)`, so PyPSA values opex `(1+r)^0.5` higher
   (+1.5% at r = 0.03).

3. **Fixed O&M is frozen at the build year.** PyPSA's `fom_cost` is a scalar per component, so a
   vintage carries `FixedCost[build_year]` for its whole life, whereas OSeMOSYS applies
   `FixedCost[y]` year by year. Only `wind-offshore-unspecified` has a materially year-varying
   `FixedCost` in this model (it falls by ~1.4× of its mean across 2023–2050); every other
   technology is flat, so the two agree exactly.

## How residual capacity is represented

`ResidualCapacity[r,t,y]` is exogenous, and its retirement schedule is already baked into the
year-by-year values upstream. So it is not something the LP should reason about: there is one
**non-extendable** Generator per year with `p_nom = ResidualCapacity[y]`, `build_year = y` and
`lifetime = 1`. PyPSA's activity rule (`build_year <= period < build_year + lifetime`) makes each
active in its own year alone, so the technology's active `p_nom` in year `y` is exactly
`ResidualCapacity[y]` — verified against `n.get_active_assets` to 3.6e-15 GW. Years with zero
residual get no component at all.

Retirement therefore never reaches the optimiser: it is arithmetic already done in preprocessing.
`p_max_pu` on these components stays the bare `CapacityFactor[l,y]`, with nothing folded into it.

Two encodings that look plausible but are wrong:

- **One always-active component at peak `p_nom`, decline carried in `p_max_pu`.** The dispatch
  limit comes out right (`p_nom × p_max_pu = ResidualCapacity[y] × CF`), but `p_nom` then reads
  as the peak in every year and no longer matches `TotalCapacityAnnual`.
- **Residual folded into the expansion vintages as `p_nom_min`.** A vintage stays active for its
  whole operating life, so per-vintage floors accumulate: gas-unspecified in `GRIDREGION-JPN-CB`
  would be pinned at 147 GW in 2050 against a true residual of 0.

### Fixed O&M is not charged on residual capacity

PyPSA's objective only ever touches extendable components: `define_objective` builds its
investment term from `c.extendables`, and the constant that would credit already-built `p_nom`
is assembled only in the non-multi-investment branch, so `n.objective_constant` is `0.0` under
`multi_investment_periods=True`. A non-extendable fleet therefore pays no `fom_cost`, while
OSeMOSYS charges `FixedCost` on `TotalCapacityAnnual`, which includes `ResidualCapacity`.

For this model that is **$370.01 bn of discounted fixed O&M** present on the OSeMOSYS side and
absent from PyPSA's objective. It is a pure constant — the capacity is fixed either way, so no
dispatch or investment decision changes — but it must be added to the PyPSA objective before the
two totals are compared.

## Multi-mode technologies

The CCS plants keep `capturing_dom` / `capturing_int` / `venting`. They collapse to a single
Generator whose `marginal_cost` is the per-year minimum over modes — all modes output 1.0
electricity per unit activity and share one capacity, so the LP optimum is unchanged. With the
capture credit gone, `venting` wins in every year.

## Problem size

`build_year_step` controls how often investment is offered. PyPSA ties dispatch to the vintage
component, so a decision every year (matching OSeMOSYS's `NewCapacity[r,t,y]`) is expensive:

| `build_year_step` | components | active dispatch variables |
| --- | --- | --- |
| 1 (matches OSeMOSYS) | 6 039 generators + 1 120 links | 6.62 M |
| 5 | 2 871 generators + 680 links | 1.70 M |

2 007 of those generators and 560 of the links carry residual capacity and are unaffected by
`build_year_step`. Their component count is large but their LP cost is not: each is active in a
single period, and PyPSA masks inactive assets out of the problem, so they contribute 96
dispatch variables each rather than 96 × 28.

The OSeMOSYS LP has ~0.9 M `RateOfActivity` variables, because its dispatch is per
`(region, technology, year)` regardless of vintage. If you use `build_year_step > 1` you must
restrict OSeMOSYS's build years to match, or the comparison is unfair.

## Verified against the reference OSeMOSYS solution

(`artifacts/reference/step1-gurobi/osemosys/solution.nc` in the bundled artefacts.)

| Check | Result |
| --- | --- |
| snapshot weightings sum to 8760 h per year | exact |
| annual demand vs `SpecifiedAnnualDemand` | max diff 5.7e-14 TWh |
| residual available power vs `ResidualCapacity[y] × CapacityFactor[l,y]` | max diff 3.6e-15 GW |
| residual active `p_nom` per year vs `ResidualCapacity[y]` | max diff 3.6e-15 GW |
| extendable `p_max_pu` vs `CapacityFactor[l,y]` | exact, 4 032 generators |
| `overnight_cost` vs `CapitalCost × 1e3` | exact, 4 032 vintages |
| `discount_rate` vs `DiscountRateIdv` | exact, 4 032 vintages |
| `lifetime` vs `OperationalLife` | exact, 4 032 vintages |
| `fom_cost` vs `FixedCost[build_year] × 1e3` | exact, 4 032 vintages |
| `marginal_cost` vs cheapest-mode `VariableCost` | exact |
