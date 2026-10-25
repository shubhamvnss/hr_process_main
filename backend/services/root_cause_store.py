"""
backend.services.root_cause_store - persistence and retrieval for the Root Cause Agent output.

Written by root_cause_agent.py, read by backend.routers.root_cause. The flattening logic
(flatten_findings) is a pure function so it can be tested without a database.

Finding ids are stable and addressable: RC-<tier1 kpi id>-<rank>, e.g. RC-T1-01-2. They are
derived from the analysis itself rather than from a sequence, so the same root cause keeps the
same id across re-runs of the same KPI run and the frontend can deep-link to it.
"""
from __future__ import annotations

import json

from .. import db


# ------------------------------------------------------------------ flattening (pure)
def finding_id(tier1_kpi_id: str, rank) -> str:
    return f"RC-{tier1_kpi_id}-{rank}"


def _join(values, sep=";"):
    return sep.join(str(v).strip() for v in (values or []) if str(v).strip()) or None


def flatten_findings(result: dict, run_id=None) -> list[dict]:
    """Project the agent's answer into one row per ranked root cause.

    The detailed block (tier1_root_causes) is authoritative; the frontend block supplies the
    business-facing summary wording. They are matched on the root_cause text, which the prompt
    and validator require to be identical in both.
    """
    front_by_kpi = {}
    for kpi in ((result.get("frontend_summary") or {}).get("analysis_summary") or {}).get(
            "critical_kpis") or []:
        front_by_kpi[kpi.get("kpi_id")] = {
            "kpi": kpi,
            "causes": {(rc.get("root_cause") or "").strip(): rc
                       for rc in kpi.get("top_root_causes") or []},
        }

    out = []
    for block in result.get("tier1_root_causes") or []:
        t1 = block.get("tier1_kpi") or {}
        kid = t1.get("kpi_id")
        front = front_by_kpi.get(kid, {})
        fkpi = front.get("kpi") or {}
        for rc in block.get("root_causes") or []:
            text = (rc.get("root_cause") or "").strip()
            frc = (front.get("causes") or {}).get(text, {})
            affected = rc.get("affected_cases") or frc.get("affected_cases") or {}
            out.append({
                "finding_id": finding_id(kid, rc.get("rank")),
                "run_id": run_id,
                "tier1_kpi_id": kid,
                "tier1_kpi_name": t1.get("kpi_name") or fkpi.get("kpi_name"),
                "tier1_current_value": t1.get("current_value"),
                "tier1_target": t1.get("target"),
                "tier1_unit": t1.get("unit"),
                "tier1_variance_pct": t1.get("variance_vs_target_pct"),
                "tier1_cohort_rule": t1.get("cohort_rule") or fkpi.get("cohort_rule"),
                "rank": rc.get("rank"),
                "root_cause": text,
                "root_cause_type": rc.get("root_cause_type"),
                "confidence": rc.get("confidence"),
                "root_cause_summary": frc.get("root_cause_summary"),
                "impact_summary": frc.get("impact_summary"),
                "causal_explanation": rc.get("causal_explanation"),
                "gap_references": _join(rc.get("gap_references") or frc.get("gap_references")),
                "linked_tier2_kpis": _join([t.get("kpi_id") for t in rc.get("tier2_evidence") or []]
                                           or frc.get("linked_tier2_kpis")),
                "affected_case_type": affected.get("case_type"),
                "affected_cases_count": affected.get("count"),
                "affected_cases_pct": affected.get("percentage"),
                "affected_denominator": affected.get("denominator_description"),
                "evidence_limitations": rc.get("evidence_limitations"),
                "impact_statistics": rc.get("impact_statistics") or [],
                "evidence_chain": rc.get("evidence_chain") or [],
                "symptoms": rc.get("symptoms") or [],
                "contributing_factors": rc.get("contributing_factors") or [],
                "alternative_explanations": rc.get("alternative_explanations") or [],
                "recommended_interventions": rc.get("recommended_interventions") or [],
                "detail_json": rc,
            })
    return out


# columns stored as JSONB - serialised at insert time, returned natively by both read paths
JSONB_FINDING_FIELDS = {"impact_statistics", "evidence_chain", "symptoms", "contributing_factors",
                        "alternative_explanations", "recommended_interventions", "detail_json"}

