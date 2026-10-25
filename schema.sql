-- =====================================================================
-- Talent Acquisition Process  -  Deloitte Kinetic backend schema (PostgreSQL)
-- Schema name is substituted by 01_create_tables.py ({schema})
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS {schema};
SET search_path TO {schema}, public;

-- ---------------------------------------------------------------------
-- 1. REFERENCE / DICTIONARY TABLES
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS activity_master (
    activity_name            VARCHAR(80)  PRIMARY KEY,
    stage_code               VARCHAR(5)   NOT NULL,      -- S1..S9, S0 = cross-stage
    activity_stage           VARCHAR(60)  NOT NULL,
    stage_sequence           SMALLINT,
    primary_source_system    VARCHAR(60)  NOT NULL,      -- exactly one per activity (BRD section 5)
    primary_persona          VARCHAR(40)  NOT NULL,
    manual_touch_by_default  CHAR(1),
    rework_activity          CHAR(1),
    exception_activity       CHAR(1),
    closes_sla_codes         VARCHAR(60),
    description              TEXT
);

CREATE TABLE IF NOT EXISTS sla_definitions (
    sla_code                       VARCHAR(10) PRIMARY KEY,
    sla_name                       VARCHAR(80),
    from_activity                  VARCHAR(80),
    to_activity                    VARCHAR(80),
    target_business_days_standard  NUMERIC(6,2),
    target_minutes_standard        INTEGER,
    target_minutes_urgent          INTEGER,
    target_minutes_critical        INTEGER,
    stage_code                     VARCHAR(5),
    applies_to                     VARCHAR(20),          -- REQUISITION / APPLICATION / BOTH
    basis                          TEXT,
    status                         TEXT
);

CREATE TABLE IF NOT EXISTS gap_mapping (
    gap_reference               VARCHAR(10) PRIMARY KEY,
    stage_or_domain             VARCHAR(60),
    gap_definition              TEXT,
    tier2_evidence              VARCHAR(80),             -- comma separated Tier 2 kpi_ids
    tier1_impact                VARCHAR(80),             -- comma separated Tier 1 kpi_ids
    illustrative_detection_rule TEXT,
    rca_hypothesis_not_fact     TEXT,
    intervention_options        TEXT
);

-- Tier 1 / Tier 2 CATALOGUES (definitions from the BRD). The computed values live in
-- kpi_tier1_health / kpi_tier2_diagnostics - these two tables are the dictionary only.
CREATE TABLE IF NOT EXISTS kpi_catalogue_tier1 (
    kpi_id         VARCHAR(10) PRIMARY KEY,
    kpi            VARCHAR(80),
    definition     TEXT,
    primary_data   VARCHAR(80),
    direction      VARCHAR(60),
    tier2_drivers  VARCHAR(120)
);

CREATE TABLE IF NOT EXISTS kpi_catalogue_tier2 (
    kpi_id         VARCHAR(10) PRIMARY KEY,
    diagnostic_kpi VARCHAR(100),
    definition     TEXT,
    feeds_tier1    VARCHAR(80)
);

CREATE TABLE IF NOT EXISTS persona_master (
    persona_id     VARCHAR(10) PRIMARY KEY,
    persona        VARCHAR(40) UNIQUE,
    persona_group  VARCHAR(40),
    hourly_rate    NUMERIC(10,2)                          -- used by recruitment effort / cost per hire
);

CREATE TABLE IF NOT EXISTS data_dictionary (
    dict_id     SERIAL PRIMARY KEY,
    table_name  VARCHAR(40),
    field       VARCHAR(120),
    type        VARCHAR(40),
    definition  TEXT
);

