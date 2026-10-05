"""AMSContext: thin wrapper around a live ``ams.System``.

Owns a single ``System`` instance per session. All Route 3-6 operations
mutate the system in place, mirroring the ex2.ipynb workflow:

    sp = ams.load(...)             # load_case
    sp.PQ.alter(...); sp.RTED.update('pd')   # apply_load_change
    sp.StaticGen.set(...); sp.RTED.update()  # trip_gen / restore_gen
    sp.Line.alter(...); sp.RTED.update()     # trip_line / restore_line
    sp.RTED.disable([...])                   # disable_constraints
    sp.RTED.config.update(t=1)               # set_config_t
    sp.RTED.run(solver=...)                  # solve
"""

import os
import copy
from typing import Any, Dict, List, Optional

import ams
import numpy as np

from agent.ams_engine.case_catalog import SHIPPED_CASES, resolve_case
from agent.ams_engine.routines import (
    compatible_solvers,
    is_routine_class,
    resolve_routine,
    routine_family,
)
from agent.ams_engine.formulation_edit import IBRReplacementSpec, apply_ibr_replacement


def resolve_case_path(case: str) -> str:
    """Resolve a short alias, keyword, or sub-path into a real case file path.

    Order of resolution:
    1. Absolute path that exists on disk.
    2. Deterministic keyword resolver (:func:`resolve_case`) — handles aliases,
       bus numbers, and name keywords. An ambiguous request resolves to that
       family's default.
    3. Pass-through to ``ams.get_case`` for unknown AMS sub-paths.
    """
    if os.path.isabs(case) and os.path.exists(case):
        return case
    res = resolve_case(case)
    if res.path:
        return ams.get_case(res.path)
    return ams.get_case(case)


