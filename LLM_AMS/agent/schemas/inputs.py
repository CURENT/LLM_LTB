"""Pydantic input schema for the LLM-AMS session.

Mirrors the ex2.ipynb workflow:
- case_path: which AMS case file is loaded
- routine: which scheduling routine is active (RTED, DCOPF, ED, UC, ...)
- solver: which cvxpy backend to use
- config_t: time interval for RTED/ED (hours)
- disabled_constraints: routine constraints turned off via routine.disable(...)
- load_overrides / gen_off / line_off / line_rate_overrides:
  a record of the in-session edits the modify node has already applied to the
  live AMS System. They are NOT re-applied anywhere: nothing reads them back on
  solve. They are reset-only — cleared when a case is (re)loaded
  (agent/nodes/case_io.py, web/backend/main.py) — and shown to the LLM as
  context for the next modify request.
"""

from typing import Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class Inputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # ex2 defaults: pjm 5-bus case + RTED + CLARABEL
    case_path: str = "5bus/pjm5bus_demo.xlsx"
    routine: str = "RTED"
    solver: str = "CLARABEL"

    # RTED / ED time interval in hours (ex2 default 5/60)
    config_t: Optional[float] = Field(default=None)

    # routine.disable / enable
    disabled_constraints: List[str] = Field(default_factory=list)

    # record of PQ.alter(src='p0', ...) edits already applied — keyed by PQ idx; reset-only
    load_overrides: Dict[str, float] = Field(default_factory=dict)

    # StaticGen.set(src='u', idx=..., value=0|1) — set of gen idx currently off
    gen_off: List[str] = Field(default_factory=list)

    # Line.alter(src='u', idx=..., value=0|1) — set of line idx currently off
    line_off: List[str] = Field(default_factory=list)

    # Line.alter / set rate_a — keyed by line idx
    line_rate_overrides: Dict[str, float] = Field(default_factory=dict)
