# LLM-AMS — Build Progress & Resumption Notes

**Last updated**: 2026-05-25, near end of session (usage limit).
**Working dir**: `/Users/jyin/Documents/work/LLM_AMS/LLM_AMS`
**Conda env**: `llm-ams` at `/Users/jyin/miniforge3/envs/llm-ams` (Python 3.12, ltbams 1.3.0, andes 2.0.0, cvxpy 1.9.0)
**Installed cvxpy solvers**: `CLARABEL, SCS, SCIP, SCIPY, HIGHS, OSQP`.

---

## ✅ Completed work

### Phase 1: scaffold + routes 1-6 (tasks #1-7, all done)
- Conda env `llm-ams` created, all deps installed (`ltbams`, LangGraph, LangChain, Ollama, OpenAI, PIL→removed, pyfiglet, pexpect)
- Mirrored pv-curve-llm-master architecture into `LLM_AMS/`
- 6 routes implemented:
  | Route | Node file | Purpose |
  |---|---|---|
  | 1 question_general | `agent/nodes/question_general.py` | concept Q&A about AMS / scheduling |
  | 2 question_parameter | `agent/nodes/question_parameter.py` | discovery — routines / solvers / loads / gens |
  | 3 case_io | `agent/nodes/case_io.py` | load / inspect case file or alias |
  | 4 configure | `agent/nodes/configure.py` | routine, solver, config_t, enable/disable constraints |
  | 5 modify | `agent/nodes/modify.py` | load p0, gen on/off, line on/off, line rate_a |
  | 6 solve | `agent/nodes/solve.py` | run active routine + plots + constraint check |
- LangGraph workflow: classifier → router → {one of 6 routes OR planner→step_controller→{routes}} → END
- AMSContext singleton (`agent/ams_engine/engine.py`) holds the live `ams.System` and mirrors ex2.ipynb mutations.

### UI redesign (task #8)
- `agent/utils/theme.py` — `BRAND_GREEN = "rgb(51,118,31)"`, symbols `[?] [i] [✓] [✗] ❯`
- ASCII banner via pyfiglet ANSI Shadow (no more PIL/PNG logo) → `_BANNER_LINES` in `agent/utils/display.py`
- `display_routines_table()` — 5-row table (ACED / DCED / DCUC / DED / PF) + docs URL
- `display_solver_table()` — solver list with descriptions + default marker
- `choice_prompt()` — `[?]` blue prompt for selections
- `display_status_bar()` — sticky one-line bar `case · routine · solver · mods` above each turn's prompt

### Markup bug + interactive verify (task #9)
- `[i]`, `[✓]`, `[✗]` were being eaten by Rich's italic tag parser → fixed by using `Text` + `Text.from_markup` instead of f-strings with markup
- `info(msg)`, `ok(msg)`, `fail(msg)` now support inline rich markup like `[value]foo[/]`
- Added input hint line above `❯` prompt
- pexpect test `/tmp/test_llm_ams_interactive.py` drives a real PTY through 7 turns; all pass.

### UC multi-period crash fix (task #10)
- Root cause: AMS multi-period routines (UC, ED) return `pg`/`plf` as 2-D `(n_devices, n_periods)` arrays. The 1-D bar plot crashed with broadcast error.
- Fixed `agent/ams_engine/plotting.py`: bar chart for 1-D, line chart for 2-D, per-variable try/except
- Fixed `agent/nodes/solve.py`: `_fmt_1d` / `_fmt_2d` for the markdown response; wraps `plot_results` in try/except so a plot failure can't hide the solver output
- pexpect test `/tmp/test_uc_solve.py` confirms `obj=430.860409`, 24-period line chart generated

### Routine-aware UI (task #11, **IN PROGRESS**)
- `agent/ams_engine/formulations.py` — Unicode-art math boxes for DCOPF, RTED, ED, UC, ACOPF, DCPF, PFlow, DOPF
- `agent/ams_engine/snapshots.py` — per-family data tables (DCED / DCUC / ACED / DED / PF), pulling cost (GCost), ramps (StaticGen.R10), commitment params (td1/td2/ton0/toff0)
- `agent/ams_engine/constraint_check.py` — post-solve violation panel (Gen vs Pmin/Pmax, line loading %, reserve totals, balance residual) with severity tags OK / LOW / WARN / VIOLATION
- `agent/utils/display.py` — `display_snapshot`, `display_formulation`, `display_constraint_check`; prompt label changed to `❯ Message ▸`; auto-help on first turn (`hint=is_first_turn` flag)
- `cli.py` — calls `display_snapshot` + `display_formulation` after bootstrap; tracks `last_case` / `last_routine` and re-displays when either changes after a turn
- `agent/nodes/solve.py` — calls `check_constraints` + `display_constraint_check` after each solve

