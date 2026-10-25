"""
Talent Acquisition / Deloitte Kinetic - Root Cause API.

Run from the project root so the existing modules (db_config, kpi_core, root_cause_*) are
importable:

    uvicorn backend.main:app --reload --port 8010

Configuration is entirely environmental - PG* for the database (db_config.py), see
backend/config.py for the service's own variables. Nothing is hard-coded.
"""
from __future__ import annotations

import logging
import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Allow `uvicorn backend.main:app` from anywhere: the project root holds db_config.py / kpi_core.py.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from . import config, db                       # noqa: E402
from .routers import fix_with_ai, root_cause   # noqa: E402

logging.basicConfig(level=config.LOG_LEVEL,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("kinetic")

app = FastAPI(
    title="Talent Acquisition - Root Cause API",
    description="Evidence-backed root-cause analysis for the Deloitte Kinetic talent "
                "acquisition process: the stored agent output, plus the deterministic "
                "Fix-with-AI analysis (impacted cases, the KPIs they move, the representative "
                "case and what happened in it).",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(root_cause.router)
app.include_router(fix_with_ai.router)


@app.on_event("startup")
def startup() -> None:
    """Provision the root-cause tables so an existing database picks them up without a rebuild."""
    try:
        with db.connection() as conn:
            db.ensure_root_cause_schema(conn)
            run_id = db.latest_run_id(conn)
            analysis_id = db.latest_analysis_id(conn)
            conn.commit()
        log.info("Root Cause API ready (schema=%s, latest KPI run=%s, latest analysis=%s)",
                 db.schema(), run_id, analysis_id)
        if run_id is None:
            log.warning("No KPI run found - run 04_compute_kpis_to_postgres.py first.")
        elif analysis_id is None:
            log.warning("No stored analysis - run root_cause_agent.py. The API will fall back to %s "
                        "if that file exists.", config.FALLBACK_FILE)
    except Exception as exc:                   # the service still starts; /health reports it
        log.error("Startup database check failed: %s", exc)


@app.get("/api/health")
def health():
    return root_cause.health()
