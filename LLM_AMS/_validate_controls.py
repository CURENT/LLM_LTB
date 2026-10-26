"""Run with the llm-ams Python: python _validate_controls.py."""

import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from agent.ams_engine.engine import AMSContext
from web.backend.parameter_controls import (
    ControlPlan, apply_control, apply_controls, load_control_config,
    parameter_payload, parse_prompt,
)


def context(case="pjm5bus_demo"):
    ctx = AMSContext()
    ctx.load_case(case)
    ctx.set_routine("DCOPF")
    return ctx


class ControlsTest(unittest.TestCase):
    def test_pq_device_validation_and_percentage(self):
        ctx = context("ieee14")
        loads = parameter_payload(ctx)["options"]["pq_loads"]
        self.assertIn("PQ_3", [r["idx"] for r in loads])
        self.assertIn("PQ_1", [r["idx"] for r in loads])
        before = ctx.system.PQ.p0.v.copy()
        self.assertFalse(apply_control(ctx, "scale_pq_load", {"load_idx": "PV_1", "percent_delta": 10}).ok)
        for pct in (-20, -10, -1, 1, 10, 20):
            current = ctx.system.PQ.get(src="p0", idx="PQ_3", attr="v")
            result = apply_control(ctx, "scale_pq_load", {"load_idx": "PQ_3", "percent_delta": pct})
            self.assertTrue(result.ok, result.message)
            self.assertAlmostEqual(ctx.system.PQ.get(src="p0", idx="PQ_3", attr="v"), current * (1 + pct / 100))
        self.assertEqual(ctx.system.PQ.p0.v[0], before[0])
        self.assertEqual(parameter_payload(ctx)["values"]["scale_pq_load"]["percent_delta"], 20)
        self.assertFalse(apply_control(ctx, "scale_pq_load", {"load_idx": "PQ_3", "percent_delta": .2}).ok)
        demo = context()
        self.assertEqual([r["idx"] for r in parameter_payload(demo)["options"]["pq_loads"]], ["PQ_1", "PQ_2", "PQ_3"])
        for idx in ("PQ_1", "PQ_2", "PQ_3"):
            before = demo.system.PQ.get(src="p0", idx=idx, attr="v")
            reactive = demo.system.PQ.get(src="q0", idx=idx, attr="v")
            result = apply_control(demo, "scale_pq_load", {"load_idx": idx, "percent_delta": 10})
            self.assertTrue(result.ok, result.message)
            self.assertAlmostEqual(demo.system.PQ.get(src="p0", idx=idx, attr="v"), before * 1.1)
            self.assertEqual(demo.system.PQ.get(src="q0", idx=idx, attr="v"), reactive)
        self.assertTrue(demo.solve()["converged"])
        self.assertTrue(apply_control(demo, "scale_pq_load", {"load_idx": "PQ_1", "percent_delta": 50}).ok)
        self.assertFalse(apply_control(demo, "scale_pq_load", {"load_idx": "PQ_1", "percent_delta": -101}).ok)

    def test_atomic_failure_and_disabled_catalog(self):
        ctx = context()
        original = ctx.system
        actions = [{"operation": "set_line_status", "parameters": {"line_idx": "Line_1", "online": False}},
                   {"operation": "set_line_rate", "parameters": {"line_idx": "Line_2", "rate_a": -1}}]
        self.assertFalse(apply_controls(ctx, actions).ok)
        self.assertIs(ctx.system, original)

        cfg = load_control_config()
        next(c for c in cfg["controls"] if c["id"] == "set_line_status")["enabled"] = False
        with patch("web.backend.parameter_controls.load_control_config", return_value=cfg):
            self.assertFalse(apply_controls(ctx, actions[:1]).ok)
        for value in (float("nan"), float("inf"), -1):
            self.assertFalse(apply_control(ctx, "set_line_rate", {"line_idx": "Line_1", "rate_a": value}).ok)
        self.assertFalse(apply_control(ctx, "set_line_status", {"line_idx": "1", "online": False}).ok)
        self.assertFalse(apply_control(ctx, "set_line_status", {"line_idx": "Line_1", "online": "false"}).ok)

    def test_llm_percentage_units_are_checked(self):
        ctx = context("ieee14")
        llm = Mock()
        llm.with_structured_output.return_value.invoke.return_value = ControlPlan(
            intent="update", actions=[{"operation": "scale_pq_load", "parameters": {"load_idx": "PQ_3", "percent_delta": .2}}])
        original = ctx.system
        self.assertFalse(parse_prompt(ctx, "Increase the load at bus 4 by 20%", llm).ok)
        self.assertIs(ctx.system, original)

    def test_topology_rebuild_and_restore_after_solve(self):
        ctx = context()
        self.assertTrue(ctx.solve()["converged"])
        before = ctx.active_routine().Bbus.v.toarray().copy()
        result = apply_control(ctx, "set_line_status", {"line_idx": "Line_1", "online": False})
        self.assertTrue(result.ok, result.message)
        self.assertTrue(ctx.solve()["converged"])
        self.assertFalse((ctx.active_routine().Bbus.v.toarray() == before).all())
        self.assertEqual(parameter_payload(ctx)["options"]["lines"][0]["u"], 0)
        self.assertTrue(apply_control(ctx, "set_line_status", {"line_idx": "Line_1", "online": True}).ok)
        self.assertTrue(ctx.solve()["converged"])

    def test_objective_and_added_constraint_are_solved(self):
        ctx = context()
        baseline = ctx.solve()["objective"]
        for coefficient in (5, 2):
            result = apply_control(ctx, "add_dispatch_cost", {"coefficient": coefficient})
            self.assertTrue(result.ok, result.message)
            solution = ctx.solve()
            self.assertTrue(solution["converged"])
            self.assertAlmostEqual(solution["objective"], baseline + coefficient * sum(solution["pg"]), places=5)
        self.assertTrue(apply_control(ctx, "set_total_generation_cap", {"limit": 15}).ok)
        self.assertTrue(ctx.solve()["converged"])
        self.assertTrue(apply_control(ctx, "set_total_generation_cap", {"limit": 1}).ok)
        self.assertFalse(ctx.solve()["converged"])
        result = apply_control(ctx, "set_constraint_status", {"constraint": "configured_total_generation", "enabled": False})
        self.assertTrue(result.ok, result.message)
        self.assertTrue(ctx.solve()["converged"])

    def test_routine_specific_control_catalog(self):
        ctx = context()
        dcopf = {c["id"] for c in parameter_payload(ctx)["available_controls"]}
        self.assertIn("add_dispatch_cost", dcopf)
        self.assertNotIn("set_config_t", dcopf)

        ctx.set_routine("ED")
        ed = {c["id"] for c in parameter_payload(ctx)["available_controls"]}
        self.assertIn("set_config_t", ed)
        self.assertNotIn("add_dispatch_cost", ed)

        ctx.set_routine("DCPF")
        dcpf = {c["id"] for c in parameter_payload(ctx)["available_controls"]}
        self.assertNotIn("set_config_t", dcpf)
        self.assertNotIn("add_dispatch_cost", dcpf)
        self.assertFalse(apply_control(ctx, "set_config_t", {"t": 1}).ok)

    def test_ibr_template(self):
        ctx = context()
        result = apply_control(ctx, "replace_generator_with_ibr", {
            "generator_idx": "PV_1", "available_power": 1, "dispatch_cost": 1, "curtailment_cost": 20})
        self.assertTrue(result.ok, result.message)
        self.assertTrue(ctx.solve()["verification"]["passed"])

    def test_chat_uses_catalog_and_manual_state(self):
        from web.backend import main
        ctx = context("ieee14")
        llm = Mock()
        llm.with_structured_output.return_value.invoke.return_value = ControlPlan(
            intent="update", actions=[{"operation": "scale_pq_load", "parameters": {"load_idx": "PQ_3", "percent_delta": 10}}])
        with patch.object(main, "_ctx", ctx), patch.object(main, "_state", {"alias": "ieee14"}), \
             patch.object(main, "_agent", {"llm": llm}), patch.object(main, "_ensure_agent", return_value=None):
            client = TestClient(main.app)
            response = client.post("/api/chat", json={"message": "Increase the load at bus 4 by 10%", "case": "ieee14", "routine": "DCOPF"})
            self.assertTrue(response.json()["ok"], response.text)
            payload = client.get("/api/parameters", params={"case": "ieee14", "routine": "DCOPF"}).json()
            self.assertEqual(payload["values"]["scale_pq_load"]["percent_delta"], 10)
            self.assertAlmostEqual(next(r["p0"] for r in payload["options"]["pq_loads"] if r["idx"] == "PQ_3"), .478 * 1.1)
            response = client.post("/api/parameters/reset", json={"case": "ieee14", "routine": "DCOPF"})
            self.assertEqual(response.json()["parameters"]["values"], {})
        prompt = llm.with_structured_output.return_value.invoke.call_args.args[0][0][1]
        self.assertIn("scale_pq_load", prompt)
        self.assertIn("PQ_3", prompt)

    def test_questions_unsupported_and_llm_failure_never_mutate(self):
        ctx = context()
        original = ctx.system
        llm = Mock()
        for intent in ("chat", "unsupported"):
            llm.with_structured_output.return_value.invoke.return_value = ControlPlan(intent=intent)
            self.assertFalse(parse_prompt(ctx, "What is line 1?", llm).ok)
            self.assertIs(ctx.system, original)
        llm.with_structured_output.return_value.invoke.side_effect = RuntimeError("offline")
        self.assertFalse(parse_prompt(ctx, "trip line 1", llm).ok)
        self.assertIs(ctx.system, original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
