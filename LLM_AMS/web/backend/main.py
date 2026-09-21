"""LLM-AMS web backend — FastAPI.

A lightweight, read-only API + static-file server for the local web platform.
Its centerpiece is accurate **MathJax** rendering of each routine's objective
and constraints (UC, ED, RTED, DCOPF, ACOPF, …), backed by the same live
``ams.System`` the terminal agent uses for its data snapshots.

Run locally (from the project root ``LLM_AMS/LLM_AMS``)::

    uvicorn web.backend.main:app --host 127.0.0.1 --port 8000

Then open  http://127.0.0.1:8000  in a browser.

Endpoints
---------
GET  /api/health                      liveness probe
GET  /api/routines                    routines grouped by category (+ formulation flag)
GET  /api/cases                       shipped cases the viewer can load
GET  /api/solvers/{routine}           solvers compatible with a routine
GET  /api/formulation/{routine}       structured LaTeX objective + constraints
GET  /api/case?routine=&case=         routine-aware data tables for a loaded case
POST /api/solve                       run a routine, plot results, return a summary
POST /api/report                      build a Markdown power-system analysis report
POST /api/chat                        natural-language turn with the LangGraph agent
POST /api/week/build                  build an N-hour case from an uploaded profile.csv
GET  /generated/<file>                result plots produced by a solve
"""

# Matplotlib must use a non-interactive backend: the solve runs inside a worker
# thread with no display. Set this *before* anything imports pyplot.
import matplotlib
matplotlib.use("Agg")

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent.ams_engine.constraint_check import check_constraints
from agent.ams_engine.engine import AMSContext, SHIPPED_CASES
from agent.ams_engine.formulations_latex import get_latex_formulation, _BY_FAMILY
from agent.ams_engine.plotting import plot_results
from agent.ams_engine.routines import all_routine_names, compatible_solvers, routine_family
from agent.ams_engine.week_case import build_week_case
from web.backend.case_data import get_case_tables
from web.backend.report import build_report


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
# Plots are written to ``<cwd>/generated`` by plot_results (AMS_OUTPUT_DIR). The
# server is launched from the project root (LLM_AMS/LLM_AMS), so resolve it the
# same way and expose it read-only at /generated.
GENERATED_DIR = Path(os.environ.get("AMS_OUTPUT_DIR") or (Path.cwd() / "generated"))

DEFAULT_CASE_ALIAS = "pjm5bus_demo"
DEFAULT_ROUTINE = "RTED"


# High-level routine categories (mirrors the terminal's routine table so the
# web UI groups routines identically). Names are filtered against the routines
# actually available in the installed AMS version.
ROUTINE_CATEGORIES = [
    {
        "category": "DCED",
        "name": "DC Economic Dispatch",
        "routines": ["DCOPF", "DCOPF2", "RTED", "RTEDDG", "RTEDESP", "RTEDES",
                      "RTEDVIS", "RTED2", "ED", "EDDG", "EDES", "ED2"],
    },
    {
        "category": "DCUC",
        "name": "Unit Commitment",
        "routines": ["UC", "UCDG", "UCES", "UC2"],
    },
    {
        "category": "ACED",
        "name": "AC Optimal Power Flow",
        "routines": ["ACOPF", "OPF"],
    },
    {
        "category": "DED",
        "name": "Distribution OPF",
        "routines": ["DOPF", "DOPFVIS"],
    },
    {
        "category": "PF",
        "name": "Power Flow",
        "routines": ["DCPF", "PFlow"],
    },
]


# Curated, de-duplicated list of shipped cases for the case picker.
CASE_PICKER = [
    ("pjm5bus_demo", "PJM 5-bus (demo)"),
    ("pjm5bus_ev",   "PJM 5-bus (EV)"),
    ("ieee14",       "IEEE 14-bus"),
    ("ieee14_uced",  "IEEE 14-bus (UC/ED)"),
    ("ieee39",       "IEEE 39-bus"),
    ("ieee39_uced",  "IEEE 39-bus (UC/ED)"),
    ("case118",      "MATPOWER case118"),
    ("npcc",         "NPCC 140-bus"),
    ("wecc",         "WECC 179-bus"),
]


