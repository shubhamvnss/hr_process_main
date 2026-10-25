# Synthetic Talent Acquisition event log — Deloitte Kinetic prototype

Synthetic, requisition-centric process-mining dataset built from
`Deloitte_Kinetic_Talent_Acquisition_BRD.pages` (Prototype baseline, 19 Oct 2026).

```bash
python3 generate_ta_event_log.py     # regenerate the extract into data/ (~12s)
python3 validate_ta_event_log.py     # 50 integrity and plausibility checks
```

Deterministic: `SEED = 20261019`, `GEN_VERSION = ta-synth-v1.0`. Re-running produces
byte-identical CSVs.

---

## 1. What was generated

| | |
|---|---|
| Requisition cases (primary lifecycle perspective) | **1,200** |
| Candidate application cases (linked by `requisition_id`) | **7,235** |
| Events | **110,686** (14,920 requisition-level + 95,766 application-level) |
| Distinct canonical activities | 88 across the 9 BRD stages + a cross-stage system/governance bucket |
| Personas | 15 · Source systems | 10 · Reason codes | 78 · Waiting-reason codes | 18 |
| Event window | 2025-04-01 → 2026-10-19 09:00 (reporting cut-off) |
| Business calendar | `IN-STD-09:00-17:30-MONFRI`, 510 productive minutes/day, 7 holidays/year |

The BRD's two-log model (section 6.1) is preserved in **one physical table** with
`case_type ∈ {REQUISITION, APPLICATION}` and `process_instance_id` as the case key,
exactly as the canonical schema in section 6.2 specifies. `requisition_id` is present
on **every** row, so the requisition is always the anchoring lifecycle perspective and
candidate journeys never get mixed into a requisition timeline.

## 2. Files

| File | Rows | What it is |
|---|---|---|
| `ta_event_log.csv` | 110,686 | The event log. 44 columns, BRD 6.2 canonical schema. |
| `ta_requisition_master.csv` | 1,200 | Derived requisition case attributes (BRD 6.3) + milestones, rollups, recruiter load at assignment. |
| `ta_application_master.csv` | 7,235 | Derived application case attributes, outcome, stage counters. |
| `ta_stage_transitions.csv` | 26,343 | Stage entry/exit per occurrence with revisit flag — the direct input for T2-02 / T2-03. |
| `ta_recruiter_load_snapshot.csv` | ~3,400 | Weekly open + weighted requisition load per recruiter — the direct input for T2-22 / T1-11. |
| `ta_activity_master.csv` | 88 | Canonical activity dictionary: stage, one primary source system, persona, flags, SLA closed. |
| `ta_sla_definitions.csv` | 17 | Illustrative SLA profile (SLA-01..SLA-17) with Standard / Urgent / Critical targets. |
| `ta_gap_mapping.csv` | 18 | GAP-01..GAP-18 reference library, transcribed from BRD section 10. |
| `ta_kpi_tier1.csv` / `ta_kpi_tier2.csv` | 12 / 26 | Tier 1 and Tier 2 catalogues from BRD sections 8 and 9. |
| `ta_gap_detection.csv` | 8,027 | Gaps **derived** from the event log by deterministic rules, with cited `evidence_event_ids`. |
| `ta_scenario_ground_truth.csv` | 1,200 | What the generator intended. For validating the detector only — never an analytical input. |
| `ta_data_dictionary.csv` | 47 | Field definitions and calculation rules. |

## 3. The design rule that matters most

**`gap_reference` is empty in every event row.** The BRD (section 10) and the pain-point
brief both require gaps to be *discovered* from event evidence, not read off a label.
So the pipeline is:

```
generator  ->  event log (no gap labels)  ->  deterministic rules  ->  ta_gap_detection.csv
                                                                           |
                            ta_scenario_ground_truth.csv  <-- compared --> recall check
```

`validate_ta_event_log.py` closes the loop: it re-derives GAP-01/02/03 from the log and
reports recall against generator intent (100% / 99% / 86%). That is a sanity check on
the rules, not a target — the rules deliberately also fire on cases the generator did
not *design* as problems but which nonetheless breached policy, which is the correct
behaviour for an evidence-based detector.

## 4. How each pain point is produced as evidence, not as a number

Every gap comes from a causal chain among actors and load, so the diagnosis is
reconstructable. Structural drivers built into the actor population:

- **8 of 45 recruiters** carry a deliberately heavy portfolio (`cap_weight` 2.2) and 10 a
  moderate one. Recruiter load is tracked **dynamically**: a requisition's assignment
  delay, screening backlog and posting delay are computed from the weighted load the
  assigned recruiter was actually carrying on that date (`LOAD_REF = 11.0`).
- **5 of 16 approvers** are bottleneck approvers; **3 of 9** workforce analysts are slow;
  **~22% of hiring managers** are chronically slow reviewers and ~20% submit incomplete intake.
- **11% of panelists** submit late feedback and 14% have low availability.
- **3 integration incident windows** (Nov 2025, Mar 2026, Jul 2026) raise sync-failure
  probability 4×, so GAP-16 clusters in time rather than scattering.
- **4 locations** use a slower verification vendor, so GAP-15 clusters by geography.

