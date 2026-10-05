"""Build a multi-period (e.g. 168-hour) base case from a base xlsx and a load profile.

Plan §4.3 steps 1, 4, 6, 7 (docs/multi_period_load_scenario_plan.md): the canonical study
input is ``base case + profile.csv``; this module turns them into ONE xlsx whose slot tables
carry the whole horizon. Scenarios (``case_writer``) are then written on top of that file.

profile.csv
    columns ``hour`` (1..N, contiguous), optional ``timestamp``, and one column per PQ load
    (``PQ_1, PQ_2, ...``) in MW (or p.u. / factor, see ``unit``). Every PQ of the base case
    must have a column.

Mapping onto AMS tables (see ``ams/core/service.py`` LoadScale: ``pds[i,t] = sd[area(i),t] * p0_i``)
    factor_i(t) = MW_i(t) / (p0_i * mva)
    sd[a, t]    = sum_{i in a} MW_i(t) / (mva * sum_{i in a} p0_i)       (area shape, load-weighted)
    r_i(t)      = factor_i(t) / sd[area(i), t]                          (per-load residual)
The area shapes become ``EDSlotLoad``/``UCSlotLoad`` rows (native). If any residual differs
from 1 by more than ``residual_tol`` the residuals are written as ``EDSlotPQ``/``UCSlotPQ``
rows (the repo-private per-load sheets; see ``AMSContext.attach_pq_curves``). Either way the
solver sees exactly ``MW_i(t) / mva`` per load and slot — verified by reloading the written
file (step 7).

Slot tables are REBUILT (not appended): ``System.add`` cannot delete the shipped slots, so the
sheets are rewritten with pandas. ``EDSlot.name`` carries the profile timestamp.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import ams
import numpy as np
import pandas as pd

from agent.ams_engine.engine import PQ_CURVE_SHEETS, AMSContext, resolve_case_path

SLOT_PREFIX = {"EDSlot": "EDT", "UCSlot": "UCT"}
SLOT_LOAD = {"EDSlot": "EDSlotLoad", "UCSlot": "UCSlotLoad"}
SLOT_GEN = {"EDSlot": "EDSlotGen"}

HORIZON_NOTE = (
    "Slot tables (EDSlot/EDSlotLoad/UCSlot/UCSlotLoad) were REBUILT from a load profile by "
    "LLM_LTB week_case; EDSlot.name / UCSlot.name carry the profile timestamps."
)


@dataclass
class WeekCaseArtifact:
    xlsx_path: str
    manifest_path: str
    n_slots: int
    curve_sheets: List[str] = field(default_factory=list)   # residual sheets written (if any)
    max_residual: float = 0.0                                # max |r_i(t) - 1| seen
    sha256: str = ""


# --------------------------------------------------------------------------- #
# profile
# --------------------------------------------------------------------------- #
def read_profile(path: str, load_idx: List[str]) -> pd.DataFrame:
    """Read and validate ``profile.csv``; returns it indexed by hour with one column per load."""
    df = pd.read_csv(path)
    if "hour" not in df.columns:
        raise ValueError(f"{path}: missing 'hour' column")
    hours = df["hour"].to_numpy()
    n = len(hours)
    if n == 0 or not np.array_equal(hours, np.arange(1, n + 1)):
        raise ValueError(f"{path}: 'hour' must be 1..N contiguous (got {n} rows, first {hours[:3]})")
    missing = [i for i in load_idx if i not in df.columns]
    extra = [c for c in df.columns if c not in ("hour", "timestamp", *load_idx)]
    if missing or extra:
        raise ValueError(f"{path}: columns must be 'hour', optional 'timestamp', and exactly the base "
                         f"case's PQ idx {load_idx}; missing {missing}, unknown {extra}")
    vals = df[load_idx].to_numpy(dtype=float)
    if not np.isfinite(vals).all():
        raise ValueError(f"{path}: non-finite load values")
    if (vals < 0).any():
        raise ValueError(f"{path}: negative load values (net injection is not supported by sd scaling)")
    return df


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# builder
# --------------------------------------------------------------------------- #
def build_week_case(base_case: str, profile_csv: str, out_dir: str, case_id: Optional[str] = None,
                    unit: str = "MW", residual_tol: float = 1e-5) -> WeekCaseArtifact:
    """Write ``<out_dir>/<case_id>.xlsx`` with N-slot tables built from ``profile_csv``.

    ``unit``: ``"MW"`` (divided by ``p0 * mva``), ``"pu"`` (divided by ``p0``) or ``"factor"``
    (used as is). Raises on any inconsistency; verifies the written file by reloading it.
    """
    base_path = resolve_case_path(base_case)
    ss = ams.load(base_path, setup=True, no_output=True)
    load_idx = [str(i) for i in ss.PQ.idx.v]
    df = read_profile(profile_csv, load_idx)
    n = len(df)
    mva = float(ss.config.mva)
    p0 = np.asarray(ss.PQ.get(src="p0", idx=load_idx, attr="v"), dtype=float)
    if (p0 <= 0).any():
        bad = [i for i, v in zip(load_idx, p0) if v <= 0]
        raise ValueError(f"loads {bad} have p0 <= 0 in the base case; sd scaling cannot represent them")
    raw = df[load_idx].to_numpy(dtype=float)                       # (n, nPQ)
    if unit == "MW":
        factor = raw / (p0 * mva)
    elif unit == "pu":
        factor = raw / p0
    elif unit == "factor":
        factor = raw
    else:
        raise ValueError(f"unit must be MW, pu or factor, got {unit!r}")

    # area shape (load-weighted) + per-load residual
    areas = [str(a) for a in ss.Area.idx.v]
    load_area = [str(a) for a in ss.Bus.get(src="area", idx=list(ss.PQ.bus.v), attr="v")]
    sd = np.ones((len(areas), n))
    for k, a in enumerate(areas):
        members = [j for j, la in enumerate(load_area) if la == a]
        if members:
            sd[k] = (raw[:, members] if unit != "factor" else factor[:, members] * p0[members]).sum(axis=1) \
                / ((p0[members] * mva).sum() if unit == "MW" else p0[members].sum())
    # An area whose loads are all zero in a slot has sd = 0 there; the residual is then 0/0.
    # Use a neutral 1.0 (the demand stays 0 either way) instead of letting NaN reach the
    # manifest / API response.
    area_sd = sd[[areas.index(a) for a in load_area], :].T                     # (n, nPQ)
    resid = np.divide(factor, area_sd, out=np.ones_like(factor), where=area_sd != 0)
    max_resid = float(np.abs(resid - 1).max()) if resid.size else 0.0
    write_residuals = max_resid > residual_tol

    # EDSlotGen: regenerated as all-committed; refuse if the base carried decommitments
    if ss.EDSlotGen.n and not (np.asarray(ss.EDSlotGen.ug.v) == 1).all():
        raise ValueError("base case EDSlotGen has ug=0 entries; the week builder cannot carry them "
                         "across a rebuilt horizon (specify commitment via changes.csv — not implemented)")

    # ---- rebuild the slot sheets with pandas ----
    # The base may be json/m/raw (shipped aliases such as ieee14, case118, npcc): convert it
    # through AMS's own xlsx writer first so the sheet rewrite below has an xlsx to start from.
    os.makedirs(out_dir, exist_ok=True)
    if not str(base_path).lower().endswith(".xlsx"):
        converted = os.path.join(os.path.abspath(out_dir), f"{os.path.splitext(os.path.basename(base_path))[0]}.base.xlsx")
        ams.io.xlsx.write(ss, converted, overwrite=True)
        base_path = converted
    sheets: Dict[str, pd.DataFrame] = pd.read_excel(base_path, sheet_name=None, engine="openpyxl")
    # A base that is itself a built week case carries its own EDSlotPQ/UCSlotPQ and Summary
    # rows; they describe the OLD profile. Drop them so they can neither stay active nor be
    # written twice — residuals for the new profile are recomputed below.
    for sheet in PQ_CURVE_SHEETS:
        sheets.pop(sheet, None)
    if "Summary" in sheets:
        stale_fields = {"Horizon", "Scenario", *PQ_CURVE_SHEETS}
        sheets["Summary"] = sheets["Summary"][~sheets["Summary"]["field"].isin(stale_fields)].reset_index(drop=True)
    ts = [str(x) for x in df["timestamp"]] if "timestamp" in df.columns else [f"h{h}" for h in df["hour"]]
    slot_names: Dict[str, List[str]] = {}
    for slot_model, prefix in SLOT_PREFIX.items():
        if getattr(ss, slot_model).n == 0:
            continue
        names = [f"{prefix}{t + 1}" for t in range(n)]
        slot_names[slot_model] = names
        sheets[slot_model] = pd.DataFrame({"uid": range(n), "idx": names, "name": ts})
        rows = [dict(area=a, slot=names[t], sd=float(sd[k, t])) for t in range(n) for k, a in enumerate(ss.Area.idx.v)]
        sheets[SLOT_LOAD[slot_model]] = pd.DataFrame(rows)
        if slot_model in SLOT_GEN and SLOT_GEN[slot_model] in sheets:
            gens = list(ss.StaticGen.get_all_idxes())
            sheets[SLOT_GEN[slot_model]] = pd.DataFrame(
                [dict(gen=g, slot=names[t], ug=1) for t in range(n) for g in gens])
    if not slot_names:
        raise ValueError(f"{base_path} has no EDSlot/UCSlot rows; nothing to rebuild")

    # Summary rows: visible to whoever opens the file
    summ = sheets["Summary"]
    add = [dict(field="Horizon", comment=f"{n} slots from {os.path.basename(profile_csv)} "
                                            f"(sha256 {_sha256(profile_csv)[:12]}) unit={unit}, "
                                            f"start {ts[0]} (LLM_LTB week_case {datetime.now():%Y-%m-%d})"),
           dict(field="Horizon", comment=HORIZON_NOTE)]
    curve_rows: Dict[str, pd.DataFrame] = {}
    if write_residuals:
        from agent.ams_engine.case_writer import CONVENTION_NOTE
        for sheet, slot_model in PQ_CURVE_SHEETS.items():
            if slot_model in slot_names:
                curve_rows[sheet] = pd.DataFrame(
                    [dict(pq=load_idx[j], slot=slot_names[slot_model][t], sd=float(resid[t, j]))
                     for j in range(len(load_idx)) for t in range(n)])
                add.append(dict(field=sheet, comment=CONVENTION_NOTE))
    sheets["Summary"] = pd.concat([summ, pd.DataFrame(add)], ignore_index=True)
    sheets["Summary"]["uid"] = range(len(sheets["Summary"]))

    case_id = case_id or f"{os.path.splitext(os.path.basename(base_path))[0]}_{n}h"
    out_dir = os.path.abspath(out_dir)          # AMSContext.load_case needs absolute paths
    os.makedirs(out_dir, exist_ok=True)
    xlsx_path = os.path.join(out_dir, f"{case_id}.xlsx")
    manifest_path = os.path.join(out_dir, f"{case_id}.manifest.json")
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as w:
        for name, sh in sheets.items():
            sh.to_excel(w, sheet_name=name, index=False)
        for sheet, sh in curve_rows.items():
            sh.to_excel(w, sheet_name=sheet, index=False)

    # ---- step 7: reload and verify the solver's load matrix equals the profile ----
    # residuals below residual_tol were folded into the area shape, so the file reproduces
    # the profile to that relative tolerance (exactly, when residual sheets were written)
    verify_week_case(xlsx_path, factor, load_idx, slot_names,
                     rtol=(1e-12 if write_residuals else residual_tol))

    manifest = {
        "case_id": case_id, "n_slots": n, "unit": unit,
        "base_case": {"path": base_path, "sha256": _sha256(base_path)},
        "profile": {"path": os.path.abspath(profile_csv), "sha256": _sha256(profile_csv),
                    "start": ts[0], "end": ts[-1]},
        "output": {"path": xlsx_path, "sha256": _sha256(xlsx_path)},
        "written_at": datetime.now().isoformat(timespec="seconds"), "ams_version": ams.__version__,
        "slot_tables": {m: n for m in slot_names}, "area_sd_range": [float(sd.min()), float(sd.max())],
        "per_load_residual": {"max_abs_dev": max_resid, "tol": residual_tol,
                              "curve_sheets": list(curve_rows)},
        "edslotgen": "regenerated, ug=1 for every (gen, slot)" if "EDSlotGen" in sheets else None,
        "solve_note": "pass ignore_dpp=True for > 160 slots (cvxpy 1.9.2 DPP bug)",
    }
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    return WeekCaseArtifact(xlsx_path=xlsx_path, manifest_path=manifest_path, n_slots=n,
                            curve_sheets=list(curve_rows), max_residual=max_resid,
                            sha256=manifest["output"]["sha256"])


def verify_week_case(xlsx_path: str, factor: np.ndarray, load_idx: List[str],
                     slot_names: Dict[str, List[str]], rtol: float = 1e-12, atol: float = 1e-12) -> None:
    """Reload the written file and assert every multi-period routine's ``pds`` equals
    ``factor * p0`` per load and slot (to ``rtol``). Raises ``RuntimeError`` on any mismatch."""
    ctx = AMSContext()
    ctx.load_case(xlsx_path)
    ss = ctx.system
    p0 = np.asarray(ss.PQ.get(src="p0", idx=load_idx, attr="v"), dtype=float)
    expect = (factor * p0).T                                       # (nPQ, n)
    for slot_model, names in slot_names.items():
        if getattr(ss, slot_model).n != len(names):
            raise RuntimeError(f"{xlsx_path}: {slot_model} has {getattr(ss, slot_model).n} rows, expected {len(names)}")
        rtn = ss.ED if slot_model == "EDSlot" else ss.UC
        got = np.asarray(rtn.pds.v, dtype=float)
        if got.shape != expect.shape or not np.allclose(got, expect, rtol=rtol, atol=atol):
            worst = float(np.abs(got - expect).max()) if got.shape == expect.shape else float("nan")
            raise RuntimeError(f"{xlsx_path}: {rtn.class_name}.pds does not reproduce the profile "
                               f"(shape {got.shape} vs {expect.shape}, max abs dev {worst:.3e})")