# ----------------------------------------------------------------- shared state
_ctx = AMSContext()
_lock = threading.Lock()                 # the AMS System is not concurrency-safe
_state = {"alias": None}                 # alias of the case currently loaded
# Cases built at runtime from an uploaded profile (POST /api/week/build):
# case_id -> {"path", "label", "n_slots", "manifest"}. They join the picker and
# resolve like shipped aliases; the id is the xlsx stem under generated/week/.
_custom_cases: Dict[str, Dict[str, Any]] = {}
# Uploaded profiles / built workbooks / manifests are NOT served: GENERATED_DIR is mounted
# read-only at /generated for result plots, so week artifacts live outside it (data/ is
# gitignored by pattern). Override with AMS_WEEK_DIR.
WEEK_DIR = Path(os.environ.get("AMS_WEEK_DIR") or (Path.cwd() / "data" / "week_cases"))

# Request-controlled case references (`case` on /api/solve|/api/case|/api/chat, `base` on
# /api/week/build) may name a file only under these roots: the server's working dir (the
# LLM_AMS project: cases/, data/, generated/), the repo-level cases/ and data/ dirs, and
# WEEK_DIR. Extend with AMS_CASE_ROOTS (os.pathsep-separated). Shipped aliases and cases
# built in this server are always allowed. Deployment policy — see _resolve_case_ref.
_CASE_EXTS = (".xlsx", ".json", ".m", ".raw")
CASE_ROOTS: List[Path] = [Path.cwd(), Path.cwd().parent / "cases", Path.cwd().parent / "data"]
CASE_ROOTS += [Path(p) for p in os.environ.get("AMS_CASE_ROOTS", "").split(os.pathsep) if p]


def _case_roots() -> List[Path]:
    return CASE_ROOTS + [WEEK_DIR]                   # WEEK_DIR read at call time (tests patch it)


def _resolve_case_ref(ref: str) -> str:
    """Map a request-supplied case reference to something ``AMSContext.load_case`` may open.

    Returns a shipped alias / catalog key unchanged, a built case id's stored path, or an
    absolute file path that lies under one of ``CASE_ROOTS``. Anything else raises
    ``PermissionError`` (surfaced as HTTP 400) — arbitrary server-side files are not readable
    through the API.
    """
    raw = (ref or "").strip()
    if raw in _custom_cases:
        return _custom_cases[raw]["path"]
    if raw in SHIPPED_CASES:
        return raw
    looks_like_path = os.path.isabs(raw) or raw.lower().endswith(_CASE_EXTS) and os.sep in raw
    if not looks_like_path:
        return raw                                   # catalog keyword ("5bus", "ieee39") -> resolver
    p = Path(raw).expanduser().resolve()
    if p.suffix.lower() not in _CASE_EXTS or not p.is_file():
        raise PermissionError(f"case path {raw!r} is not an existing case file")
    roots = _case_roots()
    for root in roots:
        try:
            if p.is_relative_to(root.resolve()):
                return str(p)
        except (OSError, ValueError):
            continue
    raise PermissionError(
        f"case path {raw!r} is outside the allowed case roots {[str(r) for r in roots]}; "
        f"copy it under one of them or set AMS_CASE_ROOTS"
    )
_available_routines: set = set()         # filled at startup

# Cache of the most recent solve, keyed by (case_alias, routine). Lets /api/report
# reuse the values + plots produced by /api/solve without re-running.
_last_solve: Dict[tuple, Dict[str, Any]] = {}

# Lazily-built LangGraph agent (only when /api/chat is first hit). Sharing _ctx
# means a chat "load case … / run …" updates the same System the viewer reads.
_agent: Dict[str, Any] = {"graph": None, "state": None, "error": None, "tried": False}

