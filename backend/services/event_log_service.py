"""
backend.services.event_log_service - (4) WHAT HAPPENED IN THE REPRESENTATIVE CASE.

The factual journey of one case, straight from the event log. Nothing here is generated, inferred
or summarised by a model: every line is a row that exists, in the order it occurred.

Three views, for three different jobs:

    journey()        every event of the case, in execution order, with the waits between them
                     and a flag for the ones matching the root cause. The complete baseline.
    key_events()     the thinned, executive-readable version - the matching events plus the
                     case's first and last activity - so the UI tells a story, not a log dump.
    what_happened()  the deterministic narrative: the longest waits, the rework loops, the SLA
                     breaches, the handoffs and how the case ended, each as a fact with its
                     numbers attached.

On waiting time: event_log.waiting_time_minutes is the business minutes since the previous event
of the same case, so a wait is attributed to the event that ENDED it, not the one that started it.
"What was the case waiting for" is therefore the waiting_reason_code on the closing event, and
that is how it is reported here. Where the source recorded no reason, it stays unknown rather
than being guessed (BRD section 6.2).
"""
from __future__ import annotations

from typing import List, Optional

from .. import db
from . import fix_selectors as sel
from .impact_service import BUSINESS_MIN_PER_DAY

MAX_KEY_EVENTS = 14
LONG_WAIT_DAYS = 3.0            # a wait worth narrating, in business days

# Activities that do not move a case forward - the same list the KPI engine excludes from
# inactivity measurement, so "longest wait" here means the same thing it means on the dashboard.
FOLLOW_UP_ACTIVITIES = (
    "Workforce Budget Validation Reminder Sent", "Requisition Approval Reminder Sent",
    "Recruiter Assignment Follow-up", "Screening Follow-up Logged",
    "Shortlist Review Reminder Sent", "Assessment Reminder Sent",
    "Interview Scheduling Follow-up", "Interview Feedback Reminder Sent",
    "Selection Decision Reminder Sent", "Offer Approval Reminder Sent",
    "Offer Follow-up Logged", "Integration Sync Retried",
)


def journey(conn, case_id: str, selectors: Optional[dict] = None) -> List[dict]:
    """Every event_log row for this case, in execution order, unedited."""
    s = db.schema()
    activities = list((selectors or {}).get("activity_names") or [])
    rows = db.rows(conn, f"""
        SELECT e.event_id, e.activity_name, e.activity_stage, e.stage_code, e.event_timestamp,
               e.event_end_timestamp, e.actor_persona, e.actor_id_pseudonymous, e.source_system,
               e.status_before, e.status_after, e.outcome_code, e.reason_code,
               e.duration_minutes, e.waiting_time_minutes, e.waiting_reason_code,
               e.sla_code, e.sla_target_minutes, e.sla_measured_minutes, e.sla_breach_flag,
               e.sla_overrun_minutes, e.rework_flag, e.repeat_sequence_number,
               e.manual_touch_flag, e.handoff_flag, e.exception_flag,
               (e.activity_name = ANY(%(activities)s)) AS matched_root_cause,
               ROW_NUMBER() OVER (ORDER BY e.event_timestamp, e.event_id) AS sequence,
               COUNT(*)     OVER ()                                       AS total_events
        FROM {s}.event_log e
        WHERE e.process_instance_id = %(case_id)s
        ORDER BY e.event_timestamp, e.event_id;""",
        {"case_id": case_id, "activities": activities})

    out = []
    for r in rows:
        waiting = r.get("waiting_time_minutes")
        out.append({
            "sequence": r["sequence"],
            "event_id": r.get("event_id"),
            "activity": r.get("activity_name") or "",
            "stage": r.get("activity_stage"),
            "stage_code": r.get("stage_code"),
            "timestamp": r.get("event_timestamp"),
            "actor_persona": r.get("actor_persona"),
            "actor_id": r.get("actor_id_pseudonymous"),
            "source_system": r.get("source_system"),
            "status_before": r.get("status_before"),
            "status_after": r.get("status_after"),
            "outcome_code": r.get("outcome_code"),
            "reason_code": r.get("reason_code"),
            "duration_minutes": r.get("duration_minutes"),
            "waiting_minutes": waiting,
            "waiting_days": round(waiting / BUSINESS_MIN_PER_DAY, 2) if waiting else None,
            "waiting_reason_code": r.get("waiting_reason_code"),
            "sla_code": r.get("sla_code"),
            "sla_breached": r.get("sla_breach_flag") == "Y",
            "sla_overrun_days": (round(r["sla_overrun_minutes"] / BUSINESS_MIN_PER_DAY, 2)
                                 if r.get("sla_overrun_minutes") else None),
            "is_rework": r.get("rework_flag") == "Y",
            "repeat_number": r.get("repeat_sequence_number"),
            "is_manual": r.get("manual_touch_flag") == "Y",
            "is_handoff": r.get("handoff_flag") == "Y",
            "is_exception": r.get("exception_flag") == "Y",
            "is_follow_up": (r.get("activity_name") in FOLLOW_UP_ACTIVITIES),
            "matched_root_cause": bool(r.get("matched_root_cause")),
        })
    return out


def key_events(conn, case_id: str, selectors: dict, timeline: Optional[List[dict]] = None):
    """The thinned timeline: matching events, notable events, and the two ends of the case."""
    events = timeline if timeline is not None else journey(conn, case_id, selectors)
    if not events:
        return []
    last_seq = events[-1]["sequence"]

    def notable(e):
        return (e["matched_root_cause"] or e["is_rework"] or e["is_exception"]
                or e["sla_breached"] or (e["waiting_days"] or 0) >= LONG_WAIT_DAYS
                or e["sequence"] in (1, last_seq))

    keep = [e for e in events if notable(e)]
    if len(keep) > MAX_KEY_EVENTS:              # keep both ends, thin the middle
        keep = keep[: MAX_KEY_EVENTS - 1] + [keep[-1]]
    return keep


