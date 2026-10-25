/**
 * Root Cause Detail - the screen behind "View evidence" on the dashboard.
 *
 * Route: /root-cause/:findingId          e.g. /root-cause/RC-T1-01-2
 * Data:  GET /api/root-cause/findings/:finding_id
 *
 * Sections, in the order the journey needs them:
 *   1. The root cause      - what it is, its type and confidence, and the causal mechanism
 *   2. KPI context         - the Critical Tier 1 KPI it explains, and the cohort it was measured on
 *   3. Impact              - the quantitative statistics behind the finding
 *   4. Evidence chain      - the ordered steps from KPI deviation to mechanism
 *   5. Typed evidence      - Tier 2, gap detections, stage, activity, rework, capacity, segment
 *                            and stalled evidence, each as supplied by the agent
 *   6. Limitations         - alternative explanations and what the evidence cannot establish
 *   7. Interventions       - options for the business to decide on
 *
 * Every figure here was produced by the KPI engine and carried through the evidence package; the
 * model supplied the narrative only. Section 6 is rendered as prominently as the finding itself:
 * the BRD requires that an association is never presented as proven causation.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { API_BASE_URL, getFinding } from "../api/client.js";
import {
  caseTypeLabel,
  formatCount,
  formatMetric,
  formatValue,
  splitIds,
  unitSuffix,
  varianceLabel,
} from "../format.js";
import "../rootcause.css";

/* ------------------------------------------------------------------ generic evidence table */

/**
 * columns: [[key, header], ...] - rendered in order, missing values as an em dash.
 * `units` maps a column key to a unit so numbers read the same way as on the dashboard.
 */
