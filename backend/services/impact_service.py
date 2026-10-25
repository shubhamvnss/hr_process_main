"""
backend.services.impact_service - (1) IMPACTED CASES.

How many cases this root cause actually touches, and what it costs them. Every figure here is SQL
over event_log and the two case masters; none of it is ever asked of the model.

Matching a case to a root cause
-------------------------------
A case is impacted when its event log contains at least one event matching a selector taken from
the agent's own evidence (see fix_selectors):

    activity_name       in the activities / rework loops the agent cited
    gap detection       the case appears in gap_detection for a gap the agent cited

Two measurement rules this module is careful about, both from BRD section 6.4:

  * Right-censoring. Open cases are counted and reported, but they never enter a completion
    measure. An open requisition has no time-to-fill, and treating it as zero - or excluding it
    silently - would both be wrong.
  * The denominator is the matching case type. A root cause about candidate withdrawal is scored
    against applications; one about approval delay against requisitions. The impact percentage is
    therefore comparable with the Tier 1 KPI it explains.
"""
from __future__ import annotations

from typing import List, Optional

from .. import db
from . import fix_selectors as sel

# Business minutes per day, as used by the KPI engine. Imported when available so the two can
# never drift; the literal is the documented fallback for environments without pandas.
try:                                                        # pragma: no cover - env dependent
    from kpi_core import BUSINESS_MIN_PER_DAY
except Exception:                                           # pragma: no cover
    BUSINESS_MIN_PER_DAY = 510


