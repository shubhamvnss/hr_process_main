"""
backend.agents.to_be_workflow_orchestrator - assemble, design, and count.

    finding_id
      ├─ recommendation_agent.recommend_for_finding()   the ONLY agents this design may use
      ├─ representative_case.select()                   which case to redesign
      ├─ event_log_service.journey()                    that case's real activity list
      ├─ kpi_context_service.build()                    Tier 1 KPI, Tier 2 KPIs, documented gaps
      ├─ root_cause_store.get_finding()                 the root cause itself
      │
      ├─ to_be_workflow_agent.generate()                [model] the disposition of each activity
      └─ compute_metrics()                              [pure python] every number reported

The split is the same one used everywhere else in this pipeline: the model decides the
DISPOSITION of each real activity, and Python counts. No headline number on this screen is a
figure a model produced, which is what makes "12 activities automated" auditable rather than
asserted.

compute_metrics() is a pure function over the design rows, so it is unit-testable with no model
and no database.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

from ..to_be_schemas import AUTOMATED_TYPES, REDUCING_TYPES

# Precedence when several current-state rows merge into one future step: the strongest
# disposition in the group describes the resulting step.
_DISPOSITION_ORDER = ["AI Agent", "Automated (Deterministic)", "Human", "Combined", "Retained",
                      "Eliminated"]


class ToBeContextError(RuntimeError):
    """The To-Be workflow could not be assembled from the available evidence."""


def _now() -> str:
    return _dt.datetime.now().isoformat(sep=" ", timespec="seconds")


# ============================================================================
# Context assembly - the four inputs, and nothing else
# ============================================================================

def build_context(conn, finding_id: str, analysis_id: Optional[int] = None,
                  case_id: Optional[str] = None) -> dict:
    """Gather exactly the four inputs the designer is allowed to see."""
    from ..services import (event_log_service, fix_selectors, kpi_context_service,
                            representative_case, root_cause_store)
    from . import recommendation_agent

    finding = root_cause_store.get_finding(conn, finding_id.upper(), analysis_id)
    if not finding:
        raise ToBeContextError(f"No root cause {finding_id.upper()} in the stored analysis.")

    selectors = fix_selectors.extract(finding)
    if not fix_selectors.has_evidence(selectors):
        raise ToBeContextError(
            f"{finding_id.upper()} cites no activity and no gap, so no case can be identified to "
            f"redesign.")

    run_id = finding.get("run_id")
    stage_code = next(iter(selectors.get("stage_codes") or []), None)

    # --- input 3: Tier 1 KPI, Tier 2 KPIs, documented gaps, root cause
    kpis = kpi_context_service.build(conn, selectors, run_id)

    # --- input 1: the recommended agents, and ONLY those
    detections = {g["gap_reference"]: {"cases": (g.get("detected") or {}).get("cases") or 0}
                  for g in (kpis.get("gaps") or [])}
    routing = recommendation_agent.recommend_for_finding(
        finding,
        tier1_rows=[kpis["tier1"]] if kpis.get("tier1") else [],
        tier2_rows=kpis.get("tier2") or [],
        gap_rows=kpis.get("gaps") or [],
        detections=detections,
        stage_code=stage_code)

    recommended_agents = _agent_list(routing)
    if not recommended_agents:
        raise ToBeContextError(
            f"No agent was routed for {finding_id.upper()}, so there is nothing to design a "
            f"future state around. Check backend/agents/recommendation_agent.py routing.")

    # --- input 2: the representative case's real activity list
    if case_id:
        chosen = {"case_id": case_id}
    else:
        selection = representative_case.select(conn, selectors, top_n=1)
        chosen = (selection or {}).get("case") or {}
        if not chosen.get("case_id"):
            raise ToBeContextError(
                "No completed impacted case is available to redesign. Every matching case is "
                "still open at the reporting cut-off.")

    timeline = event_log_service.journey(conn, chosen["case_id"], selectors)
    if not timeline:
        raise ToBeContextError(f"No events found for case {chosen['case_id']}.")

    current_state = [_current_row(e) for e in timeline]
    case_systems = sorted({e["source_system"] for e in timeline if e.get("source_system")})

    return {
        "finding_id": finding_id.upper(),
        "run_id": run_id,
        "case_id": chosen["case_id"],
        "case_type": chosen.get("case_type") or selectors.get("case_type"),
        "case": chosen,
        "current_state": current_state,
        "case_systems": case_systems,
        "tier1_kpi": kpis.get("tier1") or {},
        "tier2_kpis": kpis.get("tier2") or [],
        "gaps": kpis.get("gaps") or [],
        "root_cause": {
            "finding_id": finding.get("finding_id"),
            "root_cause": finding.get("root_cause"),
            "root_cause_type": finding.get("root_cause_type"),
            "confidence": finding.get("confidence"),
            "causal_explanation": finding.get("causal_explanation"),
            "evidence_limitations": finding.get("evidence_limitations"),
            "gap_references": selectors.get("gap_references"),
        },
        "recommended_agents": recommended_agents,
        "allowed_agent_names": [a["name"] for a in recommended_agents],
        "routing": routing,
        "selectors": selectors,
    }


def _agent_list(routing: dict) -> List[dict]:
    """Primary first, then supporting. Exactly the agents the routing layer approved."""
    out, seen = [], set()
    primary = routing.get("primary_agent")
    if primary:
        out.append({**primary, "role": "primary",
                    "intervention_focus": routing.get("intervention_focus")})
        seen.add(primary["agent_id"])
    for a in routing.get("supporting_agents") or []:
        if a and a["agent_id"] not in seen:
            out.append({**a, "role": "supporting"})
            seen.add(a["agent_id"])
    return out


def _current_row(e: dict) -> dict:
    """One current-state row, trimmed to what the designer needs to reason about."""
    return {
        "sequence": e["sequence"],
        "activity": e["activity"],
        "stage": e.get("stage"),
        "actor_persona": e.get("actor_persona"),
        "source_system": e.get("source_system"),
        "timestamp": e.get("timestamp"),
        "duration_minutes": e.get("duration_minutes"),
        "waiting_days": e.get("waiting_days"),
        "waiting_reason_code": e.get("waiting_reason_code"),
        "sla_code": e.get("sla_code"),
        "sla_breached": e.get("sla_breached"),
        "is_rework": e.get("is_rework"),
        "is_manual": e.get("is_manual"),
        "is_handoff": e.get("is_handoff"),
        "is_exception": e.get("is_exception"),
        "is_follow_up": e.get("is_follow_up"),
        "matched_root_cause": e.get("matched_root_cause"),
    }


# ============================================================================
# Metrics - pure python, every number on the screen
# ============================================================================

def _group_disposition(rows: List[dict]) -> str:
    """The resulting step's disposition when several current rows merge into one."""
    types = {r["change_type"] for r in rows}
    if len(rows) > 1 and types == {"Combined"}:
        # a pure merge: classify by what actually performs the merged step
        if any(r.get("responsible_agent") for r in rows):
            return "AI Agent"
        if any(r.get("deterministic_automation") for r in rows):
            return "Automated (Deterministic)"
        if any(r.get("human_in_the_loop") for r in rows):
            return "Human"
        return "Combined"
    for disposition in _DISPOSITION_ORDER:
        if disposition in types:
            return disposition
    return "Retained"


