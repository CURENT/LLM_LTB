"""LangGraph workflow wiring for LLM_AMS Phase 1.

Nodes:
  classifier → router →
    question_general | question_parameter | case_io | configure | modify | solve | planner
  planner → step_controller → (one of the seven route nodes or summary)
  route nodes → END | advance_step | error_handler
  error_handler → END | advance_step
  advance_step → step_controller | summary
  summary → END

Extension routes (agent.extensions.RouteExtension) are wired exactly like the
built-in route nodes. They come from ``create_workflow(..., extensions=...)`` or,
when that argument is omitted, from ``$LTB_EXTENSIONS``.
"""

from langgraph.graph import END, START, StateGraph

from agent.extensions import build_schemas, extend_prompts, load_extensions, validate_extensions
from agent.nodes.advance_step import advance_step
from agent.nodes.case_io import case_io_agent
from agent.nodes.classify import classify_message
from agent.nodes.configure import configure_agent
from agent.nodes.error_handler import error_handler_agent
from agent.nodes.modify import modify_agent
from agent.nodes.formulate import formulate_agent
from agent.nodes.planner import planner_agent
from agent.nodes.question_general import question_general_agent
from agent.nodes.question_parameter import question_parameter_agent
from agent.nodes.route import router
from agent.nodes.solve import solve_agent
from agent.nodes.step_controller import step_controller
from agent.nodes.summary import summary_agent
from agent.state.app_state import State


_ROUTE_NODES = ("question_general", "question_parameter", "case_io",
                "configure", "modify", "formulate", "solve")


def create_workflow(llm, prompts, ams_ctx, extensions=None):
    # extensions=None reads $LTB_EXTENSIONS; pass () to disable extensions.
    extensions = load_extensions() if extensions is None else validate_extensions(extensions)
    prompts = extend_prompts(prompts, extensions)
    classifier_schema, plan_schema = build_schemas(extensions)
    routes = _ROUTE_NODES + tuple(e.name for e in extensions)

    g = StateGraph(State)

    g.add_node("classifier", lambda s: classify_message(s, llm, prompts, classifier_schema))
    g.add_node("router", lambda s: router(s, extensions))
    g.add_node("planner", lambda s: planner_agent(s, llm, prompts, plan_schema))
    g.add_node("step_controller", step_controller)
    g.add_node("advance_step", advance_step)
    g.add_node("summary", summary_agent)
    g.add_node("error_handler", lambda s: error_handler_agent(s, llm, prompts))

    g.add_node("question_general", lambda s: question_general_agent(s, llm, prompts, ams_ctx))
    g.add_node("question_parameter", lambda s: question_parameter_agent(s, llm, prompts, ams_ctx))
    g.add_node("case_io", lambda s: case_io_agent(s, llm, prompts, ams_ctx))
    g.add_node("configure", lambda s: configure_agent(s, llm, prompts, ams_ctx))
    g.add_node("modify", lambda s: modify_agent(s, llm, prompts, ams_ctx))
    g.add_node("formulate", lambda s: formulate_agent(s, llm, prompts, ams_ctx))
    g.add_node("solve", lambda s: solve_agent(s, llm, prompts, ams_ctx))
    for ext in extensions:
        g.add_node(ext.name, lambda s, ext=ext: ext.node(s, llm, prompts, ams_ctx))

    g.add_edge(START, "classifier")
    g.add_edge("classifier", "router")

    g.add_conditional_edges(
        "router",
        lambda s: s.get("next"),
        {**{n: n for n in routes}, "planner": "planner"},
    )
    g.add_edge("planner", "step_controller")
    g.add_conditional_edges(
        "step_controller",
        lambda s: s.get("next"),
        {**{n: n for n in routes}, "advance_step": "advance_step", "summary": "summary"},
    )

    def _after_route(state):
        if state.get("error_info"):
            return "error_handler"
        if state.get("is_compound"):
            return "advance_step"
        return "END"

    for node in routes:
        g.add_conditional_edges(
            node, _after_route,
            {"error_handler": "error_handler", "advance_step": "advance_step", "END": END},
        )

    g.add_conditional_edges(
        "error_handler",
        lambda s: "advance_step" if s.get("is_compound") else "END",
        {"advance_step": "advance_step", "END": END},
    )
    g.add_conditional_edges(
        "advance_step",
        lambda s: s.get("next"),
        {"step_controller": "step_controller", "summary": "summary"},
    )
    g.add_edge("summary", END)

    return g.compile()
