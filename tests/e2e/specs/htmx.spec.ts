import { expect, test, type Page } from "@playwright/test";
import { scenario } from "./scenarios";
import { watch } from "./watch";

/**
 * What htmx does to a node page, driven in a real browser.
 *
 * `tests/view/test_bounds.py` proves every `/fragment/…` route answers 200, and
 * `test_every_link_that_swaps_the_pane_lands_the_pane_in_the_pane` reads the six swap attributes
 * the way htmx resolves them — inheritance and all. What neither can see is where the response
 * lands: served HTML reads the attributes, only a browser reads what they do
 * (`.claude/rules/viewer-ui.md`). So every leaf here asserts the target as well as the markup.
 *
 * The pages are the scenario list's, named by route template rather than by URL, so a scenario
 * that moves takes this spec with it.
 */

const SESSION = "/session/{session_id}";
const TURN = "/session/{session_id}/thread/{source}/turn/{turn_id}";
const TOOL = "/session/{session_id}/thread/{source}/tool/{tool_call_id}";
const UNATTACHED = "/session/{session_id}/unattached";
const SESSION_NUMBERS = "/fragment/numbers/session/{session_id}";
const RUN_NUMBERS = "/fragment/numbers/session/{session_id}/run/{run_id}";

// What the popover rides on, counted through `page.on('request')`.
const NUMBERS = "/fragment/numbers/";

/** The title the reading pane's own body carries — never an expansion's, which nests under it. */
function paneTitle(page: Page): Promise<string> {
  return page.locator('#reading-pane > [data-body] > h1 [data-field="title"]').innerText();
}

type Row = { key: string; href: string; x: number; y: number };

/**
 * The nearest NavTree row to the selection a reader could click where it stands, and the point
 * to click it at.
 *
 * By coordinates because `locator.click()` scrolls its target into view first, and a spec that
 * measures the scroller afterwards has measured the driver (`.claude/rules/viewer-ui.md`). The
 * row has to be inside the scroller's own box and reachable at its centre — the open path clamps
 * over the rows passing under it, and a click aimed at a covered row lands on the cover. Nearest
 * because a sibling's page opens the same level of the same tree: a row further off would swap in
 * a tree of another length, and the browser would clamp the scroll this leaf is about.
 */
async function visibleRow(page: Page): Promise<Row> {
  const found = await page.evaluate(() => {
    const tree = document.getElementById("nav-tree");
    if (!tree) return null;
    const rows = Array.from(tree.querySelectorAll("li.node"));
    const selected = rows.findIndex((row) => row.hasAttribute("data-selected"));
    const bounds = tree.getBoundingClientRect();
    const reachable = rows.flatMap((row, index) => {
      const link = row.querySelector("a");
      const key = row.getAttribute("data-nav-tree");
      if (!link || !key || index === selected) return [];
      const box = link.getBoundingClientRect();
      const x = box.left + box.width / 2;
      const y = box.top + box.height / 2;
      if (box.top < bounds.top || box.bottom > bounds.bottom) return [];
      if (!link.contains(document.elementFromPoint(x, y))) return [];
      return [{ key, href: link.getAttribute("href") ?? "", x, y, away: Math.abs(index - selected) }];
    });
    reachable.sort((one, other) => one.away - other.away);
    return reachable[0] ?? null;
  });
  expect(found, "no NavTree row stood unselected and uncovered inside the scroller").not.toBeNull();
  return found as Row;
}

