/**
 * Centralised API access for the Talent Acquisition dashboard.
 *
 * Unlike the Corporate Loan dashboard - which reads JSON files the pipeline writes into
 * public/data/ - this dashboard reads the Root Cause API directly, so a fresh agent run shows up
 * on reload with no file copying:
 *
 *   VITE_API_BASE_URL   default http://localhost:8010
 *
 * Everything throws ApiError, which carries the HTTP status and the backend's structured detail
 * so the UI can tell "no analysis yet" apart from "the database is down" without parsing strings.
 */

export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") || "http://localhost:8010";

export class ApiError extends Error {
  constructor(message, { status = 0, code = "network_error", detail = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

/**
 * Root causes are addressed by an id derived from the Tier 1 KPI id and the cause's rank. The
 * agent derives it the same way (backend/services/root_cause_store.finding_id), so the dashboard
 * can link straight through without first asking the API what the id is.
 */
export function findingId(kpiId, rank) {
  return `RC-${kpiId}-${rank}`;
}

function query(params) {
  const q = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== null && v !== undefined && v !== "" && v !== false) q.set(k, String(v));
  });
  const s = q.toString();
  return s ? `?${s}` : "";
}

async function request(path, { signal, payload, ...options } = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      // Content-Type is set only when there is a body. On a bodyless GET it describes nothing and
      // makes the request non-simple, so the browser fires a CORS preflight before every single
      // call - a wasted round trip against a read-only API.
      ...(payload === undefined
        ? {}
        : { headers: { "Content-Type": "application/json" }, body: payload }),
      signal,
      ...options,
    });
  } catch (cause) {
    if (cause?.name === "AbortError") throw cause;
    throw new ApiError(
      `Cannot reach the Root Cause API at ${API_BASE_URL}. Is the backend running?`,
      { code: "unreachable" }
    );
  }

  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (!response.ok) {
    const detail = body?.detail ?? null;
    const message =
      (typeof detail === "object" && detail?.message) ||
      (typeof detail === "string" && detail) ||
      `Request failed with status ${response.status}`;
    throw new ApiError(message, {
      status: response.status,
      code: (typeof detail === "object" && detail?.error) || "http_error",
      detail,
    });
  }
  return body;
}

/* ------------------------------------------------------------------ endpoints */

/** Service and data readiness: schema, latest KPI run, latest analysis. */
export function health({ signal } = {}) {
  return request("/api/root-cause/health", { signal });
}

/** Tier 1 KPI health + the run header - the source for the dashboard tiles. */
export function getKpiHealth({ runId = null, signal } = {}) {
  return request(`/api/root-cause/kpi-health${query({ run_id: runId })}`, { signal });
}

/** The full stored analysis: metadata + frontend_summary + tier1_root_causes. */
export function getLatestAnalysis({ runId = null, analysisId = null, signal } = {}) {
  return request(`/api/root-cause/latest${query({ run_id: runId, analysis_id: analysisId })}`, {
    signal,
  });
}

/** frontend_summary only - lighter than /latest when the detail is not needed. */
export function getSummary({ runId = null, analysisId = null, signal } = {}) {
  return request(`/api/root-cause/summary${query({ run_id: runId, analysis_id: analysisId })}`, {
    signal,
  });
}

/** One row per Critical Tier 1 KPI analysed, with its root-cause and confidence counts. */
export function listKpis({ runId = null, analysisId = null, signal } = {}) {
  return request(`/api/root-cause/kpis${query({ run_id: runId, analysis_id: analysisId })}`, {
    signal,
  });
}

/** The ranked root causes of one Tier 1 KPI. */
export function getKpiFindings(kpiId, { runId = null, analysisId = null, detail = true, signal } = {}) {
  return request(
    `/api/root-cause/kpis/${encodeURIComponent(kpiId)}${query({
      run_id: runId,
      analysis_id: analysisId,
      detail,
    })}`,
    { signal }
  );
}

/** The flat, filterable root-cause list. */
export function listFindings({
  runId = null,
  analysisId = null,
  kpiId = null,
  gap = null,
  confidence = null,
  rootCauseType = null,
  detail = false,
  limit = 100,
  signal,
} = {}) {
  return request(
    `/api/root-cause/findings${query({
      run_id: runId,
      analysis_id: analysisId,
      kpi_id: kpiId,
      gap,
      confidence,
      root_cause_type: rootCauseType,
      detail,
      limit,
    })}`,
    { signal }
  );
}

/** One root cause with its full detailed evidence, e.g. RC-T1-01-2. */
export function getFinding(id, { analysisId = null, signal } = {}) {
  return request(
    `/api/root-cause/findings/${encodeURIComponent(id)}${query({ analysis_id: analysisId })}`,
    { signal }
  );
}

/** Which GAP ids the analysis cites, joined to the GAP library and the detected volume. */
export function listGaps({ runId = null, analysisId = null, signal } = {}) {
  return request(`/api/root-cause/gaps${query({ run_id: runId, analysis_id: analysisId })}`, {
    signal,
  });
}

/** The analysis history, newest first. */
export function listAnalyses({ limit = 20, signal } = {}) {
  return request(`/api/root-cause/analyses${query({ limit })}`, { signal });
}

/** The evidence package an analysis was grounded in - the audit trail for every statistic. */
export function getEvidence(analysisId, { signal } = {}) {
  return request(`/api/root-cause/analyses/${analysisId}/evidence`, { signal });
}

/* ------------------------------------------------------------------ Fix with AI */

/**
 * The deterministic four-part analysis for one root cause: impacted cases, the KPIs those cases
 * move, the representative case, and what happened in it. No model call - this returns fast.
 */
export function getFixWithAI(id, {
  analysisId = null,
  candidates = 5,
  includeTimeline = true,
  signal,
} = {}) {
  return request(
    `/api/root-causes/${encodeURIComponent(id)}/fix-with-ai${query({
      analysis_id: analysisId,
      candidates,
      include_timeline: includeTimeline,
    })}`,
    { signal }
  );
}

/** A page of impacted cases, ordered by attributable waiting time. */
export function getImpactedCases(id, { analysisId = null, limit = 25, offset = 0, signal } = {}) {
  return request(
    `/api/root-causes/${encodeURIComponent(id)}/impacted-cases${query({
      analysis_id: analysisId,
      limit,
      offset,
    })}`,
    { signal }
  );
}

/** The specialist agents that own this root cause's remediation (deterministic routing). */
export function getAgentsForFinding(id, { analysisId = null, signal } = {}) {
  return request(
    `/api/root-causes/${encodeURIComponent(id)}/agents${query({ analysis_id: analysisId })}`,
    { signal }
  );
}

/**
 * The To-Be state workflow. This one DOES call a model, so it is requested on demand rather than
 * on page load - see the design note in pages/FixWithAI.jsx.
 */
export function getToBeWorkflow(id, {
  analysisId = null,
  caseId = null,
  includeCurrentState = true,
  signal,
} = {}) {
  return request(
    `/api/root-causes/${encodeURIComponent(id)}/to-be-workflow${query({
      analysis_id: analysisId,
      case_id: caseId,
      include_current_state: includeCurrentState,
    })}`,
    { signal }
  );
}

/** The whole agent catalogue and mapping. Needs no database. */
export function getAgentCatalogue({ runId = null, signal } = {}) {
  return request(`/api/agents${query({ run_id: runId })}`, { signal });
}