-- ---------------------------------------------------------------------
-- 2. CASE MASTERS  (two linked case types - BRD section 6.1)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS requisition_master (
    requisition_id                        VARCHAR(20) PRIMARY KEY,
    business_unit                         VARCHAR(60),
    job_family                            VARCHAR(40),
    location                              VARCHAR(40),
    job_level                             VARCHAR(10),
    priority                              VARCHAR(15),
    demand_type                           VARCHAR(30),
    positions_count                       SMALLINT,
    role_complexity_score                 NUMERIC(6,2),
    hiring_manager_id_pseudonymous        VARCHAR(20),
    approver_id_pseudonymous              VARCHAR(20),
    workforce_analyst_id_pseudonymous     VARCHAR(20),
    recruiter_id_pseudonymous             VARCHAR(20),
    recruiter_weighted_load_at_assignment NUMERIC(10,2),
    recruiter_open_reqs_at_assignment     SMALLINT,
    requisition_open_timestamp            TIMESTAMP,
    demand_validated_timestamp            TIMESTAMP,
    requisition_submit_timestamp          TIMESTAMP,
    requisition_approved_timestamp        TIMESTAMP,
    recruiter_assigned_timestamp          TIMESTAMP,
    jd_finalized_timestamp                TIMESTAMP,
    first_posting_timestamp               TIMESTAMP,
    first_shortlist_timestamp             TIMESTAMP,
    first_interview_timestamp             TIMESTAMP,
    first_offer_sent_timestamp            TIMESTAMP,
    first_join_timestamp                  TIMESTAMP,
    requisition_close_timestamp           TIMESTAMP,
    requisition_outcome                   VARCHAR(30),
    closure_reason_code                   VARCHAR(50),
    days_open                             NUMERIC(10,2),
    business_days_open                    NUMERIC(10,2),
    days_to_approval                      NUMERIC(10,2),
    days_demand_to_approval               NUMERIC(10,2),
    days_to_assignment                    NUMERIC(10,2),
    days_to_posting                       NUMERIC(10,2),
    days_to_first_shortlist               NUMERIC(10,2),
    days_to_first_interview               NUMERIC(10,2),
    days_to_first_offer                   NUMERIC(10,2),
    time_to_fill_business_days            NUMERIC(10,2),
    applications_count                    SMALLINT,
    screened_count                        SMALLINT,
    shortlisted_count                     SMALLINT,
    interviewed_count                     SMALLINT,
    offers_count                          SMALLINT,
    offers_accepted_count                 SMALLINT,
    offers_declined_count                 SMALLINT,
    withdrawn_count                       SMALLINT,
    hires_count                           SMALLINT,
    approval_return_count                 SMALLINT,
    demand_return_count                   SMALLINT,
    jd_revision_count                     SMALLINT,
    sourcing_extension_count              SMALLINT,
    additional_profiles_requested_count   SMALLINT,
    recruiter_reassigned_flag             CHAR(1),
    reopened_flag                         CHAR(1),
    on_hold_flag                          CHAR(1),
    policy_exception_documented_flag      CHAR(1),
    integration_failure_count             SMALLINT,
    rework_loop_count                     SMALLINT,
    total_elapsed_business_minutes        INTEGER,
    total_elapsed_business_days           NUMERIC(10,2),
    pipeline_rework_event_count           INTEGER,
    pipeline_manual_touch_count           INTEGER,
    pipeline_sla_breach_count             INTEGER,
    pipeline_follow_up_event_count        INTEGER,
    max_inactivity_business_days          NUMERIC(10,2),
    max_event_gap_business_days           NUMERIC(10,2),
    recorded_wait_business_days           NUMERIC(10,2),
    sla_profile                           VARCHAR(40),
    process_version                       VARCHAR(20),
    policy_version                        VARCHAR(20),
    business_calendar                     VARCHAR(40),
    is_open_censored                      CHAR(1),
    event_count                           INTEGER,
    rework_event_count                    INTEGER,
    manual_touch_count                    INTEGER,
    handoff_count                         INTEGER,
    exception_count                       INTEGER,
    data_quality_issue_count              INTEGER,
    sla_eligible_count                    INTEGER,
    sla_breach_count                      INTEGER,
    sla_overrun_minutes_total             INTEGER,
    follow_up_event_count                 INTEGER,
    waiting_minutes_total                 INTEGER,
    extraction_batch_id                   VARCHAR(40)
);

