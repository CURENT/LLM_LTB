"""Structured-output schemas for parameter/modification extraction."""

from typing import List, Optional, Union
from typing_extensions import Literal
from pydantic import BaseModel, ConfigDict, Field


# --- Route 4: Configure ---
class ConfigureChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: Literal[
        "case_path",          # change loaded case (also routes 3)
        "routine",            # active routine (RTED, DCOPF, ED, UC, ...)
        "solver",             # cvxpy solver
        "config_t",           # routine.config.t in hours
        "disable_constraint", # routine.disable([...])
        "enable_constraint",  # routine.enable([...])
    ] = Field(..., description="Which configuration target to update")

    value: Union[str, float, int] = Field(
        ...,
        description=(
            "New value. For routine/solver/case_path: the name string. "
            "For config_t: a number in hours. "
            "For disable_constraint / enable_constraint: the constraint name (e.g. 'plflb')."
        ),
    )


class ConfigureChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: List[ConfigureChange] = Field(default_factory=list)


# --- Route 5: Modify physical system ---
class SystemModification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: Literal[
        "load_p0",      # PQ.alter p0
        "gen_trip",     # StaticGen u -> 0
        "gen_restore",  # StaticGen u -> 1
        "line_trip",    # Line u -> 0
        "line_restore", # Line u -> 1
        "line_rate",    # Line rate_a
    ] = Field(..., description="What to change in the physical system")

    idx: str = Field(
        ...,
        description=(
            "AMS element idx, e.g. 'PQ_1' for a load, 'PV_1' for a generator, "
            "'Line_2' for a line."
        ),
    )

    value: Optional[float] = Field(
        default=None,
        description="Numeric value for load_p0 (pu) or line_rate (pu). Omit for trip/restore.",
    )


class SystemModifications(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modifications: List[SystemModification] = Field(default_factory=list)


class FormulationEdit(BaseModel):
    """A bounded, reviewable change to an optimization formulation."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    operation: Literal["replace_generator_with_ibr"] = "replace_generator_with_ibr"
    generator_idx: str = Field(..., description="AMS StaticGen idx to replace, e.g. PV_1")
    available_power: float = Field(..., gt=0, description="Maximum available IBR power in pu")
    dispatch_cost: float = Field(default=0.0, ge=0, description="IBR dispatch cost in $/pu")
    curtailment_cost: float = Field(default=0.0, ge=0, description="IBR curtailment penalty in $/pu")


class FormulationEdits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edits: List[FormulationEdit] = Field(default_factory=list, max_length=1)
    clarification: Optional[str] = Field(default=None, description="Ask for missing or ambiguous target, power or cost information")