# LLM provider + model chosen at runtime via /api/llm (mirrors the terminal's
# provider prompt). ``model=None`` -> use the provider's env default. Seeded from
# the same env vars the terminal ``make_llm`` reads.
_llm_cfg: Dict[str, Any] = {
    "provider": os.getenv("LLM_PROVIDER", "ollama"),
    "model": (os.getenv("OLLAMA_MODEL")
              if os.getenv("LLM_PROVIDER", "ollama") == "ollama"
              else os.getenv("OPENAI_MODEL")) or None,
}

# Curated OpenAI chat models offered in the picker (used only when a key is set).
_OPENAI_MODELS = ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "o3-mini", "gpt-4-turbo"]

# Friendly labels for cases (alias -> label), filled from CASE_PICKER.
_CASE_LABELS = {alias: label for alias, label in CASE_PICKER}


def _case_label(alias: Optional[str]) -> str:
    if alias in _custom_cases:
        return _custom_cases[alias]["label"]
    return _CASE_LABELS.get(alias or "", alias or "—")


def _alias_for_case(case_ref: Optional[str]) -> Optional[str]:
    """Map a case alias *or* a resolved file path back to a case-picker alias.

    The chat agent stores ``inputs.case_path`` as a fully-resolved path once it
    loads a case, while the UI dropdown works in short aliases. This reverse-maps
    so the sidebar can select whatever case the agent switched to. Aliases shown
    in the dropdown (``CASE_PICKER``) are preferred.
    """
    if not case_ref:
        return None
    raw = str(case_ref).strip()
    for cid, c in _custom_cases.items():                # a built case, by id or by path
        if raw == cid or raw == c["path"]:
            return cid
    norm = raw.replace("\\", "/").lower()
    rel = SHIPPED_CASES.get(raw, "").replace("\\", "/").lower() or None
    picker = [a for a, _ in CASE_PICKER]
    # Prefer a dropdown alias whose file matches (by alias or by path tail).
    for alias in picker:
        arel = SHIPPED_CASES.get(alias, "").replace("\\", "/").lower()
        if arel and (norm.endswith(arel) or rel == arel):
            return alias
    # Otherwise any shipped alias whose file matches the path tail.
    for alias, r in SHIPPED_CASES.items():
        if norm.endswith(r.replace("\\", "/").lower()):
            return alias
    # A bare alias we recognise but don't surface in the picker.
    return raw if raw in SHIPPED_CASES else None


# AMS raises these when a case lacks the generation-cost / reserve / temporal data
# an economic routine needs. Plain power-flow cases (ieee14, ieee39, case*, npcc,
# wecc) don't ship it; the *_uced variants and pjm5bus_demo do.
_MISSING_DATA_MARKERS = (
    "no gcost", "no srcost", "no sfrcost", "no sr device",
    "sfr", "srcost", "gcost", "edslot", "edslotgen", "edslotload",
    "failed to evaluate param", "missing in input",
)


def _suggest_uced(case_alias: Optional[str]) -> Optional[str]:
    """The ``*_uced`` sibling of a case, if one ships (it has cost/reserve data)."""
    if not case_alias:
        return None
    base = str(case_alias).split("_")[0]
    for cand in (f"{case_alias}_uced", f"{base}_uced"):
        if cand in SHIPPED_CASES:
            return cand
    return None


def _friendly_solve_error(exc: Exception, routine: str,
                          case_alias: Optional[str]) -> str:
    """Turn a cryptic AMS "missing data" exception into an actionable message."""
    detail = str(exc)
    if any(m in detail.lower() for m in _MISSING_DATA_MARKERS):
        uced = _suggest_uced(case_alias)
        fix = (f"switch the grid case to '{uced}' (it ships the required cost and "
               f"reserve data)" if uced
               else "use a '…_uced' case variant that ships cost/reserve data")
        last = detail.strip().splitlines()[-1] if detail.strip() else "missing model"
        return (
            f"'{_case_label(case_alias)}' has no generation-cost / reserve data, which "
            f"{routine} needs to solve an economic-dispatch problem. To run {routine}, "
            f"{fix}; or choose a power-flow routine (DCPF / PFlow) that doesn't need "
            f"cost data. (AMS: {last})"
        )
    return f"{type(exc).__name__}: {exc}"


