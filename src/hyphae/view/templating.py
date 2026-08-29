"""The Jinja environment the templates still left over render through.

`environment(dev=…)` is the whole registry: the cuts each surface prints a value at
(`view/cuts.py`), the formatters, and the globals a template names without being handed them.
It shrinks with every template the conversion to htpy retires.
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from hyphae.view import bounds, columns, cuts, highlight, nodes, render
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
        "line": cuts.line,
        "head": cuts.head,
        "member": cuts.member,
        "short": cuts.short,
        "item": cuts.item,
        "path": cuts.project_path,
        "ago": cuts.ago,
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
