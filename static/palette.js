// Ctrl+K: one box to jump to a dashboard, a draft or a page, or to press one of the page's own buttons; and the studio's
// keys, as in the owner's other studios: Alt+N a new dashboard, Ctrl+/ the Ask box, Alt+↑ / Alt+↓ the previous or next
// dashboard, ? the list of keys. Also the two skip links at the top of the page and the search button beside light or
// dark. An accessible combobox: the input owns a listbox and points at the active option with aria-activedescendant,
// so the focus never leaves the input. Loaded before app.js, which calls studioPalette.start(ui) once it has started;
// it reads what it offers from the page, so app.js keeps no state for it.
window.studioPalette = window.studioPalette || (() => {
  "use strict";

  const GROUPS = ["Dashboards", "Drafts", "Pages", "Actions"];
  const SETTINGS = [["database", "Databases", "postgres connection schemas tables"], ["metabase", "Metabase", "api key collection go live"],
    ["claude", "Claude", "model sign-in login"], ["limits", "Time zone and limits", "rows queries heavy timeout"]];
  // The keys' list, written once: the "?" dialog shows it.
  const SHORTCUTS = [
    ["Ctrl + K", "Search dashboards, drafts, pages and actions"],
    ["Alt + N", "A new dashboard"],
    ["Ctrl + /", "Ask Claude"],
    ["Alt + ↑ / Alt + ↓", "The previous or next dashboard"],
    ["?", "These shortcuts"],
    ["Enter", "Send in Ask Claude"],
    ["Shift + Enter", "A new line in Ask Claude"],
    ["Esc", "Close a dialog, or the Ask Claude drawer"],
    ["← / →", "On Ask Claude’s edge: the panel wider or narrower"],
    ["Shift + ← / Shift + →", "The same, in bigger steps"],
    ["Home / End", "On Ask Claude’s edge: the narrowest or widest panel"],
  ];
  let ui = null;           // app.js's helpers, from start(ui)
  let openNow = null;

  const $ = (id) => document.getElementById(id);
  const shown = (el) => !!el && el.isConnected && el.getClientRects().length > 0;
  const usable = (el) => shown(el) && !el.disabled && el.getAttribute("aria-disabled") !== "true";
  const typing = (el) => !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  const on = (name) => $("app").classList.contains(name);
  const wait = (ms) => new Promise((done) => setTimeout(done, ms));

  // ---------- what it offers ----------

  // A sidebar entry's button as it is now (a poll may have drawn the list again since the palette opened).
  function press(box, key, before) {
    const now = [...box.querySelectorAll(".nav-item")].find((el) => el.dataset.key === key);
    (now || before).click();
  }

  // Settings, or one of its sections: its heading takes the focus once it is drawn. Settings already open is not drawn
  // again, which would lose what was typed and not saved.
  async function openSettings(id) {
    const heading = () => (id ? document.querySelector(`#set-${id} h2`) : $("title"));
    if (!on("on-settings")) ui.go("settings");
    for (let i = 0; i < 100 && !shown(heading()); i += 1) await wait(50);
    ui.focusFirst(heading());
  }

  function openTrash() {
    if (!on("on-trash")) ui.go("trash");
    ui.focusFirst($("title"));
  }

  function items() {
    const out = [];
    const entries = (box, group, glyph) => {
      for (const el of box.querySelectorAll(".nav-item")) {
        const name = el.querySelector(".name");
        const sub = el.querySelector(".sub");
        const key = el.dataset.key;
        out.push({ group, label: name ? name.textContent : el.textContent.trim(), hint: sub ? sub.textContent : "", icon: glyph,
          current: el.getAttribute("aria-current") === "true", run: () => press(box, key, el) });
      }
    };
    entries($("list"), "Dashboards", "chart");
    entries($("drafts"), "Drafts", "sparkle");

    const page = (label, glyph, run, current = false, hint = "", keys = "") => out.push({ group: "Pages", label, icon: glyph, run, current, hint, keys });
    page("Settings", "settings", () => openSettings(null), on("on-settings"), "", "setup");
    for (const [id, name, keys] of SETTINGS) page(`Settings: ${name}`, "settings", () => openSettings(id), false, "", keys);
    page("Trash", "trash", openTrash, on("on-trash"), "What you removed", "deleted bin put back");

    const action = (label, glyph, run, hint = "", keys = "") => out.push({ group: "Actions", label, icon: glyph, run, hint, keys });
    const button = (el, label, glyph, hint = "", keys = "") => {
      if (usable(el)) action(label, glyph, () => el.click(), hint, keys);
    };
    button($("newDash"), "New dashboard", "plus", "Alt+N", "add create draft plan");
    button($("refreshAll"), "Refresh data", "refresh", "", "run queries again reload");
    button($("goLive"), "Go live…", "upload", "", "publish metabase");
    button($("openLive"), "Open in Metabase", "external", "", "published view");
    button($("dashSettings"), "Dashboard settings", "gear", "", "versions go back remove history");
    if (shown($("askText"))) action("Ask Claude", "sparkle", focusAsk, "Ctrl+/", "chat question change");
    const show = $("showAssist");
    if (usable(show) && show.getAttribute("aria-expanded") !== "true") button(show, "Show Ask Claude", "panel", "", "panel chat drawer");
    else button($("hideAssist"), "Hide Ask Claude", "panel", "", "panel chat drawer");
    const clear = $("assistClear");
    button(clear, (clear.getAttribute("aria-label") || "").trim(), on("on-new") ? "trash" : "chat", "", "conversation draft trash");
    const dark = document.documentElement.dataset.theme === "dark";
    button($("themeBtn"), dark ? "Switch to light" : "Switch to dark", dark ? "sun" : "moon", "", "theme appearance light dark");
    action("Keyboard shortcuts", "keyboard", showShortcuts, "?", "keys help");
    return out;
  }

  // Every word must appear somewhere; a label that starts with what was typed comes first.
  function score(item, words, query) {
    const label = item.label.toLowerCase();
    const hay = `${label} ${item.hint || ""} ${item.keys || ""} ${item.group}`.toLowerCase();
    if (!words.every((w) => hay.includes(w))) return -1;
    if (label.startsWith(query)) return 0;
    if (words.every((w) => label.includes(w))) return label.split(/\s+/).some((x) => x.startsWith(words[0])) ? 1 : 2;
    return 3;
  }

  // ---------- the palette ----------

  // Read from the dialog itself: its close event may not have come yet when the next Ctrl+K does.
  const paletteOpen = () => !!openNow && ui.dialog.open && ui.dialog.contains(openNow.input);

  function openPalette() {
    if (!ui) return;
    if (paletteOpen()) {
      openNow.input.focus();
      openNow.input.select();
      return;
    }
    const { h, icon } = ui;
    const all = items();
    let list = [];
    let active = 0;
    const input = h("input", { class: "palette-input", type: "text", role: "combobox", "aria-expanded": "true", "aria-controls": "paletteList",
      "aria-autocomplete": "list", "aria-label": "Search dashboards, drafts, pages and actions",
      placeholder: "Search dashboards, drafts, pages and actions…", autocomplete: "off", spellcheck: "false" });
    const box = h("div", { class: "palette-list", id: "paletteList", role: "listbox", "aria-label": "Results" });
    const count = h("div", { class: "sr-only", role: "status" });
    const foot = h("div", { class: "palette-foot", "aria-hidden": "true" },
      h("span", null, h("kbd", null, "↑"), h("kbd", null, "↓"), " to move"), h("span", null, h("kbd", null, "Enter"), " to open"),
      h("span", null, h("kbd", null, "Esc"), " to close"));
    ui.showDialog(h("h2", { class: "sr-only" }, "Search and jump"),
      h("div", { class: "palette-box" }, h("div", { class: "palette-search" }, icon("search", 18), input), box, count, foot));
    const dialog = ui.dialog;
    dialog.classList.add("palette");
    openNow = { input };
    const closed = () => {
      if (dialog.open) return;     // the close of what was open before, arriving late
      dialog.removeEventListener("close", closed);
      if (openNow && openNow.input === input) openNow = null;
      dialog.classList.remove("palette");
    };
    dialog.addEventListener("close", closed);

    let countTimer = 0;
    function draw() {
      const query = input.value.trim().toLowerCase();
      const words = query.split(/\s+/).filter(Boolean);
      let found = all.map((it, i) => ({ it, s: 0, i }));
      if (words.length) {
        found = found.map((x) => ({ ...x, s: score(x.it, words, query) })).filter((x) => x.s >= 0);
        found.sort((a, b) => a.s - b.s || GROUPS.indexOf(a.it.group) - GROUPS.indexOf(b.it.group) || a.i - b.i);
      }
      const order = words.length ? [...new Set(found.map((x) => x.it.group))] : GROUPS;
      list = [];
      const blocks = [];
      for (const group of order) {
        const its = found.filter((x) => x.it.group === group).map((x) => x.it);
        if (!its.length) continue;
        const gid = `palette-g-${group.toLowerCase()}`;
        blocks.push(h("div", { class: "palette-group", role: "group", "aria-labelledby": gid },
          h("div", { class: "palette-group-label", id: gid, role: "presentation" }, group),
          its.map((it) => {
            const n = list.length;
            list.push(it);
            return h("div", { class: "palette-item", role: "option", id: `palette-opt-${n}`, "aria-selected": "false", "data-n": n,
              onclick: () => choose(n), onmousemove: () => active !== n && setActive(n, false) },
            h("span", { class: "palette-icon" }, icon(it.icon, 16)),
            h("span", { class: "palette-label" }, it.label, it.current ? h("span", { class: "palette-tag" }, "open") : null),
            it.hint ? h("span", { class: "palette-hint" }, it.hint) : null);
          })));
      }
      if (!list.length) blocks.push(h("div", { class: "palette-empty" }, `Nothing matches “${input.value.trim()}”.`));
      box.replaceChildren(...blocks);
      setActive(0, true);
      clearTimeout(countTimer);
      countTimer = setTimeout(() => {
        count.textContent = list.length ? `${list.length} result${list.length === 1 ? "" : "s"}` : "No results";
      }, 350);
    }
    function setActive(n, scroll) {
      const before = box.querySelector('[aria-selected="true"]');
      if (before) before.setAttribute("aria-selected", "false");
      active = Math.max(0, Math.min(list.length - 1, n));
      const el = box.querySelector(`[data-n="${active}"]`);
      if (!el) return void input.removeAttribute("aria-activedescendant");
      el.setAttribute("aria-selected", "true");
      input.setAttribute("aria-activedescendant", el.id);
      if (scroll) el.scrollIntoView({ block: "nearest" });
    }
    function choose(n) {
      const it = list[n];
      if (!it) return;
      ui.closeDialog();
      // after the dialog has given the focus back, so what runs can move it on
      setTimeout(() => {
        try {
          it.run();
        } catch (error) {
          console.error(error);
        }
      }, 0);
    }
    input.addEventListener("input", draw);
    input.addEventListener("keydown", (event) => {
      if (event.isComposing) return;
      const to = { ArrowDown: active + 1 >= list.length ? 0 : active + 1, ArrowUp: active - 1 < 0 ? list.length - 1 : active - 1,
        PageDown: active + 8, PageUp: active - 8 }[event.key];
      if (to !== undefined) {
        event.preventDefault();
        setActive(to, true);
      } else if (event.key === "Enter") {
        event.preventDefault();
        choose(active);
      } else if (event.key === "Tab") {
        event.preventDefault();      // the list is reached with the arrows
      }
    });
    draw();
  }

  // ---------- the keys ----------

  function keysOf(keys) {
    const out = [];
    keys.split(" / ").forEach((one, i) => {
      if (i) out.push(" or ");
      one.split(" + ").forEach((part, j) => {
        if (j) out.push(" + ");
        out.push(ui.h("kbd", null, part));
      });
    });
    return out;
  }

  function showShortcuts() {
    if (!ui) return;
    const { h } = ui;
    ui.showDialog(h("h2", null, "Keyboard shortcuts"),
      h("div", { class: "keys-wrap" }, h("table", { class: "keys" },
        h("thead", null, h("tr", null, h("th", { scope: "col" }, "Keys"), h("th", { scope: "col" }, "What it does"))),
        h("tbody", null, SHORTCUTS.map(([keys, what]) => h("tr", null, h("td", null, keysOf(keys)), h("td", null, what)))))),
      ui.liveFooter(ui.closeButton("Close")));
  }

  // Ctrl+/: the Ask box, the panel (or, on a laptop, the drawer) shown first when it was hidden.
  function focusAsk() {
    if (shown($("askText"))) return void $("askText").focus();
    if (shown($("showAssist"))) return void $("showAssist").click();
    ui.announce("Ask Claude is beside each dashboard and on the New dashboard screen. Open one of them first.");
  }

  // Alt+↑ / Alt+↓, in the sidebar's order; from a page that is not a dashboard, the first or the last.
  function stepDashboard(dir) {
    const buttons = [...$("list").querySelectorAll(".nav-item")];
    if (!buttons.length) return;
    const at = buttons.findIndex((el) => el.getAttribute("aria-current") === "true");
    const next = at < 0 ? buttons[dir > 0 ? 0 : buttons.length - 1] : buttons[(at + dir + buttons.length) % buttons.length];
    next.click();
  }

  function onKey(event) {
    if (event.defaultPrevented || event.isComposing) return;
    const mod = event.ctrlKey || event.metaKey;
    const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
    if (mod && !event.altKey && !event.shiftKey && (key === "k" || event.code === "KeyK")) {
      event.preventDefault();
      if (!ui.dialog.open || paletteOpen()) openPalette();    // never over a dialog that is asking something
      return;
    }
    if (ui.dialog.open) return;
    if (mod && !event.altKey && (key === "/" || event.code === "Slash")) {
      event.preventDefault();
      return void focusAsk();
    }
    if (event.altKey && !mod && !event.shiftKey && (key === "n" || event.code === "KeyN")) {
      event.preventDefault();
      return void $("newDash").click();
    }
    if (event.altKey && !mod && !event.shiftKey && (event.key === "ArrowUp" || event.key === "ArrowDown") && event.target.tagName !== "SELECT") {
      event.preventDefault();
      return void stepDashboard(event.key === "ArrowUp" ? -1 : 1);
    }
    if (!mod && !event.altKey && event.key === "?" && !typing(event.target)) {
      event.preventDefault();
      showShortcuts();
    }
  }

  // ---------- the start ----------

  // Two links the first Tab reaches. Their words and whether Ask Claude has one follow the page, read from #app's
  // classes: Settings, the Trash, New dashboard and the empty page are pages, not a dashboard.
  function skipLinks() {
    const { h } = ui;
    const toPage = h("a", { class: "skip-link", href: "#", id: "skipPage" }, "Skip to the dashboard");
    const toAsk = h("a", { class: "skip-link", href: "#", id: "skipAsk", "aria-keyshortcuts": "Control+/" }, "Skip to Ask Claude");
    toPage.addEventListener("click", (event) => {
      event.preventDefault();
      ui.focusFirst($("title"), $("emptyTitle"), $("assistTitle"));   // the heading of what is shown
    });
    toAsk.addEventListener("click", (event) => {
      event.preventDefault();
      focusAsk();
    });
    document.body.prepend(toPage, toAsk);
    const sync = () => {
      const page = on("on-new") || on("on-settings") || on("on-trash") || on("no-dashboards");
      const words = page ? "Skip to the page" : "Skip to the dashboard";
      if (toPage.textContent !== words) toPage.textContent = words;
      toAsk.hidden = on("on-settings") || on("on-trash") || (on("no-dashboards") && !on("on-new"));
    };
    new MutationObserver(sync).observe($("app"), { attributes: true, attributeFilter: ["class"] });
    sync();
  }

  function start(helpers) {
    if (ui || !helpers) return;      // once
    ui = helpers;
    const search = ui.h("button", { id: "paletteBtn", class: "btn ghost small square", type: "button", "aria-label": "Search and jump",
      title: "Search and jump (Ctrl+K)", "aria-keyshortcuts": "Control+K", "aria-haspopup": "dialog" }, ui.icon("search", 15));
    search.addEventListener("click", openPalette);
    $("themeBtn").before(search);
    $("newDash").setAttribute("aria-keyshortcuts", "Alt+N");
    $("askText").setAttribute("aria-keyshortcuts", "Control+/");
    skipLinks();
    document.addEventListener("keydown", onKey);
  }

  return { start, open: openPalette, shortcuts: showShortcuts };
})();
