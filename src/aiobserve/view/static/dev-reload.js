// The dev loop's browser half, on the page only under `aiobserve view --dev`.
//
// `/dev/reload` sends one message per debounced change set (`view/dev.py`). A reload costs the
// reader nothing here: every state but tree width rides the URL, so the page comes back at the
// node, the view and the knobs it was on. A stylesheet still takes the faster path, because the
// scroll position and the open sections are worth keeping while you nudge a colour.
const reloads = new EventSource("/dev/reload");

reloads.onmessage = (event) => {
  if (event.data === "css") {
    restyle();
  } else {
    location.reload();
  }
};

// `EventSource` retries a dropped connection on its own, so a restarted viewer picks the loop
// back up. The reconnect carries no message, so the reload is here: without it the page would
// go on showing what the old server rendered. The first open is this page's own connection,
// not a restart — reloading on it would loop.
let opened = false;
reloads.onopen = () => {
  if (opened) {
    location.reload();
  }
  opened = true;
};

// Re-fetch every stylesheet at a URL the cache has not seen, swapping each `href` in place. The
// old sheet stays applied until its replacement arrives, so the page restyles without a flash.
function restyle() {
  for (const link of document.querySelectorAll('link[rel="stylesheet"]')) {
    const url = new URL(link.href);
    url.searchParams.set("saved", Date.now());
    link.href = url.href;
  }
}
