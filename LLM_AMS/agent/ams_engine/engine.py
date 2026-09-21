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
from typing import Any, Dict, List, Optional

import ams
import numpy as np
import pandas as pd
from ams.core.service import LoadScale

from agent.ams_engine.case_catalog import SHIPPED_CASES, resolve_case
from agent.ams_engine.routines import (
    PYPOWER_FAMILIES,
    compatible_solvers,
    is_routine_class,
    resolve_routine,
    routine_family,
)


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
    # An existing file given relative to the cwd (e.g. "generated/week/x.xlsx") is a file,
    # not a catalog key; without this it would be sent to ams.get_case and fail confusingly.
    if os.path.splitext(case)[1].lower() in (".xlsx", ".json", ".m", ".raw") and os.path.isfile(case):
        return os.path.abspath(case)
    res = resolve_case(case)
    if res.path:
        return ams.get_case(res.path)
    return ams.get_case(case)


# Per-load time curves. AMS has no (pq, slot) load table: in ED/UC the load matrix is
# pds[i, t] = sd[area(bus_i), t] * p0_i  (ams/core/service.py LoadScale.v), so every load
# in an Area shares one time shape. A scenario xlsx may carry an extra sheet
# EDSlotPQ(pq, slot, sd) / UCSlotPQ(pq, slot, sd); ams ignores unknown sheets, and this
# module multiplies the sheet's factor onto the routine's `pds` at run time. Area
# membership and the area-pooled reserve tables are never touched.
PQ_CURVE_SHEETS = {"EDSlotPQ": "EDSlot", "UCSlotPQ": "UCSlot"}   # sheet -> slot model it indexes


class LoadCurveConflict(ValueError):
    """A static ``PQ.p0`` edit was requested while the active routine carries a per-load
    curve. ``pds = sd[area, t] * curve[pq, t] * p0``, so the edit would be multiplied by
    the curve in every slot — refuse instead of compounding silently."""


class PQLoadScale(LoadScale):
    """``LoadScale`` whose ``.v`` is multiplied by a per-(PQ, slot) factor matrix.

    Installed by swapping the *class* of a routine's existing ``pds`` instance, so the
    optimisation model — which re-reads ``pds.v`` on every evaluate/update — sees the
    factor everywhere, including after ``routine.update()`` and in derived services
    such as ``UC.pdsp``.
    """

    sdpq: Optional[np.ndarray] = None   # (nPQ, nSlot); None -> plain LoadScale

    @property
    def v(self):
        base = LoadScale.v.fget(self)
        if self.sdpq is None:
            return base
        if np.shape(base) != self.sdpq.shape:
            raise RuntimeError(
                f"PQLoadScale: pds shape {np.shape(base)} != factor shape {self.sdpq.shape}; "
                f"the case changed after attach_pq_curves() (ams {ams.__version__})."
            )
        return base * self.sdpq


