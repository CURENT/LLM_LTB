from datetime import datetime

from agent.schemas.planner import MultiStepPlan
from agent.schemas.response import NodeResponse
from agent.state.app_state import State
from agent.utils.display import display_executing_node


def planner_agent(state: State, llm, prompts, schema=MultiStepPlan):
    # ``schema``: a MultiStepPlan whose step actions include registered extensions
    display_executing_node("planner")

    inputs = state["inputs"]
    last_message = state["messages"][-1]

    planner_llm = llm.with_structured_output(schema)
    plan = planner_llm.invoke([
        {"role": "system", "content": prompts["planner"]["system"].format(
            case_path=inputs.case_path, routine=inputs.routine, solver=inputs.solver
        )},
        {"role": "user", "content": prompts["planner"]["user"].format(user_input=last_message.content)},
    ])

    nr = NodeResponse(
        node_type="planner",
        success=True,
        data={"description": plan.description, "n_steps": len(plan.steps),
              "plan": plan.model_dump()},
        message=f"Plan: {plan.description} ({len(plan.steps)} steps)",
        timestamp=datetime.now(),
    )
    return {"plan": plan, "current_step": 0, "step_results": [], "node_response": nr}
