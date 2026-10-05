"""Multi-step plan schema for compound queries like 'change load PQ_1 to 3.2 then solve'."""

from typing import List, Optional
from typing_extensions import Literal
from pydantic import BaseModel, ConfigDict, Field


class StepType(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "question_general",
        "question_parameter",
        "case_io",
        "configure",
        "modify",
        "formulate",
        "solve",
    ] = Field(..., description="Which route handles this step")

    content: str = Field(..., description="Natural-language instruction for the route to execute")


class MultiStepPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(..., description="One-line summary of what the whole plan accomplishes")
    steps: List[StepType] = Field(..., description="Ordered steps to execute")
