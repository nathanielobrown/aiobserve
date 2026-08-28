"""`tests/e2e/routes.json`: every scenario the viewer tier pins, as data a Playwright spec reads.

Run by hand — `uv run python -m tools.gen_e2e_routes` — and read back by
`tests/tools/test_gen_e2e_routes.py`, which regenerates into a scratch directory and compares
bytes. Not a cog block like the rest of `tools/`: what it writes is a file another runtime
loads, not a table spliced into a document.

Every scenario reaches the file whole, fragments included, so the browser tier decides what to
sweep. `fragment` says which is which, off the same root `gen_routes` derives from the constants
the app mints fragment URLs from — a prefix typed into TypeScript would be a second answer.
"""

import json
from pathlib import Path

from tests.view.scenarios import SCENARIOS, Scenario
from tools.gen_routes import FRAGMENT_ROOT

ROOT = Path(__file__).resolve().parent.parent
ROUTES = ROOT / "tests" / "e2e" / "routes.json"
# What a failing staleness check tells the reader to run.
COMMAND = "uv run python -m tools.gen_e2e_routes"


def entry(route: str, scenario: Scenario) -> dict[str, str | bool]:
    """One scenario as a spec reads it: what it is called, where it lives, and what kind it is."""
    return {
        "route": route,
        "url": scenario.url,
        "title": scenario.title,
        "group": scenario.group.value,
        "note": scenario.note,
        "fragment": scenario.url.startswith(FRAGMENT_ROOT),
    }


def generate() -> str:
    """The whole file, in registry order — the order the gallery lists the scenarios in."""
    listed = [entry(route, scenario) for route, scenario in SCENARIOS.items()]
    return json.dumps(listed, indent=2) + "\n"


def write(path: Path) -> None:
    """Write the file, creating its directory if the tree does not hold one yet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generate())


def main() -> None:
    write(ROUTES)
    print(f"wrote {ROUTES.relative_to(ROOT)}: {len(SCENARIOS)} scenarios")


if __name__ == "__main__":
    main()
