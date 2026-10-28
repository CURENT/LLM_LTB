"""Route extensions: add a route to the agent without editing the built-in routes.

A route that lives outside this repository describes itself with a
:class:`RouteExtension`. ``create_workflow`` registers it next to the built-in
routes: its node joins the graph, its name becomes a valid classifier
``message_type`` and planner ``action``, its hints are appended to the
classifier and planner prompts, and its own prompts are merged into the prompt
dict its node receives.

With no extensions every helper here returns its input unchanged (the same
schema classes, the same prompt dict), so the agent behaves exactly as before.

Extensions are passed explicitly (``create_workflow(..., extensions=[...])``) or
named in ``$LTB_EXTENSIONS`` as comma-separated ``module:attribute`` entries,
where the attribute is a ``RouteExtension`` or a list/tuple of them::

    LTB_EXTENSIONS=casebuilder.ltb_plugin:CASE_BUILD

Failure isolation: an extension must not take the built-in routes down with it.
Entries from ``$LTB_EXTENSIONS`` that fail to import or validate are skipped with
a warning (:func:`load_warnings`); an exception in an extension node becomes
``error_info`` for the error handler; an exception in ``is_change`` counts as
False. Extensions passed explicitly are still validated strictly.
"""

from __future__ import annotations

import importlib
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Type, get_args

from pydantic import Field, create_model
from typing_extensions import Literal

from agent.schemas.classifier import MessageClassifier
from agent.schemas.planner import MultiStepPlan, StepType

ENV_VAR = "LTB_EXTENSIONS"
_log = logging.getLogger(__name__)
_LOAD_WARNINGS: List[str] = []

# Built-in routes, read from the classifier schema so a route added there is
# picked up here too. An extension may not reuse these or the graph's other nodes.
BUILTIN_ROUTES: Tuple[str, ...] = get_args(MessageClassifier.model_fields["message_type"].annotation)
_RESERVED = set(BUILTIN_ROUTES) | {"classifier", "router", "planner", "step_controller",
                                   "advance_step", "summary", "error_handler"}
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class RouteExtension:
    """One extra route.

    name:            route / node name, e.g. ``"case_build"`` (lower snake case).
    node:            ``node(state, llm, prompts, ams_ctx) -> dict`` — same contract
                     as the built-in route nodes.
    summary:         one line, appended to the classifier schema description.
    classifier_hint: bullet text for the classifier prompt (what the route does,
                     example messages).
    planner_hint:    optional rule text for the planner prompt.
    prompts:         optional ``{key: {"system": ...}}`` merged into the prompt
                     dict (keys must not collide with existing ones).
    is_change:       optional ``is_change(lowercased_text) -> bool``. When true
                     and the text also asks to solve, the router plans the turn
                     as change-then-solve (like "change ... and solve").
    """

    name: str
    node: Callable
    summary: str
    classifier_hint: str
    planner_hint: str = ""
    prompts: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    is_change: Optional[Callable[[str], bool]] = None


# --------------------------------------------------------------------------- #
# loading and validation
# --------------------------------------------------------------------------- #
def validate_extensions(extensions: Sequence[RouteExtension]) -> Tuple[RouteExtension, ...]:
    """Return the extensions as a tuple; raise ``ValueError`` on a bad set."""
    seen = set()
    for ext in extensions:
        if not isinstance(ext, RouteExtension):
            raise ValueError(f"not a RouteExtension: {ext!r}")
        if not _NAME_RE.match(ext.name):
            raise ValueError(f"extension name {ext.name!r} must be lower snake case")
        if ext.name in _RESERVED:
            raise ValueError(f"extension name {ext.name!r} is a built-in node name")
        if ext.name in seen:
            raise ValueError(f"extension name {ext.name!r} is registered twice")
        if not callable(ext.node):
            raise ValueError(f"extension {ext.name!r}: node is not callable")
        seen.add(ext.name)
    return tuple(extensions)


