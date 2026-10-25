-- =====================================================================
-- Talent Acquisition - Root Cause Agent output storage
-- Applied by 01_create_tables.py and again on API startup, so an existing
-- database picks the tables up without a rebuild. Idempotent.
-- =====================================================================
SET search_path TO {schema}, public;

-- One row per Root Cause Agent run. analysis_json holds the full agent answer exactly as
-- validated; the flattened findings below exist so the API can filter without parsing JSON.
CREATE TABLE IF NOT EXISTS root_cause_analysis (
    analysis_id           SERIAL PRIMARY KEY,
    run_id                INTEGER REFERENCES kpi_run (run_id) ON DELETE CASCADE,
    created_at            TIMESTAMP   DEFAULT NOW(),
    model                 VARCHAR(60),
    reasoning_effort      VARCHAR(10),
    analysis_title        TEXT,
    overall_observation   TEXT,
    critical_tier1_count  SMALLINT,
    findings_count        SMALLINT,
    validation_status     VARCHAR(20),       -- passed / problems
    validation_problems   JSONB,
    evidence_run_id       INTEGER,           -- run_id the evidence package was built from
    evidence_json         JSONB,             -- the evidence the answer was grounded in (audit)
    analysis_json         JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rca_run ON root_cause_analysis (run_id, analysis_id DESC);

-- One row per ranked root cause, flattened from analysis_json for querying and drill-down.
CREATE TABLE IF NOT EXISTS root_cause_finding (
    id                      SERIAL PRIMARY KEY,
    analysis_id             INTEGER NOT NULL REFERENCES root_cause_analysis (analysis_id) ON DELETE CASCADE,
    finding_id              VARCHAR(40) NOT NULL,      -- RC-<tier1 kpi>-<rank>, e.g. RC-T1-01-2
    run_id                  INTEGER,
    tier1_kpi_id            VARCHAR(10) NOT NULL,
    tier1_kpi_name          VARCHAR(80),
    tier1_current_value     NUMERIC(14,2),
    tier1_target            NUMERIC(14,2),
    tier1_unit              VARCHAR(20),
    tier1_variance_pct      NUMERIC(10,1),
    tier1_cohort_rule       TEXT,
    rank                    SMALLINT NOT NULL,
    root_cause              TEXT NOT NULL,
    root_cause_type         VARCHAR(30),
    confidence              VARCHAR(10),
    root_cause_summary      TEXT,
    impact_summary          TEXT,
    causal_explanation      TEXT,
    gap_references          VARCHAR(120),              -- semicolon separated GAP ids
    linked_tier2_kpis       VARCHAR(120),              -- semicolon separated Tier 2 ids
    affected_case_type      VARCHAR(12),
    affected_cases_count    INTEGER,
    affected_cases_pct      NUMERIC(6,1),
    affected_denominator    TEXT,
    evidence_limitations    TEXT,
    impact_statistics       JSONB,
    evidence_chain          JSONB,
    symptoms                JSONB,
    contributing_factors    JSONB,
    alternative_explanations JSONB,
    recommended_interventions JSONB,
    detail_json             JSONB,                     -- the full detailed root-cause object
    UNIQUE (analysis_id, finding_id)
);
CREATE INDEX IF NOT EXISTS ix_rcf_kpi        ON root_cause_finding (tier1_kpi_id);
CREATE INDEX IF NOT EXISTS ix_rcf_analysis   ON root_cause_finding (analysis_id, tier1_kpi_id, rank);
CREATE INDEX IF NOT EXISTS ix_rcf_confidence ON root_cause_finding (confidence);

-- Latest analysis, and its findings
CREATE OR REPLACE VIEW v_root_cause_latest AS
    SELECT * FROM root_cause_analysis ORDER BY analysis_id DESC LIMIT 1;

CREATE OR REPLACE VIEW v_root_cause_findings_latest AS
    SELECT f.* FROM root_cause_finding f
    JOIN v_root_cause_latest a ON a.analysis_id = f.analysis_id
    ORDER BY f.tier1_kpi_id, f.rank;
