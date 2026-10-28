# LLM-AMS: AI-Powered Power System Scheduling

**Conversational LangGraph agent that drives [CURENT LTB AMS](https://github.com/CURENT/ams) — DCOPF, RTED, ED, UC, ACOPF — through natural-language commands.**

Built as a sibling to [`pv-curve-llm`](https://github.com/CURENT/pv-curve-llm); same multi-agent architecture, swapped domain engine (AMS / cvxpy / ANDES instead of pandapower).

---

## Table of Contents

- [Overview](#overview)
- [Phase 1 Routes (1-6)](#phase-1-routes-1-6)
- [Architecture](#architecture)
- [Installation](#installation)
- [Quick Start (Terminal CLI)](#quick-start-terminal-cli)
- [Web Platform (Local)](#web-platform-local)
- [Usage Examples](#usage-examples)
- [LangGraph Workflow](#langgraph-workflow)
  - [Route extensions (plugins)](#route-extensions)
- [Configuration](#configuration)
- [Project Structure](#project-structure)
- [Roadmap (Phase 2 & 3)](#roadmap-phase-2--3)
- [License](#license)

---

## Overview

LLM-AMS lets you ask things like

> *"Load case ieee14_uced, trip generator PV_1, then solve with SCS"*

…and have the agent translate that into the exact AMS API calls you'd otherwise write by hand from `examples/ex2.ipynb`:

```python
sp = ams.load(ams.get_case('ieee14/ieee14_uced.xlsx'), setup=True, no_output=True)
sp.StaticGen.set(src='u', idx='PV_1', attr='v', value=0)
sp.RTED.update()
sp.RTED.run(solver='SCS')
```

The agent uses a **LangGraph state machine** with a classifier → router → seven route-specific nodes (Q&A, Discovery, Case I/O, Configure, Modify, Formulate, Solve), plus a planner for compound queries like *"replace PV_1 with an IBR, then solve"*.

**Technology stack**

- **Agent framework**: LangGraph + LangChain
- **LLMs**: Ollama (default, local) or OpenAI
- **Power simulator**: LTB AMS (`ltbams`) + ANDES + cvxpy
- **Solvers**: open-source CLARABEL, OSQP, SCS, HiGHS, SCIP, SCIPY (commercial GUROBI / MOSEK / CPLEX / COPT auto-detected if installed)

---

## Routes (1-7)

| Route | Node | Maps to AMS API | ex2 cell |
|---|---|---|---|
| **1 – Q&A** | `question_general` | concept Q&A: routines, constraints, cvxpy DCP | (educational) |
| **2 – Discovery** | `question_parameter` | `cvxpy.installed_solvers()`, `all_routines`, list loads / gens / lines, constraint ON/OFF | cell 64 |
| **3 – Case I/O** | `case_io` | `ams.load(ams.get_case(...))`, case info | cells 6, 50, 59 |
| **4 – Configure** | `configure` | active routine, solver, `routine.config.update(t=...)`, `routine.disable([...])`, `routine.enable([...])` | cells 69, 76, 81, 92 |
| **5 – Modify** | `modify` | `PQ.alter('p0', ...)`, `StaticGen.set('u', ...)`, `Line.alter('u', ...)`, `Line.set('rate_a', ...)` | cells 19, 41, 52, 61 |
| **6 – Formulate** | `formulate` | reviewed IBR replacement template → new variables, constraints, bus mapping, and costs | structural extension |
| **7 – Solve** | `solve` | `routine.run(solver=...)` → `pg`, `pibr`, `pcurt`, `plf`, `pd`, `obj` + plots | cells 13, 23, 34, 45, 54, 66, 72, 78, 89, 96, 103 |

Additional temporal, scenario, ANDES co-simulation, formulation, and export capabilities remain Phase 2 / 3 work.

---

## Architecture

```
┌─────────┐
│  START  │
└────┬────┘
     │
     ▼
┌────────────┐
│ CLASSIFIER │  → one of seven message_type labels
└────┬───────┘
     │
     ▼
┌────────────┐
│   ROUTER   │  → simple single-route OR planner for compound
└────┬───────┘
     │
     ├──► QUESTION_GENERAL ────► END | ADVANCE_STEP
     ├──► QUESTION_PARAMETER ──► END | ADVANCE_STEP
     ├──► CASE_IO ─────────────► END | ADVANCE_STEP | ERROR_HANDLER
     ├──► CONFIGURE ───────────► END | ADVANCE_STEP | ERROR_HANDLER
     ├──► MODIFY ──────────────► END | ADVANCE_STEP | ERROR_HANDLER
     ├──► FORMULATE ───────────► END | ADVANCE_STEP | ERROR_HANDLER
     ├──► SOLVE ───────────────► END | ADVANCE_STEP | ERROR_HANDLER
     └──► PLANNER ─────────────► STEP_CONTROLLER ──► (any of the 7) ──► ADVANCE_STEP ──► SUMMARY ──► END

┌────────────────┐
│ ERROR_HANDLER  │ ─────► ADVANCE_STEP | END
└────────────────┘
```

### Core components

- **State** (`agent/state/app_state.py`): TypedDict LangGraph state holding messages, current `Inputs`, last `results`, plan / step counter, error info.
- **Inputs** (`agent/schemas/inputs.py`): pydantic model — `case_path`, `routine`, `solver`, `config_t`, `disabled_constraints`, `load_overrides`, `gen_off`, `line_off`, `line_rate_overrides`.
- **AMSContext** (`agent/ams_engine/engine.py`): thin object that owns one `ams.System` and exposes idempotent methods matching ex2's API (`load_case`, `alter_load_p0`, `set_gen_status`, `set_line_status`, `set_config_t`, `disable_constraints`, `solve`, …). Held by the SessionManager; bound into node closures (same pattern as `retriever` in pv-curve-llm).
- **Workflow** (`agent/workflows/workflow.py`): compiled LangGraph StateGraph wiring all 14 nodes.
- **Plotting** (`agent/ams_engine/plotting.py`): bar charts for `pg`, `plf`, `pd` saved under `generated/`.

---

## Installation

### Prerequisites

- Python ≥ 3.11 (tested on 3.12)
- [conda](https://docs.conda.io/) or any venv tool
- [Ollama](https://ollama.com/download) for local LLM (optional if you use OpenAI)

### Conda environment

```bash
conda create -n llm-ams python=3.12 -y
conda activate llm-ams
pip install -r requirements.txt
```

`requirements.txt` pulls in `ltbams` (which brings ANDES + cvxpy + kvxopt), LangChain stack, plus open-source solvers `highspy` + `pyscipopt`. cvxpy's bundled `CLARABEL`, `OSQP`, `SCS`, `SCIPY` are always available.

### Or with a plain virtualenv (no conda)

```powershell
# Windows PowerShell — from the project root (folder with main.py)
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> If PowerShell blocks `Activate.ps1` (execution policy), don't activate — just
> call the interpreter by path, e.g. `.\.venv\Scripts\python.exe main.py`.

### Ollama (default LLM)

```bash
ollama pull llama3.1:8b
```

Add a `.env` file (copy `.env.example`) if you want OpenAI:

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

### Verify

```bash
python -c "import ams, cvxpy; print(ams.__version__, cvxpy.installed_solvers())"
# 1.3.0 ['CLARABEL', 'SCS', 'SCIP', 'SCIPY', 'HIGHS', 'OSQP']
```

---

## Quick Start (Terminal CLI)

```bash
conda activate llm-ams
cd LLM_AMS
python main.py
```

You will be asked three things in order (each has a default — just hit Enter):

1. **Provider**: `openai` or `ollama` *(default ollama)*
2. **Routine**: any AMS class name (`RTED`, `DCOPF`, `ED`, `UC`, …) *(default RTED)*
3. **Solver**: shown filtered by your routine's compatibility *(default first in list)*

The agent then loads the **ex2 default case** `5bus/pjm5bus_demo.xlsx` and you're at the prompt.

---

## Web Platform (Local)

Besides the terminal CLI, LLM-AMS ships a **local web app** — a single-page browser dashboard with an **Assistant chat**, live **equation rendering** (MathJax), routine-aware **case data**, a one-click **solver**, and an auto-generated **power-system analysis report**.

### 1. Install web dependencies

The web app reuses the agent stack and adds **FastAPI + Uvicorn**:

```bash
# from the project root (the folder with main.py and web/)
pip install -r requirements.txt        # agent + AMS stack (enables the chat)
pip install -r web/requirements.txt    # adds fastapi + uvicorn[standard]
```

> **Viewer only** (no chat)? `pip install -r web/requirements.txt` alone is enough — the equation viewer, case tables, **Run solve** and **report** all work without the LangChain / LLM stack. The chat simply shows a friendly "install the LLM stack" notice.

### 2. (Optional) Choose the chat model

The Assistant uses the same LLM backend as the CLI. For local **Ollama**, pull a model:

```bash
ollama pull llama3.1:8b     # or another model, e.g. `ollama pull llama3.2`
```

Set a default before launching, or switch it live from the sidebar **Assistant model** dropdown:

```powershell
$env:OLLAMA_MODEL = "llama3.1:8b"   # Windows PowerShell
```

For **OpenAI**, put `OPENAI_API_KEY` in `.env` and pick *OpenAI* in the dropdown.

### 3. Launch the server

Run **from the project root** so the static frontend and the `generated/` plots resolve:

```bash
python -m uvicorn web.backend.main:app --host 127.0.0.1 --port 8000
```

On Windows using the bundled virtual-env, call the interpreter by path:

```powershell
.\.venv\Scripts\python.exe -m uvicorn web.backend.main:app --host 127.0.0.1 --port 8000
```

*(The first launch takes ~15-30 s while the AMS stack imports — wait for `Application startup complete`.)*

### 4. Open the app

### → http://127.0.0.1:8000

| Area | What it does |
|---|---|
| **Assistant** (top, full-width) | natural-language chat — *"switch to ieee14_uced and run UC"* |
| **Left bar** | grid case · routine · LLM provider / model |
| **Middle bar** | problem formulation (equations) + solve results |
| **Right bar** | generated analysis report |

Drag the divider under the Assistant (and the one left of the report) to change the split; **double-click** a divider to reset.

### API (served by the same process)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | liveness probe |
| `GET` | `/api/routines`, `/api/cases`, `/api/solvers/{routine}` | pickers |
| `GET` | `/api/formulation/{routine}` | LaTeX objective + constraints |
| `GET` | `/api/case?routine=&case=` | routine-aware case tables |
| `POST` | `/api/solve` | run a routine → objective + plots |
| `POST` | `/api/report` | build a Markdown analysis report |
| `POST` | `/api/chat` | one conversational agent turn |
| `GET` / `POST` | `/api/llm` | read / switch LLM provider + model |

### Which case works with which routine?

Economic routines (**RTED / ED / UC / DCOPF**) need generator-cost + reserve data — use **`pjm5bus_demo`** or any **`*_uced`** case (`ieee14_uced`, `ieee39_uced`, …). Plain topology cases (`ieee14`, `ieee39`, `case118`, `npcc`, `wecc`) only fit power-flow routines (**DCPF / PFlow**). Mixing them shows an actionable message telling you the fix.

---

## Usage Examples

### 1. Replay ex2.ipynb section "Change Load"

```
Message: Change load PQ_1 to 3.2 and PQ_2 to 3.2
  → modify: load PQ_1 p0 → 3.2 pu, load PQ_2 p0 → 3.2 pu

Message: Solve
  → solve: RTED with CLARABEL, obj = 0.846329
            pg = [0.20, 1.64, 0.60, 5.96, 2.00]   ← exact match with ex2
```

### 2. Replay ex2 section "Trip a Generator"

```
Message: Trip generator PV_1 then solve
  → planner: 2 steps
  → modify:  tripped generator PV_1
  → solve:   pg = [-0.00, 0.50, 0.60, 2.97, 0.33]   ← matches ex2 cell 47
```

### 3. Replay ex2 section "Disable Constraints"

```
Message: Disable plflb and plfub
  → configure: disabled plflb, disabled plfub

Message: Solve
  → solve: line-flow limits ignored; plf swings to ex2 cell 74 values
```

### 4. Switch routine / solver / grid case

```
Message: Switch routine to DCOPF
Message: Use solver SCS
Message: Load case ieee14_uced
Message: Solve
  → DCOPF on IEEE-14 with SCS, plots saved under generated/
```

### 5. Discovery

```
Message: What routines are available?
  → list of 33 routines grouped by family

Message: Which solvers can I use for UC?
  → ['SCIP', 'SCIPY']   ← only MIP-capable installed solvers
```

### 6. Pure Q&A

```
Message: What is RTED and how is it different from DCOPF?
  → educational answer from the AMS concepts reference
```

Type `quit` (or `q`) to exit.

---

## LangGraph Workflow

14 nodes wired in `agent/workflows/workflow.py`:

| Category | Nodes |
|---|---|
| Routing | `classifier`, `router` |
| Planning | `planner`, `step_controller`, `advance_step`, `summary` |
| Actions (Routes 1-7) | `question_general`, `question_parameter`, `case_io`, `configure`, `modify`, `formulate`, `solve` |
| Recovery | `error_handler` |

**Simple-query path**: `START → classifier → router → <one route> → END`.

**Multi-step path** (planner-triggered by "then", "and then", "compare", or change-then-solve patterns):

```
START → classifier → router → planner →
  step_controller → <route node> → advance_step →
    ... loop ...
  → summary → END
```

**Recovery**: any route node may attach `error_info`; the conditional edge routes to `error_handler`, which uses the LLM to explain the failure and resets retry state.

### Route extensions

A route can also live **outside this repository** as a plugin. It joins the agent like a
built-in route — the classifier can pick it, the planner can use it as a step, errors go to
`error_handler` — without editing the built-in routes, prompts or schemas
(`agent/extensions.py`). With no plugin configured the agent behaves exactly as before.

Known plugin: `ltb-casebuilder` (separate repository; see its README) — route `case_build`,
builds a month / year case from real regional hourly load (*"Build the IEEE39 case for July 2020 and
run ED"*).

#### Using a plugin

One-time setup, in this project's environment (`conda activate llm-ams`):

```bash
# 1. make the plugin importable (editable: edits to its code apply immediately)
python -m pip install -e /path/to/ltb-casebuilder --no-deps

# 2. enable it in LLM_AMS/.env (read by both the CLI and the web server);
#    several plugins: comma-separate the entries on that one line
echo 'LTB_EXTENSIONS=casebuilder.ltb_plugin:CASE_BUILD' >> .env
```

Then start the CLI (`python main.py`) or the web server as usual — no `export` needed.

| To … | Do |
|---|---|
| check it loaded | the plugin's route answers (e.g. *"Build the IEEE39 case for July 2020"*); a skipped plugin is logged in the terminal and shown once in the web chat as **⚠ Route extension not loaded — …** |
| turn it off for one run | `export LTB_EXTENSIONS=` before starting (a shell value overrides `.env`) |
| turn it off for good | delete the line from `.env`; `pip uninstall ltb-casebuilder` |
| use it without installing | `export PYTHONPATH=/path/to/ltb-casebuilder:.` and `export LTB_EXTENSIONS=...` before starting |

Web notes:

- Cases a plugin builds and loads get a **sidebar entry automatically** (named after the
  file, e.g. `ieee39_uced_NE_2020_07 · 744 h`), so the sidebar, **Run solve** and the report
  name the right case.
- These entries live in memory. After a server restart, a page that still names one finds
  the file again under `data/` / the case roots, or gets a clear "rebuild it" error — never a
  silently different case. Reloading the page resets the sidebar.
- `/api/chat` runs a parameter-control pre-check **before** the agent; with a small local
  model it may reject a plugin request before it reaches the plugin (reply: *"当前配置暂不支持此操作…"*).
  The CLI (`python main.py`) has no pre-check.

#### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| **⚠ Route extension not loaded — LTB_EXTENSIONS entry '…' skipped: could not be loaded: No module named '…'** | plugin not installed / moved — redo step 1, or fix the module name |
| **… skipped: must look like 'module:attribute'** | typo in `.env` — the value is `module:ATTRIBUTE` |
| **… skipped: not a RouteExtension** / **registered twice** / **is a built-in node name** | the attribute is wrong, listed twice, or the plugin's route name clashes |
| the plugin's reply is an error explanation | its node raised or returned no result; the explanation comes from `error_handler`, the cause is in the terminal log (`extension 'name': node raised …`) |
| **case '…' is not available on this server** | a case built before a server restart whose file is gone — ask for it again |

A broken plugin never takes the built-in routes down: a failing `LTB_EXTENSIONS` entry is
skipped, an exception in its node goes to `error_handler`, an exception in its `is_change`
counts as "no". Extensions passed explicitly in code are validated strictly instead.

#### Writing a plugin

A plugin exposes one `RouteExtension` (see `ltb-casebuilder/casebuilder/ltb_plugin.py` for a
complete example):

```python
from agent.extensions import RouteExtension

MY_ROUTE = RouteExtension(
    name="my_route",                          # lower snake case, not a built-in name
    node=my_route_agent,                      # (state, llm, prompts, ams_ctx) -> dict
    summary="one line for the classifier schema",
    classifier_hint='What it does. Examples: "...", "..."',
    planner_hint="optional rule for the planner",
    prompts={"my_route_extract": {"system": "..."}},   # optional, merged into prompts
    is_change=lambda text: ...,               # optional: counts as change-then-solve
)
```

- The node has the same contract as the built-in route nodes: return a dict with
  `messages` / `node_response` (and `inputs` if it changes the session), or `error_info` +
  `failed_node` for an expected failure. Talk to AMS only through `ams_ctx` (`AMSContext`).
- Register explicitly with `create_workflow(llm, prompts, ams_ctx, extensions=[MY_ROUTE])`
  (tests), or through `LTB_EXTENSIONS` as above. `extensions=()` ignores the environment.
- Tests for the mechanism: `python -m pytest test_files/test_route_extensions.py -q`.

#### Prompt sensitivity

Hint text is inserted into the built-in prompts (classifier bullets before "Pick the
BEST…", planner actions into the action list, planner rules as the next numbered rule).
Small local models are sensitive to it: with `llama3.1:8b`, one wording of a planner
rule turned "Change load PQ_1 to 3.2 then solve" from `modify → solve` into
`formulate → solve`. After writing or changing a plugin's hints, re-run a few built-in
compound requests with and without it and check that the plans match.

---

## Configuration

### Environment variables (`.env`)

```env
# Default — local Ollama
OLLAMA_MODEL=llama3.1:8b
OLLAMA_BASE_URL=http://localhost:11434

# Optional — OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# Where to drop generated plots
AMS_OUTPUT_DIR=generated

# Optional — route-extension plugins (see "Route extensions")
LTB_EXTENSIONS=casebuilder.ltb_plugin:CASE_BUILD
```

### Adding commercial solvers

```bash
# GUROBI (license required)
pip install gurobipy

# MOSEK (license required)
pip install mosek
```

Both are auto-detected via `cvxpy.installed_solvers()` and will appear in the solver-selection prompt for compatible routines (UC, ACOPF, large-scale DCOPF).

### Adding cases

Drop any AMS-readable file (`.xlsx`, `.json`, `.raw`, MATPOWER `.m`) into AMS's `cases/` folder, or pass an absolute path:

```
Message: Load case /Users/me/my_grid.xlsx
```

### Custom banner

The startup banner is an ASCII-art string in
[`agent/utils/display.py`](agent/utils/display.py) (constant `_BANNER_LINES`,
ANSI-Shadow font). Edit those lines to change the wordmark.

---

## Project Structure

```
LLM_AMS/
├── main.py                       # entry point
├── cli.py                        # terminal interface (provider → routine → solver prompts)
├── requirements.txt
├── .env.example
│
├── agent/
│   ├── core.py                   # LLM + AMSContext + graph factory
│   ├── session.py                # SessionManager (bootstraps default case, streams turns)
│   ├── prompts.py                # all system / user prompts
│   │
│   ├── workflows/
│   │   └── workflow.py           # compiled LangGraph
│   │
│   ├── nodes/                    # 14 nodes
│   │   ├── classify.py
│   │   ├── route.py
│   │   ├── planner.py
│   │   ├── step_controller.py
│   │   ├── advance_step.py
│   │   ├── summary.py
│   │   ├── error_handler.py
│   │   ├── question_general.py   # Route 1
│   │   ├── question_parameter.py # Route 2
│   │   ├── case_io.py            # Route 3
│   │   ├── configure.py          # Route 4
│   │   ├── modify.py             # Route 5
│   │   ├── formulate.py          # Route 6
│   │   └── solve.py              # Route 7
│   │
│   ├── state/
│   │   └── app_state.py          # TypedDict
│   │
│   ├── schemas/                  # pydantic structured-output schemas
│   │   ├── inputs.py
│   │   ├── classifier.py
│   │   ├── parameter.py
│   │   ├── planner.py
│   │   └── response.py
│   │
│   ├── ams_engine/               # the AMS-side "engine" (mirrors pv_curve/)
│   │   ├── engine.py             # AMSContext: live ams.System wrapper
│   │   ├── routines.py           # routine ↔ solver compatibility
│   │   └── plotting.py           # pg / plf / pd bar charts
│   │
│   └── utils/
│       ├── common_utils.py
│       ├── context.py
│       └── display.py            # rich banner / parameter table / streaming
│
├── web/                          # local web platform (see "Web Platform (Local)")
│   ├── backend/                  # FastAPI app: main.py + report.py + case_data.py
│   ├── frontend/                 # index.html · app.js · styles.css (MathJax)
│   ├── requirements.txt          # fastapi + uvicorn[standard] (+ ltbams)
│   ├── Dockerfile
│   └── docker-compose.yml
│
└── generated/                    # output plots (gitignored)
```

---

## IBR formulation edit: live example

Use the `llm-ams` environment, start Ollama, and select **DCOPF** with **CLARABEL**
in either `python main.py` or the web app. With the default PJM 5-bus demo loaded:

```text
Replace PV_1 with an IBR with available power 1 pu, dispatch cost 1 dollar per pu, curtailment penalty 20 dollars per pu.
Solve
```

The `FormulationEdit` schema feeds the deterministic `formulate` compiler. It
sets the original generator offline, adds `pibr` and `pcurt`, connects the IBR
injection at the same bus, enforces available-power balance and bounds, and
adds dispatch and curtailment costs. Compilation happens on an isolated copy;
a failure leaves the live model intact. The compiled CVXPY problem must be DCP.

Current scope: **one replacement in single-period nodal DCOPF**. This is an
active-power scheduling equivalent, not an inverter dynamics or reactive-power
model. Reload the case to remove the edit. Other routines and duplicate edits
are rejected. Supply exact generator IDs and pu quantities; ambiguous IDs or
missing capacity require clarification. Omitted cost coefficients default to
zero and are shown in the response.

Validation, without an LLM or an external API:

```bash
python _validate_formulate.py --json ../artifacts/live-validation.json
```

The nine live checks cover an actual AMS baseline and edited solves, a separately
assembled SciPy/HiGHS LP, alternate device mapping, rollback and invalid inputs,
penalty sensitivity, injected result errors, and HTTP solve/formulation output.
Expected example: objective approximately **9.674966453**, `pibr` approximately
**1 pu**, `pcurt` approximately **0 pu**. The separate HiGHS model gives
**9.674966451**. These tests verify this template and example, not arbitrary
LLM-generated formulations. The natural-language CLI and web runs additionally
require Ollama. Captured execution evidence is in `../artifacts/evidence/`.

---

## Configuration-backed web parameters

`config/formulation_controls.json` is the shared capability catalog for the
left Parameters panel and the top Assistant prompt. The backend projects the
global catalog into `available_controls` for the currently selected routine and
case, so DCPF/PFlow, ACOPF, ED, UC and DCOPF can expose different internal
parameters from the same file. The selected Ollama/OpenAI LLM receives the same
routine-aware catalog and live device IDs and returns a structured operation plan.
Both paths use the same validated, atomic executor; failed plans leave the
live model unchanged. No arbitrary LLM-generated code or expressions execute.

Supported controls: PQ-device active-load scaling, line outage/restoration, generator
status, line thermal rating, routine interval `config.t` for time-based routines,
existing constraint enable/disable, additional linear dispatch cost and total-generation
cap for DCOPF, ED and UC, and the DCOPF IBR replacement template (new variables,
objective terms and constraints). ED/UC cost and cap sum generation over every time slot;
the cap is a horizon total, not a separate limit on each slot. Unsupported or
ambiguous requests return a message without changes. Structural templates are
bounded; arbitrary new variables and constraints are not supported.

Each catalog control declares `effect: scenario` or `effect: formulation`. Formulation
controls with new objective terms, constraints or variables also name a reviewed
`template`. The backend checks that declaration against its compiler before exposing
the control. The Parameters panel groups scenario inputs and formulation changes.
For each successful edit, the API returns an audit receipt with the LLM or manual
plan and values read back from the live AMS model; a mismatch aborts the entire
batch. The panel displays these values after an edit. For a single direct numeric
prompt such as "set the dispatch cost to 0.9", the backend also compares an explicit
number in the user's text with the LLM's planned value. The receipt verifies model
state, while the text check covers that common parsing error. More complex requests
still require a review of the planned operation and target.

Load changes scale **current p0**, leave q0 unchanged, and require an online,
positive PQ load device, regardless of its connected bus type (PQ, PV or slack).
The default PJM demo supports PQ_1, PQ_2 and PQ_3. For example, request
`increase load PQ_1 by 10%`. In IEEE 14-bus (or its UC/ED variant for economic solves), try:
`increase the load at bus 4 by 10%` or `trip line Line_2` in the top Assistant.
Use the displayed exact indices for other cases. Outages accumulate: cutting
one line from an intact case is N-1; further cuts are N-k. Restore lines using
the Online checkbox, or Reset the whole case. The slider shows a signed percentage;
its Min/Max inputs set the interaction range (initially -20% to +20%). Decreases
cannot exceed 100%. Valid range changes take effect immediately; narrowing a
range clamps the slider value. Ranges survive parameter refreshes in the current
page. Only edited parameters are submitted by Apply Changes.

In DCOPF with PJM demo, try `Add an extra dispatch cost of 5 dollars per pu`
or `Add a constraint limiting total generation to 15 pu`. The live formulation
shows these changes and subsequent solves use them. IBR and the other structural
templates are mutually exclusive until Reset. Reset reloads the case, clears
edits/results and refreshes parameters. State is in memory, shared by tabs on
this single-user development server, and is not saved into the source case.

To extend the catalog, set `enabled`, supported exact `routines` or
`routine_families`, descriptions, `prompt_examples`, and typed `parameters`
(select source, boolean, number or range, validation limits, defaults and
slider_min/slider_max). Labels, routine applicability and limits require only
JSON changes; a new operation also needs a reviewed executor branch in
`web/backend/parameter_controls.py` and a validation test. JSON never contains
executable code. Both API and UI respect disabled entries.

Run `python _validate_controls.py` for integration tests covering actual
AMS solves, PQ eligibility, rollback, configuration switches, objective/constraint
effects, routine-specific catalogs, IBR validation and HTTP state synchronization.
LLM output is mocked in that repeatable suite; a live prompt additionally requires the selected model.
Run the web server with `python -m uvicorn web.backend.main:app --host 127.0.0.1 --port 8000`.

## Roadmap (Phase 2 & 3)

The current build ships Routes 1-7, including a bounded structural IBR replacement template for single-period nodal DCOPF.
The following routes from the design table are deferred:

| Phase | Route | Function |
|---|---|---|
| 2 | **7 – Temporal** | multi-period ED / UC schedule, time-series plots |
| 2 | **8 – Scenario** | snapshot + compare two solve results (matches pv-curve Route 8) |
| 3 | **9 – ANDES** | `ss.to_andes(...)` co-simulation (AMS's unique feature) |
| 3 | **10 – Additional Formulations** | storage, reactive-power, virtual-inertia, and other reviewed templates |
| 3 | **11 – Export** | dump case to xlsx / JSON / MATPOWER |

---

## License

This project follows the licenses of its dependencies:
- LangChain / LangGraph: MIT
- LTB AMS: GPL-3.0
- cvxpy: Apache-2.0
- ANDES: GPL-3.0

---

## Acknowledgments

- **[CURENT LTB](https://ltb.curent.org/)** — the testbed AMS is part of
- **[pv-curve-llm](https://github.com/CURENT/pv-curve-llm)** — the architecture this project mirrors
- **[LTB AMS](https://github.com/CURENT/ams)** — the scheduling simulator being driven
- **[LangChain / LangGraph](https://github.com/langchain-ai/langgraph)** — agent framework
- **[Ollama](https://ollama.com/)** — local LLM runtime