class AMSContext:
    """Holds the live AMS System and the active routine name."""

    def __init__(self):
        self.system: Optional[ams.System] = None
        self.case_path: Optional[str] = None
        self.routine_name: str = "RTED"
        # routine name -> PQ idxes that carry a per-slot curve (see attach_pq_curves)
        self.pq_curves: Dict[str, List[str]] = {}
        # routine name -> the sheet those curves came from (EDSlotPQ / UCSlotPQ)
        self.pq_curve_sheets: Dict[str, str] = {}

    # ---------- Route 3: case I/O ----------
    def load_case(self, case: str) -> Dict[str, Any]:
        path = resolve_case_path(case)
        self.system = ams.load(path, setup=True, no_output=True)
        self.case_path = path
        self.pq_curves = {}
        self.pq_curve_sheets = {}
        self._auto_attach_pq_curves()
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

    # ---------- per-load time curves (EDSlotPQ / UCSlotPQ sheets) ----------
    def _sheet_table(self, sheet: str) -> pd.DataFrame:
        """Return the raw sheet from ``system.df_in``, failing loudly if the hook is gone.

        ``df_in`` is set by ``andes.io.xlsx.read`` under a literal "for debugging" comment
        and has no API guarantee; it is the only place an unknown sheet survives loading.
        """
        df_in = getattr(self.system, "df_in", None)
        if not isinstance(df_in, dict):
            raise RuntimeError(
                f"system.df_in is missing or not a dict (ams {ams.__version__}, "
                f"case {self.case_path}); the xlsx reader no longer keeps raw sheets, so "
                f"{sheet} cannot be read. Update AMSContext.attach_pq_curves()."
            )
        if sheet not in df_in:
            raise KeyError(
                f"sheet {sheet!r} not in {self.case_path} (sheets: {sorted(df_in)})"
            )
        return df_in[sheet]

    def _auto_attach_pq_curves(self) -> None:
        """After ``load_case``: attach every PQ-curve sheet the xlsx file carries."""
        if not str(self.case_path).lower().endswith(".xlsx"):
            return
        in_file = set(pd.ExcelFile(self.case_path, engine="openpyxl").sheet_names)
        for sheet in PQ_CURVE_SHEETS:
            if sheet in in_file:
                # the file has the sheet -> df_in must expose it, or we would silently
                # solve the base load and call it a scenario
                self.attach_pq_curves(self._sheet_table(sheet), sheet=sheet)

    def attach_pq_curves(self, table=None, sheet: str = "EDSlotPQ") -> Dict[str, List[str]]:
        """Multiply per-(PQ, slot) factors onto ``pds`` of every routine indexed by ``sheet``.

        Parameters
        ----------
        table : DataFrame | list[dict] | None
            Rows with columns ``pq`` (PQ idx), ``slot`` (EDSlot/UCSlot idx), ``sd`` (factor).
            ``None`` reads the sheet named ``sheet`` from the loaded xlsx.
        sheet : {"EDSlotPQ", "UCSlotPQ"}
            Decides which routines receive the factors (those whose ``timeslot`` is the
            matching slot model) and which slot idxes are valid.

        Missing ``(pq, slot)`` cells default to 1.0; duplicates, unknown ``pq``/``slot`` and
        NaN raise. Returns ``{routine_name: [pq idx with a curve]}`` for the routines touched.
        """
        if self.system is None:
            raise RuntimeError("No case loaded.")
        if sheet not in PQ_CURVE_SHEETS:
            raise ValueError(f"sheet must be one of {sorted(PQ_CURVE_SHEETS)}, got {sheet!r}")
        df = self._sheet_table(sheet) if table is None else pd.DataFrame(table)
        missing = {"pq", "slot", "sd"} - set(df.columns)
        if missing:
            raise ValueError(f"{sheet}: missing columns {sorted(missing)}")
        sd_arr = pd.to_numeric(df["sd"], errors="coerce").to_numpy(dtype=float)
        ok = np.isfinite(sd_arr) & (sd_arr >= 0)          # a factor of 0 is a legitimate "load off"
        if not ok.all():
            bad = df.loc[~ok, ["pq", "slot", "sd"]].values.tolist()
            raise ValueError(f"{sheet}: sd must be finite numbers >= 0; bad rows {bad[:5]}")
        dup = df.duplicated(subset=["pq", "slot"], keep=False)
        if dup.any():
            raise ValueError(f"{sheet}: duplicate (pq, slot) rows: {df[dup][['pq', 'slot']].values.tolist()}")

        slot_model = PQ_CURVE_SHEETS[sheet]
        touched: Dict[str, List[str]] = {}
        for name, rtn in self.system.routines.items():
            if getattr(getattr(rtn, "timeslot", None), "model", None) != slot_model or not hasattr(rtn, "pds"):
                continue
            touched[name] = self._attach_to_routine(rtn, df, sheet)
            self.pq_curves[name] = touched[name]
            self.pq_curve_sheets[name] = sheet
        if not touched:
            raise RuntimeError(f"no routine on the system uses {slot_model}; nothing to attach {sheet} to")
        return touched

    @staticmethod
    def _attach_to_routine(rtn, df: pd.DataFrame, sheet: str) -> List[str]:
        pds = getattr(rtn, "pds")
        if not isinstance(pds, LoadScale):
            raise TypeError(
                f"{rtn.class_name}.pds is {type(pds).__module__}.{type(pds).__name__}, not "
                f"ams.core.service.LoadScale (ams {ams.__version__}); PQLoadScale cannot be installed."
            )
        pq_idx = [str(i) for i in pds.u.get_all_idxes()]          # row order LoadScale.v uses
        slots = [str(x) for x in np.asarray(rtn.timeslot.v).tolist()]   # column order = horizon
        M = np.ones((len(pq_idx), len(slots)), dtype=float)
        for r in df.itertuples(index=False):
            pq, slot = str(r.pq), str(r.slot)
            if pq not in pq_idx:
                raise ValueError(f"{sheet}: unknown pq {pq!r} (loads: {pq_idx})")
            if slot not in slots:
                raise ValueError(f"{sheet}: unknown slot {slot!r} for {rtn.class_name} (first: {slots[:3]})")
            M[pq_idx.index(pq), slots.index(slot)] = float(r.sd)
        pds.__class__ = PQLoadScale
        pds.sdpq = M
        if getattr(rtn, "initialized", False):
            rtn.update()                                     # push the new pds.v into om
        return sorted({str(p) for p in df["pq"]})

    # ---------- Route 5: physical modifications ----------
    def alter_load_p0(self, load_idx: str, value: float) -> None:
        if self.system is None:
            raise RuntimeError("No case loaded.")
        # PQ.p0 is shared by every routine on the system, so a curve attached to ANY routine
        # (not just the active one) would compound with the edit as soon as that routine runs.
        # Only the curved loads are affected: an omitted (pq, slot) cell is a factor of 1.0,
        # so editing an uncurved load's p0 is an ordinary constant scaling.
        curved_on = sorted(r for r, pqs in self.pq_curves.items() if str(load_idx) in pqs)
        if curved_on:
            attached = sorted({self.pq_curve_sheets.get(r, "?") for r in curved_on})
            raise LoadCurveConflict(
                f"Cannot set {load_idx} p0 = {float(value)} pu: this load carries a per-load time "
                f"curve from sheet(s) {attached} on routines {curved_on}; active routine is "
                f"{self.routine_name}. The dispatched load is pds = sd(area, t) x curve(pq, t) x p0, "
                f"so a new p0 would be multiplied by the curve in every slot and the two effects would "
                f"compound. Change the {'/'.join(attached)} curve for {load_idx} in the case file instead."
            )
        self.system.PQ.alter(src="p0", idx=[load_idx], value=[float(value)])
        self.active_routine().update("pd")

    def set_gen_status(self, gen_idx: str, online: bool) -> None:
        if self.system is None:
            raise RuntimeError("No case loaded.")
        self.system.StaticGen.set(src="u", idx=gen_idx, attr="v", value=1 if online else 0)
        self.active_routine().update()

    def set_line_status(self, line_idx: str, online: bool) -> None:
        if self.system is None:
            raise RuntimeError("No case loaded.")
        self.system.Line.alter(src="u", idx=line_idx, value=1 if online else 0)
        self.active_routine().update()

    def alter_line_rate(self, line_idx: str, rate_a: float) -> None:
        if self.system is None:
            raise RuntimeError("No case loaded.")
        self.system.Line.set(src="rate_a", idx=[line_idx], attr="v", value=[float(rate_a)])
        self.active_routine().update("rate_a")

    def _accepts_ignore_dpp(self) -> bool:
        """True for cvxpy-backed routines (those with an OModel); PYPOWER routines take no such kwarg."""
        if routine_family(self.routine_name) in PYPOWER_FAMILIES:
            return False
        return hasattr(self.active_routine(), "om")

    # ---------- Route 6: solve ----------
    def solve(self, solver: str = "CLARABEL", ignore_dpp: bool = False) -> Dict[str, Any]:
        if self.system is None:
            raise RuntimeError("No case loaded.")
        rtn = self.active_routine()
        compat = compatible_solvers(self.routine_name)
        if compat and solver not in compat and "(internal: pypower)" not in compat:
            raise ValueError(
                f"Solver '{solver}' is not compatible with routine '{self.routine_name}'. "
                f"Compatible: {compat}"
            )
        # ignore_dpp goes through RoutineBase.run() to cvxpy prob.solve(); cvxpy 1.9.2's DPP
        # canonicalisation fails above 160 slots and ignoring DPP gives identical results.
        # It is a cvxpy-only keyword: never forward it to PYPOWER-backed routines, and record
        # what was actually applied rather than what was asked for.
        ignore_dpp = bool(ignore_dpp) and self._accepts_ignore_dpp()
        run_kwargs: Dict[str, Any] = {"solver": solver}
        if ignore_dpp:
            run_kwargs["ignore_dpp"] = True
        ok = rtn.run(**run_kwargs)
        out: Dict[str, Any] = {
            "routine": self.routine_name,
            "solver": solver,
            "converged": bool(getattr(rtn, "converged", ok)),
            "exit_code": int(getattr(rtn, "exit_code", 0)),
            # provenance: what produced these numbers
            "status": str(getattr(getattr(getattr(rtn, "om", None), "prob", None), "status", "")),
            "ams_version": ams.__version__,
            "ignore_dpp": bool(ignore_dpp),
            "disabled_constraints": sorted(n for n, c in rtn.constrs.items() if c.is_disabled),
            "pq_curves": list(self.pq_curves.get(self.routine_name, [])),
            "pq_curve_sheet": self.pq_curve_sheets.get(self.routine_name),
        }
        if hasattr(rtn, "obj") and rtn.obj is not None:
            try:
                out["objective"] = float(np.asarray(rtn.obj.v))
            except Exception:
                out["objective"] = None
        # pi (LMP), pds (scaled load), SOC, ugd are 2-D (device x slot) on multi-period routines
        for var_name in ("pg", "plf", "pd", "pn", "aBus", "vBus", "ug", "pi", "pds", "SOC", "ugd"):
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
        out["bus_idx"] = list(self.system.Bus.idx.v)
        if hasattr(rtn, "timeslot"):
            try:
                out["slot_idx"] = [str(x) for x in np.asarray(rtn.timeslot.v).tolist()]
            except Exception:
                pass
        return out
