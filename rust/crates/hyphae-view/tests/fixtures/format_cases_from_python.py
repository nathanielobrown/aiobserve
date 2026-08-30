"""Write the printed-value cases the Rust `format` module is checked against.

A page is mostly these: a cost, a count, a span, how long ago. They are small enough to look
right and easy enough to get wrong — thousands separators, a negative delta's sign, the two
digits an `05m` is padded to — so the expected side comes from the Python that prints them
today rather than from a reading of it. Regenerate after either module changes:

    mise x -- python rust/crates/hyphae-view/tests/fixtures/format_cases_from_python.py \
        > rust/crates/hyphae-view/tests/fixtures/format_cases.json

Absent values are left out: Python spells one `None` and Rust spells it `None` too, so the
Rust leaf asserts those itself rather than reading a null out of here.
"""

import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "src"))

from hyphae.view import format as fmt  # noqa: E402

MONEY = [0.0, 0.004, 0.005, 0.015, 1.0, 12.3456, 1234.5, 0.12345]
COUNTS = [0, 7, 999, 1000, 1234, 1_000_000, -1, -1234, -1_000_000]
DURATIONS = [0, 1, 800, 999, 1000, 59_999, 60_000, 125_000, 3_599_999, 3_600_000, 7_500_000]
SHARES = [(0.0, 1.0), (1.0, 3.0), (0.022, 1.0), (2.0, 0.0), (1.0, 8.0)]
PATHS = [
    ("/Users/someone/repos/hyphae", "/Users/someone"),
    ("/Users/someone", "/Users/someone"),
    ("/Users/someone-else/repos/hyphae", "/Users/someone"),
    ("/opt/elsewhere", "/Users/someone"),
    ("/Users/someone/repos/hyphae", ""),
]
# One instant, and every span before it a relative time has a different word for.
NOW = dt.datetime(2026, 8, 30, 12, 0, 0, tzinfo=dt.UTC)
AGOS = [0, 30_000, 60_000, 90_000, 3_600_000, 7_200_000, 86_400_000, 300_000_000, -5_000]
STAMPS = ["2026-08-30T12:34:56+00:00", "2026-01-01T00:00:00+00:00"]

print(
    json.dumps(
        {
            "money": [{"value": v, "money": fmt.money(v), "charge": fmt.charge(v)} for v in MONEY],
            "counts": [
                {"value": v, "count": fmt.count(v), "signed": fmt.signed(v)} for v in COUNTS
            ],
            "durations": [{"value": v, "shown": fmt.duration(v)} for v in DURATIONS],
            "shares": [
                {"part": p, "whole": w, "share": fmt.share(p, w), "percent": fmt.percent(p)}
                for p, w in SHARES
            ],
            "paths": [{"value": v, "home": h, "shown": fmt.path(v, h)} for v, h in PATHS],
            "now": NOW.isoformat(),
            "agos": [
                {"before_ms": ms, "shown": fmt.ago(NOW - dt.timedelta(milliseconds=ms), NOW)}
                for ms in AGOS
            ],
            "stamps": [
                {
                    "value": s,
                    "when": fmt.when(dt.datetime.fromisoformat(s)),
                    "clock": fmt.clock(dt.datetime.fromisoformat(s)),
                }
                for s in STAMPS
            ],
            "flags": [{"value": v, "shown": fmt.flag(v)} for v in (True, False)],
            "cuts": [
                {"value": v, "size": n, "shown": fmt.cut(v, n)}
                for v, n in [("abcdef", 3), ("abc", 3), ("abc", 9), ("é🌱x", 2), ("", 3)]
            ],
        },
        indent=2,
        ensure_ascii=False,
    )
)