def load_extensions(spec: Optional[str] = None) -> Tuple[RouteExtension, ...]:
    """Import the extensions named in ``spec`` (default ``$LTB_EXTENSIONS``)."""
    spec = os.environ.get(ENV_VAR, "") if spec is None else spec
    found: List[RouteExtension] = []
    for entry in (e.strip() for e in spec.split(",")):
        if not entry:
            continue
        module_name, sep, attr = entry.partition(":")
        if not sep or not module_name or not attr:
            raise ValueError(f"{ENV_VAR} entry {entry!r} must look like 'module:attribute'")
        try:
            obj = getattr(importlib.import_module(module_name), attr)
        except (ImportError, AttributeError) as exc:
            raise ValueError(f"{ENV_VAR} entry {entry!r} could not be loaded: {exc}") from exc
        found.extend(obj if isinstance(obj, (list, tuple)) else [obj])
    return validate_extensions(found)


def load_extensions_lenient(spec: Optional[str] = None) -> Tuple[RouteExtension, ...]:
    """Like :func:`load_extensions`, but skip (and log) entries that fail.

    Used for ``$LTB_EXTENSIONS``: a moved, uninstalled or broken plugin must not
    disable the whole agent. The skipped entries are available from
    :func:`load_warnings` until the next call.
    """
    spec = os.environ.get(ENV_VAR, "") if spec is None else spec
    found: List[RouteExtension] = []
    warnings: List[str] = []
    for entry in (e.strip() for e in spec.split(",")):
        if not entry:
            continue
        try:
            for ext in load_extensions(entry):
                validate_extensions(found + [ext])        # name clashes with earlier entries
                found.append(ext)
        except Exception as exc:                          # ImportError, SyntaxError, ValueError, ...
            reason = str(exc)
            prefix = f"{ENV_VAR} entry {entry!r} "
            reason = reason[len(prefix):] if reason.startswith(prefix) else reason
            msg = f"{prefix}skipped: {reason}"
            _log.warning(msg)
            warnings.append(msg)
    _LOAD_WARNINGS[:] = warnings
    return tuple(found)


def load_warnings() -> List[str]:
    """Entries the last :func:`load_extensions_lenient` call skipped, as messages."""
    return list(_LOAD_WARNINGS)


# --------------------------------------------------------------------------- #
# failure isolation
# --------------------------------------------------------------------------- #
def says_change(ext: RouteExtension, text: str) -> bool:
    """``ext.is_change(text)``; False if it is missing or raises."""
    if ext.is_change is None:
        return False
    try:
        return bool(ext.is_change(text))
    except Exception as exc:                              # never break routing of other turns
        _log.warning("extension %r: is_change raised %s: %s", ext.name, type(exc).__name__, exc)
        return False


def run_node(ext: RouteExtension, state, llm, prompts, ams_ctx) -> dict:
    """Run an extension node; an exception or a non-dict result becomes ``error_info``
    (like built-in nodes' errors). LangGraph itself raises on a non-dict update
    (``InvalidUpdateError``), and ``None`` would end the turn with no reply at all."""
    try:
        result = ext.node(state, llm, prompts, ams_ctx)
    except Exception as exc:                              # not BaseException: Ctrl+C still stops
        _log.warning("extension %r: node raised %s: %s", ext.name, type(exc).__name__, exc)
        return _node_error(ext, state, f"{type(exc).__name__}: {exc}")
    if not isinstance(result, dict):
        _log.warning("extension %r: node returned %s, expected dict", ext.name, type(result).__name__)
        return _node_error(ext, state, f"the route returned {type(result).__name__}, expected a dict")
    return result


def _node_error(ext: RouteExtension, state, message: str) -> dict:
    messages = state.get("messages") or []
    inputs = state.get("inputs")
    return {"error_info": {"error_type": f"{ext.name}_error",
                           "error_message": message,
                           "user_input": getattr(messages[-1], "content", "") if messages else "",
                           "current_inputs": inputs.model_dump() if hasattr(inputs, "model_dump") else None},
            "failed_node": ext.name}


