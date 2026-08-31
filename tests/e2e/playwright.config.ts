import { defineConfig } from "@playwright/test";

// The gallery's own port for this tier, and never 8477 (a live viewer) or 8478 (the gallery
// default a reader may have open) — `.claude/rules/viewer-ui.md`. `mise run gallery` claims the
// port and exits if something already holds it, so a stale server cannot be swept by mistake.
const PORT = 8479;

// The three things a second implementation of the viewer needs to move, and nothing else: the
// specs read `routes.json` and `baseURL` and are genuinely unchanged. Unset, every one of these
// is what it was before the seam existed — the Python gallery, at the port above.
//
// The Rust prototype points them at its own binary over a store the Python gallery built
// (`plans/rust-prototype/design.md`); its readiness URL is `/` because the scenario index at
// `/gallery` is Python's, and no spec visits it.
const BASE_URL = process.env.HYPHAE_E2E_BASE_URL ?? `http://127.0.0.1:${PORT}`;
const SERVER = process.env.HYPHAE_E2E_SERVER ?? `mise run gallery --port ${PORT}`;
const READY = process.env.HYPHAE_E2E_READY ?? `${BASE_URL}/gallery`;

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
    command: SERVER,
    cwd: "../..",
    url: READY,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
