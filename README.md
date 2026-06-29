# LLM-LTB: AI-Enabled Power System Large-scale Testbed

## Update:
We are developing LLM for ANDES for transient stability modeling and simulation; LLM for AMS for scheduling modeling and simulation.

**Conversational LangGraph agent that drives [CURENT LTB AMS](https://github.com/CURENT/ams) — DCOPF, RTED, ED, UC, ACOPF — through natural-language commands.**

Built as a sibling to [`pv-curve-llm`](https://github.com/CURENT/pv-curve-llm); same multi-agent architecture, swapped domain engine (AMS / cvxpy / ANDES instead of pandapower).

---

## Table of Contents

- [Overview](#overview)
- [Phase 1 Routes (1-6)](#phase-1-routes-1-6)
- [Architecture](#architecture)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Usage Examples](#usage-examples)
- [LangGraph Workflow](#langgraph-workflow)
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

The agent uses a **LangGraph state machine** with a classifier → router → six route-specific nodes (Q&A, Discovery, Case I/O, Configure, Modify, Solve), plus a planner for compound queries like *"change load PQ_1 to 3.2 then solve"*.

**Technology stack**

- **Agent framework**: LangGraph + LangChain
- **LLMs**: Ollama (default, local) or OpenAI
- **Power simulator**: LTB AMS (`ltbams`) + ANDES + cvxpy
- **Solvers**: open-source CLARABEL, OSQP, SCS, HiGHS, SCIP, SCIPY (commercial GUROBI / MOSEK / CPLEX / COPT auto-detected if installed)

---

## Phase 1 Routes (1-6)

| Route | Node | Maps to AMS API | ex2 cell |
|---|---|---|---|
| **1 – Q&A** | `question_general` | concept Q&A: routines, constraints, cvxpy DCP | (educational) |
| **2 – Discovery** | `question_parameter` | `cvxpy.installed_solvers()`, `all_routines`, list loads / gens / lines, constraint ON/OFF | cell 64 |
| **3 – Case I/O** | `case_io` | `ams.load(ams.get_case(...))`, case info | cells 6, 50, 59 |
| **4 – Configure** | `configure` | active routine, solver, `routine.config.update(t=...)`, `routine.disable([...])`, `routine.enable([...])` | cells 69, 76, 81, 92 |
| **5 – Modify** | `modify` | `PQ.alter('p0', ...)`, `StaticGen.set('u', ...)`, `Line.alter('u', ...)`, `Line.set('rate_a', ...)` | cells 19, 41, 52, 61 |
| **6 – Solve** | `solve` | `routine.run(solver=...)` → `pg`, `plf`, `pd`, `obj` + bar-chart plots | cells 13, 23, 34, 45, 54, 66, 72, 78, 89, 96, 103 |

Routes 7-11 (multi-period temporal, ANDES co-sim, custom cvxpy formulations, exporting) are Phase 2 / 3.

---

## Architecture

```
┌─────────┐
│  START  │
└────┬────┘
     │
     ▼
┌────────────┐
│ CLASSIFIER │  → one of six message_type labels
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
     ├──► SOLVE ───────────────► END | ADVANCE_STEP | ERROR_HANDLER
     └──► PLANNER ─────────────► STEP_CONTROLLER ──► (any of the 6) ──► ADVANCE_STEP ──► SUMMARY ──► END

┌────────────────┐
│ ERROR_HANDLER  │ ─────► ADVANCE_STEP | END
└────────────────┘
```

### Core components

- **State** (`agent/state/app_state.py`): TypedDict LangGraph state holding messages, current `Inputs`, last `results`, plan / step counter, error info.
- **Inputs** (`agent/schemas/inputs.py`): pydantic model — `case_path`, `routine`, `solver`, `config_t`, `disabled_constraints`, `load_overrides`, `gen_off`, `line_off`, `line_rate_overrides`.
- **AMSContext** (`agent/ams_engine/engine.py`): thin object that owns one `ams.System` and exposes idempotent methods matching ex2's API (`load_case`, `alter_load_p0`, `set_gen_status`, `set_line_status`, `set_config_t`, `disable_constraints`, `solve`, …). Held by the SessionManager; bound into node closures (same pattern as `retriever` in pv-curve-llm).
- **Workflow** (`agent/workflows/workflow.py`): compiled LangGraph StateGraph wiring all 13 nodes.
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

## Quick Start

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

13 nodes wired in `agent/workflows/workflow.py`:

| Category | Nodes |
|---|---|
| Routing | `classifier`, `router` |
| Planning | `planner`, `step_controller`, `advance_step`, `summary` |
| Actions (Routes 1-6) | `question_general`, `question_parameter`, `case_io`, `configure`, `modify`, `solve` |
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
│   ├── nodes/                    # 13 nodes
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
│   │   └── solve.py              # Route 6
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
└── generated/                    # output plots (gitignored)
```

---

## Roadmap (Phase 2 & 3)

Phase 1 ships Routes 1-6 (single-period DCOPF / RTED workflows from ex1-ex4).
The following routes from the design table are deferred:

| Phase | Route | Function |
|---|---|---|
| 2 | **7 – Temporal** | multi-period ED / UC schedule, time-series plots |
| 2 | **8 – Scenario** | snapshot + compare two solve results (matches pv-curve Route 8) |
| 3 | **9 – ANDES** | `ss.to_andes(...)` co-simulation (AMS's unique feature) |
| 3 | **10 – Custom Formulation** | runtime cvxpy extension (ex8) |
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
