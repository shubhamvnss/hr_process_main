"""
backend.services.kpi_context_service - (2) KPIs ASSOCIATED WITH THE IMPACTED CASES.

Two different questions get answered here, and they must not be confused:

  a) Which KPIs is this root cause *declared* against?
     The Tier 1 KPI it explains, the Tier 2 diagnostics it cites, and the documented gaps behind
     them - read from the KPI run and the gap library. This is catalogue context.

  b) How do the impacted cases actually *score* on those measures, compared with everyone else?
     Recomputed here over the impacted cohort and the unimpacted cohort of the same case type.
     This is the part that tells you whether the root cause is associated with worse performance
     at all, and by how much.

(b) is a cohort comparison, not a causal test. The impacted and unimpacted populations are not
randomised and are not matched on role complexity, priority or seniority - senior and overseas
roles legitimately take longer (BRD section 3.1). Every comparison returned here carries that
caveat in `interpretation_note`, and the API passes it through rather than dropping it.

Right-censoring is respected throughout: open cases are counted but excluded from completion
measures, exactly as kpi_core does, so these numbers stay comparable with the dashboard.
"""
from __future__ import annotations

from typing import List, Optional

from .. import db
from . import fix_selectors as sel

CAVEAT = ("Impacted and unimpacted cases are not matched on role complexity, job level, location "
          "or priority. A difference here is an association, not a measured effect.")


# ------------------------------------------------------------------ (a) catalogue context
def tier1_context(conn, kpi_id: str, run_id: int) -> dict:
    if not (kpi_id and run_id):
        return {}
    s = db.schema()
    return db.one(conn, f"""
        SELECT kpi_id, kpi_name, formula, current_value, target, unit, better_when,
               variance_vs_target_pct, health, trend_direction, previous_value,
               denominator_cases, cohort_rule, tier2_drivers
        FROM {s}.kpi_tier1_health WHERE run_id = %s AND kpi_id = %s;""", [run_id, kpi_id]) or {}


def tier2_context(conn, kpi_ids: List[str], run_id: int) -> List[dict]:
    if not kpi_ids or not run_id:
        return []
    s = db.schema()
    return db.rows(conn, f"""
        SELECT kpi_id, kpi_name, formula, current_value, target, unit, better_when,
               variance_vs_target_pct, health, feeds_tier1_kpi, gap_reference,
               root_cause_category, denominator_cases, cohort_rule
        FROM {s}.kpi_tier2_diagnostics WHERE run_id = %s AND kpi_id = ANY(%s)
        ORDER BY kpi_id;""", [run_id, kpi_ids])


def gap_context(conn, gap_ids: List[str]) -> List[dict]:
    """The gap library rows, each carrying how often the rule actually fired."""
    if not gap_ids:
        return []
    s = db.schema()
    gaps = db.rows(conn, f"""
        SELECT gap_reference, stage_or_domain, gap_definition, tier2_evidence, tier1_impact,
               illustrative_detection_rule, rca_hypothesis_not_fact, intervention_options
        FROM {s}.gap_mapping WHERE gap_reference = ANY(%s) ORDER BY gap_reference;""", [gap_ids])
    detected = {d["gap_reference"]: d for d in db.rows(conn, f"""
        SELECT gap_reference, COUNT(*) AS detections,
               COUNT(DISTINCT process_instance_id) AS cases,
               ROUND(AVG(measured_value)::numeric, 2) AS avg_measured_value,
               ROUND(MIN(threshold_value)::numeric, 2) AS threshold_value,
               MIN(measure_unit) AS measure_unit
        FROM {s}.gap_detection WHERE gap_reference = ANY(%s)
        GROUP BY gap_reference;""", [gap_ids])}
    for g in gaps:
        g["detected"] = detected.get(g["gap_reference"],
                                     {"detections": 0, "cases": 0,
                                      "note": "cited by the analysis but never detected in this "
                                              "extract - weak evidence"})
    return gaps


def stage_context(conn, stage_codes: List[str], case_type: Optional[str], run_id: int) -> List[dict]:
    """Stage KPIs for the stages this root cause runs through, in the right case type."""
    if not stage_codes or not run_id:
        return []
    s = db.schema()
    return db.rows(conn, f"""
        SELECT case_type, stage_code, stage_name, cases, median_elapsed_days, p90_elapsed_days,
               stage_sla_days, sla_breach_pct, revisit_rate_pct, avg_recorded_waiting_days, health
        FROM {s}.kpi_stage_breakdown
        WHERE run_id = %s AND stage_code = ANY(%s)
          AND (%s IS NULL OR case_type = %s)
        ORDER BY case_type, stage_sequence;""",
        [run_id, stage_codes, case_type, case_type])


# ------------------------------------------------------------------ (b) cohort comparison
_APPLICATION_MEASURES = [
    ("Candidate journey", "AVG(a.total_elapsed_business_days)", "days", "lower",
     "a.is_open_censored = 'N'"),
    ("Longest inactivity", "AVG(a.max_inactivity_business_days)", "days", "lower", "TRUE"),
    ("Interview rounds", "AVG(a.number_of_interview_rounds)", "rounds", "lower", "TRUE"),
    ("Interview reschedules", "AVG(a.interview_reschedule_count)", "per case", "lower", "TRUE"),
    ("Rework loops", "AVG(a.rework_loop_count)", "per case", "lower", "TRUE"),
    ("Manual touches", "AVG(a.manual_touch_count)", "per case", "lower", "TRUE"),
    ("Follow-up events", "AVG(a.follow_up_event_count)", "per case", "lower", "TRUE"),
    ("Withdrawal rate", "100.0 * COUNT(*) FILTER (WHERE a.application_outcome = 'Withdrawn')"
                        " / NULLIF(COUNT(*), 0)", "%", "lower", "TRUE"),
    ("Offer acceptance rate", "100.0 * COUNT(*) FILTER (WHERE a.offer_acceptance_flag = 'Y')"
                              " / NULLIF(COUNT(*) FILTER (WHERE a.offer_acceptance_flag"
                              " IN ('Y','N')), 0)", "%", "higher", "TRUE"),
    ("SLA breach rate", "100.0 * SUM(a.sla_breach_count) / NULLIF(SUM(a.sla_eligible_count), 0)",
     "%", "lower", "TRUE"),
]

