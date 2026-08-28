import { expect, test } from "@playwright/test";
import { fullPages, SCENARIOS } from "./scenarios";
import { watch } from "./watch";

/**
 * Every full page of the gallery, loaded cold in a real browser: the one thing the Python tier
 * structurally cannot see. `tests/view/test_bounds.py` already proves each of these URLs answers
 * 200 under budget through a `TestClient`, so nothing here re-proves that; what a `TestClient`
 * has no eye for is an inline `<style>` or `<script>` the CSP refuses, a script that throws, and
 * anything else the console carries.
 */

// What the sweep below actually visited, read back by the `afterAll` at the foot of the file.
const visited = new Set<string>();

test("the route file carries full pages for the sweep to visit", () => {
  // A filter that matched nothing would generate no tests at all, and a describe block with no
  // tests runs no hooks — so an empty sweep would report green with nothing to say. This is the
  // leaf that reds instead.
  expect(SCENARIOS.length).toBeGreaterThan(0);
  expect(fullPages.length).toBeGreaterThan(0);
  // ...and the file holds fragments too, filtered here rather than dropped upstream.
  expect(fullPages.length).toBeLessThan(SCENARIOS.length);
});

test.describe("every full page loads with nothing in the console", () => {
  for (const scenario of fullPages) {
    // Named by the scenario's title, which is the gallery's link text and, from slice 5, the
    // name of the page's Chromatic snapshot.
    test(scenario.title, async ({ page }) => {
      // If everything the page complains about is collected as it loads...
      const problems = await watch(page);

      // ...when the page is opened at its own URL...
      const response = await page.goto(scenario.url);
      expect(response?.status(), `${scenario.url} did not answer`).toBe(200);
      // ...and waited for by selector, which says what it is waiting for. `waitForFunction`
      // does resolve under this CSP in this runner, whatever the Python harness found
      // (`.claude/rules/viewer-ui.md`) — it just names a condition rather than a thing.
      await expect(page.locator("main")).toBeVisible();
      visited.add(scenario.url);

      // ...then the page said nothing, and a failure names the URL and everything it said.
      expect(problems, `${scenario.url}\n  ${problems.join("\n  ")}`).toEqual([]);
    });
  }

  test.afterAll(() => {
    // The sweep visited every full page the file carries and no other: a count on its own would
    // pass a run that opened one page fourteen times.
    expect(visited).toEqual(new Set(fullPages.map((scenario) => scenario.url)));
  });
});
