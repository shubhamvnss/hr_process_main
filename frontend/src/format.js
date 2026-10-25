/**
 * Shared display formatting, so the KPI tiles and the root-cause screens present the same number
 * the same way.
 *
 * Three conventions from the pipeline that are easy to get backwards:
 *
 *  - variance_vs_target_pct is positive when a KPI is *worse* than target, whichever direction the
 *    KPI points (kpi_core.variance_pct). A bare "+35.5%" would read as "the value went up", so it
 *    is always rendered with the words "worse"/"better than target".
 *  - trend_direction means improving/worsening, not up/down in value
 *    (04_compute_kpis_to_postgres.py add_trend).
 *  - every rate in this process belongs to a case type. A percentage without its denominator is
 *    meaningless here, because requisition-level and application-level rates are never pooled
 *    (BRD section 6.1), so affected-case counts are always rendered with their case type.
 */

export function formatValue(value, unit) {
  if (value === null || value === undefined) return "—";
  if (unit === "USD") return `$${Math.round(value).toLocaleString()}`;
  // Ratios carry their meaning in the second decimal: an imbalance index of 1.99 against a 1.6
  // target must not render as "2", which reads as a round number rather than a near-2x spread.
  const places = unit === "ratio" ? 2 : 1;
  const rounded = Number.isInteger(value) ? value : Number(Number(value).toFixed(places));
  return rounded.toLocaleString(undefined, { maximumFractionDigits: places });
}

export function unitSuffix(unit) {
  if (!unit || unit === "USD") return "";
  return unit === "%" ? "%" : ` ${unit}`;
}

export function varianceLabel(variance) {
  if (variance === null || variance === undefined) return { text: "—", tone: "neutral" };
  if (Math.abs(variance) < 0.05) return { text: "on target", tone: "neutral" };
  if (variance > 0) return { text: `${variance.toFixed(1)}% worse than target`, tone: "bad" };
  return { text: `${Math.abs(variance).toFixed(1)}% better than target`, tone: "good" };
}

export const TREND = {
  up: { glyph: "▲", tone: "good", label: "improving vs previous run" },
  down: { glyph: "▼", tone: "bad", label: "worsening vs previous run" },
  flat: { glyph: "—", tone: "neutral", label: "unchanged vs previous run" },
};

/** "81.3 days", "82.5%", "$1,972" - value and unit together. */
export function formatMetric(value, unit) {
  if (value === null || value === undefined) return "—";
  return `${formatValue(value, unit)}${unitSuffix(unit)}`;
}

export function formatTimestamp(value) {
  if (!value) return "";
  return String(value).replace("T", " ").slice(0, 16);
}

export function formatCount(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString();
}

/** "REQUISITION" -> "requisitions", "APPLICATION" -> "applications". */
export function caseTypeLabel(caseType, count = 2) {
  const noun = caseType === "REQUISITION" ? "requisition" : "application";
  return count === 1 ? noun : `${noun}s`;
}

/** "1,040 applications (39.0%) — applications reaching interview" */
export function affectedLabel(affected) {
  if (!affected) return null;
  const count = affected.count ?? affected.affected_cases_count;
  const pct = affected.percentage ?? affected.affected_cases_pct;
  const caseType = affected.case_type ?? affected.affected_case_type;
  if (count === null || count === undefined) return null;
  const pctText = pct === null || pct === undefined ? "" : ` (${Number(pct).toFixed(1)}%)`;
  return `${formatCount(count)} ${caseTypeLabel(caseType, count)} affected${pctText}`;
}

/** The pipeline stores comma/semicolon separated id lists; the API returns them unchanged. */
export function splitIds(value) {
  if (Array.isArray(value)) return value.filter(Boolean);
  if (!value) return [];
  return String(value)
    .split(/[;,]/)
    .map((v) => v.trim())
    .filter(Boolean);
}