_REQUISITION_MEASURES = [
    ("Time to fill", "AVG(c.time_to_fill_business_days)", "days", "lower",
     "c.is_open_censored = 'N'"),
    ("Days open", "AVG(c.business_days_open)", "days", "lower", "TRUE"),
    ("Approval lead time", "AVG(c.days_to_approval)", "days", "lower", "TRUE"),
    ("Time to recruiter assignment", "AVG(c.days_to_assignment)", "days", "lower", "TRUE"),
    ("Rework loops", "AVG(c.rework_loop_count)", "per case", "lower", "TRUE"),
    ("Manual touches", "AVG(c.manual_touch_count)", "per case", "lower", "TRUE"),
    ("Follow-up events", "AVG(c.follow_up_event_count)", "per case", "lower", "TRUE"),
    ("Longest inactivity", "AVG(c.max_inactivity_business_days)", "days", "lower", "TRUE"),
    ("SLA breach rate", "100.0 * SUM(c.sla_breach_count) / NULLIF(SUM(c.sla_eligible_count), 0)",
     "%", "lower", "TRUE"),
    ("Fill rate", "100.0 * COUNT(*) FILTER (WHERE c.requisition_outcome = 'Filled')"
                  " / NULLIF(COUNT(*) FILTER (WHERE c.is_open_censored = 'N'), 0)",
     "%", "higher", "TRUE"),
]


def cohort_comparison(conn, selectors: dict) -> List[dict]:
    """Impacted vs unimpacted cases of the same case type, measure by measure."""
    s = db.schema()
    params = sel.match_params(selectors)
    case_type = selectors.get("case_type") or "APPLICATION"
    measures = _APPLICATION_MEASURES if case_type == "APPLICATION" else _REQUISITION_MEASURES
    alias, table, key = (("a", "application_master", "application_id") if case_type == "APPLICATION"
                         else ("c", "requisition_master", "requisition_id"))

    out = []
    for label, expr, unit, better, cohort_filter in measures:
        # The cohort split is a GROUP BY, not a FILTER clause. Several of these measures are
        # ratios that already contain their own aggregate FILTER - and in PostgreSQL FILTER may
        # only follow an aggregate call, never a composite expression, so wrapping them would be
        # a syntax error.
        rows = db.rows(conn, f"""
            WITH impacted AS (
                SELECT DISTINCT e.process_instance_id
                FROM {s}.event_log e
                WHERE e.case_type = %(case_type)s AND {sel.match_sql(s)}
            )
            SELECT (i.process_instance_id IS NOT NULL) AS is_impacted,
                   ROUND(({expr})::numeric, 2)         AS value,
                   COUNT(*)                            AS cases
            FROM {s}.{table} {alias}
            LEFT JOIN impacted i ON i.process_instance_id = {alias}.{key}
            WHERE {cohort_filter}
            GROUP BY 1;""", {**params, "case_type": case_type})
        by_cohort = {bool(r.get("is_impacted")): r for r in rows}
        row = {
            "impacted": (by_cohort.get(True) or {}).get("value"),
            "unimpacted": (by_cohort.get(False) or {}).get("value"),
            "impacted_n": (by_cohort.get(True) or {}).get("cases"),
            "unimpacted_n": (by_cohort.get(False) or {}).get("cases"),
        }
        if row.get("impacted") is None:
            continue
        imp, unimp = row.get("impacted"), row.get("unimpacted")
        delta = round(imp - unimp, 2) if imp is not None and unimp is not None else None
        worse = None
        if delta is not None:
            worse = (delta > 0) if better == "lower" else (delta < 0)
        out.append({
            "measure": label, "unit": unit, "better_when": better,
            "impacted": imp, "unimpacted": unimp, "difference": delta,
            "impacted_cases": row.get("impacted_n"), "unimpacted_cases": row.get("unimpacted_n"),
            "direction": None if worse is None else ("worse" if worse else "better"),
            "cohort_rule": ("completed cases only - open cases are right-censored"
                            if cohort_filter != "TRUE" else "all cases of this case type"),
        })
    return out


def build(conn, selectors: dict, run_id: Optional[int]) -> dict:
    """Everything the UI shows under "KPIs associated with the impacted cases"."""
    case_type = selectors.get("case_type") or "APPLICATION"
    comparison = cohort_comparison(conn, selectors)
    worse = [c for c in comparison if c["direction"] == "worse"]
    return {
        "tier1": tier1_context(conn, selectors.get("tier1_kpi_id"), run_id),
        "tier2": tier2_context(conn, selectors.get("tier2_kpi_ids") or [], run_id),
        "gaps": gap_context(conn, selectors.get("gap_references") or []),
        "stages": stage_context(conn, selectors.get("stage_codes") or [], case_type, run_id),
        "cohort_comparison": comparison,
        "measures_worse_on_impacted": len(worse),
        "measures_compared": len(comparison),
        "interpretation_note": CAVEAT,
    }
