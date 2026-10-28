from datetime import datetime
import re

from agent.extensions import says_change
from agent.schemas.response import NodeResponse
from agent.state.app_state import State
from agent.utils.display import display_executing_node


# English hints used as a robust *fallback* when the LLM classifier does not
# flag a message as multi-step. Kept high-precision so
# they only fire on genuine compound requests.
_STEP_SEPARATORS = (
    "then ", "after that", "and then", "followed by", "; ",
)
_COMPARE_HINTS = (
    "compare ", "versus ", " vs ", "both ",
)
# A request that both CHANGES something and asks to SOLVE is compound: it must
# be split so the change is applied before the run.
_SOLVE_HINTS = (
    "solve", "run ", "dispatch", "optimi",
)
_CHANGE_HINTS = (
    "change", "set ", "trip", "switch", "load ", "restore", "disable", "enable", "replace",
)


def router(state: State, extensions=()):
    # ``extensions``: registered RouteExtension objects (agent.extensions)
    display_executing_node("router")

    message_type = state.get("message_type", "question_general")
    last = state["messages"][-1] if state.get("messages") else None
    user_input = last.content.lower() if last and hasattr(last, "content") else ""

    # Primary signal: the LLM classifier's own multi-step judgement.
    llm_multi_step = bool(state.get("is_multi_step"))

    # Fallback heuristics in case the classifier under-reports.
    is_multi = any(k in user_input for k in _STEP_SEPARATORS) or any(
        k in user_input for k in _COMPARE_HINTS
    )
    # "change/switch/load X ... and solve" → the change must precede the run.
    has_change = any(c in user_input for c in _CHANGE_HINTS) or any(
        says_change(e, user_input) for e in extensions
    )
    has_change_and_solve = has_change and any(
        s in user_input for s in _SOLVE_HINTS
    )
    needs_planning = llm_multi_step or is_multi or has_change_and_solve
    # A replacement includes variables, costs and constraints but remains one
    # atomic edit. Do not split its capacity/cost numbers into unrelated steps.
    if message_type == "formulate" and not re.search(
        r"\b(solve|run|load|switch)\b", user_input
    ):
        needs_planning = False

    if needs_planning:
        next_node, is_compound = "planner", True
    else:
        next_node = message_type if message_type in {
            "question_general", "question_parameter", "case_io",
            "configure", "modify", "formulate", "solve",
            *(e.name for e in extensions),
        } else "question_general"
        is_compound = False

    nr = NodeResponse(
        node_type="router",
        success=True,
        data={"next": next_node, "message_type": message_type,
              "is_compound": is_compound, "llm_multi_step": llm_multi_step},
        message=f"Routing to: {next_node}",
        timestamp=datetime.now(),
    )
    return {"next": next_node, "is_compound": is_compound, "node_response": nr}
