"""
STEP 4 - Compute the Tier 1 / Tier 2 Talent Acquisition KPIs from the event log + case masters
         and write them back into the KPI tables, plus an offline CSV copy of every result.

Reads   : {schema}.event_log, requisition_master, application_master, stage_transitions,
          recruiter_load_snapshot, gap_detection, gap_mapping, sla_definitions, persona_master
          (or the CSV extract in data/ with --source csv)
Writes  : {schema}.kpi_run                 - one control row per computation (history kept)
          {schema}.kpi_summary             - one row per run: the dashboard header
          {schema}.kpi_tier1_health        - 12 outcome KPIs with health + trend vs previous run
          {schema}.kpi_tier2_diagnostics   - 26 diagnostic KPIs linked to Tier 1 + GAP ids
          {schema}.kpi_stage_breakdown     - elapsed / waiting / effort per recruitment stage
          {schema}.kpi_activity_breakdown  - per-activity hotspots (waiting & effort ranks)
          {schema}.kpi_rework_loops        - rework loop profile and business days lost
          {schema}.kpi_monthly_trend       - Tier 1 KPIs by month
          {schema}.kpi_stalled_cases       - open cases past policy + recommended action
and     : data/ta_kpi_*.csv                - the same eight result sets as an offline extract

Usage:
    python 04_compute_kpis_to_postgres.py
    python 04_compute_kpis_to_postgres.py --source csv --target csv          # no database at all
    python 04_compute_kpis_to_postgres.py --as-of "2026-10-19 09:00"
    python 04_compute_kpis_to_postgres.py --from 2025-07-01 --to 2026-06-30  # period filter
    python 04_compute_kpis_to_postgres.py --keep-runs 5                      # prune older runs
    python 04_compute_kpis_to_postgres.py --quiet                            # no console report
"""
import argparse
import json
import os

import pandas as pd

import kpi_core

HERE = os.path.dirname(os.path.abspath(__file__))

# result set -> (postgres table, csv file, insert columns in schema order)
OUTPUTS = {
    "summary": ("kpi_summary", "ta_kpi_summary.csv", [
        "as_of_timestamp", "period_from", "period_to", "requisitions_total", "requisitions_filled",
        "requisitions_cancelled", "requisitions_closed_no_hire", "requisitions_open",
        "fill_rate_pct", "applications_total", "applications_joined", "applications_withdrawn",
        "applications_in_progress", "offers_sent", "hires", "events_total",
        "time_to_fill_median_days", "time_to_fill_p90_days", "approval_lead_time_days",
        "sla_attainment_pct", "waiting_time_share_pct", "offer_acceptance_pct", "offer_to_join_pct",
        "withdrawal_rate_pct", "rework_rate_pct", "conformance_rate_pct",
        "manual_touch_pct_of_events", "recruiter_load_median", "recruiter_load_p90",
        "tier1_critical", "tier1_watch", "tier1_healthy", "tier2_critical", "gap_findings_total",
        "gap_references_detected", "top_gap_reference", "top_gap_findings",
        "top_bottleneck_stage", "top_bottleneck_stage_days", "top_waiting_activity"]),
    "tier1": ("kpi_tier1_health", "ta_kpi_tier1_health.csv", [
        "kpi_id", "kpi_name", "formula", "current_value", "target", "unit", "better_when",
        "variance_vs_target_pct", "health", "trend_direction", "previous_value",
        "denominator_cases", "cohort_rule", "tier2_drivers"]),
    "tier2": ("kpi_tier2_diagnostics", "ta_kpi_tier2_diagnostics.csv", [
        "kpi_id", "kpi_name", "formula", "current_value", "target", "unit", "better_when",
        "variance_vs_target_pct", "health", "feeds_tier1_kpi", "gap_reference",
        "root_cause_category", "denominator_cases", "cohort_rule"]),
    "stages": ("kpi_stage_breakdown", "ta_kpi_stage_breakdown.csv", [
        "case_type", "stage_code", "stage_name", "stage_sequence", "cases", "occurrences", "revisit_rate_pct",
        "median_elapsed_days", "avg_elapsed_days", "p90_elapsed_days", "share_of_cycle_pct",
        "stage_sla_days", "sla_breach_pct", "avg_recorded_waiting_days", "avg_processing_hours",
        "avg_events", "avg_manual_events", "rework_events_per_case", "exception_events_per_case",
        "handoffs_per_case", "gap_references", "health"]),
    "activities": ("kpi_activity_breakdown", "ta_kpi_activity_breakdown.csv", [
        "activity_name", "stage_code", "activity_stage", "actor_persona", "source_system",
        "events", "cases", "cases_touched_pct", "avg_duration_min", "avg_waiting_min",
        "total_waiting_days", "repeat_event_pct", "sla_breach_pct", "avg_sla_overrun_days",
        "exception_rate_pct", "rework_share_pct", "manual_pct", "handoff_pct",
        "top_waiting_reason", "wait_is_container", "waiting_rank", "effort_rank"]),
    "rework_loops": ("kpi_rework_loops", "ta_kpi_rework_loops.csv", [
        "rework_loop_type", "case_type", "stage_code", "activity_stage", "cases", "events",
        "cases_pct", "loops_per_affected_case", "days_lost", "avg_days_lost_per_affected_case",
        "top_reason_code", "gap_reference"]),
    "monthly_trend": ("kpi_monthly_trend", "ta_kpi_monthly_trend.csv", [
        "month", "requisitions_opened", "requisitions_filled", "hires", "applications_received",
        "offers_sent", "time_to_fill_days", "approval_lead_time_days", "sla_attainment_pct",
        "offer_acceptance_pct", "withdrawal_rate_pct", "rework_rate_pct", "conformance_rate_pct",
        "manual_touches_per_case", "waiting_days_per_case", "recruiter_load_median"]),
    "stalled": ("kpi_stalled_cases", "ta_kpi_stalled_cases.csv", [
        "process_instance_id", "case_type", "requisition_id", "application_id",
        "last_activity_name", "activity_stage", "stage_code", "actor_persona",
        "owner_id_pseudonymous", "recruiter_id_pseudonymous", "last_event_timestamp",
        "open_business_days", "stage_sla_days", "waiting_reason_code", "open_sla_code",
        "open_sla_overrun_days", "business_unit", "job_family", "location", "job_level",
        "priority", "gap_references", "kinetic_flag", "recommended_action"]),
}

