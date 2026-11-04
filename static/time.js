// The studio's one way of writing its own times: a reply's, a run's step, a removal, a version, a card's last run. The
// data's own dates are app.js's (fmtDate), written as Metabase writes them. Loaded first; window.studioTime:
// - when(at), for the start of a line: "Just now" (under a minute), "5:43 pm" (today), "Yesterday, 5:43 pm",
//   "27 Oct, 5:28 pm" (this year), "27 Oct 2025, 5:28 pm" (an earlier year).
// - inline(at), the same words mid-sentence: "just now", "yesterday, 5:43 pm".
// - full(at), for a title: "27 Oct 2026, 5:43 pm".
// - tag(at, words): a <time> with when's (or inline's) words, its datetime, and full's words as its title. Its words
//   stay true: "Just now" becomes "5:43 pm" a minute later, "5:43 pm" becomes "Yesterday, 5:43 pm" after midnight.
// at is the studio's local ISO time ("2026-10-27T17:41:03", "2026-10-27 17:41") or a step's bare clock ("17:41:03",
// always "5:41 pm"). Anything else is shown as given. now is for tests.
window.studioTime = (() => {
  "use strict";

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const STAMP = /^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d)(?:\.\d+)?)?$/;
  const CLOCK = /^(\d\d?):(\d\d)(?::(\d\d))?$/;
  const MINUTE = 60000;

  // {date, bare: a clock without a day}, or null for anything that is not one of the studio's times.
  function read(at) {
    const text = typeof at === "string" ? at.trim() : "";
    const stamp = STAMP.exec(text);
    if (stamp) {
      const [y, mo, d, hh, mm, ss] = stamp.slice(1).map((part) => Number(part || 0));
      const date = new Date(y, mo - 1, d, hh, mm, ss);
      const real = date.getFullYear() === y && date.getMonth() === mo - 1 && date.getDate() === d && hh < 24 && mm < 60 && ss < 60;
      return real ? { date, bare: false } : null;
    }
    const clock = CLOCK.exec(text);
    if (!clock) return null;
    const [hh, mm, ss] = clock.slice(1).map((part) => Number(part || 0));
    return hh < 24 && mm < 60 && ss < 60 ? { date: new Date(2000, 0, 1, hh, mm, ss), bare: true } : null;
  }

  const asGiven = (at) => (at === null || at === undefined ? "" : String(at));
  const clockOf = (d) => `${d.getHours() % 12 || 12}:${String(d.getMinutes()).padStart(2, "0")} ${d.getHours() < 12 ? "am" : "pm"}`;
  const dayOf = (d, withYear) => `${d.getDate()} ${MONTHS[d.getMonth()]}${withYear ? ` ${d.getFullYear()}` : ""}`;
  const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

  function when(at, now = new Date()) {
    const found = read(at);
    if (!found) return asGiven(at);
    const { date, bare } = found;
    if (bare) return clockOf(date);
    if (Math.abs(now - date) < MINUTE) return "Just now";
    if (sameDay(date, now)) return clockOf(date);
    if (sameDay(date, new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1))) return `Yesterday, ${clockOf(date)}`;
    return `${dayOf(date, date.getFullYear() !== now.getFullYear())}, ${clockOf(date)}`;
  }

  function inline(at, now = new Date()) {
    const words = when(at, now);
    return read(at) && /^(Just now|Yesterday)/.test(words) ? words[0].toLowerCase() + words.slice(1) : words;
  }

  function full(at) {
    const found = read(at);
    if (!found) return asGiven(at);
    return found.bare ? clockOf(found.date) : `${dayOf(found.date, true)}, ${clockOf(found.date)}`;
  }

  const WORDS = { when, inline };

  function tag(at, words = "when") {
    const el = document.createElement("time");
    const found = read(at);
    el.textContent = (WORDS[words] || when)(at);
    if (!found) return el;
    el.dateTime = String(at).trim().replace(" ", "T");
    if (found.bare) return el;   // a clock alone never changes, and says all its title would
    el.title = full(at);
    el.dataset.at = String(at);
    el.dataset.words = WORDS[words] ? words : "when";
    return el;
  }

  // The words of every time on the page that can change ("Just now", today's), put right in place: no redraw, so the
  // focus and a screen reader's place are left as they are.
  function refresh() {
    const now = new Date();
    for (const el of document.querySelectorAll("time[data-words]")) {
      const words = WORDS[el.dataset.words](el.dataset.at, now);
      if (el.textContent !== words) el.textContent = words;
    }
  }
  if (typeof document !== "undefined" && typeof setInterval === "function") setInterval(refresh, 15000);

  return { when, inline, full, tag };
})();