CREATE TABLE IF NOT EXISTS application_master (
    application_id                     VARCHAR(20) PRIMARY KEY,
    requisition_id                     VARCHAR(20) NOT NULL REFERENCES requisition_master (requisition_id) ON DELETE CASCADE,
    candidate_surrogate_id             VARCHAR(20) NOT NULL,
    source_channel                     VARCHAR(40),
    business_unit                      VARCHAR(60),
    job_family                         VARCHAR(40),
    location                           VARCHAR(40),
    job_level                          VARCHAR(10),
    priority                           VARCHAR(15),
    recruiter_id_pseudonymous          VARCHAR(20),
    hiring_manager_id_pseudonymous     VARCHAR(20),
    candidate_engagement_profile       VARCHAR(15),
    notice_period_days                 SMALLINT,
    application_created_timestamp      TIMESTAMP,
    screening_decision_timestamp       TIMESTAMP,
    shortlist_submitted_timestamp      TIMESTAMP,
    hm_review_decision_timestamp       TIMESTAMP,
    assessment_assigned_timestamp      TIMESTAMP,
    assessment_completed_timestamp     TIMESTAMP,
    first_interview_timestamp          TIMESTAMP,
    last_feedback_timestamp            TIMESTAMP,
    selection_decision_timestamp       TIMESTAMP,
    offer_approved_timestamp           TIMESTAMP,
    offer_sent_timestamp               TIMESTAMP,
    offer_response_timestamp           TIMESTAMP,
    bgv_cleared_timestamp              TIMESTAMP,
    documents_received_timestamp       TIMESTAMP,
    join_timestamp                     TIMESTAMP,
    final_outcome_timestamp            TIMESTAMP,
    application_outcome                VARCHAR(40),
    outcome_stage                      VARCHAR(5),
    outcome_reason_code                VARCHAR(50),
    withdrawal_stage                   VARCHAR(5),
    offer_made_flag                    CHAR(1),
    offer_acceptance_flag              CHAR(1),
    joining_flag                       CHAR(1),
    number_of_interview_rounds         SMALLINT,
    interview_reschedule_count         SMALLINT,
    interview_noshow_count             SMALLINT,
    feedback_return_count              SMALLINT,
    feedback_late_count                SMALLINT,
    assessment_count                   SMALLINT,
    assessment_expired_flag            CHAR(1),
    comp_revision_count                SMALLINT,
    offer_approval_return_count        SMALLINT,
    offer_revision_count               SMALLINT,
    offer_negotiation_count            SMALLINT,
    selection_reopened_flag            CHAR(1),
    additional_round_requested_flag    CHAR(1),
    verification_exception_flag        CHAR(1),
    document_resubmission_count        SMALLINT,
    integration_failure_count          SMALLINT,
    rework_loop_count                  SMALLINT,
    max_inactivity_business_days       NUMERIC(10,2),
    max_event_gap_business_days        NUMERIC(10,2),
    recorded_wait_business_days        NUMERIC(10,2),
    total_elapsed_business_minutes     INTEGER,
    total_elapsed_business_days        NUMERIC(10,2),
    is_open_censored                   CHAR(1),
    event_count                        INTEGER,
    rework_event_count                 INTEGER,
    manual_touch_count                 INTEGER,
    handoff_count                      INTEGER,
    exception_count                    INTEGER,
    data_quality_issue_count           INTEGER,
    sla_eligible_count                 INTEGER,
    sla_breach_count                   INTEGER,
    sla_overrun_minutes_total          INTEGER,
    follow_up_event_count              INTEGER,
    waiting_minutes_total              INTEGER,
    extraction_batch_id                VARCHAR(40)
);
CREATE INDEX IF NOT EXISTS ix_app_requisition ON application_master (requisition_id);
CREATE INDEX IF NOT EXISTS ix_app_outcome     ON application_master (application_outcome);