class AMSContext:
    """Holds the live AMS System and the active routine name."""

    def __init__(self):
        self.system: Optional[ams.System] = None
        self.case_path: Optional[str] = None
        self.routine_name: str = "RTED"
        self.formulation_edit = None

    # ---------- Route 3: case I/O ----------
    def load_case(self, case: str) -> Dict[str, Any]:
        path = resolve_case_path(case)
        self.system = ams.load(path, setup=True, no_output=True)
        self.formulation_edit = None
        self.case_path = path
        return self.case_info()

    def case_info(self) -> Dict[str, Any]:
        if self.system is None:
            return {"loaded": False}
        ss = self.system
        gen_idx = list(ss.StaticGen.get_all_idxes())
        return {
            "loaded": True,
            "case_path": self.case_path,
            "n_bus": len(ss.Bus.idx.v),
            "n_line": len(ss.Line.idx.v),
            "n_pq": len(ss.PQ.idx.v),
            "n_pv": len(ss.PV.idx.v),
            "n_slack": len(ss.Slack.idx.v),
            "n_staticgen": len(gen_idx),
            "load_idx": list(ss.PQ.idx.v),
            "gen_idx": gen_idx,
            "line_idx": list(ss.Line.idx.v),
        }

    # ---------- Route 4: configure ----------
    def set_routine(self, name: str) -> str:
        stripped = (name or "").strip()
        if is_routine_class(stripped):
            resolved = stripped                       # exact class name — honor it
        else:
            res = resolve_routine(name)
            resolved = res.name if res.name else stripped.upper()
        if self.formulation_edit and resolved != "DCOPF":
            raise ValueError("An IBR replacement is active in DCOPF. Reload the case before switching routines.")
        if self.system is None:
            self.routine_name = resolved
            return resolved
        if not hasattr(self.system, resolved):
            raise ValueError(f"Routine '{name}' not found on system.")
        self.routine_name = resolved
        return resolved

    def active_routine(self):
        if self.system is None:
            raise RuntimeError("No case loaded. Use case_io to load one first.")
        return getattr(self.system, self.routine_name)

    def set_config_t(self, t: float) -> None:
        rtn = self.active_routine()
        rtn.config.update(t=float(t))
        rtn.update()

    def disable_constraints(self, names: List[str]) -> List[str]:
        rtn = self.active_routine()
        names = [n for n in names if n in rtn.constrs and rtn.constrs[n].is_disabled is False]
        if names:
            rtn.disable(names)
        return names

    def enable_constraints(self, names: List[str]) -> List[str]:
        rtn = self.active_routine()
        names = [n for n in names if n in rtn.constrs]
        if names:
            rtn.enable(names)
        return names

    def constraint_status(self) -> Dict[str, bool]:
        rtn = self.active_routine()
        return {name: (not c.is_disabled) for name, c in rtn.constrs.items()}

    # ---------- Route 5: physical modifications ----------
    def alter_load_p0(self, load_idx: str, value: float) -> None:
        if self.system is None:
            raise RuntimeError("No case loaded.")
        self.system.PQ.alter(src="p0", idx=[load_idx], value=[float(value)])
        if self.active_routine().initialized:
            self.active_routine().update("pd")

    def set_gen_status(self, gen_idx: str, online: bool) -> None:
        if self.formulation_edit and gen_idx == self.formulation_edit["generator_idx"]:
            raise ValueError("This generator has been replaced by an IBR. Reload the case to restore it.")
        if self.system is None:
            raise RuntimeError("No case loaded.")
        self.system.StaticGen.set(src="u", idx=gen_idx, attr="v", value=1 if online else 0)
        if self.active_routine().initialized:
            self.active_routine().update()

    def set_line_status(self, line_idx: str, online: bool) -> None:
        if self.system is None:
            raise RuntimeError("No case loaded.")
        self.system.Line.alter(src="u", idx=line_idx, value=1 if online else 0)
        if self.active_routine().initialized:
            self.active_routine().update()

    def alter_line_rate(self, line_idx: str, rate_a: float) -> None:
        if self.system is None:
            raise RuntimeError("No case loaded.")
        self.system.Line.set(src="rate_a", idx=[line_idx], attr="v", value=[float(rate_a)])
        if self.active_routine().initialized:
            self.active_routine().update("rate_a")

    # ---------- Route 6: structural formulation edits ----------
    def apply_formulation_edit(self, edit) -> Dict[str, Any]:
        if self.system is None:
            raise RuntimeError("Load a case before editing its formulation")
        generator_idx = next((idx for idx in self.system.StaticGen.get_all_idxes()
                              if str(idx) == str(edit.generator_idx)), edit.generator_idx)
        spec = IBRReplacementSpec(
            generator_idx=generator_idx,
            available_power=float(edit.available_power),
            dispatch_cost=float(edit.dispatch_cost),
            curtailment_cost=float(edit.curtailment_cost),
        )
        if self.system is None:
            raise RuntimeError("Load a case before editing its formulation")
        if self.formulation_edit:
            raise ValueError("An IBR replacement is already active. Reload the case for another scenario.")
        # Compile on a candidate system. A failed build cannot corrupt the live session.
        # Native solver handles cannot be pickled. Drop only their caches in
        # the copy, keeping the original solved system untouched.
        memo = {}
        for routine in self.system.routines.values():
            problem = getattr(getattr(routine, "om", None), "prob", None)
            cache = getattr(problem, "_solver_cache", None)
            if cache is not None:
                memo[id(cache)] = {}
        candidate = copy.deepcopy(self.system, memo)
        report = apply_ibr_replacement(getattr(candidate, self.routine_name), candidate, spec)
        self.system = candidate
        self.formulation_edit = report
        return report

    # ---------- Route 7: solve ----------
    def solve(self, solver: str = "CLARABEL") -> Dict[str, Any]:
        if self.system is None:
            raise RuntimeError("No case loaded.")
        rtn = self.active_routine()
        compat = compatible_solvers(self.routine_name)
        if compat and solver not in compat and "(internal: pypower)" not in compat:
            raise ValueError(
                f"Solver '{solver}' is not compatible with routine '{self.routine_name}'. "
                f"Compatible: {compat}"
            )
        ok = rtn.run(solver=solver)
        out: Dict[str, Any] = {
            "routine": self.routine_name,
            "solver": solver,
            "converged": bool(getattr(rtn, "converged", ok)),
            "exit_code": int(getattr(rtn, "exit_code", 0)),
        }
        if hasattr(rtn, "obj") and rtn.obj is not None:
            try:
                out["objective"] = float(np.asarray(rtn.obj.v))
            except Exception:
                out["objective"] = None
        for var_name in ("pg", "pibr", "pcurt", "plf", "pd", "pn", "aBus", "vBus", "ug"):
            if hasattr(rtn, var_name):
                try:
                    arr = np.asarray(getattr(rtn, var_name).v, dtype=float)
                    out[var_name] = arr.tolist()
                except Exception:
                    pass
        # element idxes for plotting / display
        out["gen_idx"] = list(self.system.StaticGen.get_all_idxes())
        out["line_idx"] = list(self.system.Line.idx.v)
        out["load_idx"] = list(self.system.PQ.idx.v)
        if self.formulation_edit:
            from agent.ams_engine.formulation_edit import verify_ibr_solution
            out["formulation_edit"] = self.formulation_edit
            out["ibr_idx"] = [f"IBR_{self.formulation_edit['generator_idx']}"]
            if out["converged"]:
                out["verification"] = verify_ibr_solution(rtn, self.formulation_edit, out)
        return out
