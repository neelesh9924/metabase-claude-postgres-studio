// Claude's progress in the Ask Claude panel, and what a run says of itself outside it. Loaded before app.js, which
// calls draw after every poll and mark whenever it sets the window's title. Ported from the owner's other studios.
// - The steps (#activity) are drawn in place, one line per step matched by its number, never all at once: the step it
//   is on, and "Still on this step" when nothing new has come for a while.
// - After a run that did not finish, #assistOffer offers Try again (the same request), Carry on (from where it stopped,
//   when it changed the dashboard's files) or Build again. Never while a run is under way.
// - A screen reader hears the newest step at most every ten seconds while the chat is in sight, and the end once.
// - A run under way marks the window's title and the tab's icon; one that ends while the window is behind others
//   leaves "Ready" or "Needs you" there until the window is looked at again.
window.studioProgress = (() => {
  "use strict";

  const SPEAK_EVERY = 10000;   // ms: a step is said aloud at most this often
  const QUIET = 20;            // s: this long with nothing new, and the head says "Still on this step"
  const SHOWN = 12;            // the steps shown at once, the newest last
  const CARRY_ON = "Carry on from where you stopped, and finish the request.";
  const WORKING = { plan: "Claude is planning", build: "Claude is building", edit: "Claude is working" };
  const MARKS = { busy: "● Working — ", ready: "✓ Ready — ", needs: "! Needs you — " };
  const DOTS = { busy: "#3355e0", ready: "#1f9d63", needs: "#d6452f" };

  let ui = null;
  let titleMark = "";                                            // said before the window's title: "" for none
  const steps = { job: null, time: null, note: null, list: null };   // the activity drawn now: its run, and its parts
  const heard = { job: null, at: 0, step: 0, ended: true };    // the run seen under way: when and which step was said
  let seen = null;                                               // the run under way at the last draw
  let iconText;                                                  // the studio's own icon, as /static/icon.svg holds it

  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const took = (seconds) => (seconds >= 60 ? `${Math.floor(seconds / 60)} min ${seconds % 60} s` : `${seconds} s`);

  // ---------- Claude's steps ----------

  function headWords(job) {
    return took(Math.max(0, job.seconds || 0)) + (job.queries ? ` · ${plural(job.queries, "query", "queries")}` : "");
  }

  function stepWords(job) {
    return job.steps && job.quiet >= QUIET ? `Still on this step · ${took(job.quiet)}` : `Step ${job.steps || 0}`;
  }

  function stepEl(entry, key) {
    return ui.h("li", { "data-n": entry.n, "data-key": key }, ui.h("time", null, entry.at), entry.text, entry.detail ? ui.h("small", null, entry.detail) : null);
  }

  function drawActivity(box, job, key) {
    if (!job || job.status !== "running" || !key || job.key !== key) {
      if (box.firstChild) box.replaceChildren();   // nothing left in it when idle
      steps.job = null;
      return;
    }
    if (steps.job !== job.id || !steps.list || !box.contains(steps.list)) {
      steps.job = job.id;
      steps.time = ui.h("span", { class: "activity-time" });
      steps.note = ui.h("div", { class: "activity-note" });
      steps.list = ui.h("ol");
      box.replaceChildren(ui.h("div", { class: "activity" },
        ui.h("div", { class: "activity-head" }, ui.h("i", { class: "pulse" }), WORKING[job.kind] || "Claude is working", steps.time),
        steps.note, steps.list));
    }
    const time = headWords(job);
    if (steps.time.textContent !== time) steps.time.textContent = time;
    const note = stepWords(job);
    if (steps.note.textContent !== note) steps.note.textContent = note;
    const shown = new Map([...steps.list.children].map((li) => [Number(li.dataset.n), li]));
    for (const entry of (job.activity || []).slice(-SHOWN)) {
      const key = JSON.stringify([entry.text, entry.detail]);
      const li = shown.get(entry.n);
      if (!li) steps.list.append(stepEl(entry, key));
      else if (li.dataset.key !== key) li.replaceWith(stepEl(entry, key));
    }
    while (steps.list.children.length > SHOWN) steps.list.firstElementChild.remove();
  }

  // ---------- after a run that did not finish ----------

  // What the end of the conversation offers: {kind: "again" | "carry" | "build", text | plan, unanswered}, or null.
  function offerOf(job, key, messages) {
    if (!key || (job && job.status === "running")) return null;
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const m = messages[i];
      if (m.offer === "build") return m.plan ? { kind: "build", plan: m.plan } : null;
      if (m.offer === "carry") return { kind: "carry", text: CARRY_ON };
      if (m.offer === "again") {
        const asked = messages.find((x) => x.id === m.request);
        const text = asked ? String(asked.text || "").trim() : "";
        return text ? { kind: "again", text, unanswered: false } : null;
      }
      // A request with no reply at all: the studio closed while Claude worked on it.
      if (m.role === "user") return String(m.text || "").trim() ? { kind: "again", text: m.text.trim(), unanswered: true } : null;
      return null;
    }
    return null;
  }

  const OFFERS = {
    again: (offer) => [offer.unanswered ? "No answer came to this request." : "Send the same request again.", "Try again", "refresh"],
    carry: () => ["What Claude changed so far stays; Versions can undo it.", "Carry on", "send"],
    build: () => ["The build did not finish.", "Build again", "refresh"],
  };

  function drawOffer(box, job, key, messages) {
    const offer = offerOf(job, key, messages || []);
    ui.drawIfChanged(box, JSON.stringify([key, offer]), () => {
      box.hidden = !offer;
      if (!offer) return void box.replaceChildren();
      const [words, label, mark] = OFFERS[offer.kind](offer);
      const button = ui.h("button", { class: "btn small", type: "button" }, ui.icon(mark, 14), ui.h("span", null, label));
      button.addEventListener("click", () => take(button, offer));
      box.replaceChildren(ui.h("div", { class: "offer-row" }, ui.h("span", { class: "offer-words" }, words), button));
    });
  }

  async function take(button, offer) {
    await ui.whileBusy(button, () => (offer.kind === "build" ? ui.build(offer.plan, button) : ui.ask(offer.text, button)));
    // The row goes once the run is under way: the focus goes back to the box (or to Stop while the box waits).
    ui.focusFirst(document.getElementById("askText"), document.getElementById("stopBtn"));
  }

  // ---------- aloud ----------

  function endWords(job) {
    if (job.status === "done") return `Claude finished: ${job.said || "Done."}`;
    if (job.status === "cancelled") return "Stopped";
    return job.said || "Claude did not finish.";
  }

  function speak(job, key, inSight) {
    if (job && job.status === "running") {
      if (heard.job !== job.id) Object.assign(heard, { job: job.id, at: 0, step: 0, ended: false });
      const list = job.activity || [];
      const newest = list[list.length - 1];
      if (inSight && job.key === key && newest && newest.n > heard.step && Date.now() - heard.at >= SPEAK_EVERY) {
        ui.announce(newest.detail ? `${newest.text}: ${newest.detail}` : newest.text);
        Object.assign(heard, { at: Date.now(), step: newest.n });
      }
    } else if (job && heard.job === job.id && !heard.ended) {
      heard.ended = true;
      ui.announce(job.key === key ? endWords(job) : `${job.name || "Another conversation"}: ${endWords(job)}`);
    }
  }

  // ---------- the window's title and the tab's icon ----------

  async function tabIcon(kind) {
    const link = document.querySelector('link[rel="icon"]');
    if (!link) return;
    if (!kind) return void link.setAttribute("href", "/static/icon.svg");
    if (iconText === undefined) iconText = await fetch("/static/icon.svg").then((r) => (r.ok ? r.text() : "")).catch(() => "");
    if (titleMark !== MARKS[kind] || !iconText.includes("</svg>")) return;
    const dot = `<circle cx="50" cy="50" r="12" fill="${DOTS[kind]}" stroke="#ffffff" stroke-width="4"/>`;
    link.setAttribute("href", `data:image/svg+xml,${encodeURIComponent(iconText.replace("</svg>", `${dot}</svg>`))}`);
  }

  function setMark(kind) {
    const text = MARKS[kind] || "";
    if (titleMark === text) return;
    titleMark = text;
    if (ui) ui.retitle();
    tabIcon(kind);
  }

  // Working while a run is under way; then, if it ended unseen, Ready (or Needs you, after a failure) until looked at.
  function track(job) {
    const running = job && job.status === "running" ? job.id : null;
    if (running) setMark("busy");
    else if (seen && !document.hasFocus()) setMark(job && job.id === seen && job.status === "failed" ? "needs" : "ready");
    else if (document.hasFocus()) setMark("");
    seen = running;
  }

  window.addEventListener("focus", () => {
    if (titleMark === MARKS.ready || titleMark === MARKS.needs) setMark("");
  });

  // ---------- what app.js calls ----------

  // After every poll: boxes {activity, offer}; view {job, key, messages, inSight, mode, draft}; helpers, the page's ui.
  function draw(boxes, view, helpers) {
    ui = helpers;
    const { job = null, key = null, messages = [], inSight = false } = view || {};
    if (boxes && boxes.activity) drawActivity(boxes.activity, job, key);
    if (boxes && boxes.offer) drawOffer(boxes.offer, job, key, messages);
    speak(job, key, inSight);
    track(job);
  }

  // What goes before the window's title now: "" or one of the marks.
  function mark() {
    return titleMark;
  }

  return { draw, mark };
})();