-- ---------------------------------------------------------------------
-- 3. EVENT LOG  (BRD section 6.2 canonical schema, both case types)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS event_log (
    event_id                        VARCHAR(20) PRIMARY KEY,
    process_instance_id             VARCHAR(20) NOT NULL,
    case_type                       VARCHAR(12) NOT NULL,      -- REQUISITION / APPLICATION
    requisition_id                  VARCHAR(20) NOT NULL,
    application_id                  VARCHAR(20),
    candidate_surrogate_id          VARCHAR(20),
    activity_name                   VARCHAR(80) NOT NULL,
    activity_stage                  VARCHAR(60) NOT NULL,
    stage_code                      VARCHAR(5)  NOT NULL,
    stage_sequence                  SMALLINT,
    event_timestamp                 TIMESTAMP   NOT NULL,
    event_end_timestamp             TIMESTAMP,
    actor_id_pseudonymous           VARCHAR(20),
    actor_persona                   VARCHAR(40) NOT NULL,
    source_system                   VARCHAR(60) NOT NULL,
    source_event_id                 VARCHAR(30),
    status_before                   VARCHAR(50),
    status_after                    VARCHAR(50),
    outcome_code                    VARCHAR(40),
    reason_code                     VARCHAR(50),
    duration_minutes                INTEGER,
    waiting_time_minutes            INTEGER,
    waiting_reason_code             VARCHAR(40),
    sla_code                        VARCHAR(10),
    sla_target_minutes              INTEGER,
    sla_measured_minutes            INTEGER,
    sla_breach_flag                 CHAR(1),
    sla_overrun_minutes             INTEGER,
    rework_flag                     CHAR(1),
    repeat_sequence_number          SMALLINT,
    manual_touch_flag               CHAR(1),
    handoff_flag                    CHAR(1),
    exception_flag                  CHAR(1),
    gap_reference                   VARCHAR(60),               -- intentionally NULL: gaps are derived
    business_unit                   VARCHAR(60),
    job_family                      VARCHAR(40),
    location                        VARCHAR(40),
    job_level                       VARCHAR(10),
    priority                        VARCHAR(15),
    recruiter_id_pseudonymous       VARCHAR(20),
    hiring_manager_id_pseudonymous  VARCHAR(20),
    source_channel                  VARCHAR(40),
    data_quality_flag               CHAR(1),
    extraction_batch_id             VARCHAR(40)
);
CREATE INDEX IF NOT EXISTS ix_ev_case        ON event_log (process_instance_id, event_timestamp);
CREATE INDEX IF NOT EXISTS ix_ev_requisition ON event_log (requisition_id);
CREATE INDEX IF NOT EXISTS ix_ev_activity    ON event_log (activity_name);
CREATE INDEX IF NOT EXISTS ix_ev_stage       ON event_log (stage_code);
CREATE INDEX IF NOT EXISTS ix_ev_ts          ON event_log (event_timestamp);

-- ---------------------------------------------------------------------
-- 4. DERIVED ANALYTICAL INPUTS  (produced with the event log, not invented)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stage_transitions (
    id                         SERIAL PRIMARY KEY,
    process_instance_id        VARCHAR(20) NOT NULL,
    case_type                  VARCHAR(12) NOT NULL,
    requisition_id             VARCHAR(20) NOT NULL,
    application_id             VARCHAR(20),
    stage_code                 VARCHAR(5)  NOT NULL,
    stage_name                 VARCHAR(60) NOT NULL,
    stage_sequence             SMALLINT,
    stage_occurrence           SMALLINT,
    is_revisit                 CHAR(1),
    entry_activity             VARCHAR(80),
    exit_activity              VARCHAR(80),
    entry_timestamp            TIMESTAMP,
    exit_timestamp             TIMESTAMP,
    events_in_stage            SMALLINT,
    elapsed_business_minutes   INTEGER,
    elapsed_business_days      NUMERIC(10,2),
    stage_sla_target_minutes   INTEGER,
    stage_sla_breach_flag      CHAR(1),
    business_unit              VARCHAR(60),
    job_family                 VARCHAR(40),
    location                   VARCHAR(40),
    job_level                  VARCHAR(10),
    priority                   VARCHAR(15),
    recruiter_id_pseudonymous  VARCHAR(20),
    extraction_batch_id        VARCHAR(40)
);
CREATE INDEX IF NOT EXISTS ix_st_case  ON stage_transitions (process_instance_id);
CREATE INDEX IF NOT EXISTS ix_st_stage ON stage_transitions (stage_code);