---

## 🔄 Where we left off (resume here)

**Status**: Task #11 implementation is COMPLETE. Only the pexpect end-to-end verification is in flight.

**Last test run** (`/tmp/test_routine_aware.py`):
```
>>> [OK] saw 'Conversational scheduling agent'  (banner subtitle)
>>> [OK] saw 'Which model provider'  (provider prompt)
>>> [OK] saw 'Available routines'  (routines table)
>>> [OK] saw 'Which routine'  (routine prompt)
>>> [OK] saw 'Which solver'  (solver prompt)
>>> [OK] saw 'Loaded'  (bootstrap)
>>> [OK] saw 'DCED — DC Economic Dispatch focus'  (snapshot heading)
>>> [OK] saw 'Generators — dispatch inputs (DCED)'  (snapshot table)
!!! TIMEOUT  ← waiting for "Real-Time Economic Dispatch  (RTED)"  (2-space match was too strict)
```

I edited the test to match shorter substring `"Real-Time Economic Dispatch"` (without the parenthetical) but **never re-ran it**.

### Exact next steps on resume

```bash
# 1) Re-run the routine-aware pexpect test
/Users/jyin/miniforge3/envs/llm-ams/bin/python /tmp/test_routine_aware.py 2>&1 \
  | sed 's/\x1b\[[0-9;]*m//g' \
  | grep -E ">>>|TIMEOUT|EOF|ALL ROUTINE|Constraint check" | head -30

# Expected if everything works:
#   >>> [OK] saw 'Conversational scheduling agent'  (banner subtitle)
#   >>> [OK] saw 'Which model provider'  ...
#   ... (13 checks total)
#   >>> [OK] saw 'Constraint check'  (constraint check panel)
#   >>> ALL ROUTINE-AWARE CHECKS PASSED
```

If it still times out at the math-box step:
- Look at raw output, search for the actual rendered text. Rich may have wrapped or differently formatted "Real-Time Economic Dispatch" — adjust the needle in `/tmp/test_routine_aware.py` accordingly.
- Likely needles to try: `"DCOPF  +  Secondary Frequency Reserve"`, or the box-drawing characters `╔══`.

After the test passes, mark task #11 completed.

### Possible follow-ups the user mentioned but I haven't done

1. **The `?` help-shown-once auto-mechanism** is implemented; the user requested that the hint appear once after selecting a solver — DONE (`cli.py` calls `display_help()` after `display_initial_case_summary`).

2. **Solver-log highlighting** ("Constraint Check" violations panel after solver log) — partially done. The panel renders, but the user's example had specific items like "Bus 17 voltage 0.913 pu VIOLATION" which only show for ACOPF (since DC routines don't have bus voltages). For the 5-bus / RTED demo, the panel will likely be near-empty or show only the power-balance line. If the user wants more aggressive violation surfacing, extend `check_constraints` to also flag `pglb` / `pgub` / `plflb` / `plfub` direct dual values from `ss.RTED.constrs`.

3. **README** still needs an update to mention the new routine-aware data display + formulation + constraint check. (Search for the "Custom banner" section and add a "Routine-aware data presentation" section above.)

---

## 📁 Files modified or created this session

### Created
- `LLM_AMS/agent/ams_engine/formulations.py` — math boxes
- `LLM_AMS/agent/ams_engine/snapshots.py` — routine-aware data tables
- `LLM_AMS/agent/ams_engine/constraint_check.py` — violation panel
- `LLM_AMS/agent/utils/theme.py` — colors / symbols
- (`LLM_AMS/agent/utils/logo.py` — created earlier with PIL, then **deleted** when reverting to ASCII)
- `LLM_AMS/PROGRESS.md` — this file
- `/tmp/test_llm_ams_interactive.py` — 7-turn RTED scenario
- `/tmp/test_uc_solve.py` — UC scenario
- `/tmp/test_routine_aware.py` — routine-aware features scenario  ← **needs final re-run**