def _ensure_case(case_alias: Optional[str]):
    """Load the requested (or default) case if it isn't already active."""
    target = case_alias or _state["alias"] or DEFAULT_CASE_ALIAS
    if _ctx.system is None or target != _state["alias"]:
        _ctx.load_case(_resolve_case_ref(target))    # built id / alias / allowlisted path only
        _state["alias"] = target


def _plot_urls(plots: Dict[str, str]) -> Dict[str, str]:
    """Turn ``{var: absolute_png_path}`` into browser URLs under /generated."""
    return {k: f"generated/{Path(p).name}" for k, p in plots.items()}


def _run_solve(routine: str, case_alias: Optional[str], solver: str) -> Dict[str, Any]:
    """Run one routine end-to-end (solve + plot + violation check) and cache it.

    Caller MUST hold ``_lock`` (mutates the shared System). Returns a JSON-able
    dict with the objective, per-variable summaries, violations and plot URLs.
    """
    _ensure_case(case_alias)
    _ctx.set_routine(routine)
    # Multi-period routines: ignore_dpp skips cvxpy 1.9.2's DPP canonicalisation, which
    # fails above 160 slots; results are identical (checked at 160 slots).
    multi_period = hasattr(_ctx.active_routine(), "timeslot")
    results = _ctx.solve(solver=solver, ignore_dpp=multi_period)

    # Plots are isolated — a bad variable must not sink the whole turn.
    try:
        plots = plot_results(results)
    except Exception as exc:  # pragma: no cover - defensive
        plots = {}
        results["plot_warning"] = str(exc)

    try:
        results["violations"] = check_constraints(_ctx, results)
    except Exception:
        results["violations"] = []

    info = _ctx.case_info()
    payload = {
        "routine": routine,
        "family": routine_family(routine),
        "case": _state["alias"],
        "case_label": _case_label(_state["alias"]),
        "solver": solver,
        "converged": results.get("converged"),
        "exit_code": results.get("exit_code"),
        "objective": results.get("objective"),
        "violations": results.get("violations", []),
        "plots": _plot_urls(plots),
        "info": info,
        # provenance for multi-period / curve cases (see AMSContext.solve)
        "horizon_slots": len(results.get("slot_idx", [])) or 1,
        "ignore_dpp": results.get("ignore_dpp", False),
        "pq_curves": results.get("pq_curves", []),
        "status": results.get("status", ""),
    }
    # Stash the rich result + plots so /api/report can reuse them verbatim.
    _last_solve[(_state["alias"], routine)] = {
        "results": results,
        "plots": _plot_urls(plots),
        "info": info,
        "solver": solver,
    }
    return payload


# ----------------------------------------------------------------- request models
class SolveRequest(BaseModel):
    routine: str = DEFAULT_ROUTINE
    case: Optional[str] = None
    solver: str = "CLARABEL"


class ReportRequest(BaseModel):
    routine: str = DEFAULT_ROUTINE
    case: Optional[str] = None
    solver: str = "CLARABEL"


class ChatRequest(BaseModel):
    message: str
    routine: Optional[str] = None
    case: Optional[str] = None
    solver: Optional[str] = None


# Upload limits for /api/week/build: 8760 hourly rows x ~5 loads is well under 1 MB.
MAX_PROFILE_BYTES = int(os.environ.get("AMS_MAX_PROFILE_BYTES", 4 * 1024 * 1024))
MAX_PROFILE_ROWS = int(os.environ.get("AMS_MAX_PROFILE_ROWS", 8760 + 1))


