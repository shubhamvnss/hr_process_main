/**
 * Fix with AI - the screen behind "Fix with AI" on a root cause.
 *
 * Route: /root-cause/:findingId/fix-with-ai
 *
 * Five sections, in the order the journey needs them:
 *   1. Impacted cases        how many cases this root cause touches, and the cases themselves
 *   2. KPIs associated       how the impacted cohort scores versus everyone else
 *   3. Representative case   the typical impacted case, deterministically chosen
 *   4. What happened         its journey, as facts assembled from event rows
 *   5. Recommended agents    who owns the remediation, from the deterministic routing map
 *   6. To-Be state workflow  the redesigned activity list and its counts
 *
 * Loading strategy. Sections 1-5 come from one fast, deterministic endpoint and render on page
 * load. Section 6 calls a model, so it is requested on demand instead - firing a billable,
 * minutes-long generation on every page view would be the wrong default, and the user should
 * choose when to spend it. The impacted-case list is also lazy, because it is a long table nobody
 * needs until they ask for it.
 *
 * Nothing on this screen is recomputed in the browser. Every count comes from the API, which
 * computes them in Python from the event log and the design - not from a model.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  ApiError,
  API_BASE_URL,
  getFixWithAI,
  getImpactedCases,
  getToBeWorkflow,
} from "../api/client.js";
import {
  caseTypeLabel,
  formatCount,
  formatMetric,
  formatTimestamp,
  formatValue,
  unitSuffix,
  varianceLabel,
} from "../format.js";
import "../fixwithai.css";

/* ------------------------------------------------------------------ states */

const ANALYSIS_STEPS = [
  "Resolving the root cause",
  "Matching impacted cases in the event log",
  "Comparing KPI cohorts",
  "Scoring representative cases",
  "Reading the case journey",
  "Routing the specialist agents",
];