CSV_SOURCES = {
    "events": "ta_event_log.csv",
    "requisitions": "ta_requisition_master.csv",
    "applications": "ta_application_master.csv",
    "stage_transitions": "ta_stage_transitions.csv",
    "recruiter_load": "ta_recruiter_load_snapshot.csv",
    "gap_detection": "ta_gap_detection.csv",
    "gap_mapping": "ta_gap_mapping.csv",
    "sla_definitions": "ta_sla_definitions.csv",
}


# ------------------------------------------------------------------ read
def read_source_csv(input_dir, period_from, period_to):
    src = {}
    for key, name in CSV_SOURCES.items():
        path = os.path.join(input_dir, name)
        if not os.path.exists(path):
            if key in ("gap_mapping", "gap_detection", "sla_definitions"):
                src[key] = pd.DataFrame()
                continue
            raise SystemExit(f"Missing source extract: {path}  (run generate_ta_event_log.py first)")
        src[key] = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    return _apply_period(src, period_from, period_to)


def read_source_db(conn, period_from, period_to):
    from db_config import SCHEMA
    q = lambda sql: pd.read_sql_query(sql, conn)
    src = {
        "events": q(f"SELECT * FROM {SCHEMA}.event_log ORDER BY process_instance_id, event_timestamp, event_id"),
        "requisitions": q(f"SELECT * FROM {SCHEMA}.requisition_master ORDER BY requisition_id"),
        "applications": q(f"SELECT * FROM {SCHEMA}.application_master ORDER BY application_id"),
        "stage_transitions": q(f"SELECT * FROM {SCHEMA}.stage_transitions"),
        "recruiter_load": q(f"SELECT * FROM {SCHEMA}.recruiter_load_snapshot"),
        "gap_detection": q(f"SELECT * FROM {SCHEMA}.gap_detection"),
        "gap_mapping": q(f"SELECT * FROM {SCHEMA}.gap_mapping"),
        "sla_definitions": q(f"SELECT * FROM {SCHEMA}.sla_definitions"),
    }
    return _apply_period(src, period_from, period_to)


