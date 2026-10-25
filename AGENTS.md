# AGENTS.md

This repository is a Deloitte Kinetic Talent Acquisition prototype with three main layers:

- Python data generation, validation, and KPI/postgres pipeline in the project root
- FastAPI backend services in [backend](backend)
- Vite + React frontend in [frontend](frontend)

Use this file as the default source of truth for working in this codebase. Keep changes aligned with the existing synthetic-data and evidence-based process-mining design.

## 1. Project purpose

The code builds and analyzes a synthetic Talent Acquisition event log driven by the BRD and KPI definitions in the repository. The data model is intentionally deterministic and evidence-based:

- Synthetic cases are generated from a fixed seed and versioned configuration.
- Event rows are not supposed to carry direct "gap" labels; gaps are inferred from evidence.
- Root-cause analysis should rely on process evidence, not fabricated causal narratives.

## 2. Repository layout

- [generate_ta_event_log.py](generate_ta_event_log.py): regenerate the synthetic CSV extract
- [validate_ta_event_log.py](validate_ta_event_log.py): integrity and plausibility checks
- [run_all.py](run_all.py): full ETL/KPI pipeline runner
- [01_create_tables.py](01_create_tables.py), [02_load_master_data.py](02_load_master_data.py), [03_load_event_log.py](03_load_event_log.py), [04_compute_kpis_to_postgres.py](04_compute_kpis_to_postgres.py): data-loading pipeline stages
- [db_config.py](db_config.py), [kpi_core.py](kpi_core.py), [root_cause_agent.py](root_cause_agent.py), [root_cause_prompt.py](root_cause_prompt.py), [root_cause_schema.py](root_cause_schema.py), [root_cause_tool.py](root_cause_tool.py): core analytics and root-cause logic
- [backend](backend): FastAPI application, routers, services, and DB helpers
- [data](data): generated CSV inputs/outputs used by the pipeline
- [frontend](frontend): React dashboard UI

## 3. Key commands

From the repository root:

```bash
python generate_ta_event_log.py
python validate_ta_event_log.py
python run_all.py
python run_all.py --source generate --recreate
python run_all.py --offline
```

Backend API:

```bash
cd backend
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8010
```

Frontend app:

```bash
cd frontend
npm install
npm run dev
```

## 4. Working conventions

- Run commands from the project root when working with the root-level Python scripts and imports.
- Keep the pipeline deterministic. If you change generator logic or KPI logic, preserve the intended seed/version behavior unless the task explicitly requires a new dataset design.
- Treat [data](data) outputs as generated artifacts unless the task is to change the generator itself.
- Do not treat gap findings as truth by themselves; they are evidence-backed hypotheses about process execution.
- When changing root-cause logic, keep the explanation grounded in events, stages, SLAs, and case context instead of invented business claims.

## 5. Architecture notes

- The project is a process-mining dataset with requisition and application case types in a single event log.
- KPI computation and analytics remain closely tied to SQL/Postgres-backed storage, while the API exposes analysis results for the frontend.
- The frontend is UI-only; API contract and data interpretation live in the backend routers and services.

## 6. Validation expectations

There is no formal automated test suite checked into this repo. Prefer these validation patterns:

- run the relevant script directly after a generator or KPI change
- verify the FastAPI app starts and exposes the health endpoint
- verify the frontend builds successfully with Vite when changing UI code

## 7. Before editing

- Prefer the smallest change that addresses the root problem.
- Match the repository's existing Python style and naming conventions.
- Preserve CSV column naming, run order, and database schema expectations unless the task explicitly changes them.
- If a change affects the synthetic data generator, check the downstream impact on validation and KPI pipelines.

## 8. Files to consult first

- [README.md](README.md): the clearest project overview and design assumptions
- [run_all.py](run_all.py): canonical pipeline orchestration
- [backend/main.py](backend/main.py): API bootstrap and startup behavior
- [frontend/package.json](frontend/package.json): frontend scripts and dependencies

This repo is domain-specific and process-heavy; keep AI-generated reasoning evidence-based and traceable to the underlying event log and KPI logic.