| Gap | Evidence the log carries | Detection rule in `ta_gap_detection.csv` | Rate |
|---|---|---|---|
| GAP-01 | `Hiring Demand Returned for Clarification` → `Resubmitted` loops with `MISSING_JOB_GRADE` / `MISSING_BUDGET_DETAIL` reason codes | returns ≥ 2 | 6.6% of requisitions |
| GAP-02 | validation requested, reminder events, long pending interval, eventual validation | elapsed > SLA-01, or still pending at cut-off | 33% |
| GAP-03 | approval queue wait, reminders, return/resubmit cycles | submit→approve > SLA-02 **or** returns ≥ 2 | 32% |
| GAP-04 | approved requisition unassigned, `Recruiter Assignment Follow-up` by the hiring manager | approve→assign > SLA-03, load cited as evidence | 15% |
| GAP-05 | repeated `Job Description Revised` with `ROLE_CRITERIA_NOT_ALIGNED`, late posting | revisions ≥ 2 **or** assign→post > SLA-05 | 31% |
| GAP-06 | shortlist submitted, reminder events, late `Shortlist Review Decision Recorded` | submit→decision > SLA-07, or no decision at cut-off | 21% |
| GAP-07 | aged screening queue, `Screening Follow-up Logged`, `Application Rescreened` | receipt→decision > SLA-06 **or** rescreen evidenced | 13% |
| GAP-08 | `Interview Scheduling Follow-up`, `Interview Rescheduled` with `PANEL_UNAVAILABLE`, re-confirmation | worst request→confirm > SLA-08 **or** reschedules ≥ 2 | 39% |
| GAP-09 | `Interview Feedback Reminder Sent`, late submission, `Feedback Returned for Completion` | submission > SLA-09 **or** a return evidenced | 40% |
| GAP-10 | `Assessment Reminder Sent`, `Assessment Expired` → `Reissued`, `Result Synchronized` failures | assign→complete > SLA-10, expiry, or result-sync failure | 38% |
| GAP-11 | all feedback in, `Selection Decision Reminder Sent`, `Decision Reopened`, `Additional Interview Round Requested` | last feedback→decision > SLA-11, repeat, or extra round | 24% |
| GAP-12 | `Offer Approval Returned for Revision` ↔ `Compensation Proposal Revised` loops, `OUTSIDE_APPROVED_BAND` | proposal→approval > SLA-12 **or** revision/return evidenced | 36% |
| GAP-13 | approval→send delay, `Offer Negotiation Requested`, `Offer Revised and Resent` with version reason codes | approve→send > SLA-13 **or** negotiation evidenced | 33% |
| GAP-14 | `Candidate Withdrew` positioned **inside** a long inactivity interval, reason `PROCESS_TOO_SLOW` / `ACCEPTED_OTHER_OFFER` | withdrawal preceded by a ≥ 7 business-day progression gap | 31% of withdrawals |
| GAP-15 | `Verification Information Requested`, `Verification Exception Raised`, `Document Resubmission Requested` | acceptance→clearance/documents > SLA-15/16, or unresolved exception | 42% of accepted offers |
| GAP-16 | `Integration Sync Failed` → `Retried` → (`Manual Status Re-entry` \| `Duplicate Status Update Detected`) → `Completed` | any failure/retry/duplicate/manual re-entry; recovery lag reported | 4.5% of cases |
| GAP-17 | heavy weighted load at assignment, aged queues, high follow-up volume | load ≥ P80 **and** (aged assignment **or** ≥ 6 follow-ups) | 11% |
| GAP-18 | a mandatory control event is **absent** from the trace and no `Policy Exception Documented` accompanies it | missing `Requisition Approved` / `Budget Validated` / `Offer Approved` / `BGV Cleared` | 1.3% |

Note on GAP-18: ~30–40% of deliberate deviations *are* accompanied by a documented
`Policy Exception Documented` event. Those are **legitimate** exceptions and the rule
correctly does not flag them — so the prototype can be tested on telling the two apart.

## 5. Counterexamples — deliberately included

The brief warns against data that lets an RCA agent conclude "delay causes withdrawal".
So candidates carry an engagement profile (`resilient` 22% / `normal` 66% / `impatient` 12%)
that modulates the disengagement hazard by 0.3× / 1.0× / 2.2×, and impatient candidates
carry a flat hazard independent of any delay. The result:

- Of applications with a ≥ 7 business-day inactivity gap, only **~7% withdraw** and
  **~62% go on to join**.
- **~70% of withdrawals** had no ≥ 7-day wait beforehand at all.
- A **healthy benchmark cohort of ~18% of requisitions** (responsive stakeholders, complete
  intake, standard-capacity recruiter, available panels) runs alongside the rest in the same
  job families and locations: median time to fill 76.1 vs 82.8 business days, 0.43 vs 1.29
  rework loops per case, and 61% vs 19% with no requisition-stage finding.
- Senior roles (L6/L7) and overseas locations legitimately take longer through
  `role_complexity_score`, so "slow" is not automatically "badly executed".

The summary at the end of the generator prints the best contrast triples it found —
same job family, location and job level, time to fill ranging from ~47 to ~190 business
days. Those are the cases Kinetic should be able to *explain*.

## 6. Headline KPI position of the dataset

| KPI | Value | Note |
|---|---|---|
| T1-01 time to fill (approved → joined) | median **81.3** bd, mean 84.7, P90 113.9 | includes the candidate's notice period (30/60/90 days) |
| T1-02 requisition approval lead time | median **1.5** bd | long tail from bottleneck approvers |
| T1-03 stage SLA attainment | **76.5%** (9,296 breaches of 39,578 measured intervals) | evaluated per priority segment, never a universal threshold |
| T1-04 waiting-time share | median **58%** of case elapsed time sits in intervals with a *recorded* waiting reason | only 13% of events carry a reliable duration, so active time is **reported, not inferred** (BRD 6.4) |
| T1-05 conversion | 55% shortlisted → 36% interviewed → 22% final decision → 18% offered | funnel printed by the generator |
| T1-06 offer acceptance | **82.5%** of offers with a final response | expired/pending separated, per BRD |
| T1-07 offer-to-join | **76.4%** | accepted-but-did-not-join cases preserved |
| T1-08 withdrawal rate | **4.8%** of applications, spread across stages 5–9 | |
| T1-09 rework | by loop: **53%** of requisitions, **27%** of applications | both event-count and loop-count variants reported |
| T1-10 conformance | **98.9%** | |
| T1-11 recruiter load | median weighted **7.1**, P80 11.4, max 26.8 | from `ta_recruiter_load_snapshot.csv` |

Open cases are **right-censored**, never completed: 12.0% of requisitions and 2.2% of
applications are `In Progress` with no close timestamp and `is_open_censored = Y`.

## 7. Outcome mix, and where it differs from the suggested starting mix

| Requisition outcome | Share | | Application outcome | Share |
|---|---|---|---|---|
| Filled | 63.1% | | Rejected – screening/shortlist | 56.9% |
| Cancelled | 14.8% | | Rejected – interview/assessment | 12.6% |
| Open / In Progress | 12.0% | | **Joined** | **11.3%** |
| Closed Without Hire | 7.2% | | Withdrawn | 4.8% |
| Rejected at Approval | 3.0% | | Rejected – final selection | 4.1% |
| | | | Closed – requisition cancelled | 2.8% |
| | | | Offer declined | 2.7% |
| | | | In Progress | 2.2% |
| | | | No show | 1.4% |
| | | | Accepted – did not join | 0.8% |
| | | | Offer expired | 0.4% |

