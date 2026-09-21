"""Route 6: solve the active routine.

Handles both:
- Single-period routines (DCOPF / RTED / DCPF / ACOPF / DOPF) — 1-D ``pg``/``plf``
- Multi-period routines  (ED / UC / *2* variants)              — 2-D ``pg``/``plf``

Plot failures are isolated so the user always sees the objective + values.
"""

from datetime import datetime
from typing import Iterable, List, Optional
import re

import numpy as np
from langchain_core.messages import AIMessage

from agent.ams_engine.constraint_check import check_constraints
from agent.ams_engine.plotting import plot_results
from agent.ams_engine.routines import all_routine_names
from agent.schemas.response import NodeResponse
from agent.state.app_state import State
from agent.utils.display import display_constraint_check, display_executing_node


def _parse_routine(text: str) -> Optional[str]:
    """Return a routine name explicitly requested in ``text`` (e.g. "run UC",
    "运行uc问题"), or ``None`` if none is named.

    Matches known AMS routine names, longest first (so ``RTED2`` wins over
    ``RTED``). Boundaries exclude ASCII letters/digits only, so a name embedded
    in CJK text ("运行uc问题") still matches while substrings inside larger
    English words ("reduce" → not "uc") do not.
    """
    if not text:
        return None
    low = text.lower()
    for name in sorted(all_routine_names(), key=len, reverse=True):
        if re.search(rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])", low):
            return name
    return None


def _fmt_1d(arr, labels: Iterable[str], fmt: str = "{:.4f}") -> List[str]:
    out = []
    labels = list(labels)
    for i, v in enumerate(arr):
        lbl = labels[i] if i < len(labels) else str(i)
        out.append(f"  - {lbl}: {fmt.format(float(v))}")
    return out


def _fmt_2d(arr2d, labels: Iterable[str]) -> List[str]:
    """Multi-period summary: per-device min / mean / max over time."""
    out = []
    labels = list(labels)
    n_dev, n_t = arr2d.shape
    for i in range(n_dev):
        lbl = labels[i] if i < len(labels) else str(i)
        row = arr2d[i]
        out.append(
            f"  - {lbl}: min {row.min():+.4f}  mean {row.mean():+.4f}  "
            f"max {row.max():+.4f}   ({n_t} periods)"
        )
    return out


def _format_var(name: str, raw, labels: Iterable[str], max_rows_1d: int = 24):
    """Return a list of markdown bullet lines summarizing one result array.

    Returns an empty list if the array is missing, empty, or too big for the
    1-D path (>= max_rows_1d for plf to keep the message short).
    """
    if raw is None:
        return []
    try:
        arr = np.asarray(raw, dtype=float)
    except (TypeError, ValueError):
        return []
    if arr.size == 0:
        return []

    if arr.ndim == 1:
        if name == "plf" and arr.size > max_rows_1d:
            return [
                f"- {name} (pu): {arr.size} lines, "
                f"min {arr.min():+.4f}, max {arr.max():+.4f}  "
                "(per-line listing suppressed; see plot)"
            ]
        return [f"- {name} (pu):"] + _fmt_1d(arr, labels)

    if arr.ndim == 2:
        return [f"- {name} (pu, per device · summary over time):"] + _fmt_2d(arr, labels)

    return [f"- {name}: shape {arr.shape}  (unsupported dim)"]


def solve_agent(state: State, llm, prompts, ams_ctx):
    display_executing_node("solve")

    inputs = state["inputs"]
    last_message = state["messages"][-1] if state.get("messages") else None
    user_text = getattr(last_message, "content", "") if last_message else ""

    if ams_ctx.system is None:
        msg = "No case is loaded. Try: 'load case 5bus/pjm5bus_demo.xlsx'."
        reply = AIMessage(content=msg)
        nr = NodeResponse(node_type="solve", success=False, data={},
                          message=msg, timestamp=datetime.now())
        return {"messages": [reply], "node_response": nr}

    # If the user named a routine in this message (e.g. "run UC", "运行uc问题"),
    # honor it; otherwise fall back to the active routine from inputs.
    requested_routine = _parse_routine(user_text)
    routine_name = requested_routine or inputs.routine
    new_inputs = (inputs.model_copy(update={"routine": routine_name})
                  if requested_routine and requested_routine != inputs.routine
                  else None)

    # --- run the routine ---
    try:
        ams_ctx.set_routine(routine_name)
        # ignore_dpp for multi-period routines: cvxpy 1.9.2 DPP fails above 160 slots
        results = ams_ctx.solve(solver=inputs.solver,
                                ignore_dpp=hasattr(ams_ctx.active_routine(), "timeslot"))
    except Exception as exc:
        return {"error_info": {
            "error_type": "solve_error",
            "error_message": str(exc),
            "current_inputs": inputs.model_dump(),
        }, "failed_node": "solve"}

    # --- plot (isolated; never fail the turn) ---
    plot_warning = None
    try:
        plots = plot_results(results)
    except Exception as exc:
        plots = {}
        plot_warning = f"plotting skipped: {exc}"
    for k, path in plots.items():
        results[f"{k}_plot_path"] = path

    # --- constraint / violation check ---
    try:
        violations = check_constraints(ams_ctx, results)
        if violations:
            display_constraint_check(violations)
        results["violations"] = violations
    except Exception as exc:
        results["violations"] = []

    # --- compose markdown response ---
    lines = [
        f"**{results['routine']}** solved with **{results['solver']}** "
        f"(exit_code={results['exit_code']}, converged={results['converged']}).",
    ]
    if results.get("objective") is not None:
        lines.append(f"- Objective: `{results['objective']:.6f}`")

    lines += _format_var("pg",  results.get("pg"),  results.get("gen_idx", []))
    lines += _format_var("plf", results.get("plf"), results.get("line_idx", []))
    lines += _format_var("pd",  results.get("pd"),  results.get("load_idx", []))

    for k in ("pg", "plf", "pd"):
        p = plots.get(k)
        if p:
            lines.append(f"- {k} plot: `{p}`")
    if plot_warning:
        lines.append(f"- ⚠ {plot_warning}")

    content = "\n".join(lines)
    reply = AIMessage(content=content)
    nr = NodeResponse(
        node_type="solve",
        success=True,
        data={"routine": results["routine"], "solver": results["solver"],
              "objective": results.get("objective"), "converged": results.get("converged")},
        message=content,
        timestamp=datetime.now(),
        metadata={"plots": plots},
    )
    out = {"messages": [reply], "results": results, "node_response": nr}
    if new_inputs is not None:
        out["inputs"] = new_inputs
    return out
