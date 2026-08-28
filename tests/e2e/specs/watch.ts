import type { Page } from "@playwright/test";

/**
 * Everything a page complains about while a spec drives it, collected into one growing list.
 *
 * The list is the whole point of this tier: a `TestClient` reads the bytes a route answers with
 * and has no eye for an inline `<style>` the policy refuses, a script that throws, or a fetch
 * that came back 404. Install it before the first `goto` — two of the four listeners ride an
 * init script — and assert it empty at the end of the spec.
 */

declare global {
  interface Window {
    reportViolation: (detail: string) => void;
  }
}

// The header htmx puts on every fetch it makes. Scoped to that rather than every response,
// because a browser asks for `/favicon.ico` on its own and that 404 is the server having nothing
// to say — and to that rather than to `/fragment/`, because a pane swap fetches a page URL.
const HTMX_REQUEST = "hx-request";

export async function watch(page: Page): Promise<string[]> {
  const problems: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      problems.push(`console error: ${message.text()}`);
    }
  });
  page.on("pageerror", (error) => {
    problems.push(`page error: ${error.message}`);
  });
  // Every htmx fetch a spec sets off, held to 200: a swap that lands the right markup in the
  // right place after a 404 is a swap of an error page.
  page.on("response", (response) => {
    if (response.request().headers()[HTMX_REQUEST] && response.status() !== 200) {
      problems.push(`htmx ${response.status()}: ${response.url()}`);
    }
  });
  // What the CSP refused, which the browser reports to the document rather than over the
  // protocol. The listener rides an init script and hands what it hears to a binding: both are
  // the driver's, so neither is the inline script `default-src 'self'` would refuse.
  await page.exposeFunction("reportViolation", (detail: string) => {
    problems.push(`CSP: ${detail}`);
  });
  await page.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (event) => {
      const blocked = event.blockedURI || "an inline source";
      window.reportViolation(
        `${event.violatedDirective} blocked ${blocked} (${event.sourceFile}:${event.lineNumber})`,
      );
    });
  });
  return problems;
}
