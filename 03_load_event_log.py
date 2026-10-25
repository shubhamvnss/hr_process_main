"""
STEP 3 - Load the Talent Acquisition EVENT LOG and the derived analytical inputs into PostgreSQL.

Reads the event-log extract (110k+ rows) and inserts it into {schema}.event_log in chunks,
converting empty strings to NULL for the open intervals, then loads the three derived tables
that are produced with the log. Referential and quality checks run afterwards.

    event_log               <- ta_event_log.csv
    stage_transitions       <- ta_stage_transitions.csv
    recruiter_load_snapshot <- ta_recruiter_load_snapshot.csv
    gap_detection           <- ta_gap_detection.csv

Usage:
    python 03_load_event_log.py
    python 03_load_event_log.py --input-dir data --truncate --chunk-size 20000
"""
import argparse
import os

import pandas as pd

from db_config import SCHEMA, bulk_insert, db

HERE = os.path.dirname(os.path.abspath(__file__))

EVENT_COLUMNS = [
    "event_id", "process_instance_id", "case_type", "requisition_id", "application_id",
    "candidate_surrogate_id", "activity_name", "activity_stage", "stage_code", "stage_sequence",
    "event_timestamp", "event_end_timestamp", "actor_id_pseudonymous", "actor_persona",
    "source_system", "source_event_id", "status_before", "status_after", "outcome_code",
    "reason_code", "duration_minutes", "waiting_time_minutes", "waiting_reason_code",
    "sla_code", "sla_target_minutes", "sla_measured_minutes", "sla_breach_flag",
    "sla_overrun_minutes", "rework_flag", "repeat_sequence_number", "manual_touch_flag",
    "handoff_flag", "exception_flag", "gap_reference", "business_unit", "job_family",
    "location", "job_level", "priority", "recruiter_id_pseudonymous",
    "hiring_manager_id_pseudonymous", "source_channel", "data_quality_flag", "extraction_batch_id",
]
TIMESTAMP_COLS = ["event_timestamp", "event_end_timestamp"]
NUMERIC_COLS = ["stage_sequence", "duration_minutes", "waiting_time_minutes", "sla_target_minutes",
                "sla_measured_minutes", "sla_overrun_minutes", "repeat_sequence_number"]

DERIVED = [
    ("ta_stage_transitions.csv", "stage_transitions",
     ["entry_timestamp", "exit_timestamp"],
     ["stage_sequence", "stage_occurrence", "events_in_stage", "elapsed_business_minutes",
      "elapsed_business_days", "stage_sla_target_minutes"]),
    ("ta_recruiter_load_snapshot.csv", "recruiter_load_snapshot",
     ["snapshot_date"],
     ["open_requisitions", "weighted_load", "requisitions_open_over_60_days"]),
    ("ta_gap_detection.csv", "gap_detection",
     ["detected_at"],
     ["measured_value", "threshold_value", "evidence_event_count"]),
]


def read_csv(input_dir, name):
    path = os.path.join(input_dir, name)
    if not os.path.exists(path):
        raise SystemExit(f"Missing source extract: {path}  (run generate_ta_event_log.py first)")
    return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])


def prepare(df, cols, ts_cols, num_cols):
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise SystemExit(f"Extract is missing expected column(s): {', '.join(missing)}")
    d = df[cols].copy()
    for c in ts_cols:
        if c in d.columns:
            d[c] = pd.to_datetime(d[c], errors="coerce")        # '' -> NaT -> NULL
    for c in num_cols:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    return d.where(pd.notna(d), None)


