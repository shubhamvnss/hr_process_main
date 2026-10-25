"""
backend.agents.to_be_workflow_agent - the To-Be State Workflow Designer.

Takes ONE representative case's actual current-state activity sequence and redesigns it into a
future-state sequence, using ONLY the agents that recommendation_agent.py already selected
deterministically for this Tier 1 / Tier 2 / Gap combination.

    recommendation_agent.py   ->  WHICH agents            (deterministic, version-controlled)
    this module               ->  HOW they touch each real activity of this case

It does not select, invent, rename or add an agent.

Defence in depth, the same pattern as root_cause_agent.py: the prompt states the rules, the strict
JSON schema constrains the shape, and the functions below constrain the CONTENT against what is
actually true for this case - allowed agent names, systems that appear in this case's own event
log, full coverage of the current-state rows, and the governance controls that may not be
designed away.

Two guardrails exist here that have no equivalent in the Corporate Loan version, because they come
from the BRD rather than from good practice:

  * PROTECTED_ACTIVITIES may never be eliminated. A mandatory approval, budget validation,
    verification clearance or the candidate's own decision is a control, not an inefficiency.
    GAP-18 exists precisely because these go missing; a To-Be design that removes one would be
    recommending the very deviation the analysis flags.
  * CANDIDATE_FACING steps must stay under human control. The design may have an agent prepare
    them, but human_in_the_loop is forced on, because these steps commit the organisation to a
    person.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from ..to_be_schemas import NO_SYSTEM_IN_LOG, TO_BE_RESPONSE_FORMAT, ToBeWorkflowLLM


class ToBeError(RuntimeError):
    """The To-Be design could not be produced or validated."""


# Controls and decisions that may not be eliminated. Automating the preparation around them is
# fine; removing the control itself is not (BRD section 10, GAP-18).
PROTECTED_ACTIVITIES = {
    "Workforce Budget Validated",
    "Requisition Approved",
    "Offer Approved",
    "Background Verification Cleared",
    "Offer Accepted",
    "Offer Declined",
    "Candidate Withdrew",
    "Candidate Joined",
    "Selection Decision Recorded",
    "Shortlist Review Decision Recorded",
    "Policy Exception Documented",
}

# Steps that commit the organisation to a candidate, or that a person must own.
CANDIDATE_FACING_ACTIVITIES = {
    "Offer Sent to Candidate", "Offer Generated", "Offer Revised and Resent",
    "Interview Conducted", "Candidate Rejected", "Offer Approved",
    "Requisition Approved", "Selection Decision Recorded",
    "Shortlist Review Decision Recorded", "Candidate Joined",
}


SYSTEM_PROMPT = """TALENT ACQUISITION - TO-BE STATE WORKFLOW DESIGNER (Deloitte Kinetic)

ROLE
You take one representative case's ACTUAL current-state activity sequence and redesign it into a
future-state workflow, using ONLY the AI agents that a deterministic routing layer has already
selected for this root cause. You do not select, invent, rename or add agents. You decide how the
already-approved agents, plain deterministic automation, and human steps touch each real activity
this case actually went through.

INPUT
The evidence pack gives you: the complete current-state activity list for this case (each row with
a `sequence` number, the real activity name, stage, actor persona, source system, timestamps,
duration, waiting time and waiting reason, and manual / rework / exception / handoff / SLA-breach
flags), the Tier 1 KPI, the Tier 2 diagnostic KPIs, the documented gaps, the root cause, and
`recommended_agents` - the exact, already-approved agent list with id, name, responsibility and
accountable owner.

ABSOLUTE RULES
1. Return exactly one activity object per current-state row. `current_sequence` must equal that
   row's `sequence`, and `current_activity` must be that row's activity name verbatim. Every
   current-state row appears exactly once - never skip, duplicate, or add a row.
2. `responsible_agent` must be exactly the `name` of one entry in `recommended_agents`, or null.
   Set it only when change_type is "AI Agent". Never invent an agent, rename one, or use an agent
   whose responsibility does not address that activity.
3. change_type is one of: Retained, Automated (Deterministic), AI Agent, Combined, Eliminated,
   Human.
   - "Retained": the step continues exactly as it runs today. Use it generously - do not force a
     change onto every row.
   - "Combined": give two or more rows the SAME to_be_sequence and the SAME to_be_activity text.
   - "Eliminated": the activity becomes unnecessary. Explain why in reason_for_change and give a
     to_be_activity of "N/A - eliminated".
   - "Human": the step stays with a person but is redesigned.
4. Reminder, chase and follow-up events are the clearest elimination or automation candidates:
   they consume coordination effort without moving the case forward. A reminder should usually
   become an automated escalation rather than a person remembering to send it.
