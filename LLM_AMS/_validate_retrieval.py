r"""Permutation-based validation of case + routine retrieval.

Run:  .\.venv\Scripts\python.exe _validate_retrieval.py

Exercises the deterministic resolvers (no LLM) across many phrasings/templates
to prove that every shipped AMS case is retrievable and every routine is
identifiable. Prints a PASS/FAIL report and exits non-zero on any failure.
"""

import sys

# The console may be cp1252 (Windows); the test phrasings include Chinese.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from agent.ams_engine.case_catalog import (
    CASES, ALIAS_INDEX, resolve_case,
)
from agent.ams_engine.routines import resolve_routine
from ams.routines import class_names


def kpath(key: str) -> str:
    return ALIAS_INDEX[key].path


# ---------------------------------------------------------------- case data
# expected_key -> phrasings that must resolve to that key's file (res.path).
# A phrasing prefixed with "!" must additionally be an unambiguous `single`.
CASE_EXPECT = {
    # 5-bus family (default = pjm5bus)
    "pjm5bus": ["5 bus", "5bus", "5", "pjm", "pjm case", "pjm5bus",
                "5 bus system", "change to 5 bus system", "pjm 5 bus",
                "load the pjm case"],
    "pjm5bus_ev": ["!ev", "!pjm5bus ev", "!5 bus ev", "!5bus ev", "!pjm ev"],
    "pjm5bus_jumper": ["!jumper", "!pjm5bus jumper", "!5 bus jumper"],
    "pjm5bus_json": ["!pjm5bus json", "!pjm json", "!5 bus json"],

    # IEEE 14-bus family (default = ieee14 / json)
    "ieee14": ["ieee14", "ieee 14", "14", "14 bus", "ieee14 case", "ieee-14"],
    "ieee14_conn": ["!conn", "!ieee14 conn", "!ieee 14 connectivity",
                    "!ieee14 conn case"],
    "ieee14_uced": ["!ieee14 uced", "!ieee 14 uced", "!ieee14 for uc",
                    "!ieee14 unit commitment"],
    "ieee14_raw": ["!ieee14 raw", "!raw", "!ieee 14 psse", "!ieee14 raw file"],

    # IEEE 39-bus family (default = ieee39)
    "ieee39": ["ieee39", "ieee 39", "39", "39 bus", "ieee-39", "switch to ieee39"],
    "ieee39_uced": ["ieee39 uced", "ieee 39 uced", "ieee39 uc"],
    "ieee39_uced_esd1": ["!esd1", "!ieee39 esd1", "!ieee39 storage",
                         "!ieee39 battery", "!ieee39 uced esd1"],
    "ieee39_uced_pvd1": ["!pvd1", "!ieee39 pvd1", "!ieee39 solar", "!ieee39 pv"],
    "ieee39_uced_vis": ["!vis", "!ieee39 vis", "!ieee39 uced vis"],

    # IEEE 123-bus family (default = ieee123)
    "ieee123": ["ieee123", "123", "123 bus", "ieee 123"],
    "ieee123_regcv1": ["!regcv1", "!ieee123 regcv1", "!123 regcv1"],

    # Hawaii 40-bus
    "hawaii40": ["!hawaii", "!40", "!hawaii40", "!oahu", "!40 bus", "!hawaii 40"],

    # MATPOWER standalone cases
    "case5": ["!case5", "!matpower 5", "!matpower case5"],
    "case14": ["!case14", "!matpower 14", "!matpower case14"],
    "case39": ["!case39", "!matpower 39", "!matpower case39"],
    "case118": ["!case118", "!118", "!118 bus", "!matpower 118", "!ieee118"],
    "case300": ["!case300", "!300", "!300 bus", "!matpower 300"],
    "case2000": ["!case2000", "!2000", "!activsg", "!activsg2000", "!texas",
                 "!2000 bus", "!synthetic"],

    # Regional models (name-keyed)
    "npcc": ["npcc", "npcc case", "northeast"],
    "npcc_uced": ["!npcc uced", "!npcc uc"],
    "wecc": ["wecc", "wecc case", "western"],
    "wecc_uced": ["!wecc uced", "!wecc uc"],

    # PGLib benchmark (number-gated off; name only)
    "pglib_case39": ["!pglib", "!epri", "!api", "!pglib39"],
}

TEMPLATES = [
    "{x}", "change to {x}", "load {x}", "switch to {x}", "use {x}",
    "load the {x} case", "switch case to {x}", "run on {x}",
]


def validate_cases():
    failures = []
    checked = 0
    for key, phrasings in CASE_EXPECT.items():
        want = kpath(key)
        for p in phrasings:
            must_single = p.startswith("!")
            base = p[1:] if must_single else p
            for tmpl in TEMPLATES:
                q = tmpl.format(x=base)
                res = resolve_case(q)
                checked += 1
                if res.path != want:
                    failures.append(
                        f"CASE  {q!r:42} -> {res.path} (status={res.status}); want {want}")
                elif must_single and res.status != "single":
                    failures.append(
                        f"CASE  {q!r:42} -> ambiguous but should be single "
                        f"(default {res.entry.key if res.entry else '?'})")
    return checked, failures