test.describe("a NavTree taller than its scroller", () => {
  // The tool call's ten rows in 220 px, the viewport the recorded witness of this scroller was
  // taken at. At the config's 1400×900 every NavTree in this corpus fits, and a scroller that
  // cannot move proves nothing about one that can.
  test.use({ viewport: { width: 1400, height: 220 } });

  test("a row click swaps the pane in place and leaves the NavTree where it stood", async ({
    page,
  }) => {
    // If a tool call's page is open, deep enough that the tree arrives scrolled...
    const problems = await watch(page);
    await page.goto(scenario(TOOL).url);
    const scroller = page.locator("#nav-tree");
    await expect(scroller.locator("[data-selected]")).toHaveCount(1);
    // ...with the scrollbar on the element the swap does not replace...
    const room = await scroller.evaluate((tree) => tree.scrollHeight - tree.clientHeight);
    expect(room, "`#nav-tree` is not what scrolls, so a click cannot be shown to leave it alone")
      .toBeGreaterThan(0);
    // ...and `static/nav-tree.js` having centred the selection on the way in...
    const stood = await scroller.evaluate((tree) => tree.scrollTop);
    expect(stood, "the tree did not open scrolled, so this leaf would prove nothing")
      .toBeGreaterThan(0);

    // ...then clicking a row that is already on screen...
    const row = await visibleRow(page);
    await page.mouse.click(row.x, row.y);
    await expect(page.locator(`#nav-tree-rows [data-selected="${row.key}"]`)).toHaveCount(1);

    // ...lands the pane in the pane. htmx aims at the clicked element by default, so the failure
    // this catches is a second `#reading-pane` standing inside the NavTree while the first still
    // reads the node the reader came from — the URL changes and the page does not.
    await expect(page.locator("#reading-pane")).toHaveCount(1);
    await expect(page.locator("#browser > #reading-pane")).toHaveCount(1);
    // ...reading the node the row named, which the crumb chain ends on...
    await expect(page.locator("#reading-pane [data-crumb]").last()).toHaveAttribute(
      "data-crumb",
      row.key,
    );
    expect(new URL(page.url()).pathname).toBe(new URL(row.href, page.url()).pathname);

    // ...and the reader keeps their place, because the scroller is not what swapped.
    const after = await scroller.evaluate((tree) => tree.scrollTop);
    expect(after, "the swap moved the NavTree's scroller").toBe(stood);
    expect(problems, problems.join("\n  ")).toEqual([]);
  });
});

test("the preset control points at the node a click just swapped in", async ({ page }) => {
  // If a session's page is open and a reader clicks a row...
  const problems = await watch(page);
  await page.goto(scenario(SESSION).url);
  const row = await visibleRow(page);
  await page.mouse.click(row.x, row.y);
  await expect(page.locator(`#nav-tree-rows [data-selected="${row.key}"]`)).toHaveCount(1);

  // ...then the three presets offer that node under each depth, and not the one they left. The
  // control renders inside `#nav-tree-rows` for exactly this reason (`.claude/rules/viewer-ui.md`).
  const presets = page.locator("#nav-tree-rows .presets a[data-nav]");
  await expect(presets).toHaveCount(3);
  const wanted = new URL(row.href, page.url()).pathname;
  const paths = await presets.evaluateAll((links) =>
    links.map((link) => (link as HTMLAnchorElement).pathname),
  );
  expect(paths).toEqual([wanted, wanted, wanted]);
  expect(problems, problems.join("\n  ")).toEqual([]);
});