def compute_metrics(to_be_rows: List[dict], current_state: List[dict]) -> dict:
    """Every number the To-Be screen reports. Pure function - no model, no database.

    `to_be_rows` are the designer's activity objects as plain dicts. Rows sharing a
    `to_be_sequence` are one future-state activity; "Eliminated" rows produce no future step.
    """
    current_by_seq = {r["sequence"]: r for r in current_state}
    eliminated_rows = [r for r in to_be_rows if r["change_type"] == "Eliminated"]
    live_rows = [r for r in to_be_rows if r["change_type"] != "Eliminated"]

    groups: Dict[int, List[dict]] = {}
    order: List[int] = []
    for r in sorted(live_rows, key=lambda x: (x["to_be_sequence"], x["current_sequence"])):
        seq = r["to_be_sequence"]
        if seq not in groups:
            groups[seq] = []
            order.append(seq)
        groups[seq].append(r)

    to_be_activities = []
    for position, seq in enumerate(order, start=1):
        rows = groups[seq]
        head = rows[0]
        disposition = _group_disposition(rows)
        sources = [{"current_sequence": r["current_sequence"],
                    "current_activity": r["current_activity"]} for r in rows]
        to_be_activities.append({
            "to_be_sequence": position,
            "design_sequence": seq,
            "to_be_activity": head["to_be_activity"],
            "change_type": disposition,
            "responsible_agent": next((r.get("responsible_agent") for r in rows
                                       if r.get("responsible_agent")), None),
            "deterministic_automation": any(r.get("deterministic_automation") for r in rows),
            "human_in_the_loop": any(r.get("human_in_the_loop") for r in rows),
            "actor_persona": head.get("actor_persona"),
            "system_used": sorted({s for r in rows for s in (r.get("system_used") or [])}),
            "trigger": head.get("trigger"),
            "action": head.get("action"),
            "expected_outcome": head.get("expected_outcome"),
            "reason_for_change": head.get("reason_for_change"),
            "kpi_gap_addressed": head.get("kpi_gap_addressed"),
            "replaces_current_activities": sources,
            "is_consolidation": len(rows) > 1,
        })

    def of(kind):
        return [a for a in to_be_activities if a["change_type"] == kind]

    ai_agent = of("AI Agent")
    deterministic = of("Automated (Deterministic)")
    human = of("Human")
    as_is = of("Retained")
    combined_other = of("Combined")
    automated = ai_agent + deterministic

    current_n, to_be_n = len(current_state), len(to_be_activities)
    consolidated = [a for a in to_be_activities if a["is_consolidation"]]
    rows_consolidated_away = sum(len(a["replaces_current_activities"]) - 1 for a in consolidated)

    eliminated = [{
        "current_sequence": r["current_sequence"],
        "current_activity": r["current_activity"],
        "reason_for_change": r.get("reason_for_change"),
        "was_follow_up": bool((current_by_seq.get(r["current_sequence"]) or {}).get("is_follow_up")),
    } for r in eliminated_rows]

    reconciles = (len(ai_agent) + len(deterministic) + len(human) + len(as_is)
                  + len(combined_other)) == to_be_n

    return {
        # ---- the to-be state list of activities
        "to_be_activities": to_be_activities,

        # ---- the headline numbers
        "metrics": {
            "current_activity_count": current_n,
            "to_be_activity_count": to_be_n,
            "activities_reduced": current_n - to_be_n,
            "activities_reduced_pct": (round(100.0 * (current_n - to_be_n) / current_n, 1)
                                       if current_n else None),
            "activities_automated": len(automated),
            "ai_agent_activities": len(ai_agent),
            "deterministic_automation_activities": len(deterministic),
            "human_activities": len(human),
            "activities_continuing_as_is": len(as_is),
            "activities_eliminated": len(eliminated),
            "activities_consolidated": len(consolidated),
            "current_rows_consolidated_away": rows_consolidated_away,
            "human_in_the_loop_activities": sum(1 for a in to_be_activities
                                                if a["human_in_the_loop"]),
            "automation_rate_pct": (round(100.0 * len(automated) / to_be_n, 1)
                                    if to_be_n else None),
        },

        # ---- the same thing as named lists, for the UI
        "breakdown": {
            "automated": automated,
            "ai_agent_activities": ai_agent,
            "deterministic_automation_activities": deterministic,
            "human_activities": human,
            "continuing_as_is": as_is,
            "eliminated": eliminated,
            "consolidated": consolidated,
        },

        # ---- before / after, for the comparison strip
        "comparison": _comparison(current_state, to_be_activities, ai_agent, deterministic),

        "counts_reconcile": reconciles,
        "counting_rules": {
            "to_be_activity_count": "future-state steps; rows sharing a to_be_sequence are one "
                                    "step, eliminated rows are none",
            "activities_reduced": "current activity count minus to-be activity count - the net "
                                  "effect of eliminations and consolidations",
            "activities_automated": "AI Agent plus Automated (Deterministic) steps",
            "human_activities": "steps redesigned to stay with a person (change type Human)",
            "activities_continuing_as_is": "steps unchanged from today (change type Retained)",
            "note": "human_in_the_loop is a flag on top of any disposition, so an AI Agent step "
                    "under human control is counted as automated AND as a human touchpoint - "
                    "these two counts deliberately overlap.",
        },
    }


