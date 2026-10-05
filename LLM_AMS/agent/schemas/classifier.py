"""Classifier output: which Route 1-6 should handle this turn."""

from typing_extensions import Literal
from pydantic import BaseModel, ConfigDict, Field


class MessageClassifier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_type: Literal[
        "question_general",   # Route 1: Q&A about AMS / scheduling concepts
        "question_parameter", # Route 2: discovery — what cases/routines/solvers exist
        "case_io",            # Route 3: load / inspect / export a case file
        "configure",          # Route 4: change routine, solver, config_t, enable/disable constraints
        "modify",             # Route 5: change load p0, trip gen/line, change line rate
        "formulate",          # Route 6: bounded objective/variable/constraint edits
        "solve",              # Route 7: run the active routine
    ] = Field(
        ...,
        description=(
            "Classify into one route: question_general (concepts), "
            "question_parameter (discovery of available cases/routines/solvers), "
            "case_io (load/inspect/export case file), "
            "configure (routine/solver/config/constraint enable-disable), "
            "modify (load p0, trip gen/line, line rate), formulate "
            "(replace a generator with an IBR and add its formulation), "
            "solve (run the active routine). For a message that mixes MULTIPLE "
            "actions, pick the LAST action here and set is_multi_step=true."
        ),
    )

    is_multi_step: bool = Field(
        default=False,
        description=(
            "True when the message asks for MORE THAN ONE action that must run "
            "in sequence — e.g. load/switch a case AND run it, change a value "
            "AND solve, or 'do A then B'. Works in any language (English or "
            "Chinese). Examples that are multi-step: 'switch to ieee39 and run "
            "UC', '换成ieee39节点，运行uc问题', 'change load PQ_1 to 3.2 then "
            "solve'. A single action (just 'run RTED', just 'load ieee14') is "
            "NOT multi-step."
        ),
    )
