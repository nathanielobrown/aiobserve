import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * The scenario list the viewer tier pins, as this tier reads it.
 *
 * `tools/gen_e2e_routes.py` writes `routes.json` from `tests/view/scenarios.py:SCENARIOS`, and a
 * leaf beside the generator compares the two byte for byte — so a spec that names a route
 * template here drives the URL that tier pins, and a scenario that moves takes both tiers with
 * it. Nothing under this directory types a session id.
 */

export type Scenario = {
  // The route's own path template: the scenario's key on both sides, and what a spec names.
  route: string;
  url: string;
  title: string;
  group: string;
  note: string;
  // Derived by the generator off the root the app mints fragment URLs from, so what counts as
  // a fragment is answered once, in Python.
  fragment: boolean;
};

export const SCENARIOS: Scenario[] = JSON.parse(
  readFileSync(join(__dirname, "..", "routes.json"), "utf8"),
);

// A fragment is partial HTML with no `base.html` around it: nothing to load cold and no console
// of its own. The sweep visits the rest; the htmx spec opens fragments mid-interaction instead.
export const fullPages = SCENARIOS.filter((scenario) => !scenario.fragment);

/** The scenario one route template names, or a failure naming the template that went missing. */
export function scenario(route: string): Scenario {
  const found = SCENARIOS.find((candidate) => candidate.route === route);
  if (!found) {
    throw new Error(
      `routes.json pins no scenario at ${route}.` +
        ` Regenerate it with: uv run python -m tools.gen_e2e_routes`,
    );
  }
  return found;
}