5. NEVER eliminate a mandatory control or a candidate's own decision - budget validation,
   requisition approval, offer approval, verification clearance, the selection decision, the
   candidate accepting, declining, withdrawing or joining. Automating the preparation around a
   control is good design; removing the control is the governance deviation the analysis exists to
   flag. If a control is slow, make it faster, not absent.
6. human_in_the_loop = true whenever the step communicates with a candidate, commits the
   organisation, makes or overrides a hiring or compensation decision, or is a formal approval.
   This flag applies on top of any change_type, including "AI Agent".
7. `system_used` must contain ONLY systems from `case_systems` - the systems this case's own event
   log actually shows - or the exact literal string
   "Client API / integration dependency - system not present in event log" when a capability
   genuinely needs an integration this case does not evidence. Never name a product
   (Workday, SAP, Greenhouse, etc.) that is not in case_systems.
8. Never invent a metric, number, case id, persona or activity that is not in the evidence pack.
9. Ground reason_for_change and kpi_gap_addressed in the actual Tier 1 / Tier 2 KPI, GAP id and
   root cause supplied. No generic boilerplate detached from this case.
10. Do not assert causation. The evidence shows sequence and association - that a candidate
    withdrew after a long wait does not establish that the wait caused it. Write "is associated
    with" and "follows", not "caused by".
11. design_limitations must state plainly what this design cannot claim: it is built from one
    representative case, the agents were selected from aggregate KPI evidence, and the expected
    outcomes are intentions to be measured after the change, not forecasts.

