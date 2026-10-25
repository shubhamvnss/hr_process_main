"""
kpi_core - shared Tier 1 / Tier 2 KPI computation for the Talent Acquisition process
           (Deloitte Kinetic prototype).

This module contains NO input/output logic. compute_kpis(src, as_of) takes the already-read
source frames - from PostgreSQL via 04_compute_kpis_to_postgres.py, or from the CSV extract
in offline mode - and returns a dict of result DataFrames, so every caller shares exactly
the same KPI definitions:

    summary        - one row: the dashboard header for this run
    tier1          - Tier 1 KPI health overview (12 outcome KPIs, BRD section 8)
    tier2          - Tier 2 diagnostic KPIs (26 driver KPIs, BRD section 9)
    stages         - elapsed time, waiting, effort and SLA breach per recruitment stage
    activities     - per-activity volume, duration, waiting, exception and rework profile
    rework_loops   - rework loop profile: cases affected and business days lost per loop
    stalled        - open cases whose current wait breaches policy + recommended action
    monthly_trend  - Tier 1 KPIs by requisition opening month
    run_stats      - counts for the kpi_run control record

Required source frames (keys of `src`):
    events, requisitions, applications, stage_transitions, recruiter_load, gap_detection,
    gap_mapping (optional), sla_definitions (optional)

Measurement rules enforced here, straight from BRD section 6.4
  * Open cases are right-censored: they never contribute to a completion KPI, and they are
    reported separately instead of being counted as failures.
  * Waiting time is milestone-to-milestone elapsed time. Active processing time is only used
    where a reliable duration exists, and its coverage is disclosed on every effort KPI.
  * SLA breach is evaluated against the priority-specific target carried on the event, never
    against a universal threshold.
  * Every KPI records the cohort size and the inclusion rule actually applied.

Tuning parameters (targets, stage SLAs, the persona rate card, the stalled threshold) are the
module-level constants below.
"""

import numpy as np
import pandas as pd

# ------------------------------------------------------------------ configuration
BUSINESS_MIN_PER_DAY = 510          # 09:00-17:30 working day, per the BRD design assumption

# Illustrative prototype targets. The BRD is explicit that the client must approve these.
TARGETS = {
    "T1-01": 60.0,    # time to fill, business days (approved requisition -> joining)
    "T1-02": 3.0,     # requisition approval lead time, business days
    "T1-03": 85.0,    # stage SLA attainment, %
    "T1-04": 45.0,    # waiting-time share, %
    "T1-05": 60.0,    # candidate conversion, shortlist review -> interview, %
    "T1-06": 90.0,    # offer acceptance, %
    "T1-07": 95.0,    # offer-to-join conversion, %
    "T1-08": 5.0,     # candidate withdrawal rate, %
    "T1-09": 25.0,    # recruitment rework rate, %
    "T1-10": 99.0,    # process conformance, %
    "T1-11": 1.6,     # recruiter workload imbalance index (P90 / median weighted load)
    "T1-12": None,    # recruitment effort per hire - informational until effort data is approved
}

# Elapsed-day SLA per recruitment stage, PER CASE TYPE. The two case types occupy the same
# stage codes with different meaning: a requisition sits in "Sourcing" for as long as it is
# open with a live pipeline, while a candidate's stage dwell is their own progression time.
# One shared threshold for both would label normal open-requisition dwell as a breach.
STAGE_SLA_DAYS = {
    "REQUISITION": {"S1": 4.0, "S2": 4.0, "S3": 4.0, "S4": 45.0, "S5": 7.0,
                    "S6": 15.0, "S7": 4.0, "S8": 10.0, "S9": 3.0, "S0": 1.0},
    "APPLICATION": {"S1": 4.0, "S2": 4.0, "S3": 4.0, "S4": 10.0, "S5": 7.0,
                    "S6": 15.0, "S7": 4.0, "S8": 10.0, "S9": 20.0, "S0": 1.0},
}
STAGE_BREACH_WATCH_PCT = 20.0        # a stage breaching more often than this cannot read Healthy


def stage_sla(case_type, stage_code):
    return STAGE_SLA_DAYS.get(case_type, STAGE_SLA_DAYS["APPLICATION"]).get(stage_code)
STALLED_THRESHOLD_DAYS = 10         # an open case idle longer than this is a Kinetic exception

# Loaded hourly cost per persona (USD) - used for recruitment effort / cost per hire.
# 02_load_master_data.py writes this into persona_master so it is maintained in the DB.
PERSONA_RATE_PER_HOUR = {
    "Business Manager": 90, "Workforce Planning Analyst": 55, "Hiring Manager": 85,
    "Approving Business Leader": 140, "TA Operations Coordinator": 35, "Recruiter": 45,
    "Sourcer": 35, "Recruiting Coordinator": 32, "Interview Panel": 80,
    "Assessment Coordinator": 35, "Compensation Analyst": 60,
    "Compensation Approver": 120, "HR Operations": 38, "Candidate": 0,
    "System / Integration": 0,
}
AGENCY_FEE_PER_HIRE = 9_000         # allocated when an agency was engaged on the requisition
ASSESSMENT_COST_PER_EVENT = 45      # per assessment assigned

# Activities that do not move a case forward. Excluded from inactivity measurement so a chase
# e-mail cannot disguise a long wait, and counted separately as coordination effort (T2-24).
FOLLOW_UP_ACTIVITIES = {
    "Workforce Budget Validation Reminder Sent", "Requisition Approval Reminder Sent",
    "Recruiter Assignment Follow-up", "Screening Follow-up Logged",
    "Shortlist Review Reminder Sent", "Assessment Reminder Sent",
    "Interview Scheduling Follow-up", "Interview Feedback Reminder Sent",
    "Selection Decision Reminder Sent", "Offer Approval Reminder Sent",
    "Offer Follow-up Logged", "Integration Sync Retried",
}

# Returns to an already-completed activity or stage. The paired resubmit / re-confirm event is
# deliberately excluded so one return cycle counts as exactly one loop (BRD 6.4).
REWORK_LOOP_GAP = {
    "Hiring Demand Returned for Clarification": "GAP-01",
    "Requisition Returned for Clarification": "GAP-03",
    "Requisition Reopened": "GAP-18",
    "Recruiter Reassigned": "GAP-04;GAP-17",
    "Job Description Revised": "GAP-05",
    "Sourcing Campaign Extended": "GAP-05",
    "Additional Profiles Requested": "GAP-06",
    "Application Rescreened": "GAP-07",
    "Assessment Reissued": "GAP-10",
    "Interview Rescheduled": "GAP-08",
    "Interview Feedback Returned for Completion": "GAP-09",
    "Additional Interview Round Requested": "GAP-11",
    "Selection Decision Reopened": "GAP-11",
    "Compensation Proposal Revised": "GAP-12",
    "Offer Approval Returned for Revision": "GAP-12",
    "Offer Negotiation Requested": "GAP-13",
    "Document Resubmission Requested": "GAP-15",
    "Manual Status Re-entry": "GAP-16",
}
REWORK_LOOP_ACTIVITIES = set(REWORK_LOOP_GAP)

# Mandatory controls for the conformance / governance KPIs. A deviation only counts when the
# trace carries no documented policy exception.
MANDATORY_CONTROLS = [
    ("REQUISITION", "Recruiter Assigned", "Requisition Approved",
     "Recruiter assigned without an evidenced requisition approval"),
    ("REQUISITION", "Requisition Approved", "Workforce Budget Validated",
     "Requisition approved without an evidenced budget validation"),
    ("APPLICATION", "Offer Sent to Candidate", "Offer Approved",
     "Offer sent without an evidenced offer approval"),
    ("APPLICATION", "Candidate Joined", "Background Verification Cleared",
     "Candidate joined without an evidenced verification clearance"),
]
POLICY_EXCEPTION = "Policy Exception Documented"

# Activities whose PRECEDING interval is not an actionable process wait:
#   * case-closing events on the requisition log - that interval is the candidate pipeline,
#     already measured event by event on the linked application cases (counting it again on
#     the requisition would double-count the same elapsed time);
#   * Candidate Joined - the gap before it is the contractual notice period, outside the
#     recruiting team's control.
# They keep their real measured numbers in kpi_activity_breakdown but are excluded from the
# waiting / effort ranking so the hotspot list stays actionable.
CONTAINER_WAIT_ACTIVITIES = {
    "Requisition Filled", "Requisition Cancelled", "Requisition Closed Without Hire",
    "Application Closed", "Candidate Joined",
}

