# OSeMOSYS vs PyPSA — capacity expansion on identical inputs

Both frameworks solve the *same* system: one serialised `tz-osemosys` `model.json`,
translated directly into a PyPSA network. Same 96 timeslices × 28 years (2023–2050),
same 9 Japan grid regions, same 16 technologies, same solver. **No TSAM, no
re-clustering** — the OSeMOSYS timeslices *are* the PyPSA snapshots, so this is a
comparison of two formulations rather than of two temporal aggregations.

The finding, in one line: **the two frameworks find the same optimum, and PyPSA takes
at least 25× longer to get there** — because a vintage axis that OSeMOSYS puts on
investment, PyPSA is forced to put on dispatch.

## Quickstart

```bash
git clone <this repo> && cd osemosys-vs-pypsa

python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

./unpack_artifacts.sh          # 14 MB -> 105 MB of solved reference runs
jupyter lab comparison.ipynb   # run all cells; solves nothing
```

`comparison.ipynb` reads saved artefacts, so it runs in seconds and needs no solver
licence. Start there. Then read `REPORT.md` for the written findings, and
`osemosys_vs_pypsa/METHODOLOGY.md` for how the translation works and what was removed
from the OSeMOSYS model to keep the comparison fair.

## Doing your own runs

```bash
# recommended default: 5-yearly investment on both sides, HiGHS
python run_benchmark.py artifacts/model.json --outdir runs/step5 --build-year-step 5
```

One command does the whole chain — simplify the model, build the PyPSA network,
restrict OSeMOSYS to the same build years, solve both sides, and write solved
artefacts, solver logs and a `manifest.json` that the notebook reads directly. Point
`SOLUTIONS_RUN` and `TIMING_RUN` at your run directory and re-run the notebook.

```
run_benchmark.py MODEL --outdir DIR
  --build-year-step N   offer investment every N years on BOTH sides (default 5)
  --solver {highs,gurobi}
  --threads N           default 8
  --no-crossover        stop after the interior point; no basic solution
  --mock-solve          build both LPs and skip the solve -- validates the chain in ~70 s
  --write-lp            also write labelled .lp files (see the size warning below)
  --skip-pypsa / --skip-osemosys
```

### Choose `--build-year-step` deliberately

This is the setting that decides whether your run finishes. Measured on 16 cores /
121 GB, HiGHS 1.15.1 with 8 threads:

| step | side | LP variables | HiGHS | Gurobi |
|---|---|---|---|---|
| 1 | OSeMOSYS | 499,184 | **optimal, 965 s** | optimal, 36 s |
| 1 | PyPSA | 6,622,352 | **never finished — still in simplex cleanup at 23,720 s (6.6 h)** | optimal, 908 s |
| 5 | OSeMOSYS | 499,184 | see `runs/` after your own run | — |
| 5 | PyPSA | 1,701,240 | see `runs/` after your own run | — |

**With HiGHS only, use `--build-year-step 5`.** At step 1 the PyPSA side needs roughly
a 12 GB working set and, more importantly, its interior-point solution sits on a huge
degenerate face that crossover cannot clear — it made 6.02 M dual pushes, ended
`imprecise`, and fell back to dual simplex without converging. Step 1 is reproducible
with Gurobi, which sidesteps this by solving with dual simplex outright.

`--build-year-step 1` is what `REPORT.md` measures. Anything coarser is a valid
comparison but not *that* comparison.

## Two things that will bite you

**1. `TotalDiscountedCost` in `solution.nc` is not the OSeMOSYS LP objective.**
linopy drops the objective's constant term, so the solver minimises everything except
fixed O&M on `ResidualCapacity`, while the saved variable includes it — 961,739 $m
saved against a 597,457 $m objective. PyPSA omits the same term for an unrelated
reason (the residual fleet is non-extendable, so it never enters the objective).
Subtract it from the OSeMOSYS side and the two are directly comparable at
597,457 vs 607,473 $m, a +1.68 % gap explained by two documented discounting
conventions. `osemosys_vs_pypsa/reconcile.py` does this, and `tests/test_reconcile.py`
fails loudly if either framework ever changes its mind.

**2. A coarser build-year step is only fair if both sides get it.** PyPSA ties
dispatch to the vintage component, so `--build-year-step 5` genuinely shrinks its
problem; OSeMOSYS offers `NewCapacity[r,t,y]` in every year regardless.
`run_benchmark.py` therefore pins OSeMOSYS's `capacity_additional_max` to zero in the
off-step years. The pre-presolve row count *grows* slightly when it does this (one
bound row per forbidden year — 3,608 rows at step 5); presolve removes the fixed
variables, so the LP that actually reaches the solver is the reduced one.

## Layout

```
comparison.ipynb              read this first -- every number and chart, from artefacts
REPORT.md                     the written findings
run_benchmark.py              solve both sides; writes a run directory
unpack_artifacts.sh           verify + unpack artifacts.tar.gz
reference/                    the original hand-written HTML comparison
osemosys_vs_pypsa/
  METHODOLOGY.md              the translation, field by field, and what was removed
  simplify_model.py           strip what PyPSA has no equivalent for, from the OSeMOSYS side
  converter.py                model.json -> pypsa.Network
  build_years.py              restrict OSeMOSYS investment to PyPSA's build years
  reconcile.py                make the two objectives comparable
  solverlog.py                parse HiGHS / Gurobi logs
  analysis.py                 comparison tables and charts
  runio.py                    load a run directory
tests/                        pytest; skips artefact-dependent tests if not unpacked
```

```bash
python -m pytest tests/ -q     # 20 tests, ~7 s
```

## Reference artefacts

`artifacts.tar.gz` holds the input model and two complete reference runs, both at
`--build-year-step 1`:

| run | contents | use it for |
|---|---|---|
| `reference/step1-gurobi` | both sides solved to optimality | results agreement |
| `reference/step1-highs` | the `REPORT.md` solve-time comparison; PyPSA has a log and no solved network because it never finished | the wall-clock story |

Their `manifest.json` files are marked `reconstructed: true` — these runs predate
`run_benchmark.py`, so their manifests were rebuilt from the solver logs by
`bundle_reference_artifacts.py`.

The labelled `.lp` files behind the row-by-row analysis in `REPORT.md` are **not**
bundled: they are 0.9 GB (OSeMOSYS) and 5.8 GB (PyPSA). Regenerate with
`run_benchmark.py --write-lp`, which exports with `explicit_coordinate_names=True` so
every variable and constraint reads like
`CAa4_Constraint_Capacity(GRIDREGION-JPN-SH,wind-offshore-unspecified,2023,winter_typical:00h00-02h00…)`.

## Versions

Solve times are only comparable against `REPORT.md` on the pinned versions in
`requirements.txt` — HiGHS in particular changes interior-point behaviour between
releases. The reference runs used Python 3.13, `pypsa==1.2.4`, `linopy==0.9.0`,
`tz-osemosys==0.4.0`, `highspy==1.15.1`, `gurobipy==13.0.2`. Every dependency is a
public package; nothing here needs access to the TransitionZero platform.