def _comparison(current_state: List[dict], to_be: List[dict],
                ai_agent: List[dict], deterministic: List[dict]) -> dict:
    cur_manual = sum(1 for r in current_state if r.get("is_manual"))
    cur_rework = sum(1 for r in current_state if r.get("is_rework"))
    cur_follow = sum(1 for r in current_state if r.get("is_follow_up"))
    cur_wait = sum(1 for r in current_state if (r.get("waiting_days") or 0) > 0)
    cur_breach = sum(1 for r in current_state if r.get("sla_breached"))
    cur_systems = sorted({r["source_system"] for r in current_state if r.get("source_system")})

    personas = [a.get("actor_persona") for a in to_be]
    to_be_handoffs = sum(1 for a, b in zip(personas[:-1], personas[1:]) if a and b and a != b)
    cur_handoffs = sum(1 for r in current_state if r.get("is_handoff"))

    kept_seqs = {s["current_sequence"] for a in to_be for s in a["replaces_current_activities"]
                 if a["change_type"] == "Retained"}
    return {
        "number_of_activities": {"current": len(current_state), "to_be": len(to_be)},
        "manual_activities": {"current": cur_manual,
                              "to_be": sum(1 for a in to_be
                                           if a["change_type"] in ("Human", "Retained"))},
        "ai_agent_activities": {"current": 0, "to_be": len(ai_agent)},
        "deterministic_automation_activities": {"current": 0, "to_be": len(deterministic)},
        "follow_up_activities": {
            "current": cur_follow,
            "to_be": sum(1 for a in to_be
                         for s in a["replaces_current_activities"]
                         if s["current_sequence"] in kept_seqs
                         and (next((r for r in current_state
                                    if r["sequence"] == s["current_sequence"]), {}) or {})
                         .get("is_follow_up"))},
        "rework_activities": {
            "current": cur_rework,
            "to_be": sum(1 for seq in kept_seqs
                         if (next((r for r in current_state if r["sequence"] == seq), {}) or {})
                         .get("is_rework"))},
        "waiting_activities": {"current": cur_wait, "to_be": None},
        "sla_breaching_activities": {"current": cur_breach, "to_be": None},
        "handoffs": {"current": cur_handoffs, "to_be": to_be_handoffs},
        "systems_touched": {"current": cur_systems,
                            "to_be": sorted({s for a in to_be for s in a["system_used"]})},
        "note": "Current-state figures are measured from this case's event log. To-be figures are "
                "counted from the design. Waiting and SLA outcomes are left null because they can "
                "only be measured after the change, not designed.",
    }