class WeekBuildRequest(BaseModel):
    """Body of POST /api/week/build: the uploaded CSV as text (no multipart dep).

    The file may have any name — the browser reads it and sends its contents; only the
    columns matter (``hour``, optional ``timestamp``, one column per PQ load).
    """
    profile_csv: str = Field(..., max_length=MAX_PROFILE_BYTES)
    base: str = DEFAULT_CASE_ALIAS          # shipped alias or absolute xlsx path
    case_id: Optional[str] = None           # xlsx stem; default <base>_<N>h
    unit: str = "MW"


def _build_week_from_text(req: WeekBuildRequest) -> Dict[str, Any]:
    """Write the uploaded CSV under generated/week/, build the N-slot case, register it.

    Caller MUST hold ``_lock``. Raises ValueError/RuntimeError from the builder.
    """
    import re
    text = (req.profile_csv or "").strip()
    if not text:
        raise ValueError("profile_csv is empty")
    nbytes = len(text.encode("utf-8"))
    if nbytes > MAX_PROFILE_BYTES:
        raise ValueError(f"profile is {nbytes} bytes; limit {MAX_PROFILE_BYTES}")
    nrows = text.count("\n")                            # data rows (header excluded)
    if nrows > MAX_PROFILE_ROWS:
        raise ValueError(f"profile has {nrows} rows; limit {MAX_PROFILE_ROWS} (one year of hours)")
    WEEK_DIR.mkdir(parents=True, exist_ok=True)
    stem = (req.case_id or "").strip() or None
    if stem and not re.fullmatch(r"[A-Za-z0-9_.-]+", stem):
        raise ValueError("case_id may contain only letters, digits, '_', '.', '-'")
    # base may be a shipped alias, an absolute path, or the id of a case built earlier in
    # this server (the UI sends whatever is selected in the picker)
    base_ref = _resolve_case_ref(req.base)           # built id / alias / allowlisted path only
    csv_path = WEEK_DIR / f"{stem or 'upload'}.profile.csv"
    csv_path.write_text(text + "\n")
    try:
        art = build_week_case(base_ref, str(csv_path), str(WEEK_DIR), case_id=stem, unit=req.unit)
    except Exception:
        csv_path.unlink(missing_ok=True)                 # do not keep rejected uploads
        raise
    case_id = Path(art.xlsx_path).stem
    # a rebuild under the same id replaces the workbook: cached solves for it are now stale
    for key in [k for k in _last_solve if k[0] == case_id]:
        del _last_solve[key]
    _custom_cases[case_id] = {
        "path": art.xlsx_path,
        "label": f"{_case_label(req.base)} · {art.n_slots} h ({case_id})",
        "n_slots": art.n_slots,
        "manifest": art.manifest_path,
    }
    # the built file becomes the active case (a later /api/solve without `case` runs it)
    _ctx.load_case(art.xlsx_path)
    _state["alias"] = case_id
    return {
        "case_id": case_id,
        "label": _custom_cases[case_id]["label"],
        "path": art.xlsx_path,
        "manifest": art.manifest_path,
        "n_slots": art.n_slots,
        "curve_sheets": art.curve_sheets,
        "max_residual": art.max_residual,
        "profile_saved_as": str(csv_path),
        "note": "Solve with a multi-period routine (ED / UC / EDES / UCES); ignore_dpp is applied automatically.",
    }


class LLMRequest(BaseModel):
    provider: str
    model: Optional[str] = None


