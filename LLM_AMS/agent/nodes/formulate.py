"""Route 6: compile a bounded, reviewed formulation template."""

from datetime import datetime

from langchain_core.messages import AIMessage

from agent.schemas.parameter import FormulationEdits
from agent.schemas.response import NodeResponse
from agent.state.app_state import State
from agent.utils.display import display_executing_node


def formulate_agent(state: State, llm, prompts, ams_ctx):
    display_executing_node("formulate")
    inputs = state["inputs"]
    if ams_ctx.system is None:
        msg = "No case is loaded. Load a case before editing its formulation."
        return {"messages": [AIMessage(content=msg)], "node_response": NodeResponse(
            node_type="formulate", success=False, data={}, message=msg, timestamp=datetime.now())}

    info = ams_ctx.case_info()
    extractor = llm.with_structured_output(FormulationEdits)
    result = extractor.invoke([
        {"role": "system", "content": prompts["formulate_extract"]["system"].format(
            routine=inputs.routine, gen_idx=info["gen_idx"], active_edit=inputs.formulation_edit)},
        {"role": "user", "content": state["messages"][-1].content},
    ])
    if not result.edits:
        msg = result.clarification or "Please specify the generator ID and available IBR power in pu."
        return {"messages": [AIMessage(content=msg)], "node_response": NodeResponse(
            node_type="formulate", success=False, data={}, message=msg, timestamp=datetime.now()),
            "is_compound": False, "results": None}

    edit = result.edits[0]
    try:
        report = ams_ctx.apply_formulation_edit(edit)
    except Exception as exc:
        return {"error_info": {"error_type": "formulation_error", "error_message": str(exc),
                               "current_inputs": inputs.model_dump(),
                               "user_input": state["messages"][-1].content},
                "failed_node": "formulate", "is_compound": False, "results": None}

    new_inputs = inputs.model_copy(update={"formulation_edit": edit.model_dump()})
    msg = (
        f"Compiled IBR replacement for {edit.generator_idx}: disabled the conventional unit; "
        "added pibr and pcurt; added availability and nodal-injection constraints; "
        f"added dispatch and curtailment costs. DCP check: passed. Bus: {report['bus_idx']}. "
        f"Available power: {edit.available_power} pu. Dispatch cost: {edit.dispatch_cost} dollars/pu. "
        f"Curtailment penalty: {edit.curtailment_cost} dollars/pu. "
        "Active-power scheduling equivalent only; no dynamic inverter or reactive-power model."
    )
    return {"messages": [AIMessage(content=msg)], "inputs": new_inputs, "results": None,
            "node_response": NodeResponse(node_type="formulate", success=True, data=report,
                                           message=msg, timestamp=datetime.now())}