def _fmt_days(value) -> str:
    return f"{value:g} business day{'s' if value and value != 1 else ''}"


def what_happened(conn, case_id: str, selectors: dict,
                  timeline: Optional[List[dict]] = None) -> dict:
    """A deterministic account of the case: the facts, in the order they matter.

    Every statement is produced from event rows, so nothing here can be a model's invention.
    """
    events = timeline if timeline is not None else journey(conn, case_id, selectors)
    if not events:
        return {"case_id": case_id, "narrative": [], "summary": {}, "timeline": []}

    progression = [e for e in events if not e["is_follow_up"]]
    waits = sorted((e for e in progression if e["waiting_days"]),
                   key=lambda e: e["waiting_days"], reverse=True)
    rework = [e for e in events if e["is_rework"]]
    breaches = [e for e in events if e["sla_breached"]]
    exceptions = [e for e in events if e["is_exception"]]
    follow_ups = [e for e in events if e["is_follow_up"]]
    matched = [e for e in events if e["matched_root_cause"]]
    first, last = events[0], events[-1]

    total_wait = sum(e["waiting_minutes"] or 0 for e in events)
    matched_wait = sum(e["waiting_minutes"] or 0 for e in matched)

    narrative = []

    def say(kind, text, **facts):
        narrative.append({"type": kind, "text": text, **facts})

    say("start", f"The case opened with {first['activity']} on {first['timestamp']}, "
                 f"owned by {first['actor_persona']} in {first['source_system']}.",
        sequence=first["sequence"], activity=first["activity"])

    for e in waits[:3]:
        reason = e["waiting_reason_code"] or "no reason recorded in the source"
        say("wait",
            f"The longest waits include {_fmt_days(e['waiting_days'])} before "
            f"{e['activity']} ({e['stage']}), waiting reason: {reason}.",
            sequence=e["sequence"], activity=e["activity"], days=e["waiting_days"],
            waiting_reason_code=e["waiting_reason_code"])

    if rework:
        by_activity = {}
        for e in rework:
            by_activity.setdefault(e["activity"], []).append(e)
        for activity, group in sorted(by_activity.items(), key=lambda kv: -len(kv[1]))[:3]:
            reasons = sorted({e["reason_code"] for e in group if e["reason_code"]})
            say("rework",
                f"{activity} occurred {len(group)} time(s) as rework"
                + (f", reason: {', '.join(reasons)}." if reasons else "."),
                activity=activity, occurrences=len(group), reasons=reasons)

    for e in breaches[:3]:
        say("sla",
            f"{e['activity']} breached {e['sla_code']} by "
            f"{_fmt_days(e['sla_overrun_days'])}." if e["sla_overrun_days"] else
            f"{e['activity']} breached {e['sla_code']}.",
            sequence=e["sequence"], activity=e["activity"], sla_code=e["sla_code"],
            overrun_days=e["sla_overrun_days"])

    for e in exceptions[:2]:
        say("exception",
            f"An exception was recorded at {e['activity']}"
            + (f": {e['reason_code']}." if e["reason_code"] else "."),
            sequence=e["sequence"], activity=e["activity"], reason_code=e["reason_code"])

    if follow_ups:
        say("coordination",
            f"{len(follow_ups)} follow-up or reminder event(s) were logged - coordination effort "
            f"that did not move the case forward.",
            count=len(follow_ups))

    say("end",
        f"The case ended with {last['activity']} on {last['timestamp']}"
        + (f", outcome: {last['outcome_code']}" if last.get("outcome_code") else "")
        + (f" ({last['reason_code']})." if last.get("reason_code") else "."),
        sequence=last["sequence"], activity=last["activity"],
        outcome_code=last.get("outcome_code"), reason_code=last.get("reason_code"))

    summary = {
        "events": len(events),
        "progression_events": len(progression),
        "follow_up_events": len(follow_ups),
        "matched_events": len(matched),
        "rework_events": len(rework),
        "exception_events": len(exceptions),
        "sla_breaches": len(breaches),
        "handoffs": sum(1 for e in events if e["is_handoff"]),
        "manual_touches": sum(1 for e in events if e["is_manual"]),
        "total_waiting_days": round(total_wait / BUSINESS_MIN_PER_DAY, 2),
        "waiting_days_attributable_to_root_cause": round(matched_wait / BUSINESS_MIN_PER_DAY, 2),
        "waiting_share_attributable_pct": (round(100.0 * matched_wait / total_wait, 1)
                                           if total_wait else None),
        "first_event_timestamp": first["timestamp"],
        "last_event_timestamp": last["timestamp"],
        "final_status": last.get("status_after"),
        "final_outcome_code": last.get("outcome_code"),
        "distinct_systems": len({e["source_system"] for e in events if e["source_system"]}),
        "distinct_personas": len({e["actor_persona"] for e in events if e["actor_persona"]}),
        "waiting_reasons_recorded": sorted({e["waiting_reason_code"] for e in events
                                            if e["waiting_reason_code"]}),
    }
    return {
        "case_id": case_id,
        "summary": summary,
        "narrative": narrative,
        "key_events": key_events(conn, case_id, selectors, timeline=events),
        "timeline": events,
    }