# ----------------------------------------------------------------- chat agent
def _ensure_agent() -> Optional[str]:
    """Lazily build the LangGraph agent over the shared System.

    Returns ``None`` on success or an error string if the agent cannot be built
    (e.g. the LangChain stack or an LLM backend is not available). The viewer
    keeps working regardless; only /api/chat depends on this.
    """
    if _agent["graph"] is not None:
        return None
    if _agent["tried"]:
        return _agent["error"]
    _agent["tried"] = True
    try:
        from agent.core import make_llm
        from agent.prompts import get_prompts
        from agent.workflows.workflow import create_workflow
        from agent.utils.common_utils import create_initial_state

        provider = _llm_cfg["provider"]
        # make_llm reads the model from these env vars — reflect the UI choice.
        if _llm_cfg.get("model"):
            os.environ["OPENAI_MODEL" if provider == "openai" else "OLLAMA_MODEL"] = _llm_cfg["model"]
        llm = make_llm(provider)
        _llm_cfg["model"] = getattr(llm, "_model_name", _llm_cfg.get("model"))
        prompts = get_prompts()
        # Share the viewer's AMSContext so chat + viewer act on one System.
        graph = create_workflow(llm, prompts, _ctx)
        _agent["graph"] = graph
        _agent["state"] = create_initial_state(
            case_path=_state["alias"] or DEFAULT_CASE_ALIAS,
            routine=_ctx.routine_name or DEFAULT_ROUTINE,
        )
    except Exception as exc:  # langchain missing / import error
        _agent["error"] = (
            f"The conversational agent is unavailable ({type(exc).__name__}: {exc}). "
            "Use the Run button for results; install the LangChain stack and an "
            "LLM backend (Ollama or OPENAI_API_KEY) to enable chat."
        )
    return _agent["error"]


def _reset_agent() -> None:
    """Drop the cached agent so the next turn rebuilds it with the current LLM config."""
    _agent.update({"graph": None, "state": None, "error": None, "tried": False})


def _ollama_models() -> List[str]:
    """Names of models the local Ollama server has pulled (empty if unreachable)."""
    import json as _json
    import urllib.request

    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=1.5) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        return sorted(m["name"] for m in data.get("models", []) if m.get("name"))
    except Exception:
        return []


def _llm_status() -> Dict[str, Any]:
    """Current provider/model plus the providers & models the UI can offer.

    Mirrors the terminal's provider prompt: Ollama (local) or OpenAI (needs key).
    """
    ollama_models = _ollama_models()
    ollama_env_default = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
    ollama_default = (ollama_env_default if ollama_env_default in ollama_models
                      else (ollama_models[0] if ollama_models else ollama_env_default))
    openai_key = bool(os.getenv("OPENAI_API_KEY"))
    openai_default = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    provider = _llm_cfg["provider"]
    model = _llm_cfg.get("model") or (
        openai_default if provider == "openai" else ollama_default)
    return {
        "provider": provider,
        "model": model,
        "agent_ready": _agent["graph"] is not None,
        "providers": [
            {"id": "ollama", "label": "Ollama (local)",
             "available": bool(ollama_models), "models": ollama_models,
             "default": ollama_default},
            {"id": "openai", "label": "OpenAI (API key)",
             "available": openai_key, "models": _OPENAI_MODELS,
             "default": openai_default},
        ],
    }


