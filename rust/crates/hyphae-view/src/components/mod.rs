//! The markup: one function per piece of a page, mirroring `src/hyphae/view/components/`.
//!
//! The porting pattern, which every module here follows and stage 3b repeats:
//!
//! - **One plain `pub fn` per component**, named as the Python is, returning [`Markup`]. Python
//!   passes keyword-only arguments; Rust has none, so a component with more than about three
//!   takes one `struct` whose field names read like them, declared beside the component that
//!   consumes it — the way `NavTreeRow` and `Facts` are declared in Python.
//! - **The body is `rsx! { … }.memoize()`.** `rsx!` alone is a `Lazy` borrowing its
//!   environment, which no function can return; `memoize` renders it into the same
//!   `Raw<String>` the render module hands out, so a component composes into another exactly as
//!   htpy's `Html` does.
//! - **Escaping is the macro's.** Anything interpolated with `(…)` is escaped on the way in.
//!   Markup that is already markup — a rendered title, another component — is a [`Markup`] and
//!   passes through. Nothing else may: the one opt-out in the crate is
//!   [`crate::render::Markup`], and it is minted in `render`, `inline_markdown` and here.
//! - **An absent attribute is `name=[option]`.** The bracket makes the whole attribute
//!   conditional on a `Some`, which is htpy's `None` — `data-selected=[selected]` writes
//!   nothing at all when there is nothing to say, rather than `data-selected=""`.
//! - **An absent child is `Option<Markup>`**, which renders as nothing. A component that may
//!   have nothing to say returns one, the way the Python returns `Html | None`.
//! - **htmx and `data-*` attributes are written plainly** — `hx-get`, `data-nav-tree`,
//!   `aria-current`. Hypertext's tables know the htmx set through its `htmx` feature and take
//!   any `data-*` name, so the quoted-name escape hatch is not needed anywhere in this package.

pub mod citation;
pub mod layout;
pub mod logs;
pub mod nav_tree;
pub mod node_body;
pub mod node_page;
pub mod pages;
pub mod parts;

pub use crate::render::Markup;
