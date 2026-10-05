"""Deterministic scenario case writer: one xlsx per scenario, full horizon.

Given a validated :class:`~agent.schemas.scenario.ScenarioSpec` and the base case, write a
self-contained scenario case file plus a JSON manifest.

- ``uniform`` / ``regional``: the base case's ``EDSlotLoad.sd`` / ``UCSlotLoad.sd`` rows for
  the targeted Areas are multiplied in place (native tables, nothing else changes).
- ``bus_perturbation``: the loads on the target bus get per-slot multipliers written as
  extra sheets ``EDSlotPQ`` / ``UCSlotPQ`` ``(pq, slot, sd)``. No Area is created or moved,
  no SFR/SR/NSR rows are added. ``ams.io.xlsx.write`` iterates ``system.models`` only, so
  it would drop these sheets on write-back — this module appends them itself.

``EDSlotPQ``/``UCSlotPQ`` are an LLM_LTB-private convention: ``ams.load`` ignores unknown
sheets, so another ams user opening the file solves the *base* load. The writer therefore
puts a note into the ``Summary`` sheet (first sheet, round-tripped by ams) where a person
opening the file sees it, and repeats it in the manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List

import ams
import pandas as pd

from agent.ams_engine.engine import PQ_CURVE_SHEETS, resolve_case_path
from agent.schemas.scenario import ScenarioSpec

# slot model -> per-area load table whose `sd` uniform/regional scale
SLOT_LOAD_TABLES = {"EDSlot": "EDSlotLoad", "UCSlot": "UCSlotLoad"}

CONVENTION_NOTE = (
    "LLM_LTB-private sheet: per-load time-curve multipliers (pq, slot, sd) applied on top of "
    "EDSlotLoad/UCSlotLoad by LLM_LTB's AMSContext at run time. Plain ams.load() IGNORES this "
    "sheet and solves the BASE load. Do not treat this file as an ams-native scenario."
)


@dataclass
class ScenarioArtifact:
    xlsx_path: str
    manifest_path: str
    curve_sheets: List[str] = field(default_factory=list)   # sheets appended by the writer
    affected_loads: List[str] = field(default_factory=list)
    sha256: str = ""


# --------------------------------------------------------------------------- #
# validation (needs the loaded base case; cannot live in the pydantic schema)
# --------------------------------------------------------------------------- #
def _slot_tables(base_system) -> Dict[str, int]:
    """{slot model: n rows} for the slot tables the base case actually carries."""
    return {m: getattr(base_system, m).n for m in PQ_CURVE_SHEETS.values() if getattr(base_system, m).n > 0}


def planned_curve_sheets(spec: ScenarioSpec, base_system) -> Dict[str, str]:
    """{curve sheet: slot model} the writer will append for this spec (empty unless bus-level)."""
    if spec.method != "bus_perturbation":
        return {}
    tables = _slot_tables(base_system)
    return {sheet: m for sheet, m in PQ_CURVE_SHEETS.items() if m in tables}


def base_curve_sheets(base_system) -> Dict[str, pd.DataFrame]:
    """Curve sheets the base xlsx already carries (from ``system.df_in``; {} for non-xlsx)."""
    df_in = getattr(base_system, "df_in", None)
    if not isinstance(df_in, dict):
        return {}
    return {sheet: df_in[sheet] for sheet in PQ_CURVE_SHEETS if sheet in df_in}


def loads_on_bus(base_system, bus) -> List[str]:
    return [str(i) for i, b in zip(base_system.PQ.idx.v, base_system.PQ.bus.v) if str(b) == str(bus)]


def validate_scenario(spec: ScenarioSpec, base_system, allow_base_curves: bool = False) -> Dict[str, object]:
    """Check ``spec`` against the loaded base case. Raises ``ValueError``; returns a report.

    Rules (docs/multi_period_load_scenario_plan.md §4.2):
    - the routine exists and its horizon equals ``spec.horizon_slots``;
    - the target exists (Area idx with loads / Bus idx with loads);
    - curve-sheet completeness: if the base case carries both EDSlot and UCSlot rows, a
      bus-level shape must write both EDSlotPQ and UCSlotPQ (EDT_k <-> UCT_k by position).
      A sheet that cannot be written is an error naming that sheet — never a default;
    - a base that already carries a curve sheet (a file left over from an earlier scenario
      write) is refused, naming the sheet and the loads it affects, unless
      ``allow_base_curves=True`` — in which case the writer *composes* (multiplies) the new
      curve onto the existing rows instead of silently replacing the sheet.
    """
    rtn = getattr(base_system, spec.routine, None)
    if rtn is None:
        raise ValueError(f"routine {spec.routine!r} not on base case")
    stale = base_curve_sheets(base_system)
    if stale and not allow_base_curves:
        desc = "; ".join(f"{sheet!r} affecting loads {sorted(set(map(str, df['pq'])))}" for sheet, df in stale.items())
        raise ValueError(
            f"base case already carries per-load curve sheet {desc}. Writing a scenario on top of it "
            f"would either drop or compound those curves. Use a clean base case, or pass "
            f"allow_base_curves=True to compose the new curve onto the existing rows explicitly."
        )
    if not hasattr(rtn, "timeslot"):
        raise ValueError(
            f"routine {spec.routine!r} is single-period; scenario studies require a timeslot routine"
        )
    n = int(rtn.timeslot.n)
    if n != spec.horizon_slots:
        raise ValueError(f"spec.horizon_slots={spec.horizon_slots} but {spec.routine} has {n} slots")

    areas = [str(a) for a in base_system.Area.idx.v]
    report: Dict[str, object] = {"routine": spec.routine, "horizon_slots": n, "curve_sheets": []}

    if spec.method == "regional":
        if str(spec.target) not in areas:
            raise ValueError(f"target Area {spec.target!r} not in {areas}")
        bus_area = base_system.Bus.get(src="area", idx=list(base_system.PQ.bus.v), attr="v")
        loads = [str(i) for i, a in zip(base_system.PQ.idx.v, bus_area) if str(a) == str(spec.target)]
        if not loads:
            raise ValueError(f"Area {spec.target!r} has no PQ load")
        report["affected_loads"] = loads
    elif spec.method == "bus_perturbation":
        loads = loads_on_bus(base_system, spec.target)
        if not loads:
            raise ValueError(f"bus {spec.target!r} has no PQ load")
        report["affected_loads"] = loads
        tables = _slot_tables(base_system)
        planned = planned_curve_sheets(spec, base_system)
        for sheet, model in PQ_CURVE_SHEETS.items():
            if model in tables and sheet not in planned:
                raise ValueError(f"base case has {model} rows but sheet {sheet} would not be written")
        report["curve_sheets"] = list(planned)
    else:
        report["affected_loads"] = [str(i) for i in base_system.PQ.idx.v]

    # A per-slot curve is applied by position to EVERY slot table the case carries (so ED
    # and UC describe the same scenario); a table of a different length cannot be mapped.
    for model, n_rows in _slot_tables(base_system).items():
        if n_rows != spec.horizon_slots and (spec.curve is not None or spec.method == "bus_perturbation"):
            sheet = next(sh for sh, m in PQ_CURVE_SHEETS.items() if m == model)
            what = sheet if spec.method == "bus_perturbation" else SLOT_LOAD_TABLES[model]
            raise ValueError(
                f"cannot write {what}: {model} has {n_rows} rows but the scenario has "
                f"{spec.horizon_slots} slots; EDT_k <-> UCT_k mapping by position is impossible")
    return report


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #
def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _scale_area_rows(base_system, slot_model: str, areas: List[str], mult: List[float]) -> int:
    """Multiply `sd` of the per-area load table rows (for `areas`) by mult[t]; returns rows touched."""
    table = getattr(base_system, SLOT_LOAD_TABLES[slot_model])
    slots = [str(s) for s in getattr(base_system, slot_model).idx.v]
    if len(slots) != len(mult):
        raise ValueError(f"{slot_model} has {len(slots)} rows but {len(mult)} multipliers were given")
    touched = 0
    for row, area, slot, sd in zip(table.idx.v, table.area.v, table.slot.v, table.sd.v):
        if str(area) in areas:
            table.alter(src="sd", idx=row, value=float(sd) * mult[slots.index(str(slot))])
            touched += 1
    return touched


def write_scenario(spec: ScenarioSpec, out_dir: str, allow_base_curves: bool = False) -> ScenarioArtifact:
    """Validate, then write ``<out_dir>/<spec.id>.xlsx`` and ``<spec.id>.manifest.json``."""
    base_path = resolve_case_path(spec.base_case)
    ss = ams.load(base_path, setup=False, no_output=True)
    report = validate_scenario(spec, ss, allow_base_curves=allow_base_curves)
    inherited = base_curve_sheets(ss) if allow_base_curves else {}
    mult = spec.multipliers()
    out_dir = os.path.abspath(out_dir)          # AMSContext.load_case needs absolute paths
    os.makedirs(out_dir, exist_ok=True)
    xlsx_path = os.path.join(out_dir, f"{spec.id}.xlsx")
    manifest_path = os.path.join(out_dir, f"{spec.id}.manifest.json")

    curve_rows: Dict[str, List[dict]] = {}
    if spec.method in ("uniform", "regional"):
        areas = [str(a) for a in ss.Area.idx.v] if spec.method == "uniform" else [str(spec.target)]
        # every slot-load table the case carries is scaled, so ED and UC see the same
        # scenario; a constant factor broadcasts, a curve must match the table length
        for slot_model, n_rows in _slot_tables(ss).items():
            table_mult = [float(spec.factor)] * n_rows if spec.factor is not None else mult
            _scale_area_rows(ss, slot_model, areas, table_mult)
        ss.add("Summary", field="Scenario",
               comment=f"{spec.id}: {spec.method} x{min(mult):.3f}..{max(mult):.3f} on areas {areas} "
                       f"(written by LLM_LTB case_writer {datetime.now():%Y-%m-%d})")
        # allow_base_curves=True: the base's per-load sheets must survive the rewrite (ams's own
        # writer drops unknown sheets). The area scaling lives in the sd rows, so carrying the
        # curve rows through unchanged composes multiplicatively with it.
        for sheet, df in inherited.items():
            curve_rows[sheet] = [dict(pq=str(r.pq), slot=str(r.slot), sd=float(r.sd))
                                 for r in df.itertuples(index=False)]
    else:
        loads = report["affected_loads"]
        for sheet, slot_model in planned_curve_sheets(spec, ss).items():
            slots = [str(s) for s in getattr(ss, slot_model).idx.v]
            # start from the base's own rows when composing is explicitly allowed
            cells: Dict[tuple, float] = {}
            if sheet in inherited:
                for r in inherited[sheet].itertuples(index=False):
                    cells[(str(r.pq), str(r.slot))] = float(r.sd)
            for pq in loads:
                for t, slot in enumerate(slots):
                    cells[(pq, slot)] = cells.get((pq, slot), 1.0) * float(mult[t])
            curve_rows[sheet] = [dict(pq=pq, slot=slot, sd=sd) for (pq, slot), sd in cells.items()]
        # visible to whoever opens the file: Summary is the first sheet and ams round-trips it
        ss.add("Summary", field="Scenario",
               comment=f"{spec.id}: bus {spec.target} loads {loads} follow sheets {list(curve_rows)} "
                       f"(written by LLM_LTB case_writer {datetime.now():%Y-%m-%d})")
        for sheet in curve_rows:
            ss.add("Summary", field=sheet, comment=CONVENTION_NOTE)

    ss.setup()
    ams.io.xlsx.write(ss, xlsx_path, overwrite=True)
    if curve_rows:
        with pd.ExcelWriter(xlsx_path, engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
            for sheet, rows in curve_rows.items():
                pd.DataFrame(rows, columns=["pq", "slot", "sd"]).to_excel(w, sheet_name=sheet, index=False)

    manifest = {
        "scenario": spec.model_dump(),
        "base_case": {"path": base_path, "sha256": _sha256(base_path)},
        "output": {"path": xlsx_path, "sha256": _sha256(xlsx_path)},
        "written_at": datetime.now().isoformat(timespec="seconds"),
        "ams_version": ams.__version__,
        "affected_loads": report["affected_loads"],
        "multipliers": {"min": float(min(mult)), "max": float(max(mult)), "n": len(mult)},
        "curve_sheets": {s: len(r) for s, r in curve_rows.items()},
        "applied_at_run_time": {"disabled_constraints": spec.disabled_constraints, "ignore_dpp": spec.ignore_dpp},
        "convention_note": CONVENTION_NOTE if curve_rows else None,
        "composed_onto_base_curves": sorted(inherited) if inherited else [],
    }
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    return ScenarioArtifact(xlsx_path=xlsx_path, manifest_path=manifest_path,
                            curve_sheets=list(curve_rows), affected_loads=list(report["affected_loads"]),
                            sha256=manifest["output"]["sha256"])