def _agent_turn(message: str, routine: Optional[str],
                case: Optional[str], solver: Optional[str]) -> Dict[str, Any]:
    """Run one conversational turn. Caller holds ``_lock``."""
    err = _ensure_agent()
    if err:
        return {"ok": False, "reply": err, "results": None}

    from langchain_core.messages import HumanMessage

    state = _agent["state"]
    # Reflect the UI's current selection into the agent's inputs.
    inputs = state["inputs"]
    update = {}
    if routine:
        update["routine"] = routine.upper().strip()
    if solver:
        update["solver"] = solver.strip()
    if case:
        # The sidebar selection is AUTHORITATIVE: actually load the chosen case
        # into the shared System so the agent runs it — not merely record its
        # name. Without this, a case picked in the UI while the System still held
        # another (e.g. the dropdown changed during a busy turn) would be ignored
        # and the agent would keep running the previously-loaded case.
        case_alias = case.strip()
        prev_alias = _state["alias"]
        try:
            _ensure_case(case_alias)
        except Exception:
            pass
        update["case_path"] = _ctx.case_path or case_alias
        if _state["alias"] != prev_alias:
            # Fresh case → drop stale per-case modifications (mirror case_io).
            update.update({"load_overrides": {}, "gen_off": [], "line_off": [],
                           "line_rate_overrides": {}, "disabled_constraints": []})
    if update:
        state["inputs"] = inputs.model_copy(update=update)

    state["messages"] = state.get("messages", []) + [HumanMessage(content=message)]

    replies: List[str] = []
    try:
        for chunk in _agent["graph"].stream(state, config={"recursion_limit": 50},
                                            stream_mode="updates"):
            for _node, upd in chunk.items():
                for k, v in upd.items():
                    if k == "messages":
                        state["messages"] = state.get("messages", []) + v
                        for m in v:
                            content = getattr(m, "content", None)
                            if content:
                                replies.append(content)
                    else:
                        state[k] = v
    except Exception as exc:
        return {"ok": False,
                "reply": f"The agent could not complete the turn "
                         f"({type(exc).__name__}: {exc}). Is the LLM backend running?",
                "results": None}

    # Reflect the agent's final session so the UI sidebar can sync to whatever
    # case / routine / solver this turn switched to (loads the right equations).
    session = None
    final_inputs = _agent["state"].get("inputs")
    if final_inputs is not None:
        alias = _alias_for_case(getattr(final_inputs, "case_path", None))
        if alias:
            _state["alias"] = alias          # keep the viewer endpoints consistent
        routine_name = (getattr(final_inputs, "routine", None) or "").upper() or None
        session = {
            "routine": routine_name,
            "case": alias,
            "solver": getattr(final_inputs, "solver", None),
        }

    return {
        "ok": True,
        "reply": "\n\n".join(replies) if replies else "_(no reply)_",
        "results": state.get("results"),
        "session": session,
    }


# ----------------------------------------------------------------- lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _available_routines
    _available_routines = set(all_routine_names())
    # Eagerly load the default case so the first request is fast.
    with _lock:
        _ensure_case(DEFAULT_CASE_ALIAS)
        _ctx.set_routine(DEFAULT_ROUTINE)
    yield


