"""
root_cause_schema - the JSON schema the Talent Acquisition Root Cause Agent's answer must conform to.

The schema is passed to the OpenAI Responses API as text.format (json_schema, strict), so the
model is *constrained* to emit exactly this structure - the frontend never has to defend against
a stray Markdown fence or a renamed field.

Strict mode is a restricted JSON Schema subset: no numeric/length constraints, no recursion,
every object closed (additionalProperties: false) with every property listed in `required`.
A value that may be missing is therefore expressed as a nullable union type - ["number", "null"] -
which is also exactly rule 24 of the prompt: use null where a value is unavailable.

validate_output() adds the checks a JSON schema cannot express - only Critical KPIs analysed,
at most 6 root causes, frontend ranking identical to the detailed ranking, every cited KPI id and
GAP id present in the evidence package, and the Talent Acquisition-specific rule that a root cause
touching candidate withdrawal must not assert causation. It returns a list of human-readable
problems; an empty list means the answer passed.
"""
from __future__ import annotations

SCHEMA_NAME = "talent_acquisition_root_cause_analysis"

STR = {"type": "string"}
NUM = {"type": ["number", "null"]}          # strict-mode nullable: never omit, send null
INT = {"type": "integer"}
STR_LIST = {"type": "array", "items": STR}

HEALTH_VALUES = ["Critical", "Watch", "Healthy", "Informational", "N/A"]
CONFIDENCE_VALUES = ["High", "Medium", "Low"]
ROOT_CAUSE_TYPES = ["Process", "People", "Technology", "Data", "Governance", "Policy",
                    "Communication", "Workload", "Candidate Experience"]
CASE_TYPES = ["REQUISITION", "APPLICATION"]
MAX_ROOT_CAUSES = 6


def _obj(properties):
    """Closed object: every property required, no extras - what structured outputs expect."""
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def _arr(items):
    return {"type": "array", "items": items}


_AFFECTED_CASES = _obj({
    "case_type": {"type": "string", "enum": CASE_TYPES},
    "count": NUM,
    "percentage": NUM,
    "denominator_description": STR,
})

_FRONTEND_ROOT_CAUSE = _obj({
    "rank": INT,
    "root_cause": STR,
    "root_cause_summary": STR,
    "impact_summary": STR,
    "confidence": {"type": "string", "enum": CONFIDENCE_VALUES},
    "statistics": _arr(_obj({"metric": STR, "value": NUM, "unit": STR})),
    "gap_references": STR_LIST,
    "linked_tier2_kpis": STR_LIST,
    "affected_cases": _AFFECTED_CASES,
})

_FRONTEND_KPI = _obj({
    "kpi_id": STR,
    "kpi_name": STR,
    "current_value": NUM,
    "target": NUM,
    "unit": STR,
    "variance_vs_target_pct": NUM,
    "health": {"type": "string", "enum": ["Critical"]},
    "cohort_rule": STR,
    "kpi_performance_summary": STR,
    "why_not_performing": STR,
    "top_root_causes": _arr(_FRONTEND_ROOT_CAUSE),
})