def table_columns(conn, table):
    with conn.cursor() as cur:
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_schema = %s AND table_name = %s AND column_name <> 'id'
                       ORDER BY ordinal_position;""", (SCHEMA, table))
        return [r[0] for r in cur.fetchall()]


def quality_checks(conn):
    q = lambda sql: pd.read_sql_query(sql, conn)
    print("\nQuality checks")
    print("-" * 78)
    print(q(f"""
        SELECT COUNT(*) AS events,
               COUNT(DISTINCT process_instance_id) AS cases,
               COUNT(DISTINCT requisition_id) AS requisitions,
               MIN(event_timestamp)::date AS first_event,
               MAX(event_timestamp)::date AS last_event
        FROM {SCHEMA}.event_log;""").to_string(index=False))

    checks = {
        "events with no requisition in requisition_master": f"""
            SELECT COUNT(*) FROM {SCHEMA}.event_log e
            LEFT JOIN {SCHEMA}.requisition_master r USING (requisition_id)
            WHERE r.requisition_id IS NULL;""",
        "application events with no application_master row": f"""
            SELECT COUNT(*) FROM {SCHEMA}.event_log e
            LEFT JOIN {SCHEMA}.application_master a USING (application_id)
            WHERE e.application_id IS NOT NULL AND a.application_id IS NULL;""",
        "events with an unknown activity": f"""
            SELECT COUNT(*) FROM {SCHEMA}.event_log e
            LEFT JOIN {SCHEMA}.activity_master m ON m.activity_name = e.activity_name
            WHERE m.activity_name IS NULL;""",
        "events with an unknown sla_code": f"""
            SELECT COUNT(*) FROM {SCHEMA}.event_log e
            LEFT JOIN {SCHEMA}.sla_definitions s USING (sla_code)
            WHERE e.sla_code IS NOT NULL AND s.sla_code IS NULL;""",
        "event_end_timestamp before event_timestamp": f"""
            SELECT COUNT(*) FROM {SCHEMA}.event_log
            WHERE event_end_timestamp IS NOT NULL AND event_end_timestamp < event_timestamp;""",
        "breach flagged without an SLA definition": f"""
            SELECT COUNT(*) FROM {SCHEMA}.event_log
            WHERE sla_breach_flag = 'Y' AND sla_code IS NULL;""",
        "breach flag disagreeing with measured vs target": f"""
            SELECT COUNT(*) FROM {SCHEMA}.event_log WHERE sla_code IS NOT NULL
              AND (sla_measured_minutes > sla_target_minutes) <> (sla_breach_flag = 'Y');""",
        "gap_reference pre-labelled in the source log (must be 0)": f"""
            SELECT COUNT(*) FROM {SCHEMA}.event_log WHERE gap_reference IS NOT NULL;""",
        "gap findings citing an unknown GAP id": f"""
            SELECT COUNT(*) FROM {SCHEMA}.gap_detection d
            LEFT JOIN {SCHEMA}.gap_mapping g USING (gap_reference)
            WHERE g.gap_reference IS NULL;""",
        "stage transitions with no matching case": f"""
            SELECT COUNT(*) FROM {SCHEMA}.stage_transitions s
            WHERE NOT EXISTS (SELECT 1 FROM {SCHEMA}.event_log e
                              WHERE e.process_instance_id = s.process_instance_id);""",
    }
    failed = 0
    for label, sql in checks.items():
        n = int(q(sql).iloc[0, 0])
        ok = (n == 0)
        failed += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:<58} {n}")
    print("-" * 78)
    print("All quality checks passed" if not failed else f"{failed} quality check(s) FAILED")
    return failed


def main(a):
    input_dir = a.input_dir if os.path.isabs(a.input_dir) else os.path.join(HERE, a.input_dir)
    with db() as conn:
        if a.truncate:
            with conn.cursor() as cur:
                cur.execute(f"TRUNCATE TABLE {SCHEMA}.event_log, {SCHEMA}.stage_transitions, "
                            f"{SCHEMA}.recruiter_load_snapshot, {SCHEMA}.gap_detection CASCADE;")
            print("Truncated event_log, stage_transitions, recruiter_load_snapshot, gap_detection")

        ev = read_csv(input_dir, "ta_event_log.csv")
        rows = prepare(ev, EVENT_COLUMNS, TIMESTAMP_COLS, NUMERIC_COLS)
        total = 0
        for i in range(0, len(rows), a.chunk_size):
            total += bulk_insert(conn, f"{SCHEMA}.event_log",
                                 rows.iloc[i:i + a.chunk_size], EVENT_COLUMNS)
            print(f"   event_log            {total:>7} / {len(rows)} rows", end="\r")
        print(f"   {'event_log':<22} {total:>7} rows   <- ta_event_log.csv")

        for name, table, ts_cols, num_cols in DERIVED:
            df = read_csv(input_dir, name)
            cols = [c for c in table_columns(conn, table) if c in df.columns]
            n = bulk_insert(conn, f"{SCHEMA}.{table}",
                            prepare(df, cols, ts_cols, num_cols), cols)
            print(f"   {table:<22} {n:>7} rows   <- {name}")

        conn.commit()
        failed = quality_checks(conn)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Load the Talent Acquisition event log into PostgreSQL")
    p.add_argument("--input-dir", default="data", help="folder holding the CSV extract")
    p.add_argument("--truncate", action="store_true", help="truncate the target tables first")
    p.add_argument("--chunk-size", type=int, default=20000)
    main(p.parse_args())
