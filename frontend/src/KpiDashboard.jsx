/**
 * KpiDashboard - Talent Acquisition (Deloitte Kinetic) KPI + root-cause dashboard.
 *
 * Four sections:
 *   1. Run header        - the KPI run and the analysis that explains it
 *   2. Tier 1 KPI tiles  - one per headline KPI, value dominant, target, variance and cohort
 *   3. Root causes       - the ranked, evidence-backed causes for every Critical Tier 1 KPI
 *   4. Gap coverage      - which GAP ids the analysis cites, against how often each was actually
 *                          detected in the event log
 *
 * It is a pure presentation layer: everything it shows comes from the Root Cause API, and no
 * number is recomputed here. The agent's contract (prompt section 28) is that frontend_summary can
 * be displayed as-is, so this component never re-ranks, re-aggregates or re-words anything.
 *
 * Data sources, each loaded and failing independently:
 *   GET /api/root-cause/kpi-health   tiles + run header - works before any analysis exists
 *   GET /api/root-cause/latest       frontend_summary + analysis metadata
 *   GET /api/root-cause/gaps         the gap coverage strip
 */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import {
  API_BASE_URL,
  findingId,
  getKpiHealth,
  getLatestAnalysis,
  listGaps,
} from "./api/client.js";
import {
  TREND,
  affectedLabel,
  formatCount,
  formatTimestamp,
  formatValue,
  splitIds,
  unitSuffix,
  varianceLabel,
} from "./format.js";

const HEALTH_ORDER = ["Critical", "Watch", "Healthy", "Informational", "N/A"];

const healthClass = (health) => (health || "na").toLowerCase().replace(/[^a-z]/g, "") || "na";

/* ------------------------------------------------------------------ tiles */

function KpiTile({ kpi, rootCauseCount, onSelect }) {
  const variance = varianceLabel(kpi.variance_vs_target_pct);
  const trend = TREND[kpi.trend_direction];
  const health = kpi.health || "N/A";
  const isCritical = health === "Critical";
  const Tag = isCritical ? "button" : "div";

  return (
    <Tag
      className={`tile tile--${healthClass(health)}`}
      {...(isCritical
        ? {
            type: "button",
            onClick: () => onSelect(kpi.kpi_id),
            "aria-label": `${kpi.kpi_name}: Critical. Jump to its root causes.`,
          }
        : {})}
    >
      <header className="tile__head">
        <span className="tile__id">{kpi.kpi_id}</span>
        <span className={`badge badge--${healthClass(health)}`}>{health}</span>
      </header>

      <h3 className="tile__title">{kpi.kpi_name}</h3>

      <p className="tile__value">
        {formatValue(kpi.current_value, kpi.unit)}
        <span className="tile__unit">{unitSuffix(kpi.unit)}</span>
        {trend && (
          <span className={`tile__trend tone--${trend.tone}`} title={trend.label}>
            {trend.glyph}
          </span>
        )}
      </p>

      <dl className="tile__meta">
        <div>
          <dt>Target</dt>
          <dd>
            {kpi.target === null || kpi.target === undefined
              ? "—"
              : `${formatValue(kpi.target, kpi.unit)}${unitSuffix(kpi.unit)}`}
          </dd>
        </div>
        <div>
          <dt>Variance</dt>
          <dd className={`tone--${variance.tone}`}>{variance.text}</dd>
        </div>
      </dl>

      {/* The cohort is part of the KPI, not a footnote: a rate here is only interpretable with it. */}
      {kpi.cohort_rule && (
        <p className="tile__cohort" title={kpi.cohort_rule}>
          {kpi.denominator_cases !== null && kpi.denominator_cases !== undefined
            ? `${formatCount(kpi.denominator_cases)} cases — `
            : ""}
          {kpi.cohort_rule}
        </p>
      )}

      {isCritical && rootCauseCount > 0 && (
        <p className="tile__link">
          {rootCauseCount} root {rootCauseCount === 1 ? "cause" : "causes"} →
        </p>
      )}
    </Tag>
  );
}

/* ------------------------------------------------------------------ root causes */

function EvidenceLink({ id, available }) {
  if (!available) {
    return (
      <span className="cause__id" title="The detail view reads the stored analysis. This page is being served from the agent's JSON file, so there is nothing to drill into yet.">
        {id} · detail needs the stored analysis
      </span>
    );
  }
  return (
    <Link
      to={`/root-cause/${encodeURIComponent(id)}`}
      className="btn-evidence"
      aria-label={`View the full evidence for root cause ${id}`}
    >
      <svg className="btn-evidence__icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path
          d="M11 3a8 8 0 105.3 14l4.4 4.4 1.4-1.4-4.4-4.4A8 8 0 0011 3zm0 2a6 6 0 110 12 6 6 0 010-12zm-1 3v4h2V8h-2zm0 5v2h2v-2h-2z"
          fill="currentColor"
        />
      </svg>
      View evidence
      <span className="btn-evidence__arrow" aria-hidden="true">→</span>
    </Link>
  );
}

