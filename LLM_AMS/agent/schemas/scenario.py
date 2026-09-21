"""Scenario spec, run record and comparison invariants for multi-period load studies.

The LLM fills :class:`ScenarioSpec`; deterministic Python
(``agent/ams_engine/case_writer.py``) validates it against the loaded base case and
writes one xlsx per scenario. Nothing here imports ams: this module is pure data
validation and is safe to hand to the LLM's structured-output layer.

Load methods
------------
- ``uniform``  — every Area's ``EDSlotLoad.sd`` / ``UCSlotLoad.sd`` row is multiplied.
- ``regional`` — only the target Area's ``sd`` rows are multiplied.
- ``bus_perturbation`` — the loads on the target bus get their own per-slot factor,
  written as an extra ``EDSlotPQ`` / ``UCSlotPQ`` sheet (a repo-private convention;
  see ``AMSContext.attach_pq_curves``). No Area is created or moved.

``bus_perturbation`` does **not** require reserves to be disabled: stage-1 test (d)
(``test_files/test_pq_curves.py::test_d_reserves_on_dud_per_area_equals_base``) shows
that with the curve attached ``pdz``, ``dud``, ``ddd`` and the ``gs`` pooling matrix are
array-equal to the base case's, because no bus changes Area.
"""

from __future__ import annotations

import math
from typing import List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import Literal

LoadMethod = Literal["uniform", "regional", "bus_perturbation"]


class ScenarioSpec(BaseModel):
    """One load scenario over the routine's full horizon."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, pattern=r"^[A-Za-z0-9_.-]+$",
                    description="Scenario slug; becomes the xlsx / manifest file stem")
    base_case: str = Field("5bus/pjm5bus_demo.xlsx", description="Case catalog key or absolute path")
    routine: str = Field("ED", description="Routine the scenario is solved with (ED, EDES, UC, UCES, ...)")
    solver: str = "CLARABEL"

    horizon_slots: int = Field(..., ge=1, description="Slots the routine solves; verified against the case")
    method: LoadMethod
    target: Optional[Union[int, str]] = Field(
        None, description="Area idx for 'regional', Bus idx for 'bus_perturbation'; omit for 'uniform'")
    factor: Optional[float] = Field(None, gt=0, allow_inf_nan=False,
                                    description="Constant multiplier applied in every slot")
    curve: Optional[List[float]] = Field(
        None, description="Per-slot multipliers, length == horizon_slots (1 + delta(t)); all > 0")

    # cvxpy 1.9.2 DPP canonicalisation fails above 160 slots; ignoring DPP gives identical
    # results (160-slot check: obj 2883.191876 both ways). Recorded on every result.
    ignore_dpp: bool = True
    # Applied through AMSContext.disable_constraints() — the configure node's single path.
    disabled_constraints: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "ScenarioSpec":
        if (self.factor is None) == (self.curve is None):
            raise ValueError("give exactly one of 'factor' or 'curve'")
        if self.curve is not None:
            if len(self.curve) != self.horizon_slots:
                raise ValueError(f"curve has {len(self.curve)} values but horizon_slots={self.horizon_slots}")
            if any(not (math.isfinite(v) and v > 0) for v in self.curve):
                raise ValueError("curve values must be finite and > 0 (multipliers on load)")
        if self.method == "uniform" and self.target is not None:
            raise ValueError("method='uniform' takes no target")
        if self.method in ("regional", "bus_perturbation") and self.target is None:
            raise ValueError(f"method={self.method!r} requires a target (Area idx or Bus idx)")
        return self

    def multipliers(self) -> List[float]:
        """Per-slot multipliers, expanding a constant factor over the horizon."""
        return list(self.curve) if self.curve is not None else [float(self.factor)] * self.horizon_slots


class RunRecord(BaseModel):
    """Result artifact of one solve, built from ``AMSContext.solve()`` output.

    ``disabled_constraints`` (not a binary reserves flag), ``ams_version``, ``pq_curves`` and
    ``ignore_dpp`` say what produced the ``pi`` table stored next to them.
    """

    model_config = ConfigDict(extra="forbid")

    label: str
    case_path: str
    routine: str
    solver: str
    status: str
    converged: bool
    objective: Optional[float]

    ams_version: str
    ignore_dpp: bool
    disabled_constraints: List[str]
    pq_curves: List[str]
    pq_curve_sheet: Optional[str] = None      # EDSlotPQ / UCSlotPQ when pq_curves is non-empty
    horizon_slots: int

    # (n_gen, n_slot), (n_bus, n_slot), (n_load, n_slot); single-period -> n_slot = 1
    pg: List[List[float]]
    pi: List[List[float]]
    pds: List[List[float]]
    gen_idx: List[str]
    bus_idx: List[Union[int, str]]
    load_idx: List[str]
    slot_idx: List[str]

    @classmethod
    def from_solve(cls, res: dict, label: str, case_path: str) -> "RunRecord":
        def as_2d(a):
            import numpy as np
            arr = np.asarray(a, dtype=float)
            return (arr.reshape(-1, 1) if arr.ndim == 1 else arr).tolist()
        slots = list(res.get("slot_idx", []))
        return cls(
            label=label, case_path=str(case_path), routine=res["routine"], solver=res["solver"],
            status=res.get("status", ""), converged=bool(res["converged"]), objective=res.get("objective"),
            ams_version=res["ams_version"], ignore_dpp=bool(res["ignore_dpp"]),
            disabled_constraints=list(res["disabled_constraints"]), pq_curves=list(res["pq_curves"]),
            pq_curve_sheet=res.get("pq_curve_sheet"),
            horizon_slots=len(slots) or 1,
            pg=as_2d(res.get("pg", [])), pi=as_2d(res.get("pi", [])),
            pds=as_2d(res["pds"] if "pds" in res else res.get("pd", [])),
            gen_idx=[str(g) for g in res["gen_idx"]], bus_idx=list(res["bus_idx"]),
            load_idx=[str(i) for i in res["load_idx"]], slot_idx=[str(s) for s in slots],
        )


class ComparisonResult(BaseModel):
    """Base vs scenario. Both records must have been produced under the same regime.

    ``pq_curves`` may differ — the curve *is* the scenario — but the base must not carry a
    curve of its own: a base xlsx left over from an earlier scenario write would make this a
    curve-vs-curve comparison that passes every other check. Curve-vs-curve is allowed only
    with the explicit ``allow_base_curves=True``.
    """

    model_config = ConfigDict(extra="forbid")

    base: RunRecord
    scenario: RunRecord
    allow_base_curves: bool = False

    @model_validator(mode="after")
    def _same_regime(self) -> "ComparisonResult":
        for f in ("disabled_constraints", "ignore_dpp", "ams_version", "routine", "solver", "horizon_slots"):
            b, s = getattr(self.base, f), getattr(self.scenario, f)
            if b != s:
                raise ValueError(f"base and scenario differ in {f}: {b!r} vs {s!r}; not comparable")
        # The base must be curve-free regardless of the scenario's method: a curve-bearing base
        # against a uniform/regional scenario is just as much a different load regime.
        if self.base.pq_curves and not self.allow_base_curves:
            raise ValueError(
                f"base record {self.base.label!r} ({self.base.case_path}) already carries a per-load "
                f"curve sheet {self.base.pq_curve_sheet!r} affecting loads {self.base.pq_curves}; "
                f"comparing it against scenario {self.scenario.label!r} (curves on "
                f"{self.scenario.pq_curves or 'none'}) would mix load regimes. Use a clean base case, "
                f"or pass allow_base_curves=True if that is intended."
            )
        return self
