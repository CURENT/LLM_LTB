"""Deterministic compiler for bounded optimization-formulation edits.

The LLM selects and parameterizes a reviewed template. It never emits Python,
CVXPY, or AMS expression strings directly.
"""

from dataclasses import asdict, dataclass
import inspect
from typing import Any, Dict

import numpy as np


@dataclass(frozen=True)
class IBRReplacementSpec:
    generator_idx: str
    available_power: float
    dispatch_cost: float = 0.0
    curtailment_cost: float = 0.0

    def validate(self) -> None:
        if self.generator_idx is None or self.generator_idx == "":
            raise ValueError("generator_idx is required")
        if not np.isfinite(self.available_power) or self.available_power <= 0:
            raise ValueError("available_power must be finite and greater than zero")
        for name in ("dispatch_cost", "curtailment_cost"):
            value = getattr(self, name)
            if not np.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")


def describe_ibr_template(spec: IBRReplacementSpec) -> Dict[str, Any]:
    spec.validate()
    return {
        "operation": "replace_generator_with_ibr",
        "generator_idx": spec.generator_idx,
        "device_change": f"disable {spec.generator_idx}; add an IBR at the same bus",
        "variables": ["pibr (IBR active-power dispatch)", "pcurt (curtailment)"],
        "constraints": [
            "0 <= pibr <= available_power",
            "pibr + pcurt = available_power",
            "Cibr @ pibr enters nodal power balance",
        ],
        "objective_terms": ["dispatch_cost * pibr", "curtailment_cost * pcurt"],
        "parameters": asdict(spec),
    }


def _value(obj):
    value = getattr(obj, "v", obj)
    return value.toarray() if hasattr(value, "toarray") else np.asarray(value)


def verify_ibr_solution(routine, report, results, tolerance=1e-6):
    """Recompute physical residuals and each cost term from solved values."""
    pg = np.asarray(results["pg"], dtype=float)
    theta = np.asarray(results["aBus"], dtype=float)
    pibr = np.asarray(results["pibr"], dtype=float)
    pcurt = np.asarray(results["pcurt"], dtype=float)
    spec = report["parameters"]
    residual = (_value(routine.Bbus) @ theta + _value(routine.Pbusinj)
                + _value(routine.Cl) @ _value(routine.pd)
                + _value(routine.Csh) @ _value(routine.gsh)
                - _value(routine.Cg) @ pg - _value(routine.Cibr) @ pibr)
    conventional = float(np.sum(_value(routine.c2) * pg**2
                               + _value(routine.c1) * pg
                               + _value(routine.ug) * _value(routine.c0)))
    dispatch = float(spec["dispatch_cost"] * pibr.sum())
    curtailment = float(spec["curtailment_cost"] * pcurt.sum())
    terms = {"conventional": conventional, "ibr_dispatch": dispatch,
             "curtailment": curtailment}
    errors = {
        "nodal_balance_pu": float(np.max(np.abs(residual))),
        "availability_pu": float(abs(pibr.sum() + pcurt.sum() - spec["available_power"])),
        "old_generator_pu": float(abs(pg[report["target_position"]])),
        "ibr_bounds_pu": float(max(0, -pibr.min(), -pcurt.min(), pibr.max() - spec["available_power"])),
        "objective": float(abs(sum(terms.values()) - results["objective"])),
    }
    return {"passed": all(np.isfinite(v) and v <= tolerance for v in errors.values()),
            "tolerance": tolerance, "residuals": errors, "objective_terms": terms,
            "claim": "independent nodal balance, availability, replacement and objective checks"}


def apply_ibr_replacement(routine, system, spec: IBRReplacementSpec) -> Dict[str, Any]:
    """Compile an IBR replacement into an AMS DCOPF routine and validate it."""
    spec.validate()
    class_name = getattr(routine, "class_name", routine.__class__.__name__)
    if class_name != "DCOPF":
        raise ValueError("IBR replacement supports the nodal DCOPF routine. Select DCOPF first.")
    if hasattr(routine, "pibr") or hasattr(routine, "pcurt"):
        raise ValueError("A structural formulation edit is already active")

    gen_idx = list(system.StaticGen.get_all_idxes())
    if spec.generator_idx not in gen_idx:
        raise ValueError(f"Generator '{spec.generator_idx}' not found. Available: {gen_idx}")
    target = gen_idx.index(spec.generator_idx)
    if not system.StaticGen.get(src="u", idx=spec.generator_idx, attr="v"):
        raise ValueError("The selected generator is already offline")
    routine.init()
    cg = _value(routine.Cg)
    if cg.ndim != 2 or target >= cg.shape[1]:
        raise ValueError("Generator-to-bus connection matrix is incompatible with the selected generator")
    cibr = np.asarray(cg[:, target:target + 1], dtype=float)

    routine.addService("Cibr", cibr, info="IBR-to-bus connection matrix")
    routine.addService("ibr_pavail", np.array([spec.available_power]), unit="p.u.")
    routine.addService("ibr_dispatch_cost", np.array([spec.dispatch_cost]), unit="$/p.u.")
    routine.addService("ibr_curtailment_cost", np.array([spec.curtailment_cost]), unit="$/p.u.")
    routine.addVars("pibr", shape=1, nonneg=True, unit="p.u.", info="IBR active power")
    routine.addVars("pcurt", shape=1, nonneg=True, unit="p.u.", info="IBR curtailment")
    legacy = "is_eq" in inspect.signature(routine.addConstrs).parameters
    if legacy:
        routine.addConstrs("pibrub", "pibr - ibr_pavail", info="IBR available-power limit")
        routine.addConstrs("ibravail", "pibr + pcurt - ibr_pavail", is_eq=True)
        routine.pb.e_str = f"({routine.pb.e_str}) - Cibr@pibr"
        atom = ""
    else:
        routine.addConstrs("pibrub", "pibr - ibr_pavail <= 0")
        routine.addConstrs("ibravail", "pibr + pcurt - ibr_pavail == 0")
        lhs, separator, rhs = routine.pb.e_str.rpartition("==")
        if not separator or rhs.strip() != "0":
            raise ValueError("Unsupported nodal power-balance expression")
        routine.pb.e_str = f"({lhs.strip()}) - Cibr@pibr == 0"
        atom = "cp."
    multiply = "mul" if legacy else "cp.multiply"
    routine.obj.e_str = (
        f"({routine.obj.e_str})"
        f"+ {atom}sum({multiply}(ibr_dispatch_cost, pibr))"
        f"+ {atom}sum({multiply}(ibr_curtailment_cost, pcurt))"
    )
    system.StaticGen.set(src="u", idx=spec.generator_idx, attr="v", value=0)

    routine.init(force=True, force_mats=True, force_om=True)
    problem = getattr(getattr(routine, "om", None), "prob", None)
    if problem is None or not problem.is_dcp():
        raise ValueError("Compiled IBR formulation failed the CVXPY DCP check")
    report = describe_ibr_template(spec)
    report.update({"target_position": target, "dcp": True,
                   "bus_idx": list(system.Bus.idx.v)[int(np.argmax(cibr[:, 0]))],
                   "scope": "single-period active-power dispatch, no dynamic or reactive-power model"})
    return report