function RootCauseCard({ cause, kpi, detailAvailable }) {
  const id = findingId(kpi.kpi_id, cause.rank);
  const affected = affectedLabel(cause.affected_cases);

  return (
    <article className="cause">
      <header className="cause__head">
        <span className="cause__rank">{cause.rank}</span>
        <h4 className="cause__title">{cause.root_cause}</h4>
        <span className={`pill pill--${(cause.confidence || "").toLowerCase()}`}>
          {cause.confidence} confidence
        </span>
      </header>

      {cause.root_cause_summary && <p className="cause__text">{cause.root_cause_summary}</p>}
      {cause.impact_summary && (
        <p className="cause__text cause__text--impact">
          <strong>Impact.</strong> {cause.impact_summary}
        </p>
      )}

      {!!(cause.statistics || []).length && (
        <ul className="stats">
          {cause.statistics.map((stat, i) => (
            <li className="stat" key={`${stat.metric}-${i}`}>
              <span className="stat__value">
                {formatValue(stat.value, stat.unit)}
                <span className="stat__unit">{unitSuffix(stat.unit)}</span>
              </span>
              <span className="stat__metric">{stat.metric}</span>
            </li>
          ))}
        </ul>
      )}

      <footer className="cause__foot">
        {affected && (
          <span
            className="chip chip--cases"
            title={cause.affected_cases?.denominator_description || undefined}
          >
            {affected}
          </span>
        )}
        {splitIds(cause.linked_tier2_kpis).map((t2) => (
          <span className="chip chip--kpi" key={t2} title="Linked Tier 2 diagnostic KPI">
            {t2}
          </span>
        ))}
        {splitIds(cause.gap_references).map((gap) => (
          <span className="chip chip--gap" key={gap} title="Documented process gap">
            {gap}
          </span>
        ))}
      </footer>

      <div className="cause__action">
        {detailAvailable ? <span className="cause__id">{id}</span> : <span />}
        <span className="cause__buttons">
          <EvidenceLink id={id} available={detailAvailable} />
          {detailAvailable && (
            <Link
              to={`/root-cause/${encodeURIComponent(id)}/fix-with-ai`}
              className="btn-fix"
              aria-label={`Fix with AI: impacted cases, representative case and agents for ${id}`}
            >
              <svg className="btn-fix__icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <path
                  d="M12 2.5l1.9 5.1 5.1 1.9-5.1 1.9-1.9 5.1-1.9-5.1L5 9.5l5.1-1.9L12 2.5zM18.5 15l.9 2.4 2.4.9-2.4.9-.9 2.4-.9-2.4-2.4-.9 2.4-.9.9-2.4z"
                  fill="currentColor"
                />
              </svg>
              Fix with AI
              <span className="btn-fix__arrow" aria-hidden="true">→</span>
            </Link>
          )}
        </span>
      </div>
    </article>
  );
}