**Two deliberate deviations from the suggested mix, flagged rather than silently applied:**

1. *"Joined candidates 20–35% of applications"* → actual **11.3%**. A 20–35% per-application
   join rate is not reachable alongside ~6 progressed applications per requisition and one
   hire per position; it would imply roughly two hires per requisition. I kept the hire
   count tied to `positions_count` and honoured the *spirit* of the target instead:
   **36% of applications reach interview** and **22% reach a final selection decision**, so
   there is a large population of deeply-progressed journeys to mine. To move the join share
   up, shrink the pipeline in `app_volume()` (e.g. `T(2, 7, 18)` → `T(1, 3, 6)`).
2. *"Open/in-progress 5–10%"* → **12.0% of requisitions** (2.2% of applications). Open cases
   here are an emergent consequence of right-censoring a 16-month demand window at a fixed
   cut-off, which is more faithful than forcing a quota. Move `WINDOW_END` earlier to reduce it.

*"Applications with delays or rework 20–30%"* lands at 27.8% of applications. The
requisition figure is higher (53%) because requisition-level rework aggregates events
driven by the whole candidate pipeline — `Additional Profiles Requested` and
`Sourcing Campaign Extended` fire when individual candidates fail.

## 8. Tuning dials

All in `generate_ta_event_log.py`:

- `N_REQUISITIONS`, `WINDOW_START`, `WINDOW_END`, `CUTOFF`, `SEED`
- `SLA_DEFS` — the 17 illustrative SLA targets; `PRIORITY_SLA_FACTOR` scales them per priority
- `LOAD_REF` — the weighted load a recruiter absorbs comfortably; raising it weakens the
  capacity→delay link that drives GAP-04/07/17
- `cap_weight` in `RECRUITERS`, plus the `bottleneck` / `slow` / `slow_reviewer` /
  `late_feedback` / `low_availability` actor flags
- `clean_run = chance(0.18)` in `make_requisition` — size of the healthy benchmark cohort
- `TEMPERAMENT_FACTOR` and the `base_p` arguments to `try_withdraw` — withdrawal hazard
- `SYNC_INCIDENT_WINDOWS` and the `p_base` arguments to `sync_hiccup` — GAP-16 density
- `intent = wpick([("FILL", 0.74), ("NOHIRE", 0.11), ("CANCEL", 0.15)])` — requisition outcome mix

## 9. Caveats

- Every SLA threshold, KPI target, system name, gap rule and volume in this dataset is a
  **prototype design assumption**, consistent with the BRD's own framing. None of it is a
  benchmark and none should be shown to a client as one.
- `duration_minutes` is populated only where a start/end genuinely exists (interviews,
  assessments, screening reviews — 13% of events). Everywhere else elapsed time is
  milestone-to-milestone and labelled as such. Do not compute "active time" as
  elapsed-minus-nothing.
- `waiting_reason_code` is recorded on 31% of events — specifically where a material wait
  (> 1 business day) occurred and the source would plausibly record a reason. It is left
  **empty** elsewhere rather than invented, per the brief.
- No candidate PII exists anywhere in the dataset: candidates are `CAND-######` surrogates
  and all actors are pseudonymous role-prefixed IDs.
- Detections in `ta_gap_detection.csv` are evidence-backed **hypotheses about process
  execution**, not established causes. GAP-14 in particular records that a withdrawal
  followed a long wait; it does not establish that the wait caused it.

---

# PostgreSQL pipeline and KPI engine

Same script names, conventions and `ta_` file prefix as the Corporate Loan prototype.

```bash
python 01_create_tables.py --drop          # build the talent_acquisition schema
python 02_load_master_data.py --truncate   # reference masters + the two case masters
python 03_load_event_log.py --truncate     # event log + derived analytical inputs
python 04_compute_kpis_to_postgres.py      # compute KPIs -> PostgreSQL + offline CSVs
```

```bash
python run_all.py --recreate               # all four steps in order
```

```bash
python run_all.py --offline                # KPIs from CSV to CSV, no database needed
```

Connection settings come from the usual environment variables; the schema defaults to
`talent_acquisition`:

```bash
export PGHOST=localhost PGPORT=5432 PGDATABASE=kinetic PGUSER=postgres PGPASSWORD=postgres
export PGSCHEMA=talent_acquisition
```

Requires `pip install psycopg2-binary pandas numpy`.

## Scripts

| Script | Role |
|---|---|
| `db_config.py` | Connection helper, `bulk_insert`, `truncate` — schema defaults to `talent_acquisition` |
| `schema.sql` | 22 tables + 6 views: reference masters, the two case masters, event log, derived inputs, KPI tables |
| `01_create_tables.py` | Executes `schema.sql` (`--drop` to rebuild) |
| `02_load_master_data.py` | Loads the reference masters, derives `persona_master` (the rate card), loads both case masters |
| `03_load_event_log.py` | Loads the 110k-row event log in chunks + the three derived tables, then runs 10 quality checks |
| `kpi_core.py` | **All KPI definitions.** No I/O — `compute_kpis(src, as_of)` returns the eight result frames |
| `04_compute_kpis_to_postgres.py` | Reads source, calls `kpi_core`, writes PostgreSQL **and** the offline CSVs |
| `run_all.py` | Runs the pipeline end to end |

## The eight KPI tables

Written on every run, each keyed by `run_id` so history is retained and Tier 1 can show a
trend arrow against the previous run.

| Table | Offline CSV | Rows | What it holds |
|---|---|---|---|
| `kpi_summary` | `ta_kpi_summary.csv` | 1 | The dashboard header: volumes, outcomes, all 12 headline values, health counts, top gap, worst stage |
| `kpi_tier1_health` | `ta_kpi_tier1_health.csv` | 12 | T1‑01..T1‑12 with target, variance, health, trend, **cohort size and the inclusion rule applied** |
| `kpi_tier2_diagnostics` | `ta_kpi_tier2_diagnostics.csv` | 26 | T2‑01..T2‑26 with the Tier 1 KPIs each explains and the GAP ids that cite it as evidence |
| `kpi_stage_breakdown` | `ta_kpi_stage_breakdown.csv` | 10 | Elapsed / waiting / effort / breach / revisit per stage, **split by case type** |
| `kpi_activity_breakdown` | `ta_kpi_activity_breakdown.csv` | 88 | Per-activity hotspots with waiting and effort ranks and the dominant waiting reason |
| `kpi_rework_loops` | `ta_kpi_rework_loops.csv` | 18 | Each rework loop: cases, events, business days lost, dominant reason, GAP id |
| `kpi_monthly_trend` | `ta_kpi_monthly_trend.csv` | 19 | Tier 1 KPIs by month |
| `kpi_stalled_cases` | `ta_kpi_stalled_cases.csv` | 237 | Open cases past policy, with the breached open SLA and a recommended action |

