"""
Run the whole Talent Acquisition / Deloitte Kinetic pipeline in one command.

    create tables  ->  load master data  ->  load event log  ->  compute & store KPIs

Two source modes:
    --source csv        load the CSV extract from --input-dir (default)
    --source generate   regenerate the synthetic extract first, then load it

Usage:
    python run_all.py                                   # load data/ and compute the KPIs
    python run_all.py --recreate                        # drop and rebuild the schema first
    python run_all.py --source generate --recreate      # regenerate 1200 requisitions, then load
    python run_all.py --as-of "2026-10-19 09:00"
    python run_all.py --offline                         # no database: KPIs from CSV to CSV
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def run(cmd, cwd=HERE):
    print(f"\n{'=' * 78}\n$ {' '.join(cmd)}\n{'=' * 78}")
    r = subprocess.run([sys.executable] + cmd, cwd=cwd)
    if r.returncode != 0:
        sys.exit(f"Step failed: {' '.join(cmd)}")


def main(a):
    if a.source == "generate":
        run(["generate_ta_event_log.py"])
        run(["validate_ta_event_log.py"])

    if a.offline:
        run(["04_compute_kpis_to_postgres.py", "--source", "csv", "--target", "csv",
             "--as-of", a.as_of, "--input-dir", a.input_dir, "--output-dir", a.input_dir]
            + (["--quiet"] if a.quiet else []))
        print("\nOffline pipeline complete - KPI CSVs written, no database touched.")
        return

    run(["01_create_tables.py"] + (["--drop"] if a.recreate else []))
    run(["02_load_master_data.py", "--input-dir", a.input_dir, "--truncate"])
    run(["03_load_event_log.py", "--input-dir", a.input_dir, "--truncate"])
    run(["04_compute_kpis_to_postgres.py", "--as-of", a.as_of, "--target", "both",
         "--output-dir", a.input_dir] + (["--quiet"] if a.quiet else []))
    print("\nPipeline complete.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Run the full Talent Acquisition PostgreSQL pipeline")
    p.add_argument("--source", choices=["csv", "generate"], default="csv")
    p.add_argument("--input-dir", default="data", help="folder holding the CSV extract")
    p.add_argument("--as-of", default="2026-10-19 09:00")
    p.add_argument("--recreate", action="store_true", help="drop and recreate the schema first")
    p.add_argument("--offline", action="store_true", help="compute KPIs from CSV to CSV, no database")
    p.add_argument("--quiet", action="store_true")
    main(p.parse_args())