def _apply_period(src, period_from, period_to):
    """Filter on the requisition opening date and cascade to every linked frame."""
    if not (period_from or period_to):
        return src
    rq = src["requisitions"].copy()
    opened = pd.to_datetime(rq["requisition_open_timestamp"], errors="coerce")
    keep = pd.Series(True, index=rq.index)
    if period_from:
        keep &= opened >= pd.Timestamp(period_from)
    if period_to:
        keep &= opened <= pd.Timestamp(period_to)
    ids = set(rq.loc[keep, "requisition_id"])
    src["requisitions"] = rq[keep]
    for key in ("applications", "events", "stage_transitions", "gap_detection"):
        d = src[key]
        if not d.empty and "requisition_id" in d.columns:
            src[key] = d[d["requisition_id"].isin(ids)]
    return src


def load_rate_card(conn):
    """Take the persona rate card from persona_master so it is maintained in the DB, not in code."""
    from db_config import SCHEMA
    try:
        rates = pd.read_sql_query(f"SELECT persona, hourly_rate FROM {SCHEMA}.persona_master", conn)
    except Exception:
        return kpi_core.PERSONA_RATE_PER_HOUR
    if not rates.empty:
        kpi_core.PERSONA_RATE_PER_HOUR = {r.persona: float(r.hourly_rate) for r in rates.itertuples()}
    return kpi_core.PERSONA_RATE_PER_HOUR


# ------------------------------------------------------------------ write
def create_run(conn, as_of, period_from, period_to, stats, params):
    from db_config import SCHEMA
    with conn.cursor() as cur:
        cur.execute(f"""
            INSERT INTO {SCHEMA}.kpi_run
                (as_of_timestamp, period_from, period_to, requisitions_total, requisitions_closed,
                 requisitions_filled, requisitions_open, applications_total, applications_joined,
                 cases_stalled, events_total, parameters)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING run_id;""",
            (as_of, period_from, period_to, stats["requisitions_total"], stats["requisitions_closed"],
             stats["requisitions_filled"], stats["requisitions_open"], stats["applications_total"],
             stats["applications_joined"], stats["cases_stalled"], stats["events_total"],
             json.dumps(params)))
        return cur.fetchone()[0]


def add_trend(conn, tier1, run_id):
    """Compare with the previous run so the dashboard can draw the up/down arrow."""
    from db_config import SCHEMA
    tier1 = tier1.copy()
    tier1["previous_value"] = None
    tier1["trend_direction"] = None
    with conn.cursor() as cur:
        cur.execute(f"SELECT MAX(run_id) FROM {SCHEMA}.kpi_run WHERE run_id < %s;", (run_id,))
        prev = cur.fetchone()[0]
    if not prev:
        return tier1
    old = pd.read_sql_query(
        f"SELECT kpi_id, current_value FROM {SCHEMA}.kpi_tier1_health WHERE run_id = %s",
        conn, params=[prev])
    m = dict(zip(old["kpi_id"], old["current_value"]))
    tier1["previous_value"] = tier1["kpi_id"].map(m)

    def direction(r):
        if pd.isna(r["previous_value"]) or pd.isna(r["current_value"]):
            return None
        d = float(r["current_value"]) - float(r["previous_value"])
        if abs(d) < 0.05:
            return "flat"
        improving = (d < 0) if r["better_when"] == "lower" else (d > 0)
        return "up" if improving else "down"

    tier1["trend_direction"] = tier1.apply(direction, axis=1)
    return tier1


def write_postgres(conn, run_id, res):
    from db_config import SCHEMA, bulk_insert
    written = {}
    for key, (table, _csv, cols) in OUTPUTS.items():
        d = res[key].copy()
        if d.empty:
            written[table] = 0
            continue
        for c in cols:
            if c not in d.columns:          # a result set that legitimately has no such column
                d[c] = None
        d["run_id"] = run_id
        written[table] = bulk_insert(conn, f"{SCHEMA}.{table}", d, ["run_id"] + cols)
    return written


def write_csv(res, output_dir, run_id):
    os.makedirs(output_dir, exist_ok=True)
    written = {}
    for key, (_table, name, cols) in OUTPUTS.items():
        d = res[key].copy()
        d.insert(0, "run_id", run_id)
        for c in cols:
            if c not in d.columns:
                d[c] = None
        d[["run_id"] + cols].to_csv(os.path.join(output_dir, name), index=False)
        written[name] = len(d)
    return written