`kpi_run` is the control table those eight reference — one row per computation, carrying the
as-of timestamp, the period filter, the volumes and the full parameter set as JSONB.

Note the naming: `ta_kpi_tier1.csv` / `ta_kpi_tier2.csv` are the **BRD catalogues**
(definitions), loaded into `kpi_catalogue_tier1` / `kpi_catalogue_tier2`.
`ta_kpi_tier1_health.csv` / `ta_kpi_tier2_diagnostics.csv` are the **computed values**.

## Measurement rules the engine enforces

Straight from BRD section 6.4 — these are the decisions that make the numbers defensible:

- **Open cases are right-censored.** They never enter a completion KPI and are reported
  separately rather than counted as failures. T1‑01 uses 739 filled requisitions, not 1,200.
- **Every KPI records its own cohort.** `denominator_cases` and `cohort_rule` are stored on
  each row, so "4.9% withdrawal" is visibly *withdrawn / 7,034 applications past screening,
  excluding those closed by requisition cancellation* — not withdrawn over everything.
- **Active time is measured, never inferred.** Only 13% of events carry a reliable duration,
  so T1‑12 and T2‑25 state that coverage on the row and report *measured* effort. Waiting is
  milestone-to-milestone elapsed time, taken from the business-minute waits on the log (their
  running total telescopes exactly, so no calendar-to-business approximation is used).
- **SLA breach is per priority segment.** The engine reads the priority-specific target carried
  on each event, never a universal threshold.
- **The two case types are never pooled.** `kpi_stage_breakdown` carries `case_type`, because a
  requisition sitting in Sourcing while its pipeline runs (median 67 business days, 84% of the
  requisition cycle) is a different measurement from a candidate's stage dwell. Pooling them
  would misattribute the elapsed time — and requisition and application stages therefore get
  separate SLA profiles in `kpi_core.STAGE_SLA_DAYS`.
- **Container waits are flagged, not ranked.** The interval before `Requisition Filled` is the
  whole candidate pipeline, already measured event by event on the linked applications;
  the interval before `Candidate Joined` is the contractual notice period. Both keep their real
  numbers in `kpi_activity_breakdown` but carry `wait_is_container = Y` and are excluded from
  the waiting / effort ranking, so the hotspot list stays actionable.
- **A documented policy exception is conformant.** Governance KPIs only count a missing
  mandatory control as a deviation when no `Policy Exception Documented` event accompanies it.
- **Monthly trend states its bases.** Requisition metrics by opening month, application metrics
  by creation month, offer metrics by offer-sent month, event metrics by event month. They are
  never mixed inside one denominator.

## Current KPI position

| | Value | Target | Health |
|---|---|---|---|
| T1‑01 End-to-end time to fill | 81.3 days (P90 114.0) | 60 | **Critical** |
| T1‑02 Requisition approval lead time | 1.5 days | 3 | Healthy |
| T1‑03 Stage SLA attainment | 76.5% | 85 | Watch |
| T1‑04 Waiting-time share | 39.8% | 45 | Healthy |
| T1‑05 Candidate conversion (shortlist → interview) | 67.8% | 60 | Healthy |
| T1‑06 Offer acceptance | 82.5% | 90 | Watch |
| T1‑07 Offer-to-join | 93.5% | 95 | Watch |
| T1‑08 Candidate withdrawal | 4.9% | 5 | Healthy |
| T1‑09 Recruitment rework | 28.8% | 25 | Watch |
| T1‑10 Process conformance | 98.9% | 99 | Watch |
| T1‑11 Recruiter workload imbalance | 1.99× | 1.6 | **Critical** |
| T1‑12 Effort / cost per hire | $1,972 (9.8 measured hours) | — | Informational |

Six Tier 2 drivers come out Critical and they line up with the gap profile rather than
contradicting it: handoff waiting (T2‑03), SLA breach severity (T2‑07), accepted-offer
non-joining (T2‑16), repeated approvals (T2‑18), feedback lateness (T2‑19) and integration
failures (T2‑26). The top waiting hotspots after container exclusion are interview
coordination, the screening queue, hiring-manager shortlist review, interview feedback and
verification clearance — the same five places the GAP detections cluster.

## Verification status

- The KPI computation and the offline CSV path were **executed end to end** on the 110,686-event
  extract; all eight result sets were produced and cross-checked against the generator's own
  independent summary (time to fill 81.3, SLA attainment 76.5%, offer acceptance 82.5%,
  conformance 98.9% all agree).
- Every CSV header was verified column-by-column against its `schema.sql` table — all 20
  source and result files map exactly, so the loaders have no column drift.
- `schema.sql` was parsed with the PostgreSQL dialect (42 statements, no errors).
- **The PostgreSQL read/write path has not been run here** — this machine has no PostgreSQL
  server and no `psycopg2`. `db_config.py` and the `bulk_insert` pattern are carried over
  unchanged from the working Corporate Loan pipeline, but the first `run_all.py --recreate`
  against a live database is still the real test.

---

# Root Cause Agent and the Root Cause API

Same four-module layout and naming as the Corporate Loan prototype. The KPI engine decides
**what** is Critical; the agent decides **why**, and only from evidence the engine produced.

```
PostgreSQL KPI tables
        │
        ▼
get_root_cause_evidence()        ← the only tool the agent can call   (root_cause_tool.py)
        │
        ▼
GPT-5.1 + frozen system prompt   ← 33 sections, 21 validation rules   (root_cause_prompt.py)
        │
        ▼
strict JSON (text.format)        ← schema + post-run validator        (root_cause_schema.py)
        │
        ▼
ta_root_causes.json  +  root_cause_analysis / root_cause_finding      (root_cause_agent.py)
        │
        ▼
GET /api/root-cause/…            ← read-only HTTP surface   (backend/routers/root_cause.py)
```