# --------------------------------------------------------------------------- #
# schemas
# --------------------------------------------------------------------------- #
def build_schemas(extensions: Sequence[RouteExtension]) -> Tuple[Type[MessageClassifier], Type[MultiStepPlan]]:
    """Classifier / planner schemas whose route literals include the extensions.

    Without extensions the built-in classes are returned as they are.
    """
    if not extensions:
        return MessageClassifier, MultiStepPlan
    names = BUILTIN_ROUTES + tuple(e.name for e in extensions)
    routes = Literal[names]   # Literal[("a", "b")] == Literal["a", "b"]

    base_desc = MessageClassifier.model_fields["message_type"].description or ""
    extra = "; ".join(f"{e.name} ({e.summary})" for e in extensions)
    classifier = create_model(
        "MessageClassifier", __base__=MessageClassifier,
        message_type=(routes, Field(..., description=f"{base_desc} Extension routes: {extra}.")),
    )
    step = create_model(
        "StepType", __base__=StepType,
        action=(routes, Field(..., description=StepType.model_fields["action"].description)),
    )
    plan = create_model(
        "MultiStepPlan", __base__=MultiStepPlan,
        steps=(List[step], Field(..., description=MultiStepPlan.model_fields["steps"].description)),
    )
    return classifier, plan


# --------------------------------------------------------------------------- #
# prompts
# --------------------------------------------------------------------------- #
def _escape_braces(text: str) -> str:
    # the planner system prompt goes through str.format(); hints must not add fields
    return text.replace("{", "{{").replace("}", "}}")


# Where extension text goes. Placement matters to small models: with llama3.1:8b,
# extension actions appended after the planner's "Current session" block (or
# just before it) turned "Change load PQ_1 to 3.2 then solve" from
# modify -> solve into formulate -> solve, 5 of 5 runs; listing them inside the
# built-in action list kept modify -> solve. Each anchor falls back to appending
# when the prompt text changes.
_CLASSIFIER_ANCHOR = "\n\nPick the BEST single category"
_PLANNER_RULES_ANCHOR = "\n\nExample —"


def _insert_before(text: str, anchor: str, addition: str) -> str:
    i = text.find(anchor)
    return text + addition if i < 0 else text[:i] + addition + text[i:]


def _insert_into_action_list(text: str, addition_lines: List[str]) -> Optional[str]:
    """Insert lines right after the last line of the planner's '- <route>' list."""
    lines = text.split("\n")
    items = {f"- {r}" for r in BUILTIN_ROUTES}
    hits = [i for i, line in enumerate(lines) if line.strip() in items]
    if not hits:
        return None
    last = hits[0]
    while last + 1 < len(lines) and lines[last + 1].strip() in items:
        last += 1
    return "\n".join(lines[:last + 1] + addition_lines + lines[last + 1:])


def extend_prompts(prompts: Dict[str, Dict[str, str]],
                   extensions: Sequence[RouteExtension]) -> Dict[str, Dict[str, str]]:
    """Prompt dict with extension hints inserted and extension prompts merged.

    Without extensions ``prompts`` is returned as it is. Otherwise a new dict is
    returned; the caller's dict is not modified.
    """
    if not extensions:
        return prompts
    out = {k: dict(v) for k, v in prompts.items()}

    # classifier: extension routes as more bullets after the built-in ones
    bullets = "".join(f"\n\n- **{e.name}**: {e.classifier_hint.strip()}" for e in extensions)
    out["classifier"]["system"] = _insert_before(out["classifier"]["system"], _CLASSIFIER_ANCHOR, bullets)

    # planner: extension actions inside the action list, rules at the end of Rules
    planner = out["planner"]["system"]
    names = [_escape_braces(f"- {e.name}") for e in extensions]
    listed = _insert_into_action_list(planner, names)
    planner = listed if listed is not None else planner + "\n\nAlso valid step actions:\n" + "\n".join(names)
    hints = [_escape_braces(e.planner_hint.strip()) for e in extensions if e.planner_hint.strip()]
    if hints:
        # continue the numbered Rules list ("5. ..."); a "- " bullet in the same spot
        # flipped the modify -> solve case above to formulate -> solve
        head = planner.split(_PLANNER_RULES_ANCHOR, 1)[0]
        numbers = [int(n) for n in re.findall(r"^(\d+)\. ", head, flags=re.M)]
        start = max(numbers) + 1 if numbers else 1
        rules = "".join(f"\n{start + i}. {h}" for i, h in enumerate(hints))
        planner = _insert_before(planner, _PLANNER_RULES_ANCHOR, rules)
    out["planner"]["system"] = planner

    for e in extensions:
        for key, value in e.prompts.items():
            if key in out:
                raise ValueError(f"extension {e.name!r}: prompt key {key!r} already exists")
            out[key] = dict(value)
    return out
