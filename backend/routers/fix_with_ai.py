"""
backend.routers.fix_with_ai - the Fix-with-AI HTTP surface.

    GET /api/root-causes/{finding_id}/fix-with-ai        the full four-part analysis
    GET /api/root-causes/{finding_id}/impact             (1) impacted cases only
    GET /api/root-causes/{finding_id}/impacted-cases     (1) the paged case list
    GET /api/root-causes/{finding_id}/kpis               (2) the KPIs those cases move
    GET /api/root-causes/{finding_id}/representative-case  (3) selection + candidates
    GET /api/root-causes/{finding_id}/what-happened      (4) journey, key events, narrative
    GET /api/root-causes/{finding_id}/agents             (5) the specialist agents to invoke
    GET /api/agents                                      the deterministic agent mapping itself
    GET /api/root-causes/{finding_id}/to-be-workflow     the To-Be state workflow + its metrics
    GET /api/to-be-workflow/context/{finding_id}         the designer's evidence pack (no model)
    GET /api/cases/{case_id}/journey                     any case's full event timeline

The sub-resources exist so the UI can render the screen progressively instead of blocking on the
whole payload; they share exactly the same services, so the numbers cannot diverge between them.

Errors are mapped to clean HTTP responses. Stack traces stay in the server log - the client only
ever sees a message and the id it failed on.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .. import db
from ..agents import recommendation_agent
from ..agents.fix_plan_orchestrator import InsufficientEvidence, RootCauseNotFound, run
from ..services import (event_log_service, fix_selectors, impact_service, kpi_context_service,
                        representative_case, root_cause_store)

log = logging.getLogger("kinetic.fix_with_ai")
router = APIRouter(prefix="/api", tags=["fix-with-ai"])


def _fail(exc: Exception, finding_id: str):
    """Translate a domain error into an HTTP response without leaking internals."""
    if isinstance(exc, RootCauseNotFound):
        raise HTTPException(status_code=404, detail={
            "error": "root_cause_not_found", "finding_id": finding_id, "message": str(exc)})
    if isinstance(exc, InsufficientEvidence):
        raise HTTPException(status_code=422, detail={
            "error": "insufficient_evidence", "finding_id": finding_id, "message": str(exc)})
    log.exception("Fix-with-AI failed for %s", finding_id)
    raise HTTPException(status_code=503, detail={
        "error": "analysis_failed", "finding_id": finding_id,
        "message": "The Fix-with-AI analysis could not be completed. Check the PG* configuration "
                   "and that the KPI run and the stored analysis are both present."})


def _resolve(conn, finding_id: str, analysis_id: Optional[int]) -> tuple:
    """The stored finding plus its deterministic selectors, or a clean 404 / 422."""
    finding = root_cause_store.get_finding(conn, finding_id.upper(), analysis_id)
    if not finding:
        _fail(RootCauseNotFound(
            f"No root cause {finding_id.upper()} in the stored analysis."), finding_id.upper())
    selectors = fix_selectors.extract(finding)
    if not fix_selectors.has_evidence(selectors):
        _fail(InsufficientEvidence(
            f"{finding_id.upper()} cites no activity and no gap, so it cannot be tied to cases "
            f"in the event log."), finding_id.upper())
    return finding, selectors


@router.get("/root-causes/{finding_id}/fix-with-ai")
def fix_with_ai(finding_id: str,
                analysis_id: Optional[int] = Query(None, description="pin a specific analysis"),
                candidates: int = Query(5, ge=1, le=20,
                                        description="representative-case candidates to return"),
                include_timeline: bool = Query(True,
                                               description="include the full event timeline")):
    """Impacted cases, the KPIs they move, the representative case and what happened in it."""
    try:
        return run(finding_id, analysis_id=analysis_id, candidates=candidates,
                   include_timeline=include_timeline)
    except (RootCauseNotFound, InsufficientEvidence) as exc:
        _fail(exc, finding_id.upper())
    except Exception as exc:
        _fail(exc, finding_id.upper())


@router.get("/root-causes/{finding_id}/impact")
def impact(finding_id: str, analysis_id: Optional[int] = Query(None)):
    """(1) How many cases this root cause touches, and what it costs them."""
    try:
        with db.connection() as conn:
            finding, selectors = _resolve(conn, finding_id, analysis_id)
            return {"finding_id": finding_id.upper(), "selectors": selectors,
                    "impact": impact_service.calculate(conn, selectors, finding.get("run_id"))}
    except HTTPException:
        raise
    except Exception as exc:
        _fail(exc, finding_id.upper())


@router.get("/root-causes/{finding_id}/impacted-cases")
def impacted_cases(finding_id: str, analysis_id: Optional[int] = Query(None),
                   limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    """(1) The impacted cases themselves, ordered by attributable waiting time."""
    try:
        with db.connection() as conn:
            _finding, selectors = _resolve(conn, finding_id, analysis_id)
            rows = impact_service.impacted_case_list(conn, selectors, limit=limit, offset=offset)
            return {"finding_id": finding_id.upper(), "case_type": selectors.get("case_type"),
                    "count": len(rows), "limit": limit, "offset": offset, "cases": rows}
    except HTTPException:
        raise
    except Exception as exc:
        _fail(exc, finding_id.upper())


@router.get("/root-causes/{finding_id}/kpis")
def kpis(finding_id: str, analysis_id: Optional[int] = Query(None)):
    """(2) The KPIs associated with the impacted cases, impacted vs unimpacted."""
    try:
        with db.connection() as conn:
            finding, selectors = _resolve(conn, finding_id, analysis_id)
            return {"finding_id": finding_id.upper(),
                    "kpis": kpi_context_service.build(conn, selectors, finding.get("run_id"))}
    except HTTPException:
        raise
    except Exception as exc:
        _fail(exc, finding_id.upper())


@router.get("/root-causes/{finding_id}/representative-case")
def rep_case(finding_id: str, analysis_id: Optional[int] = Query(None),
             candidates: int = Query(5, ge=1, le=20)):
    """(3) The most representative case, with the ranked candidates behind the choice."""
    try:
        with db.connection() as conn:
            _finding, selectors = _resolve(conn, finding_id, analysis_id)
            return {"finding_id": finding_id.upper(),
                    **representative_case.select(conn, selectors, top_n=candidates)}
    except HTTPException:
        raise
    except Exception as exc:
        _fail(exc, finding_id.upper())


@router.get("/root-causes/{finding_id}/what-happened")
def what_happened(finding_id: str, analysis_id: Optional[int] = Query(None),
                  case_id: Optional[str] = Query(None,
                                                 description="override the selected case"),
                  include_timeline: bool = Query(True)):
    """(4) What happened in the representative case - journey, key events and the facts."""
    try:
        with db.connection() as conn:
            _finding, selectors = _resolve(conn, finding_id, analysis_id)
            target = case_id
            if not target:
                selection = representative_case.select(conn, selectors, top_n=1)
                target = ((selection or {}).get("case") or {}).get("case_id")
            if not target:
                raise HTTPException(status_code=404, detail={
                    "error": "no_representative_case", "finding_id": finding_id.upper(),
                    "message": "No completed impacted case is available to illustrate this root "
                               "cause."})
            story = event_log_service.what_happened(conn, target, selectors)
            if not include_timeline:
                story.pop("timeline", None)
            return {"finding_id": finding_id.upper(), **story}
    except HTTPException:
        raise
    except Exception as exc:
        _fail(exc, finding_id.upper())


@router.get("/cases/{case_id}/journey")
def case_journey(case_id: str):
    """Any case's complete event timeline, unedited - the audit view behind every figure."""
    try:
        with db.connection() as conn:
            events = event_log_service.journey(conn, case_id)
            if not events:
                raise HTTPException(status_code=404, detail={
                    "error": "case_not_found", "case_id": case_id,
                    "message": "No events for this case id. Ids look like REQ-10001 or APP-100788."})
            return {"case_id": case_id, "events": len(events), "timeline": events}
    except HTTPException:
        raise
    except Exception as exc:
        _fail(exc, case_id)