## Running it

```bash
export OPENAI_API_KEY=...
python root_cause_agent.py --dry-run                      # build the evidence pack, call nothing
python root_cause_agent.py                                # latest KPI run, every Critical KPI
python root_cause_agent.py --kpi-ids T1-01 T1-11          # restrict the analysis
python root_cause_agent.py --evidence-out evidence.json   # keep the pack for audit
python root_cause_agent.py --evidence-file evidence.json  # re-run with no database in front
```

```bash
uvicorn backend.main:app --reload --port 8010             # then open /docs
```

`pip install -r backend/requirements.txt`. The API is read-only and needs no model credentials.

## The four modules

| Module | Role |
|---|---|
| `root_cause_tool.py` | The **single** tool the agent gets. Builds a fixed evidence package from the KPI tables — no free SQL access, so the agent cannot wander off the deterministic output. |
| `root_cause_prompt.py` | The frozen system prompt. Sent as `instructions` on every call, so the long prefix is prompt-cacheable; nothing dynamic is interpolated into it. |
| `root_cause_schema.py` | The strict `json_schema` the answer is constrained to, plus `validate_output()` for the rules a schema cannot express. |
| `root_cause_agent.py` | The tool loop: reasoning items are carried forward between turns, refusals and truncation are detected, the answer is validated, written to JSON and persisted. |

## What the evidence tool returns

Beyond the loan prototype's blocks (`critical_tier1`, `linked_tier2`, `stages`, `activities`,
`rework_loops`, `stalled`, `monthly_trend`), the Talent Acquisition pack adds four things the
BRD specifically calls for:

- **`gap_detections`** — how often each GAP actually fired against the event log, with the median
  and P90 measured value against the threshold and sample evidence summaries. A GAP's stated
  hypothesis is therefore testable: a compelling description with 12 detections is weak evidence,
  and the prompt requires the agent to say so.
- **`segments`** — the KPI position by business unit, job family, location, job level, priority,
  demand type, source channel and candidate engagement profile, with small segments suppressed.
  This is what lets a finding be "concentrated in Digital Engineering L5 roles" rather than an
  average.
- **`recruiter_load`** — the weighted-load distribution, the design bands and the most loaded
  recruiters next to their median time to fill and assignment lead time.
- **`case_metrics`** — requisition and application aggregates plus `withdrawal_by_stage` and
  `withdrawal_reasons`, so candidate exits can be located without touching candidate records.

There is no `kpi_root_cause_link` bridge table in this schema: Tier 2 carries its Tier 1 and GAP
links as columns, and the tool expands those into `root_cause_links` — the same expansion
`v_root_cause_tree` does in SQL.

## The guardrails that are enforced, not just requested

The prompt asks for these; `validate_output()` makes them fail loudly when they are broken.

- **Only Critical KPIs are analysed.** A Watch or Healthy KPI in the answer is a validation error,
  as is a Critical KPI that was skipped.
- **Every cited id must exist in the evidence package.** A hallucinated `T2-31` or `GAP-22` is
  caught by cross-checking the answer against the pack the tool actually returned.
- **No asserted causation on candidate outcomes.** This is the Talent Acquisition-specific rule.
  The BRD is explicit that a candidate withdrawing after a long wait does not establish that the
  wait caused it — and the dataset backs that up, since ~65% of candidates who waited just as long
  still joined. A finding that pairs withdrawal/decline language with "caused by", "proves that"
  or "directly caused" is rejected.
- **High confidence needs real backing.** A root cause claiming High confidence with neither Tier 2
  nor gap-detection evidence is rejected.
- **Every root cause needs quantitative statistics, an evidence chain, an alternative explanation
  and a stated limitation.** Rank 1..N with no gaps, and the frontend ranking must equal the
  detailed ranking.
- **Case-type denominators are mandatory.** Requisition-level and application-level rates are
  never pooled, and the prompt warns the agent not to present requisition sourcing dwell and
  candidate stage dwell as two separate causes — it is the same elapsed time.
- **No individual performance judgements.** Persona and recruiter evidence is for capacity and
  handoff patterns only.

## The API

Read-only. Producing an analysis is a long, billable, high-effort model run, so it stays in
`root_cause_agent.py` rather than behind a request.

| Endpoint | Returns |
|---|---|
| `GET /api/root-cause/health` | Service + data readiness: schema, latest KPI run, latest analysis |
| `GET /api/root-cause/latest` | The full stored analysis — metadata + `frontend_summary` + `tier1_root_causes` |
| `GET /api/root-cause/summary` | `frontend_summary` only — what a dashboard card needs |
| `GET /api/root-cause/kpis` | One row per Critical Tier 1 KPI with its root-cause and confidence counts |
| `GET /api/root-cause/kpis/{kpi_id}` | The ranked root causes of one Tier 1 KPI, e.g. `T1-01` |
| `GET /api/root-cause/findings` | Flat, filterable list: `?kpi_id=&gap=&confidence=&root_cause_type=&detail=` |
| `GET /api/root-cause/findings/{finding_id}` | One root cause with its full evidence, e.g. `RC-T1-01-2` |
| `GET /api/root-cause/gaps` | Which GAP ids the analysis cites, joined to the GAP library and the detected volume |
| `GET /api/root-cause/analyses` | The analysis history, newest first |
| `GET /api/root-cause/analyses/{id}` | One stored analysis |
| `GET /api/root-cause/analyses/{id}/evidence` | The evidence package it was grounded in — the audit trail for every statistic |

Every endpoint accepts `?run_id=` or `?analysis_id=` to address a specific KPI run or analysis;
without them the latest stored analysis is served.

**Finding ids are stable and addressable**: `RC-<tier1 kpi id>-<rank>`, e.g. `RC-T1-01-2`. They are
derived from the analysis rather than from a sequence, so a root cause keeps the same id across
re-runs of the same KPI run and the frontend can deep-link to it.

**Fallback file**: if `root_cause_analysis` holds no row — or the database is unreachable — the API
serves the agent's own `ta_root_causes.json` and marks the response `source: "file"`. That keeps
the endpoint useful immediately after an agent run that could not persist.

