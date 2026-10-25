/**
 * App - routing for the Talent Acquisition dashboard.
 *
 * Routes
 *   /                         KPI health, KPI analysis and ranked root causes
 *   /root-cause/:findingId                one root cause with its full evidence chain
 *   /root-cause/:findingId/fix-with-ai    impacted cases, representative case, agents, To-Be
 *
 * Unlike the Corporate Loan dashboard, which reads JSON files from public/data/, every screen here
 * reads the Root Cause API (src/api/client.js). Each screen loads its own data and reports its own
 * failure, so one unavailable endpoint never blanks the whole page:
 *
 *   GET /api/root-cause/kpi-health   the Tier 1 tiles + run header   (works with no analysis yet)
 *   GET /api/root-cause/latest       frontend_summary + metadata     (the root-cause sections)
 *   GET /api/root-cause/gaps         the GAP coverage strip
 *   GET /api/root-cause/findings/:id one root cause, on the detail route
 *   GET /api/root-causes/:id/fix-with-ai  the Fix-with-AI analysis
 */
import { BrowserRouter, Route, Routes } from "react-router-dom";

import KpiDashboard from "./KpiDashboard.jsx";
import FixWithAIPage from "./pages/FixWithAI.jsx";
import RootCauseDetailPage from "./pages/RootCauseDetail.jsx";
import "./dashboard.css";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<KpiDashboard />} />
        <Route path="/root-cause/:findingId" element={<RootCauseDetailPage />} />
        <Route path="/root-cause/:findingId/fix-with-ai" element={<FixWithAIPage />} />
        <Route path="*" element={<KpiDashboard />} />
      </Routes>
    </BrowserRouter>
  );
}