CREATE TABLE IF NOT EXISTS recruiter_load_snapshot (
    id                            SERIAL PRIMARY KEY,
    snapshot_date                 DATE NOT NULL,
    recruiter_id_pseudonymous     VARCHAR(20) NOT NULL,
    open_requisitions             SMALLINT,
    weighted_load                 NUMERIC(10,2),
    requisitions_open_over_60_days SMALLINT,
    portfolio_design_band         VARCHAR(12),
    extraction_batch_id           VARCHAR(40),
    UNIQUE (snapshot_date, recruiter_id_pseudonymous)
);

CREATE TABLE IF NOT EXISTS gap_detection (
    id                    SERIAL PRIMARY KEY,
    gap_reference         VARCHAR(10) NOT NULL,
    case_type             VARCHAR(12) NOT NULL,
    process_instance_id   VARCHAR(20) NOT NULL,
    requisition_id        VARCHAR(20) NOT NULL,
    application_id        VARCHAR(20),
    activity_stage        VARCHAR(60),
    stage_code            VARCHAR(5),
    detection_rule        TEXT,
    measured_value        NUMERIC(14,2),
    threshold_value       NUMERIC(14,2),
    measure_unit          VARCHAR(40),
    evidence_event_ids    TEXT,
    evidence_event_count  SMALLINT,
    evidence_summary      TEXT,
    detected_at           TIMESTAMP,
    extraction_batch_id   VARCHAR(40)
);
CREATE INDEX IF NOT EXISTS ix_gd_gap  ON gap_detection (gap_reference);
CREATE INDEX IF NOT EXISTS ix_gd_case ON gap_detection (process_instance_id);

-- ---------------------------------------------------------------------
-- 5. KPI RUN CONTROL  (one row per computation; history is kept)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kpi_run (
    run_id                SERIAL PRIMARY KEY,
    run_timestamp         TIMESTAMP DEFAULT NOW(),
    as_of_timestamp       TIMESTAMP NOT NULL,
    period_from           DATE,
    period_to             DATE,
    requisitions_total    INTEGER,
    requisitions_closed   INTEGER,
    requisitions_filled   INTEGER,
    requisitions_open     INTEGER,
    applications_total    INTEGER,
    applications_joined   INTEGER,
    cases_stalled         INTEGER,
    events_total          INTEGER,
    parameters            JSONB,
    status                VARCHAR(20) DEFAULT 'COMPLETED'
);

-- ---------------------------------------------------------------------
-- 6. KPI SUMMARY  -  one row per run: the dashboard header
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kpi_summary (
    id                            SERIAL PRIMARY KEY,
    run_id                        INTEGER NOT NULL REFERENCES kpi_run (run_id) ON DELETE CASCADE,
    as_of_timestamp               TIMESTAMP,
    period_from                   DATE,
    period_to                     DATE,
    requisitions_total            INTEGER,
    requisitions_filled           INTEGER,
    requisitions_cancelled        INTEGER,
    requisitions_closed_no_hire   INTEGER,
    requisitions_open             INTEGER,
    fill_rate_pct                 NUMERIC(6,1),
    applications_total            INTEGER,
    applications_joined           INTEGER,
    applications_withdrawn        INTEGER,
    applications_in_progress      INTEGER,
    offers_sent                   INTEGER,
    hires                         INTEGER,
    events_total                  INTEGER,
    time_to_fill_median_days      NUMERIC(10,2),
    time_to_fill_p90_days         NUMERIC(10,2),
    approval_lead_time_days       NUMERIC(10,2),
    sla_attainment_pct            NUMERIC(6,1),
    waiting_time_share_pct        NUMERIC(6,1),
    offer_acceptance_pct          NUMERIC(6,1),
    offer_to_join_pct             NUMERIC(6,1),
    withdrawal_rate_pct           NUMERIC(6,1),
    rework_rate_pct               NUMERIC(6,1),
    conformance_rate_pct          NUMERIC(6,1),
    manual_touch_pct_of_events    NUMERIC(6,1),
    recruiter_load_median         NUMERIC(10,2),
    recruiter_load_p90            NUMERIC(10,2),
    tier1_critical                SMALLINT,
    tier1_watch                   SMALLINT,
    tier1_healthy                 SMALLINT,
    tier2_critical                SMALLINT,
    gap_findings_total            INTEGER,
    gap_references_detected       SMALLINT,
    top_gap_reference             VARCHAR(10),
    top_gap_findings              INTEGER,
    top_bottleneck_stage          VARCHAR(60),
    top_bottleneck_stage_days     NUMERIC(10,2),
    top_waiting_activity          VARCHAR(80),
    UNIQUE (run_id)
);

