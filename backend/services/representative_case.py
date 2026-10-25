"""
backend.services.representative_case - (3) THE MOST REPRESENTATIVE CASE.

Explicitly not "the first impacted case". Candidates are scored on evidence pulled from the event
log, and the scoring itself (score_candidates) is a pure function so it can be unit-tested without
a database.

What the score rewards:

    matched events        the root cause is strongly present in this case
    matched waiting       waiting time attributable to the root cause
    matched rework        repeated work attributable to the root cause
    selector coverage     the case shows several kinds of evidence, not just one
    sla breached          the case actually misses the policy it is measured against
    exceptions            exception events on the matching activities
    evidence volume       a case with a handful of events proves little

and what it penalises:

    atypicality           distance from the MEDIAN impacted case. The most extreme case is the
                          least representative one - it would overstate the problem, and the UI
                          presents this case as typical of the impacted population. Candidates
                          outside the P10-P90 elapsed-time band are dropped before scoring
                          (drop_outliers), and the remaining distance is penalised heavily enough
                          to outweigh evidence volume.

Two Talent Acquisition specifics:

  * Open cases are excluded from selection. A case still in flight has no finished journey to
    show, and presenting one as representative would imply an outcome that has not happened.
  * The candidate pool is restricted to the root cause's own case type, so an application-level
    finding never illustrates itself with a requisition.

The model never picks the case. If a narrative layer is added later it receives the ranked
candidates and writes the explanation - the selection stays deterministic.
"""
from __future__ import annotations

from typing import List, Optional

from .. import db
from . import fix_selectors as sel
from .impact_service import BUSINESS_MIN_PER_DAY

WEIGHTS = {
    "matched_events": 3.0,
    "matched_waiting": 2.0,
    "matched_rework": 1.5,
    "selector_coverage": 1.0,
    "sla_breached": 1.0,
    "exceptions": 0.5,
    "evidence_volume": 0.5,
    # Strong enough to actually decide the choice: the evidence terms can contribute at most 9.5,
    # and a case at 2x the median elapsed time must not win on evidence volume alone.
    "atypicality": -2.5,
}
CANDIDATE_POOL = 200
OUTLIER_BAND = (0.10, 0.90)     # candidates outside this elapsed-time band are not representative
MIN_POOL_FOR_OUTLIER_FILTER = 10


