// A dashboard's own settings, from the gear beside Go live: its name, its versions to go back to, and Remove. Loaded
// before app.js, which calls studioDashboard.open(ui, spec) with its helpers and the dashboard as /api/dashboard gave it.
// Times are written the studio's one way (static/time.js).
window.studioDashboard = (() => {
  "use strict";

  const BY = { claude: "Claude", you: "You", outside: "", studio: "The studio" };   // outside: its note says so
  const BY_ICON = { claude: "sparkle", you: "history", outside: "code", studio: "chart" };
  const time = () => window.studioTime;

  function put(box, ...pieces) {
    box.replaceChildren(...pieces.flat().filter(Boolean));
  }

  function open(ui, spec) {
    const { h, icon } = ui;
    const versions = h("div", { class: "versions" }, h("p", { class: "live-soft" }, "Reading its versions…"));
    const remove = h("button", { class: "btn danger small", type: "button" }, icon("trash", 14), "Remove from the studio…");
    remove.addEventListener("click", () => {
      ui.closeDialog();
      ui.askRemove(spec);
    });
    const title = h("h2", null, spec.name);
    const view = { ui, spec, box: versions, title, field: null };
    const naming = nameField(view);
    ui.showDialog(title,
      h("p", { class: "live-soft" }, `Dashboard settings${spec.database_name ? `, on ${spec.database_name}` : ""}: rename it, go back to an earlier version, or take it out of the studio.`),
      naming,
      h("section", { class: "versions-zone", "aria-labelledby": "versionsTitle" },
        h("h3", { id: "versionsTitle" }, "Versions"),
        h("p", { class: "field-hint" }, "Every change is kept: Claude’s, yours, and changes made to its files outside the studio. Going back is kept too, so it can be undone."),
        versions),
      h("section", { class: "danger-zone", "aria-label": "Remove" },
        h("div", { class: "danger-row" },
          h("span", null, "It moves to the Trash with its conversation and versions, to put back or delete forever. Metabase is not touched."),
          remove)),
      ui.liveFooter(ui.closeButton("Close")));
    ui.dialog.classList.add("dash-settings");
    // Not the first control, the name: the heading, read out with the dialog.
    ui.focusFirst(title);
    draw(view);
  }

  // The name, as the heading, the sidebar and the window's title show it: only that changes (dashboard.json's "name"),
  // never its folder or its #address. What went wrong is said under the field, and the words typed stay.
  function nameField(view) {
    const { ui, spec, title } = view;
    const { h } = ui;
    const hint = h("span", { class: "field-hint", id: "renameHint" }, "Only the title changes: its folder and its address stay.",
      spec.live ? " Metabase keeps the old name until the next Go live, which renames it there too." : null);
    const error = h("span", { class: "field-error", id: "renameError", hidden: true });
    const field = h("input", { class: "input", id: "renameField", value: spec.name, autocomplete: "off", spellcheck: "false",
      "aria-describedby": "renameHint" });
    const rename = h("button", { class: "btn", type: "button" }, "Rename");
    view.field = field;
    const say = (text) => {
      error.textContent = text;
      error.hidden = !text;
      if (text) field.setAttribute("aria-invalid", "true");
      else field.removeAttribute("aria-invalid");
      field.setAttribute("aria-describedby", text ? "renameError renameHint" : "renameHint");
    };
    field.addEventListener("input", () => say(""));
    field.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.isComposing) return;
      event.preventDefault();
      rename.click();   // ignored while a rename is under way, like a click
    });
    rename.addEventListener("click", () => ui.whileBusy(rename, async () => {
      const reply = await ui.post("/api/rename", { slug: spec.slug, name: field.value });
      if (reply.error) {
        say(reply.error);
        return void ui.announce(reply.error, { urgent: true });
      }
      say("");
      named(view, reply.name);
      field.value = reply.name;
      ui.announce(`Renamed to “${reply.name}”`);
      ui.refresh();   // the sidebar, the page's heading and the window's title
      await draw(view);
    }));
    return h("div", { class: "name-zone" },
      h("label", { class: "field-label", for: "renameField" }, "Name"),
      h("div", { class: "rename-row" }, field, rename), error, hint);
  }

  // The dialog follows the dashboard's name (a rename, a Go back): its heading, which names it, and the field while
  // the words in it are still the old name.
  function named(view, name) {
    if (!name || name === view.spec.name) return;
    if (view.field && view.field.value.trim() === view.spec.name) view.field.value = name;
    view.spec.name = name;
    view.title.textContent = name;
  }

  // With `said` (what Go back did), the focus is left on that line, and it is spoken: the button pressed is gone with
  // the list it was in.
  async function draw(view, said = null) {
    const { ui, spec, box } = view;
    const { h, icon } = ui;
    let reply = null;
    try {
      reply = await ui.get(`/api/versions?slug=${encodeURIComponent(spec.slug)}`);
    } catch {
      reply = null;
    }
    if (!box.isConnected) return;
    if (!reply || !Array.isArray(reply.versions)) {
      const again = h("button", { class: "btn small", type: "button" }, "Try again");
      again.addEventListener("click", () => ui.whileBusy(again, () => draw(view)));
      return void put(box, h("div", { class: "result bad" }, icon("alert", 16),
        h("div", { class: "grow" }, (reply && reply.error) || "Its versions could not be read just now."), again));
    }
    named(view, reply.name);
    const listed = reply.versions;
    const live = listed.find((v) => v.live) || null;
    const back = (button, version) => ui.whileBusy(button, async () => {
      const done = await ui.post("/api/versions/back", { slug: spec.slug, id: version.id });
      if (done.error) return draw(view, { text: done.error, kind: "bad" });
      const of = time().inline(version.at);
      await draw(view, done.changed ? { text: `Went back to the version of ${of}. The dashboard shows it now.`, kind: "ok" }
        : { text: `It is already the version of ${of}.`, kind: "ok" });
      ui.refresh();
    });
    const button = (label, name, version) => {
      const el = h("button", { class: "btn small", type: "button", "aria-label": name }, label);
      el.addEventListener("click", () => back(el, version));
      return el;
    };
    const row = (version) => h("li", { class: "version" }, icon(BY_ICON[version.by] || "history", 15),
      h("div", { class: "version-text" }, h("b", null, time().tag(version.at)),
        h("small", null, [BY[version.by] ?? version.by, version.note].filter(Boolean).join(" · "))),
      h("span", { class: "version-tags" }, version.now ? h("span", { class: "chip ok" }, "Now") : null,
        version.live ? h("span", { class: "chip live" }, "Live") : null),
      version.now ? null : button("Go back to this", `Go back to this, ${time().inline(version.at)}`, version));
    const line = said ? h("div", { class: `result ${said.kind} versions-said` }, icon(said.kind === "ok" ? "check" : "alert", 16),
      h("div", { class: "grow" }, said.text)) : null;
    // What Metabase shows is a version too: one step back to it when the files have moved on.
    const notLive = live && !live.now ? h("div", { class: "result warn" }, icon("alert", 16),
      h("div", { class: "grow" }, "Changes not live. Metabase shows the version of ", time().tag(live.at, "inline"), "."),
      button("Go back to what is live", `Go back to what is live, ${time().inline(live.at)}`, live)) : null;
    put(box,
      line,
      notLive,
      listed.length ? h("ol", { class: "version-list", "aria-label": "Versions, newest first" }, listed.map(row))
        : h("p", { class: "live-soft" }, "No versions yet. The first is kept once Claude is done with this dashboard."),
      listed.length > 1 ? h("p", { class: "field-hint" }, "Going back changes the dashboard’s files. Cards whose query changes run again when shown.") : null);
    if (line) {
      ui.focusFirst(line);
      ui.announce(said.text, { urgent: said.kind === "bad" });
    }
  }

  return { open };
})();