STAGE_NAMES = {
    "S1": "1. Hiring Demand", "S2": "2. Requisition & Approval",
    "S3": "3. Recruitment Initiation", "S4": "4. Sourcing",
    "S5": "5. Screening & Shortlisting", "S6": "6. Interview & Assessment",
    "S7": "7. Selection & Decision", "S8": "8. Offer Management",
    "S9": "9. Pre-joining & Closure", "S0": "Cross-stage (System / Governance)",
}

# Terminal application activities - used to decide whether an open interval is still running
TERMINAL_APP_ACTIVITIES = {
    "Candidate Withdrew", "Candidate Rejected", "Offer Declined", "Offer Expired",
    "Candidate Did Not Join", "Application Closed", "Candidate Joined",
}


# ------------------------------------------------------------------ helpers
def pct(num, den):
    return round(100.0 * num / den, 1) if den else np.nan


def bdays(minutes):
    return minutes / BUSINESS_MIN_PER_DAY


def health(value, target, direction, watch_band=0.20):
    """Kinetic legend: Critical = worse than target by >20%, Watch = within +/-20%, Healthy = at/better."""
    if target is None or value is None or (isinstance(value, float) and np.isnan(value)):
        return "N/A" if target is not None else "Informational"
    if direction == "lower":
        if value <= target:
            return "Healthy"
        return "Critical" if value > target * (1 + watch_band) else "Watch"
    if value >= target:
        return "Healthy"
    return "Critical" if value < target * (1 - watch_band) else "Watch"


def variance_pct(value, target, direction):
    if target in (None, 0) or value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    v = (value - target) / target * 100
    return round(v if direction == "lower" else -v, 1)      # positive = worse than target


def med(series):
    s = pd.to_numeric(pd.Series(series), errors="coerce").dropna()
    return float(s.median()) if len(s) else np.nan


def p90(series):
    s = pd.to_numeric(pd.Series(series), errors="coerce").dropna()
    return float(s.quantile(0.90)) if len(s) else np.nan


def mean_(series):
    s = pd.to_numeric(pd.Series(series), errors="coerce").dropna()
    return float(s.mean()) if len(s) else np.nan


def kpi(rows, kid, name, formula, value, target, unit, direction,
        feeds="", gaps="", n=None, cohort="", drivers=""):
    """Append one KPI row. `n` is the cohort size and `cohort` the inclusion rule applied."""
    val = None if value is None or (isinstance(value, float) and np.isnan(value)) else round(float(value), 2)
    rows.append({
        "kpi_id": kid, "kpi_name": name, "formula": formula,
        "current_value": val, "target": target, "unit": unit, "better_when": direction,
        "variance_vs_target_pct": variance_pct(val, target, direction),
        "health": health(val, target, direction),
        "feeds_tier1_kpi": feeds, "gap_reference": gaps,
        "denominator_cases": int(n) if n is not None else None,
        "cohort_rule": cohort, "tier2_drivers": drivers,
    })


def coerce_numeric(df, skip=()):
    """Convert genuinely numeric columns of a CSV-read frame to numbers.

    A column is converted only when the conversion introduces no new missing values, so
    identifiers ("REQ-10001"), Y/N flags and timestamps are left untouched.
    """
    for c in df.columns:
        if c in skip or df[c].dtype.kind in "ifb":
            continue
        conv = pd.to_numeric(df[c], errors="coerce")
        if conv.isna().sum() == df[c].isna().sum():
            df[c] = conv
    return df


def interval_bdays(ev, from_activity, to_activity, anchor_from="last", anchor_to="last"):
    """Business days between two activities within each case.

    waiting_time_minutes holds the business minutes since the previous event of the same case,
    so its running total telescopes exactly: the business minutes between any two events are
    the difference of their cumulative waits. No calendar-to-business approximation is used.
    """
    f = ev[ev["activity_name"] == from_activity]
    t = ev[ev["activity_name"] == to_activity]
    if f.empty or t.empty:
        return pd.Series(dtype=float)
    agg_f, agg_t = ("min" if anchor_from == "first" else "max"), ("min" if anchor_to == "first" else "max")
    a = f.groupby("process_instance_id").agg(a_cw=("cw", agg_f), a_ts=("ts", agg_f))
    b = t.groupby("process_instance_id").agg(b_cw=("cw", agg_t), b_ts=("ts", agg_t))
    j = a.join(b, how="inner").dropna()
    j = j[j["b_ts"] >= j["a_ts"]]
    return ((j["b_cw"] - j["a_cw"]) / BUSINESS_MIN_PER_DAY).rename("days")


def open_wait_bdays(ev, from_activity, to_activity, as_of_cw):
    """Business days a still-running interval has been open at the cut-off.

    Only cases where the opening activity is evidenced and the closing one is not.
    """
    f = ev[ev["activity_name"] == from_activity]
    if f.empty:
        return pd.Series(dtype=float)
    closed = set(ev.loc[ev["activity_name"] == to_activity, "process_instance_id"])
    a = f[~f["process_instance_id"].isin(closed)].groupby("process_instance_id")["cw"].max()
    if a.empty:
        return pd.Series(dtype=float)
    end = a.index.map(as_of_cw)
    return pd.Series((np.asarray(end, dtype=float) - a.to_numpy()) / BUSINESS_MIN_PER_DAY,
                     index=a.index, name="days").dropna()


def busdays_between(start, end):
    """Weekday count between two timestamps (public holidays are not subtracted here).

    `end` may be a single timestamp. Missing values on either side return NaN rather than 0,
    so an unmeasurable interval is excluded instead of being counted as same-day.
    """
    s = pd.to_datetime(pd.Series(start).reset_index(drop=True), errors="coerce")
    if isinstance(end, (str, pd.Timestamp)) or np.isscalar(end):
        e = pd.Series([pd.Timestamp(end)] * len(s))
    else:
        e = pd.to_datetime(pd.Series(end).reset_index(drop=True), errors="coerce")
    out = np.full(len(s), np.nan)
    ok = (~s.isna() & ~e.isna()).to_numpy()
    if ok.any():
        out[ok] = np.busday_count(s[ok].to_numpy("datetime64[D]"),
                                  e[ok].to_numpy("datetime64[D]"))
    return out


def sla_days(ev, sla_code, closing_activity=None):
    """Measured business days for an SLA interval, taken from the event-carried measure."""
    s = ev[ev["sla_code"] == sla_code]
    if closing_activity:
        s = s[s["activity_name"] == closing_activity]
    if s.empty:
        return pd.Series(dtype=float)
    last = s.sort_values("ts").groupby("process_instance_id")["sla_measured_minutes"].last()
    return (last / BUSINESS_MIN_PER_DAY).rename("days")


def case_has(ev, activity):
    """Set of process_instance_ids where the activity is evidenced."""
    return set(ev.loc[ev["activity_name"] == activity, "process_instance_id"])


def count_by_case(ev, activity):
    return ev[ev["activity_name"] == activity].groupby("process_instance_id").size()


