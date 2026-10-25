"""
STEP 2 - Load the master / reference data and the two case masters into PostgreSQL.

Loads, in dependency order:
    activity_master      <- ta_activity_master.csv
    sla_definitions      <- ta_sla_definitions.csv
    gap_mapping          <- ta_gap_mapping.csv
    kpi_catalogue_tier1  <- ta_kpi_tier1.csv          (BRD definitions, not computed values)
    kpi_catalogue_tier2  <- ta_kpi_tier2.csv          (BRD definitions, not computed values)
    persona_master       <- derived from activity_master + the rate card in kpi_core
    data_dictionary      <- ta_data_dictionary.csv
    requisition_master   <- ta_requisition_master.csv
    application_master   <- ta_application_master.csv

Everything is written into PostgreSQL - no CSV is produced.

Usage:
    python 02_load_master_data.py
    python 02_load_master_data.py --input-dir data --truncate      # reload from scratch
    python 02_load_master_data.py --reference-only                 # skip the two case masters
"""
import argparse
import os

import pandas as pd

import kpi_core
from db_config import SCHEMA, bulk_insert, db, truncate

HERE = os.path.dirname(os.path.abspath(__file__))

PERSONA_GROUP = {
    "Business Manager": "Business", "Hiring Manager": "Business",
    "Approving Business Leader": "Business", "Workforce Planning Analyst": "Workforce Planning",
    "Recruiter": "Talent Acquisition", "Sourcer": "Talent Acquisition",
    "TA Operations Coordinator": "TA Operations", "Recruiting Coordinator": "TA Operations",
    "Interview Panel": "Interview Panel", "Assessment Coordinator": "Assessment Operations",
    "Compensation Analyst": "Compensation", "Compensation Approver": "Compensation",
    "HR Operations": "HR Operations", "Candidate": "Candidate",
    "System / Integration": "Technology",
}

# csv file -> (table, column list in schema order)
LOADS = [
    ("ta_activity_master.csv", "activity_master", [
        "activity_name", "stage_code", "activity_stage", "stage_sequence",
        "primary_source_system", "primary_persona", "manual_touch_by_default",
        "rework_activity", "exception_activity", "closes_sla_codes", "description"]),
    ("ta_sla_definitions.csv", "sla_definitions", [
        "sla_code", "sla_name", "from_activity", "to_activity",
        "target_business_days_standard", "target_minutes_standard", "target_minutes_urgent",
        "target_minutes_critical", "stage_code", "applies_to", "basis", "status"]),
    ("ta_gap_mapping.csv", "gap_mapping", [
        "gap_reference", "stage_or_domain", "gap_definition", "tier2_evidence", "tier1_impact",
        "illustrative_detection_rule", "rca_hypothesis_not_fact", "intervention_options"]),
    ("ta_kpi_tier1.csv", "kpi_catalogue_tier1", [
        "kpi_id", "kpi", "definition", "primary_data", "direction", "tier2_drivers"]),
    ("ta_kpi_tier2.csv", "kpi_catalogue_tier2", [
        "kpi_id", "diagnostic_kpi", "definition", "feeds_tier1"]),
    ("ta_data_dictionary.csv", "data_dictionary", [
        "table_name", "field", "type", "definition"]),
]
CASE_MASTERS = [
    ("ta_requisition_master.csv", "requisition_master"),
    ("ta_application_master.csv", "application_master"),
]

TABLES_IN_RELOAD_ORDER = [
    "application_master", "requisition_master", "data_dictionary", "persona_master",
    "kpi_catalogue_tier2", "kpi_catalogue_tier1", "gap_mapping", "sla_definitions",
    "activity_master",
]


def read_csv(input_dir, name):
    path = os.path.join(input_dir, name)
    if not os.path.exists(path):
        raise SystemExit(f"Missing source extract: {path}  (run generate_ta_event_log.py first)")
    return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])


def table_columns(conn, table):
    with conn.cursor() as cur:
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_schema = %s AND table_name = %s
                       ORDER BY ordinal_position;""", (SCHEMA, table))
        return [r[0] for r in cur.fetchall()]


def prepare(df, cols):
    """Keep the schema columns, blank -> NULL, and fail loudly on a missing column."""
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise SystemExit(f"Extract is missing expected column(s): {', '.join(missing)}")
    d = df[cols].copy()
    return d.where(pd.notna(d), None)


def build_personas(activities):
    """Persona catalogue + hourly rate card, so the KPI engine prices effort from the database."""
    personas = sorted(set(activities["primary_persona"]) | set(kpi_core.PERSONA_RATE_PER_HOUR))
    return [{"persona_id": f"P{i:02d}", "persona": p,
             "persona_group": PERSONA_GROUP.get(p, "Other"),
             "hourly_rate": kpi_core.PERSONA_RATE_PER_HOUR.get(p, 0)}
            for i, p in enumerate(personas, start=1)]


def main(a):
    input_dir = a.input_dir if os.path.isabs(a.input_dir) else os.path.join(HERE, a.input_dir)
    with db() as conn:
        if a.truncate:
            truncate(conn, *[f"{SCHEMA}.{t}" for t in TABLES_IN_RELOAD_ORDER])
            print(f"Truncated {len(TABLES_IN_RELOAD_ORDER)} tables in {SCHEMA}")

        activities = None
        for name, table, cols in LOADS:
            df = read_csv(input_dir, name)
            if table == "activity_master":
                activities = df
            n = bulk_insert(conn, f"{SCHEMA}.{table}", prepare(df, cols), cols)
            print(f"   {table:<22} {n:>6} rows   <- {name}")

        personas = build_personas(activities)
        n = bulk_insert(conn, f"{SCHEMA}.persona_master", personas,
                        ["persona_id", "persona", "persona_group", "hourly_rate"])
        print(f"   {'persona_master':<22} {n:>6} rows   <- activity_master + kpi_core rate card")

        if not a.reference_only:
            for name, table in CASE_MASTERS:
                df = read_csv(input_dir, name)
                cols = [c for c in table_columns(conn, table) if c in df.columns]
                extra = [c for c in df.columns if c not in cols]
                if extra:
                    print(f"   note: {len(extra)} column(s) in {name} are not in {table}: "
                          f"{', '.join(extra[:6])}{' ...' if len(extra) > 6 else ''}")
                n = bulk_insert(conn, f"{SCHEMA}.{table}", prepare(df, cols), cols)
                print(f"   {table:<22} {n:>6} rows   <- {name}")

        counts = pd.read_sql_query(f"""
            SELECT 'activity_master' AS table_name, COUNT(*) FROM {SCHEMA}.activity_master
            UNION ALL SELECT 'sla_definitions', COUNT(*) FROM {SCHEMA}.sla_definitions
            UNION ALL SELECT 'gap_mapping', COUNT(*) FROM {SCHEMA}.gap_mapping
            UNION ALL SELECT 'persona_master', COUNT(*) FROM {SCHEMA}.persona_master
            UNION ALL SELECT 'requisition_master', COUNT(*) FROM {SCHEMA}.requisition_master
            UNION ALL SELECT 'application_master', COUNT(*) FROM {SCHEMA}.application_master;""", conn)
        print("\nMaster data loaded")
        print(counts.to_string(index=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Load the Talent Acquisition master data into PostgreSQL")
    p.add_argument("--input-dir", default="data", help="folder holding the CSV extract")
    p.add_argument("--truncate", action="store_true", help="truncate the target tables first")
    p.add_argument("--reference-only", action="store_true", help="skip requisition / application master")
    main(p.parse_args())