def validate_case_roundtrip():
    """Every catalog key and sub-path must resolve back to its own file."""
    failures = []
    for e in CASES:
        for q in (e.key, e.path):
            res = resolve_case(q)
            if res.path != e.path:
                failures.append(f"ROUNDTRIP {q!r} -> {res.path}; want {e.path}")
    return failures


def validate_paths_exist():
    """Every catalog sub-path must resolve on disk via ams.get_case."""
    import os
    import ams
    failures = []
    for e in CASES:
        try:
            p = ams.get_case(e.path)
            if not os.path.exists(p):
                failures.append(f"MISSING {e.key}: {e.path}")
        except Exception as exc:
            failures.append(f"GETCASE {e.key}: {e.path} -> {exc}")
    return failures


# ---------------------------------------------------------------- routine data
ROUTINE_EXPECT = {
    "DCPF": ["DCPF", "dcpf", "dc power flow", "dc pf", "DC-PF"],
    "PFlow": ["PFlow", "pflow", "power flow", "pf", "ac power flow"],
    "ACOPF": ["ACOPF", "acopf", "ac opf", "ac optimal power flow", "AC-OPF"],
    "DCOPF": ["DCOPF", "dcopf", "dc opf", "dc optimal power flow",
              "opf", "optimal power flow"],
    "RTED": ["RTED", "rted", "real-time economic dispatch",
             "real time economic dispatch", "rt economic dispatch"],
    "ED": ["ED", "ed", "economic dispatch", "econ dispatch"],
    "UC": ["UC", "uc", "unit commitment", "commitment"],
    "DOPF": ["DOPF", "dopf", "distribution opf", "distributional opf"],
    # versioned / decorated variants
    "RTED2": ["RTED2", "rted2", "rted 2", "rted v2"],
    "RTEDDG": ["RTEDDG", "rted dg", "rted with distributed generation"],
    "RTEDES": ["RTEDES", "rted es", "rted with storage", "rted with energy storage"],
    "RTEDESP": ["RTEDESP", "rted esp"],
    "RTEDVIS": ["RTEDVIS", "rted vis", "rted visualization"],
    "RTED2ES": ["RTED2ES", "rted2 es", "rted 2 with storage"],
    "ED2": ["ED2", "ed2", "ed 2", "economic dispatch 2"],
    "EDDG": ["EDDG", "ed dg", "economic dispatch with dg"],
    "EDES": ["EDES", "ed es", "ed with storage"],
    "UC2": ["UC2", "uc2", "uc 2", "unit commitment 2"],
    "UCDG": ["UCDG", "uc dg", "unit commitment with dg"],
    "UCES": ["UCES", "uc es", "uc with storage", "unit commitment with storage"],
    "UC2ES": ["UC2ES", "uc2 es", "uc 2 with storage"],
    "DOPFVIS": ["DOPFVIS", "dopf vis", "dopf visualization"],
    # pypower + grbopt
    "DCPF1": ["DCPF1", "dcpf1"],
    "PFlow1": ["PFlow1", "pflow1"],
    "DCOPF1": ["DCOPF1", "dcopf1"],
    "ACOPF1": ["ACOPF1", "acopf1"],
    "OPF": ["grbopt", "gurobi opf"],
}


def validate_routines():
    failures = []
    checked = 0
    for want, phrasings in ROUTINE_EXPECT.items():
        for p in phrasings:
            res = resolve_routine(p)
            checked += 1
            if res.name != want:
                failures.append(
                    f"ROUTINE {p!r:38} -> {res.name} (status={res.status}); want {want}")
    # every exact class name must resolve to itself, EXCEPT the gurobi "OPF":
    # bare "opf" is intentionally steered to DCOPF (the usual intent); the
    # gurobi routine stays reachable via "grbopt"/"gurobi opf" (checked above).
    for c in class_names:
        res = resolve_routine(c)
        checked += 1
        if c == "OPF":
            if res.name != "DCOPF":
                failures.append(f"ROUTINE exact 'OPF' -> {res.name}; want DCOPF (steered)")
            continue
        if res.name != c:
            failures.append(f"ROUTINE exact {c!r} -> {res.name}; want {c}")
    return checked, failures


def main():
    print("=" * 70)
    print("CASE RETRIEVAL VALIDATION")
    print("=" * 70)
    c_checked, c_fail = validate_cases()
    rt_fail = validate_case_roundtrip()
    print(f"phrasing×template checks: {c_checked}")
    print(f"roundtrip checks:         {len(CASES) * 2}")

    print("\n" + "=" * 70)
    print("ROUTINE RETRIEVAL VALIDATION")
    print("=" * 70)
    r_checked, r_fail = validate_routines()
    print(f"routine checks:           {r_checked}")

    print("\n" + "=" * 70)
    print("CASE FILES EXIST ON DISK (ams.get_case)")
    print("=" * 70)
    p_fail = validate_paths_exist()
    print(f"path existence checks:    {len(CASES)}")

    all_fail = c_fail + rt_fail + r_fail + p_fail
    print("\n" + "=" * 70)
    if all_fail:
        print(f"FAILURES: {len(all_fail)}")
        for f in all_fail:
            print("  " + f)
        print("=" * 70)
        return 1
    print("ALL CHECKS PASSED")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