# ------------------------------------------------------------------ main entry point
def compute_kpis(src, as_of):
    """Compute every Tier 1 / Tier 2 KPI and supporting root-cause table."""
    as_of = pd.Timestamp(as_of)
    ev = src["events"].copy()
    rq = src["requisitions"].copy()
    ap = src["applications"].copy()
    st = src["stage_transitions"].copy()
    ld = src["recruiter_load"].copy()
    gd = src.get("gap_detection", pd.DataFrame()).copy()
    gmap = src.get("gap_mapping", pd.DataFrame()).copy()
    slad = src.get("sla_definitions", pd.DataFrame()).copy()

    # ---- normalise types -------------------------------------------------
    ev["ts"] = pd.to_datetime(ev["event_timestamp"])
    for c in ["duration_minutes", "waiting_time_minutes", "sla_target_minutes",
              "sla_measured_minutes", "sla_overrun_minutes", "repeat_sequence_number"]:
        ev[c] = pd.to_numeric(ev.get(c), errors="coerce")
    for c in ["sla_breach_flag", "rework_flag", "manual_touch_flag", "handoff_flag",
              "exception_flag", "data_quality_flag"]:
        ev[c] = ev[c].fillna("N").astype(str)
    for c in ["sla_code", "waiting_reason_code", "reason_code", "outcome_code", "application_id"]:
        ev[c] = ev[c].fillna("").astype(str)
    ev = ev.sort_values(["process_instance_id", "event_timestamp", "event_id"])
    ev["wait_min"] = ev["waiting_time_minutes"].fillna(0)
    ev["cw"] = ev.groupby("process_instance_id")["wait_min"].cumsum()
    # minutes that sit inside an interval with a recorded waiting reason
    ev["recorded_wait_min"] = ev["wait_min"].where(ev["waiting_reason_code"].ne(""), 0)
    ev["is_manual"] = ev["manual_touch_flag"].eq("Y")
    ev["is_rework"] = ev["rework_flag"].eq("Y")
    ev["is_exc"] = ev["exception_flag"].eq("Y")
    ev["is_handoff"] = ev["handoff_flag"].eq("Y")
    ev["is_breach"] = ev["sla_breach_flag"].eq("Y")
    ev["is_follow_up"] = ev["activity_name"].isin(FOLLOW_UP_ACTIVITIES)
    ev["month"] = ev["ts"].dt.to_period("M").astype(str)

    TS_COLS = [c for c in set(rq.columns) | set(ap.columns) | set(st.columns)
               if c.endswith("_timestamp")]
    for frame in (rq, ap, st, ld):
        coerce_numeric(frame, skip=TS_COLS + ["snapshot_date", "detected_at"])
    for c in [c for c in rq.columns if c.endswith("_timestamp")]:
        rq[c] = pd.to_datetime(rq[c], errors="coerce")
    for c in [c for c in ap.columns if c.endswith("_timestamp")]:
        ap[c] = pd.to_datetime(ap[c], errors="coerce")
    for c in [c for c in st.columns if c.endswith("_timestamp")]:
        st[c] = pd.to_datetime(st[c], errors="coerce")
    if not ld.empty:
        ld["snapshot_date"] = pd.to_datetime(ld["snapshot_date"], errors="coerce")
    if not gd.empty:
        coerce_numeric(gd, skip=["detected_at"])

    as_of_cw = ev.groupby("process_instance_id")["cw"].max().to_dict()

    # ---- cohorts ---------------------------------------------------------
    rq_open = rq[rq["is_open_censored"].eq("Y")]
    rq_closed = rq[rq["is_open_censored"].eq("N")]
    filled = rq[rq["requisition_outcome"].eq("Filled")]
    cancelled = rq[rq["requisition_outcome"].eq("Cancelled")]
    nohire = rq[rq["requisition_outcome"].eq("Closed Without Hire")]
    ap_open = ap[ap["is_open_censored"].eq("Y")]
    ap_closed = ap[ap["is_open_censored"].eq("N")]
    joined = ap[ap["application_outcome"].eq("Joined")]
    withdrawn = ap[ap["application_outcome"].eq("Withdrawn")]
    offers = ap[ap["offer_made_flag"].eq("Y")]
    offers_responded = ap[ap["offer_acceptance_flag"].isin(["Y", "N"])]
    accepted = ap[ap["offer_acceptance_flag"].eq("Y")]
    # offer-to-join must only use accepted offers that have had time to reach a joining date
    accepted_mature = accepted[accepted["joining_flag"].isin(["Y", "N"])]
    # withdrawal denominator: applications that actually entered the managed process and were
    # not closed because the requisition itself was cancelled
    wd_eligible = ap[ap["screening_decision_timestamp"].notna()
                     & ~ap["application_outcome"].eq("Closed - Requisition Cancelled")]

    n_rq, n_ap = len(rq), len(ap)

    # ================================================================ TIER 1
    t1 = []
    ttf = filled["time_to_fill_business_days"].dropna()
    kpi(t1, "T1-01", "End-to-end Time to Fill",
        "MEDIAN(business days from Requisition Approved to Candidate Joined) over filled requisitions",
        med(ttf), TARGETS["T1-01"], "days", "lower", n=len(ttf),
        cohort="Filled requisitions with an evidenced approval and joining timestamp; open cases excluded",
        drivers="T2-01, T2-02, T2-03, T2-04, T2-05, T2-06")

    appr = rq["days_to_approval"].dropna()
    kpi(t1, "T1-02", "Requisition Approval Lead Time",
        "MEDIAN(business days from first Requisition Submitted to Requisition Approved)",
        med(appr), TARGETS["T1-02"], "days", "lower", n=len(appr),
        cohort="Requisitions with both a submission and a final approval event",
        drivers="T2-01, T2-02, T2-07")

    elig = int(ev["sla_code"].ne("").sum())
    brch = int(ev["is_breach"].sum())
    kpi(t1, "T1-03", "Recruitment Stage SLA Attainment",
        "COUNT(measured SLA intervals within target) / COUNT(all measured SLA intervals)",
        pct(elig - brch, elig), TARGETS["T1-03"], "%", "higher", n=elig,
        cohort="Completed SLA intervals only, each measured against its own priority-specific target",
        drivers="T2-02, T2-03, T2-04, T2-05, T2-06, T2-07")

    comp = pd.concat([rq_closed[["recorded_wait_business_days", "total_elapsed_business_minutes"]],
                      ap_closed[["recorded_wait_business_days", "total_elapsed_business_minutes"]]])
    wshare = pct((comp["recorded_wait_business_days"] * BUSINESS_MIN_PER_DAY).sum(),
                 comp["total_elapsed_business_minutes"].sum())
    kpi(t1, "T1-04", "Waiting-time Share",
        "SUM(minutes in intervals with a recorded waiting reason) / SUM(case elapsed business minutes)",
        wshare, TARGETS["T1-04"], "%", "lower", n=len(comp),
        cohort="Completed cases. Milestone-to-milestone elapsed time - active time is not inferred",
        drivers="T2-03, T2-04, T2-05, T2-06, T2-07")

    conv_den = ap[ap["hm_review_decision_timestamp"].notna()]
    conv_num = conv_den[conv_den["first_interview_timestamp"].notna()]
    kpi(t1, "T1-05", "Candidate Conversion Rate (shortlist review to interview)",
        "COUNT(applications with an interview conducted) / COUNT(applications with a shortlist decision)",
        pct(len(conv_num), len(conv_den)), TARGETS["T1-05"], "%", "higher", n=len(conv_den),
        cohort="Applications whose shortlist review reached a decision; still-pending reviews excluded",
        drivers="T2-08, T2-09, T2-10, T2-11")

    kpi(t1, "T1-06", "Offer Acceptance Rate",
        "COUNT(offers accepted) / COUNT(offers with a final response)",
        pct(len(accepted), len(offers_responded)), TARGETS["T1-06"], "%", "higher",
        n=len(offers_responded),
        cohort=f"Offers with a final response. Pending ({len(offers) - len(offers_responded)}) and "
               f"expired offers are reported separately",
        drivers="T2-12, T2-13, T2-14")

    kpi(t1, "T1-07", "Offer-to-Join Conversion",
        "COUNT(candidates joined) / COUNT(accepted offers that reached a joining decision)",
        pct(int(accepted_mature["joining_flag"].eq("Y").sum()), len(accepted_mature)),
        TARGETS["T1-07"], "%", "higher", n=len(accepted_mature),
        cohort="Accepted offers with an evidenced joining or non-joining outcome (mature cohort only)",
        drivers="T2-14, T2-15, T2-16")

    kpi(t1, "T1-08", "Candidate Withdrawal Rate",
        "COUNT(withdrawn applications) / COUNT(applications that reached a screening decision)",
        pct(int(wd_eligible["application_outcome"].eq("Withdrawn").sum()), len(wd_eligible)),
        TARGETS["T1-08"], "%", "lower", n=len(wd_eligible),
        cohort="Applications past screening, excluding those closed by requisition cancellation",
        drivers="T2-08, T2-10, T2-13, T2-14, T2-15")

    rw_den = pd.concat([rq_closed[["rework_loop_count"]], ap_closed[["rework_loop_count"]]])
    kpi(t1, "T1-09", "Recruitment Rework Rate",
        "COUNT(cases with >=1 rework loop) / COUNT(completed cases)",
        pct(int((rw_den["rework_loop_count"] > 0).sum()), len(rw_den)),
        TARGETS["T1-09"], "%", "lower", n=len(rw_den),
        cohort="Completed cases. A rework loop is a return to an already-completed activity or stage",
        drivers="T2-17, T2-18, T2-19")

    deviations, control_rows = _conformance(ev)
    conf_den = len(set(ev["process_instance_id"]))
    kpi(t1, "T1-10", "Process Conformance Rate",
        "COUNT(cases with every mandatory control evidenced) / COUNT(cases)",
        pct(conf_den - len(deviations), conf_den), TARGETS["T1-10"], "%", "higher", n=conf_den,
        cohort="All cases. A documented policy exception is treated as conformant, not as a breach",
        drivers="T2-18, T2-19, T2-20, T2-21")

    load_med, load_p90 = med(ld["weighted_load"]), p90(ld["weighted_load"])
    imbalance = (load_p90 / load_med) if load_med else np.nan
    kpi(t1, "T1-11", "Recruiter Workload Imbalance Index",
        "P90(weighted open requisition load) / MEDIAN(weighted open requisition load) across weekly snapshots",
        imbalance, TARGETS["T1-11"], "ratio", "lower", n=len(ld),
        cohort="Weekly recruiter snapshots. Balanced load is the goal, not a minimised load",
        drivers="T2-22, T2-23, T2-24")

    effort, cost, coverage = _effort_per_hire(ev, rq, filled, joined)
    kpi(t1, "T1-12", "Recruitment Effort / Cost per Hire",
        "SUM(measured persona hours x rate + agency + assessment cost) / COUNT(hires)",
        cost, TARGETS["T1-12"], "USD", "lower", n=len(joined),
        cohort=f"Hires on filled requisitions. Only {coverage}% of events carry a reliable duration, "
               f"so this is measured effort, not total effort ({effort} hours per hire)",
        drivers="T2-22, T2-23, T2-24, T2-25, T2-26")

    tier1 = pd.DataFrame(t1)

    # ================================================================ TIER 2
    t2 = []
    g = _gap_index(gmap)                      # tier2 kpi_id -> "GAP-xx;GAP-yy", inverted from the library
    evr = ev[ev["case_type"].eq("REQUISITION")]
    eva = ev[ev["case_type"].eq("APPLICATION")]

    # -- approval and intake (GAP-01, GAP-02, GAP-03)
    first_action = evr[evr["activity_name"].isin(
        ["Requisition Returned for Clarification", "Requisition Approved", "Requisition Rejected"])]
    qa = evr[evr["activity_name"].eq("Requisition Submitted for Approval")] \
        .groupby("process_instance_id")["cw"].min()
    qb = first_action.groupby("process_instance_id")["cw"].min()
    queue = ((qb - qa).dropna() / BUSINESS_MIN_PER_DAY)
    queue = queue[queue >= 0]
    kpi(t2, "T2-01", "Approval queue time",
        "MEDIAN(business days from requisition submission to the first approver action)",
        med(queue), 2.0, "days", "lower", "T1-01, T1-02", g["T2-01"], len(queue),
        "Requisitions with a submission and at least one approver action")

    stage_el = st["elapsed_business_days"].dropna()
    kpi(t2, "T2-02", "Stage elapsed time by stage",
        "MEDIAN(stage entry to next-stage entry, business days) across all stage occurrences",
        med(stage_el), 4.0, "days", "lower", "T1-01, T1-03", g["T2-02"], len(stage_el),
        "Every stage occurrence of every case; the per-stage split is in kpi_stage_breakdown")

    ho = ev.loc[ev["is_handoff"], "waiting_time_minutes"].dropna()
    kpi(t2, "T2-03", "Waiting-time minutes by handoff",
        "MEAN(business minutes between an outgoing event and the next responsible actor's first action) / 510",
        bdays(mean_(ho)), 1.5, "days", "lower", "T1-01, T1-03, T1-04", g["T2-03"], len(ho),
        "Events where responsibility moved between personas (handoff_flag = Y)")

    a4 = sla_days(evr, "SLA-03")
    kpi(t2, "T2-04", "Time to recruiter assignment",
        "MEDIAN(business days from Requisition Approved to Recruiter Assigned)",
        med(a4), 2.0, "days", "lower", "T1-01, T1-03, T1-04", g["T2-04"], len(a4),
        "Approved requisitions that reached recruiter assignment")

    s5 = rq["days_to_first_shortlist"].dropna()
    kpi(t2, "T2-05", "Time to first qualified shortlist",
        "MEDIAN(business days from sourcing launch to the first shortlisted candidate)",
        med(s5), 10.0, "days", "lower", "T1-01, T1-03, T1-04", g["T2-05"], len(s5),
        "Requisitions that were posted and produced at least one shortlisted candidate")

    s6 = sla_days(eva, "SLA-08")
    kpi(t2, "T2-06", "Interview scheduling lead time",
        "MEDIAN(business days from Interview Requested to Interview Confirmed); rescheduling is T2-19/T2-17",
        med(s6), 2.0, "days", "lower", "T1-01, T1-03, T1-04", g["T2-06"], len(s6),
        "Interview rounds that reached a confirmed slot")

    sev = ev.loc[ev["is_breach"], "sla_overrun_minutes"].dropna()
    kpi(t2, "T2-07", "SLA breach frequency and severity",
        "COUNT(breached intervals) / COUNT(measured intervals); severity = MEAN(minutes beyond target)",
        pct(brch, elig), 15.0, "%", "lower", "T1-02, T1-03", g["T2-07"], elig,
        f"All measured SLA intervals. Mean severity when breached: {round(bdays(mean_(sev)), 2)} business days")

    c8den = ap[ap["shortlist_submitted_timestamp"].notna()]
    c8num = c8den[c8den["hm_review_decision_timestamp"].notna()
                  & c8den["first_interview_timestamp"].notna()]
    kpi(t2, "T2-08", "Stage-to-stage candidate conversion",
        "COUNT(shortlisted candidates progressing to an interview) / COUNT(shortlists submitted)",
        pct(len(c8num), len(c8den)), 55.0, "%", "higher", "T1-05, T1-08", g["T2-08"], len(c8den),
        "Applications submitted to a hiring manager; pending reviews stay in the denominator "
        "only when a decision has been reached")

    s9 = sla_days(eva, "SLA-06")
    kpi(t2, "T2-09", "Screening turnaround time",
        "MEDIAN(business days from Application Received to Resume Screening Completed)",
        med(s9), 3.0, "days", "lower", "T1-05, T1-08", g["T2-09"], len(s9),
        "Applications with a completed screening decision")

    inact = ap["max_inactivity_business_days"].dropna()
    kpi(t2, "T2-10", "Candidate inactivity duration",
        "MEAN(longest gap between consecutive progression events per application); reminders excluded",
        mean_(inact), 5.0, "days", "lower", "T1-05, T1-08", g["T2-10"], len(inact),
        "All applications. Follow-up and reminder events are excluded so a chase cannot hide a wait")

    asm_assigned = len(case_has(eva, "Assessment Assigned"))
    asm_done = len(case_has(eva, "Assessment Completed"))
    asm_exp = len(case_has(eva, "Assessment Expired"))
    kpi(t2, "T2-11", "Assessment completion and expiry rate",
        "COUNT(assessments completed) / COUNT(assessments assigned); expiry rate reported alongside",
        pct(asm_done, asm_assigned), 90.0, "%", "higher", "T1-05", g["T2-11"], asm_assigned,
        f"Applications with an assessment assigned. Expiry rate {pct(asm_exp, asm_assigned)}%")

    s12 = sla_days(eva, "SLA-14")
    resp = offers_responded.copy()
    resp_days = (busdays_between(resp["offer_sent_timestamp"], resp["offer_response_timestamp"])
                 if len(resp) else np.array([]))
    kpi(t2, "T2-12", "Offer decision turnaround",
        "MEDIAN(business days from the last Offer Sent to the candidate's final response)",
        med(s12) if len(s12) else med(resp_days), 5.0, "days", "lower",
        "T1-06", g["T2-12"], len(resp),
        f"Offers with a final response. {len(offers) - len(offers_responded)} offers were still pending "
        f"at the cut-off and are excluded")

    neg = offers[(offers["offer_negotiation_count"].fillna(0) > 0)
                 | (offers["offer_revision_count"].fillna(0) > 0)]
    kpi(t2, "T2-13", "Offer revision / negotiation rate",
        "COUNT(offers with >=1 negotiation or revision event) / COUNT(offers sent)",
        pct(len(neg), len(offers)), 15.0, "%", "lower", "T1-06, T1-08", g["T2-13"], len(offers),
        "Applications with an offer sent")

    s14 = sla_days(eva, "SLA-12")
    ret = offers["offer_approval_return_count"].fillna(0) + offers["comp_revision_count"].fillna(0)
    kpi(t2, "T2-14", "Offer approval cycle and rework",
        "MEDIAN(business days from Compensation Proposal Created to Offer Approved); returns counted alongside",
        med(s14), 3.0, "days", "lower", "T1-06, T1-07, T1-08", g["T2-14"], len(s14),
        f"Offers that reached approval. Mean returns/revisions per offer: {round(mean_(ret) or 0, 2)}")

    pj = accepted.copy()
    pj_days = interval_bdays(eva, "Offer Accepted", "Joining Readiness Confirmed", "last", "last")
    kpi(t2, "T2-15", "Pre-joining completion time",
        "MEDIAN(business days from Offer Accepted to Joining Readiness Confirmed)",
        med(pj_days), 12.0, "days", "lower", "T1-07, T1-08", g["T2-15"], len(pj_days),
        "Accepted offers that reached joining readiness")

    kpi(t2, "T2-16", "Accepted-offer no-show / non-joining rate",
        "COUNT(accepted offers that did not convert to a joining) / COUNT(mature accepted offers)",
        pct(int(accepted_mature["joining_flag"].eq("N").sum()), len(accepted_mature)),
        5.0, "%", "lower", "T1-07", g["T2-16"], len(accepted_mature),
        "Accepted offers with an evidenced joining or non-joining outcome")

    loops_all = pd.concat([rq_closed["rework_loop_count"], ap_closed["rework_loop_count"]]).dropna()
    kpi(t2, "T2-17", "Rework loop count",
        "MEAN(returns to an already-completed activity or stage per case)",
        mean_(loops_all), 0.6, "loops", "lower", "T1-09", g["T2-17"], len(loops_all),
        "Completed cases. Repeated interview rounds are legitimate repetition and are not counted")

    returned = rq[(rq["demand_return_count"].fillna(0) > 0) | (rq["approval_return_count"].fillna(0) > 0)]
    kpi(t2, "T2-18", "Repeated approval / incomplete submission rate",
        "COUNT(requisitions returned or resubmitted for missing information) / COUNT(requisitions)",
        pct(len(returned), n_rq), 15.0, "%", "lower", "T1-09, T1-10", g["T2-18"], n_rq,
        "All requisitions")

    fb_cases = case_has(eva, "Interview Feedback Submitted")
    fb_bad = ap[(ap["feedback_late_count"].fillna(0) > 0) | (ap["feedback_return_count"].fillna(0) > 0)]
    fb_bad = fb_bad[fb_bad["application_id"].isin(fb_cases)]
    kpi(t2, "T2-19", "Interview feedback lateness / incompleteness",
        "COUNT(applications with feedback past SLA or returned for missing fields) / COUNT(applications with feedback)",
        pct(len(fb_bad), len(fb_cases)), 15.0, "%", "lower", "T1-09, T1-10", g["T2-19"], len(fb_cases),
        "Applications with at least one submitted interview feedback")

    kpi(t2, "T2-20", "Mandatory control completion rate",
        "COUNT(eligible cases with every mandatory approval/check evidenced) / COUNT(eligible cases)",
        pct(control_rows["eligible"] - control_rows["missing"], control_rows["eligible"]),
        99.0, "%", "higher", "T1-10", g["T2-20"], control_rows["eligible"],
        "Cases that reached a step requiring a mandatory control; documented exceptions count as complete")

    kpi(t2, "T2-21", "Process path deviation rate",
        "COUNT(cases with a missing mandatory control and no documented exception) / COUNT(cases)",
        pct(len(deviations), conf_den), 1.0, "%", "lower", "T1-10", g["T2-21"], conf_den,
        "All cases")

    kpi(t2, "T2-22", "Active requisitions per recruiter",
        "MEAN(weighted open requisition load per recruiter per weekly snapshot)",
        mean_(ld["weighted_load"]), 12.0, "weighted reqs", "lower",
        "T1-11, T1-12", g["T2-22"], len(ld),
        f"Weekly recruiter snapshots. Median {round(load_med, 2)}, P90 {round(load_p90, 2)}, "
        f"aged (>60 days) requisitions per snapshot {round(mean_(ld['requisitions_open_over_60_days']), 2)}")

    mt = pd.concat([rq["manual_touch_count"], ap["manual_touch_count"]]).dropna()
    kpi(t2, "T2-23", "Manual touch count per case",
        "MEAN(events with manual_touch_flag = Y per case)",
        mean_(mt), 15.0, "events", "lower", "T1-11, T1-12", g["T2-23"], len(mt),
        "All cases. Only evidenced manual actions count - a human-owned event is not assumed manual")

    fu = pd.concat([rq["follow_up_event_count"], ap["follow_up_event_count"]]).dropna()
    kpi(t2, "T2-24", "Follow-up / coordination event rate",
        "MEAN(reminder, chase and scheduling-coordination events per case)",
        mean_(fu), 2.5, "events", "lower", "T1-11, T1-12", g["T2-24"], len(fu),
        "All cases, counting only the follow-up activities listed in kpi_core.FOLLOW_UP_ACTIVITIES")

    kpi(t2, "T2-25", "Measured recruitment effort per hire",
        "SUM(duration_minutes of events on filled requisitions) / 60 / COUNT(hires)",
        effort, None, "hours", "lower", "T1-12", g["T2-25"], len(joined),
        f"Hires on filled requisitions. Duration coverage {coverage}% of events - this is measured "
        f"effort only, never total effort")

    sync_cases = set(ev.loc[ev["activity_name"].eq("Integration Sync Failed"), "process_instance_id"])
    lag = sla_days(ev, "SLA-17")
    kpi(t2, "T2-26", "Integration failure / synchronization lag",
        "COUNT(cases with a failed cross-system handoff) / COUNT(cases); lag = recovery time to sync completion",
        pct(len(sync_cases), conf_den), 2.0, "%", "lower", "T1-12", g["T2-26"], conf_den,
        f"All cases. Mean recovery lag when a sync failed: {round(mean_(lag) or 0, 2)} business days, "
        f"{int(ev['activity_name'].eq('Integration Sync Retried').sum())} retries, "
        f"{int(ev['activity_name'].eq('Manual Status Re-entry').sum())} manual re-entries")

    tier2 = pd.DataFrame(t2)
    tier2["root_cause_category"] = tier2["kpi_id"].map(_T2_CATEGORY).fillna("Process execution")

    # ================================================================ stage breakdown
    # Grouped by case_type as well as stage: a requisition sitting in "Sourcing" while its
    # candidate pipeline runs is not the same measurement as a candidate's stage dwell, and
    # pooling the two would misattribute the elapsed time (BRD section 6.1).
    ev_stage = ev.groupby(["case_type", "stage_code"]).agg(
        events=("event_id", "count"),
        manual=("is_manual", "sum"),
        rework=("is_rework", "sum"),
        exceptions=("is_exc", "sum"),
        handoffs=("is_handoff", "sum"),
        dur=("duration_minutes", "sum"),
        recorded_wait=("recorded_wait_min", "sum")).reset_index()
    sg = st.groupby(["case_type", "stage_code"]).agg(
        cases=("process_instance_id", "nunique"),
        occurrences=("stage_code", "count"),
        revisits=("is_revisit", lambda s: (s == "Y").sum()),
        median_elapsed_days=("elapsed_business_days", "median"),
        avg_elapsed_days=("elapsed_business_days", "mean"),
        p90_elapsed_days=("elapsed_business_days", lambda s: s.quantile(0.90)),
        total_elapsed=("elapsed_business_days", "sum")).reset_index()
    sg["stage_name"] = sg["stage_code"].map(STAGE_NAMES)
    sg["stage_sequence"] = sg["stage_code"].map(lambda c: int(c[1:]) if c[1:].isdigit() else 0)
    sg["revisit_rate_pct"] = (100.0 * sg["revisits"] / sg["occurrences"]).round(1)
    sg["share_of_cycle_pct"] = (100.0 * sg["total_elapsed"]
                                / sg.groupby("case_type")["total_elapsed"].transform("sum")).round(1)
    sg["stage_sla_days"] = [stage_sla(r.case_type, r.stage_code) for r in sg.itertuples()]
    st["stage_sla_days"] = [stage_sla(r.case_type, r.stage_code) for r in st.itertuples()]
    st["breached"] = st["elapsed_business_days"] > st["stage_sla_days"]
    brk = st.groupby(["case_type", "stage_code"])["breached"].sum().rename("breaches").reset_index()
    sg = sg.merge(brk, on=["case_type", "stage_code"], how="left")
    sg["sla_breach_pct"] = (100.0 * sg["breaches"] / sg["occurrences"]).round(1)
    sg = sg.merge(ev_stage, on=["case_type", "stage_code"], how="left")
    sg["avg_recorded_waiting_days"] = (sg["recorded_wait"] / BUSINESS_MIN_PER_DAY / sg["cases"]).round(2)
    sg["avg_processing_hours"] = (sg["dur"] / 60.0 / sg["cases"]).round(2)
    sg["avg_events"] = (sg["events"] / sg["cases"]).round(1)
    sg["avg_manual_events"] = (sg["manual"] / sg["cases"]).round(1)
    sg["rework_events_per_case"] = (sg["rework"] / sg["cases"]).round(2)
    sg["exception_events_per_case"] = (sg["exceptions"] / sg["cases"]).round(2)
    sg["handoffs_per_case"] = (sg["handoffs"] / sg["cases"]).round(2)
    if not gd.empty:
        gstage = (gd.groupby(["case_type", "stage_code"])["gap_reference"]
                    .agg(lambda s: ";".join(sorted(set(s))[:6])).rename("gap_references").reset_index())
        sg = sg.merge(gstage, on=["case_type", "stage_code"], how="left")
    else:
        sg["gap_references"] = None
    _RANK = {"Healthy": 0, "Watch": 1, "Critical": 2, "N/A": 0, "Informational": 0}
    sg["health"] = [
        max(health(r.median_elapsed_days, r.stage_sla_days, "lower"),
            health(r.sla_breach_pct, STAGE_BREACH_WATCH_PCT, "lower"),
            key=lambda h: _RANK[h])
        for r in sg.itertuples()]
    sg["sla_ratio"] = sg["median_elapsed_days"] / sg["stage_sla_days"]
    sg = sg.sort_values(["case_type", "stage_sequence"])
    stages = sg[["case_type", "stage_code", "stage_name", "stage_sequence", "cases", "occurrences",
                 "revisit_rate_pct", "median_elapsed_days", "avg_elapsed_days", "p90_elapsed_days",
                 "share_of_cycle_pct", "stage_sla_days", "sla_breach_pct", "avg_recorded_waiting_days",
                 "avg_processing_hours", "avg_events", "avg_manual_events", "rework_events_per_case",
                 "exception_events_per_case", "handoffs_per_case", "gap_references", "health"]].copy()
    for c in ["median_elapsed_days", "avg_elapsed_days", "p90_elapsed_days"]:
        stages[c] = stages[c].round(2)

    # ================================================================ activity breakdown
    def top_reason(s):
        s = s[s.ne("")]
        return s.value_counts().index[0] if len(s) else None

    act = ev.groupby("activity_name").agg(
        stage_code=("stage_code", "first"),
        activity_stage=("activity_stage", "first"),
        actor_persona=("actor_persona", "first"),
        source_system=("source_system", "first"),
        events=("event_id", "count"),
        cases=("process_instance_id", "nunique"),
        avg_duration_min=("duration_minutes", "mean"),
        avg_waiting_min=("waiting_time_minutes", "mean"),
        total_waiting_min=("waiting_time_minutes", "sum"),
        repeats=("repeat_sequence_number", lambda s: (s > 1).sum()),
        sla_measured=("sla_code", lambda s: s.ne("").sum()),
        breaches=("sla_breach_flag", lambda s: (s == "Y").sum()),
        overrun=("sla_overrun_minutes", "sum"),
        exceptions=("exception_flag", lambda s: (s == "Y").sum()),
        rework=("rework_flag", lambda s: (s == "Y").sum()),
        manual=("manual_touch_flag", lambda s: (s == "Y").sum()),
        handoffs=("handoff_flag", lambda s: (s == "Y").sum()),
        top_waiting_reason=("waiting_reason_code", top_reason)).reset_index()
    act["cases_touched_pct"] = (100.0 * act["cases"] / conf_den).round(1)
    act["avg_duration_min"] = act["avg_duration_min"].round(1)
    act["avg_waiting_min"] = act["avg_waiting_min"].round(1)
    act["total_waiting_days"] = (act["total_waiting_min"] / BUSINESS_MIN_PER_DAY).round(1)
    act["repeat_event_pct"] = (100.0 * act["repeats"] / act["events"]).round(1)
    act["sla_breach_pct"] = np.where(act["sla_measured"] > 0,
                                     (100.0 * act["breaches"] / act["sla_measured"]).round(1), np.nan)
    act["avg_sla_overrun_days"] = np.where(act["breaches"] > 0,
                                           (act["overrun"] / act["breaches"] / BUSINESS_MIN_PER_DAY).round(2),
                                           np.nan)
    act["exception_rate_pct"] = (100.0 * act["exceptions"] / act["events"]).round(1)
    act["rework_share_pct"] = (100.0 * act["rework"] / act["events"]).round(1)
    act["manual_pct"] = (100.0 * act["manual"] / act["events"]).round(1)
    act["handoff_pct"] = (100.0 * act["handoffs"] / act["events"]).round(1)
    act["wait_is_container"] = np.where(act["activity_name"].isin(CONTAINER_WAIT_ACTIVITIES), "Y", "N")
    rankable = act["wait_is_container"].eq("N")
    act["waiting_rank"] = pd.Series(
        act.loc[rankable, "total_waiting_days"].rank(ascending=False, method="min"), index=act.index)
    act["effort_rank"] = pd.Series(
        (act.loc[rankable, "avg_duration_min"].fillna(0) * act.loc[rankable, "events"])
        .rank(ascending=False, method="min"), index=act.index)
    activities = act[["activity_name", "stage_code", "activity_stage", "actor_persona", "source_system",
                      "events", "cases", "cases_touched_pct", "avg_duration_min", "avg_waiting_min",
                      "total_waiting_days", "repeat_event_pct", "sla_breach_pct", "avg_sla_overrun_days",
                      "exception_rate_pct", "rework_share_pct", "manual_pct", "handoff_pct",
                      "top_waiting_reason", "wait_is_container", "waiting_rank", "effort_rank"]] \
        .sort_values(["waiting_rank", "total_waiting_days"], ascending=[True, False],
                     na_position="last").reset_index(drop=True)

    # ================================================================ rework loops
    lp = ev[ev["activity_name"].isin(REWORK_LOOP_ACTIVITIES)]
    if lp.empty:
        rework_loops = pd.DataFrame(columns=["rework_loop_type"])
    else:
        rl = lp.groupby("activity_name").agg(
            case_type=("case_type", "first"),
            stage_code=("stage_code", "first"),
            activity_stage=("activity_stage", "first"),
            cases=("process_instance_id", "nunique"),
            events=("event_id", "count"),
            days_lost=("waiting_time_minutes", lambda s: s.sum() / BUSINESS_MIN_PER_DAY),
            top_reason_code=("reason_code", top_reason)).reset_index()
        rl = rl.rename(columns={"activity_name": "rework_loop_type"})
        rl["cases_pct"] = (100.0 * rl["cases"] / conf_den).round(1)
        rl["loops_per_affected_case"] = (rl["events"] / rl["cases"]).round(2)
        rl["avg_days_lost_per_affected_case"] = (rl["days_lost"] / rl["cases"]).round(2)
        rl["days_lost"] = rl["days_lost"].round(1)
        rl["gap_reference"] = rl["rework_loop_type"].map(REWORK_LOOP_GAP)
        rework_loops = rl.sort_values("days_lost", ascending=False).reset_index(drop=True)

    # ================================================================ stalled cases
    stalled = _stalled(ev, rq, ap, st, gd, slad, as_of)

    # ================================================================ monthly trend
    trend = _monthly_trend(ev, rq, ap, ld, elig)

    # ================================================================ summary header
    t1v = dict(zip(tier1["kpi_id"], tier1["current_value"]))
    gap_counts = gd["gap_reference"].value_counts() if not gd.empty else pd.Series(dtype=int)
    worst_stage = sg.sort_values("sla_ratio", ascending=False).head(1)
    summary = pd.DataFrame([{
        "as_of_timestamp": as_of,
        "requisitions_total": n_rq, "requisitions_filled": len(filled),
        "requisitions_cancelled": len(cancelled), "requisitions_closed_no_hire": len(nohire),
        "requisitions_open": len(rq_open), "fill_rate_pct": pct(len(filled), len(rq_closed)),
        "applications_total": n_ap, "applications_joined": len(joined),
        "applications_withdrawn": len(withdrawn), "applications_in_progress": len(ap_open),
        "offers_sent": len(offers), "hires": int(rq["hires_count"].fillna(0).sum()),
        "events_total": len(ev),
        "time_to_fill_median_days": t1v.get("T1-01"), "time_to_fill_p90_days": round(p90(ttf), 2),
        "approval_lead_time_days": t1v.get("T1-02"), "sla_attainment_pct": t1v.get("T1-03"),
        "waiting_time_share_pct": t1v.get("T1-04"), "offer_acceptance_pct": t1v.get("T1-06"),
        "offer_to_join_pct": t1v.get("T1-07"), "withdrawal_rate_pct": t1v.get("T1-08"),
        "rework_rate_pct": t1v.get("T1-09"), "conformance_rate_pct": t1v.get("T1-10"),
        "manual_touch_pct_of_events": pct(int(ev["is_manual"].sum()), len(ev)),
        "recruiter_load_median": round(load_med, 2), "recruiter_load_p90": round(load_p90, 2),
        "tier1_critical": int((tier1["health"] == "Critical").sum()),
        "tier1_watch": int((tier1["health"] == "Watch").sum()),
        "tier1_healthy": int((tier1["health"] == "Healthy").sum()),
        "tier2_critical": int((tier2["health"] == "Critical").sum()),
        "gap_findings_total": int(len(gd)),
        "gap_references_detected": int(gd["gap_reference"].nunique()) if not gd.empty else 0,
        "top_gap_reference": gap_counts.index[0] if len(gap_counts) else None,
        "top_gap_findings": int(gap_counts.iloc[0]) if len(gap_counts) else None,
        "top_bottleneck_stage": (f"{worst_stage['case_type'].iloc[0]} / {worst_stage['stage_name'].iloc[0]}"
                                 if len(worst_stage) else None),
        "top_bottleneck_stage_days": worst_stage["median_elapsed_days"].iloc[0] if len(worst_stage) else None,
        "top_waiting_activity": activities["activity_name"].iloc[0] if len(activities) else None,
    }])

    return {
        "summary": summary, "tier1": tier1, "tier2": tier2, "stages": stages,
        "activities": activities, "rework_loops": rework_loops, "stalled": stalled,
        "monthly_trend": trend,
        "run_stats": {"requisitions_total": n_rq, "requisitions_closed": len(rq_closed),
                      "requisitions_filled": len(filled), "requisitions_open": len(rq_open),
                      "applications_total": n_ap, "applications_joined": len(joined),
                      "cases_stalled": int(len(stalled)), "events_total": int(len(ev))},
    }