-- ---------------------------------------------------------------------
-- 7. TIER 1  -  KPI HEALTH OVERVIEW
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kpi_tier1_health (
    id                      SERIAL PRIMARY KEY,
    run_id                  INTEGER NOT NULL REFERENCES kpi_run (run_id) ON DELETE CASCADE,
    kpi_id                  VARCHAR(10) NOT NULL,
    kpi_name                VARCHAR(80) NOT NULL,
    formula                 TEXT,
    current_value           NUMERIC(14,2),
    target                  NUMERIC(14,2),
    unit                    VARCHAR(20),
    better_when             VARCHAR(10),               -- lower / higher
    variance_vs_target_pct  NUMERIC(10,1),
    health                  VARCHAR(15),               -- Critical / Watch / Healthy / Informational
    trend_direction         VARCHAR(10),               -- up / down / flat vs previous run
    previous_value          NUMERIC(14,2),
    denominator_cases       INTEGER,                   -- cohort size behind the value
    cohort_rule             TEXT,                      -- inclusion / exclusion rule applied
    tier2_drivers           VARCHAR(120),
    UNIQUE (run_id, kpi_id)
);

-- ---------------------------------------------------------------------
-- 8. TIER 2  -  DIAGNOSTIC / ROOT-CAUSE KPIs
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kpi_tier2_diagnostics (
    id                      SERIAL PRIMARY KEY,
    run_id                  INTEGER NOT NULL REFERENCES kpi_run (run_id) ON DELETE CASCADE,
    kpi_id                  VARCHAR(10) NOT NULL,
    kpi_name                VARCHAR(100) NOT NULL,
    formula                 TEXT,
    current_value           NUMERIC(14,2),
    target                  NUMERIC(14,2),
    unit                    VARCHAR(20),
    better_when             VARCHAR(10),
    variance_vs_target_pct  NUMERIC(10,1),
    health                  VARCHAR(15),
    feeds_tier1_kpi         VARCHAR(60),               -- comma separated Tier 1 kpi_ids explained
    gap_reference           VARCHAR(80),               -- semicolon separated GAP ids -> gap_mapping
    root_cause_category     VARCHAR(60),
    denominator_cases       INTEGER,
    cohort_rule             TEXT,
    UNIQUE (run_id, kpi_id)
);

-- ---------------------------------------------------------------------
-- 9. ROOT-CAUSE SUPPORTING TABLES
-- ---------------------------------------------------------------------
-- Stage metrics are reported PER CASE TYPE. Requisition-level stage dwell and
-- candidate-level stage dwell measure different things and must never be pooled
-- into one denominator (BRD section 6.1).
CREATE TABLE IF NOT EXISTS kpi_stage_breakdown (
    id                        SERIAL PRIMARY KEY,
    run_id                    INTEGER NOT NULL REFERENCES kpi_run (run_id) ON DELETE CASCADE,
    case_type                 VARCHAR(12) NOT NULL,
    stage_code                VARCHAR(5)  NOT NULL,
    stage_name                VARCHAR(60) NOT NULL,
    stage_sequence            SMALLINT,
    cases                     INTEGER,
    occurrences               INTEGER,
    revisit_rate_pct          NUMERIC(6,1),
    median_elapsed_days       NUMERIC(10,2),
    avg_elapsed_days          NUMERIC(10,2),
    p90_elapsed_days          NUMERIC(10,2),
    share_of_cycle_pct        NUMERIC(6,1),
    stage_sla_days            NUMERIC(6,2),
    sla_breach_pct            NUMERIC(6,1),
    avg_recorded_waiting_days NUMERIC(10,2),
    avg_processing_hours      NUMERIC(10,2),
    avg_events                NUMERIC(8,1),
    avg_manual_events         NUMERIC(8,1),
    rework_events_per_case    NUMERIC(8,2),
    exception_events_per_case NUMERIC(8,2),
    handoffs_per_case         NUMERIC(8,2),
    gap_references            VARCHAR(80),
    health                    VARCHAR(15)
);

