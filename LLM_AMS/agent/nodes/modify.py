"""Route 5: change load p0, trip/restore gen, trip/restore line, change line rate."""

from datetime import datetime

from langchain_core.messages import AIMessage

from agent.ams_engine.engine import LoadCurveConflict
from agent.schemas.parameter import SystemModifications
from agent.schemas.response import NodeResponse
from agent.state.app_state import State
from agent.utils.display import display_executing_node


def modify_agent(state: State, llm, prompts, ams_ctx):
    display_executing_node("modify")

    last_message = state["messages"][-1]
    inputs = state["inputs"]

    if ams_ctx.system is None:
        msg = "No case is loaded. Try: 'load case 5bus/pjm5bus_demo.xlsx'."
        reply = AIMessage(content=msg)
        nr = NodeResponse(node_type="modify", success=False, data={},
                          message=msg, timestamp=datetime.now())
        return {"messages": [reply], "node_response": nr}

    info = ams_ctx.case_info()
    extractor = llm.with_structured_output(SystemModifications)
    result = extractor.invoke([
        {"role": "system", "content": prompts["modify_extract"]["system"].format(
            load_idx=info["load_idx"],
            gen_idx=info["gen_idx"],
            line_idx=info["line_idx"],
            load_overrides=inputs.load_overrides,
            gen_off=inputs.gen_off,
            line_off=inputs.line_off,
        )},
        {"role": "user", "content": last_message.content},
    ])

    if not result.modifications:
        reply = AIMessage(content="No system modifications detected.")
        nr = NodeResponse(node_type="modify", success=True, data={},
                          message=reply.content, timestamp=datetime.now())
        return {"messages": [reply], "node_response": nr}

    load_overrides = dict(inputs.load_overrides)
    gen_off = list(inputs.gen_off)
    line_off = list(inputs.line_off)
    rate_overrides = dict(inputs.line_rate_overrides)
    summary = []

    for mod in result.modifications:
        try:
            if mod.target == "load_p0":
                if mod.value is None:
                    summary.append(f"⚠ skipped load_p0 for {mod.idx} — no value")
                    continue
                ams_ctx.alter_load_p0(mod.idx, float(mod.value))
                load_overrides[mod.idx] = float(mod.value)
                summary.append(f"load {mod.idx} p0 → {mod.value} pu")

            elif mod.target == "gen_trip":
                ams_ctx.set_gen_status(mod.idx, online=False)
                if mod.idx not in gen_off:
                    gen_off.append(mod.idx)
                summary.append(f"tripped generator {mod.idx}")

            elif mod.target == "gen_restore":
                ams_ctx.set_gen_status(mod.idx, online=True)
                if mod.idx in gen_off:
                    gen_off.remove(mod.idx)
                summary.append(f"restored generator {mod.idx}")

            elif mod.target == "line_trip":
                ams_ctx.set_line_status(mod.idx, online=False)
                if mod.idx not in line_off:
                    line_off.append(mod.idx)
                summary.append(f"tripped line {mod.idx}")

            elif mod.target == "line_restore":
                ams_ctx.set_line_status(mod.idx, online=True)
                if mod.idx in line_off:
                    line_off.remove(mod.idx)
                summary.append(f"restored line {mod.idx}")

            elif mod.target == "line_rate":
                if mod.value is None:
                    summary.append(f"⚠ skipped line_rate for {mod.idx} — no value")
                    continue
                ams_ctx.alter_line_rate(mod.idx, float(mod.value))
                rate_overrides[mod.idx] = float(mod.value)
                summary.append(f"line {mod.idx} rate_a → {mod.value} pu")

        except LoadCurveConflict as exc:
            # Deterministic, user-facing refusal: do not route through the LLM error handler.
            done = ("\n".join(f"• {x}" for x in summary) + "\n\n") if summary else ""
            # The sheet that feeds the ACTIVE routine (EDSlotPQ for ED-family, UCSlotPQ for
            # UC-family); if the active routine has no curve, every attached sheet is named.
            sheets = getattr(ams_ctx, "pq_curve_sheets", {})
            active_sheet = sheets.get(ams_ctx.routine_name)
            where = (f"the active routine ({ams_ctx.routine_name})" if active_sheet
                     else f"routines {sorted(sheets)}")
            edit = active_sheet or "/".join(sorted(set(sheets.values()))) or "EDSlotPQ/UCSlotPQ"
            content = (
                f"{done}I did not change load {mod.idx}. This case attaches a per-load time curve "
                f"to {where}, and a fixed p0 would be multiplied by that curve in every time slot — "
                f"the two effects would compound. To change this load, edit its curve in the case "
                f"file's {edit} sheet instead, or load a case without the curve.\n\n(engine: {exc})"
            )
            reply = AIMessage(content=content)
            nr = NodeResponse(node_type="modify", success=False,
                              data={"refused": mod.model_dump(), "reason": "load_curve_conflict"},
                              message=content, timestamp=datetime.now())
            new_inputs = inputs.model_copy(update={
                "load_overrides": load_overrides, "gen_off": gen_off,
                "line_off": line_off, "line_rate_overrides": rate_overrides,
            })
            return {"messages": [reply], "inputs": new_inputs, "node_response": nr}

        except Exception as exc:
            return {"error_info": {
                "error_type": "modify_error",
                "error_message": str(exc),
                "current_inputs": inputs.model_dump(),
                "user_input": last_message.content,
            }, "failed_node": "modify"}

    new_inputs = inputs.model_copy(update={
        "load_overrides": load_overrides,
        "gen_off": gen_off,
        "line_off": line_off,
        "line_rate_overrides": rate_overrides,
    })

    content = "Applied system modifications:\n" + "\n".join(f"• {s}" for s in summary)
    reply = AIMessage(content=content)
    nr = NodeResponse(node_type="modify", success=True,
                      data={"modifications": [m.model_dump() for m in result.modifications]},
                      message=content, timestamp=datetime.now())
    return {"messages": [reply], "inputs": new_inputs, "node_response": nr}
