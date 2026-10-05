"""Solve a written scenario and its base under one regime, producing comparable records.

Every AMS call goes through :class:`AMSContext` on a fresh instance (batch work never
touches the interactive session). ``disabled_constraints`` are applied through
``AMSContext.disable_constraints`` — the configure node's single path.
"""

from __future__ import annotations

from typing import List, Tuple

from agent.ams_engine.case_writer import ScenarioArtifact, write_scenario
from agent.ams_engine.engine import AMSContext, resolve_case_path
from agent.schemas.scenario import ComparisonResult, RunRecord, ScenarioSpec


def run_case(case_path: str, routine: str, solver: str, ignore_dpp: bool,
             disabled_constraints: List[str], label: str) -> RunRecord:
    ctx = AMSContext()
    ctx.load_case(case_path)
    ctx.set_routine(routine)
    if disabled_constraints:
        known = set(ctx.active_routine().constrs)
        unknown = sorted(set(disabled_constraints) - known)
        if unknown:
            raise ValueError(f"{routine} has no constraint(s) {unknown}; known: {sorted(known)}")
        ctx.disable_constraints(list(disabled_constraints))
    res = ctx.solve(solver, ignore_dpp=ignore_dpp)
    return RunRecord.from_solve(res, label=label, case_path=ctx.case_path)


def run_scenario(spec: ScenarioSpec, out_dir: str,
                 allow_base_curves: bool = False) -> Tuple[ScenarioArtifact, RunRecord]:
    art = write_scenario(spec, out_dir, allow_base_curves=allow_base_curves)
    rec = run_case(art.xlsx_path, spec.routine, spec.solver, spec.ignore_dpp, spec.disabled_constraints, spec.id)
    return art, rec


def run_comparison(spec: ScenarioSpec, out_dir: str,
                   allow_base_curves: bool = False) -> Tuple[ScenarioArtifact, ComparisonResult]:
    """Base and scenario solved from the same spec; ComparisonResult re-checks the regime.

    For a ``bus_perturbation`` scenario the base must not carry a curve sheet of its own
    (a stale EDSlotPQ/UCSlotPQ from an earlier write); that is refused before the scenario
    is even written unless ``allow_base_curves=True`` is passed explicitly.
    """
    base = run_case(resolve_case_path(spec.base_case), spec.routine, spec.solver, spec.ignore_dpp,
                    spec.disabled_constraints, f"{spec.id}:base")
    if base.pq_curves and not allow_base_curves:
        raise ValueError(
            f"base case {base.case_path} already carries curve sheet {base.pq_curve_sheet!r} "
            f"affecting loads {base.pq_curves}; a {spec.method} comparison against it would mix "
            f"load regimes. Use a clean base case, or pass allow_base_curves=True explicitly."
        )
    art, scen = run_scenario(spec, out_dir, allow_base_curves=allow_base_curves)
    return art, ComparisonResult(base=base, scenario=scen, allow_base_curves=allow_base_curves)


# --------------------------------------------------------------------------- #
# outputs (plan §4.4): stdlib csv tables under generated/<study>_<ts>/
# --------------------------------------------------------------------------- #
def write_records(out_dir: str, records: List[RunRecord], study: str = "study") -> str:
    """Write one CSV per (record, variable) plus ``summary.json``; returns the study dir.

    Tables are slot-major: one row per slot, one column per device, first column ``slot``.
    Status vocabulary: ``optimal | failed | error``.
    """
    import csv
    import json
    import os
    from datetime import datetime

    study_dir = os.path.join(os.path.abspath(out_dir), f"{study}_{datetime.now():%Y%m%d_%H%M%S}")
    os.makedirs(study_dir, exist_ok=True)
    summary = []
    for rec in records:
        slots = rec.slot_idx or ["t0"]
        for var, cols in (("pg", rec.gen_idx), ("pi", rec.bus_idx), ("pds", rec.load_idx)):
            mat = getattr(rec, var)
            if not mat:
                continue
            path = os.path.join(study_dir, f"{rec.label}_{var}.csv")
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["slot"] + [str(c) for c in cols])
                for t, slot in enumerate(slots):
                    w.writerow([slot] + [f"{row[t]:.9g}" for row in mat])
        status = "optimal" if rec.converged and rec.status == "optimal" else ("failed" if rec.status else "error")
        summary.append({
            "label": rec.label, "case_path": rec.case_path, "routine": rec.routine, "solver": rec.solver,
            "status": status, "objective": rec.objective, "horizon_slots": rec.horizon_slots,
            "ams_version": rec.ams_version, "ignore_dpp": rec.ignore_dpp,
            "disabled_constraints": rec.disabled_constraints, "pq_curves": rec.pq_curves,
            "pq_curve_sheet": rec.pq_curve_sheet,
        })
    with open(os.path.join(study_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    return study_dir