function EvidenceTable({ rows, columns, units = {} }) {
  if (!rows?.length) return null;
  return (
    <div className="etable-wrap">
      <table className="etable">
        <thead>
          <tr>
            {columns.map(([key, header]) => (
              <th key={key} scope="col">
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map(([key]) => {
                const value = row[key];
                const unit = units[key];
                return (
                  <td key={key}>
                    {value === null || value === undefined || value === ""
                      ? "—"
                      : typeof value === "number"
                      ? `${formatValue(value, unit)}${unitSuffix(unit)}`
                      : String(value)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Block({ title, note, children, variant = "" }) {
  if (!children) return null;
  return (
    <section className={`block ${variant}`}>
      <div className="block__head">
        <h2>{title}</h2>
        {note && <p className="block__note">{note}</p>}
      </div>
      <div className="block__body">{children}</div>
    </section>
  );
}

function Bullets({ items }) {
  if (!items?.length) return null;
  return (
    <ul className="bullets">
      {items.map((item, i) => (
        <li key={i}>{item}</li>
      ))}
    </ul>
  );
}

/* ------------------------------------------------------------------ states */

function LoadingState({ id }) {
  return (
    <div className="rcd">
      <div className="rcd-loading" role="status" aria-live="polite">
        <h2>
          <span className="spinner" aria-hidden="true" />
          Loading root cause…
        </h2>
        <p>{id}</p>
      </div>
    </div>
  );
}

function ErrorState({ error, id, onRetry, retrying }) {
  const isMissing = error?.code === "finding_not_found" || error?.status === 404;
  return (
    <div className="rcd">
      <Link to="/" className="rcd__back">
        ← Back to the dashboard
      </Link>
      <div className="rcd-error" role="alert">
        <h2>{isMissing ? "No such root cause" : "Could not load this root cause"}</h2>
        <p>{error?.message || "Unknown error."}</p>
        {error?.code === "unreachable" && (
          <p>
            The dashboard is pointed at <code>{API_BASE_URL}</code>. Start the API with{" "}
            <code>uvicorn backend.main:app --port 8010</code>, or set{" "}
            <code>VITE_API_BASE_URL</code>.
          </p>
        )}
        {isMissing && (
          <p>
            Ids are <code>RC-&lt;tier1 kpi&gt;-&lt;rank&gt;</code>, e.g. <code>RC-T1-01-1</code>.
            They exist only for KPIs the engine flagged Critical in the stored analysis.
          </p>
        )}
        <div className="rcd-error__actions">
          <Link to="/" className="btn-secondary">
            ← Back to the dashboard
          </Link>
          {!isMissing && (
            <button type="button" className="btn-secondary" onClick={onRetry} disabled={retrying}>
              {retrying && <span className="spinner" aria-hidden="true" />}
              {retrying ? "Retrying…" : "Try again"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ page */

export default function RootCauseDetailPage() {
  const { findingId: id } = useParams();
  const [state, setState] = useState({ status: "loading" });
  const [retrying, setRetrying] = useState(false);
  const abortRef = useRef(null);

  const load = useCallback(() => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    return getFinding(id, { signal: controller.signal })
      .then((data) => setState({ status: "ready", data }))
      .catch((err) => {
        if (err?.name === "AbortError") return;
        setState({ status: "error", error: err });
      });
  }, [id]);

  useEffect(() => {
    setState({ status: "loading" });
    load();
    return () => abortRef.current?.abort();
  }, [load]);

  const retry = () => {
    setRetrying(true);
    load().finally(() => setRetrying(false));
  };

  if (state.status === "loading") return <LoadingState id={id} />;
  if (state.status === "error") {
    return <ErrorState error={state.error} id={id} onRetry={retry} retrying={retrying} />;
  }

  const f = state.data;
  const d = f.detail_json || {};
  const variance = varianceLabel(f.tier1_variance_pct);
  const statistics = f.impact_statistics || d.impact_statistics || [];
  const chain = f.evidence_chain || d.evidence_chain || [];
  const alternatives = f.alternative_explanations || d.alternative_explanations || [];
  const interventions = f.recommended_interventions || d.recommended_interventions || [];
  const symptoms = f.symptoms || d.symptoms || [];
  const factors = f.contributing_factors || d.contributing_factors || [];
  const limitations = f.evidence_limitations || d.evidence_limitations;

  return (
    <div className="rcd">
      <Link to="/" className="rcd__back">
        ← Back to the dashboard
      </Link>

      <header className="rcd__head">
        <div className="rcd__eyebrow">
          <span className="rcd__id">{f.finding_id}</span>
          <span className="cause__rank">{f.rank}</span>
          {f.root_cause_type && <span className="chip chip--type">{f.root_cause_type}</span>}
          <span className={`pill pill--${(f.confidence || "").toLowerCase()}`}>
            {f.confidence} confidence
          </span>
          {splitIds(f.linked_tier2_kpis).map((t2) => (
            <span className="chip chip--kpi" key={t2} title="Linked Tier 2 diagnostic KPI">
              {t2}
            </span>
          ))}
          {splitIds(f.gap_references).map((gap) => (
            <span className="chip chip--gap" key={gap} title="Documented process gap">
              {gap}
            </span>
          ))}
        </div>

        <h1 className="rcd__title">{f.root_cause}</h1>
        {(f.causal_explanation || d.causal_explanation) && (
          <p className="rcd__explain">{f.causal_explanation || d.causal_explanation}</p>
        )}

        <div className="rcd__kpi">
          <div>
            <span className="rcd__kpi-id">{f.tier1_kpi_id}</span>
            <p className="rcd__kpi-name">{f.tier1_kpi_name}</p>
          </div>
          <dl className="rcd__kpi-meta">
            <div>
              <dt>Current</dt>
              <dd className="rcd__kpi-value">
                {formatMetric(f.tier1_current_value, f.tier1_unit)}
              </dd>
            </div>
            <div>
              <dt>Target</dt>
              <dd>{formatMetric(f.tier1_target, f.tier1_unit)}</dd>
            </div>
            <div>
              <dt>Variance</dt>
              <dd className={`tone--${variance.tone}`}>{variance.text}</dd>
            </div>
            <div>
              <dt>Affected</dt>
              <dd>
                {f.affected_cases_count === null || f.affected_cases_count === undefined
                  ? "—"
                  : `${formatCount(f.affected_cases_count)} ${caseTypeLabel(
                      f.affected_case_type,
                      f.affected_cases_count
                    )}`}
              </dd>
            </div>
          </dl>
        </div>
        {f.tier1_cohort_rule && (
          <p className="rcd__cohort">
            <strong>KPI measured on.</strong> {f.tier1_cohort_rule}
          </p>
        )}
        {f.affected_denominator && (
          <p className="rcd__cohort">
            <strong>Affected cases counted against.</strong> {f.affected_denominator}
          </p>
        )}
      </header>

      {!!statistics.length && (
        <Block title="Impact" note="Every figure comes from the KPI engine, not the model">
          <ul className="stats">
            {statistics.map((stat, i) => (
              <li className="stat" key={`${stat.metric}-${i}`}>
                <span className="stat__value">
                  {formatValue(stat.value, stat.unit)}
                  <span className="stat__unit">{unitSuffix(stat.unit)}</span>
                </span>
                <span className="stat__metric">{stat.metric}</span>
                {stat.interpretation && <span className="stat__metric">{stat.interpretation}</span>}
              </li>
            ))}
          </ul>
        </Block>
      )}

      {!!chain.length && (
        <Block title="Evidence chain" note="KPI deviation → diagnostic → gap → process evidence">
          <ol className="chain">
            {chain.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        </Block>
      )}

      {!!(d.tier2_evidence || []).length && (
        <Block title="Tier 2 diagnostic evidence">
          <EvidenceTable
            rows={d.tier2_evidence}
            columns={[
              ["kpi_id", "KPI"],
              ["kpi_name", "Diagnostic"],
              ["current_value", "Value"],
              ["target", "Target"],
              ["unit", "Unit"],
              ["variance_vs_target_pct", "Variance %"],
              ["health", "Health"],
            ]}
          />
        </Block>
      )}

      {!!(d.gap_evidence || []).length && (
        <Block
          title="Gap detections"
          note="How often the deterministic rule actually fired against the event log"
        >
          <EvidenceTable
            rows={d.gap_evidence}
            columns={[
              ["gap_reference", "Gap"],
              ["findings", "Findings"],
              ["cases", "Cases"],
              ["requisitions", "Requisitions"],
              ["median_measured_value", "Median measured"],
              ["threshold_value", "Threshold"],
              ["measure_unit", "Unit"],
            ]}
          />
        </Block>
      )}

      {!!(d.stage_evidence || []).length && (
        <Block title="Stage evidence" note="Requisition and candidate stage dwell are never pooled">
          <EvidenceTable
            rows={d.stage_evidence}
            columns={[
              ["stage_name", "Stage"],
              ["case_type", "Case type"],
              ["median_elapsed_days", "Median days"],
              ["p90_elapsed_days", "P90 days"],
              ["stage_sla_days", "SLA days"],
              ["sla_breach_pct", "Breach %"],
              ["avg_recorded_waiting_days", "Recorded wait"],
              ["revisit_rate_pct", "Revisit %"],
            ]}
          />
        </Block>
      )}

      {!!(d.activity_evidence || []).length && (
        <Block title="Activity evidence" note="Waiting hotspots, container waits excluded">
          <EvidenceTable
            rows={d.activity_evidence}
            columns={[
              ["activity_name", "Activity"],
              ["actor_persona", "Persona"],
              ["events", "Events"],
              ["cases_touched_pct", "Cases %"],
              ["avg_waiting_min", "Avg wait (min)"],
              ["total_waiting_days", "Total wait (days)"],
              ["sla_breach_pct", "Breach %"],
              ["top_waiting_reason", "Waiting reason"],
            ]}
          />
        </Block>
      )}

      {!!(d.rework_evidence || []).length && (
        <Block title="Rework evidence">
          <EvidenceTable
            rows={d.rework_evidence}
            columns={[
              ["rework_loop_type", "Loop"],
              ["cases", "Cases"],
              ["cases_pct", "Cases %"],
              ["days_lost", "Days lost"],
              ["avg_days_lost_per_affected_case", "Per case"],
              ["top_reason_code", "Reason"],
            ]}
          />
        </Block>
      )}

      {!!(d.capacity_evidence || []).length && (
        <Block
          title="Capacity evidence"
          note="Load concentration and queue ageing - not an individual performance measure"
        >
          <EvidenceTable
            rows={d.capacity_evidence}
            columns={[
              ["recruiter_id_pseudonymous", "Recruiter"],
              ["avg_weighted_load", "Avg load"],
              ["peak_weighted_load", "Peak load"],
              ["requisitions", "Requisitions"],
              ["median_time_to_fill_days", "Median time to fill"],
              ["avg_assignment_days", "Avg assignment"],
            ]}
          />
        </Block>
      )}

      {!!(d.segment_evidence || []).length && (
        <Block title="Segment evidence" note="Where the pattern concentrates, and where it does not">
          <EvidenceTable
            rows={d.segment_evidence}
            columns={[
              ["dimension", "Dimension"],
              ["segment", "Segment"],
              ["metric", "Metric"],
              ["value", "Value"],
              ["comparison_value", "Comparison"],
              ["comparison_basis", "Basis"],
            ]}
          />
        </Block>
      )}

      {!!(d.stalled_evidence || []).length && (
        <Block title="Stalled-case evidence" note="A forward-looking queue signal, not a completed-case statistic">
          <EvidenceTable
            rows={d.stalled_evidence}
            columns={[
              ["grouping", "Grouping"],
              ["label", "Label"],
              ["stalled_cases", "Stalled cases"],
              ["avg_open_days", "Avg open days"],
            ]}
          />
        </Block>
      )}

      {(!!alternatives.length || limitations) && (
        <Block
          title="What this evidence cannot establish"
          note="Observational event data shows sequence and association, not causation"
          variant="block--caveat"
        >
          <>
            {limitations && <p className="rcd__explain">{limitations}</p>}
            {!!alternatives.length && (
              <>
                <p className="block__note" style={{ marginTop: 12 }}>
                  Alternative explanations considered
                </p>
                <Bullets items={alternatives} />
              </>
            )}
          </>
        </Block>
      )}

      {(!!symptoms.length || !!factors.length) && (
        <div className="twocol">
          {!!factors.length && (
            <Block title="Contributing factors">
              <Bullets items={factors} />
            </Block>
          )}
          {!!symptoms.length && (
            <Block title="Symptoms" note="Observable effects, not causes">
              <Bullets items={symptoms} />
            </Block>
          )}
        </div>
      )}

      {!!interventions.length && (
        <Block
          title="Recommended interventions"
          note="Options for the business to decide on - the agent does not set policy"
        >
          <ul className="interventions">
            {interventions.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </Block>
      )}
    </div>
  );
}