_DETAILED_ROOT_CAUSE = _obj({
    "rank": INT,
    "root_cause": STR,
    "root_cause_type": {"type": "string", "enum": ROOT_CAUSE_TYPES},
    "confidence": {"type": "string", "enum": CONFIDENCE_VALUES},
    "causal_explanation": STR,
    "gap_references": STR_LIST,
    "tier2_evidence": _arr(_obj({
        "kpi_id": STR, "kpi_name": STR, "current_value": NUM, "target": NUM, "unit": STR,
        "health": {"type": "string", "enum": HEALTH_VALUES}, "variance_vs_target_pct": NUM,
    })),
    "gap_evidence": _arr(_obj({
        "gap_reference": STR, "findings": NUM, "cases": NUM, "requisitions": NUM,
        "median_measured_value": NUM, "threshold_value": NUM, "measure_unit": STR,
    })),
    "stage_evidence": _arr(_obj({
        "case_type": {"type": "string", "enum": CASE_TYPES}, "stage_name": STR,
        "median_elapsed_days": NUM, "p90_elapsed_days": NUM, "stage_sla_days": NUM,
        "sla_breach_pct": NUM, "avg_recorded_waiting_days": NUM, "revisit_rate_pct": NUM,
    })),
    "activity_evidence": _arr(_obj({
        "activity_name": STR, "activity_stage": STR, "actor_persona": STR, "events": NUM,
        "cases_touched_pct": NUM, "avg_waiting_min": NUM, "total_waiting_days": NUM,
        "sla_breach_pct": NUM, "rework_share_pct": NUM, "top_waiting_reason": STR,
        "waiting_rank": NUM,
    })),
    "rework_evidence": _arr(_obj({
        "rework_loop_type": STR, "cases": NUM, "cases_pct": NUM, "days_lost": NUM,
        "avg_days_lost_per_affected_case": NUM, "top_reason_code": STR,
    })),
    "capacity_evidence": _arr(_obj({
        "recruiter_id_pseudonymous": STR, "avg_weighted_load": NUM, "peak_weighted_load": NUM,
        "requisitions": NUM, "median_time_to_fill_days": NUM, "avg_assignment_days": NUM,
    })),
    "segment_evidence": _arr(_obj({
        "dimension": STR, "segment": STR, "metric": STR, "value": NUM,
        "comparison_value": NUM, "comparison_basis": STR,
    })),
    "stalled_evidence": _arr(_obj({
        "grouping": STR, "label": STR, "stalled_cases": NUM, "avg_open_days": NUM,
    })),
    "impact_statistics": _arr(_obj({"metric": STR, "value": NUM, "unit": STR, "interpretation": STR})),
    "affected_cases": _AFFECTED_CASES,
    "evidence_chain": STR_LIST,
    "contributing_factors": STR_LIST,
    "symptoms": STR_LIST,
    "alternative_explanations": STR_LIST,
    "evidence_limitations": STR,
    "recommended_interventions": STR_LIST,
})

OUTPUT_SCHEMA = _obj({
    "frontend_summary": _obj({
        "analysis_title": STR,
        "analysis_summary": _obj({
            "critical_tier1_count": INT,
            "critical_kpis": _arr(_FRONTEND_KPI),
            "overall_observation": STR,
        }),
    }),
    "tier1_root_causes": _arr(_obj({
        "tier1_kpi": _obj({
            "kpi_id": STR, "kpi_name": STR, "current_value": NUM, "target": NUM, "unit": STR,
            "variance_vs_target_pct": NUM, "health": {"type": "string", "enum": ["Critical"]},
            "denominator_cases": NUM, "cohort_rule": STR,
        }),
        "root_causes": _arr(_DETAILED_ROOT_CAUSE),
    })),
})

# Responses API: client.responses.create(..., text={"format": OUTPUT_FORMAT})
OUTPUT_FORMAT = {
    "type": "json_schema",
    "name": SCHEMA_NAME,
    "schema": OUTPUT_SCHEMA,
    "strict": True,
}

# Words that assert causation. A withdrawal or decline finding may describe association only -
# the BRD is explicit that a candidate withdrawing after a long wait does not establish that the
# delay caused it (gap GAP-14).
CAUSAL_ASSERTIONS = ("caused by", "causes the", "proves that", "demonstrates that the delay",
                     "because of the delay", "directly caused", "is the reason")
ASSOCIATION_SUBJECTS = ("withdraw", "withdrawal", "decline", "declined", "did not join",
                        "non-joining", "disengage")


