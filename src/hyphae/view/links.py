"""Where the session list is served, and every link into it.

The list is the way out of a session and the way into one, so three surfaces have to agree on
its URL: the route that serves it, the form it writes, and every link another page mints at it.
That agreement is what lives here — the mount point, the order a bare visit gets, and the one
builder every narrowed or paged link goes through.

A node's own URL is minted on the node (`view/nodes.py`); this module is for the one page that
is not a node.
"""

from collections.abc import Mapping
from urllib.parse import urlencode

from hyphae.view import bounds

# Where the session list is served. `/` is the projects landing, and a link that still points
# there drops the sort and the filters the request composed.
LIST_URL = "/sessions"

# Newest first: the session someone is looking for is usually the one that just ran.
DEFAULT_SORT = "started_at"
DEFAULT_DIRECTION = "desc"


def list_url(sort: str, direction: str, page: int, size: int, filters: Mapping[str, str]) -> str:
    """A link back to the list, carrying everything that made this view of it.

    Every link the list writes goes through here. A filter that rode the sort headings but
    not the pager would widen the list halfway through reading it, which is the kind of thing
    a reader notices only after quoting the wrong count.
    """
    query: dict[str, str | int] = {"sort": sort, "direction": direction}
    if page > 1:
        query["page"] = page
    if size != bounds.SESSIONS.default:
        query["size"] = size
    narrowed = {key: value for key, value in filters.items() if value}
    return f"{LIST_URL}?" + urlencode(query | narrowed)


def project_link(project_dir: str | None) -> str | None:
    """The session list narrowed to one project, or None when there is no list to open.

    The second way out of a session, minted by the node page's crumb chain and by the landing
    page's rows. The path is the whole one and not the head a row shows — the list's filter
    matches a path prefix, and a cut one matches nothing. A row the query left NULL is a row
    with no link: the sessions that named no directory, and a path longer than the head the
    page shows.
    """
    if project_dir is None:
        return None
    return list_url(
        DEFAULT_SORT, DEFAULT_DIRECTION, 1, bounds.SESSIONS.default, {"project": project_dir}
    )
