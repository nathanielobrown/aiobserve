// Two things about the NavTree that no stylesheet can do: open it where the reader left off,
// and stand a popover beside the row it belongs to.
//
// A file rather than an inline script, which `app.CSP` forbids. Writing `top` through the CSSOM
// is not an inline style and the policy allows it, the way `nav-tree-width.js` writes the column
// width. Nothing here fetches or renders: the popover's markup is htmx's (`_nav_tree.html`) and
// its left edge is the stylesheet's — what a script has to find is a row's place on the screen.
(() => {
  const tree = document.getElementById("nav-tree");
  if (!tree) return; // every page but a node page

  // The gap a popover keeps from either edge of the window when the row it belongs to is close
  // enough to one that the numbers would run off it.
  const GUTTER = 8;
  // The rows whose popover is up: the two states the stylesheet shows one under.
  const SHOWN = "li.node:hover .popover, li.node:focus-within .popover";

  // Top-aligned to the row, so a reader's eye stays on the line they are pointing at. Held
  // inside the window from below, because a row near the foot of a long NavTree would otherwise
  // open its numbers past the bottom of the screen.
  const place = (popover) => {
    const row = popover.closest("li.node");
    if (!row) return;
    const lowest = window.innerHeight - popover.offsetHeight - GUTTER;
    const top = Math.max(GUTTER, Math.min(row.getBoundingClientRect().top, lowest));
    popover.style.top = `${Math.round(top)}px`;
  };
  const placeShown = () => document.querySelectorAll(SHOWN).forEach(place);

  // A popover is placed when the reader reaches its row, when it arrives — htmx fetches it a
  // moment after the pointer lands — and whenever the NavTree scrolls under one that is up.
  tree.addEventListener("mouseover", placeShown);
  tree.addEventListener("focusin", placeShown);
  tree.addEventListener("htmx:afterSwap", placeShown);
  tree.addEventListener("scroll", placeShown, { passive: true });

  // And the selection is scrolled to the middle of the NavTree on the way in. A deep node's row
  // is hundreds of rows down its own scroller, and the page arrives with the tree at the top —
  // so a reader who pasted a link would have to hunt for the node they are already reading.
  // Centred rather than merely brought into view, which is what shows the rows around it.
  const selected = tree.querySelector("[data-selected]");
  if (selected) selected.scrollIntoView({ block: "center" });
})();
