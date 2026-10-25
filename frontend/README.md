# Talent Acquisition — KPI dashboard (React)

Four sections, fed entirely by the Root Cause API:

1. **Run header** — the KPI run being shown, the analysis that explains it, and where the data came
   from (`database` or the API's JSON-file fallback).
2. **Tier 1 KPI tiles** — one tile per headline KPI. The current value is the dominant element;
   target, variance and **the cohort the KPI was measured on** sit beneath it, with a health badge
   and a trend marker vs the previous run. Critical tiles are buttons: clicking one jumps to that
   KPI's root causes.
3. **Root causes** — for every Critical Tier 1 KPI: what it was measured on, its performance
   summary, why it is not performing, and the ranked root causes with statistics, GAP references,
   linked Tier 2 KPIs and affected-case counts. Each cause carries a **View evidence** button.
4. **Gap coverage** — which GAP ids the analysis cites, against how many cases the deterministic
   rules actually detected for each one. A GAP cited on thin detection volume is visible here.

Nothing is recomputed in the browser. The agent's `frontend_summary` is a presentation-ready layer
by contract (prompt section 28), so the UI renders it as-is — it never re-ranks or re-aggregates.

## Run it

```bash
cd frontend
npm install
npm run dev            # http://localhost:5174
```

The API must be running:

```bash
cd ..
uvicorn backend.main:app --reload --port 8010
```

## Where the data comes from

Unlike the Corporate Loan dashboard — which reads JSON files from `public/data/` — every screen
here reads the API, so a fresh agent run appears on reload with no file copying.

| Screen | Endpoint |
|---|---|
| Tiles + run header | `GET /api/root-cause/kpi-health` |
| Fix with AI (sections 1–5) | `GET /api/root-causes/{id}/fix-with-ai` |
| Impacted-case table | `GET /api/root-causes/{id}/impacted-cases` |
| To-Be workflow | `GET /api/root-causes/{id}/to-be-workflow` |
| Root-cause sections | `GET /api/root-cause/latest` |
| Gap coverage | `GET /api/root-cause/gaps` |
| Evidence detail route | `GET /api/root-cause/findings/{finding_id}` |

```bash
export VITE_API_BASE_URL=http://localhost:8010     # default
```

Each section loads and fails **independently**, which matters because the pipeline has a real
intermediate state: after `04_compute_kpis_to_postgres.py` but before `root_cause_agent.py`, the
KPI tiles are available and the analysis is not. In that state the tiles render normally and the
root-cause section says what to run. The reverse also holds — if the KPI tables cannot be read,
the tiles fall back to the Critical KPIs named in the analysis.

## Degraded states the UI handles explicitly

| Condition | What you see |
|---|---|
| API not running | Red banner naming `VITE_API_BASE_URL` and the uvicorn command |
| No KPI run | Tiles section explains to run `04_compute_kpis_to_postgres.py` |
| No stored analysis | Root-cause section explains to run `root_cause_agent.py` |
| No Critical KPI in the run | Says there is nothing to diagnose — this is a valid outcome, not an error |
| API served the JSON fallback | Amber banner; the **View evidence** drill-down is disabled, because the detail route needs the stored analysis |
| Analysis stored with validation problems | Amber banner listing the first five, so a flagged finding is never read as clean |

## The evidence detail route

Every root cause links to

    /root-cause/:findingId            e.g. /root-cause/RC-T1-01-2

The id is derived from the Tier 1 KPI id and the cause's rank (`findingId()` in
`src/api/client.js`), exactly as the agent derives it
(`backend/services/root_cause_store.finding_id`), so no lookup round trip is needed.

The page renders, in order: the root cause and its causal mechanism, the Tier 1 KPI context with
its cohort, the impact statistics, the evidence chain, then each typed evidence block the agent
supplied — Tier 2, gap detections, stage, activity, rework, capacity, segment and stalled evidence.

**The limitations block is rendered as prominently as the finding itself.** Alternative
explanations and `evidence_limitations` get their own highlighted section, because the BRD requires
that an association is never read as proven causation — and in this dataset ~65% of candidates who
waited just as long still joined.

## The Fix with AI screen

Route: `/root-cause/:findingId/fix-with-ai` — reached from the **Fix with AI** button on every
root cause. Six sections, in the order the journey needs them:

1. **Impacted cases** — the headline count and what the root cause costs those cases, with the
   case list itself behind a button
2. **KPIs associated with the impacted cases** — the Tier 1 and Tier 2 position, plus the
   impacted-vs-unimpacted cohort comparison
3. **Most representative case** — the typical impacted case, with why it was chosen
4. **What happened** — the assembled narrative and the key-event timeline
5. **Recommended agents** — primary and supporting, with owner, intervention focus and the KPIs
   to re-measure
6. **To-Be state workflow** — the six headline counts and the redesigned activity list

**Loading strategy.** Sections 1–5 come from one fast deterministic endpoint and render on page
load. Two things are deliberately lazy:

| Lazy part | Why |
|---|---|
| The impacted-case table | A long table nobody needs until they ask for it |
| The To-Be workflow | It calls a model and takes a minute or two. Firing a billable generation on every page view would be the wrong default — the user chooses when to spend it |

## Files

| File | Role |
|---|---|
| `src/App.jsx` | Routing: `/`, `/root-cause/:findingId`, `/root-cause/:findingId/fix-with-ai` |
| `src/KpiDashboard.jsx` | The dashboard: header, tiles, root causes, gap coverage |
| `src/pages/RootCauseDetail.jsx` | One root cause with its full evidence |
| `src/pages/FixWithAI.jsx` | Impacted cases, representative case, what happened, agents, To-Be |
| `src/api/client.js` | Every endpoint, one `ApiError` type carrying the backend's structured detail |
| `src/format.js` | Shared formatting — variance wording, trend glyphs, case-type labels |
| `src/dashboard.css` | Design tokens + dashboard styles (dark by default) |
| `src/rootcause.css` | Detail-screen styles; reads the same tokens |
| `src/fixwithai.css` | Fix-with-AI screen styles; reads the same tokens |

## Theme

Dark by default and unconditional, so the screens look the same on every machine. The light
palette is kept in `dashboard.css` under `[data-theme="light"]` — add `data-theme="light"` to
`<html>` in `index.html` to switch.
