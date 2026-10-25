"""
STEP 5 - Talent Acquisition Root Cause Agent (Deloitte Kinetic).

The KPI engine (kpi_core.py + 04_compute_kpis_to_postgres.py) decides WHAT is Critical.
This agent decides WHY. It is a structured diagnostic agent, not a general-purpose analyst:

    PostgreSQL KPI tables
            |
            v
    get_root_cause_evidence()        <- the only tool the agent can call (root_cause_tool.py)
            |
            v
    GPT-5.1 + frozen system prompt   <- root_cause_prompt.py, via the OpenAI Responses API
            |
            v
    strict JSON (text.format)        <- root_cause_schema.py, then validate_output()
            |
            v
    ta_root_causes.json  +  {schema}.root_cause_analysis / root_cause_finding  -> API / dashboard

Every root cause the agent returns has to be backed by a quantitative signal that came out of the
evidence tool; the schema and the post-run validator enforce the parts of that contract a prompt
cannot. The analysis is also persisted so backend/routers/root_cause.py can serve it.

Requires:  pip install openai      (plus psycopg2-binary for the evidence tool)
           export OPENAI_API_KEY=...

Usage:
    python root_cause_agent.py                                  # latest KPI run, every Critical KPI
    python root_cause_agent.py --kpi-ids T1-01 T1-11            # restrict the analysis
    python root_cause_agent.py --run-id 7 --out run7.json
    python root_cause_agent.py --evidence-out evidence.json     # keep the evidence pack for audit
    python root_cause_agent.py --evidence-file evidence.json    # re-run the agent without the DB
    python root_cause_agent.py --dry-run                        # build the evidence pack, call nothing
"""
from __future__ import annotations

import argparse
import json
import sys

import  os
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)
from openai import AzureOpenAI

from dotenv import load_dotenv

from root_cause_prompt import SYSTEM_PROMPT, USER_PROMPT
from root_cause_schema import OUTPUT_FORMAT, validate_output
from root_cause_tool import TOOL_DEFINITION, TOOL_NAME, run_tool

load_dotenv()


MODEL = "gpt-5.1"
EFFORT = "high"                 # none | low | medium | high - GPT-5.1 defaults to none, so set it
MAX_OUTPUT_TOKENS = 64_000      # the answer is a large JSON document
REQUEST_TIMEOUT = 1_800         # seconds: a high-effort reasoning turn over the evidence is slow
MAX_ITERATIONS = 8              # tool calls + continuations; the agent should need 2
DEFAULT_OUT = "ta_root_causes.json"


# ------------------------------------------------------------------ helpers
def _refusal_of(response):
    """Structured outputs can be refused instead of followed - find the refusal text, if any."""
    for item in response.output:
        for block in getattr(item, "content", None) or []:
            if getattr(block, "type", None) == "refusal":
                return block.refusal
    return None


