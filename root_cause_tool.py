"""
root_cause_tool - the single evidence tool behind the Talent Acquisition Root Cause Agent.

The agent is deliberately NOT given free access to the database. It gets exactly one tool,
get_root_cause_evidence(), which returns a fixed diagnostic package built from the KPI tables
written by 04_compute_kpis_to_postgres.py (i.e. from the deterministic output of
kpi_core.compute_kpis). The KPI engine decides WHAT is Critical; the agent only explains WHY.

Reads  : {schema}.kpi_run, kpi_summary, kpi_tier1_health, kpi_tier2_diagnostics,
         kpi_stage_breakdown, kpi_activity_breakdown, kpi_rework_loops, kpi_stalled_cases,
         kpi_monthly_trend, gap_mapping, gap_detection, recruiter_load_snapshot,
         requisition_master, application_master
Returns: a JSON-serialisable dict with the keys

    run_stats          - volumes and the as-of timestamp of the KPI run
    summary            - the kpi_summary header row for the run
    critical_tier1     - Tier 1 KPIs with health = 'Critical' (the only analysis targets)
    tier1_all          - every Tier 1 KPI (context only - not an analysis target)
    linked_tier2       - Tier 2 KPIs that declare one of those Critical Tier 1 KPIs in
                         feeds_tier1_kpi, each carrying feeds_tier1 and gaps
    root_cause_links   - the normalised Tier1 -> Tier2 -> Gap edges built from those two columns
    gaps               - gap_mapping rows for every gap referenced by linked_tier2
    gap_detections     - per-gap detection counts measured from the event log, with a sample of
                         the evidence summaries the deterministic rules produced
    stages             - per-stage elapsed / waiting / effort / breach, SPLIT BY CASE TYPE
    activities         - waiting and effort hotspots (container waits excluded from the ranking)
    rework_loops       - rework loop profile, top N by business days lost
    stalled            - stalled-case summary plus the oldest open cases
    recruiter_load     - weighted-load imbalance summary plus the most loaded recruiters
    segments           - KPI position by business unit, job family, location, job level,
                         priority, source channel and recruiter
    case_metrics       - requisition and application aggregates plus the slowest cases
    monthly_trend      - Tier 1 KPIs by month
    notes              - anything the tool had to drop or substitute, for the agent to read

Note on case_metrics: requisition_master holds 1,200 rows and application_master 7,235. Returning
them raw would flood the agent's context with data it can only use in aggregate, so the tool
returns aggregate statistics plus a small worst-case sample instead.

Note on linked_tier2: the Talent Acquisition schema has no kpi_root_cause_link bridge table -
Tier 2 carries its Tier 1 and GAP links on kpi_tier2_diagnostics.feeds_tier1_kpi and
.gap_reference, so the edges are expanded here (the same expansion the v_root_cause_tree view
does in SQL).

Usage (normally called by root_cause_agent.py, but it runs stand-alone too):

    python root_cause_tool.py                          # evidence pack for every Critical Tier 1 KPI
    python root_cause_tool.py --kpi-ids T1-01 T1-11    # restrict to specific Critical KPIs
    python root_cause_tool.py --run-id 7 --out pack.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import decimal
import json

TOOL_NAME = "get_root_cause_evidence"

TOOL_DESCRIPTION = (
    "Return the complete, pre-assembled evidence package for the Talent Acquisition process: the "
    "Critical Tier 1 KPIs, the Tier 2 diagnostic KPIs explicitly linked to them, the documented "
    "process gaps behind those Tier 2 KPIs together with how often each gap was actually detected "
    "in the event log, and the supporting stage, activity, rework, stalled-case, recruiter-load, "
    "segment and case-level evidence from the latest KPI run. Call this tool once with no kpi_ids "
    "to analyse every Critical Tier 1 KPI. This is the only source of data available to you - every "
    "statistic, KPI id and gap id in your answer must come from its output."
)

# strict:true requires additionalProperties:false and every property listed in `required`,
# so optional arguments are expressed as a nullable union type instead of being absent.
TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "kpi_ids": {
            "type": ["array", "null"],
            "items": {"type": "string"},
            "description": "Tier 1 KPI ids to analyse, e.g. ['T1-01','T1-11']. Use null (recommended) "
                           "to analyse every Tier 1 KPI the KPI engine flagged as Critical. Ids that "
                           "are not Critical are ignored and reported back in `notes`.",
        },
        "top_n_activities": {
            "type": "integer",
            "description": "How many waiting hotspots and effort hotspots to return (default 20).",
        },
        "top_n_rework_loops": {
            "type": "integer",
            "description": "How many rework loops to return, ranked by business days lost (default 12).",
        },
        "include_stalled_cases": {
            "type": "boolean",
            "description": "Include the stalled-case summary and the oldest open cases.",
        },
        "include_segments": {
            "type": "boolean",
            "description": "Include the KPI position by business unit, job family, location, job "
                           "level, priority, source channel and recruiter.",
        },
        "include_case_metrics": {
            "type": "boolean",
            "description": "Include aggregated requisition / application metrics and the worst cases.",
        },
    },
    "required": ["kpi_ids", "top_n_activities", "top_n_rework_loops", "include_stalled_cases",
                 "include_segments", "include_case_metrics"],
    "additionalProperties": False,
}

# Responses API function tool: flat shape, schema under `parameters`.
TOOL_DEFINITION = {
    "type": "function",
    "name": TOOL_NAME,
    "description": TOOL_DESCRIPTION,
    "strict": True,
    "parameters": TOOL_PARAMETERS,
}

DEFAULTS = {
    "kpi_ids": None,
    "top_n_activities": 20,
    "top_n_rework_loops": 12,
    "include_stalled_cases": True,
    "include_segments": True,
    "include_case_metrics": True,
}

STALLED_SAMPLE = 20          # oldest open cases returned alongside the stalled summary
RECRUITER_SAMPLE = 15        # most loaded recruiters returned alongside the load summary
WORST_CASE_SAMPLE = 10       # slowest requisitions returned alongside the aggregates
GAP_EVIDENCE_SAMPLE = 3      # evidence summaries returned per detected gap
SEGMENT_MIN_CASES = 15       # a segment below this size is not reported - too small to compare


# ------------------------------------------------------------------ JSON helpers
def _clean(v):
    """psycopg2 gives Decimal / date / datetime - make everything JSON-serialisable."""
    if isinstance(v, decimal.Decimal):
        f = float(v)
        return int(f) if f.is_integer() else round(f, 4)
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat(sep=" ", timespec="minutes") if isinstance(v, _dt.datetime) else v.isoformat()
    return v


def _rows(cur, sql, params=None):
    cur.execute(sql, params)
    return [{k: _clean(v) for k, v in dict(r).items()} for r in cur.fetchall()]


def _one(cur, sql, params=None):
    rows = _rows(cur, sql, params)
    return rows[0] if rows else None


def _split(value, sep):
    return [p.strip() for p in str(value or "").split(sep)
            if p.strip() and p.strip().lower() not in ("nan", "none")]


# ------------------------------------------------------------------ the tool
def get_root_cause_evidence(kpi_ids=None, top_n_activities=20, top_n_rework_loops=12,
                            include_stalled_cases=True, include_segments=True,
                            include_case_metrics=True, run_id=None, conn=None):
    """Build the fixed diagnostic package. Pass conn to reuse an open connection."""
    if conn is not None:
        return _build(conn, kpi_ids, top_n_activities, top_n_rework_loops,
                      include_stalled_cases, include_segments, include_case_metrics, run_id)
    from db_config import db                                  # imported lazily: no DB, no import cost
    with db() as c:
        return _build(c, kpi_ids, top_n_activities, top_n_rework_loops,
                      include_stalled_cases, include_segments, include_case_metrics, run_id)


# Whitelisted segmentation dimensions. Interpolated into SQL, so the whitelist is the guard.
REQ_SEGMENT_DIMENSIONS = ["business_unit", "job_family", "location", "job_level", "priority",
                          "demand_type"]
APP_SEGMENT_DIMENSIONS = ["source_channel", "candidate_engagement_profile"]


def _segment_sql(schema, dimension):
    return f"""
        SELECT %s AS dimension, {dimension}::text AS segment,
               COUNT(*) AS requisitions,
               COUNT(*) FILTER (WHERE requisition_outcome = 'Filled') AS filled,
               COUNT(*) FILTER (WHERE requisition_outcome = 'Cancelled') AS cancelled,
               ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (
                     ORDER BY time_to_fill_business_days)::numeric, 1) AS median_time_to_fill_days,
               ROUND(AVG(days_to_approval)::numeric, 2)   AS avg_approval_days,
               ROUND(AVG(days_to_assignment)::numeric, 2) AS avg_assignment_days,
               ROUND(AVG(rework_loop_count)::numeric, 2)  AS avg_rework_loops,
               ROUND(AVG(manual_touch_count)::numeric, 1) AS avg_manual_touches,
               ROUND(100.0 * SUM(sla_breach_count) / NULLIF(SUM(sla_eligible_count), 0), 1)
                                                          AS sla_breach_pct,
               ROUND(AVG(max_inactivity_business_days)::numeric, 2) AS avg_max_inactivity_days
        FROM {schema}.requisition_master
        GROUP BY {dimension}
        HAVING COUNT(*) >= %s"""


def _app_segment_sql(schema, dimension):
    return f"""
        SELECT %s AS dimension, {dimension}::text AS segment,
               COUNT(*) AS applications,
               COUNT(*) FILTER (WHERE application_outcome = 'Joined')    AS joined,
               COUNT(*) FILTER (WHERE application_outcome = 'Withdrawn') AS withdrawn,
               ROUND(100.0 * COUNT(*) FILTER (WHERE application_outcome = 'Withdrawn')
                     / NULLIF(COUNT(*), 0), 1) AS withdrawal_rate_pct,
               COUNT(*) FILTER (WHERE offer_made_flag = 'Y') AS offers,
               ROUND(100.0 * COUNT(*) FILTER (WHERE offer_acceptance_flag = 'Y')
                     / NULLIF(COUNT(*) FILTER (WHERE offer_acceptance_flag IN ('Y','N')), 0), 1)
                                              AS offer_acceptance_pct,
               ROUND(AVG(max_inactivity_business_days)::numeric, 2) AS avg_max_inactivity_days,
               ROUND(AVG(interview_reschedule_count)::numeric, 2)   AS avg_reschedules,
               ROUND(AVG(rework_loop_count)::numeric, 2)            AS avg_rework_loops
        FROM {schema}.application_master
        GROUP BY {dimension}
        HAVING COUNT(*) >= %s"""


def _build(conn, kpi_ids, top_n_activities, top_n_rework_loops,
           include_stalled_cases, include_segments, include_case_metrics, run_id):
    import psycopg2.extras
    from db_config import SCHEMA as S

    top_n_activities = max(1, int(top_n_activities or DEFAULTS["top_n_activities"]))
    top_n_rework_loops = max(1, int(top_n_rework_loops or DEFAULTS["top_n_rework_loops"]))
    notes = []

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # ---------------------------------------------------------- the run
        run = _one(cur, f"""SELECT run_id, run_timestamp, as_of_timestamp, period_from, period_to,
                                   requisitions_total, requisitions_closed, requisitions_filled,
                                   requisitions_open, applications_total, applications_joined,
                                   cases_stalled, events_total
                            FROM {S}.kpi_run
                            {'WHERE run_id = %s' if run_id else ''}
                            ORDER BY run_id DESC LIMIT 1;""", [run_id] if run_id else None)
        if not run:
            raise RuntimeError(
                "No KPI run found. Run 04_compute_kpis_to_postgres.py before the Root Cause Agent."
            )
        rid = run["run_id"]

        summary = _one(cur, f"SELECT * FROM {S}.kpi_summary WHERE run_id = %s;", [rid])
        if summary:
            summary.pop("id", None)
            summary.pop("run_id", None)

        # ---------------------------------------------------------- Tier 1
        tier1 = _rows(cur, f"""SELECT kpi_id, kpi_name, formula, current_value, target, unit,
                                      better_when, variance_vs_target_pct, health, trend_direction,
                                      previous_value, denominator_cases, cohort_rule, tier2_drivers
                               FROM {S}.kpi_tier1_health WHERE run_id = %s ORDER BY kpi_id;""", [rid])

        critical = [k for k in tier1 if k["health"] == "Critical"]
        if kpi_ids:
            wanted = {k.strip().upper() for k in kpi_ids}
            skipped = sorted(wanted - {k["kpi_id"] for k in critical})
            critical = [k for k in critical if k["kpi_id"] in wanted]
            if skipped:
                notes.append(f"Requested KPI id(s) {', '.join(skipped)} are not Critical in run {rid} "
                             f"and were excluded - only Critical Tier 1 KPIs are analysed.")
        crit_ids = [k["kpi_id"] for k in critical]

        empty = {"run_stats": run, "summary": summary, "critical_tier1": [], "tier1_all": tier1,
                 "linked_tier2": [], "root_cause_links": [], "gaps": [], "gap_detections": [],
                 "stages": [], "activities": [], "rework_loops": [], "stalled": None,
                 "recruiter_load": None, "segments": None, "case_metrics": None,
                 "monthly_trend": [], "notes": notes}
        if not crit_ids:
            notes.append(f"No Tier 1 KPI is Critical in run {rid}. There is nothing to diagnose.")
            return empty

        # ------------------------------------------- Tier1 -> Tier2 -> Gap edges
        # The Talent Acquisition schema carries these links as columns on kpi_tier2_diagnostics
        # rather than in a bridge table, so the edges are expanded here.
        tier2_all = _rows(cur, f"""SELECT kpi_id, kpi_name, formula, current_value, target, unit,
                                          better_when, variance_vs_target_pct, health,
                                          feeds_tier1_kpi, gap_reference, root_cause_category,
                                          denominator_cases, cohort_rule
                                   FROM {S}.kpi_tier2_diagnostics WHERE run_id = %s
                                   ORDER BY kpi_id;""", [rid])
        tier2, links = [], []
        for t in tier2_all:
            feeds = _split(t["feeds_tier1_kpi"], ",")
            hit = [f for f in feeds if f in crit_ids]
            if not hit:
                continue
            t["feeds_tier1"] = sorted(hit)
            t["gaps"] = _split(t.pop("gap_reference"), ";")
            t.pop("feeds_tier1_kpi", None)
            tier2.append(t)
            for t1 in sorted(hit):
                for g in (t["gaps"] or [None]):
                    links.append({"tier1_kpi_id": t1, "tier2_kpi_id": t["kpi_id"],
                                  "gap_reference": g, "tier2_health": t["health"]})
        # worst-first: the agent still ranks, but the severe drivers are easiest to see at the top
        tier2.sort(key=lambda t: (t["health"] != "Critical", -(t["variance_vs_target_pct"] or 0)))
        if not tier2:
            notes.append(f"No Tier 2 KPI declares any of {', '.join(crit_ids)} in feeds_tier1_kpi. "
                         f"Diagnose from the stage, activity, rework and gap evidence instead.")

        gap_ids = sorted({g for t in tier2 for g in t["gaps"]})
        gaps = _rows(cur, f"""SELECT gap_reference, stage_or_domain, gap_definition, tier2_evidence,
                                     tier1_impact, illustrative_detection_rule,
                                     rca_hypothesis_not_fact, intervention_options
                              FROM {S}.gap_mapping WHERE gap_reference = ANY(%s)
                              ORDER BY gap_reference;""", [gap_ids]) if gap_ids else []
        if gap_ids and not gaps:
            notes.append("gap_mapping is empty - gap ids are available on the Tier 2 KPIs but their "
                         "definitions could not be loaded.")

        # ------------------------------- how often each gap was actually detected in the log
        gap_detections = _rows(cur, f"""
            SELECT gap_reference,
                   COUNT(*)                                   AS findings,
                   COUNT(DISTINCT process_instance_id)         AS cases,
                   COUNT(DISTINCT requisition_id)              AS requisitions,
                   MIN(stage_code)                             AS stage_code,
                   MIN(measure_unit)                           AS measure_unit,
                   ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY measured_value)::numeric, 2)
                                                               AS median_measured_value,
                   ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY measured_value)::numeric, 2)
                                                               AS p90_measured_value,
                   ROUND(MIN(threshold_value)::numeric, 2)     AS threshold_value,
                   MIN(detection_rule)                         AS detection_rule
            FROM {S}.gap_detection
            {'WHERE gap_reference = ANY(%s)' if gap_ids else ''}
            GROUP BY gap_reference ORDER BY findings DESC;""", [gap_ids] if gap_ids else None)
        for g in gap_detections:
            g["evidence_examples"] = [r["evidence_summary"] for r in _rows(cur, f"""
                SELECT evidence_summary FROM {S}.gap_detection
                WHERE gap_reference = %s AND evidence_summary IS NOT NULL
                ORDER BY measured_value DESC NULLS LAST LIMIT %s;""",
                [g["gap_reference"], GAP_EVIDENCE_SAMPLE])]
        if not gap_detections:
            notes.append("gap_detection is empty - no deterministic gap rule fired for this extract, "
                         "so gap references carry no detected-volume evidence.")

        # ---------------------------------------------------------- process evidence
        stages = _rows(cur, f"""SELECT case_type, stage_code, stage_name, cases, occurrences,
                                       revisit_rate_pct, median_elapsed_days, avg_elapsed_days,
                                       p90_elapsed_days, share_of_cycle_pct, stage_sla_days,
                                       sla_breach_pct, avg_recorded_waiting_days,
                                       avg_processing_hours, avg_events, avg_manual_events,
                                       rework_events_per_case, exception_events_per_case,
                                       handoffs_per_case, gap_references, health
                                FROM {S}.kpi_stage_breakdown WHERE run_id = %s
                                ORDER BY case_type, stage_sequence;""", [rid])

        # container waits (the interval before a closure event, or a contractual notice period) are
        # excluded: that elapsed time is already measured on the linked application cases
        activities = _rows(cur, f"""SELECT activity_name, stage_code, activity_stage, actor_persona,
                                           source_system, events, cases, cases_touched_pct,
                                           avg_duration_min, avg_waiting_min, total_waiting_days,
                                           repeat_event_pct, sla_breach_pct, avg_sla_overrun_days,
                                           exception_rate_pct, rework_share_pct, manual_pct,
                                           handoff_pct, top_waiting_reason, waiting_rank, effort_rank
                                    FROM {S}.kpi_activity_breakdown
                                    WHERE run_id = %s AND wait_is_container = 'N'
                                      AND (waiting_rank <= %s OR effort_rank <= %s)
                                    ORDER BY waiting_rank NULLS LAST;""",
                           [rid, top_n_activities, top_n_activities])

        rework = _rows(cur, f"""SELECT rework_loop_type, case_type, stage_code, activity_stage,
                                       cases, events, cases_pct, loops_per_affected_case, days_lost,
                                       avg_days_lost_per_affected_case, top_reason_code, gap_reference
                                FROM {S}.kpi_rework_loops WHERE run_id = %s
                                ORDER BY days_lost DESC NULLS LAST LIMIT %s;""",
                       [rid, top_n_rework_loops])

        trend = _rows(cur, f"""SELECT month, requisitions_opened, requisitions_filled, hires,
                                      applications_received, offers_sent, time_to_fill_days,
                                      approval_lead_time_days, sla_attainment_pct,
                                      offer_acceptance_pct, withdrawal_rate_pct, rework_rate_pct,
                                      manual_touches_per_case, waiting_days_per_case,
                                      recruiter_load_median
                               FROM {S}.kpi_monthly_trend WHERE run_id = %s ORDER BY month;""", [rid])

        # ---------------------------------------------------------- stalled cases
        stalled = None
        if include_stalled_cases:
            stalled = {
                "total_stalled_cases": run["cases_stalled"],
                "by_stage": _rows(cur, f"""SELECT case_type, activity_stage, COUNT(*) AS stalled_cases,
                                                  ROUND(AVG(open_business_days), 1) AS avg_open_days,
                                                  ROUND(MAX(open_business_days), 1) AS max_open_days
                                           FROM {S}.kpi_stalled_cases WHERE run_id = %s
                                           GROUP BY case_type, activity_stage
                                           ORDER BY stalled_cases DESC;""", [rid]),
                "by_persona": _rows(cur, f"""SELECT actor_persona, COUNT(*) AS stalled_cases,
                                                    ROUND(AVG(open_business_days), 1) AS avg_open_days
                                             FROM {S}.kpi_stalled_cases WHERE run_id = %s
                                             GROUP BY actor_persona
                                             ORDER BY stalled_cases DESC;""", [rid]),
                "by_waiting_reason": _rows(cur, f"""SELECT COALESCE(NULLIF(waiting_reason_code, ''),
                                                                   'Not recorded') AS waiting_reason_code,
                                                           COUNT(*) AS stalled_cases,
                                                           ROUND(AVG(open_business_days), 1) AS avg_open_days
                                                    FROM {S}.kpi_stalled_cases WHERE run_id = %s
                                                    GROUP BY 1 ORDER BY stalled_cases DESC LIMIT 12;""", [rid]),
                "by_open_sla": _rows(cur, f"""SELECT COALESCE(open_sla_code, 'none') AS open_sla_code,
                                                     COUNT(*) AS stalled_cases,
                                                     ROUND(AVG(open_sla_overrun_days), 1) AS avg_overrun_days
                                              FROM {S}.kpi_stalled_cases WHERE run_id = %s
                                              GROUP BY 1 ORDER BY stalled_cases DESC LIMIT 12;""", [rid]),
                "oldest_open_cases": _rows(cur, f"""SELECT process_instance_id, case_type, requisition_id,
                                                           application_id, last_activity_name,
                                                           activity_stage, actor_persona,
                                                           open_business_days, stage_sla_days,
                                                           waiting_reason_code, open_sla_code,
                                                           open_sla_overrun_days, business_unit,
                                                           job_family, job_level, priority,
                                                           gap_references, recommended_action
                                                    FROM {S}.kpi_stalled_cases WHERE run_id = %s
                                                    ORDER BY open_business_days DESC LIMIT %s;""",
                                           [rid, STALLED_SAMPLE]),
            }

        # ---------------------------------------------------------- recruiter capacity
        recruiter_load = {
            "note": "Weekly snapshots of weighted open-requisition load per recruiter. Compare "
                    "like-for-like case mix before drawing a conclusion about an individual.",
            "distribution": _one(cur, f"""SELECT COUNT(DISTINCT recruiter_id_pseudonymous) AS recruiters,
                           COUNT(*) AS snapshots,
                           ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY weighted_load)::numeric, 2)
                                                                                AS median_weighted_load,
                           ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY weighted_load)::numeric, 2)
                                                                                AS p90_weighted_load,
                           ROUND(MAX(weighted_load)::numeric, 2)                AS max_weighted_load,
                           ROUND(AVG(open_requisitions)::numeric, 1)            AS avg_open_requisitions,
                           ROUND(AVG(requisitions_open_over_60_days)::numeric, 2) AS avg_aged_requisitions
                    FROM {S}.recruiter_load_snapshot;"""),
            "by_design_band": _rows(cur, f"""SELECT portfolio_design_band,
                                                    COUNT(DISTINCT recruiter_id_pseudonymous) AS recruiters,
                                                    ROUND(AVG(weighted_load)::numeric, 2) AS avg_weighted_load,
                                                    ROUND(AVG(open_requisitions)::numeric, 1) AS avg_open_reqs,
                                                    ROUND(AVG(requisitions_open_over_60_days)::numeric, 2)
                                                        AS avg_aged_reqs
                                             FROM {S}.recruiter_load_snapshot
                                             GROUP BY portfolio_design_band
                                             ORDER BY avg_weighted_load DESC;"""),
            "most_loaded_recruiters": _rows(cur, f"""
                    SELECT l.recruiter_id_pseudonymous,
                           ROUND(AVG(l.weighted_load)::numeric, 2) AS avg_weighted_load,
                           ROUND(MAX(l.weighted_load)::numeric, 2) AS peak_weighted_load,
                           ROUND(AVG(l.open_requisitions)::numeric, 1) AS avg_open_requisitions,
                           ROUND(AVG(l.requisitions_open_over_60_days)::numeric, 2) AS avg_aged_requisitions,
                           (SELECT COUNT(*) FROM {S}.requisition_master r
                             WHERE r.recruiter_id_pseudonymous = l.recruiter_id_pseudonymous) AS requisitions,
                           (SELECT ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (
                                     ORDER BY r.time_to_fill_business_days)::numeric, 1)
                              FROM {S}.requisition_master r
                             WHERE r.recruiter_id_pseudonymous = l.recruiter_id_pseudonymous)
                                                                       AS median_time_to_fill_days,
                           (SELECT ROUND(AVG(r.days_to_assignment)::numeric, 2)
                              FROM {S}.requisition_master r
                             WHERE r.recruiter_id_pseudonymous = l.recruiter_id_pseudonymous)
                                                                       AS avg_assignment_days
                    FROM {S}.recruiter_load_snapshot l
                    GROUP BY l.recruiter_id_pseudonymous
                    ORDER BY avg_weighted_load DESC LIMIT %s;""", [RECRUITER_SAMPLE]),
        }

        # ---------------------------------------------------------- segment evidence
        segments = None
        if include_segments:
            req_seg, app_seg = [], []
            for dim in REQ_SEGMENT_DIMENSIONS:
                req_seg += _rows(cur, _segment_sql(S, dim) + " ORDER BY 3 DESC;",
                                 [dim, SEGMENT_MIN_CASES])
            for dim in APP_SEGMENT_DIMENSIONS:
                app_seg += _rows(cur, _app_segment_sql(S, dim) + " ORDER BY 3 DESC;",
                                 [dim, SEGMENT_MIN_CASES])
            segments = {
                "note": f"Segments with fewer than {SEGMENT_MIN_CASES} cases are omitted - too small "
                        f"to compare. Compare like-for-like role complexity before concluding that a "
                        f"segment performs badly; senior and overseas roles legitimately take longer.",
                "requisition_segments": req_seg,
                "application_segments": app_seg,
                "hiring_manager_outliers": _rows(cur, f"""
                    SELECT hiring_manager_id_pseudonymous, COUNT(*) AS requisitions,
                           ROUND(AVG(days_to_approval)::numeric, 2) AS avg_approval_days,
                           ROUND(AVG(rework_loop_count)::numeric, 2) AS avg_rework_loops,
                           ROUND(AVG(demand_return_count)::numeric, 2) AS avg_demand_returns
                    FROM {S}.requisition_master GROUP BY 1 HAVING COUNT(*) >= %s
                    ORDER BY avg_rework_loops DESC NULLS LAST LIMIT 10;""", [SEGMENT_MIN_CASES]),
            }

        # ---------------------------------------------------------- case-level evidence
        case_metrics = None
        if include_case_metrics:
            case_metrics = {
                "note": "requisition_master holds one row per requisition and application_master one "
                        "row per application. The aggregates below cover every case in the extract; "
                        "open cases are right-censored and excluded from completion measures.",
                "requisitions": _one(cur, f"""SELECT COUNT(*) AS requisitions,
                       COUNT(*) FILTER (WHERE is_open_censored = 'Y')                AS open_censored,
                       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (
                             ORDER BY time_to_fill_business_days)::numeric, 1)       AS median_time_to_fill_days,
                       ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (
                             ORDER BY time_to_fill_business_days)::numeric, 1)       AS p90_time_to_fill_days,
                       ROUND(MAX(time_to_fill_business_days)::numeric, 1)            AS max_time_to_fill_days,
                       ROUND(AVG(days_to_approval)::numeric, 2)                      AS avg_approval_days,
                       ROUND(AVG(days_to_assignment)::numeric, 2)                    AS avg_assignment_days,
                       ROUND(AVG(days_to_posting)::numeric, 2)                       AS avg_posting_days,
                       ROUND(AVG(days_to_first_shortlist)::numeric, 2)               AS avg_first_shortlist_days,
                       ROUND(AVG(applications_count)::numeric, 1)                    AS avg_applications,
                       ROUND(AVG(rework_loop_count)::numeric, 2)                     AS avg_rework_loops,
                       ROUND(AVG(manual_touch_count)::numeric, 1)                    AS avg_manual_touches,
                       ROUND(AVG(follow_up_event_count)::numeric, 1)                 AS avg_follow_ups,
                       ROUND(AVG(handoff_count)::numeric, 1)                         AS avg_handoffs,
                       ROUND(AVG(max_inactivity_business_days)::numeric, 2)          AS avg_max_inactivity_days,
                       COUNT(*) FILTER (WHERE integration_failure_count > 0)         AS with_integration_failure,
                       COUNT(*) FILTER (WHERE policy_exception_documented_flag = 'Y') AS with_documented_exception
                FROM {S}.requisition_master;"""),
                "applications": _one(cur, f"""SELECT COUNT(*) AS applications,
                       COUNT(*) FILTER (WHERE is_open_censored = 'Y')                AS open_censored,
                       COUNT(*) FILTER (WHERE application_outcome = 'Joined')        AS joined,
                       COUNT(*) FILTER (WHERE application_outcome = 'Withdrawn')     AS withdrawn,
                       COUNT(*) FILTER (WHERE application_outcome = 'Offer Declined') AS offer_declined,
                       COUNT(*) FILTER (WHERE application_outcome = 'Accepted - Did Not Join')
                                                                                     AS accepted_not_joined,
                       ROUND(AVG(number_of_interview_rounds)::numeric, 2)            AS avg_interview_rounds,
                       ROUND(AVG(interview_reschedule_count)::numeric, 2)            AS avg_reschedules,
                       ROUND(AVG(feedback_late_count)::numeric, 2)                   AS avg_late_feedback,
                       ROUND(AVG(offer_negotiation_count)::numeric, 2)               AS avg_negotiations,
                       ROUND(AVG(document_resubmission_count)::numeric, 2)           AS avg_doc_resubmissions,
                       ROUND(AVG(max_inactivity_business_days)::numeric, 2)          AS avg_max_inactivity_days,
                       ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (
                             ORDER BY max_inactivity_business_days)::numeric, 2)     AS p90_max_inactivity_days
                FROM {S}.application_master;"""),
                "withdrawal_by_stage": _rows(cur, f"""SELECT withdrawal_stage, COUNT(*) AS withdrawals,
                               ROUND(AVG(max_inactivity_business_days)::numeric, 2) AS avg_max_inactivity_days
                        FROM {S}.application_master
                        WHERE application_outcome = 'Withdrawn' AND withdrawal_stage IS NOT NULL
                        GROUP BY withdrawal_stage ORDER BY withdrawals DESC;"""),
                "withdrawal_reasons": _rows(cur, f"""SELECT outcome_reason_code, COUNT(*) AS withdrawals
                        FROM {S}.application_master WHERE application_outcome = 'Withdrawn'
                        GROUP BY 1 ORDER BY withdrawals DESC LIMIT 10;"""),
                "slowest_requisitions": _rows(cur, f"""SELECT requisition_id, business_unit, job_family,
                               location, job_level, priority, time_to_fill_business_days,
                               days_to_approval, days_to_assignment, days_to_posting,
                               applications_count, rework_loop_count, manual_touch_count,
                               sla_breach_count, recruiter_id_pseudonymous,
                               recruiter_weighted_load_at_assignment
                        FROM {S}.requisition_master WHERE time_to_fill_business_days IS NOT NULL
                        ORDER BY time_to_fill_business_days DESC LIMIT %s;""", [WORST_CASE_SAMPLE]),
            }

    return {
        "run_stats": run,
        "summary": summary,
        "critical_tier1": critical,
        "tier1_all": tier1,
        "linked_tier2": tier2,
        "root_cause_links": links,
        "gaps": gaps,
        "gap_detections": gap_detections,
        "stages": stages,
        "activities": activities,
        "rework_loops": rework,
        "stalled": stalled,
        "recruiter_load": recruiter_load,
        "segments": segments,
        "case_metrics": case_metrics,
        "monthly_trend": trend,
        "notes": notes,
    }


def run_tool(tool_input, run_id=None, conn=None):
    """Execute the tool from the raw argument dict the model sends, applying the documented defaults."""
    args = {**DEFAULTS, **{k: v for k, v in (tool_input or {}).items() if v is not None}}
    return get_root_cause_evidence(
        kpi_ids=args["kpi_ids"],
        top_n_activities=args["top_n_activities"],
        top_n_rework_loops=args["top_n_rework_loops"],
        include_stalled_cases=args["include_stalled_cases"],
        include_segments=args["include_segments"],
        include_case_metrics=args["include_case_metrics"],
        run_id=run_id, conn=conn,
    )


# ------------------------------------------------------------------ CLI
def main():
    p = argparse.ArgumentParser(
        description="Build the Root Cause Agent evidence package from the Talent Acquisition KPI tables")
    p.add_argument("--kpi-ids", nargs="*", default=None, help="Tier 1 KPI ids (default: every Critical KPI)")
    p.add_argument("--run-id", type=int, default=None, help="KPI run to read (default: latest)")
    p.add_argument("--top-n-activities", type=int, default=DEFAULTS["top_n_activities"])
    p.add_argument("--top-n-rework-loops", type=int, default=DEFAULTS["top_n_rework_loops"])
    p.add_argument("--no-stalled-cases", action="store_true")
    p.add_argument("--no-segments", action="store_true")
    p.add_argument("--no-case-metrics", action="store_true")
    p.add_argument("--out", default=None, help="write the evidence package to this file instead of stdout")
    a = p.parse_args()

    pack = get_root_cause_evidence(
        kpi_ids=a.kpi_ids, top_n_activities=a.top_n_activities,
        top_n_rework_loops=a.top_n_rework_loops,
        include_stalled_cases=not a.no_stalled_cases,
        include_segments=not a.no_segments,
        include_case_metrics=not a.no_case_metrics, run_id=a.run_id,
    )
    text = json.dumps(pack, indent=2)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text)
        crit = ", ".join(k["kpi_id"] for k in pack["critical_tier1"]) or "none"
        print(f"Evidence package for run {pack['run_stats']['run_id']} written to {a.out} "
              f"({len(text):,} chars) - Critical Tier 1: {crit}")
    else:
        print(text)


if __name__ == "__main__":
    main()