@router.get("/root-causes/{finding_id}/agents")
def agents(finding_id: str, analysis_id: Optional[int] = Query(None)):
    """(5) The specialist agents that own this root cause's remediation.

    Deterministic: the Tier 1 x Tier 2 x gap mapping is version-controlled in
    backend/agents/recommendation_agent.py. No model selects an agent id.
    """
    try:
        with db.connection() as conn:
            finding, selectors = _resolve(conn, finding_id, analysis_id)
            ctx = kpi_context_service.build(conn, selectors, finding.get("run_id"))
            detections = {g["gap_reference"]: {"cases": (g.get("detected") or {}).get("cases") or 0}
                          for g in (ctx.get("gaps") or [])}
            return recommendation_agent.recommend_for_finding(
                finding,
                tier1_rows=[ctx["tier1"]] if ctx.get("tier1") else [],
                tier2_rows=ctx.get("tier2") or [],
                gap_rows=ctx.get("gaps") or [],
                detections=detections,
                stage_code=next(iter(selectors.get("stage_codes") or []), None))
    except HTTPException:
        raise
    except Exception as exc:
        _fail(exc, finding_id.upper())


@router.get("/agents")
def agent_catalogue(run_id: Optional[int] = Query(None, description="also route this KPI run"),
                    include_untriggered: bool = Query(False)):
    """The agent catalogue and the deterministic mapping; optionally routed against a KPI run."""
    payload = {
        "agents": [{"agent_id": aid, **a} for aid, a in recommendation_agent.AGENT_CATALOGUE.items()],
        "tier1_agent_map": recommendation_agent.TIER1_AGENT_MAP,
        "tier1_supporting_map": recommendation_agent.TIER1_SUPPORTING_MAP,
        "tier2_agent_map": recommendation_agent.TIER2_AGENT_MAP,
        "tier1_tier2_map": recommendation_agent.TIER1_TIER2_MAP,
        "gap_agent_map": recommendation_agent.GAP_AGENT_MAP,
        "gap_tier2_map": recommendation_agent.GAP_TIER2_MAP,
        "stage_agent_map": recommendation_agent.STAGE_AGENT_MAP,
        "routing_priority": ["gap", "tier2", "tier1"],
        "mapping_problems": recommendation_agent.validate_maps(),
    }
    if run_id is None:
        return payload
    try:
        payload["routing"] = recommendation_agent.recommend_from_db(
            run_id=run_id, include_untriggered=include_untriggered)
    except Exception as exc:
        log.warning("Could not route KPI run %s: %s", run_id, exc)
        payload["routing_error"] = str(exc)
    return payload