## Storage

`backend/schema_root_cause.sql` adds two tables (applied by `01_create_tables.py` and again on API
startup, so an existing database picks them up without a rebuild):

- **`root_cause_analysis`** — one row per agent run: model, reasoning effort, validation status and
  problems, the full `analysis_json`, and the `evidence_json` it was grounded in.
- **`root_cause_finding`** — one row per ranked root cause, flattened for querying: Tier 1 context,
  rank, type, confidence, GAP and Tier 2 ids, affected cases with the denominator described, and
  the statistics / evidence chain / alternatives / interventions as JSONB.

Plus `v_root_cause_latest` and `v_root_cause_findings_latest`.

## Verification status

- **Executed here**: the strict schema was checked for OpenAI strict-mode compliance (every object
  closed, `required` == `properties`, recursively); `validate_output()` was exercised against a
  well-formed answer (passes clean) and against a deliberately bad one, where it correctly caught
  an asserted causation on withdrawal, a hallucinated Tier 2 id, missing Tier 2 evidence and
  unjustified High confidence; `flatten_findings()` was unit-tested end to end and covers every
  storage column; the FastAPI app boots and all **11 endpoints register and appear in the OpenAPI
  schema**; the degraded path returns `503 database_unavailable` rather than a stack trace; and the
  file-fallback path was verified serving `/latest` and `/summary` with `source: "file"`.
- `backend/schema_root_cause.sql` parses clean under the PostgreSQL dialect (9 statements).
- **Not executed here**: the SQL in `root_cause_tool.py` and the store's read/write queries have
  not run against a live PostgreSQL (no server, no `psycopg2` on this machine), and the model call
  itself has not been made (no `OPENAI_API_KEY`). The first real test is
  `python root_cause_agent.py --dry-run` against a loaded database — that exercises every query in
  the evidence tool without spending a token.

---

# Frontend (React + Vite)

`frontend/` — same structure and naming as the Corporate Loan dashboard, but every screen reads
the Root Cause API instead of JSON files in `public/data/`.

```bash
uvicorn backend.main:app --reload --port 8010    # terminal 1
cd frontend && npm install && npm run dev        # terminal 2 -> http://localhost:5174
```