def impacted_case_ids(conn, selectors: dict, limit: Optional[int] = None) -> List[str]:
    s = db.schema()
    sql = f"""
        SELECT DISTINCT e.process_instance_id
        FROM {s}.event_log e
        WHERE {sel.CASE_TYPE_SQL} AND {sel.match_sql(s)}
        ORDER BY 1"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r["process_instance_id"] for r in db.rows(conn, sql, sel.match_params(selectors))]


def _requisition_impact(conn, params) -> dict:
    s = db.schema()
    return db.one(conn, f"""
        WITH impacted AS (
            SELECT DISTINCT e.process_instance_id
            FROM {s}.event_log e
            WHERE e.case_type = 'REQUISITION' AND {sel.match_sql(s)}
        )
        SELECT
            COUNT(*)                                                            AS total_cases,
            COUNT(i.process_instance_id)                                        AS impacted_cases,
            COUNT(*) FILTER (WHERE i.process_instance_id IS NOT NULL
                               AND c.is_open_censored = 'Y')                    AS impacted_open,
            ROUND(AVG(c.time_to_fill_business_days)
                  FILTER (WHERE i.process_instance_id IS NOT NULL), 1)          AS avg_time_to_fill,
            ROUND(AVG(c.time_to_fill_business_days)
                  FILTER (WHERE i.process_instance_id IS NULL), 1)              AS avg_time_to_fill_unimpacted,
            ROUND(AVG(c.business_days_open)
                  FILTER (WHERE i.process_instance_id IS NOT NULL), 1)          AS avg_days_open,
            ROUND(100.0 * SUM(c.sla_breach_count) FILTER (WHERE i.process_instance_id IS NOT NULL)
                  / NULLIF(SUM(c.sla_eligible_count)
                           FILTER (WHERE i.process_instance_id IS NOT NULL), 0), 1)
                                                                                AS sla_breach_rate,
            ROUND(100.0 * COUNT(*) FILTER (WHERE i.process_instance_id IS NOT NULL
                                             AND c.rework_loop_count > 0)
                  / NULLIF(COUNT(i.process_instance_id), 0), 1)                 AS rework_rate,
            ROUND(AVG(c.handoff_count) FILTER (WHERE i.process_instance_id IS NOT NULL), 1)
                                                                                AS avg_handoffs,
            ROUND(AVG(c.manual_touch_count) FILTER (WHERE i.process_instance_id IS NOT NULL), 1)
                                                                                AS avg_manual_touches,
            ROUND(AVG(c.follow_up_event_count) FILTER (WHERE i.process_instance_id IS NOT NULL), 1)
                                                                                AS avg_follow_ups,
            ROUND(AVG(c.max_inactivity_business_days)
                  FILTER (WHERE i.process_instance_id IS NOT NULL), 2)          AS avg_max_inactivity_days,
            COUNT(*) FILTER (WHERE i.process_instance_id IS NOT NULL
                               AND c.requisition_outcome = 'Filled')            AS impacted_filled,
            COUNT(*) FILTER (WHERE i.process_instance_id IS NOT NULL
                               AND c.requisition_outcome = 'Cancelled')         AS impacted_cancelled
        FROM {s}.requisition_master c
        LEFT JOIN impacted i ON i.process_instance_id = c.requisition_id;""", params) or {}


def _application_impact(conn, params) -> dict:
    s = db.schema()
    return db.one(conn, f"""
        WITH impacted AS (
            SELECT DISTINCT e.process_instance_id
            FROM {s}.event_log e
            WHERE e.case_type = 'APPLICATION' AND {sel.match_sql(s)}
        )
        SELECT
            COUNT(*)                                                            AS total_cases,
            COUNT(i.process_instance_id)                                        AS impacted_cases,
            COUNT(*) FILTER (WHERE i.process_instance_id IS NOT NULL
                               AND a.is_open_censored = 'Y')                    AS impacted_open,
            ROUND(AVG(a.total_elapsed_business_days)
                  FILTER (WHERE i.process_instance_id IS NOT NULL), 1)          AS avg_elapsed_days,
            ROUND(AVG(a.total_elapsed_business_days)
                  FILTER (WHERE i.process_instance_id IS NULL), 1)              AS avg_elapsed_days_unimpacted,
            ROUND(100.0 * SUM(a.sla_breach_count) FILTER (WHERE i.process_instance_id IS NOT NULL)
                  / NULLIF(SUM(a.sla_eligible_count)
                           FILTER (WHERE i.process_instance_id IS NOT NULL), 0), 1)
                                                                                AS sla_breach_rate,
            ROUND(100.0 * COUNT(*) FILTER (WHERE i.process_instance_id IS NOT NULL
                                             AND a.rework_loop_count > 0)
                  / NULLIF(COUNT(i.process_instance_id), 0), 1)                 AS rework_rate,
            ROUND(100.0 * COUNT(*) FILTER (WHERE i.process_instance_id IS NOT NULL
                                             AND a.application_outcome = 'Withdrawn')
                  / NULLIF(COUNT(i.process_instance_id), 0), 1)                 AS withdrawal_rate,
            ROUND(100.0 * COUNT(*) FILTER (WHERE i.process_instance_id IS NULL
                                             AND a.application_outcome = 'Withdrawn')
                  / NULLIF(COUNT(*) FILTER (WHERE i.process_instance_id IS NULL), 0), 1)
                                                                                AS withdrawal_rate_unimpacted,
            ROUND(100.0 * COUNT(*) FILTER (WHERE i.process_instance_id IS NOT NULL
                                             AND a.offer_acceptance_flag = 'Y')
                  / NULLIF(COUNT(*) FILTER (WHERE i.process_instance_id IS NOT NULL
                                              AND a.offer_acceptance_flag IN ('Y','N')), 0), 1)
                                                                                AS offer_acceptance_rate,
            ROUND(AVG(a.handoff_count) FILTER (WHERE i.process_instance_id IS NOT NULL), 1)
                                                                                AS avg_handoffs,
            ROUND(AVG(a.manual_touch_count) FILTER (WHERE i.process_instance_id IS NOT NULL), 1)
                                                                                AS avg_manual_touches,
            ROUND(AVG(a.follow_up_event_count) FILTER (WHERE i.process_instance_id IS NOT NULL), 1)
                                                                                AS avg_follow_ups,
            ROUND(AVG(a.max_inactivity_business_days)
                  FILTER (WHERE i.process_instance_id IS NOT NULL), 2)          AS avg_max_inactivity_days,
            COUNT(*) FILTER (WHERE i.process_instance_id IS NOT NULL
                               AND a.application_outcome = 'Joined')            AS impacted_joined
        FROM {s}.application_master a
        LEFT JOIN impacted i ON i.process_instance_id = a.application_id;""", params) or {}


def attributable_waiting(conn, selectors: dict) -> Optional[float]:
    """Average business days of waiting on the MATCHING events only.

    This is waiting attributable to this root cause, not the whole case - quoting whole-case
    elapsed time here would credit the finding with delay it did not cause.
    """
    s = db.schema()
    params = {**sel.match_params(selectors), "mins": BUSINESS_MIN_PER_DAY}
    row = db.one(conn, f"""
        SELECT ROUND(AVG(w.mins) / %(mins)s::numeric, 2) AS avg_waiting_days,
               ROUND(SUM(w.mins) / %(mins)s::numeric, 1) AS total_waiting_days
        FROM (
            SELECT e.process_instance_id, SUM(COALESCE(e.waiting_time_minutes, 0)) AS mins
            FROM {s}.event_log e
            WHERE {sel.CASE_TYPE_SQL} AND e.activity_name = ANY(%(activities)s)
            GROUP BY 1
        ) w;""", params)
    return row or {}


def calculate(conn, selectors: dict, run_id: Optional[int] = None) -> dict:
    """The impact block of the API response. Safe when nothing matches."""
    params = sel.match_params(selectors)
    case_type = selectors.get("case_type") or "APPLICATION"

    agg = (_application_impact(conn, params) if case_type == "APPLICATION"
           else _requisition_impact(conn, params))
    impacted = int(agg.get("impacted_cases") or 0)
    total = int(agg.get("total_cases") or 0)
    open_cases = int(agg.get("impacted_open") or 0)
    completed = impacted - open_cases

    waiting = attributable_waiting(conn, selectors) if impacted else {}

    # the Tier 1 target this root cause is measured against, read from the KPI run
    target = None
    if run_id is not None and selectors.get("tier1_kpi_id"):
        s = db.schema()
        row = db.one(conn, f"""SELECT target, unit FROM {s}.kpi_tier1_health
                               WHERE run_id = %s AND kpi_id = %s;""",
                     [run_id, selectors["tier1_kpi_id"]])
        target = (row or {}).get("target")

    if case_type == "APPLICATION":
        headline, headline_unimpacted = agg.get("avg_elapsed_days"), agg.get("avg_elapsed_days_unimpacted")
        headline_label = "Average candidate journey"
    else:
        headline, headline_unimpacted = agg.get("avg_time_to_fill"), agg.get("avg_time_to_fill_unimpacted")
        headline_label = "Average time to fill"

    delta = (round(headline - headline_unimpacted, 1)
             if headline is not None and headline_unimpacted is not None else None)

    out = {
        "case_type": case_type,
        "selector_summary": sel.describe(selectors),
        "impacted_cases": impacted,
        "total_cases": total,
        "impact_percentage": round(100.0 * impacted / total, 1) if total else None,
        "impacted_completed": completed,
        "impacted_open_censored": open_cases,
        "headline_metric": headline_label,
        "headline_value": headline,
        "headline_unimpacted": headline_unimpacted,
        "additional_days_vs_unimpacted": delta,
        "target": target,
        "sla_breach_rate": agg.get("sla_breach_rate"),
        "rework_rate": agg.get("rework_rate"),
        "average_handoffs": agg.get("avg_handoffs"),
        "average_manual_touches": agg.get("avg_manual_touches"),
        "average_follow_ups": agg.get("avg_follow_ups"),
        "average_max_inactivity_days": agg.get("avg_max_inactivity_days"),
        "average_waiting_days_attributable": (waiting or {}).get("avg_waiting_days"),
        "total_waiting_days_attributable": (waiting or {}).get("total_waiting_days"),
        "withdrawal_rate": agg.get("withdrawal_rate"),
        "withdrawal_rate_unimpacted": agg.get("withdrawal_rate_unimpacted"),
        "offer_acceptance_rate": agg.get("offer_acceptance_rate"),
        "outcome_counts": {k: agg.get(k) for k in
                           ("impacted_joined", "impacted_filled", "impacted_cancelled")
                           if agg.get(k) is not None},
        "metrics": [],
        "notes": [],
    }

    # Display metrics, built only from values that actually exist - no placeholders.
    def add(label, value, unit, detail):
        if value is not None:
            out["metrics"].append({"label": label, "value": float(value), "unit": unit,
                                   "detail": detail})

    noun = "applications" if case_type == "APPLICATION" else "requisitions"
    add("Cases impacted", impacted, noun,
        f"{out['impact_percentage']}% of {total} {noun}" if total else "")
    add(headline_label, headline, "days",
        f"against a {target}-day target" if target is not None else f"across impacted {noun}")
    add("Additional time vs unaffected cases", delta, "days",
        f"difference in {headline_label.lower()} between impacted and unimpacted {noun}")
    add("Waiting attributable to this root cause", (waiting or {}).get("avg_waiting_days"), "days",
        "average waiting on the matching activities only, not whole-case elapsed time")
    add("SLA breached", agg.get("sla_breach_rate"), "%",
        "of measured SLA intervals on impacted cases")
    add("Rework rate", agg.get("rework_rate"), "%",
        f"impacted {noun} with at least one rework loop")
    add("Withdrawal rate", agg.get("withdrawal_rate"), "%",
        f"vs {agg.get('withdrawal_rate_unimpacted')}% on unimpacted applications"
        if agg.get("withdrawal_rate_unimpacted") is not None else "of impacted applications")
    add("Average follow-ups", agg.get("avg_follow_ups"), "per case",
        "reminder and chase events - coordination effort that does not move the case")
    add("Longest inactivity", agg.get("avg_max_inactivity_days"), "days",
        "average longest gap between progression events")

    if open_cases:
        out["notes"].append(
            f"{open_cases} of the {impacted} impacted cases are still open at the reporting "
            f"cut-off and are right-censored: they are counted in the population but excluded "
            f"from completion measures.")
    if not impacted:
        out["notes"].append(
            "No case in the event log matches this root cause's selectors. The finding cannot be "
            "grounded in specific cases - check that the analysis and the KPI run are the same.")
    return out


def impacted_case_list(conn, selectors: dict, limit: int = 50, offset: int = 0) -> List[dict]:
    """A page of impacted cases with the attributes the UI lists them by."""
    s = db.schema()
    params = {**sel.match_params(selectors), "limit": int(limit), "offset": int(offset)}
    case_type = selectors.get("case_type") or "APPLICATION"

    if case_type == "APPLICATION":
        return db.rows(conn, f"""
            WITH matched AS (
                SELECT e.process_instance_id,
                       COUNT(*) FILTER (WHERE e.activity_name = ANY(%(activities)s)) AS matched_events,
                       SUM(COALESCE(e.waiting_time_minutes, 0))
                           FILTER (WHERE e.activity_name = ANY(%(activities)s))      AS matched_waiting_minutes
                FROM {s}.event_log e
                WHERE e.case_type = 'APPLICATION' AND {sel.match_sql(s)}
                GROUP BY 1
            )
            SELECT a.application_id AS case_id, 'APPLICATION' AS case_type, a.requisition_id,
                   a.application_outcome AS outcome, a.outcome_stage, a.is_open_censored,
                   a.business_unit, a.job_family, a.location, a.job_level, a.priority,
                   a.source_channel, a.total_elapsed_business_days AS elapsed_days,
                   a.max_inactivity_business_days, a.rework_loop_count, a.sla_breach_count,
                   m.matched_events, ROUND(m.matched_waiting_minutes / {BUSINESS_MIN_PER_DAY}::numeric, 2)
                       AS matched_waiting_days
            FROM matched m
            JOIN {s}.application_master a ON a.application_id = m.process_instance_id
            ORDER BY m.matched_waiting_minutes DESC NULLS LAST, m.matched_events DESC
            LIMIT %(limit)s OFFSET %(offset)s;""", params)

    return db.rows(conn, f"""
        WITH matched AS (
            SELECT e.process_instance_id,
                   COUNT(*) FILTER (WHERE e.activity_name = ANY(%(activities)s)) AS matched_events,
                   SUM(COALESCE(e.waiting_time_minutes, 0))
                       FILTER (WHERE e.activity_name = ANY(%(activities)s))      AS matched_waiting_minutes
            FROM {s}.event_log e
            WHERE e.case_type = 'REQUISITION' AND {sel.match_sql(s)}
            GROUP BY 1
        )
        SELECT c.requisition_id AS case_id, 'REQUISITION' AS case_type, c.requisition_id,
               c.requisition_outcome AS outcome, c.is_open_censored,
               c.business_unit, c.job_family, c.location, c.job_level, c.priority,
               c.time_to_fill_business_days AS elapsed_days, c.business_days_open,
               c.max_inactivity_business_days, c.rework_loop_count, c.sla_breach_count,
               c.applications_count, c.recruiter_id_pseudonymous,
               m.matched_events, ROUND(m.matched_waiting_minutes / {BUSINESS_MIN_PER_DAY}::numeric, 2)
                   AS matched_waiting_days
        FROM matched m
        JOIN {s}.requisition_master c ON c.requisition_id = m.process_instance_id
        ORDER BY m.matched_waiting_minutes DESC NULLS LAST, m.matched_events DESC
        LIMIT %(limit)s OFFSET %(offset)s;""", params)
