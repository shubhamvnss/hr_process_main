"""
backend.services.fix_selectors - turn one stored root cause into a deterministic event-log filter.

Everything downstream of "Fix with AI" (impacted cases, the KPIs those cases move, the
representative case and its journey) is found with these selectors. They are extracted from the
agent's own evidence, so the cases returned are the cases the finding was actually grounded in -
never a fresh interpretation.

Where they come from
--------------------
    activity_names      detail_json.activity_evidence[].activity_name
                        + detail_json.rework_evidence[].rework_loop_type (a rework loop in this
                          process IS an activity - "Interview Rescheduled", "Job Description
                          Revised" - so both lists match against event_log.activity_name)
    gap_references      root_cause_finding.gap_references        (semicolon separated)
    stage_codes         detail_json.stage_evidence[].stage_name  (mapped back to S1..S9)
    case_type           root_cause_finding.affected_case_type

Two differences from the Corporate Loan prototype, both deliberate:

1. There is no step_id in this event log; activities are matched by their canonical name, which is
   the primary key of activity_master.

2. event_log.gap_reference is intentionally EMPTY in this dataset - gaps are not pre-labelled on
   events, they are derived by the deterministic rules into gap_detection (BRD section 10). So a
   gap selector resolves through gap_detection.process_instance_id, not through a column on the
   event. Matching event_log.gap_reference here would silently return zero cases.

3. case_type is a first-class part of the filter. A root cause about candidate withdrawal impacts
   APPLICATION cases; one about approval delay impacts REQUISITION cases. Counting them against
   the wrong population would produce a meaningless percentage (BRD section 6.1).
"""
from __future__ import annotations

import json
from typing import List, Optional

# Stage names as rendered by the KPI engine, mapped back to their codes.
STAGE_CODE_BY_NAME = {
    "1. Hiring Demand": "S1",
    "2. Requisition & Approval": "S2",
    "3. Recruitment Initiation": "S3",
    "4. Sourcing": "S4",
    "5. Screening & Shortlisting": "S5",
    "6. Interview & Assessment": "S6",
    "7. Selection & Decision": "S7",
    "8. Offer Management": "S8",
    "9. Pre-joining & Closure": "S9",
    "Cross-stage (System / Governance)": "S0",
}

CASE_TYPES = ("REQUISITION", "APPLICATION")


def _as_list(value) -> List[str]:
    """Accept a JSON array, a semicolon/comma separated string, or None."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = str(value).replace(",", ";").split(";")
    out, seen = [], set()
    for item in items:
        text = str(item).strip()
        if text and text.lower() not in ("nan", "none", "null") and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _detail(finding: dict) -> dict:
    detail = finding.get("detail_json")
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except (TypeError, ValueError):
            detail = {}
    return detail or {}


def extract(finding: dict) -> dict:
    """Build the selector set for one root_cause_finding row."""
    detail = _detail(finding)

    activities = [a.get("activity_name") for a in (detail.get("activity_evidence") or [])]
    loops = [r.get("rework_loop_type") for r in (detail.get("rework_evidence") or [])]
    stages = [STAGE_CODE_BY_NAME.get(s.get("stage_name"), s.get("stage_name"))
              for s in (detail.get("stage_evidence") or [])]

    case_type = (finding.get("affected_case_type") or "").upper()
    if case_type not in CASE_TYPES:
        case_type = None                    # unknown: both case types are searched

    return {
        "finding_id": finding.get("finding_id"),
        "case_type": case_type,
        "activity_names": _as_list(activities) + _as_list(loops),
        "rework_loop_types": _as_list(loops),
        "gap_references": _as_list(finding.get("gap_references")),
        "stage_codes": [s for s in _as_list(stages) if s],
        "tier1_kpi_id": finding.get("tier1_kpi_id"),
        "tier2_kpi_ids": _as_list(finding.get("linked_tier2_kpis")),
        "run_id": finding.get("run_id"),
    }


def has_evidence(selectors: dict) -> bool:
    """A root cause with no activity and no gap selector cannot be tied to cases in the log."""
    return bool(selectors.get("activity_names") or selectors.get("gap_references"))


def describe(selectors: dict) -> str:
    """One line explaining how the impacted population was identified - shown in the API."""
    bits = []
    if selectors.get("activity_names"):
        bits.append(f"{len(selectors['activity_names'])} activity selector(s)")
    if selectors.get("gap_references"):
        bits.append(f"gap detections for {', '.join(selectors['gap_references'])}")
    scope = selectors.get("case_type") or "REQUISITION and APPLICATION"
    return (f"{scope} cases whose event log matches " + " or ".join(bits)) if bits else \
        "no deterministic selector could be derived from this root cause"


# ------------------------------------------------------------------ SQL fragments
# Shared by every downstream service so they all see exactly the same population.
#
# The gap arm resolves through gap_detection because gaps are derived, not stored on the event.
MATCH_SQL = """(
        e.activity_name = ANY(%(activities)s)
     OR EXISTS (SELECT 1 FROM {schema}.gap_detection g
                 WHERE g.process_instance_id = e.process_instance_id
                   AND g.gap_reference = ANY(%(gaps)s))
)"""

CASE_TYPE_SQL = "(%(case_type)s IS NULL OR e.case_type = %(case_type)s)"


def match_sql(schema: str) -> str:
    return MATCH_SQL.format(schema=schema)


def match_params(selectors: dict) -> dict:
    return {
        "activities": list(selectors.get("activity_names") or []),
        "gaps": list(selectors.get("gap_references") or []),
        "case_type": selectors.get("case_type"),
    }


def master_table(case_type: Optional[str]) -> str:
    """The case master a case_type lives in."""
    return "application_master" if case_type == "APPLICATION" else "requisition_master"


def master_key(case_type: Optional[str]) -> str:
    return "application_id" if case_type == "APPLICATION" else "requisition_id"