# ============================================================================
# Entry point
# ============================================================================

def run(finding_id: str, analysis_id: Optional[int] = None, case_id: Optional[str] = None,
        conn=None, client=None, include_current_state: bool = True) -> dict:
    """Assemble the context, design the future state, and count it."""
    from .. import db
    if conn is None:
        with db.connection() as c:
            return run(finding_id, analysis_id, case_id, c, client, include_current_state)

    context = build_context(conn, finding_id, analysis_id, case_id)
    return design(context, client=client, include_current_state=include_current_state)


def design(context: dict, client=None, config=None,
           include_current_state: bool = True) -> dict:
    """Call the designer on an assembled context and compute the metrics.

    Split out from run() so the design step can be exercised with a stub client, and so a caller
    that already has the context (the Fix-with-AI payload, say) does not rebuild it.
    """
    from . import to_be_workflow_agent

    plan, corrections = to_be_workflow_agent.generate(context, client=client, config=config)
    rows = [a.model_dump() for a in plan.activities]
    computed = compute_metrics(rows, context["current_state"])

    out = {
        "generated_at": _now(),
        "finding_id": context.get("finding_id"),
        "run_id": context.get("run_id"),
        "case_id": context.get("case_id"),
        "case_type": context.get("case_type"),
        "workflow_summary": plan.workflow_summary,
        "design_limitations": plan.design_limitations,
        "recommended_agents": context.get("recommended_agents"),
        "root_cause": context.get("root_cause"),
        "tier1_kpi": context.get("tier1_kpi"),
        "tier2_kpis": context.get("tier2_kpis"),
        "gaps": [{"gap_reference": g.get("gap_reference"),
                  "gap_definition": g.get("gap_definition")} for g in context.get("gaps") or []],
        "design_rows": rows,
        "validator_corrections": corrections,
        **computed,
    }
    if include_current_state:
        out["current_state"] = context["current_state"]
    return out