FINDING_COLUMNS = [
    "analysis_id", "finding_id", "run_id", "tier1_kpi_id", "tier1_kpi_name",
    "tier1_current_value", "tier1_target", "tier1_unit", "tier1_variance_pct",
    "tier1_cohort_rule", "rank", "root_cause", "root_cause_type", "confidence",
    "root_cause_summary", "impact_summary", "causal_explanation", "gap_references",
    "linked_tier2_kpis", "affected_case_type", "affected_cases_count", "affected_cases_pct",
    "affected_denominator", "evidence_limitations", "impact_statistics", "evidence_chain",
    "symptoms", "contributing_factors", "alternative_explanations", "recommended_interventions",
    "detail_json",
]


# ------------------------------------------------------------------ write
def save_analysis(conn, result: dict, evidence=None, run_id=None, model=None,
                  reasoning_effort=None, problems=None):
    """Insert one analysis plus its flattened findings. Returns (analysis_id, findings_count)."""
    s = db.schema()
    fs = (result.get("frontend_summary") or {}).get("analysis_summary") or {}
    findings = flatten_findings(result, run_id)
    evidence_run = ((evidence or {}).get("run_stats") or {}).get("run_id")

    with conn.cursor() as cur:
        cur.execute(f"""
            INSERT INTO {s}.root_cause_analysis
                (run_id, model, reasoning_effort, analysis_title, overall_observation,
                 critical_tier1_count, findings_count, validation_status, validation_problems,
                 evidence_run_id, evidence_json, analysis_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING analysis_id;""",
            (run_id, model, reasoning_effort,
             (result.get("frontend_summary") or {}).get("analysis_title"),
             fs.get("overall_observation"), fs.get("critical_tier1_count"), len(findings),
             "problems" if problems else "passed",
             json.dumps(problems or []),
             evidence_run,
             json.dumps(evidence) if evidence else None,
             json.dumps(result)))
        analysis_id = cur.fetchone()[0]

        if findings:
            import psycopg2.extras
            for f in findings:
                f["analysis_id"] = analysis_id
            def _val(f, c):
                v = f.get(c)
                return json.dumps(v) if c in JSONB_FINDING_FIELDS else v

            psycopg2.extras.execute_values(
                cur,
                f"INSERT INTO {s}.root_cause_finding ({', '.join(FINDING_COLUMNS)}) VALUES %s",
                [tuple(_val(f, c) for c in FINDING_COLUMNS) for f in findings])
    return analysis_id, len(findings)


# ------------------------------------------------------------------ read
ANALYSIS_META = """analysis_id, run_id, created_at, model, reasoning_effort, analysis_title,
                   overall_observation, critical_tier1_count, findings_count, validation_status,
                   validation_problems, evidence_run_id"""


def list_analyses(conn, limit=20):
    return db.rows(conn, f"""SELECT {ANALYSIS_META} FROM {db.schema()}.root_cause_analysis
                             ORDER BY analysis_id DESC LIMIT %s;""", [limit])


def get_analysis(conn, analysis_id=None, run_id=None, include_analysis_json=True):
    s = db.schema()
    cols = ANALYSIS_META + (", analysis_json" if include_analysis_json else "")
    if analysis_id:
        return db.one(conn, f"SELECT {cols} FROM {s}.root_cause_analysis WHERE analysis_id = %s;",
                      [analysis_id])
    if run_id:
        return db.one(conn, f"""SELECT {cols} FROM {s}.root_cause_analysis WHERE run_id = %s
                                ORDER BY analysis_id DESC LIMIT 1;""", [run_id])
    return db.one(conn, f"SELECT {cols} FROM {s}.root_cause_analysis "
                        f"ORDER BY analysis_id DESC LIMIT 1;")


def get_evidence(conn, analysis_id):
    return db.one(conn, f"""SELECT analysis_id, run_id, evidence_run_id, evidence_json
                            FROM {db.schema()}.root_cause_analysis WHERE analysis_id = %s;""",
                  [analysis_id])


def list_findings(conn, analysis_id, kpi_id=None, gap=None, confidence=None,
                  root_cause_type=None, limit=200, detail=False):
    s = db.schema()
    cols = """finding_id, analysis_id, run_id, tier1_kpi_id, tier1_kpi_name, tier1_current_value,
              tier1_target, tier1_unit, tier1_variance_pct, tier1_cohort_rule, rank, root_cause,
              root_cause_type, confidence, root_cause_summary, impact_summary, gap_references,
              linked_tier2_kpis, affected_case_type, affected_cases_count, affected_cases_pct,
              affected_denominator, impact_statistics"""
    if detail:
        cols += ", causal_explanation, evidence_limitations, evidence_chain, symptoms, " \
                "contributing_factors, alternative_explanations, recommended_interventions"
    where, params = ["analysis_id = %s"], [analysis_id]
    if kpi_id:
        where.append("UPPER(tier1_kpi_id) = %s")
        params.append(kpi_id.strip().upper())
    if gap:
        where.append("gap_references ILIKE %s")
        params.append(f"%{gap.strip()}%")
    if confidence:
        where.append("UPPER(confidence) = %s")
        params.append(confidence.strip().upper())
    if root_cause_type:
        where.append("UPPER(root_cause_type) = %s")
        params.append(root_cause_type.strip().upper())
    params.append(limit)
    return db.rows(conn, f"""SELECT {cols} FROM {s}.root_cause_finding
                             WHERE {' AND '.join(where)}
                             ORDER BY tier1_kpi_id, rank LIMIT %s;""", params)