# ------------------------------------------------------------------ post-checks
def validate_output(result, evidence=None):
    """Check the rules the schema cannot express. Returns a list of problems (empty = clean)."""
    problems = []
    fs = (result.get("frontend_summary") or {}).get("analysis_summary") or {}
    front = fs.get("critical_kpis") or []
    detail = result.get("tier1_root_causes") or []

    if fs.get("critical_tier1_count") != len(front):
        problems.append(f"critical_tier1_count ({fs.get('critical_tier1_count')}) does not match the "
                        f"number of critical_kpis ({len(front)}).")

    if evidence:
        expected = [k["kpi_id"] for k in evidence.get("critical_tier1", [])]
        analysed = [k.get("kpi_id") for k in front]
        extra = [k for k in analysed if k not in expected]
        missing = [k for k in expected if k not in analysed]
        if extra:
            problems.append(f"KPI(s) analysed that the KPI engine did not flag Critical: "
                            f"{', '.join(extra)}.")
        if missing:
            problems.append(f"Critical KPI(s) not analysed: {', '.join(missing)}.")

        known_kpis = ({k["kpi_id"] for k in evidence.get("tier1_all", [])}
                      | {t["kpi_id"] for t in evidence.get("linked_tier2", [])})
        known_gaps = ({g["gap_reference"] for g in evidence.get("gaps", [])}
                      | {g["gap_reference"] for g in evidence.get("gap_detections", [])})
        for kpi in front:
            for rc in kpi.get("top_root_causes") or []:
                for t2 in rc.get("linked_tier2_kpis") or []:
                    if known_kpis and t2 not in known_kpis:
                        problems.append(f"{kpi.get('kpi_id')} rank {rc.get('rank')}: Tier 2 KPI id "
                                        f"'{t2}' does not exist in the evidence package.")
                for gap in rc.get("gap_references") or []:
                    if known_gaps and gap not in known_gaps:
                        problems.append(f"{kpi.get('kpi_id')} rank {rc.get('rank')}: Gap id '{gap}' "
                                        f"does not exist in the evidence package.")

    detail_by_kpi = {(d.get("tier1_kpi") or {}).get("kpi_id"): d for d in detail}
    for kpi in front:
        kid = kpi.get("kpi_id")
        causes = kpi.get("top_root_causes") or []
        if not kpi.get("kpi_performance_summary"):
            problems.append(f"{kid}: kpi_performance_summary is empty.")
        if not kpi.get("why_not_performing"):
            problems.append(f"{kid}: why_not_performing is empty.")
        if not causes:
            problems.append(f"{kid}: no root causes returned.")
        if len(causes) > MAX_ROOT_CAUSES:
            problems.append(f"{kid}: {len(causes)} root causes returned, the maximum is "
                            f"{MAX_ROOT_CAUSES}.")
        if [c.get("rank") for c in causes] != list(range(1, len(causes) + 1)):
            problems.append(f"{kid}: frontend root-cause ranks are not 1..{len(causes)}.")

        d = detail_by_kpi.get(kid)
        if d is None:
            problems.append(f"{kid}: present in frontend_summary but missing from tier1_root_causes.")
            continue
        d_causes = d.get("root_causes") or []
        if [c.get("root_cause") for c in causes] != [c.get("root_cause") for c in d_causes]:
            problems.append(f"{kid}: frontend root-cause ranking does not match the detailed "
                            f"ranking (rule 20).")
        for rc in d_causes:
            tag = f"{kid} rank {rc.get('rank')}"
            if not (rc.get("impact_statistics") or []):
                problems.append(f"{tag}: no impact_statistics - every root cause needs quantitative "
                                f"evidence (rule 12).")
            if not (rc.get("evidence_chain") or []):
                problems.append(f"{tag}: no evidence_chain (rule 14).")
            if not (rc.get("tier2_evidence") or []) and \
                    "tier 2" not in (rc.get("causal_explanation") or "").lower():
                problems.append(f"{tag}: no tier2_evidence and the absence is not explained (rule 13).")
            text = " ".join([rc.get("root_cause") or "", rc.get("causal_explanation") or ""]).lower()
            if any(s in text for s in ASSOCIATION_SUBJECTS) and \
                    any(c in text for c in CAUSAL_ASSERTIONS):
                problems.append(f"{tag}: states candidate withdrawal / decline as proven causation. "
                                f"The BRD requires association language for GAP-14 (rule 27).")
            if (rc.get("confidence") == "High") and not (rc.get("tier2_evidence") or []) \
                    and not (rc.get("gap_evidence") or []):
                problems.append(f"{tag}: confidence High with neither Tier 2 nor gap-detection "
                                f"evidence (rule 22).")

    for kid in detail_by_kpi:
        if kid not in {k.get("kpi_id") for k in front}:
            problems.append(f"{kid}: present in tier1_root_causes but missing from frontend_summary.")
    return problems