@router.get("/root-causes/{finding_id}/to-be-workflow")
def to_be_workflow(finding_id: str, analysis_id: Optional[int] = Query(None),
                   case_id: Optional[str] = Query(None,
                                                  description="override the representative case"),
                   include_current_state: bool = Query(True)):
    """The To-Be state workflow for one root cause.

    Designed from four inputs only: the already-recommended agents, the representative case's real
    activity list, the Tier 1 / Tier 2 KPIs and the root cause. Every headline count is computed
    in Python from the design, not produced by the model.
    """
    from ..agents import to_be_workflow_orchestrator
    from ..agents.to_be_workflow_agent import ToBeError
    try:
        return to_be_workflow_orchestrator.run(finding_id, analysis_id=analysis_id,
                                               case_id=case_id,
                                               include_current_state=include_current_state)
    except to_be_workflow_orchestrator.ToBeContextError as exc:
        raise HTTPException(status_code=422, detail={
            "error": "to_be_context_unavailable", "finding_id": finding_id.upper(),
            "message": str(exc)})
    except ToBeError as exc:
        log.exception("To-Be design failed for %s", finding_id)
        raise HTTPException(status_code=502, detail={
            "error": "to_be_design_failed", "finding_id": finding_id.upper(),
            "message": "The To-Be workflow could not be designed or validated."})
    except HTTPException:
        raise
    except Exception as exc:
        _fail(exc, finding_id.upper())


@router.get("/to-be-workflow/context/{finding_id}")
def to_be_context(finding_id: str, analysis_id: Optional[int] = Query(None),
                  case_id: Optional[str] = Query(None)):
    """The exact evidence pack the designer receives - the audit view, no model call."""
    from ..agents import to_be_workflow_orchestrator
    try:
        with db.connection() as conn:
            ctx = to_be_workflow_orchestrator.build_context(conn, finding_id, analysis_id, case_id)
        ctx.pop("selectors", None)
        return ctx
    except to_be_workflow_orchestrator.ToBeContextError as exc:
        raise HTTPException(status_code=422, detail={
            "error": "to_be_context_unavailable", "finding_id": finding_id.upper(),
            "message": str(exc)})
    except HTTPException:
        raise
    except Exception as exc:
        _fail(exc, finding_id.upper())