CREATE TABLE IF NOT EXISTS kpi_activity_breakdown (
    id                     SERIAL PRIMARY KEY,
    run_id                 INTEGER NOT NULL REFERENCES kpi_run (run_id) ON DELETE CASCADE,
    activity_name          VARCHAR(80) NOT NULL,
    stage_code             VARCHAR(5),
    activity_stage         VARCHAR(60),
    actor_persona          VARCHAR(40),
    source_system          VARCHAR(60),
    events                 INTEGER,
    cases                  INTEGER,
    cases_touched_pct      NUMERIC(6,1),
    avg_duration_min       NUMERIC(10,1),
    avg_waiting_min        NUMERIC(10,1),
    total_waiting_days     NUMERIC(12,1),
    repeat_event_pct       NUMERIC(6,1),
    sla_breach_pct         NUMERIC(6,1),
    avg_sla_overrun_days   NUMERIC(10,2),
    exception_rate_pct     NUMERIC(6,1),
    rework_share_pct       NUMERIC(6,1),
    manual_pct             NUMERIC(6,1),
    handoff_pct            NUMERIC(6,1),
    top_waiting_reason     VARCHAR(40),
    wait_is_container      CHAR(1),                 -- Y = preceding interval is a linked pipeline
    waiting_rank           INTEGER,                 -- NULL for container activities
    effort_rank            INTEGER,
    UNIQUE (run_id, activity_name)
);

CREATE TABLE IF NOT EXISTS kpi_rework_loops (
    id                              SERIAL PRIMARY KEY,
    run_id                          INTEGER NOT NULL REFERENCES kpi_run (run_id) ON DELETE CASCADE,
    rework_loop_type                VARCHAR(80) NOT NULL,
    case_type                       VARCHAR(12),
    stage_code                      VARCHAR(5),
    activity_stage                  VARCHAR(60),
    cases                           INTEGER,
    events                          INTEGER,
    cases_pct                       NUMERIC(6,1),
    loops_per_affected_case         NUMERIC(8,2),
    days_lost                       NUMERIC(12,1),
    avg_days_lost_per_affected_case NUMERIC(10,2),
    top_reason_code                 VARCHAR(50),
    gap_reference                   VARCHAR(80),
    UNIQUE (run_id, rework_loop_type)
);

CREATE TABLE IF NOT EXISTS kpi_stalled_cases (
    id                        SERIAL PRIMARY KEY,
    run_id                    INTEGER NOT NULL REFERENCES kpi_run (run_id) ON DELETE CASCADE,
    process_instance_id       VARCHAR(20) NOT NULL,
    case_type                 VARCHAR(12),
    requisition_id            VARCHAR(20),
    application_id            VARCHAR(20),
    last_activity_name        VARCHAR(80),
    activity_stage            VARCHAR(60),
    stage_code                VARCHAR(5),
    actor_persona             VARCHAR(40),
    owner_id_pseudonymous     VARCHAR(20),
    recruiter_id_pseudonymous VARCHAR(20),
    last_event_timestamp      TIMESTAMP,
    open_business_days        NUMERIC(10,1),
    stage_sla_days            NUMERIC(6,2),
    waiting_reason_code       VARCHAR(40),
    open_sla_code             VARCHAR(10),
    open_sla_overrun_days     NUMERIC(10,2),
    business_unit             VARCHAR(60),
    job_family                VARCHAR(40),
    location                  VARCHAR(40),
    job_level                 VARCHAR(10),
    priority                  VARCHAR(15),
    gap_references            VARCHAR(80),
    kinetic_flag              VARCHAR(90),
    recommended_action        VARCHAR(220)
);
CREATE INDEX IF NOT EXISTS ix_stalled_run ON kpi_stalled_cases (run_id, open_business_days DESC);