# ----------------------------------------------------------------- app factory
def create_app() -> FastAPI:
    app = FastAPI(
        title="LLM-AMS Web Platform",
        description="Local web viewer for CURENT LTB AMS — MathJax equations + case data.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Same-origin in production; allow localhost dev servers too.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8000", "http://localhost:8000",
            "http://127.0.0.1:5173", "http://localhost:5173",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # -------------------------------------------------------- API
    @app.get("/api/health")
    def health():
        return {"status": "ok", "case_loaded": _ctx.system is not None}

    @app.get("/api/routines")
    def routines():
        available = _available_routines or set(all_routine_names())
        groups = []
        for cat in ROUTINE_CATEGORIES:
            items = []
            for name in cat["routines"]:
                if name not in available:
                    continue
                items.append({
                    "name": name,
                    "hasFormulation": routine_family(name) in _BY_FAMILY,
                })
            if items:
                groups.append({
                    "category": cat["category"],
                    "name": cat["name"],
                    "routines": items,
                })
        return {"groups": groups, "default": DEFAULT_ROUTINE}

    @app.get("/api/cases")
    def cases():
        items = []
        for alias, label in CASE_PICKER:
            if alias in SHIPPED_CASES:
                items.append({"alias": alias, "label": label, "path": SHIPPED_CASES[alias]})
        with _lock:                                   # /api/week/build mutates _custom_cases
            built = list(_custom_cases.items())
        for cid, c in built:
            items.append({"alias": cid, "label": c["label"], "path": c["path"], "built": True,
                          "n_slots": c["n_slots"]})
        return {"cases": items, "default": DEFAULT_CASE_ALIAS}

    @app.post("/api/week/build")
    def week_build(req: WeekBuildRequest):
        with _lock:
            try:
                return _build_week_from_text(req)
            except (ValueError, RuntimeError, FileNotFoundError, PermissionError) as exc:
                raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}")

    @app.get("/api/formulation/{routine}")
    def formulation(routine: str):
        return get_latex_formulation(routine.upper().strip())

    @app.get("/api/solvers/{routine}")
    def solvers(routine: str):
        routine = routine.upper().strip()
        try:
            compat = compatible_solvers(routine)
        except Exception:
            compat = []
        return {"routine": routine, "solvers": compat,
                "default": compat[0] if compat else "CLARABEL"}

    @app.get("/api/case")
    def case(
        routine: str = Query(DEFAULT_ROUTINE),
        case: Optional[str] = Query(None),
    ):
        routine = routine.upper().strip()
        case_alias = (case or "").strip() or None
        with _lock:
            try:
                _ensure_case(case_alias)
                _ctx.set_routine(routine)
                info = _ctx.case_info()
                snapshot = get_case_tables(_ctx.system, routine)
            except Exception as exc:  # surface a clean error to the UI
                raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}")
        return {
            "routine": routine,
            "family": routine_family(routine),
            "case": _state["alias"],
            "info": info,
            "snapshot": snapshot,
        }

    @app.post("/api/solve")
    def solve(req: SolveRequest):
        routine = req.routine.upper().strip()
        case_alias = (req.case or "").strip() or None
        solver = (req.solver or "CLARABEL").strip() or "CLARABEL"
        with _lock:
            try:
                return _run_solve(routine, case_alias, solver)
            except Exception as exc:
                raise HTTPException(status_code=400,
                                   detail=_friendly_solve_error(exc, routine, _state["alias"]))

    @app.post("/api/report")
    def report(req: ReportRequest):
        routine = req.routine.upper().strip()
        case_alias = (req.case or "").strip() or None
        solver = (req.solver or "CLARABEL").strip() or "CLARABEL"
        with _lock:
            try:
                # Reuse the most recent solve for this (case, routine) if present;
                # otherwise run one now so the report always has fresh numbers.
                _ensure_case(case_alias)
                key = (_state["alias"], routine)
                cached = _last_solve.get(key)
                if cached is None:
                    _run_solve(routine, case_alias, solver)
                    cached = _last_solve[(_state["alias"], routine)]
                markdown = build_report(
                    _ctx.system,
                    routine=routine,
                    family=routine_family(routine),
                    case_alias=_state["alias"],
                    case_label=_case_label(_state["alias"]),
                    solver=cached["solver"],
                    info=cached["info"],
                    results=cached["results"],
                    plot_urls=cached["plots"],
                )
            except Exception as exc:
                raise HTTPException(status_code=400,
                                   detail=_friendly_solve_error(exc, routine, _state["alias"]))
        return {
            "routine": routine,
            "case": _state["alias"],
            "markdown": markdown,
            "plots": cached["plots"],
        }

    @app.post("/api/chat")
    def chat(req: ChatRequest):
        message = (req.message or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="Empty message.")
        with _lock:
            return _agent_turn(message, req.routine, req.case, req.solver)

    @app.get("/api/llm")
    def llm_get():
        with _lock:
            return _llm_status()

    @app.post("/api/llm")
    def llm_set(req: LLMRequest):
        provider = (req.provider or "").strip().lower()
        if provider not in ("ollama", "openai"):
            raise HTTPException(status_code=400,
                                detail=f"Unknown provider '{req.provider}'.")
        with _lock:
            _llm_cfg["provider"] = provider
            _llm_cfg["model"] = (req.model or "").strip() or None
            _reset_agent()          # rebuild with the new provider/model next turn
            return _llm_status()

    # -------------------------------------------------------- static frontend
    # Result plots (read-only). Mounted before "/" so it takes precedence.
    if GENERATED_DIR.is_dir():
        app.mount("/generated", StaticFiles(directory=str(GENERATED_DIR)), name="generated")

    if FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

    return app


app = create_app()