def critical_kpis(tier1):
    """Tier 1 kpi_ids currently flagged Critical - the entry point for root-cause analysis."""
    return tier1[tier1["health"] == "Critical"]["kpi_id"].tolist()


# ------------------------------------------------------------------ internals
_T2_CATEGORY = {
    "T2-01": "Approval governance", "T2-18": "Approval governance", "T2-20": "Approval governance",
    "T2-21": "Approval governance",
    "T2-02": "Stage flow", "T2-03": "Handoff friction", "T2-04": "Capacity and allocation",
    "T2-05": "Sourcing readiness", "T2-06": "Interview coordination", "T2-07": "SLA discipline",
    "T2-08": "Candidate progression", "T2-09": "Screening throughput",
    "T2-10": "Candidate engagement", "T2-11": "Assessment execution",
    "T2-12": "Offer execution", "T2-13": "Offer execution", "T2-14": "Compensation governance",
    "T2-15": "Pre-joining execution", "T2-16": "Pre-joining execution",
    "T2-17": "Rework", "T2-19": "Interview feedback discipline",
    "T2-22": "Capacity and allocation", "T2-23": "Manual effort", "T2-24": "Manual effort",
    "T2-25": "Manual effort", "T2-26": "System integration",
}


def _gap_index(gmap):
    """Invert the gap library: Tier 2 kpi_id -> the GAP ids that cite it as evidence.

    Falls back to an empty string when the library is unavailable, so a missing reference
    table degrades the metadata but never the KPI values.
    """
    idx = {f"T2-{i:02d}": [] for i in range(1, 27)}
    if gmap is not None and not gmap.empty and "tier2_evidence" in gmap.columns:
        for r in gmap.itertuples():
            for t2 in str(getattr(r, "tier2_evidence", "") or "").replace(";", ",").split(","):
                t2 = t2.strip()
                if t2 in idx:
                    idx[t2].append(r.gap_reference)
    return {k: ";".join(sorted(set(v))) for k, v in idx.items()}


