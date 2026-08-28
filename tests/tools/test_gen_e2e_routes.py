"""What the browser tier's route file has to hold: every scenario, as the specs read it.

`tests/e2e/routes.json` is the contract between the two tiers, and it is a checked-in copy of
something the Python tier owns. A copy that drifts is a browser tier sweeping yesterday's pages
while every Python leaf stays green, so the file is regenerated here and compared byte for byte.
"""

import json
from pathlib import Path

from tests.view.scenarios import SCENARIOS
from tools import gen_e2e_routes

# The prefix every fragment URL carries, spelled out here rather than imported: the generator
# derives it from the constants the app mints those URLs from, and a leaf that imported the
# same derivation could not see it move.
FRAGMENT = "/fragment/"


def test_the_checked_in_routes_file_is_what_the_generator_writes(tmp_path: Path) -> None:
    """`tests/e2e/routes.json` is byte for byte what `SCENARIOS` generates today."""
    # If the generator writes the file fresh into a scratch directory...
    fresh = tmp_path / "routes.json"
    gen_e2e_routes.write(fresh)
    # ...then the tracked copy the specs read is the same bytes, or it has drifted.
    assert fresh.read_bytes() == gen_e2e_routes.ROUTES.read_bytes(), (
        f"`{gen_e2e_routes.ROUTES.name}` has drifted from `SCENARIOS` —"
        f" regenerate it with `{gen_e2e_routes.COMMAND}`"
    )


def test_every_scenario_reaches_the_file_whole() -> None:
    """Every scenario is in the file with its title, group and note intact, fragments included.

    The browser tier filters — it sweeps the full pages and leaves the fragments to the specs
    that drive them — so a generator that dropped the fragments would decide that for it.
    """
    # If the file is read back as data...
    listed = json.loads(gen_e2e_routes.ROUTES.read_text())
    # ...then it is every scenario, in registry order, said the way the specs read it...
    assert listed == [
        {
            "route": route,
            "url": scenario.url,
            "title": scenario.title,
            "group": scenario.group.value,
            "note": scenario.note,
            "fragment": scenario.url.startswith(FRAGMENT),
        }
        for route, scenario in SCENARIOS.items()
    ]
    # ...and it carries both kinds, so the flag the specs filter on tells two things apart.
    kinds = {entry["fragment"] for entry in listed}
    assert kinds == {True, False}