def prune(conn, keep):
    from db_config import SCHEMA
    with conn.cursor() as cur:
        cur.execute(f"""DELETE FROM {SCHEMA}.kpi_run WHERE run_id NOT IN
                        (SELECT run_id FROM {SCHEMA}.kpi_run ORDER BY run_id DESC LIMIT %s);""", (keep,))
        return cur.rowcount


# ------------------------------------------------------------------ console report
def report(res):
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    pd.set_option("display.max_colwidth", 60)
    s = res["summary"].iloc[0]

    print("\nRUN SUMMARY")
    print(f"  requisitions {s.requisitions_total} (filled {s.requisitions_filled}, "
          f"cancelled {s.requisitions_cancelled}, no hire {s.requisitions_closed_no_hire}, "
          f"open {s.requisitions_open})")
    print(f"  applications {s.applications_total} (joined {s.applications_joined}, "
          f"withdrawn {s.applications_withdrawn}, in progress {s.applications_in_progress})")
    print(f"  events {s.events_total}   Tier 1 health: {s.tier1_critical} critical / "
          f"{s.tier1_watch} watch / {s.tier1_healthy} healthy   Tier 2 critical: {s.tier2_critical}")
    print(f"  worst stage: {s.top_bottleneck_stage} ({s.top_bottleneck_stage_days} business days median)"
          f"   top gap: {s.top_gap_reference} ({s.top_gap_findings} findings)")

    print("\nTIER 1 - KPI HEALTH OVERVIEW")
    print(res["tier1"][["kpi_id", "kpi_name", "current_value", "target", "unit",
                        "variance_vs_target_pct", "health", "denominator_cases"]]
          .to_string(index=False))

    print("\nTIER 2 - CRITICAL DIAGNOSTIC DRIVERS")
    crit = res["tier2"][res["tier2"]["health"] == "Critical"]
    print((crit[["kpi_id", "kpi_name", "current_value", "target", "unit", "feeds_tier1_kpi",
                 "gap_reference"]].to_string(index=False)) if len(crit) else "  none")

    print("\nSTAGE BREAKDOWN")
    print(res["stages"][["case_type", "stage_code", "stage_name", "cases", "median_elapsed_days",
                         "p90_elapsed_days", "share_of_cycle_pct", "stage_sla_days",
                         "sla_breach_pct", "revisit_rate_pct", "health"]].to_string(index=False))

    print("\nTOP WAITING ACTIVITIES")
    print(res["activities"][res["activities"]["wait_is_container"] == "N"].head(10)[
        ["activity_name", "actor_persona", "events", "cases_touched_pct", "avg_waiting_min",
         "total_waiting_days", "sla_breach_pct", "top_waiting_reason"]].to_string(index=False))
    cont = res["activities"][res["activities"]["wait_is_container"] == "Y"]
    print(f"  ({len(cont)} container activities excluded from the ranking - their preceding interval "
          f"is a linked case pipeline or a contractual notice period)")

    print("\nREWORK LOOPS")
    print(res["rework_loops"].head(12)[["rework_loop_type", "cases", "events", "cases_pct",
                                        "days_lost", "avg_days_lost_per_affected_case",
                                        "gap_reference"]].to_string(index=False))

    print("\nSTALLED CASES (Kinetic exception queue)")
    if len(res["stalled"]):
        print(res["stalled"].head(15)[["process_instance_id", "case_type", "last_activity_name",
                                       "actor_persona", "open_business_days", "waiting_reason_code",
                                       "recommended_action"]].to_string(index=False))
        print(f"  ... {len(res['stalled'])} stalled cases in total")
    else:
        print("  none")

    print("\nMONTHLY TREND (last 8 months)")
    print(res["monthly_trend"].tail(8)[["month", "requisitions_opened", "requisitions_filled",
                                        "hires", "time_to_fill_days", "approval_lead_time_days",
                                        "sla_attainment_pct", "offer_acceptance_pct",
                                        "withdrawal_rate_pct"]].to_string(index=False))