def _conformance(ev):
    """Cases missing a mandatory control, excluding those with a documented policy exception."""
    documented = set(ev.loc[ev["activity_name"].eq(POLICY_EXCEPTION), "process_instance_id"])
    deviations, eligible_ids, missing_ids = set(), set(), set()
    for case_type, trigger, control, _label in MANDATORY_CONTROLS:
        scope = ev[ev["case_type"].eq(case_type)]
        has_trigger = set(scope.loc[scope["activity_name"].eq(trigger), "process_instance_id"])
        has_control = set(scope.loc[scope["activity_name"].eq(control), "process_instance_id"])
        eligible_ids |= has_trigger
        gap = has_trigger - has_control
        missing_ids |= gap
        deviations |= (gap - documented)
    return deviations, {"eligible": len(eligible_ids), "missing": len(missing_ids - documented)}


def _effort_per_hire(ev, rq, filled, joined):
    """Measured effort and cost per hire, with the duration coverage disclosed.

    Only events carrying a reliable duration contribute. The BRD is explicit that a
    human-owned event must not be treated as effort when no duration was recorded.
    """
    coverage = pct(int(ev["duration_minutes"].notna().sum()), len(ev))
    hires = max(1, len(joined))
    scope = ev[ev["requisition_id"].isin(set(filled["requisition_id"]))].copy()
    scope["rate"] = scope["actor_persona"].map(PERSONA_RATE_PER_HOUR).fillna(0.0)
    hours = scope["duration_minutes"].fillna(0).sum() / 60.0
    labour = (scope["duration_minutes"].fillna(0) / 60.0 * scope["rate"]).sum()
    assessments = int(scope["activity_name"].eq("Assessment Assigned").sum()) * ASSESSMENT_COST_PER_EVENT
    agency_reqs = set(scope.loc[scope["activity_name"].eq("Recruitment Agency Engaged"), "requisition_id"])
    agency = len(agency_reqs) * AGENCY_FEE_PER_HIRE
    return round(hours / hires, 2), round((labour + assessments + agency) / hires, 2), coverage


