# OSeMOSYS vs PyPSA — HiGHS solve-time gap analysis

Same `model.json` (16 technologies, 9 regions, 96 timeslices × 28 years, electricity-only),
same solver (HiGHS 1.15.1, HiPO interior point, 8 threads). Artefacts: `osemosys_ce.lp` and
`pypsa_ce.lp` in this folder, exported with `explicit_coordinate_names=True` so every variable
and constraint is human-readable.

## Bottom line

| | OSeMOSYS | PyPSA |
|---|---|---|
| status | **optimal in 964.6 s** | log truncated at **23,720 s, still not optimal** |
| IPM iterations | 90 @ 10.2 s/iter | 151 @ 57.2 s/iter |
| crossover | clean (185 k pushes) | **failed** (6.06 M pushes, "imprecise") |
| simplex cleanup | not needed | ≥ 14,772 s and unfinished |
| objective | 597,457 $m | 607,473 $m (+1.68 %, documented conventions¹) |

Gap ≥ 24.6× and unbounded (the PyPSA run never finished in the log). Both frameworks find the
same optimum to within known convention differences — the gap is purely formulation, not answer.

¹ annuity vs annuity-due (+r·capex), mid-year vs start-of-year opex discounting, fixed O&M on
residual capacity charged only by OSeMOSYS.

## The two LPs

| | OSeMOSYS | PyPSA | ratio |
|---|---|---|---|
| rows | 576,828 | 13,264,304 | **23×** |
| columns | 499,184 | 6,622,352 | **13×** |
| nonzeros | 8,909,136 | 26,842,400 | 3× |
| nnz / row | 15.4 | 2.0 | — |
| after presolve (rows) | 450,062 (−22 %) | 6,175,440 (−53 %) | 14× |

Block breakdown (counted from the built models; sums match the logs exactly):

| OSeMOSYS | count | | PyPSA | count |
|---|---|---|---|---|
| `NewCapacity` | 4,032 | | `Generator-p_nom` | 4,032 |
| `NewTradeCapacity` | 560 | | `Link-p_nom` | 560 |
| `RateOfActivity` | 387,072 | | `Generator-p` | **5,784,480** |
| `Export` + `Import` | 107,520 | | `Link-p` | **833,280** |
| — | | | | |
| `CAa4_Constraint_Capacity` | 387,072 | | `Generator-ext-p-upper/lower` | **11,183,616** |
| `TC1a/b_TradeConstraint` | 107,520 | | `Link-ext-p-upper/lower` | 1,559,040 |
| `EBa10/11` energy balance | 77,952 | | `Generator/Link-fix-p-*` (residual) | 492,864 |
| `ACF1` + `EBb4` (annual) | 4,284 | | `Bus-…-nodal_balance` | 24,192 |
| `RM3` (vacuous, dropped at write) | 0 | | `p_nom` bound rows | 4,592 |

**The investment problem is identical** — 4,592 capacity variables on each side. The entire gap
is dispatch: PyPSA carries 6.6 M dispatch variables against OSeMOSYS's 0.49 M, and 96 % of
PyPSA's rows are per-component dispatch bounds.

## Why: the vintage axis lands on dispatch

Both models offer investment in every year, and both time grids are identical (the OSeMOSYS
timeslices are the PyPSA snapshots verbatim). The difference is where the build-year axis goes:

- **OSeMOSYS** sums vintages *before* bounding dispatch. `GrossCapacity = Σ_buildyear
  NewCapacity + ResidualCapacity` is a linear expression, so `CAa4` is **one row** per
  (region, tech, timeslice, year) with ~15 capacity terms in it, and `RateOfActivity` has no
  build-year index.
- **PyPSA** bounds dispatch per component, and a component *is* a vintage. Each (tech, region)
  has ~14.5 live vintages on average (28 build years, lives 25–40 yr), each with its own `p`
  variable and a two-sided `p ≤ p_max_pu·p_nom` constraint pair per snapshot.

