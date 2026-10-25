"""
backend.db - thin PostgreSQL helpers for the Talent Acquisition Root Cause API.

Reuses the project's existing connection handling (db_config.py, raw psycopg2) rather than
introducing an ORM - the rest of the pipeline is plain psycopg2 + schema.sql and there is no
reason for this service to disagree with it.

psycopg2 is imported lazily so the pure-logic modules (the store's flattening logic, the schema
validator) can be imported and unit-tested without a database driver present.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import os
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
ROOT_CAUSE_SQL = os.path.join(HERE, "schema_root_cause.sql")


def schema() -> str:
    from db_config import SCHEMA
    return SCHEMA


@contextmanager
def connection():
    """Open a connection using the project's PG* environment configuration."""
    from db_config import db
    with db() as conn:
        yield conn


def clean(v):
    """Make psycopg2 values JSON-serialisable (Decimal -> number, timestamps -> ISO strings)."""
    if isinstance(v, decimal.Decimal):
        f = float(v)
        return int(f) if f.is_integer() else round(f, 4)
    if isinstance(v, _dt.datetime):
        return v.isoformat(sep=" ", timespec="minutes")
    if isinstance(v, _dt.date):
        return v.isoformat()
    return v


def rows(conn, sql, params=None):
    """Run a query and return a list of plain, JSON-safe dicts."""
    import psycopg2.extras
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [{k: clean(v) for k, v in dict(r).items()} for r in cur.fetchall()]


def one(conn, sql, params=None):
    found = rows(conn, sql, params)
    return found[0] if found else None


def execute(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def ensure_root_cause_schema(conn) -> None:
    """Create the root-cause tables if they are missing (idempotent, safe on every startup)."""
    with open(ROOT_CAUSE_SQL) as f:
        ddl = f.read().replace("{schema}", schema())
    with conn.cursor() as cur:
        cur.execute(ddl)


def latest_run_id(conn):
    row = one(conn, f"SELECT MAX(run_id) AS run_id FROM {schema()}.kpi_run;")
    return (row or {}).get("run_id")


def latest_analysis_id(conn, run_id=None):
    sql = f"SELECT MAX(analysis_id) AS analysis_id FROM {schema()}.root_cause_analysis"
    params = None
    if run_id is not None:
        sql += " WHERE run_id = %s"
        params = [run_id]
    return (one(conn, sql + ";", params) or {}).get("analysis_id")