# ------------------------------------------------------------------ main
def main(a):
    as_of = pd.Timestamp(a.as_of)
    input_dir = a.input_dir if os.path.isabs(a.input_dir) else os.path.join(HERE, a.input_dir)
    output_dir = a.output_dir if os.path.isabs(a.output_dir) else os.path.join(HERE, a.output_dir)
    to_pg = a.target in ("postgres", "both")

    params = {"business_min_per_day": kpi_core.BUSINESS_MIN_PER_DAY,
              "targets": kpi_core.TARGETS,
              "stage_sla_days": kpi_core.STAGE_SLA_DAYS,
              "stalled_threshold_days": kpi_core.STALLED_THRESHOLD_DAYS,
              "persona_rate_per_hour": kpi_core.PERSONA_RATE_PER_HOUR,
              "agency_fee_per_hire": kpi_core.AGENCY_FEE_PER_HIRE,
              "assessment_cost_per_event": kpi_core.ASSESSMENT_COST_PER_EVENT}

    if a.source == "db" or to_pg:
        from db_config import SCHEMA, db
        with db() as conn:
            # the rate card is maintained in persona_master, not in code
            load_rate_card(conn)
            params["persona_rate_per_hour"] = kpi_core.PERSONA_RATE_PER_HOUR
            if a.source == "db":
                src = read_source_db(conn, a.period_from, a.period_to)
                origin = f"PostgreSQL schema '{SCHEMA}'"
            else:
                src = read_source_csv(input_dir, a.period_from, a.period_to)
                origin = f"CSV extract in {input_dir}"
            if src["events"].empty or src["requisitions"].empty:
                raise SystemExit("No data found - load the event log and case masters first "
                                 "(scripts 02 and 03).")
            print(f"Read {len(src['events']):,} events / {len(src['requisitions']):,} requisitions / "
                  f"{len(src['applications']):,} applications from {origin}")

            res = kpi_core.compute_kpis(src, as_of)
            res["summary"]["period_from"] = a.period_from
            res["summary"]["period_to"] = a.period_to

            run_id = create_run(conn, as_of, a.period_from, a.period_to, res["run_stats"], params)
            res["tier1"] = add_trend(conn, res["tier1"], run_id)
            written = write_postgres(conn, run_id, res)
            conn.commit()
            print(f"\nKPI run {run_id} written to PostgreSQL ({as_of:%Y-%m-%d %H:%M})")
            for t, n in written.items():
                print(f"   {t:<28} {n:>6} rows")
            if a.keep_runs:
                removed = prune(conn, a.keep_runs)
                conn.commit()
                if removed:
                    print(f"   pruned {removed} older run(s)")
    else:
        src = read_source_csv(input_dir, a.period_from, a.period_to)
        print(f"Read {len(src['events']):,} events / {len(src['requisitions']):,} requisitions / "
              f"{len(src['applications']):,} applications from the CSV extract in {input_dir}")
        res = kpi_core.compute_kpis(src, as_of)
        res["summary"]["period_from"] = a.period_from
        res["summary"]["period_to"] = a.period_to
        res["tier1"]["previous_value"] = None
        res["tier1"]["trend_direction"] = None
        run_id = 0                      # offline run: no kpi_run control row exists
        print("\nOffline run (no database): run_id 0 is written into the CSV extract")

    if a.target in ("csv", "both"):
        files = write_csv(res, output_dir, run_id)
        print(f"\nOffline CSV extract written to {output_dir}")
        for n, c in files.items():
            print(f"   {n:<36} {c:>6} rows")

    if not a.quiet:
        report(res)

    if a.target in ("postgres", "both"):
        print("\nQuery the latest results with the views: v_kpi_summary_latest, "
              "v_kpi_tier1_latest, v_kpi_tier2_latest, v_root_cause_tree, v_gap_findings")


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Compute Talent Acquisition KPIs and store them in PostgreSQL + an offline CSV extract")
    p.add_argument("--as-of", default="2026-10-19 09:00", help="reporting cut-off for open / stalled cases")
    p.add_argument("--source", choices=["db", "csv"], default="db", help="where to read the source data from")
    p.add_argument("--target", choices=["postgres", "csv", "both"], default="both", help="where to write results")
    p.add_argument("--input-dir", default="data", help="folder holding the CSV extract (for --source csv)")
    p.add_argument("--output-dir", default="data", help="folder for the offline KPI CSVs")
    p.add_argument("--from", dest="period_from", default=None, help="requisitions opened on/after this date")
    p.add_argument("--to", dest="period_to", default=None, help="requisitions opened on/before this date")
    p.add_argument("--keep-runs", type=int, default=0, help="keep only the N most recent runs")
    p.add_argument("--quiet", action="store_true")
    main(p.parse_args())