### Modified (most recently)
- `LLM_AMS/agent/utils/display.py` — banner, prompt `❯ Message ▸`, snapshot/formulation/constraint-check renderers, markup-safe `info/ok/fail`
- `LLM_AMS/cli.py` — calls snapshot + formulation after bootstrap and on routine/case change, auto-help, first-turn hint flag
- `LLM_AMS/agent/nodes/solve.py` — 2D-array handling, plot try/except, calls `check_constraints` + `display_constraint_check`
- `LLM_AMS/agent/ams_engine/plotting.py` — 1-D bar / 2-D line dispatch, per-var error isolation
- `LLM_AMS/README.md` — banner section updated

### User-edited (do NOT revert)
- `LLM_AMS/agent/prompts.py` — added per-unit / MW conversion rule to `MODIFY_EXTRACT_SYSTEM`
- `LLM_AMS/agent/ams_engine/engine.py` — uses `ss.StaticGen.get_all_idxes()` instead of `.idx.v` for gen lists (handles aggregated PV/Slack views correctly)

### Other key files (not touched recently)
- `LLM_AMS/agent/workflows/workflow.py` — LangGraph wiring of all 6 routes + planner
- `LLM_AMS/agent/core.py` — `setup_dependencies(provider)` → (llm, prompts, ams_ctx)
- `LLM_AMS/agent/session.py` — SessionManager.bootstrap() + execute_turn_streaming
- `LLM_AMS/agent/nodes/{classify,route,planner,step_controller,advance_step,summary,error_handler}.py`
- `LLM_AMS/agent/schemas/{inputs,classifier,parameter,planner,response}.py`
- `LLM_AMS/agent/state/app_state.py`
- `LLM_AMS/agent/utils/{common_utils,context}.py`
- `LLM_AMS/agent/ams_engine/routines.py` — family classification + compatible solvers
- `LLM_AMS/requirements.txt`, `LLM_AMS/main.py`, `LLM_AMS/README.md`

---

## 🐛 Known issues / open questions

1. **Test needle mismatch (task #11)** — see "Exact next steps" above. Easy fix once you actually re-run.

2. **`SCIPY` solver not actually compatible with UC at full strength** — AMS marks SCIPY as MIP-capable but it's HiGHS-via-scipy and may struggle on larger UC. For 5-bus UC, SCIP is the right default. The solver table already puts SCIP first.

3. **Slack_4 idx classification quirk** — pv-curve uses 1-based indexing; AMS uses string idx like `Slack_4`. Our code passes idx strings throughout. Confirmed working in the 7-turn test.

4. **Constraint-check panel is currently DC-only** — bus voltage limits only matter for ACOPF/PFlow. The panel checks: gen Pmin/Pmax, line loading vs rate_a, balance residual, RTED reserve totals. Add bus-V check when ACOPF is enabled in Phase 2.

5. **plotting matplotlib** uses default GUI backend on some macOS setups — if you see backend warnings, set `matplotlib.use('Agg')` at the top of `plotting.py`.

---

## 🧪 Tests available

| Script | Scenario | Status |
|---|---|---|
| `/tmp/test_llm_ams_interactive.py` | 7-turn RTED end-to-end (default solve, modify load, change solver/routine/case) | ✅ PASS |
| `/tmp/test_uc_solve.py` | UC + SCIP, multi-period 2D handling | ✅ PASS |
| `/tmp/test_routine_aware.py` | banner, snapshot, formulation, constraint check, prompt label | ⚠️ 8/13 confirmed; needle for "Real-Time Economic Dispatch" was loosened but not re-run |

All tests use a real PTY (`pexpect.spawn` with `dimensions=(40, 160)`), `COLORTERM=truecolor`, `TERM=xterm-256color`.

---

## ▶️ How to run

```bash
# Activate env + start the CLI
conda activate llm-ams
cd /Users/jyin/Documents/work/LLM_AMS/LLM_AMS
python main.py

# Defaults: Ollama (llama3.1:8b) → RTED → CLARABEL → 5bus/pjm5bus_demo.xlsx
# At the ❯ Message ▸ prompt, try:
#   "What is RTED?"
#   "List solvers compatible with UC"
#   "Change load PQ_1 to 3.2 and PQ_2 to 3.2"
#   "Trip PV_1 then solve"
#   "Switch routine to UC"   ← this should auto-redisplay UC snapshot + math
#   "solve"
#   "?"                       ← help
#   "quit"
```