Write for an HR operations executive: concrete, calm and specific. No marketing language."""


def build_payload(context: dict) -> str:
    """The evidence pack. Only the four inputs this agent is allowed to see."""
    return json.dumps({
        "case_id": context.get("case_id"),
        "case_type": context.get("case_type"),
        "current_state": context.get("current_state"),
        "case_systems": context.get("case_systems"),
        "tier_1_kpi": context.get("tier1_kpi"),
        "tier_2_kpis": context.get("tier2_kpis"),
        "documented_gaps": context.get("gaps"),
        "root_cause": context.get("root_cause"),
        "recommended_agents": context.get("recommended_agents"),
    }, separators=(",", ":"), default=str)


# ------------------------------------------------------------------ content validation
def _validate_coverage(plan: ToBeWorkflowLLM, current_state: List[dict]) -> Optional[str]:
    expected = {row["sequence"] for row in current_state}
    got = [a.current_sequence for a in plan.activities]
    if len(got) != len(set(got)):
        dupes = sorted({x for x in got if got.count(x) > 1})
        return f"duplicate current_sequence values in the response: {dupes}"
    missing = sorted(expected - set(got))
    extra = sorted(set(got) - expected)
    if missing:
        return f"missing current_sequence values that must each appear exactly once: {missing}"
    if extra:
        return f"current_sequence values that do not exist in current_state: {extra}"
    return None


def _invented_agents(plan: ToBeWorkflowLLM, allowed_names: List[str]) -> List[str]:
    lookup = {n.strip().lower() for n in allowed_names}
    offenders = []
    for a in plan.activities:
        if a.responsible_agent and a.responsible_agent.strip().lower() not in lookup:
            if a.responsible_agent not in offenders:
                offenders.append(a.responsible_agent)
    return offenders


def _eliminated_controls(plan: ToBeWorkflowLLM, current_by_seq: Dict[int, dict]) -> List[str]:
    """Mandatory controls or candidate decisions the design tried to remove."""
    offenders = []
    for a in plan.activities:
        if a.change_type != "Eliminated":
            continue
        name = (current_by_seq.get(a.current_sequence) or {}).get("activity") or a.current_activity
        if name in PROTECTED_ACTIVITIES and name not in offenders:
            offenders.append(name)
    return offenders


def _invented_systems(plan: ToBeWorkflowLLM, allowed_systems: List[str]) -> List[str]:
    if not allowed_systems:
        return []
    lookup = {s.strip().lower() for s in allowed_systems}
    offenders = []
    for a in plan.activities:
        for system in a.system_used:
            if system == NO_SYSTEM_IN_LOG:
                continue
            if system.strip().lower() not in lookup and system not in offenders:
                offenders.append(system)
    return offenders


# ------------------------------------------------------------------ content correction
def _enforce_agent_consistency(plan: ToBeWorkflowLLM) -> None:
    """responsible_agent only means anything when the step is actually run by an agent."""
    for a in plan.activities:
        if a.change_type != "AI Agent":
            a.responsible_agent = None
        if a.change_type == "Automated (Deterministic)":
            a.deterministic_automation = True


def _enforce_human_in_the_loop(plan: ToBeWorkflowLLM, current_by_seq: Dict[int, dict]) -> List[str]:
    """Force human control back onto candidate-facing and approval steps. Returns what changed."""
    corrected = []
    for a in plan.activities:
        if a.change_type == "Eliminated":
            continue
        name = (current_by_seq.get(a.current_sequence) or {}).get("activity") or a.current_activity
        if name in CANDIDATE_FACING_ACTIVITIES and not a.human_in_the_loop:
            a.human_in_the_loop = True
            corrected.append(name)
    return corrected


def _strip_invented_systems(plan: ToBeWorkflowLLM, allowed_systems: List[str]) -> None:
    lookup = {s.strip().lower() for s in allowed_systems}
    for a in plan.activities:
        a.system_used = [s for s in a.system_used
                         if s == NO_SYSTEM_IN_LOG or s.strip().lower() in lookup]


# ------------------------------------------------------------------ model call
def _call_model(client, payload: str, correction: Optional[str], config) -> str:
    user_input = payload if not correction else (
        f"{payload}\n\nYour previous answer was rejected: {correction}\n"
        f"Return a corrected answer that satisfies the schema and every absolute rule."
    )
    kwargs = {
        "model": config.MODEL,
        "instructions": SYSTEM_PROMPT,
        "input": [{"role": "user", "content": user_input}],
        "text": {"format": TO_BE_RESPONSE_FORMAT},
        "max_output_tokens": config.MAX_OUTPUT_TOKENS,
    }
    if getattr(config, "EFFORT", None) and config.EFFORT != "default":
        kwargs["reasoning"] = {"effort": config.EFFORT}

    response = client.responses.create(**kwargs)

    if getattr(response, "status", None) == "incomplete":
        reason = getattr(getattr(response, "incomplete_details", None), "reason", "unknown")
        raise ToBeError(f"The model returned an incomplete response ({reason}).")
    for item in getattr(response, "output", None) or []:
        for block in getattr(item, "content", None) or []:
            if getattr(block, "type", None) == "refusal":
                raise ToBeError(f"The model declined the request: {block.refusal}")
    return (getattr(response, "output_text", "") or "").strip()


def generate(context: dict, client=None, config=None) -> Tuple[ToBeWorkflowLLM, Dict[str, Any]]:
    """Produce a validated To-Be design covering every current-state activity exactly once.

    Returns (plan, corrections) where corrections records what the validator had to fix, so the
    orchestrator can surface it rather than hiding it.
    """
    from .. import config as default_config
    config = config or default_config
    client = client or config.llm_client()

    current_state = context.get("current_state") or []
    if not current_state:
        raise ToBeError("No current-state activities were supplied to design a To-Be workflow from.")

    current_by_seq = {row["sequence"]: row for row in current_state}
    allowed_names = list(context.get("allowed_agent_names") or [])
    allowed_systems = list(context.get("case_systems") or [])
    payload = build_payload(context)
    correction, last_error = None, None

    for attempt in (1, 2):
        try:
            raw = _call_model(client, payload, correction, config)
            if not raw:
                raise ValueError("the model returned no text")

            plan = ToBeWorkflowLLM.model_validate_json(raw)

            coverage_error = _validate_coverage(plan, current_state)
            if coverage_error:
                raise ValueError(coverage_error)

            _enforce_agent_consistency(plan)

            offenders = _invented_agents(plan, allowed_names)
            if offenders:
                raise ValueError(
                    f"these agents are not in recommended_agents: {', '.join(offenders)}. "
                    f"responsible_agent must exactly match a recommended_agents name, or be null.")

            removed_controls = _eliminated_controls(plan, current_by_seq)
            if removed_controls:
                raise ValueError(
                    f"these are mandatory controls or candidate decisions and may not be "
                    f"eliminated: {', '.join(removed_controls)}. Make the control faster, not "
                    f"absent - removing it is the governance deviation GAP-18 describes.")

            system_offenders = _invented_systems(plan, allowed_systems)
            if system_offenders:
                if attempt == 1:
                    raise ValueError(
                        f"these systems do not appear in case_systems: "
                        f"{', '.join(system_offenders)}. Use only systems from case_systems, or "
                        f"the literal '{NO_SYSTEM_IN_LOG}'.")
                _strip_invented_systems(plan, allowed_systems)

            forced_hitl = _enforce_human_in_the_loop(plan, current_by_seq)

            return plan, {
                "human_in_the_loop_forced": forced_hitl,
                "systems_stripped": system_offenders if attempt == 2 else [],
                "attempts": attempt,
                "model": config.model_label() if hasattr(config, "model_label") else config.MODEL,
            }

        except ToBeError:
            raise
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            correction = last_error[:1500]

    raise ToBeError(f"The To-Be design could not be validated: {last_error}")
