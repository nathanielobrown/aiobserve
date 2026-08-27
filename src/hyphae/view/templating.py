"""The Jinja environment every page renders through, and what a route needs to render one.

`environment(dev=…)` is the whole registry: the filters that cut a value to the width its
surface prints it at, and the globals a template names without being handed them. A filter here
is the render-time half of a query's one-extra-character protocol — the query returns a string
one character past the cut, and the filter marks it (`docs/viewer.md`).

`Viewer` is what a route module is built with: the store to read and the environment to render
through, the only two things every route needs.
"""

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from fastapi import Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from hyphae.analyze import queries
from hyphae.view import bounds, columns, highlight, nodes, render
from hyphae.view import format as fmt
from hyphae.view.enrichment import GLYPH, GLYPH_CLASS
from hyphae.view.labels import label

TEMPLATES = Path(__file__).parent / "templates"


def environment(*, dev: bool) -> Jinja2Templates:
    """The templates, with every filter and global a page names registered on them.

    `dev` is a global rather than a build-time choice because `base.html` reads it to decide
    one script tag — the only difference between a dev page and a shipped one.
    """
    templates = Jinja2Templates(directory=TEMPLATES)

    def ago(value: dt.datetime | None) -> str:
        """How long ago, against the clock at render rather than one captured at startup.

        A viewer left open is a long-lived process, so the clock is read here per render:
        one captured when the app was built would freeze every row's freshness at boot.
        """
        return fmt.ago(value, fmt.utcnow())

    def project_path(value: str | None) -> str:
        """A project directory, with the home of whoever is reading the page folded to `~`.

        Read per render like the clock above, and for the same reason: a test says who is
        reading, and the next page moves with it.
        """
        return fmt.path(value, fmt.home())

    def line(value: str | None) -> str:
        """A row's string at the width a children log prints it, marked where it was cut.

        The template's half of the one-extra-character protocol: every string a log row prints
        comes back from its query one character past this width, so a value that arrives longer
        than the cut is a value with more behind it. What `nodes.Node.log_title` does for a
        node's title, for the columns a row prints straight off the row.
        """
        return fmt.ABSENT if value is None else fmt.cut(value, queries.LOG_CHARS)

    def head(value: object) -> object:
        """A header's value as a pane prints it: a string cut and marked, anything else as is.

        The same half of the protocol `line` holds, at the pane's width — every string a
        header query previews comes back one character past this cut. Applied by
        `_parts.html:fact` to every value that reaches it rather than at the rows that need
        it, so a fact added beside them inherits the bound instead of printing a value whole.
        A header's other facts are flags and already-formatted numbers, and only a string the
        store holds can be longer than the pane: those go through as `text` leaves them.
        """
        if value is None:
            return fmt.ABSENT
        return fmt.cut(value, queries.HEADER_CHARS) if isinstance(value, str) else value

    def short(value: str | None) -> str:
        """A string at the width a row of the session list prints it, marked where it was cut.

        The row's own half of the protocol, and the narrowest of the four: a row is multiplied
        by the page, so it takes a head where the pane takes a paragraph. Every string a
        transcript or a pass wrote in a row goes through this or `item` — the session's title,
        its project path, and the line a pass wrote about it — and the mark is what the link
        beside it makes good on: the whole value is on the session's page, a click away.

        Takes None like the other cuts do: it stands ahead of `path` on the project column,
        which is where a row's one nullable string is printed.
        """
        return fmt.ABSENT if value is None else fmt.cut(value, queries.LIST_CHARS)

    def item(value: str) -> str:
        """One member of a list on a row of the session list, marked where the query cut it.

        What `member` does for a header's lists, at the width a row shows a skill or an agent
        type. The kinds of work beside them do not come through here: their vocabulary is
        closed (`enrich/taxonomy.py`), so `queries.TAG_CHARS` is a bound the page's arithmetic
        needs rather than one a value reaches, and a mark there could never be true.
        """
        return fmt.cut(value, queries.LIST_ITEM_CHARS)

    def member(value: str) -> str:
        """One member of a header's list, marked where the query cut it.

        The list half of what `head` does for a header's own strings: a list is cut twice —
        to its first `HEADER_ITEMS` members, which the pane counts out loud, and each member
        to `HEADER_ITEM_CHARS`, which nothing said until here.
        """
        return fmt.cut(value, queries.HEADER_ITEM_CHARS)

    # Jinja types `env.filters` by the ones it seeds itself with, so ours widen the value.
    templates.env.filters |= {  # pyrefly: ignore[bad-assignment]
        "money": fmt.money,
        "count": fmt.count,
        "signed": fmt.signed,
        "charge": fmt.charge,
        "share": fmt.share,
        "when": fmt.when,
        "clock": fmt.clock,
        "duration": fmt.duration,
        "text": fmt.text,
        "line": line,
        "head": head,
        "member": member,
        "short": short,
        "item": item,
        "path": project_path,
        "ago": ago,
        # The three filters that print what a transcript wrote. Each hands back escaped
        # markup; `view/render.py` and `view/highlight.py` are where that escaping lives, and
        # nothing here may add `|safe`.
        "markdown": render.markdown,
        "lit": highlight.lit,
        "link": render.link,
    }

    # What a page calls each field it prints. The namespace is typed by what Jinja seeds it
    # with, which is why the assignment needs a word: a global is any callable a template names.
    templates.env.globals["label"] = label  # pyrefly: ignore
    # And the mark every model-written string carries, beside the class that styles it.
    templates.env.globals["GLYPH"] = GLYPH  # pyrefly: ignore
    templates.env.globals["GLYPH_CLASS"] = GLYPH_CLASS  # pyrefly: ignore
    # And how long a value may be before a page prints it plain rather than marked up, which
    # is what the line beside a plain value says.
    templates.env.globals["HIGHLIGHT_CHARS"] = bounds.HIGHLIGHT_CHARS  # pyrefly: ignore
    # The syntaxes a template may ask for, so that asking for one it does not mark up raises
    # here rather than rendering a value as a line of error tokens.
    templates.env.globals["SYNTAX"] = highlight.Syntax  # pyrefly: ignore
    # And where an agent run reads, for the one link a template mints from a column rather than
    # from a node: the `Task` call that started the run.
    templates.env.globals["run_url"] = nodes.run_url  # pyrefly: ignore
    # And the thread a page is reading, which heads every path a template writes that no node
    # stands behind: the raw transcript, and the fetch of one archived record.
    templates.env.globals["thread_url"] = nodes.thread_url  # pyrefly: ignore
    # The columns each children log heads and fills, so the head and the rows cannot drift
    # apart, and how many of them an expansion opened under a row has to span.
    templates.env.globals["COLUMNS"] = columns.COLUMNS  # pyrefly: ignore
    templates.env.globals["spanned"] = nodes.spanned  # pyrefly: ignore
    # And whether this viewer was started for editing it, which `base.html` reads to decide
    # one script tag — the only difference between a dev page and a shipped one.
    templates.env.globals["DEV"] = dev  # pyrefly: ignore

    return templates


@dataclass(frozen=True)
class Viewer:
    """What every route is built with: the store it reads, and the environment it renders in.

    One per app. A route module takes it as its factory's argument rather than reaching into
    `request.app.state`, so a route body stays a plain typed function of what it needs.
    """

    db: Path
    templates: Jinja2Templates

    def error(self, request: Request, status: int, message: str) -> Response:
        """The error page, which is what every handler in `build_app` answers with."""
        return self.templates.TemplateResponse(
            request, "error.html", {"status": status, "message": message}, status_code=status
        )
