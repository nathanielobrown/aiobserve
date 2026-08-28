"""What every scenario carries beyond a URL.

The rest of the tier sweeps the URLs; these leaves read the two fields nothing else forces. A
title is the gallery's link text and the browser tier's snapshot name, so an entry without one
is a page nobody can name, and two entries sharing one silently merge two baselines.
"""

from collections import Counter

import pytest

from tests.view.scenarios import SCENARIOS, Group


@pytest.mark.parametrize("route", sorted(SCENARIOS))
def test_every_scenario_says_what_its_page_shows_and_where_it_is_listed(route: str) -> None:
    """Every entry names its page in words and belongs to one of the gallery's headings."""
    scenario = SCENARIOS[route]
    assert scenario.title.strip(), route
    # A bare string would pass a `in Group` check, so the member itself is what is read: the
    # heading order the gallery renders comes off the enum, and a string has no place in it.
    assert isinstance(scenario.group, Group), route


def test_no_two_scenarios_share_a_title() -> None:
    """One title, one page.

    A title is Chromatic's snapshot name as well as the gallery's link, and a collision there
    is invisible: two pages quietly baseline against each other instead of failing.
    """
    counted = Counter(scenario.title for scenario in SCENARIOS.values())
    assert [title for title, count in counted.items() if count > 1] == []