def _parse_json(text):
    """The schema guarantees clean JSON; the fallback path (no schema) may need unfencing."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start:end + 1])


def _usage_line(usage):
    if usage is None:
        return "tokens: n/a"
    parts = [f"in {usage.input_tokens:,}", f"out {usage.output_tokens:,}"]
    cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0)
    reasoning = getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0)
    if cached:
        parts.append(f"cached {cached:,}")
    if reasoning:
        parts.append(f"reasoning {reasoning:,}")
    return "tokens: " + ", ".join(parts)


def create_client():

    client = AzureOpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_version="2025-03-01-preview"  # or the version used by your Azure resource
    )
    return client

# ------------------------------------------------------------------ the agent loop
def run_agent(evidence_provider, model=MODEL, effort=EFFORT, max_output_tokens=MAX_OUTPUT_TOKENS,
              structured_output=True, verbose=True):
    """Drive the tool loop until the model returns the analysis. Returns (result, evidence)."""
    import openai

    # client = openai.OpenAI(timeout=REQUEST_TIMEOUT)
    client = create_client()
    # The instructions + tool definition are byte-identical on every call, so the long prefix is
    # eligible for OpenAI's automatic prompt caching from the second request onwards.
    request = {
        "model": model,
        "instructions": SYSTEM_PROMPT,
        "tools": [TOOL_DEFINITION],
        "reasoning": {"effort": effort},
        "max_output_tokens": max_output_tokens,
    }
    if structured_output:
        request["text"] = {"format": OUTPUT_FORMAT}
    input_list = [{"role": "user", "content": USER_PROMPT}]

    evidence = None
    for iteration in range(1, MAX_ITERATIONS + 1):
        try:
            response = client.responses.create(input=input_list, **request)
        except openai.BadRequestError as e:
            if structured_output and "schema" in str(e).lower():
                print(f"   output schema rejected ({e}); retrying without structured output",
                      file=sys.stderr)
                return run_agent(evidence_provider, model, effort, max_output_tokens,
                                 structured_output=False, verbose=verbose)
            raise

        if verbose:
            print(f"   turn {iteration}: status={response.status}, {_usage_line(response.usage)}")

        if response.status == "incomplete":
            reason = getattr(response.incomplete_details, "reason", "unknown")
            if reason == "max_output_tokens":
                raise RuntimeError(f"The answer was truncated at max_output_tokens="
                                   f"{max_output_tokens:,}. Re-run with a higher "
                                   f"--max-output-tokens or a lower --effort.")
            raise RuntimeError(f"The model returned an incomplete response ({reason}).")

        refusal = _refusal_of(response)
        if refusal:
            raise RuntimeError(f"The model declined the request: {refusal}")

        # Carry the whole output back - including reasoning items, which the model needs on the
        # next turn to continue the same chain of thought instead of restarting it.
        input_list += response.output

        calls = [item for item in response.output if item.type == "function_call"]
        if calls:
            for call in calls:
                if call.name != TOOL_NAME:
                    input_list.append({"type": "function_call_output", "call_id": call.call_id,
                                       "output": f"ERROR: unknown tool '{call.name}'. The only tool "
                                                 f"available is {TOOL_NAME}."})
                    continue
                try:
                    args = json.loads(call.arguments or "{}")
                except json.JSONDecodeError as exc:
                    input_list.append({"type": "function_call_output", "call_id": call.call_id,
                                       "output": f"ERROR: arguments were not valid JSON ({exc})."})
                    continue
                if verbose:
                    print(f"   -> {TOOL_NAME}({json.dumps(args)})")
                try:
                    pack = evidence_provider(args)
                    evidence = getattr(evidence_provider, "last_evidence", pack)
                    payload = json.dumps(pack, separators=(",", ":"))
                    if verbose:
                        print(f"      evidence package: {len(payload):,} chars, "
                              f"{len(pack.get('critical_tier1', []))} Critical Tier 1 KPI(s)")
                    input_list.append({"type": "function_call_output", "call_id": call.call_id,
                                       "output": payload})
                except Exception as exc:              # surface the failure to the agent, don't crash
                    input_list.append({"type": "function_call_output", "call_id": call.call_id,
                                       "output": f"ERROR: the evidence tool failed: {exc}"})
            continue

        text = (response.output_text or "").strip()
        if not text:
            raise RuntimeError("The model finished without returning any text.")
        return _parse_json(text), evidence

    raise RuntimeError(f"Gave up after {MAX_ITERATIONS} turns without a final answer.")


class _DbEvidence:
    """Calls the real tool and remembers the last package so the validator can cross-check it."""

    def __init__(self, run_id=None, kpi_ids=None):
        self.run_id, self.kpi_ids, self.last_evidence = run_id, kpi_ids, None

    def __call__(self, tool_input):
        tool_input = dict(tool_input or {})
        if self.kpi_ids:                                   # operator override wins over the model
            tool_input["kpi_ids"] = self.kpi_ids
        self.last_evidence = run_tool(tool_input, run_id=self.run_id)
        return self.last_evidence


class _FileEvidence:
    """Replays a saved evidence package - lets the agent run with no database in front of it."""

    def __init__(self, path):
        with open(path) as f:
            self.last_evidence = json.load(f)

    def __call__(self, tool_input):
        return self.last_evidence


def _persist_analysis(result, evidence, run_id, model, effort, problems, store_evidence=True):
    """
    Store the analysis in root_cause_analysis / root_cause_finding so the API can serve it.
    Best effort: the JSON file is already written, so an unreachable database must not fail the run.
    """
    try:
        from backend import db as _db
        from backend.services import root_cause_store
        with _db.connection() as conn:
            _db.ensure_root_cause_schema(conn)
            rid = run_id or (evidence or {}).get("run_stats", {}).get("run_id") or _db.latest_run_id(conn)
            analysis_id, findings = root_cause_store.save_analysis(
                conn, result, evidence if store_evidence else None, rid,
                model=model, reasoning_effort=effort, problems=problems)
            conn.commit()
            return analysis_id, findings
    except Exception as exc:
        print(f"   (analysis not persisted for the API: {exc})", file=sys.stderr)
        return None, 0


# ------------------------------------------------------------------ main
def main():
    p = argparse.ArgumentParser(description="Talent Acquisition Root Cause Agent (OpenAI GPT-5.1)")
    p.add_argument("--kpi-ids", nargs="*", default=None,
                   help="Tier 1 KPI ids to analyse (default: every Critical Tier 1 KPI)")
    p.add_argument("--run-id", type=int, default=None, help="KPI run to analyse (default: latest)")
    p.add_argument("--model", default=MODEL)
    p.add_argument("--effort", default=EFFORT, choices=["none", "low", "medium", "high"],
                   help="reasoning effort (GPT-5.1 defaults to none; this agent defaults to high)")
    p.add_argument("--max-output-tokens", type=int, default=MAX_OUTPUT_TOKENS)
    p.add_argument("--out", default=DEFAULT_OUT, help="where to write the analysis")
    p.add_argument("--evidence-out", default=None, help="also write the evidence package used")
    p.add_argument("--evidence-file", default=None,
                   help="replay a saved evidence package instead of reading the database")
    p.add_argument("--no-structured-output", action="store_true",
                   help="do not constrain the answer with the JSON schema (diagnostic use)")
    p.add_argument("--no-persist", action="store_true",
                   help="do not store the analysis in root_cause_analysis / root_cause_finding")
    p.add_argument("--no-store-evidence", action="store_true",
                   help="persist the analysis but not the evidence package alongside it")
    p.add_argument("--dry-run", action="store_true",
                   help="build the evidence package and print a summary; call no model")
    a = p.parse_args()

    provider = _FileEvidence(a.evidence_file) if a.evidence_file else _DbEvidence(a.run_id, a.kpi_ids)

    if a.dry_run:
        try:
            pack = provider({"kpi_ids": a.kpi_ids})
        except ModuleNotFoundError as exc:
            raise SystemExit(f"The evidence tool needs a PostgreSQL driver: {exc}. "
                             f"Install it with 'pip install psycopg2-binary', or replay a saved "
                             f"package with --evidence-file.")
        except Exception as exc:
            raise SystemExit(f"The evidence tool could not read the KPI tables: {exc}")
        crit = pack.get("critical_tier1", [])
        print(f"KPI run            : {pack['run_stats'].get('run_id')} "
              f"(as of {pack['run_stats'].get('as_of_timestamp')})")
        print(f"Critical Tier 1    : {', '.join(k['kpi_id'] for k in crit) or 'none'}")
        for key in ("tier1_all", "linked_tier2", "root_cause_links", "gaps", "gap_detections",
                    "stages", "activities", "rework_loops", "monthly_trend"):
            print(f"{key:<19}: {len(pack.get(key) or [])} rows")
        for key in ("stalled", "recruiter_load", "segments", "case_metrics"):
            print(f"{key:<19}: {'present' if pack.get(key) else 'omitted'}")
        print(f"package size       : {len(json.dumps(pack)):,} chars")
        for n in pack.get("notes", []):
            print(f"note               : {n}")
        if a.evidence_out:
            with open(a.evidence_out, "w") as f:
                json.dump(pack, f, indent=2)
            print(f"evidence written   : {a.evidence_out}")
        return

    print(f"Root Cause Agent - model {a.model}, reasoning effort {a.effort}")
    result, evidence = run_agent(provider, model=a.model, effort=a.effort,
                                 max_output_tokens=a.max_output_tokens,
                                 structured_output=not a.no_structured_output)
    evidence = evidence or getattr(provider, "last_evidence", None)

    problems = validate_output(result, evidence)
    with open(a.out, "w") as f:
        json.dump(result, f, indent=2)
    if a.evidence_out and evidence:
        with open(a.evidence_out, "w") as f:
            json.dump(evidence, f, indent=2)

    if not a.no_persist:
        analysis_id, findings = _persist_analysis(
            result, evidence, a.run_id, a.model, a.effort, problems,
            store_evidence=not a.no_store_evidence)
        if analysis_id:
            print(f"   analysis {analysis_id} stored with {findings} finding(s) - available at "
                  f"GET /api/root-cause/latest")

    summary = (result.get("frontend_summary") or {}).get("analysis_summary") or {}
    print(f"\nAnalysis written to {a.out}")
    for kpi in summary.get("critical_kpis", []):
        print(f"   {kpi['kpi_id']} {kpi['kpi_name']:<44} {len(kpi.get('top_root_causes', []))} root causes")
        for rc in kpi.get("top_root_causes", []):
            print(f"      {rc['rank']}. [{rc['confidence']:<6}] {rc['root_cause']}")
    if a.evidence_out and evidence:
        print(f"Evidence package written to {a.evidence_out}")

    if problems:
        print(f"\n{len(problems)} validation problem(s) - the JSON was still written:", file=sys.stderr)
        for msg in problems:
            print(f"   - {msg}", file=sys.stderr)
        sys.exit(1)
    print("Validation: passed")


if __name__ == "__main__":
    main()