_ACTION_BY_PERSONA = {
    "Approving Business Leader": "Escalate to the approver - pending beyond SLA; re-route via delegated authority",
    "Workforce Planning Analyst": "Chase budget / headcount validation; escalate to the budget owner",
    "Hiring Manager": "Hiring-manager decision overdue - escalate and offer a delegate reviewer",
    "Recruiter": "Recruiter follow-up - progress the candidate or close the application with a reason",
    "TA Operations Coordinator": "Assign or re-balance the requisition to an available recruiter",
    "Recruiting Coordinator": "Re-attempt interview scheduling with a panel capacity block",
    "Interview Panel": "Chase outstanding interview feedback against the scorecard SLA",
    "Assessment Coordinator": "Re-issue or extend the assessment and confirm the provider SLA",
    "Compensation Analyst": "Complete the compensation proposal with full benchmark data",
    "Compensation Approver": "Escalate offer approval - pending beyond the approval SLA",
    "HR Operations": "Chase verification / documents and own the exception queue",
    "Candidate": "Candidate response outstanding - recruiter to re-engage and set a response date",
    "System / Integration": "Investigate the failed interface call and reconcile the status manually",
    "Sourcer": "Review sourcing channel performance and extend or re-target the campaign",
    "Business Manager": "Return to the business owner to complete the hiring demand",
}
_ACTION_BY_WAIT_REASON = {
    "PENDING_APPROVAL": "Escalate the pending approval and apply the aging alert",
    "PENDING_BUDGET_VALIDATION": "Escalate budget validation to the headcount owner",
    "RECRUITER_UNASSIGNED": "Allocate a recruiter now - load-balanced assignment",
    "HM_REVIEW_PENDING": "Escalate the shortlist review; offer a delegate reviewer",
    "PANEL_UNAVAILABLE": "Book a standing panel capacity block or switch to self-scheduling",
    "FEEDBACK_PENDING": "Apply the feedback completeness gate and chase the panel",
    "ASSESSMENT_PENDING": "Re-issue the assessment with a clear due date",
    "SELECTION_DECISION_PENDING": "Convene the calibration / decision meeting with the decision owner",
    "COMP_APPROVAL_PENDING": "Escalate via the approval matrix; verify the proposal is complete",
    "CANDIDATE_RESPONSE_PENDING": "Recruiter to re-engage the candidate and confirm the response date",
    "VERIFICATION_PENDING": "Chase the verification vendor against the agreed provider SLA",
    "DOCUMENTS_PENDING": "Re-issue the document checklist and own it in the exception queue",
    "SYSTEM_SYNC_FAILURE": "Fix the integration error, replay the event and reconcile",
}