def candidate_features(conn, selectors: dict, limit: int = CANDIDATE_POOL) -> List[dict]:
    """Raw per-case features for every impacted, completed case (bounded)."""
    s = db.schema()
    params = sel.match_params(selectors)
    case_type = selectors.get("case_type") or "APPLICATION"
    params["case_type"] = case_type

    matched = f"""
        SELECT e.process_instance_id,
               COUNT(*) FILTER (WHERE e.activity_name = ANY(%(activities)s))    AS matched_events,
               SUM(COALESCE(e.waiting_time_minutes, 0))
                   FILTER (WHERE e.activity_name = ANY(%(activities)s))         AS matched_waiting_minutes,
               COUNT(*) FILTER (WHERE e.activity_name = ANY(%(activities)s)
                                  AND e.rework_flag = 'Y')                      AS matched_rework_events,
               COUNT(*) FILTER (WHERE e.activity_name = ANY(%(activities)s)
                                  AND e.exception_flag = 'Y')                   AS matched_exception_events,
               COUNT(*) FILTER (WHERE e.activity_name = ANY(%(activities)s)
                                  AND e.manual_touch_flag = 'Y')                AS matched_manual_events,
               COUNT(DISTINCT e.activity_name) FILTER (
                    WHERE e.activity_name = ANY(%(activities)s))                AS selector_kinds,
               COUNT(*)                                                         AS event_count
        FROM {s}.event_log e
        WHERE e.case_type = %(case_type)s AND {sel.match_sql(s)}
        GROUP BY 1"""

    if case_type == "APPLICATION":
        return db.rows(conn, f"""
            WITH matched AS ({matched})
            SELECT m.process_instance_id AS case_id, 'APPLICATION' AS case_type,
                   m.matched_events, m.matched_waiting_minutes, m.matched_rework_events,
                   m.matched_exception_events, m.matched_manual_events, m.selector_kinds,
                   m.event_count,
                   a.requisition_id, a.candidate_surrogate_id, a.source_channel,
                   a.business_unit, a.job_family, a.location, a.job_level, a.priority,
                   a.application_outcome, a.outcome_stage, a.outcome_reason_code,
                   a.total_elapsed_business_days AS elapsed_days,
                   a.max_inactivity_business_days, a.number_of_interview_rounds,
                   a.interview_reschedule_count, a.rework_loop_count, a.manual_touch_count,
                   a.handoff_count, a.sla_breach_count, a.sla_eligible_count,
                   a.recruiter_id_pseudonymous, a.hiring_manager_id_pseudonymous
            FROM matched m
            JOIN {s}.application_master a ON a.application_id = m.process_instance_id
            WHERE a.is_open_censored = 'N'
            ORDER BY m.matched_events DESC, m.matched_waiting_minutes DESC
            LIMIT {int(limit)};""", params)

    return db.rows(conn, f"""
        WITH matched AS ({matched})
        SELECT m.process_instance_id AS case_id, 'REQUISITION' AS case_type,
               m.matched_events, m.matched_waiting_minutes, m.matched_rework_events,
               m.matched_exception_events, m.matched_manual_events, m.selector_kinds,
               m.event_count,
               c.requisition_id, c.business_unit, c.job_family, c.location, c.job_level,
               c.priority, c.demand_type, c.requisition_outcome AS application_outcome,
               c.closure_reason_code AS outcome_reason_code,
               c.time_to_fill_business_days AS elapsed_days, c.business_days_open,
               c.max_inactivity_business_days, c.applications_count, c.rework_loop_count,
               c.manual_touch_count, c.handoff_count, c.sla_breach_count, c.sla_eligible_count,
               c.recruiter_id_pseudonymous, c.hiring_manager_id_pseudonymous,
               c.recruiter_weighted_load_at_assignment
        FROM matched m
        JOIN {s}.requisition_master c ON c.requisition_id = m.process_instance_id
        WHERE c.is_open_censored = 'N'
        ORDER BY m.matched_events DESC, m.matched_waiting_minutes DESC
        LIMIT {int(limit)};""", params)


def _median(values: List[float]) -> Optional[float]:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