function CriticalKpiSection({ kpi, detailAvailable }) {
  const variance = varianceLabel(kpi.variance_vs_target_pct);
  const causes = kpi.top_root_causes || [];
  return (
    <section className="rc" id={`rc-${kpi.kpi_id}`} tabIndex={-1}>
      <header className="rc__head">
        <div>
          <span className="rc__id">{kpi.kpi_id}</span>
          <h3 className="rc__title">{kpi.kpi_name}</h3>
        </div>
        <p className="rc__numbers">
          <strong>
            {formatValue(kpi.current_value, kpi.unit)}
            {unitSuffix(kpi.unit)}
          </strong>
          <span>
            vs target {formatValue(kpi.target, kpi.unit)}
            {unitSuffix(kpi.unit)}
          </span>
          <span className={`tone--${variance.tone}`}>{variance.text}</span>
        </p>
      </header>

      {kpi.cohort_rule && (
        <p className="rc__cohort">
          <strong>Measured on.</strong> {kpi.cohort_rule}
        </p>
      )}
      {kpi.kpi_performance_summary && <p className="rc__summary">{kpi.kpi_performance_summary}</p>}
      {kpi.why_not_performing && (
        <p className="rc__why">
          <strong>Why it is not performing.</strong> {kpi.why_not_performing}
        </p>
      )}

      {causes.length ? (
        <div className="rc__causes">
          {causes.map((cause) => (
            <RootCauseCard
              cause={cause}
              kpi={kpi}
              detailAvailable={detailAvailable}
              key={`${kpi.kpi_id}-${cause.rank}`}
            />
          ))}
        </div>
      ) : (
        <p className="empty">No evidence-backed root cause was returned for this KPI.</p>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ gap coverage */

function GapCoverage({ gaps }) {
  if (!gaps?.length) return null;
  return (
    <section className="section">
      <div className="section__head">
        <h2>Gap coverage</h2>
        <span className="section__note">
          GAP ids the analysis cites, against how often the deterministic rules detected each one
        </span>
      </div>
      <div className="gaps">
        {gaps.map((gap) => (
          <article className="gapcard" key={gap.gap_reference}>
            <header className="gapcard__head">
              <span className="gapcard__id">{gap.gap_reference}</span>
              <span className="gapcard__stage">{gap.stage_or_domain}</span>
            </header>
            {gap.gap_definition && <p className="gapcard__def">{gap.gap_definition}</p>}
            <dl className="gapcard__meta">
              <div>
                <dt>Cited by</dt>
                <dd>{gap.kpi_ids || "—"}</dd>
              </div>
              <div>
                <dt>Detected</dt>
                <dd>{formatCount(gap.detected?.cases)} cases</dd>
              </div>
            </dl>
          </article>
        ))}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ dashboard */

export default function KpiDashboard() {
  const [filter, setFilter] = useState("All");
  const [kpiHealth, setKpiHealth] = useState({ status: "loading" });
  const [analysis, setAnalysis] = useState({ status: "loading" });
  const [gaps, setGaps] = useState({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;

    const load = (fn, set) =>
      fn({ signal })
        .then((data) => set({ status: "ready", data }))
        .catch((err) => {
          if (err?.name === "AbortError") return;
          set({ status: "error", error: err });
        });

    // Loaded independently: a missing analysis must still leave the KPI tiles on screen.
    load(getKpiHealth, setKpiHealth);
    load(getLatestAnalysis, setAnalysis);
    load(listGaps, setGaps);

    return () => controller.abort();
  }, []);

  const runStats = kpiHealth.data?.run_stats || null;
  const tier1 = kpiHealth.data?.tier1 || [];
  const summary = analysis.data?.frontend_summary?.analysis_summary || null;
  const criticalKpis = summary?.critical_kpis || [];
  const detailAvailable = analysis.data?.source === "database";

  const causeCountById = useMemo(
    () => Object.fromEntries(criticalKpis.map((k) => [k.kpi_id, (k.top_root_causes || []).length])),
    [criticalKpis]
  );

  // Fall back to the KPIs named in the analysis when the KPI tables cannot be read, so the tiles
  // still render from a single source.
  const tiles = tier1.length ? tier1 : criticalKpis;

  const counts = useMemo(() => {
    const seed = Object.fromEntries(HEALTH_ORDER.map((h) => [h, 0]));
    tiles.forEach((k) => {
      const h = k.health || "N/A";
      seed[h] = (seed[h] || 0) + 1;
    });
    return seed;
  }, [tiles]);

  const visible = filter === "All" ? tiles : tiles.filter((k) => (k.health || "N/A") === filter);

  const jumpTo = (kpiId) => {
    const el = document.getElementById(`rc-${kpiId}`);
    if (!el) return;
    // Focus first: calling focus() after scrollIntoView cancels the smooth-scroll animation.
    el.focus({ preventScroll: true });
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
  };

  if (kpiHealth.status === "loading" && analysis.status === "loading") {
    return (
      <p className="empty empty--page">
        <span className="spinner" aria-hidden="true" />
        Loading the KPI run from {API_BASE_URL}…
      </p>
    );
  }

  const unreachable =
    kpiHealth.status === "error" && kpiHealth.error?.code === "unreachable" ? kpiHealth.error : null;
  const problems = analysis.data?.validation_problems || [];

  return (
    <div className="dash">
      {unreachable && (
        <p className="banner banner--error" role="alert">
          <strong>The Root Cause API is not reachable.</strong> {unreachable.message} Start it with{" "}
          <code>uvicorn backend.main:app --port 8010</code>, or point the dashboard elsewhere with{" "}
          <code>VITE_API_BASE_URL</code>.
        </p>
      )}

      {analysis.data?.source === "file" && (
        <p className="banner" role="status">
          <strong>Served from the agent's JSON file.</strong> The database holds no stored analysis,
          so the API fell back to <code>ta_root_causes.json</code>. Run{" "}
          <code>python root_cause_agent.py</code> against the database to enable the evidence
          drill-down.
        </p>
      )}

      {!!problems.length && (
        <div className="banner" role="status">
          <strong>
            This analysis was stored with {problems.length} validation{" "}
            {problems.length === 1 ? "problem" : "problems"}.
          </strong>{" "}
          The findings are shown as returned; treat the flagged items with care.
          <ul className="banner__list">
            {problems.slice(0, 5).map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </div>
      )}

      <header className="dash__head">
        <div className="dash__titles">
          <h1>Talent Acquisition — KPI Health &amp; Root Causes</h1>
          <p className="dash__sub">
            Deloitte Kinetic process intelligence
            {analysis.data?.source && (
              <>
                {" · "}
                <span className={`source source--${analysis.data.source}`}>
                  {analysis.data.source}
                </span>
              </>
            )}
          </p>
        </div>
        {runStats && (
          <dl className="runstats">
            <div>
              <dt>KPI run</dt>
              <dd>#{runStats.run_id}</dd>
            </div>
            <div>
              <dt>As of</dt>
              <dd>{formatTimestamp(runStats.as_of_timestamp)}</dd>
            </div>
            <div>
              <dt>Requisitions</dt>
              <dd>{formatCount(runStats.requisitions_total)}</dd>
            </div>
            <div>
              <dt>Applications</dt>
              <dd>{formatCount(runStats.applications_total)}</dd>
            </div>
            <div>
              <dt>Hires</dt>
              <dd>{formatCount(runStats.applications_joined)}</dd>
            </div>
            <div>
              <dt>Stalled</dt>
              <dd>{formatCount(runStats.cases_stalled)}</dd>
            </div>
            {analysis.data?.analysis_id && (
              <div>
                <dt>Analysis</dt>
                <dd>
                  #{analysis.data.analysis_id}
                  {analysis.data.model ? ` · ${analysis.data.model}` : ""}
                </dd>
              </div>
            )}
          </dl>
        )}
      </header>

      <section className="section">
        <div className="section__head">
          <h2>Tier 1 KPIs</h2>
          <div className="filters" role="group" aria-label="Filter KPIs by health">
            {["All", ...HEALTH_ORDER.filter((h) => counts[h] > 0)].map((option) => (
              <button
                key={option}
                type="button"
                className={`filter ${filter === option ? "filter--on" : ""}`}
                onClick={() => setFilter(option)}
              >
                {option}
                <span className="filter__count">
                  {option === "All" ? tiles.length : counts[option]}
                </span>
              </button>
            ))}
          </div>
        </div>

        {visible.length ? (
          <div className="tiles">
            {visible.map((kpi) => (
              <KpiTile
                key={kpi.kpi_id}
                kpi={kpi}
                rootCauseCount={causeCountById[kpi.kpi_id] || 0}
                onSelect={jumpTo}
              />
            ))}
          </div>
        ) : (
          <p className="empty">
            No Tier 1 KPI data loaded.{" "}
            {kpiHealth.error ? kpiHealth.error.message : "Run 04_compute_kpis_to_postgres.py first."}
          </p>
        )}
      </section>

      <section className="section">
        <div className="section__head">
          <h2>Root causes — Critical KPIs</h2>
          {!!criticalKpis.length && (
            <span className="section__note">
              {criticalKpis.length} Critical Tier 1 {criticalKpis.length === 1 ? "KPI" : "KPIs"},
              ranked by evidence and business impact
            </span>
          )}
        </div>

        {summary?.overall_observation && (
          <p className="observation">
            <strong>Overall.</strong> {summary.overall_observation}
          </p>
        )}

        {criticalKpis.length ? (
          criticalKpis.map((kpi) => (
            <CriticalKpiSection kpi={kpi} key={kpi.kpi_id} detailAvailable={detailAvailable} />
          ))
        ) : (
          <p className="empty">
            {analysis.status === "error" && analysis.error?.code === "no_analysis" ? (
              <>
                No root-cause analysis has been stored yet. Run{" "}
                <code>python root_cause_agent.py</code> and reload.
              </>
            ) : analysis.status === "error" ? (
              analysis.error.message
            ) : (
              <>
                No Tier 1 KPI is Critical in this run, so there is nothing to diagnose. The agent
                only analyses KPIs the KPI engine flagged Critical.
              </>
            )}
          </p>
        )}
      </section>

      <GapCoverage gaps={gaps.data?.gaps} />
    </div>
  );
}