def _stalled(ev, rq, ap, st, gd, slad, as_of):
    """Open cases whose current wait breaches policy, with the recommended Kinetic action."""
    open_ids = set(rq.loc[rq["is_open_censored"].eq("Y"), "requisition_id"]) \
        | set(ap.loc[ap["is_open_censored"].eq("Y"), "application_id"])
    if not open_ids:
        return pd.DataFrame(columns=["process_instance_id"])
    last = ev[ev["process_instance_id"].isin(open_ids)].sort_values("ts") \
             .groupby("process_instance_id").tail(1).copy()
    last["open_business_days"] = busdays_between(last["ts"], as_of)
    last["stage_sla_days"] = [stage_sla(r.case_type, r.stage_code) for r in last.itertuples()]

    # an interval whose opening activity is evidenced but whose closing activity never arrived
    open_code, open_overrun = {}, {}
    if slad is not None and not slad.empty:
        by_case = ev[ev["process_instance_id"].isin(open_ids)]
        acts = by_case.groupby("process_instance_id")["activity_name"].agg(set)
        opens = by_case[by_case["activity_name"].isin(set(slad["from_activity"]))]
        tgt = dict(zip(slad["sla_code"], pd.to_numeric(slad["target_business_days_standard"],
                                                       errors="coerce")))
        pair = {r.from_activity: (r.sla_code, r.to_activity) for r in slad.itertuples()}
        for pid, grp in opens.groupby("process_instance_id"):
            done = acts.get(pid, set())
            best = None
            for r in grp.sort_values("ts").itertuples():
                code, to_act = pair[r.activity_name]
                if to_act in done:
                    continue
                od = float(np.busday_count(r.ts.date(), as_of.date()))
                ov = od - (tgt.get(code) or 0)
                if best is None or ov > best[1]:
                    best = (code, ov)
            if best:
                open_code[pid], open_overrun[pid] = best[0], round(best[1], 2)
    last["open_sla_code"] = last["process_instance_id"].map(open_code)
    last["open_sla_overrun_days"] = last["process_instance_id"].map(open_overrun)

    keep = (last["open_business_days"] > STALLED_THRESHOLD_DAYS) \
        | (last["open_business_days"] > last["stage_sla_days"]) \
        | (last["open_sla_overrun_days"].fillna(-1) > 0)
    out = last[keep].copy()
    if out.empty:
        return pd.DataFrame(columns=["process_instance_id"])

    if gd is not None and not gd.empty:
        gmap_case = gd.groupby("process_instance_id")["gap_reference"] \
                      .agg(lambda s: ";".join(sorted(set(s))[:6]))
        out["gap_references"] = out["process_instance_id"].map(gmap_case)
    else:
        out["gap_references"] = None

    out["last_activity_name"] = out["activity_name"]
    out["last_event_timestamp"] = out["ts"]
    out["owner_id_pseudonymous"] = out["actor_id_pseudonymous"]
    out["kinetic_flag"] = np.where(
        out["open_sla_overrun_days"].fillna(-1) > 0,
        "Open SLA interval breached - no closing event evidenced",
        "Case idle beyond the stage SLA / stalled threshold")
    out["recommended_action"] = [
        _ACTION_BY_WAIT_REASON.get(r.waiting_reason_code)
        or _ACTION_BY_PERSONA.get(r.actor_persona, "Review the open case with the owning team")
        for r in out.itertuples()]
    out["open_business_days"] = out["open_business_days"].round(1)
    cols = ["process_instance_id", "case_type", "requisition_id", "application_id",
            "last_activity_name", "activity_stage", "stage_code", "actor_persona",
            "owner_id_pseudonymous", "recruiter_id_pseudonymous", "last_event_timestamp",
            "open_business_days", "stage_sla_days", "waiting_reason_code", "open_sla_code",
            "open_sla_overrun_days", "business_unit", "job_family", "location", "job_level",
            "priority", "gap_references", "kinetic_flag", "recommended_action"]
    return out.sort_values("open_business_days", ascending=False)[cols].reset_index(drop=True)