function LoadingState({ findingId, steps = ANALYSIS_STEPS, title = "Analysing root cause…" }) {
  const [reached, setReached] = useState(0);
  useEffect(() => {
    // Advances through the stages the backend works through, and holds on the last one until the
    // response lands - it never reports the analysis as finished before it is.
    const timers = steps.slice(0, -1).map((_, i) =>
      setTimeout(() => setReached((r) => Math.max(r, i + 1)), 500 * (i + 1))
    );
    return () => timers.forEach(clearTimeout);
  }, [steps]);

  return (
    <div className="fix-loading" role="status" aria-live="polite">
      <h2>{title}</h2>
      <p className="fix-loading__sub">{findingId}</p>
      <ul className="fix-steps">
        {steps.map((step, i) => {
          const state = i < reached ? "done" : i === reached ? "active" : "todo";
          return (
            <li key={step} className={`fix-step fix-step--${state}`}>
              <span className="fix-step__mark" aria-hidden="true">
                {state === "done" ? "✓" : state === "active" ? "●" : "○"}
              </span>
              {step}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ErrorState({ error, findingId, onRetry, retrying }) {
  const code = error?.code;
  const missing = code === "root_cause_not_found";
  const noEvidence = code === "insufficient_evidence" || code === "to_be_context_unavailable";
  return (
    <div className="fix-error" role="alert">
      <h2>
        {missing
          ? "No such root cause"
          : noEvidence
          ? "Not enough evidence to analyse this root cause"
          : "Could not complete the analysis"}
      </h2>
      <p>{error?.message || "Unknown error."}</p>
      {code === "unreachable" && (
        <p>
          The dashboard is pointed at <code>{API_BASE_URL}</code>. Start the API with{" "}
          <code>uvicorn backend.main:app --port 8010</code>, or set <code>VITE_API_BASE_URL</code>.
        </p>
      )}
      <div className="fix-error__actions">
        <Link className="btn-secondary" to="/">
          ← Back to the dashboard
        </Link>
        <Link className="btn-secondary" to={`/root-cause/${encodeURIComponent(findingId)}`}>
          View the root-cause evidence
        </Link>
        {!missing && !noEvidence && (
          <button type="button" className="btn-primary" onClick={onRetry} disabled={retrying}>
            {retrying && <span className="spinner" aria-hidden="true" />}
            {retrying ? "Retrying…" : "Try again"}
          </button>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ 1. impacted cases */

function ImpactSection({ rootCause, impact, findingId }) {
  const [cases, setCases] = useState({ status: "idle" });
  const noun = caseTypeLabel(impact.case_type, 2);

  const loadCases = () => {
    setCases({ status: "loading" });
    getImpactedCases(findingId, { limit: 25 })
      .then((data) => setCases({ status: "ready", data }))
      .catch((error) => setCases({ status: "error", error }));
  };

  return (
    <section className="fix-section">
      <header className="fix-section__head">
        <h2>Impacted cases</h2>
        <span className="fix-section__note">{impact.selector_summary}</span>
      </header>

      <h3 className="fix-rc__title">{rootCause.root_cause}</h3>
      {rootCause.root_cause_summary && (
        <p className="fix-rc__desc">{rootCause.root_cause_summary}</p>
      )}

      <div className="fix-kpi-context">
        {rootCause.root_cause_type && (
          <span className="chip chip--category">{rootCause.root_cause_type}</span>
        )}
        {rootCause.tier1_kpi_id && (
          <span className="chip chip--t1">
            {rootCause.tier1_kpi_id} {rootCause.tier1_kpi_name}
          </span>
        )}
        {(rootCause.tier2_kpi_ids || []).map((k) => (
          <span className="chip chip--t2" key={k}>
            {k}
          </span>
        ))}
        {(rootCause.gap_references || []).map((g) => (
          <span className="chip chip--gap" key={g} title="Documented process gap">
            {g}
          </span>
        ))}
      </div>

      <div className="fix-impact">
        <div className="fix-impact__headline">
          <span className="fix-impact__number">{formatCount(impact.impacted_cases)}</span>
          <span className="fix-impact__label">{caseTypeLabel(impact.case_type, 2)} impacted</span>
          {impact.impact_percentage !== null && impact.impact_percentage !== undefined && (
            <span className="fix-impact__sub">
              {impact.impact_percentage}% of {formatCount(impact.total_cases)} {noun}
            </span>
          )}
        </div>
        <ul className="fix-metrics">
          {(impact.metrics || []).slice(1).map((m) => (
            <li className="fix-metric" key={m.label}>
              <span className="fix-metric__value">{formatMetric(m.value, m.unit)}</span>
              <span className="fix-metric__label">{m.label}</span>
              {m.detail && <span className="fix-metric__detail">{m.detail}</span>}
            </li>
          ))}
        </ul>
      </div>

      {(impact.notes || []).map((n, i) => (
        <p className="fix-note" key={i}>
          {n}
        </p>
      ))}

      {cases.status === "idle" ? (
        <p className="fix-evidence">
          <button type="button" className="btn-secondary" onClick={loadCases}>
            Show the impacted {noun}
          </button>
        </p>
      ) : cases.status === "loading" ? (
        <p className="empty">
          <span className="spinner" aria-hidden="true" />
          Loading impacted {noun}…
        </p>
      ) : cases.status === "error" ? (
        <p className="empty">{cases.error.message}</p>
      ) : (
        <>
          <p className="fix-section__count">
            Showing {cases.data.count} of {formatCount(impact.impacted_cases)}, worst attributable
            waiting first
          </p>
          <div className="ftable-wrap">
            <table className="ftable">
              <thead>
                <tr>
                  <th>Case</th>
                  <th>Outcome</th>
                  <th>Business unit</th>
                  <th>Job family</th>
                  <th>Level</th>
                  <th>Elapsed (d)</th>
                  <th>Matched events</th>
                  <th>Attributable wait (d)</th>
                  <th>Rework</th>
                  <th>SLA breaches</th>
                </tr>
              </thead>
              <tbody>
                {cases.data.cases.map((c) => (
                  <tr key={c.case_id} className={c.is_open_censored === "Y" ? "is-open" : ""}>
                    <td>{c.case_id}</td>
                    <td>{c.outcome || "—"}</td>
                    <td>{c.business_unit || "—"}</td>
                    <td>{c.job_family || "—"}</td>
                    <td>{c.job_level || "—"}</td>
                    <td>{c.elapsed_days ?? "—"}</td>
                    <td>{c.matched_events ?? "—"}</td>
                    <td>{c.matched_waiting_days ?? "—"}</td>
                    <td>{c.rework_loop_count ?? "—"}</td>
                    <td>{c.sla_breach_count ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ 2. KPIs associated */

function KpiSection({ kpis }) {
  if (!kpis) return null;
  const t1 = kpis.tier1 || {};
  const comparison = kpis.cohort_comparison || [];
  const variance = varianceLabel(t1.variance_vs_target_pct);

  return (
    <section className="fix-section">
      <header className="fix-section__head">
        <h2>KPIs associated with the impacted cases</h2>
        <span className="fix-section__note">
          {kpis.measures_worse_on_impacted} of {kpis.measures_compared} measures are worse on the
          impacted cohort
        </span>
      </header>

      {t1.kpi_id && (
        <p className="fix-kpi-line">
          <strong>
            {formatValue(t1.current_value, t1.unit)}
            {unitSuffix(t1.unit)}
          </strong>{" "}
          against a target of {formatMetric(t1.target, t1.unit)} —{" "}
          <span className={`tone--${variance.tone}`}>{variance.text}</span>
          {t1.cohort_rule && <> · measured on {t1.cohort_rule}</>}
        </p>
      )}

      {!!(kpis.tier2 || []).length && (
        <div className="ftable-wrap">
          <table className="ftable">
            <thead>
              <tr>
                <th>Tier 2 diagnostic</th>
                <th>Value</th>
                <th>Target</th>
                <th>Unit</th>
                <th>Variance %</th>
                <th>Health</th>
              </tr>
            </thead>
            <tbody>
              {kpis.tier2.map((k) => (
                <tr key={k.kpi_id}>
                  <td>
                    {k.kpi_id} {k.kpi_name}
                  </td>
                  <td>{k.current_value ?? "—"}</td>
                  <td>{k.target ?? "—"}</td>
                  <td>{k.unit}</td>
                  <td>{k.variance_vs_target_pct ?? "—"}</td>
                  <td>
                    <span className={`badge badge--${(k.health || "na").toLowerCase()}`}>
                      {k.health}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!!comparison.length && (
        <>
          <h4 className="fix-case__h4">Impacted vs unimpacted cases</h4>
          <div className="ftable-wrap">
            <table className="ftable">
              <thead>
                <tr>
                  <th>Measure</th>
                  <th>Impacted</th>
                  <th>Unimpacted</th>
                  <th>Difference</th>
                  <th>Unit</th>
                  <th>Cases</th>
                </tr>
              </thead>
              <tbody>
                {comparison.map((c) => (
                  <tr key={c.measure}>
                    <td>{c.measure}</td>
                    <td>{c.impacted ?? "—"}</td>
                    <td>{c.unimpacted ?? "—"}</td>
                    <td className={c.direction === "worse" ? "tone--bad" : "tone--good"}>
                      {c.difference > 0 ? "+" : ""}
                      {c.difference ?? "—"}
                    </td>
                    <td>{c.unit}</td>
                    <td>
                      {formatCount(c.impacted_cases)} / {formatCount(c.unimpacted_cases)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {kpis.interpretation_note && <p className="fix-note">{kpis.interpretation_note}</p>}
    </section>
  );
}

/* ------------------------------------------------------------------ 3. representative case */

function RepresentativeCaseSection({ selection }) {
  const rep = selection?.case;
  if (!rep) {
    return (
      <section className="fix-section">
        <header className="fix-section__head">
          <h2>Most representative case</h2>
        </header>
        <p className="empty">{selection?.note || "No representative case was selected."}</p>
      </section>
    );
  }
  return (
    <section className="fix-section">
      <header className="fix-section__head">
        <h2>Most representative case</h2>
        <span className="fix-section__note">
          chosen by score from {selection.candidate_pool} impacted completed cases, not at random
        </span>
      </header>

      <div className="fix-case">
        <div className="fix-case__head">
          <div>
            <h3>{rep.case_id}</h3>
            <p className="fix-case__sub">
              {[
                rep.case_type,
                rep.requisition_id !== rep.case_id ? rep.requisition_id : null,
                rep.business_unit,
                rep.job_family,
                rep.job_level,
                rep.location,
                rep.priority,
                rep.source_channel,
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
          <dl className="fix-case__stats">
            <div>
              <dt>Outcome</dt>
              <dd>{rep.outcome || "—"}</dd>
            </div>
            <div>
              <dt>Elapsed</dt>
              <dd>{formatMetric(rep.elapsed_days, "days")}</dd>
            </div>
            <div>
              <dt>Longest wait</dt>
              <dd>{formatMetric(rep.max_inactivity_business_days, "days")}</dd>
            </div>
            <div>
              <dt>Rework loops</dt>
              <dd>{rep.rework_loops ?? "—"}</dd>
            </div>
            <div>
              <dt>SLA breaches</dt>
              <dd className={rep.sla_breaches > 0 ? "tone--bad" : "tone--good"}>
                {rep.sla_breaches ?? "—"}
                {rep.sla_measured ? ` / ${rep.sla_measured}` : ""}
              </dd>
            </div>
          </dl>
        </div>

        {rep.reason_selected && (
          <div className="fix-case__why">
            <h4 className="fix-case__h4">Why this case represents the root cause</h4>
            <p>{rep.reason_selected}</p>
          </div>
        )}

        {!!(rep.evidence || []).length && (
          <details className="fix-evidence">
            <summary>Case evidence used ({rep.evidence.length})</summary>
            <ul>
              {rep.evidence.map((e, i) => (
                <li key={i}>
                  <span className={`tag tag--${e.evidence_type}`}>{e.evidence_type}</span>{" "}
                  {e.evidence_text}
                </li>
              ))}
            </ul>
          </details>
        )}

        {!!(rep.gap_findings || []).length && (
          <details className="fix-evidence">
            <summary>Gap detections on this case ({rep.gap_findings.length})</summary>
            <ul>
              {rep.gap_findings.map((g, i) => (
                <li key={i}>
                  <span className="chip chip--gap">{g.gap_reference}</span> {g.evidence_summary}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ 4. what happened */

function WhatHappenedSection({ story }) {
  if (!story) return null;
  const s = story.summary || {};
  return (
    <section className="fix-section">
      <header className="fix-section__head">
        <h2>What happened in {story.case_id}</h2>
        <span className="fix-section__note">
          assembled from event rows — no statement here was written by a model
        </span>
      </header>

      <ul className="fix-metrics">
        <li className="fix-metric">
          <span className="fix-metric__value">{s.events}</span>
          <span className="fix-metric__label">Events</span>
          <span className="fix-metric__detail">{s.follow_up_events} follow-ups</span>
        </li>
        <li className="fix-metric">
          <span className="fix-metric__value">{formatMetric(s.total_waiting_days, "days")}</span>
          <span className="fix-metric__label">Total waiting</span>
          <span className="fix-metric__detail">
            {s.waiting_share_attributable_pct ?? "—"}% attributable to this root cause
          </span>
        </li>
        <li className="fix-metric">
          <span className="fix-metric__value">{s.rework_events}</span>
          <span className="fix-metric__label">Rework events</span>
        </li>
        <li className="fix-metric">
          <span className="fix-metric__value">{s.sla_breaches}</span>
          <span className="fix-metric__label">SLA breaches</span>
        </li>
        <li className="fix-metric">
          <span className="fix-metric__value">{s.handoffs}</span>
          <span className="fix-metric__label">Handoffs</span>
          <span className="fix-metric__detail">{s.distinct_personas} personas</span>
        </li>
      </ul>

      {!!(story.narrative || []).length && (
        <>
          <h4 className="fix-case__h4">The account</h4>
          <ul className="fix-narrative">
            {story.narrative.map((n, i) => (
              <li key={i} className={`kind--${n.type}`}>
                <span className="fix-narrative__kind">{n.type}</span>
                <span>{n.text}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {!!(story.key_events || []).length && (
        <>
          <h4 className="fix-case__h4">Key events</h4>
          <ol className="fix-timeline">
            {story.key_events.map((e) => (
              <li
                key={e.sequence}
                className={`fix-tl ${e.matched_root_cause ? "fix-tl--matched" : ""}`}
              >
                <span className="fix-tl__dot" aria-hidden="true" />
                <div className="fix-tl__body">
                  <p className="fix-tl__activity">
                    {e.activity}
                    {e.matched_root_cause && (
                      <span className="tag tag--evidence">root-cause evidence</span>
                    )}
                    {e.is_rework && <span className="tag tag--rework">rework</span>}
                    {e.sla_breached && <span className="tag tag--sla">SLA breach</span>}
                    {e.is_exception && <span className="tag tag--exception">exception</span>}
                    {e.is_follow_up && <span className="tag tag--followup">follow-up</span>}
                  </p>
                  <p className="fix-tl__meta">
                    {[
                      e.stage,
                      e.actor_persona,
                      e.source_system,
                      formatTimestamp(e.timestamp),
                      e.waiting_days ? `${e.waiting_days}d waiting` : null,
                      e.waiting_reason_code,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ 5. recommended agents */

function AgentsSection({ agents }) {
  if (!agents?.primary_agent) return null;
  const all = [
    { ...agents.primary_agent, role: "primary" },
    ...(agents.supporting_agents || []).map((a) => ({ ...a, role: "supporting" })),
  ];
  const measure = agents.measure_after || {};
  return (
    <section className="fix-section">
      <header className="fix-section__head">
        <h2>Recommended agents</h2>
        <span className="fix-section__note">
          routed deterministically from {agents.routing_basis === "gap" ? "the detected gap" : null}
          {agents.routing_basis === "tier2" ? "the breaching diagnostic KPI" : null}
          {agents.routing_basis === "tier1" ? "the headline KPI" : null} · confidence{" "}
          {agents.confidence}
        </span>
      </header>

      <div className="fix-agents">
        {all.map((a) => (
          <article className={`agent agent--${a.role}`} key={a.agent_id}>
            <header className="agent__head">
              <span className="agent__icon" aria-hidden="true">
                ◆
              </span>
              <div>
                <h3 className="agent__name">{a.name}</h3>
                <span className="agent__id">{a.agent_id}</span>
              </div>
              <span className={`pill pill--${a.role} agent__role`}>{a.role}</span>
            </header>
            <div className="agent__field">
              <h4>Responsibility</h4>
              <p>{a.responsibility}</p>
            </div>
            <div className="agent__foot">
              <span className="chip chip--owner">Owner: {a.owner}</span>
              <span className="chip">{a.domain}</span>
            </div>
          </article>
        ))}
      </div>

      {agents.intervention_focus && (
        <div className="agent__field">
          <h4>Intervention focus</h4>
          <p>{agents.intervention_focus}</p>
        </div>
      )}

      {(measure.tier1_kpis?.length || measure.tier2_kpis?.length) && (
        <div className="agent__field">
          <h4>Measure after the intervention</h4>
          <div className="agent__foot">
            {(measure.tier1_kpis || []).map((k) => (
              <span className="chip chip--t1" key={k}>
                {k}
              </span>
            ))}
            {(measure.tier2_kpis || []).map((k) => (
              <span className="chip chip--t2" key={k}>
                {k}
              </span>
            ))}
          </div>
        </div>
      )}

      {agents.caution && <p className="fix-note">{agents.caution}</p>}
    </section>
  );
}

/* ------------------------------------------------------------------ 6. to-be workflow */

const TOBE_STEPS = [
  "Reading the recommended agents",
  "Reading the representative case journey",
  "Designing the future-state activities",
  "Validating against the agent and control rules",
  "Counting the result",
];

const CHANGE_CLASS = {
  "AI Agent": "ai",
  "Automated (Deterministic)": "auto",
  Human: "human",
  Retained: "retained",
  Combined: "ai",
  Eliminated: "eliminated",
};

function ToBeSection({ findingId }) {
  const [state, setState] = useState({ status: "idle" });
  const abortRef = useRef(null);

  const design = () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState({ status: "loading" });
    getToBeWorkflow(findingId, { includeCurrentState: false, signal: controller.signal })
      .then((data) => setState({ status: "ready", data }))
      .catch((error) => {
        if (error?.name === "AbortError") return;
        setState({ status: "error", error });
      });
  };

  useEffect(() => () => abortRef.current?.abort(), []);

  if (state.status === "idle") {
    return (
      <section className="fix-section">
        <header className="fix-section__head">
          <h2>To-Be state workflow</h2>
          <span className="fix-section__note">designed from the recommended agents only</span>
        </header>
        <div className="tobe-cta">
          <p>
            The future-state design is generated on request: it calls a model over this case's real
            activity list and takes a minute or two. Every count it reports is computed from the
            design afterwards, not produced by the model.
          </p>
          <button type="button" className="btn-primary" onClick={design}>
            Design the To-Be workflow →
          </button>
        </div>
      </section>
    );
  }

  if (state.status === "loading") {
    return (
      <section className="fix-section">
        <header className="fix-section__head">
          <h2>To-Be state workflow</h2>
        </header>
        <LoadingState findingId={findingId} steps={TOBE_STEPS} title="Designing the future state…" />
      </section>
    );
  }

  if (state.status === "error") {
    return (
      <section className="fix-section">
        <header className="fix-section__head">
          <h2>To-Be state workflow</h2>
        </header>
        <ErrorState
          error={state.error}
          findingId={findingId}
          onRetry={design}
          retrying={false}
        />
      </section>
    );
  }

  const d = state.data;
  const m = d.metrics || {};
  const eliminated = d.breakdown?.eliminated || [];

  return (
    <section className="fix-section">
      <header className="fix-section__head">
        <h2>To-Be state workflow</h2>
        <span className="fix-section__note">
          {d.case_id} · designed from {(d.recommended_agents || []).length} recommended agent(s)
        </span>
      </header>

      {d.workflow_summary && <p className="fix-rc__desc">{d.workflow_summary}</p>}

      <ul className="tobe-metrics">
        <li className="tobe-metric">
          <span className="tobe-metric__value">{m.to_be_activity_count}</span>
          <span className="tobe-metric__label">
            To-be activities<br />
            (from {m.current_activity_count})
          </span>
        </li>
        <li className="tobe-metric tobe-metric--reduced">
          <span className="tobe-metric__value">{m.activities_reduced}</span>
          <span className="tobe-metric__label">
            Activities reduced<br />
            {m.activities_reduced_pct}%
          </span>
        </li>
        <li className="tobe-metric tobe-metric--automated">
          <span className="tobe-metric__value">{m.activities_automated}</span>
          <span className="tobe-metric__label">Activities automated</span>
        </li>
        <li className="tobe-metric tobe-metric--agent">
          <span className="tobe-metric__value">{m.ai_agent_activities}</span>
          <span className="tobe-metric__label">AI agent activities</span>
        </li>
        <li className="tobe-metric tobe-metric--human">
          <span className="tobe-metric__value">{m.human_activities}</span>
          <span className="tobe-metric__label">Human activities</span>
        </li>
        <li className="tobe-metric tobe-metric--asis">
          <span className="tobe-metric__value">{m.activities_continuing_as_is}</span>
          <span className="tobe-metric__label">Continuing as is</span>
        </li>
      </ul>

      <h4 className="fix-case__h4">Future-state activities</h4>
      <ol className="tobe-flow">
        {(d.to_be_activities || []).map((a) => (
          <li
            className={`tobe-step tobe-step--${CHANGE_CLASS[a.change_type] || "retained"}`}
            key={a.to_be_sequence}
          >
            <span className="tobe-step__seq">{a.to_be_sequence}</span>
            <div>
              <p className="tobe-step__name">{a.to_be_activity}</p>
              <p className="tobe-step__meta">
                {[
                  a.actor_persona,
                  a.responsible_agent,
                  a.human_in_the_loop ? "human in the loop" : null,
                  (a.system_used || []).join(", "),
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
              <p className="tobe-step__from">
                from: {a.replaces_current_activities.map((s) => s.current_activity).join(" + ")}
                {a.kpi_gap_addressed ? ` · addresses ${a.kpi_gap_addressed}` : ""}
              </p>
            </div>
            <span className="tobe-step__type">{a.change_type}</span>
          </li>
        ))}
      </ol>

      {!!eliminated.length && (
        <>
          <h4 className="fix-case__h4">Eliminated ({eliminated.length})</h4>
          <ol className="tobe-flow">
            {eliminated.map((e) => (
              <li className="tobe-step tobe-step--eliminated" key={e.current_sequence}>
                <span className="tobe-step__seq">{e.current_sequence}</span>
                <div>
                  <p className="tobe-step__name">{e.current_activity}</p>
                  <p className="tobe-step__meta">{e.reason_for_change}</p>
                </div>
                <span className="tobe-step__type">
                  {e.was_follow_up ? "Follow-up removed" : "Eliminated"}
                </span>
              </li>
            ))}
          </ol>
        </>
      )}

      {d.design_limitations && <p className="fix-note">{d.design_limitations}</p>}
      {!d.counts_reconcile && (
        <p className="fix-note">
          The dispositions do not sum to the future-state step count — treat these figures with
          care and check the design rows.
        </p>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ page */

export default function FixWithAIPage() {
  const { findingId } = useParams();
  const [state, setState] = useState({ status: "loading" });
  const [retrying, setRetrying] = useState(false);
  const abortRef = useRef(null);

  const load = useCallback(() => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    return getFixWithAI(findingId, { signal: controller.signal })
      .then((data) => setState({ status: "ready", data }))
      .catch((error) => {
        if (error?.name === "AbortError") return;
        setState({
          status: "error",
          error: error instanceof ApiError ? error : new ApiError(String(error)),
        });
      });
  }, [findingId]);

  useEffect(() => {
    setState({ status: "loading" });
    load();
    return () => abortRef.current?.abort();
  }, [load]);

  const retry = () => {
    setRetrying(true);
    load().finally(() => setRetrying(false));
  };

  const d = state.data;

  return (
    <div className="dash fix-page">
      <nav className="fix-breadcrumb">
        <Link to="/">KPI Health &amp; Root Causes</Link>
        <span aria-hidden="true">›</span>
        <Link to={`/root-cause/${encodeURIComponent(findingId)}`}>{findingId}</Link>
        <span aria-hidden="true">›</span>
        <span>Fix with AI</span>
      </nav>

      <header className="fix-head">
        <div>
          <h1>Fix with AI</h1>
          <p className="dash__sub">{findingId}</p>
        </div>
        {state.status === "ready" && (
          <div className="fix-head__meta">
            {d.impact?.case_type && <span className="chip">{d.impact.case_type}</span>}
            <span className="chip">deterministic analysis</span>
            <Link className="btn-secondary" to={`/root-cause/${encodeURIComponent(findingId)}`}>
              Root-cause evidence
            </Link>
          </div>
        )}
      </header>

      {state.status === "loading" && <LoadingState findingId={findingId} />}

      {state.status === "error" && (
        <ErrorState error={state.error} findingId={findingId} onRetry={retry} retrying={retrying} />
      )}

      {state.status === "ready" && (
        <>
          <ImpactSection rootCause={d.root_cause} impact={d.impact} findingId={findingId} />
          {!d.impact?.impacted_cases ? (
            <section className="fix-section">
              <p className="empty">
                No case in the event log matches this root cause, so there is nothing to analyse,
                illustrate or redesign.
              </p>
            </section>
          ) : (
            <>
              <KpiSection kpis={d.kpis} />
              <RepresentativeCaseSection selection={d.representative_case} />
              <WhatHappenedSection story={d.what_happened} />
              <AgentsSection agents={d.agents} />
              <ToBeSection findingId={findingId} />
            </>
          )}
        </>
      )}
    </div>
  );
}
