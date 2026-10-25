"""
Shared PostgreSQL connection helper for the Talent Acquisition / Deloitte Kinetic scripts.

Connection settings come from environment variables (with local defaults):

    export PGHOST=localhost
    export PGPORT=5432
    export PGDATABASE=kinetic
    export PGUSER=postgres
    export PGPASSWORD=postgres
    export PGSCHEMA=talent_acquisition   # all tables are created inside this schema

Requires:  pip install psycopg2-binary pandas numpy
"""
import os
import warnings
from contextlib import contextmanager

# pandas warns when read_sql_query is given a raw DBAPI connection; psycopg2 works fine here
warnings.filterwarnings("ignore", message=".*only supports SQLAlchemy connectable.*")

import psycopg2
import psycopg2.extras

PG = dict(
    host=os.getenv("PGHOST", "agentic200326.postgres.database.azure.com"),
    port=int(os.getenv("PGPORT", "5432")),
    dbname=os.getenv("PGDATABASE", "deloitte-kinetic"),
    user=os.getenv("PGUSER", "yaagav"),
    password=os.getenv("PGPASSWORD", "Agentic!1001"),
)
# SCHEMA = os.getenv("PGSCHEMA", "hr_process")
SCHEMA = "hr_process"

def get_conn():
    conn = psycopg2.connect(**PG)
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA};")
        cur.execute(f"SET search_path TO {SCHEMA}, public;")
    conn.commit()
    return conn


@contextmanager
def db():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _clean(v):
    """Convert pandas/numpy values to plain Python for psycopg2 (NaN/NaT -> NULL)."""
    try:
        import pandas as pd
        if v is None or (not isinstance(v, (str, bytes, list, dict)) and pd.isna(v)):
            return None
    except Exception:
        pass
    if hasattr(v, "item"):          # numpy scalar
        v = v.item()
    return v


def bulk_insert(conn, table, rows, columns=None, page_size=5000):
    """
    Fast multi-row insert.  rows = list[dict] or a pandas DataFrame.
    Empty strings for timestamp / numeric columns are converted to NULL by the caller
    (the loaders do this) - here we only map NaN/NaT/None to NULL.
    """
    import pandas as pd
    if isinstance(rows, pd.DataFrame):
        columns = columns or list(rows.columns)
        data = [tuple(_clean(v) for v in rec) for rec in rows[columns].itertuples(index=False, name=None)]
    else:
        if not rows:
            return 0
        columns = columns or list(rows[0].keys())
        data = [tuple(_clean(r.get(c)) for c in columns) for r in rows]
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s"
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, data, page_size=page_size)
    return len(data)


def truncate(conn, *tables):
    with conn.cursor() as cur:
        for t in tables:
            cur.execute(f"TRUNCATE TABLE {t} CASCADE;")


def read_df(conn, sql, params=None):
    import pandas as pd
    return pd.read_sql_query(sql, conn, params=params)