CREATE TABLE IF NOT EXISTS kpi_monthly_trend (
    id                       SERIAL PRIMARY KEY,
    run_id                   INTEGER NOT NULL REFERENCES kpi_run (run_id) ON DELETE CASCADE,
    month                    VARCHAR(7) NOT NULL,
    requisitions_opened      INTEGER,
    requisitions_filled      INTEGER,
    hires                    INTEGER,
    applications_received    INTEGER,
    offers_sent              INTEGER,
    time_to_fill_days        NUMERIC(10,2),
    approval_lead_time_days  NUMERIC(10,2),
    sla_attainment_pct       NUMERIC(6,1),
    offer_acceptance_pct     NUMERIC(6,1),
    withdrawal_rate_pct      NUMERIC(6,1),
    rework_rate_pct          NUMERIC(6,1),
    conformance_rate_pct     NUMERIC(6,1),
    manual_touches_per_case  NUMERIC(8,1),
    waiting_days_per_case    NUMERIC(10,2),
    recruiter_load_median    NUMERIC(10,2),
    UNIQUE (run_id, month)
);

-- ---------------------------------------------------------------------
-- 10. CONVENIENCE VIEWS  (latest run only)
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_latest_run AS
    SELECT * FROM kpi_run ORDER BY run_id DESC LIMIT 1;

CREATE OR REPLACE VIEW v_kpi_summary_latest AS
    SELECT s.* FROM kpi_summary s JOIN v_latest_run r ON r.run_id = s.run_id;

CREATE OR REPLACE VIEW v_kpi_tier1_latest AS
    SELECT t.* FROM kpi_tier1_health t JOIN v_latest_run r ON r.run_id = t.run_id ORDER BY t.kpi_id;

CREATE OR REPLACE VIEW v_kpi_tier2_latest AS
    SELECT t.* FROM kpi_tier2_diagnostics t JOIN v_latest_run r ON r.run_id = t.run_id ORDER BY t.kpi_id;

-- Root-cause tree without a bridge table: Tier 2 carries its Tier 1 and GAP links,
-- so the edges are produced by unnesting those two columns.
CREATE OR REPLACE VIEW v_root_cause_tree AS
    SELECT t2.run_id,
           TRIM(t1id)  AS tier1_kpi_id,
           t1.kpi_name AS tier1_kpi,
           t1.health   AS tier1_health,
           t1.current_value AS tier1_value,
           t1.target        AS tier1_target,
           t2.kpi_id   AS tier2_kpi_id,
           t2.kpi_name AS tier2_kpi,
           t2.health   AS tier2_health,
           t2.current_value AS tier2_value,
           t2.target        AS tier2_target,
           t2.unit          AS tier2_unit,
           TRIM(gref)  AS gap_reference,
           g.stage_or_domain,
           g.gap_definition,
           g.rca_hypothesis_not_fact,
           g.intervention_options
    FROM kpi_tier2_diagnostics t2
    CROSS JOIN LATERAL unnest(string_to_array(COALESCE(t2.feeds_tier1_kpi, ''), ',')) AS t1id
    LEFT JOIN LATERAL unnest(string_to_array(COALESCE(NULLIF(t2.gap_reference, ''), 'n/a'), ';')) AS gref ON TRUE
    JOIN kpi_tier1_health t1 ON t1.run_id = t2.run_id AND t1.kpi_id = TRIM(t1id)
    LEFT JOIN gap_mapping g  ON g.gap_reference = TRIM(gref)
    JOIN v_latest_run r      ON r.run_id = t2.run_id
    WHERE TRIM(t1id) <> '';

-- Gap findings of the latest extract joined to the reference library
CREATE OR REPLACE VIEW v_gap_findings AS
    SELECT d.gap_reference, g.stage_or_domain, g.gap_definition, g.rca_hypothesis_not_fact,
           COUNT(*) AS findings, COUNT(DISTINCT d.process_instance_id) AS cases,
           COUNT(DISTINCT d.requisition_id) AS requisitions
    FROM gap_detection d
    LEFT JOIN gap_mapping g ON g.gap_reference = d.gap_reference
    GROUP BY d.gap_reference, g.stage_or_domain, g.gap_definition, g.rca_hypothesis_not_fact
    ORDER BY findings DESC;