test("pointing at a row fetches its popover once and stands it at the row's top", async ({
  page,
}) => {
  // If every fetch of a row's numbers is counted...
  const problems = await watch(page);
  const fetched: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith(NUMBERS)) fetched.push(path);
  });
  await page.goto(scenario(SESSION).url);

  // ...and a reader points at one row of each kind that carries numbers — a session's, a turn's
  // and an agent run's, the three routes the popover is served under...
  const wanted: string[] = [];
  for (const kind of ["session", "turn", "run"]) {
    const row = page.locator(`#nav-tree li.node.${kind}`).first();
    const at = (await row.boundingBox())!;
    await page.mouse.move(at.x + at.width / 2, at.y + at.height / 2);
    const popover = row.locator(".popover");
    await expect(popover, `no popover came back for a ${kind} row`).toBeVisible();
    // ...each stands at the top of the row it belongs to, which is the one thing about a
    // popover's place no stylesheet can reach (`static/nav-tree.js`).
    const stood = (await popover.boundingBox())!;
    expect(Math.abs(stood.y - at.y), `the ${kind} popover missed its row`).toBeLessThan(2);
    wanted.push((await row.locator(".peek").getAttribute("hx-get"))!);
  }
  // Three fetches for three rows, and no fourth from a row the pointer passed over.
  expect(fetched).toEqual(wanted);
  // Two of the three are the very URLs the scenario list pins; the turn row's is that route at
  // another node, because the turn the list pins sits in a different session.
  expect(wanted).toContain(scenario(SESSION_NUMBERS).url);
  expect(wanted).toContain(scenario(RUN_NUMBERS).url);

  // ...and pointing at a row a second time fetches nothing, because the trigger fires once.
  const first = page.locator("#nav-tree li.node.session").first();
  const at = (await first.boundingBox())!;
  await page.mouse.move(0, 0);
  await page.mouse.move(at.x + at.width / 2, at.y + at.height / 2);
  // A bounded absence: the trigger carries `delay:200ms`, so a second fetch would have gone out
  // and come back well inside this wait.
  await page.waitForTimeout(600);
  expect(fetched).toEqual(wanted);
  // ...and a second one would have stood beside the first, since the swap appends.
  await expect(first.locator(".popover")).toHaveCount(1);
  expect(problems, problems.join("\n  ")).toEqual([]);
});

test("tabbing onto a row's link fetches the same popover", async ({ page }) => {
  const problems = await watch(page);
  const fetched: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith(NUMBERS)) fetched.push(path);
  });
  await page.goto(scenario(SESSION).url);

  // If a reader tabs off the last preset button — the preset control renders inside
  // `#nav-tree-rows`, directly above the rows, so the next stop is the first row's link...
  await page.locator("#nav-tree-rows .presets a").last().focus();
  await page.keyboard.press("Tab");
  const row = page.locator("#nav-tree li.node").first();
  await expect(row.locator("a")).toBeFocused();

  // ...then the keyboard reaches what the pointer reaches: `focusin` bubbles where `focus` does
  // not, which is why the trigger names it (`_nav_tree.html`).
  await expect(row.locator(".popover")).toBeVisible();
  expect(fetched).toEqual([await row.locator(".peek").getAttribute("hx-get")]);
  expect(problems, problems.join("\n  ")).toEqual([]);
});

// A node's body opened in the log that lists it, one case per kind the fragment is served for.
// A turn and an agent run each stand a count and a link to their own page; an api call is the
// one kind whose body lists a level — the tools it called, through the same log macro — and its
// rows are where an expansion could open another if `opens` were not turned off there.
const EXPANSIONS = [
  { route: SESSION, kind: "turn", nested: "", counted: "" },
  { route: UNATTACHED, kind: "run", nested: "", counted: "" },
  { route: TURN, kind: "call", nested: "tools", counted: "tool_calls" },
];

for (const { route, kind, nested, counted } of EXPANSIONS) {
  test(`a log row's View button opens a ${kind}'s body in place`, async ({ page }) => {
    // If a page with a children log is open...
    const problems = await watch(page);
    await page.goto(scenario(route).url);
    const reading = await paneTitle(page);
    // ...at a row with something under it, where the kind has a level to list: an api call that
    // called no tool would open a body with nothing in it to nest.
    const key = await page.evaluate((column) => {
      const rows = Array.from(document.querySelectorAll("tr[data-child]"));
      const wanted = rows.find((row) => {
        if (!row.querySelector("[data-view]")) return false;
        if (!column) return true;
        const cell = row.querySelector(`[data-column="${column}"] [data-field="${column}"]`);
        return Number((cell?.textContent ?? "").replace(/\D/g, "")) > 0;
      });
      return wanted?.querySelector("[data-view]")?.getAttribute("data-view") ?? null;
    }, counted);
    expect(key, `no ${kind} row on this page could be opened`).not.toBeNull();

    // ...and the reader opens it...
    await page.locator(`[data-view="${key}"]`).click();

    // ...then the child's body stands under that row and nowhere else.
    const expansion = page.locator(`tr[data-child="${key}"] + tr.expansion`);
    await expect(expansion).toBeVisible();
    await expect(expansion).toHaveAttribute("data-expansion", kind);
    if (nested) {
      await expect(expansion.locator(`[data-log="${nested}"] tr[data-child]`).first()).toBeVisible();
    }
    // It stops one level down: a row inside an expansion opens no expansion of its own, because
    // an accordion of accordions is a page and the node already has one
    // (`.claude/rules/viewer-ui.md`).
    await expect(expansion.locator("[data-view]")).toHaveCount(0);
    // And the pane the reader was on did not move — the fetch targets the row, not the pane.
    await expect(page.locator("#reading-pane")).toHaveCount(1);
    expect(await paneTitle(page)).toBe(reading);
    expect(problems, problems.join("\n  ")).toEqual([]);
  });
}