def _percentile(values: List[float], q: float) -> Optional[float]:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = q * (len(vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def drop_outliers(features: List[dict]) -> List[dict]:
    """Remove candidates whose elapsed time sits outside the P10-P90 band of the pool.

    A case four times the median is a real case, but it is not a representative one - showing it
    as typical would overstate the problem. The filter only engages once the pool is large enough
    for percentiles to mean anything, and it never empties the pool.
    """
    elapsed = [f.get("elapsed_days") for f in features if f.get("elapsed_days") is not None]
    if len(features) < MIN_POOL_FOR_OUTLIER_FILTER or len(elapsed) < MIN_POOL_FOR_OUTLIER_FILTER:
        return features
    lo = _percentile(elapsed, OUTLIER_BAND[0])
    hi = _percentile(elapsed, OUTLIER_BAND[1])
    kept = [f for f in features
            if f.get("elapsed_days") is None or lo <= f["elapsed_days"] <= hi]
    return kept or features


def score_candidates(features: List[dict], top_n: int = 5) -> List[dict]:
    """Rank candidates. Pure function - no database, no model, fully unit-testable."""
    if not features:
        return []
    features = drop_outliers(features)

    def norm(key):
        top = max((f.get(key) or 0) for f in features) or 1
        return lambda f: (f.get(key) or 0) / top

    n_events = norm("matched_events")
    n_waiting = norm("matched_waiting_minutes")
    n_rework = norm("matched_rework_events")
    n_exc = norm("matched_exception_events")
    n_volume = norm("event_count")
    max_kinds = max((f.get("selector_kinds") or 0) for f in features) or 1
    median_elapsed = _median([f.get("elapsed_days") for f in features]) or 0

    ranked = []
    for f in features:
        elapsed = f.get("elapsed_days")
        atypicality = 0.0
        if median_elapsed and elapsed is not None:
            atypicality = min(abs(elapsed - median_elapsed) / median_elapsed, 2.0)

        components = {
            "matched_events": n_events(f),
            "matched_waiting": n_waiting(f),
            "matched_rework": n_rework(f),
            "selector_coverage": (f.get("selector_kinds") or 0) / max_kinds,
            "sla_breached": 1.0 if (f.get("sla_breach_count") or 0) > 0 else 0.0,
            "exceptions": n_exc(f),
            "evidence_volume": n_volume(f),
            "atypicality": atypicality,
        }
        score = sum(WEIGHTS[k] * v for k, v in components.items())
        ranked.append({**f, "selection_score": round(score, 3),
                       "score_components": {k: round(v, 3) for k, v in components.items()}})

    ranked.sort(key=lambda r: (-r["selection_score"], str(r["case_id"])))
    return ranked[:top_n]


def case_evidence(conn, case_id: str, selectors: dict) -> List[dict]:
    """Deterministic evidence rows: what in this case ties it to the root cause."""
    s = db.schema()
    params = {**sel.match_params(selectors), "case_id": case_id, "mins": BUSINESS_MIN_PER_DAY}
    rows = db.rows(conn, f"""
        SELECT e.activity_name, e.activity_stage, e.actor_persona, e.reason_code,
               COUNT(*)                                                 AS events,
               ROUND(SUM(COALESCE(e.waiting_time_minutes, 0)) / %(mins)s::numeric, 2)
                                                                        AS waiting_days,
               COUNT(*) FILTER (WHERE e.rework_flag = 'Y')              AS rework_events,
               COUNT(*) FILTER (WHERE e.exception_flag = 'Y')           AS exception_events,
               COUNT(*) FILTER (WHERE e.manual_touch_flag = 'Y')        AS manual_events,
               COUNT(*) FILTER (WHERE e.sla_breach_flag = 'Y')          AS sla_breaches,
               MAX(e.waiting_reason_code)                               AS waiting_reason
        FROM {s}.event_log e
        WHERE e.process_instance_id = %(case_id)s
          AND e.activity_name = ANY(%(activities)s)
        GROUP BY 1, 2, 3, 4
        ORDER BY waiting_days DESC NULLS LAST, events DESC
        LIMIT 8;""", params)

    evidence = []
    for r in rows:
        bits = [f"{r['events']} event(s)"]
        if r.get("rework_events"):
            bits.append(f"{r['rework_events']} rework")
        if r.get("sla_breaches"):
            bits.append(f"{r['sla_breaches']} SLA breach")
        if r.get("exception_events"):
            bits.append(f"{r['exception_events']} exception")
        if r.get("manual_events"):
            bits.append(f"{r['manual_events']} manual")
        if r.get("waiting_days"):
            bits.append(f"{r['waiting_days']} days waiting")
        etype = ("rework" if r.get("rework_events") else
                 "sla" if r.get("sla_breaches") else
                 "exception" if r.get("exception_events") else
                 "waiting" if r.get("waiting_days") else "activity")
        text = f"{r.get('activity_name')} ({r.get('activity_stage')}, {r.get('actor_persona')}): " \
               + ", ".join(bits)
        if r.get("waiting_reason"):
            text += f" - waiting reason {r['waiting_reason']}"
        elif r.get("reason_code"):
            text += f" - {r['reason_code']}"
        evidence.append({"evidence_type": etype, "evidence_text": text,
                         "evidence_score": r.get("waiting_days") or r.get("events")})
    return evidence


def gap_findings_for_case(conn, case_id: str, selectors: dict) -> List[dict]:
    """The deterministic gap detections recorded against this exact case."""
    s = db.schema()
    return db.rows(conn, f"""
        SELECT gap_reference, activity_stage, detection_rule, measured_value, threshold_value,
               measure_unit, evidence_summary
        FROM {s}.gap_detection
        WHERE process_instance_id = %s AND gap_reference = ANY(%s)
        ORDER BY measured_value DESC NULLS LAST;""",
        [case_id, list(selectors.get("gap_references") or [])])


def select(conn, selectors: dict, top_n: int = 5) -> dict:
    """Deterministically choose the representative case and gather what the UI shows."""
    features = candidate_features(conn, selectors)
    ranked = score_candidates(features, top_n=top_n)
    if not ranked:
        return {"case": None, "candidates": [],
                "note": "No completed impacted case was available to illustrate this root cause. "
                        "Every matching case is still open at the reporting cut-off."}

    best = ranked[0]
    case_id = best["case_id"]
    case = {
        "case_id": case_id,
        "case_type": best.get("case_type"),
        "requisition_id": best.get("requisition_id"),
        "candidate_surrogate_id": best.get("candidate_surrogate_id"),
        "selection_score": best.get("selection_score"),
        "score_components": best.get("score_components"),
        "outcome": best.get("application_outcome"),
        "outcome_stage": best.get("outcome_stage"),
        "outcome_reason_code": best.get("outcome_reason_code"),
        "business_unit": best.get("business_unit"),
        "job_family": best.get("job_family"),
        "location": best.get("location"),
        "job_level": best.get("job_level"),
        "priority": best.get("priority"),
        "source_channel": best.get("source_channel"),
        "elapsed_days": best.get("elapsed_days"),
        "max_inactivity_business_days": best.get("max_inactivity_business_days"),
        "interview_rounds": best.get("number_of_interview_rounds"),
        "interview_reschedules": best.get("interview_reschedule_count"),
        "applications_count": best.get("applications_count"),
        "rework_loops": best.get("rework_loop_count"),
        "handoffs": best.get("handoff_count"),
        "manual_touches": best.get("manual_touch_count"),
        "sla_breaches": best.get("sla_breach_count"),
        "sla_measured": best.get("sla_eligible_count"),
        "recruiter_id_pseudonymous": best.get("recruiter_id_pseudonymous"),
        "hiring_manager_id_pseudonymous": best.get("hiring_manager_id_pseudonymous"),
        "matched_events": best.get("matched_events"),
        "matched_waiting_days": round((best.get("matched_waiting_minutes") or 0)
                                      / BUSINESS_MIN_PER_DAY, 2),
        "evidence": case_evidence(conn, case_id, selectors),
        "gap_findings": gap_findings_for_case(conn, case_id, selectors),
    }
    case["reason_selected"] = _reason(best, len(features))
    return {"case": case, "candidates": ranked,
            "candidate_pool": len(features),
            "selection_method": "Deterministic scoring over impacted, completed cases. "
                                "Weights: " + ", ".join(f"{k} {v:+}" for k, v in WEIGHTS.items())}


def _reason(best: dict, pool: int) -> str:
    """A factual, non-LLM explanation of why this case was chosen."""
    c = best.get("score_components") or {}
    bits = []
    if best.get("matched_events"):
        bits.append(f"{best['matched_events']} events matching the root cause")
    if best.get("matched_waiting_minutes"):
        bits.append(f"{round(best['matched_waiting_minutes'] / BUSINESS_MIN_PER_DAY, 1)} business "
                    f"days of attributable waiting")
    if best.get("matched_rework_events"):
        bits.append(f"{best['matched_rework_events']} rework events on those activities")
    if (best.get("sla_breach_count") or 0) > 0:
        bits.append(f"{best['sla_breach_count']} SLA breaches")
    typical = c.get("atypicality")
    tail = (" It sits close to the median impacted case, so it illustrates the problem without "
            "overstating it." if typical is not None and typical < 0.35 else
            " Note that its elapsed time is some distance from the median impacted case.")
    return (f"Selected from {pool} impacted completed cases: " + ", ".join(bits) + "." + tail) \
        if bits else f"Selected from {pool} impacted completed cases."
