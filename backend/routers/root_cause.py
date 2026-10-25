"""
backend.routers.root_cause - the HTTP surface for the Root Cause Agent's output.

    GET /api/root-cause/health                          service + data readiness
    GET /api/root-cause/latest                          the full stored analysis (latest run)
    GET /api/root-cause/summary                         frontend_summary only - the dashboard card
    GET /api/root-cause/kpi-health                      Tier 1 KPI health + run header (tiles)
    GET /api/root-cause/kpis                            one row per Critical Tier 1 KPI analysed
    GET /api/root-cause/kpis/{kpi_id}                   the ranked root causes of one Tier 1 KPI
    GET /api/root-cause/findings                        flat, filterable root-cause list
    GET /api/root-cause/findings/{finding_id}           one root cause with its full evidence
    GET /api/root-cause/gaps                            which GAP ids the analysis cites, and how often
    GET /api/root-cause/analyses                         the analysis history
    GET /api/root-cause/analyses/{analysis_id}          one stored analysis
    GET /api/root-cause/analyses/{analysis_id}/evidence  the evidence package it was grounded in

This surface is READ-ONLY. Producing an analysis is root_cause_agent.py's job - it is a long,
billable, high-effort model run and is deliberately not exposed as a request-time endpoint.

Every endpoint accepts ?run_id= or ?analysis_id= to address a specific KPI run / analysis;
without them the latest stored analysis is served. If the database holds no analysis, the
configured fallback JSON file (the agent's own output) is served instead, and the response says
so via source = "file".
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .. import config, db
from ..services import root_cause_store as store

log = logging.getLogger("kinetic.root_cause")
router = APIRouter(prefix="/api/root-cause", tags=["root-cause"])


# ------------------------------------------------------------------ helpers
def _file_analysis() -> Optional[dict]:
    path = config.FALLBACK_FILE
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as exc:
        log.warning("Fallback analysis file %s could not be read: %s", path, exc)
        return None


def _unavailable(exc: Exception):
    log.error("Root-cause database unavailable: %s", exc)
    raise HTTPException(status_code=503, detail={
        "error": "database_unavailable",
        "message": "The root-cause store could not be reached. Check the PG* configuration.",
    })


def _no_analysis():
    raise HTTPException(status_code=404, detail={
        "error": "no_analysis",
        "message": "No root-cause analysis has been stored yet. Run root_cause_agent.py after "
                   "04_compute_kpis_to_postgres.py.",
    })


def _resolve(conn, analysis_id, run_id):
    aid = analysis_id or db.latest_analysis_id(conn, run_id)
    if aid is None:
        return None
    return aid


def _with_source(payload: dict, source: str) -> dict:
    payload = dict(payload)
    payload["source"] = source
    return payload


# ------------------------------------------------------------------ endpoints
@router.get("/health")
def health():
    status = {"status": "ok", "schema": None, "kpi_run": None, "analysis_id": None,
              "fallback_file": config.FALLBACK_FILE,
              "fallback_file_present": bool(_file_analysis())}
    try:
        with db.connection() as conn:
            db.ensure_root_cause_schema(conn)
            status["schema"] = db.schema()
            status["kpi_run"] = db.latest_run_id(conn)
            status["analysis_id"] = db.latest_analysis_id(conn)
            conn.commit()
        if status["kpi_run"] is None:
            status.update(status="degraded",
                          message="No KPI run found - run 04_compute_kpis_to_postgres.py first.")
        elif status["analysis_id"] is None:
            status.update(status="degraded",
                          message="No stored analysis - run root_cause_agent.py.")
    except Exception as exc:
        status.update(status="degraded", database=f"unavailable: {type(exc).__name__}")
    return status


@router.get("/latest")
def latest(run_id: Optional[int] = Query(None, description="KPI run to read (default: latest)"),
           analysis_id: Optional[int] = Query(None, description="specific analysis to read")):
    """The complete stored analysis: metadata + frontend_summary + tier1_root_causes."""
    try:
        with db.connection() as conn:
            row = store.get_analysis(conn, analysis_id, run_id)
            if row:
                analysis = row.pop("analysis_json", None) or {}
                return _with_source({**row, **analysis}, "database")
    except Exception as exc:
        fallback = _file_analysis()
        if fallback is None:
            _unavailable(exc)
        log.warning("Serving the fallback analysis file: %s", exc)
        return _with_source(fallback, "file")

    fallback = _file_analysis()
    if fallback is None:
        _no_analysis()
    return _with_source(fallback, "file")


@router.get("/summary")
def summary(run_id: Optional[int] = Query(None), analysis_id: Optional[int] = Query(None)):
    """frontend_summary only - what a dashboard card needs, without the detailed evidence."""
    payload = latest(run_id=run_id, analysis_id=analysis_id)
    fs = payload.get("frontend_summary") or {}
    return {
        "source": payload.get("source"),
        "analysis_id": payload.get("analysis_id"),
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "model": payload.get("model"),
        "validation_status": payload.get("validation_status"),
        "analysis_title": fs.get("analysis_title"),
        "analysis_summary": fs.get("analysis_summary"),
    }


@router.get("/kpi-health")
def kpi_health(run_id: Optional[int] = Query(None, description="KPI run to read (default: latest)")):
    """Tier 1 KPI health + the run header for the dashboard tiles.

    Reads kpi_tier1_health / kpi_summary directly, so the tiles are correct even before any
    root-cause analysis exists - i.e. straight after 04_compute_kpis_to_postgres.py.
    """
    try:
        with db.connection() as conn:
            payload = store.kpi_health(conn, run_id)
            if not payload:
                raise HTTPException(status_code=404, detail={
                    "error": "no_kpi_run",
                    "message": "No KPI run found. Run 04_compute_kpis_to_postgres.py first."})
            return {"source": "database", **payload}
    except HTTPException:
        raise
    except Exception as exc:
        _unavailable(exc)


@router.get("/kpis")
def kpis(run_id: Optional[int] = Query(None), analysis_id: Optional[int] = Query(None)):
    """One row per Critical Tier 1 KPI in the analysis, with its root-cause counts."""
    try:
        with db.connection() as conn:
            aid = _resolve(conn, analysis_id, run_id)
            if aid is None:
                _no_analysis()
            return {"source": "database", "analysis_id": aid,
                    "kpis": store.kpi_rollup(conn, aid)}
    except HTTPException:
        raise
    except Exception as exc:
        _unavailable(exc)


@router.get("/kpis/{kpi_id}")
def kpi_findings(kpi_id: str, run_id: Optional[int] = Query(None),
                 analysis_id: Optional[int] = Query(None),
                 detail: bool = Query(True, description="include the full evidence of each cause")):
    """The ranked root causes for one Tier 1 KPI, e.g. /api/root-cause/kpis/T1-01."""
    try:
        with db.connection() as conn:
            aid = _resolve(conn, analysis_id, run_id)
            if aid is None:
                _no_analysis()
            found = store.list_findings(conn, aid, kpi_id=kpi_id, detail=detail,
                                        limit=config.MAX_FINDINGS_PAGE)
            if not found:
                raise HTTPException(status_code=404, detail={
                    "error": "kpi_not_analysed", "kpi_id": kpi_id.upper(),
                    "message": f"{kpi_id.upper()} has no root causes in analysis {aid}. Only Tier 1 "
                               f"KPIs the KPI engine flagged Critical are analysed."})
            return {"source": "database", "analysis_id": aid, "kpi_id": kpi_id.upper(),
                    "root_causes": found}
    except HTTPException:
        raise
    except Exception as exc:
        _unavailable(exc)


@router.get("/findings")
def findings(run_id: Optional[int] = Query(None), analysis_id: Optional[int] = Query(None),
             kpi_id: Optional[str] = Query(None, description="filter by Tier 1 KPI id, e.g. T1-01"),
             gap: Optional[str] = Query(None, description="filter by GAP id, e.g. GAP-08"),
             confidence: Optional[str] = Query(None, description="High | Medium | Low"),
             root_cause_type: Optional[str] = Query(None, description="Process, Workload, ..."),
             detail: bool = Query(False),
             limit: int = Query(100, ge=1, le=500)):
    """The flat, filterable root-cause list behind the dashboard table."""
    try:
        with db.connection() as conn:
            aid = _resolve(conn, analysis_id, run_id)
            if aid is None:
                _no_analysis()
            rows = store.list_findings(conn, aid, kpi_id=kpi_id, gap=gap, confidence=confidence,
                                       root_cause_type=root_cause_type, detail=detail,
                                       limit=min(limit, config.MAX_FINDINGS_PAGE))
            return {"source": "database", "analysis_id": aid, "count": len(rows),
                    "filters": {"kpi_id": kpi_id, "gap": gap, "confidence": confidence,
                                "root_cause_type": root_cause_type},
                    "findings": rows}
    except HTTPException:
        raise
    except Exception as exc:
        _unavailable(exc)


@router.get("/findings/{finding_id}")
def finding(finding_id: str, analysis_id: Optional[int] = Query(None)):
    """One root cause with its full detailed evidence, addressed by id, e.g. RC-T1-01-2."""
    try:
        with db.connection() as conn:
            row = store.get_finding(conn, finding_id.upper(), analysis_id)
            if not row:
                raise HTTPException(status_code=404, detail={
                    "error": "finding_not_found", "finding_id": finding_id.upper(),
                    "message": "No such root cause. Ids are RC-<tier1 kpi>-<rank>, e.g. RC-T1-01-1."})
            row.pop("id", None)
            return {"source": "database", **row}
    except HTTPException:
        raise
    except Exception as exc:
        _unavailable(exc)


@router.get("/gaps")
def gaps(run_id: Optional[int] = Query(None), analysis_id: Optional[int] = Query(None)):
    """Which GAP ids the analysis cites and how often - joins back to the GAP library."""
    try:
        with db.connection() as conn:
            aid = _resolve(conn, analysis_id, run_id)
            if aid is None:
                _no_analysis()
            cited = store.gap_rollup(conn, aid)
            if cited:
                ids = [g["gap_reference"] for g in cited]
                library = {g["gap_reference"]: g for g in db.rows(conn, f"""
                    SELECT gap_reference, stage_or_domain, gap_definition,
                           rca_hypothesis_not_fact, intervention_options
                    FROM {db.schema()}.gap_mapping WHERE gap_reference = ANY(%s);""", [ids])}
                detected = {g["gap_reference"]: g for g in db.rows(conn, f"""
                    SELECT gap_reference, COUNT(*) AS detections,
                           COUNT(DISTINCT process_instance_id) AS cases
                    FROM {db.schema()}.gap_detection WHERE gap_reference = ANY(%s)
                    GROUP BY gap_reference;""", [ids])}
                for g in cited:
                    g.update(library.get(g["gap_reference"], {}))
                    g["detected"] = detected.get(g["gap_reference"], {"detections": 0, "cases": 0})
            return {"source": "database", "analysis_id": aid, "gaps": cited}
    except HTTPException:
        raise
    except Exception as exc:
        _unavailable(exc)


@router.get("/analyses")
def analyses(limit: int = Query(20, ge=1, le=100)):
    """The analysis history, newest first."""
    try:
        with db.connection() as conn:
            return {"source": "database", "analyses": store.list_analyses(conn, limit)}
    except Exception as exc:
        _unavailable(exc)


@router.get("/analyses/{analysis_id}")
def analysis(analysis_id: int):
    try:
        with db.connection() as conn:
            row = store.get_analysis(conn, analysis_id=analysis_id)
            if not row:
                _no_analysis()
            analysis_json = row.pop("analysis_json", None) or {}
            return _with_source({**row, **analysis_json}, "database")
    except HTTPException:
        raise
    except Exception as exc:
        _unavailable(exc)


@router.get("/analyses/{analysis_id}/evidence")
def evidence(analysis_id: int):
    """The evidence package the analysis was grounded in - the audit trail for every statistic."""
    try:
        with db.connection() as conn:
            row = store.get_evidence(conn, analysis_id)
            if not row:
                _no_analysis()
            if row.get("evidence_json") is None:
                raise HTTPException(status_code=404, detail={
                    "error": "evidence_not_stored", "analysis_id": analysis_id,
                    "message": "This analysis was stored with --no-store-evidence."})
            return {"source": "database", **row}
    except HTTPException:
        raise
    except Exception as exc:
        _unavailable(exc)