test("a cut value's more link fetches the rest of it into the pane", async ({ page }) => {
  // `?detail=` only goes down from its 4,000-character default (`view/bounds.py`), and the
  // redacted corpus holds no value that long — so the width the URL asks for is what cuts a
  // value here. Twenty characters cuts the tool call's arguments; the rest is the fetch below.
  const size = 20;
  const problems = await watch(page);
  await page.goto(`${scenario(TOOL).url}?detail=${size}`);
  const detail = page.locator('[data-detail="input"]');
  const reading = await paneTitle(page);
  const before = await detail.locator('[data-field="input"]').innerText();
  const cut = Number((await detail.locator('[data-field="cut"]').innerText()).replace(/\D/g, ""));
  expect(cut, "the pane offered no rest of the value to fetch").toBeGreaterThan(0);

  // If the reader asks for the rest of it...
  await detail.locator('[data-whole="input"]').click();

  // ...the whole value replaces the section that previewed it, under the same name: what stands
  // there now carries the value's own length, which a preview never does.
  await expect(detail).toHaveAttribute("data-value", String(size + cut));
  const after = await detail.locator('[data-field="value"]').innerText();
  expect(after.length).toBeGreaterThan(before.length);
  expect(after.length).toBeGreaterThanOrEqual(size + cut);
  // ...nothing is left offering the rest, because there is no rest...
  await expect(page.locator('[data-whole="input"]')).toHaveCount(0);
  // ...and the fetch landed in the detail rather than in the pane around it.
  await expect(page.locator("#reading-pane")).toHaveCount(1);
  expect(await paneTitle(page)).toBe(reading);
  expect(problems, problems.join("\n  ")).toEqual([]);
});

test("the children log's next page turns the log and leaves the node alone", async ({ page }) => {
  // `?log=` only goes down from its hundred-row default and the widest level in this corpus is
  // four turns, so the knob is what makes a level page at all here.
  const problems = await watch(page);
  await page.goto(`${scenario(SESSION).url}?log=2`);
  const reading = await paneTitle(page);
  const counted = await page.locator('.log > h2 [data-field="children"]').innerText();
  const first = await page.locator("tr[data-child]").first().getAttribute("data-child");

  // A page load and not a swap: the pager is plain links, because turning a page is a page load
  // (`_logs.html`). What a browser proves is that the load comes back on the same node.
  await page.locator('[data-page="next"]').click();
  await page.waitForURL(/[?&]page=2/);

  // The log turned...
  await expect(page.locator("tr[data-child]").first()).not.toHaveAttribute("data-child", first!);
  // ...the heading still counts the level rather than the page...
  expect(await page.locator('.log > h2 [data-field="children"]').innerText()).toBe(counted);
  // ...the pane is reading the node it was reading...
  expect(await paneTitle(page)).toBe(reading);
  // ...and the way back is on offer, which page one did not carry.
  await expect(page.locator('[data-page="previous"]')).toHaveCount(1);
  expect(problems, problems.join("\n  ")).toEqual([]);
});
