// How wide the tree is, dragged on the handle beside it and kept in this browser.
//
// A file rather than an inline script, which `app.CSP` forbids, and a script rather than CSS
// `resize`, which nothing would remember: a width belongs to the screen a session is read on,
// so it cannot ride the URL the way every other thing a reader sets does. Setting a property
// through the CSSOM is not an inline style and the policy allows it.
(() => {
  const grip = document.getElementById("tree-grip");
  if (!grip) return; // every page but a node page
  const browser = document.getElementById("browser");
  const KEY = "hyphae:tree-width";
  // What the column may be, in px: narrower than this cuts every title to nothing, wider
  // leaves a pane too short to read a turn in. The bounds live here rather than in a CSS
  // clamp, so a drag past either end stops there instead of running on unseen.
  const NARROWEST = 256;
  const WIDEST = 768;
  // What one arrow key moves it, for a reader who is not dragging anything.
  const STEP = 16;

  // The scale the reader is moving on, which a screen reader needs beside the value below.
  grip.setAttribute("aria-valuemin", String(NARROWEST));
  grip.setAttribute("aria-valuemax", String(WIDEST));

  const apply = (px) => {
    const held = Math.round(Math.min(WIDEST, Math.max(NARROWEST, px)));
    browser.style.setProperty("--tree-width", `${held}px`);
    grip.setAttribute("aria-valuenow", String(held));
    return held;
  };
  // Storage a browser may refuse: blocked cookies and some private modes throw on the first
  // touch of `localStorage`, and a full one throws on the way in. A refusal costs the memory
  // and nothing else — the handle still drags and the arrow keys still move it for as long as
  // the page lives, which is what the reader came to the handle for.
  const orNothing = (touch) => {
    try {
      return touch();
    } catch {
      return null;
    }
  };
  // The width this browser last kept, or the one the stylesheet lays the column out at, read
  // off the grid's own first track. Not off the tree's box: under the narrow layout `#browser`
  // is a block and the tree is the whole page, so a width seeded from it survives the reader
  // widening their window as a column twice the stylesheet's — 768 px against 384, with the
  // pane left narrower than the tree.
  const remembered = Number(orNothing(() => localStorage.getItem(KEY)));
  let width = apply(remembered || parseFloat(getComputedStyle(browser).gridTemplateColumns));
  const keep = () => orNothing(() => localStorage.setItem(KEY, String(width)));

  // A drag moves the width by what the pointer moved, rather than setting it to where the
  // pointer is: the handle sits a gap away from the column it drags, and a width read off the
  // pointer's own position would jump by that gap the moment it was grabbed.
  let took = null;
  grip.addEventListener("pointerdown", (event) => {
    took = { at: event.clientX, from: width };
    // So the drag keeps following the pointer once it has left the handle, which is where a
    // drag spends nearly all of its time.
    grip.setPointerCapture(event.pointerId);
    event.preventDefault();
  });
  grip.addEventListener("pointermove", (event) => {
    if (took) width = apply(took.from + event.clientX - took.at);
  });
  const settle = () => {
    if (!took) return;
    took = null;
    keep();
  };
  grip.addEventListener("pointerup", settle);
  grip.addEventListener("pointercancel", settle);
  grip.addEventListener("keydown", (event) => {
    const way = { ArrowLeft: -STEP, ArrowRight: STEP }[event.key];
    if (way === undefined) return;
    width = apply(width + way);
    keep();
    event.preventDefault();
  });
})();