def get_finding(conn, finding_id, analysis_id=None):
    s = db.schema()
    if analysis_id:
        return db.one(conn, f"""SELECT * FROM {s}.root_cause_finding
                                WHERE analysis_id = %s AND finding_id = %s;""",
                      [analysis_id, finding_id])
    return db.one(conn, f"""SELECT * FROM {s}.root_cause_finding WHERE finding_id = %s
                            ORDER BY analysis_id DESC LIMIT 1;""", [finding_id])


def kpi_health(conn, run_id=None):
    """Tier 1 KPI health for one KPI run, straight from kpi_tier1_health + kpi_summary.

    The dashboard needs the full Tier 1 picture - including the Watch and Healthy KPIs the agent
    never analyses - and it needs it before any analysis exists. Reading the KPI tables directly
    keeps the tiles correct in that intermediate state instead of leaving them empty.
    """
    s = db.schema()
    run = db.one(conn, f"""SELECT run_id, as_of_timestamp, period_from, period_to,
                                  requisitions_total, requisitions_filled, requisitions_open,
                                  applications_total, applications_joined, cases_stalled,
                                  events_total
                           FROM {s}.kpi_run
                           {'WHERE run_id = %s' if run_id else ''}
                           ORDER BY run_id DESC LIMIT 1;""", [run_id] if run_id else None)
    if not run:
        return None
    rid = run["run_id"]
    summary = db.one(conn, f"SELECT * FROM {s}.kpi_summary WHERE run_id = %s;", [rid]) or {}
    summary.pop("id", None)
    summary.pop("run_id", None)
    return {
        "run_stats": run,
        "summary": summary,
        "tier1": db.rows(conn, f"""SELECT kpi_id, kpi_name, formula, current_value, target, unit,
                                          better_when, variance_vs_target_pct, health,
                                          trend_direction, previous_value, denominator_cases,
                                          cohort_rule, tier2_drivers
                                   FROM {s}.kpi_tier1_health WHERE run_id = %s
                                   ORDER BY kpi_id;""", [rid]),
    }


def kpi_rollup(conn, analysis_id):
    """One row per Critical Tier 1 KPI with its finding count - the dashboard index."""
    return db.rows(conn, f"""
        SELECT tier1_kpi_id, MIN(tier1_kpi_name) AS tier1_kpi_name,
               MIN(tier1_current_value) AS current_value, MIN(tier1_target) AS target,
               MIN(tier1_unit) AS unit, MIN(tier1_variance_pct) AS variance_vs_target_pct,
               MIN(tier1_cohort_rule) AS cohort_rule,
               COUNT(*) AS root_causes,
               COUNT(*) FILTER (WHERE confidence = 'High')   AS high_confidence,
               COUNT(*) FILTER (WHERE confidence = 'Medium') AS medium_confidence,
               COUNT(*) FILTER (WHERE confidence = 'Low')    AS low_confidence
        FROM {db.schema()}.root_cause_finding WHERE analysis_id = %s
        GROUP BY tier1_kpi_id ORDER BY tier1_kpi_id;""", [analysis_id])


def gap_rollup(conn, analysis_id):
    """How often each GAP id is cited across the findings of one analysis."""
    return db.rows(conn, f"""
        SELECT TRIM(g) AS gap_reference, COUNT(*) AS findings,
               COUNT(DISTINCT tier1_kpi_id) AS tier1_kpis,
               STRING_AGG(DISTINCT tier1_kpi_id, ', ' ORDER BY tier1_kpi_id) AS kpi_ids
        FROM {db.schema()}.root_cause_finding f
        CROSS JOIN LATERAL unnest(string_to_array(COALESCE(f.gap_references, ''), ';')) AS g
        WHERE f.analysis_id = %s AND TRIM(g) <> ''
        GROUP BY TRIM(g) ORDER BY findings DESC;""", [analysis_id])