def _monthly_trend(ev, rq, ap, ld, elig_total):
    """Tier 1 KPIs by month.

    Each block uses its own, explicitly stated cohort base - requisition metrics by requisition
    opening month, application metrics by application creation month, offer metrics by offer-sent
    month and event metrics by event month. Mixing them in one denominator would be wrong.
    """
    rq = rq.copy()
    rq["month"] = rq["requisition_open_timestamp"].dt.to_period("M").astype(str)
    base = rq.groupby("month").agg(
        requisitions_opened=("requisition_id", "count"),
        requisitions_filled=("requisition_outcome", lambda s: (s == "Filled").sum()),
        hires=("hires_count", "sum"),
        time_to_fill_days=("time_to_fill_business_days", "median"),
        approval_lead_time_days=("days_to_approval", "median"),
        rework_rate_pct=("rework_loop_count", lambda s: pct((s.fillna(0) > 0).sum(), len(s))),
    ).reset_index()

    ap = ap.copy()
    ap["month"] = ap["application_created_timestamp"].dt.to_period("M").astype(str)
    a = ap.groupby("month").agg(
        applications_received=("application_id", "count"),
        withdrawal_rate_pct=("application_outcome", lambda s: pct((s == "Withdrawn").sum(), len(s))),
    ).reset_index()

    om = ap[ap["offer_sent_timestamp"].notna()].copy()
    om["month"] = om["offer_sent_timestamp"].dt.to_period("M").astype(str)
    o = om.groupby("month").agg(
        offers_sent=("application_id", "count"),
        offer_acceptance_pct=("offer_acceptance_flag",
                              lambda s: pct((s == "Y").sum(), s.isin(["Y", "N"]).sum())),
    ).reset_index()

    e = ev.groupby("month").agg(
        sla_measured=("sla_code", lambda s: s.ne("").sum()),
        sla_breaches=("sla_breach_flag", lambda s: (s == "Y").sum()),
        manual=("is_manual", "sum"),
        cases=("process_instance_id", "nunique"),
        recorded_wait=("recorded_wait_min", "sum"),
    ).reset_index()
    e["sla_attainment_pct"] = (100.0 * (e["sla_measured"] - e["sla_breaches"])
                               / e["sla_measured"].replace(0, np.nan)).round(1)
    e["manual_touches_per_case"] = (e["manual"] / e["cases"]).round(1)
    e["waiting_days_per_case"] = (e["recorded_wait"] / BUSINESS_MIN_PER_DAY / e["cases"]).round(2)

    if ld is not None and not ld.empty:
        l = ld.copy()
        l["month"] = l["snapshot_date"].dt.to_period("M").astype(str)
        lm = l.groupby("month")["weighted_load"].median().round(2).rename("recruiter_load_median").reset_index()
    else:
        lm = pd.DataFrame(columns=["month", "recruiter_load_median"])

    tr = base.merge(a, on="month", how="outer").merge(o, on="month", how="outer") \
             .merge(e[["month", "sla_attainment_pct", "manual_touches_per_case",
                       "waiting_days_per_case"]], on="month", how="outer") \
             .merge(lm, on="month", how="left")
    tr["conformance_rate_pct"] = np.nan          # conformance is a case-level control, reported run-wide
    tr = tr[tr["month"].notna() & tr["month"].ne("NaT")].sort_values("month").reset_index(drop=True)
    for c in ["time_to_fill_days", "approval_lead_time_days"]:
        tr[c] = pd.to_numeric(tr[c], errors="coerce").round(2)
    for c in ["requisitions_opened", "requisitions_filled", "hires", "applications_received",
              "offers_sent"]:
        tr[c] = pd.to_numeric(tr[c], errors="coerce").fillna(0).astype(int)
    return tr
