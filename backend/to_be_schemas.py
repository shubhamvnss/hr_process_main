"""
backend.to_be_schemas - the To-Be State Workflow Designer contract.

Mirrors the pattern used by root_cause_schema.py: a Pydantic layer that validates the model's
answer, plus a hand-written strict JSON schema passed to the Responses API so the model is
*constrained* to the shape rather than merely asked for it. The two are kept side by side and not
shared with the other contracts, so each can evolve independently.

The model is never allowed to invent an activity. Every ToBeActivity is anchored to exactly one row
of the representative case's real event log via `current_sequence`; that anchoring is validated in
backend/agents/to_be_workflow_agent.py, not here, because it depends on how many current-state rows
this particular case has.

CHANGE_TYPES deliberately collapses the dispositions into six. "Remains a human activity" and
"Retained" are not two different things for a step that was already human - the distinction that
matters is whether the step CHANGES:

    Retained                  unchanged - continues exactly as it runs today
    Human                     stays with a person, but the step itself is redesigned
    AI Agent                  performed by one of the already-recommended agents
    Automated (Deterministic) automated by a rule or integration, no agent judgement needed
    Combined                  merged with another step (same to_be_sequence marks the merge)
    Eliminated                no longer needed at all

`human_in_the_loop` is not a disposition - it is a flag that can sit on top of any change type,
and it is what keeps a candidate-facing or approval step under human control even when an agent
prepares it.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

CHANGE_TYPES = [
    "Retained",
    "Automated (Deterministic)",
    "AI Agent",
    "Combined",
    "Eliminated",
    "Human",
]

NO_SYSTEM_IN_LOG = "Client API / integration dependency - system not present in event log"

# Dispositions that count towards each headline number the design has to report.
AUTOMATED_TYPES = ("AI Agent", "Automated (Deterministic)")
REDUCING_TYPES = ("Eliminated", "Combined")


class ToBeActivity(BaseModel):
    """One current-state activity, re-designed.

    Rows sharing a `to_be_sequence` are consolidated into a single future-state activity
    (change_type "Combined").
    """
    model_config = ConfigDict(extra="forbid")

    current_sequence: int = Field(
        description="Matches one row's `sequence` in the current-state payload")
    current_activity: str = Field(description="The real activity name from the event log")
    to_be_sequence: int
    to_be_activity: str
    change_type: Literal[tuple(CHANGE_TYPES)]
    responsible_agent: Optional[str] = Field(
        description="Exact name of one recommended_agents entry, or null when no agent is involved")
    deterministic_automation: bool
    human_in_the_loop: bool
    actor_persona: str = Field(description="Who owns the future-state step")
    system_used: List[str]
    trigger: str
    action: str
    expected_outcome: str
    reason_for_change: str
    kpi_gap_addressed: str = Field(
        description="The Tier 1 / Tier 2 KPI or GAP id this change addresses, from the evidence pack")

    @field_validator("system_used")
    @classmethod
    def _strip_systems(cls, v: List[str]) -> List[str]:
        return [s.strip() for s in v if s and s.strip()]


class ToBeWorkflowLLM(BaseModel):
    """Exactly what the To-Be State Workflow Designer must return."""
    model_config = ConfigDict(extra="forbid")

    workflow_summary: str = Field(
        description="What changes in this workflow and why, grounded in the evidence pack")
    design_limitations: str = Field(
        description="What this design cannot claim from the evidence available")
    activities: List[ToBeActivity] = Field(min_length=1)

    @field_validator("activities")
    @classmethod
    def _order(cls, activities: List[ToBeActivity]) -> List[ToBeActivity]:
        return sorted(activities, key=lambda a: (a.to_be_sequence, a.current_sequence))


# ================================================== strict JSON schema for the Responses API
def _obj(properties: dict) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


_STR = {"type": "string"}
_INT = {"type": "integer"}
_BOOL = {"type": "boolean"}
_STR_LIST = {"type": "array", "items": _STR}
_NULLABLE_STR = {"type": ["string", "null"]}

TO_BE_OUTPUT_SCHEMA = _obj({
    "workflow_summary": _STR,
    "design_limitations": _STR,
    "activities": {
        "type": "array",
        "items": _obj({
            "current_sequence": _INT,
            "current_activity": _STR,
            "to_be_sequence": _INT,
            "to_be_activity": _STR,
            "change_type": {"type": "string", "enum": CHANGE_TYPES},
            "responsible_agent": _NULLABLE_STR,
            "deterministic_automation": _BOOL,
            "human_in_the_loop": _BOOL,
            "actor_persona": _STR,
            "system_used": _STR_LIST,
            "trigger": _STR,
            "action": _STR,
            "expected_outcome": _STR,
            "reason_for_change": _STR,
            "kpi_gap_addressed": _STR,
        }),
    },
})

TO_BE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "name": "talent_acquisition_to_be_workflow",
    "schema": TO_BE_OUTPUT_SCHEMA,
    "strict": True,
}
