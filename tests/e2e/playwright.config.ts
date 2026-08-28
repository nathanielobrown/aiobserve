import { defineConfig } from "@playwright/test";

// The gallery's own port for this tier, and never 8477 (a live viewer) or 8478 (the gallery
// default a reader may have open) — `.claude/rules/viewer-ui.md`. `mise run gallery` claims the
// port and exits if something already holds it, so a stale server cannot be swept by mistake.
const PORT = 8479;
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./specs",
  // One worker, in declaration order: the sweep counts the pages it visited in an `afterAll`,
  // and a count split across workers would see a fraction of them.
  fullyParallel: false,
  workers: 1,
  // A `.only` left in a spec would narrow the sweep to one page and still report green.
  forbidOnly: !!process.env.CI,
  reporter: "list",
  use: {
    baseURL: BASE_URL,
    // The viewport the viewer's manual witnesses are taken at (`.claude/rules/viewer-ui.md`),
    // and the one scheme this tier covers. Dark mode and the <=900px layout are real page
    // shapes deliberately left out for now, so the default is stated rather than inherited.
    viewport: { width: 1400, height: 900 },
    colorScheme: "light",
    trace: "retain-on-failure",
  },
  // The app under test is the gallery over the redacted fixture corpus, served with the real
  // `default-src 'self'` header — the store it builds is its own, so this tier can reach no
  // private data. `cwd` is the repo root because the task runs `python -m tests.gallery.serve`.
  webServer: {
    command: `mise run gallery --port ${PORT}`,
    cwd: "../..",
    url: `${BASE_URL}/gallery`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