Same feasible set — summing PyPSA's 28 rows per timeslice reproduces the OSeMOSYS row exactly,
because vintages of a technology here have **identical marginal cost and p_max_pu** (verified:
144/144 groups). PyPSA pays 14.5 (vintages) × 2 (upper+lower) rows for expressiveness this
dataset cannot use. See `pypsa_ce.lp` vs `osemosys_ce.lp`: e.g. `CAa4_Constraint_Capacity(
GRIDREGION-JPN-SH,wind-offshore-unspecified,2023,winter_typical:00h00-02h00…)` is a single row
whose PyPSA counterpart is 2 rows × each of the live vintage generators
`wind-offshore-unspecified:GRIDREGION-JPN-SH:2023…2050`.

## Where the wall-clock goes

**1. Per-iteration cost, 5.6× (10.2 → 57.2 s/iter).** Counter-intuitively, OSeMOSYS does *more*
arithmetic per iteration (1.6e11 vs 6.7e10 flops — its aggregated rows are denser, fill-in 10.7
vs 2.5). PyPSA loses on memory, not flops: Newton system 1.87e7 vs 1.33e6 (14×), factor
1.11e8 nnz, working set **12 GB vs 1.4 GB**. Every iteration streams that 12 GB; the solve is
bandwidth-bound. Symbolic analyse alone: 181 s vs 26 s.

**2. Iteration count, 1.7× (151 vs 90).** Conditioning. PyPSA emits costs in $/MW ($2e7
overnight): cost range [3e1, 2e7], and HiGHS warns *"excessively large costs … consider
user_objective_scale −5"*. OSeMOSYS's $m/GW keeps costs in [3e-3, 2e4].

**3. Crossover failure — the biggest single item.** With ~14.5 identical vintages per
technology, any split of dispatch across them is equally optimal: the LP optimum is a huge
degenerate face. The IPM converges to the *centre* of that face, so crossover must push
millions of tied variables to a vertex: **6,024,255 dual pushes vs 180,374** (33×), ends
"imprecise" (dual infeasibility 4.9e-3), and HiGHS falls back to dual simplex — 811 k+
iterations, ≥ 14,772 s, still ~18.5 k primal infeasibilities when the log ends. OSeMOSYS never
creates the tie, so its crossover is trivial.

Multiplying the measured factors: 5.6 (per-iter) × 1.7 (iters) ≈ 9.4× to reach IPM-optimal
(8,874 s vs 962 s — checks out), and the crossover/cleanup failure takes it from 9.4× to ≥ 25×
and rising.

## Levers, in order of impact

1. **Coarsen the vintage axis** — `build_year_step 5` cuts dispatch variables ~4× and, more
   importantly, shrinks the degenerate face that kills crossover. (OSeMOSYS build years must be
   restricted to match, or the comparison is unfair.)
2. **Skip crossover** (`run_crossover=off`) if a basic solution isn't needed — the IPM point at
   8,874 s was already primal-dual feasible to 1e-9.
3. **Scale the objective** — emit $m/GW (or set `user_objective_scale=-5`) to fix the
   conditioning warning and claw back some of the 151-vs-90 iterations.
4. Not a lever: PyPSA cannot express OSeMOSYS's vintage-aggregated dispatch (`GrossCapacity`
   has no PyPSA analogue — capacity exists only as an attribute of a dispatching component),
   so the residual ~10× formulation gap is structural.

## Files

- `osemosys_ce.lp` — 0.9 GB, labeled: `CAa4_Constraint_Capacity(GRIDREGION-JPN-SH,wind-offshore-unspecified,2023,winter_typical:00h00-02h00…)`
- `pypsa_ce.lp` — 5.8 GB, labeled: `Generator_ext_p_upper((2023,winter_typical:00h00-02h00…),wind-offshore-unspecified:GRIDREGION-JPN-SH:2023)`
- The 6.3× LP-file size ratio is itself the row-count story in ASCII form.
- Stats used here: block counts from the in-memory linopy models; totals reconcile with both
  HiGHS logs exactly (OSeMOSYS 499,184/576,828 after dropping the vacuous RM3 block; PyPSA
  6,622,352/13,264,304).
