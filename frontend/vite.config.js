import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base: "/" (not "./" as in the Corporate Loan dashboard) because this app has a nested route:
// /root-cause/:findingId. With a relative base the built bundle resolves its assets against the
// current path, so a hard reload or a shared deep link to /root-cause/RC-T1-01-2 requests
// /root-cause/assets/index-*.js and gets a 404. Finding ids are addressable by design here, so
// deep links have to survive a reload. Serve the build behind a history fallback to index.html.
export default defineConfig({
  plugins: [react()],
  base: "/",
  server: { port: 5174 },
  preview: { port: 5174 },
});
