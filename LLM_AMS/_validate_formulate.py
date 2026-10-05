"""Nine live AMS checks. Run: python _validate_formulate.py --json output.json."""
import argparse
import copy
import json
from pathlib import Path
from unittest.mock import patch
import numpy as np
from scipy.optimize import linprog
from agent.ams_engine.engine import AMSContext
from agent.ams_engine.formulation_edit import verify_ibr_solution
from agent.schemas.parameter import FormulationEdit


def context():
    c = AMSContext()
    c.load_case("pjm5bus_demo")
    c.set_routine("DCOPF")
    return c


def edit(c, target="PV_1", power=1, cost=1, penalty=20):
    return c.apply_formulation_edit(FormulationEdit(
        generator_idx=target, available_power=power,
        dispatch_cost=cost, curtailment_cost=penalty))


def oracle(c):
    """Independently assemble an LP with SciPy/HiGHS, not AMS expressions."""
    r = c.active_routine()
    def v(name):
        x = getattr(r, name).v
        return np.asarray(x.toarray() if hasattr(x, "toarray") else x)
    assert np.all(v("c2") == 0), "Linear cost oracle only"
    ng, nb = len(v("c1")), len(c.system.Bus.idx.v)
    width = ng + nb + 2
    costs = np.r_[v("c1"), np.zeros(nb), v("ibr_dispatch_cost"), v("ibr_curtailment_cost")]
    eq = np.zeros((nb + 2, width))
    eq[:nb, :ng] = -v("Cg")
    eq[:nb, ng:ng + nb] = v("Bbus")
    bus = c.system.StaticGen.get(src="bus", idx=c.formulation_edit["generator_idx"], attr="v")
    eq[list(c.system.Bus.idx.v).index(bus), -2] = -1
    eq[nb, -2:] = 1
    eq[nb + 1, ng + list(c.system.Bus.idx.v).index(v("buss")[0])] = 1
    rhs = np.r_[-v("Pbusinj") - v("Cl") @ v("pd") - v("Csh") @ v("gsh"), v("ibr_pavail"), 0]
    flow = np.zeros((len(v("rate_a")), width))
    flow[:, ng:ng + nb] = v("Bf")
    angle = np.zeros_like(flow)
    angle[:, ng:ng + nb] = v("CftT")
    upper = np.r_[v("ul") * v("rate_a") - v("Pfinj"),
                  v("ul") * v("rate_a") + v("Pfinj"), v("amax"), -v("amin")]
    ctrl, u = v("ctrl"), v("ug")
    lo = (1-ctrl)*u*v("pg0") + ctrl*u*v("pmin")
    hi = (1-ctrl)*u*v("pg0") + ctrl*u*v("pmax")
    bounds = list(zip(lo, hi)) + [(None, None)]*nb + [(0, float(v("ibr_pavail")[0])), (0, None)]
    sol = linprog(costs, A_ub=np.vstack([flow, -flow, angle, -angle]), b_ub=upper,
                  A_eq=eq, b_eq=rhs, bounds=bounds, method="highs")
    assert sol.success, sol.message
    return float(sol.fun + np.sum(u*v("c0")))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--json")
    args = p.parse_args()
    checks, evidence = [], {}
    def check(name, fn):
        try:
            fn()
            checks.append({"name": name, "passed": True})
            print("PASS", name)
        except Exception as exc:
            checks.append({"name": name, "passed": False, "error": str(exc)})
            print("FAIL", name, repr(exc))
    c = context()

    def baseline():
        result = c.solve()
        assert result["converged"]
        evidence["baseline_objective"] = result["objective"]
    check("1. Live PJM 5-bus baseline", baseline)

    def replacement():
        edit(c)
        out = c.solve()
        assert c.active_routine().om.prob.is_dcp()
        assert out["converged"] and out["verification"]["passed"]
        assert abs(out["pg"][0]) < 1e-6 and abs(out["pibr"][0]-1) < 1e-6
        evidence["edited_result"] = out
    check("2. Warm IBR replacement and real DCP/residuals", replacement)

    def differential():
        ref = oracle(c)
        evidence["highs_objective"] = ref
        assert abs(ref - evidence["edited_result"]["objective"]) < 1e-6
    check("3. Independent HiGHS formulation agrees", differential)

    def cold():
        fresh = context()
        edit(fresh, target="PV_3")
        out = fresh.solve()
        assert out["converged"] and out["verification"]["passed"]
        assert fresh.formulation_edit["bus_idx"] == fresh.system.StaticGen.get(src="bus", idx="PV_3", attr="v")
        assert abs(oracle(fresh) - out["objective"]) < 1e-6
    check("4. Cold model and alternate device mapping", cold)

    def guards():
        fresh = context()
        original = fresh.system
        for kw in ({"target":"unknown"}, {"power":0}, {"cost":-1}, {"power":float("nan")}):
            try:
                edit(fresh, **kw)
            except (ValueError, TypeError):
                pass
            else:
                raise AssertionError("Invalid input accepted")
            assert fresh.system is original and fresh.formulation_edit is None
    check("5. Invalid inputs leave live system intact", guards)

    def rollback():
        fresh = context()
        original = fresh.system
        with patch("agent.ams_engine.engine.apply_ibr_replacement", side_effect=RuntimeError("build failure")):
            try:
                edit(fresh)
            except RuntimeError:
                pass
        assert fresh.system is original and fresh.solve()["converged"]
        edit(fresh)
        try:
            edit(fresh)
        except ValueError:
            pass
        else:
            raise AssertionError("Duplicate replacement accepted")
        fresh.load_case("pjm5bus_demo")
        assert fresh.formulation_edit is None and not hasattr(fresh.active_routine(), "pibr")
    check("6. Build failure rollback, duplicate guard and reload", rollback)

    def penalties():
        low = context(); edit(low, cost=10, penalty=0)
        a = low.solve()
        high = context(); edit(high, cost=10, penalty=20)
        b = high.solve()
        assert a["converged"] and b["converged"]
        assert a["pcurt"][0] > 0.9 and b["pcurt"][0] < 1e-6
        evidence["curtailment_low_penalty"] = a["pcurt"][0]
        evidence["curtailment_high_penalty"] = b["pcurt"][0]
    check("7. Curtailment responds correctly to penalty", penalties)

    def mutations():
        for key in ("pibr", "pcurt", "pg"):
            bad = copy.deepcopy(evidence["edited_result"])
            bad[key][0] += 0.1
            assert not verify_ibr_solution(c.active_routine(), c.formulation_edit, bad)["passed"]
        bad = copy.deepcopy(evidence["edited_result"])
        bad["objective"] += 1
        assert not verify_ibr_solution(c.active_routine(), c.formulation_edit, bad)["passed"]
    check("8. Verifier catches injected result errors", mutations)

    def web():
        from fastapi.testclient import TestClient
        from web.backend import main as backend
        fresh = context(); edit(fresh)
        with patch.object(backend, "_ctx", fresh), patch.object(backend, "_state", {"alias":"pjm5bus_demo"}):
            client = TestClient(backend.app)
            res = client.post("/api/solve", json={"routine":"DCOPF", "case":"pjm5bus_demo"})
            assert res.status_code == 200, res.text
            data = res.json()
            assert data["verification"]["passed"] and data["pibr"][0] > .99
            assert "IBR replacement" in client.get("/api/formulation/DCOPF").json()["title"]
            evidence["web_result"] = data
    check("9. Web HTTP solve and edited formulation", web)
    passed = sum(x["passed"] for x in checks)
    print(f"\n{passed}/9 LIVE checks passed")
    if args.json:
        Path(args.json).write_text(json.dumps({"checks":checks, "evidence":evidence}, indent=2), encoding="utf-8")
    return 0 if passed == 9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
