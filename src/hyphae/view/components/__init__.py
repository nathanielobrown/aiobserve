"""The viewer's markup: one typed function per thing a page shows.

A component takes view-models and hands back `Html`. Three rules hold the package together,
and `tests/view/test_components.py` reads each of them off this source:

- **Nothing here imports a web framework.** A component is called by a route and knows nothing
  about the request that reached it, which is what lets pyrefly own the markup the way it owns
  the view-models
- **Every parameter is keyword-only and says what it is.** A call site names each value it
  passes, and a misspelled field is a type error rather than a blank `<dd>`
- **No component constructs a `Markup`.** htpy escapes every string child and every attribute
  value — an attribute even when the value is already markup — so the four modules that do
  produce one hand it in as a child and nowhere else (`.claude/rules/viewer-ui.md`)

Elements are written `htpy.div[...]` rather than imported one by one. The prefix says which
calls emit markup, which is what makes an attribute position something a scan can find; and it
leaves `main`, `title`, `code` and `label` free to mean what a component means by them.

Several children go in a **list** — `htpy.div[[first, second]]`, never `htpy.div[first, second]`.
htpy renders the two identically, but pyrefly reads the comma form through an overload it cannot
resolve and hands back an element it then refuses everywhere markup is asked for. The list form
is what holds the narrowing in `pyproject.toml` down to the one error kind it is scoped to; the
comma form would need `bad-argument-type` off as well, which is most of what the checker is here
for.
"""

import htpy

# What every component returns and every component takes markup as: an element, or a fragment
# holding several. Concrete rather than htpy's `Renderable` protocol, which pyrefly cannot match
# against `Element` — the same undecidable `Node` alias the sub-config in `pyproject.toml`
# names, surfacing a second way. It is also the tighter statement: a component builds htpy's own
# markup, and nothing else here satisfies it.
type Html = htpy.Element | htpy.Fragment
