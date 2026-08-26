// The dev loop's browser half, on the page only under `aiobserve view --dev`.
//
// `/dev/reload` sends one message per debounced change set (`view/dev.py`). A reload costs the
// reader nothing here: every state but tree width rides the URL, so the page comes back at the
// node, the view and the knobs it was on. `EventSource` retries a dropped connection on
// its own, so a restarted viewer picks the loop back up; the restart itself refreshes nothing.
const reloads = new EventSource("/dev/reload");

reloads.onmessage = () => {
  // `css` and `page` both reload for now. The distinction is what a stylesheet swap will read:
  // re-fetching the sheets in place keeps the scroll and the open sections a reload throws away.
  location.reload();
};
