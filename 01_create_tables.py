"""
STEP 1 - Create all PostgreSQL tables for the Talent Acquisition / Deloitte Kinetic backend.

Executes schema.sql (reference tables, requisition_master, application_master, event_log,
the derived analytical inputs, the KPI Tier-1 / Tier-2 tables and the reporting views) plus
backend/schema_root_cause.sql (the Root Cause Agent output tables) inside the target schema.

Usage:
    python 01_create_tables.py                  # create if not exists
    python 01_create_tables.py --drop           # DROP the schema first, then recreate (destructive)
    python 01_create_tables.py --list           # list the objects afterwards
"""
import argparse
import os

from db_config import PG, SCHEMA, get_conn

HERE = os.path.dirname(os.path.abspath(__file__))


def create_tables(drop=False, list_after=True):
    with open(os.path.join(HERE, "schema.sql")) as f:
        ddl = f.read().replace("{schema}", SCHEMA)

    # Root Cause Agent output tables. Kept in their own file next to the API that serves them;
    # the API also applies it on startup, so an existing database picks the tables up without
    # a rebuild.
    rc_sql = os.path.join(HERE, "backend", "schema_root_cause.sql")
    if os.path.exists(rc_sql):
        with open(rc_sql) as f:
            ddl += "\n" + f.read().replace("{schema}", SCHEMA)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if drop:
                print(f"Dropping schema {SCHEMA} ...")
                cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE;")
                cur.execute(f"CREATE SCHEMA {SCHEMA};")
            cur.execute(ddl)
        conn.commit()
        print(f"Schema '{SCHEMA}' ready on {PG['host']}:{PG['port']}/{PG['dbname']}")

        if list_after:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT table_name, table_type
                    FROM information_schema.tables
                    WHERE table_schema = %s
                    ORDER BY table_type DESC, table_name;
                """, (SCHEMA,))
                rows = cur.fetchall()
            print(f"\n{len(rows)} objects created:")
            for name, ttype in rows:
                print(f"   {'VIEW ' if ttype == 'VIEW' else 'TABLE'}  {SCHEMA}.{name}")
    finally:
        conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Create the Talent Acquisition Kinetic schema in PostgreSQL")
    p.add_argument("--drop", action="store_true", help="drop and recreate the schema (destroys all data)")
    p.add_argument("--list", dest="list_after", action="store_true", default=True)
    a = p.parse_args()
    create_tables(drop=a.drop, list_after=a.list_after)
