"""One request's level reads, each statement run once however many readers want its rows.

The NavTree opens the path down to the selection, and the walk beside the pane then reads those
same levels again to find what stands next to it — on a node five deep that is a quarter of the
page's query time, spent twice. A `Levels` is held by the request's `Corpus` and dies with it,
so nothing memoized here outlives the connection it was read over.
"""

import duckdb

from hyphae.analyze.queries import ParamValue
from hyphae.view.store import Library, Row, cursorless_rows, page_rows

# What a read is answered by: the query, the cursor and cap a cursorless read adds, and
# everything the statement bound. The connection is not in the key because a `Levels` belongs to
# one request, and so does the connection every read of it runs over.
Asked = tuple[Library, str | None, int | None, tuple[tuple[str, ParamValue], ...]]


class Levels:
    """The store reads one node page has already made, keyed by what it asked.

    Both readers get one list of rows rather than a copy of it, which holds because a level is
    read and never written — every caller turns the rows into nodes and leaves them alone.
    """

    def __init__(self) -> None:
        self.asked: dict[Asked, list[Row]] = {}

    def rows(
        self, connection: duckdb.DuckDBPyConnection, page: Library, **bindings: ParamValue
    ) -> list[Row]:
        """`store.page_rows`, run once per question this request asks."""
        key: Asked = (page, None, None, tuple(sorted(bindings.items())))
        if key not in self.asked:
            self.asked[key] = page_rows(connection, page, **bindings)
        return self.asked[key]

    def cursorless(
        self,
        connection: duckdb.DuckDBPyConnection,
        page: Library,
        cursor: str,
        limit: int,
        **bindings: ParamValue,
    ) -> list[Row]:
        """`store.cursorless_rows`, run once per question this request asks."""
        key: Asked = (page, cursor, limit, tuple(sorted(bindings.items())))
        if key not in self.asked:
            self.asked[key] = cursorless_rows(connection, page, cursor, limit, **bindings)
        return self.asked[key]
