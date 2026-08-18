"""Pull the solve-time story out of a solver log.

The wall-clock comparison in REPORT.md is entirely readable from these logs:
LP shape before and after presolve, interior-point iteration count, crossover
push counts, and -- for a run that never finished -- where the simplex cleanup
had got to when the log ends.

HiGHS and Gurobi logs are both understood; ``SolveLog.solver`` records which was
detected. The crossover-push fields are HiGHS-only, so the degenerate-face story
in REPORT.md can only be read off a HiGHS log.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_LP_SHAPE = re.compile(r"has (\d+) rows; (\d+) cols; (\d+) nonzeros")
_PRESOLVE = re.compile(
    r"Presolve reductions: rows (\d+)\([^)]*\); columns (\d+)\([^)]*\); nonzeros (\d+)"
)
_COST_RANGE = re.compile(r"^\s*Cost\s+\[([\d.e+-]+), ([\d.e+-]+)\]")
_IPM_ITERS = re.compile(r"^HiPO iterations:\s+(\d+)")
_DUAL_PUSHES = re.compile(r"Number of dual pushes required:\s+(\d+)")
_PRIMAL_PUSHES = re.compile(r"Number of primal pushes required:\s+(\d+)")
_IPM_STATUS = re.compile(r"Status interior point solve:\s+(\S+)")
_CROSSOVER_STATUS = re.compile(r"Status crossover:\s+(\S+)")
_IPM_RUNTIME = re.compile(r"^\s+Runtime:\s+([\d.]+)s")
_HIPO_RUNTIME = re.compile(r"^HiPO runtime:\s+([\d.]+)")
_OBJECTIVE_VALUE = re.compile(r"objective value:\s+([\d.e+-]+)")
_MODEL_STATUS = re.compile(r"^Model status\s+:\s+(.+)$")
_TOTAL_RUNTIME = re.compile(r"^HiGHS run time\s+:\s+([\d.]+)")
_CROSSOVER_ITERS = re.compile(r"^Crossover iterations:\s+(\d+)")
_SIMPLEX_LINE = re.compile(r"^\s+(\d+)\s+([-\d.e+]+)\s+(?:Ph1|Pr):.*?([\d.]+)s\s*$")

_GRB_LP = re.compile(r"Optimize a model with (\d+) rows, (\d+) columns and (\d+) nonzeros")
_GRB_PRESOLVED = re.compile(r"^Presolved: (\d+) rows, (\d+) columns, (\d+) nonzeros")
_GRB_BARRIER = re.compile(r"Barrier solved model in (\d+) iterations and ([\d.]+) seconds")
_GRB_SOLVED = re.compile(r"^Solved in (\d+) iterations and ([\d.]+) seconds")
_GRB_OPTIMAL = re.compile(r"^Optimal objective\s+([\d.e+-]+)")
_GRB_OBJ_RANGE = re.compile(r"^\s*Objective range\s+\[([\d.e+-]+), ([\d.e+-]+)\]")


@dataclass
class SolveLog:
    """Parsed HiGHS log. Fields are None when the log does not report them."""

    path: Path
    solver: str = "unknown"
    rows: int | None = None
    cols: int | None = None
    nonzeros: int | None = None
    presolve_rows: int | None = None
    presolve_cols: int | None = None
    presolve_nonzeros: int | None = None
    cost_range: tuple[float, float] | None = None
    ipm_iterations: int | None = None
    ipm_runtime_seconds: float | None = None
    hipo_runtime_seconds: float | None = None
    ipm_status: str | None = None
    crossover_status: str | None = None
    crossover_iterations: int | None = None
    simplex_iterations: int | None = None
    dual_pushes: int | None = None
    primal_pushes: int | None = None
    objective: float | None = None
    model_status: str | None = None
    total_runtime_seconds: float | None = None
    last_simplex_iteration: int | None = None
    last_simplex_objective: float | None = None
    last_simplex_seconds: float | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def completed(self) -> bool:
        """True if HiGHS reported a final model status."""
        return self.model_status is not None

    @property
    def wall_clock_seconds(self) -> float | None:
        """Total runtime, falling back to the last timestamp of a truncated log."""
        if self.total_runtime_seconds is not None:
            return self.total_runtime_seconds
        return self.last_simplex_seconds

    @property
    def seconds_per_ipm_iteration(self) -> float | None:
        """Interior-point wall time per iteration.

        Divides ``HiPO runtime`` (interior point, before the crossover push
        phase) where the log reports it, else the IPX ``Runtime`` which includes
        crossover. REPORT.md quotes 10.2 and 57.2 s/iter on a slightly tighter
        definition again; the 5.5-5.6x ratio between the two sides is the same.
        """
        runtime = self.hipo_runtime_seconds or self.ipm_runtime_seconds
        if not self.ipm_iterations or runtime is None:
            return None
        return runtime / self.ipm_iterations

    def summary(self) -> dict[str, Any]:
        """Flat dict for tabulating several logs side by side."""
        return {
            "solver": self.solver,
            "status": self.model_status or f"truncated ({self.crossover_status})",
            "wall_clock_s": self.wall_clock_seconds,
            "rows": self.rows,
            "cols": self.cols,
            "nonzeros": self.nonzeros,
            "presolve_rows": self.presolve_rows,
            "presolve_cols": self.presolve_cols,
            "ipm_iterations": self.ipm_iterations,
            "s_per_ipm_iteration": self.seconds_per_ipm_iteration,
            "crossover_status": self.crossover_status,
            "dual_pushes": self.dual_pushes,
            "primal_pushes": self.primal_pushes,
            "objective": self.objective,
            "cost_range": self.cost_range,
        }


def parse(path: str | Path) -> SolveLog:
    """Parse a HiGHS or Gurobi log written by ``run_benchmark.py``."""
    path = Path(path)
    text = path.read_text(errors="replace")
    if "Gurobi Optimizer" in text or "Optimize a model with" in text:
        return _parse_gurobi(path, text)
    return _parse_highs(path, text)


def _parse_gurobi(path: Path, text: str) -> SolveLog:
    """Parse a Gurobi log. Crossover-push detail is not reported by Gurobi."""
    log = SolveLog(path=path, solver="gurobi")

    for line in text.splitlines():
        if match := _GRB_LP.search(line):
            log.rows, log.cols, log.nonzeros = (int(g) for g in match.groups())
        elif match := _GRB_PRESOLVED.match(line):
            log.presolve_rows, log.presolve_cols, log.presolve_nonzeros = (
                int(g) for g in match.groups()
            )
        elif match := _GRB_OBJ_RANGE.match(line):
            log.cost_range = (float(match.group(1)), float(match.group(2)))
        elif match := _GRB_BARRIER.search(line):
            log.ipm_iterations = int(match.group(1))
            log.ipm_runtime_seconds = float(match.group(2))
        elif match := _GRB_SOLVED.match(line):
            log.simplex_iterations = int(match.group(1))
            log.total_runtime_seconds = float(match.group(2))
        elif match := _GRB_OPTIMAL.match(line):
            log.objective = float(match.group(1))
            log.model_status = "Optimal"
        elif line.startswith("WARNING"):
            log.warnings.append(line.removeprefix("WARNING:").strip())

    return log


def _parse_highs(path: Path, text: str) -> SolveLog:
    """Parse a HiGHS log, including the HiPO interior-point and crossover report."""
    log = SolveLog(path=path, solver="highs")

    for line in text.splitlines():
        if match := _LP_SHAPE.search(line):
            log.rows, log.cols, log.nonzeros = (int(g) for g in match.groups())
        elif match := _PRESOLVE.search(line):
            log.presolve_rows, log.presolve_cols, log.presolve_nonzeros = (
                int(g) for g in match.groups()
            )
        elif match := _COST_RANGE.match(line):
            log.cost_range = (float(match.group(1)), float(match.group(2)))
        elif match := _IPM_ITERS.match(line):
            log.ipm_iterations = int(match.group(1))
        elif match := _CROSSOVER_ITERS.match(line):
            log.crossover_iterations = int(match.group(1))
        elif match := _DUAL_PUSHES.search(line):
            log.dual_pushes = int(match.group(1))
        elif match := _PRIMAL_PUSHES.search(line):
            log.primal_pushes = int(match.group(1))
        elif match := _IPM_STATUS.search(line):
            log.ipm_status = match.group(1)
        elif match := _CROSSOVER_STATUS.search(line):
            log.crossover_status = match.group(1)
        elif match := _IPM_RUNTIME.match(line):
            log.ipm_runtime_seconds = float(match.group(1))
        elif match := _HIPO_RUNTIME.match(line):
            log.hipo_runtime_seconds = float(match.group(1))
        elif match := _OBJECTIVE_VALUE.search(line):
            log.objective = float(match.group(1))
        elif match := _MODEL_STATUS.match(line):
            log.model_status = match.group(1).strip()
        elif match := _TOTAL_RUNTIME.match(line):
            log.total_runtime_seconds = float(match.group(1))
        elif match := _SIMPLEX_LINE.match(line):
            log.last_simplex_iteration = int(match.group(1))
            log.last_simplex_objective = float(match.group(2))
            log.last_simplex_seconds = float(match.group(3))
        elif line.startswith("WARNING"):
            log.warnings.append(line.removeprefix("WARNING:").strip())

    return log
