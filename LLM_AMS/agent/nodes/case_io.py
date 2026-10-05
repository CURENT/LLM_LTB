"""Route 3: load / inspect a case file.

Case resolution is delegated to the deterministic keyword resolver in
``agent.ams_engine.case_catalog`` so retrieval no longer depends on the LLM
guessing a canonical alias. An ambiguous request (e.g. bare "ieee14", which has
json/conn/uced/raw variants) loads that family's default and reports the other
versions so the user can switch.
"""

from datetime import datetime

from langchain_core.messages import AIMessage

from agent.ams_engine.case_catalog import resolve_case
from agent.schemas.response import NodeResponse
from agent.state.app_state import State
from agent.utils.display import display_executing_node


_INSPECT_HINTS = (
    "show", "inspect", "current case", "case info", "what case", "which case",
    "current system", "loaded case",
)


def case_io_agent(state: State, llm, prompts, ams_ctx):
    display_executing_node("case_io")

    last_message = state["messages"][-1]
    text = last_message.content
    inputs = state["inputs"]

    wants_inspect = any(k in text.lower() for k in _INSPECT_HINTS)
    res = resolve_case(text)

    # No case named → inspect the current one, or explain how to name a case.
    if res.status == "not_found":
        if wants_inspect and ams_ctx.system is not None:
            info = ams_ctx.case_info()
            content = (
                f"**Case info**: {info.get('case_path', '(none)')}\n\n"
                f"- Buses: {info.get('n_bus')}\n"
                f"- Lines: {info.get('n_line')}\n"
                f"- Loads (PQ): {info.get('n_pq')}\n"
                f"- Generators (StaticGen): {info.get('n_staticgen')} "
                f"(PV: {info.get('n_pv')}, Slack: {info.get('n_slack')})\n"
            )
            reply = AIMessage(content=content)
            nr = NodeResponse(
                node_type="case_io", success=True,
                data={"action": "inspect", "info": info},
                message=content, timestamp=datetime.now(),
            )
            return {"messages": [reply], "node_response": nr}

        msg = res.message or (
            "I couldn't parse a case from your request. Try a bus number "
            "(`5`, `14`, `39`, `118`, `300`), a name (`pjm`, `ieee14`, `ieee39`, "
            "`wecc`, `npcc`, `hawaii`, `pglib`), or a file path "
            "(e.g. `matpower/case118.m`)."
        )
        reply = AIMessage(content=msg)
        nr = NodeResponse(
            node_type="case_io", success=False, data={}, message=msg,
            timestamp=datetime.now(),
        )
        return {"messages": [reply], "node_response": nr}

    # Load the resolved (or, for an ambiguous request, the default) case.
    try:
        info = ams_ctx.load_case(res.path)
    except Exception as exc:
        caveat = res.entry.caveat if res.entry else ""
        if caveat:
            # A known ltbams load limitation — explain it directly and clearly
            # rather than surfacing a raw traceback via the generic handler.
            msg = (
                f"`{res.entry.key}` matched your request but ltbams could not "
                f"load it: {caveat}.\n\nUnderlying error: {exc}"
            )
            reply = AIMessage(content=msg)
            nr = NodeResponse(
                node_type="case_io", success=False,
                data={"action": "load_failed", "case": res.entry.key,
                      "caveat": caveat},
                message=msg, timestamp=datetime.now(),
            )
            return {"messages": [reply], "node_response": nr}
        err = {"error_type": "case_load_error", "error_message": str(exc),
               "user_input": text, "current_inputs": inputs.model_dump()}
        return {"error_info": err, "failed_node": "case_io"}

    # Reset session-level modifications since we just loaded a fresh case.
    new_inputs = inputs.model_copy(update={
        "case_path": info["case_path"],
        "load_overrides": {},
        "gen_off": [],
        "line_off": [],
        "line_rate_overrides": {},
        "disabled_constraints": [],
        "formulation_edit": None,
    })

    label = res.entry.key if res.entry else info["case_path"]
    content = (
        f"Loaded case: `{label}`\n"
        f"- Buses: {info['n_bus']}, Lines: {info['n_line']}, "
        f"Loads: {info['n_pq']}, StaticGens: {info['n_staticgen']}\n"
        f"- Session modifications reset."
    )
    if res.status == "ambiguous":
        content += "\n\n" + res.message

    reply = AIMessage(content=content)
    nr = NodeResponse(
        node_type="case_io",
        success=True,
        data={"action": "load", "info": info, "resolution": res.status,
              "candidates": [e.key for e in res.candidates]},
        message=content,
        timestamp=datetime.now(),
    )
    return {"messages": [reply], "inputs": new_inputs, "node_response": nr}