| File | Role |
|---|---|
| `src/App.jsx` | Routing: `/` and `/root-cause/:findingId` |
| `src/KpiDashboard.jsx` | Run header, Tier 1 tiles, root causes, gap coverage |
| `src/pages/RootCauseDetail.jsx` | One root cause with its full evidence (the loan app's `pages/FixWithAI.jsx` slot) |
| `src/api/client.js` | Every endpoint behind one `ApiError` type |
| `src/format.js` | Variance wording, trend glyphs, case-type labels |
| `src/dashboard.css` / `src/rootcause.css` | Same design tokens as the loan dashboard |

Each section loads and fails independently, which matters because the pipeline has a real
intermediate state: after `04_compute_kpis_to_postgres.py` but before `root_cause_agent.py` the
tiles are available and the analysis is not. See `frontend/README.md` for the full degraded-state
table.

**One endpoint was added for this**: `GET /api/root-cause/kpi-health`, which reads
`kpi_tier1_health` + `kpi_summary` directly. Without it the dashboard could only show the Critical
KPIs embedded in an analysis — no Watch/Healthy tiles, and nothing at all before the agent has run.

## Verification status

- **Executed here**: `vite build` compiles clean (29 modules). The dashboard and the detail route
  were both rendered in a browser against a mock API built from the real generated KPI CSVs, and
  the full DOM was read back: 12 Tier 1 tiles with correct health, values, variance wording and
  cohort lines; 2 Critical KPI sections with 5 ranked causes, statistics, Tier 2/GAP chips and
  correct `RC-T1-01-1`-style links; all 8 typed evidence tables, the limitations block and the
  interventions on the detail page; 4 gap-coverage cards.
- **Three real bugs were found by running it and fixed**: the client sent `Content-Type` on
  bodyless GETs, forcing a CORS preflight before every call; `base: "./"` broke deep links in the
  built app (`/root-cause/assets/…` 404); and `formatValue` rendered a 1.99 ratio as "2".
- **Not executed here**: the frontend has never run against the real FastAPI backend with a live
  PostgreSQL — only against the mock. The shapes it consumes are the ones the router returns, but
  the first end-to-end run is still the real test.

---

# Fix with AI — the four-part case analysis

Clicking **Fix with AI** on a root cause runs a deterministic analysis over the event log. Nothing
in this response is produced by a model: every figure is SQL, the case is chosen by a scoring
function, and the narrative is assembled from event rows.

```
finding_id (RC-T1-01-2)
   └─ fix_selectors.extract()            derive the event-log filter from the agent's own evidence
        ├─ (1) impact_service            impacted cases + what they cost
        ├─ (2) kpi_context_service       the KPIs those cases move, impacted vs unimpacted
        ├─ (3) representative_case       the most representative case, deterministically scored
        └─ (4) event_log_service         what happened in it: journey, key events, the facts
```

| Script | Answers |
|---|---|
| `backend/services/fix_selectors.py` | How a root cause becomes an event-log filter |
| `backend/services/impact_service.py` | **(1)** Which cases are impacted, and what it costs them |
| `backend/services/kpi_context_service.py` | **(2)** Which KPIs those cases move |
| `backend/services/representative_case.py` | **(3)** Which single case best represents the problem |
| `backend/services/event_log_service.py` | **(4)** What actually happened in that case |
| `backend/agents/fix_plan_orchestrator.py` | Composes 1–4 into one payload |
| `backend/routers/fix_with_ai.py` | The HTTP surface |

## Endpoints

| Endpoint | Returns |
|---|---|
| `GET /api/root-causes/{finding_id}/fix-with-ai` | All four parts in one payload |
| `GET /api/root-causes/{finding_id}/impact` | (1) impacted-case metrics |
| `GET /api/root-causes/{finding_id}/impacted-cases` | (1) the paged case list, worst waiting first |
| `GET /api/root-causes/{finding_id}/kpis` | (2) catalogue context + the cohort comparison |
| `GET /api/root-causes/{finding_id}/representative-case` | (3) the pick, plus the ranked candidates |
| `GET /api/root-causes/{finding_id}/what-happened` | (4) journey, key events and the narrative |
| `GET /api/cases/{case_id}/journey` | Any case's complete, unedited timeline |

The sub-resources let the UI render progressively; they call the same services, so the numbers
cannot diverge between them.

## Three decisions that differ from the Corporate Loan prototype

1. **Gaps resolve through `gap_detection`, not through a column on the event.** This event log
   deliberately leaves `event_log.gap_reference` empty — gaps are derived by the deterministic
   rules, not pre-labelled (BRD section 10). Matching on the event column, as the loan prototype
   does, would silently return zero cases here.
2. **`case_type` is part of the filter.** A root cause about candidate withdrawal is scored
   against applications; one about approval delay against requisitions. Counting one against the
   other produces a meaningless percentage, and the impact percentage has to stay comparable with
   the Tier 1 KPI it explains.
3. **Open cases are handled explicitly.** They are counted in the impacted population but excluded
   from completion measures, and excluded entirely from representative-case selection — a case
   still in flight has no finished journey to show.

## What the numbers mean

- **Attributable waiting** is the waiting on the *matching activities only*, never whole-case
  elapsed time. Quoting the latter would credit the root cause with delay it did not cause.
- **The cohort comparison is an association.** Impacted and unimpacted cases are not matched on
  role complexity, level, location or priority, and senior or overseas roles legitimately take
  longer. Every comparison carries that caveat in `interpretation_note`, and the API passes it
  through rather than dropping it.
- **The representative case is the typical one, not the worst one.** Candidates outside the
  P10–P90 elapsed-time band are dropped before scoring, and the remaining distance from the median
  is penalised heavily enough to outweigh evidence volume. The UI presents this case as typical,
  so picking the extreme would overstate the problem.
- **The narrative is assembled, not written.** Longest waits, rework loops, SLA breaches,
  exceptions, coordination volume and the ending are each produced from rows, with their numbers
  attached. Where the source recorded no waiting reason it says so rather than guessing.

## Verification status

- **Executed here**: all 46 SQL statements the four services generate were rendered and parsed
  under the PostgreSQL dialect; `fix_selectors.extract`, `representative_case.score_candidates`,
  `drop_outliers` and `event_log_service.what_happened` were unit-tested against synthetic data;
  all 7 new endpoints register and appear in the OpenAPI schema, and degrade to a clean
  `503 analysis_failed` with no database.
- **Two real bugs were found by that verification and fixed**: the cohort comparison applied
  `FILTER` to a composite expression, which PostgreSQL only permits on an aggregate call — it
  would have failed at runtime; and the representative-case scoring picked a 400-day outlier over
  a typical case, contradicting what the module promised.
- **Not executed here**: none of this has run against a live PostgreSQL. `--dry-run` equivalents
  do not exist for these services, so the first real test is calling
  `/api/root-causes/<id>/fix-with-ai` against a loaded database.

---

# Recommendation agents — deterministic Tier 1 × Tier 2 × Gap routing

`backend/agents/recommendation_agent.py` holds the whole mapping and the selection engine. No
model picks an agent id and no model computes a KPI; an LLM layer may later draft the *wording* of
a recommendation from the evidence supplied, which is what keeps the routing auditable.

```bash
python backend/agents/recommendation_agent.py --validate        # check the maps, no DB needed
python backend/agents/recommendation_agent.py --show-mapping    # print the full mapping
python backend/agents/recommendation_agent.py                   # route the latest KPI run
python backend/agents/recommendation_agent.py --finding-id RC-T1-01-1
```

## The 16 agents

Four domains, as specified: Recruitment Operations (`HR_REC_01/03/04/05/06/07/08/09`), Governance &
Controls (`HR_REC_02/10/14`), Candidate Experience (`HR_REC_11/12`), Process & Integration
(`HR_REC_13/15/16`). Each carries a name, responsibility and an accountable owner.

## Selection priority

| Priority | Route | Trigger |
|---|---|---|
| 1 | **Gap** → `GAP_AGENT_MAP` | the gap is *detected* in `gap_detection` — association in the library is not a trigger |
| 2 | **Tier 2** → `TIER2_AGENT_MAP` | the diagnostic is Critical or Watch, and no detected gap already explains it |
| 3 | **Tier 1** → `TIER1_AGENT_MAP` | only a headline deviation; the route directs investigation and explicitly records that a Tier 1 KPI alone never establishes a root cause |

Confidence is derived, not asserted: **High** when the gap is detected *and* a corroborating Tier 2
KPI is breaching *and* an impacted Tier 1 KPI is Critical; **Medium** when two of the three hold;
**Low** otherwise.

`"stage_specialist"` in the supporting lists resolves through `STAGE_AGENT_MAP` once the bottleneck
stage is known — so GAP‑14 on an interview-stage case asks `HR_REC_06` to investigate the delay
rather than naming a generic owner.

## Two documented departures from the supplied specification

1. **Section 4 is implemented, not the section 6 dict.** Section 4 gives a primary *and* a
   supporting agent for every gap; the illustrative `GAP_AGENT_MAP` in section 6 drops the
   supporting agent for GAP‑01 to GAP‑04. Taking section 6 literally would silently remove
   governance (`HR_REC_14`) from every approval-delay recommendation.
2. **GAP‑01 evidence is kept in both forms.** Section 5 lists `T2-17` as evidence for GAP‑01 while
   the BRD gap library records `T2-01`. `GAP_TIER2_MAP` holds the spec's routing evidence and
   `gap_mapping.tier2_evidence` keeps the library value where the KPI engine reads it. They answer
   different questions, so they are not merged.

## Integration

The mapping is wired into the Fix-with-AI flow as part (5), so one click returns impacted cases,
the KPIs they move, the representative case, what happened in it **and** who owns the fix:

| Endpoint | Returns |
|---|---|
| `GET /api/root-causes/{finding_id}/agents` | the agents for one root cause, with owner, intervention focus and the KPIs to re-measure |
| `GET /api/agents` | the full catalogue and every mapping table; `?run_id=` also routes that KPI run |

`GET /api/agents` needs no database — the mapping is configuration, so it can be reviewed and
diffed without a running pipeline.

## Verification status

- **Executed here**: the maps self-validate at import and the module refuses to load if they are
  inconsistent — all 16 agents reachable, 18 gaps, 12 Tier 1 and 26 Tier 2 KPIs covered, no unknown
  agent id. The engine was run against the real generated KPI run (T1‑01 and T1‑11 Critical, 6
  Tier 2 Critical, all 18 gaps detected): it produced 18 gap routes invoking 14 agents with
  confidence correctly graded. The specification's own worked example — T1‑01 Critical + T2‑06
  breached + GAP‑08 detected → `HR_REC_06` primary, `HR_REC_11` supporting — reproduces exactly.
  Both fallback levels were tested in isolation, as was gap-over-diagnostic precedence, the
  undetected-gap suppression, and stage-specialist resolution across all nine stages.
- **Not executed here**: `recommend_from_db` and the two endpoints have not run against a live
  PostgreSQL. The pure engine is fully exercised; only the SQL adapter around it is untested.

---

# To-Be State Workflow Designer

Takes one representative case's real activity sequence and redesigns it into a future-state
workflow, using **only** the agents `recommendation_agent.py` already selected.

```
recommendation_agent.py   ->  WHICH agents    (deterministic, version-controlled)
to_be_workflow_agent.py   ->  HOW they touch each real activity of this case
compute_metrics()         ->  every number on the screen   (pure Python, no model)
```

| File | Role |
|---|---|
| `backend/to_be_schemas.py` | The contract: Pydantic + a strict JSON schema for the Responses API |
| `backend/agents/to_be_workflow_agent.py` | The designer, with the content guardrails |
| `backend/agents/to_be_workflow_orchestrator.py` | Context assembly + `compute_metrics()` |
| `GET /api/root-causes/{finding_id}/to-be-workflow` | The design and its metrics |
| `GET /api/to-be-workflow/context/{finding_id}` | The designer's evidence pack — audit view, no model call |

## The four inputs, and nothing else

1. **The recommended agents** — primary and supporting, from `recommend_for_finding()`. The
   designer may not select, invent or rename an agent; `responsible_agent` must match one of these
   names exactly or be null.
2. **The representative case's activity list** — every row of its real event log, with waiting
   time, waiting reason, and the manual / rework / exception / handoff / SLA-breach flags.
3. **Tier 1 KPI and Tier 2 KPIs** — from the KPI run.
4. **The root cause** — the stored finding, including its stated limitations.

## Outputs

| Output | Meaning |
|---|---|
| `to_be_activities[]` | The to-be state list. Rows sharing a `to_be_sequence` are one step; eliminated rows produce none |
| `metrics.activities_automated` | AI Agent + Automated (Deterministic) steps |
| `metrics.activities_reduced` | Current count minus to-be count — the net effect of eliminations and consolidations |
| `metrics.ai_agent_activities` | Steps performed by a recommended agent |
| `metrics.human_activities` | Steps redesigned to stay with a person (change type Human) |
| `metrics.activities_continuing_as_is` | Steps unchanged from today (change type Retained) |

Plus `breakdown` (the same thing as named lists), `comparison` (before/after), and
`counts_reconcile` — a self-check that the dispositions sum to the to-be step count.

One counting rule worth knowing: `human_in_the_loop` is a flag on top of any disposition, so an AI
Agent step under human control is counted as **both** automated and a human touchpoint. Those two
counts deliberately overlap; `human_activities` and `activities_continuing_as_is` do not.

## Guardrails enforced in code, not just asked for in the prompt

| Guardrail | Behaviour |
|---|---|
| Invented agent | Rejected and sent back for correction |
| **Eliminating a mandatory control** | Rejected — budget validation, requisition approval, offer approval, verification clearance, the selection decision and the candidate's own decisions may not be designed away |
| Missing or duplicated current-state row | Rejected — exactly one design row per real activity |
| Invented system | Rejected once, then stripped rather than failing the run |
| Candidate-facing step without human control | `human_in_the_loop` forced on, and the correction reported |
| `responsible_agent` on a non-agent step | Cleared |
| Two bad answers in a row | Raises `ToBeError` rather than returning something wrong |

The mandatory-control rule is the one with no equivalent in the Corporate Loan version, and it
matters most: a design that removes an approval to save a step is recommending exactly the
governance deviation GAP‑18 exists to flag. The rule is "make the control faster, not absent".

## Verification status

- **Executed here**: `compute_metrics()` was run against a realistic 12-activity interview journey
  ending in a withdrawal — 12 activities → 9, 3 reduced, 2 AI agent, 2 deterministic, 2 human,
  3 continuing as-is, counts reconciling, follow-ups 3 → 0 and rework 1 → 0. All seven guardrails
  were tested with a stub model client: each rejection fires with the right message, the retry
  recovers, and two bad answers raise rather than return. The strict schema passes OpenAI
  strict-mode compliance recursively. Both endpoints register and degrade cleanly.
- **Not executed here**: no real model call and no live PostgreSQL. `build_context()` and `run()`
  are untested end to end; the design step and all the counting are fully exercised.

---

# Fix with AI screen

`frontend/src/pages/FixWithAI.jsx`, route `/root-cause/:findingId/fix-with-ai`, reached from the
**Fix with AI** button on every root cause on the dashboard. Six sections: impacted cases, the
KPIs associated with them, the most representative case, what happened in it, the recommended
agents, and the To-Be state workflow.

Sections 1–5 render on page load from the one deterministic endpoint. Two parts are lazy by
design: the impacted-case table (a long table nobody needs until they ask) and the To-Be workflow
(it calls a model and takes minutes — firing a billable generation on every page view would be
the wrong default).

## Verification status

- **Executed here**: `vite build` compiles clean (31 modules). The screen was rendered in a
  browser against a mock of the real endpoint shapes and the DOM read back: all six sections
  present with no errors, the 1,061-case headline and nine impact metrics, the Tier 2 table, the
  8-row cohort comparison, the representative case with its selection reason, the 9-step narrative
  and key-event timeline, the primary and supporting agent cards. Both lazy loads were clicked and
  verified — 25 impacted cases in the table, and the To-Be section returning all six headline
  counts (8 activities from 12, 4 reduced, 4 automated, 2 AI agent, 2 human, 4 as-is) with its
  8 future-state steps and 3 eliminated activities.
- **Not executed here**: the screen has never run against the real backend — only against the
  mock. The shapes it consumes are the ones the services return, but the first end-to-end run with
  a live PostgreSQL and a real model call is still the real test.
