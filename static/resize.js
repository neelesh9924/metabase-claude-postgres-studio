// The Ask Claude panel's edge, as in the owner's other studios: drag it, or focus it and use the arrow keys (Shift for
// bigger steps, Home and End for the narrowest and widest), to make the panel wider or narrower; a double-click puts it
// back. It starts itself and needs nothing from app.js: it sets --assist-w on #app, which sizes the panel's column and,
// from 900 to 1180 px, the drawer, and the charts follow through app.js's ResizeObserver on the grid. keys.css hides
// the edge below 900 px, on the New dashboard screen, and with the panel.
(() => {
  "use strict";
  const app = document.getElementById("app");
  const assist = document.getElementById("assist");
  if (!app || !assist || document.getElementById("assistResize")) return;   // once

  const MIN = 320;
  const DEFAULT = 388;     // app.css's --assist-w
  const STORE = "assist-width";
  const widest = () => Math.max(MIN, Math.min(760, Math.round(window.innerWidth * 0.5)));

  const handle = document.createElement("div");
  for (const [name, value] of Object.entries({ id: "assistResize", class: "assist-resize", role: "separator", tabindex: "0",
    "aria-orientation": "vertical", "aria-controls": "assist", "aria-label": "Resize the Ask Claude panel",
    title: "Drag, or use the arrow keys, to resize" })) handle.setAttribute(name, value);
  assist.prepend(handle);

  // Kept in this browser only; the page works the same without it.
  function stored() {
    try {
      return Number(localStorage.getItem(STORE)) || DEFAULT;
    } catch {
      return DEFAULT;
    }
  }
  let wanted = stored();   // the width chosen, which a narrower window only holds back

  function setAttr(name, value) {
    if (handle.getAttribute(name) !== String(value)) handle.setAttribute(name, value);
  }

  function setWidth(px, save = true) {
    const w = Math.round(Math.max(MIN, Math.min(widest(), Number(px) || DEFAULT)));
    app.style.setProperty("--assist-w", `${w}px`);
    setAttr("aria-valuemin", MIN);
    setAttr("aria-valuemax", widest());
    setAttr("aria-valuenow", w);
    setAttr("aria-valuetext", `${w} pixels wide`);
    if (save) {
      wanted = w;
      try {
        localStorage.setItem(STORE, String(w));
      } catch {}
    }
    return w;
  }

  let dragging = false;
  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    dragging = true;
    handle.setPointerCapture(event.pointerId);
    document.body.classList.add("resizing");
    event.preventDefault();
  });
  handle.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    setWidth(assist.getBoundingClientRect().right - event.clientX, false);
  });
  const end = (event) => {
    if (!dragging) return;
    dragging = false;
    try {
      handle.releasePointerCapture(event.pointerId);
    } catch {}
    document.body.classList.remove("resizing");
    setWidth(Number(handle.getAttribute("aria-valuenow")));
  };
  handle.addEventListener("pointerup", end);
  handle.addEventListener("pointercancel", end);
  handle.addEventListener("dblclick", () => setWidth(DEFAULT));
  handle.addEventListener("keydown", (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const now = Number(handle.getAttribute("aria-valuenow")) || DEFAULT;
    const step = event.shiftKey ? 64 : 16;
    const next = { ArrowLeft: now + step, ArrowRight: now - step, Home: MIN, End: widest() }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    setWidth(next);
  });
  window.addEventListener("resize", () => setWidth(wanted, false));
  setWidth(wanted, false);
})();
