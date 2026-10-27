// The Trash page: what was removed from the studio, newest first. Put one back, or delete it forever once the user has
// read what goes. Loaded before app.js, which calls studioTrash.render(ui, box) with its own helpers, when the page
// opens and whenever it likes after: the list is drawn again only when what it shows changed, and the focus stays.
window.studioTrash = (() => {
  "use strict";

  const KIND_ICON = { dashboard: "chart", draft: "sparkle", chat: "chat", other: "file" };
  const KINDS = [["dashboard", "dashboard", "dashboards"], ["draft", "draft", "drafts"], ["chat", "conversation", "conversations"],
    ["other", "other thing", "other things"]];
  const MB = 1024 * 1024;
  const SAID_MS = 120000;   // how long the last action's result line stays at the top

  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const sizeOf = (bytes) => (bytes >= MB ? `${(bytes / MB).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`);
  const joined = (parts) => (parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0] || "");
  let said = null;   // { text, kind, open, at, fresh }: what the last Put back or Delete did

  function put(box, ...pieces) {
    box.replaceChildren(...pieces.flat().filter(Boolean));
  }

  function panel(ui, ...body) {
    const { h, icon } = ui;
    return h("div", { class: "settings" },
      h("section", { class: "panel", "aria-labelledby": "trashTitle" },
        h("header", { class: "panel-head" }, h("span", { class: "panel-mark" }, icon("trash", 17)),
          h("div", null, h("h2", { id: "trashTitle" }, "Trash"), h("p", null, "What you removed, newest first. Put one back, or delete it forever."))),
        h("div", { class: "form" }, body)));
  }

  async function render(ui, box) {
    if (said && Date.now() - said.at > SAID_MS) said = null;
    if (!box.childElementCount) put(box, panel(ui, ui.h("p", { class: "live-soft" }, "Reading the Trash…")));
    let reply = null;
    try {
      reply = await ui.get("/api/trash");
    } catch {
      reply = null;
    }
    if (!reply || !Array.isArray(reply.entries)) {
      ui.drawIfChanged(box, "unread", () => {
        const again = ui.h("button", { class: "btn small", type: "button", "data-focus": "again" }, "Try again");
        again.addEventListener("click", () => ui.whileBusy(again, () => render(ui, box)));
        put(box, panel(ui, ui.h("div", { class: "result bad" }, ui.icon("alert", 16),
          ui.h("div", { class: "grow" }, "The Trash could not be read just now. Is the studio still running?"), again)));
      });
      return;
    }
    const line = said;
    ui.drawIfChanged(box, JSON.stringify([reply.entries, line && [line.text, line.kind, line.open]]), () => {
      // Drawn again: the focus goes back to the same button (Put back, Orders) when it is still there.
      const had = box.contains(document.activeElement) ? document.activeElement.dataset.focus : null;
      put(box, draw(ui, box, reply.entries, line));
      const same = had ? [...box.querySelectorAll("[data-focus]")].find((el) => el.dataset.focus === had) : null;
      if (same) ui.focusFirst(same);
    });
    if (line && line.fresh) {
      // The button pressed may be gone with its row: the focus is left on what it did, and that is spoken.
      line.fresh = false;
      ui.focusFirst(box.querySelector(".trash-said"), box.querySelector(".trash-row button"), box.querySelector("#emptyAll"));
      ui.announce(line.text, { urgent: line.kind === "bad" });
    }
  }

  function draw(ui, box, entries, line) {
    const { h, icon } = ui;
    const total = entries.reduce((sum, e) => sum + (e.size || 0), 0);
    const counted = KINDS.map(([kind, one, many]) => [entries.filter((e) => e.kind === kind).length, one, many])
      .filter(([n]) => n).map(([n, one, many]) => plural(n, one, many));
    const empty = h("button", { class: "btn danger", type: "button", id: "emptyAll", "data-focus": "empty", disabled: !entries.length },
      icon("trash", 15), "Empty trash…");
    empty.addEventListener("click", () => askDelete(ui, box, entries, true));
    let open = null;
    if (line && line.open) {
      open = h("button", { class: "btn small", type: "button", "data-focus": "open" }, "Open it");
      open.addEventListener("click", () => ui.go(line.open));
    }
    return panel(ui,
      line ? h("div", { class: `result ${line.kind} trash-said` }, icon(line.kind === "ok" ? "check" : "alert", 16),
        h("div", { class: "grow" }, line.text), open) : null,
      h("div", { class: "form-foot" },
        h("span", { class: "field-hint grow" }, entries.length ? `${joined(counted)} in the Trash · ${sizeOf(total)} in all` : ""), empty),
      entries.length ? h("ol", { class: "trash-list", "aria-label": "In the Trash" }, entries.map((entry) => row(ui, box, entry)))
        : h("p", { class: "live-soft trash-empty" }, "The Trash is empty."));
  }

  // Each button's name starts with the words it shows, then whose it is: "Put back, Orders".
  function row(ui, box, entry) {
    const { h, icon } = ui;
    const where = [`Removed ${entry.when}`, sizeOf(entry.size || 0), entry.kind === "draft" ? "a draft" : null,
      entry.database_name ? `on ${entry.database_name}` : null,
      entry.kind === "dashboard" && entry.live ? "was live: it stays in Metabase" : null].filter(Boolean).join(" · ");
    const holds = [entry.kind === "dashboard" ? plural(entry.cards || 0, "card", "cards") : null,
      entry.kind !== "chat" && entry.messages ? plural(entry.messages, "message", "messages") : null,
      entry.files ? plural(entry.files, "attached file", "attached files") : null].filter(Boolean).join(" · ");
    let back = null;
    if (entry.kind !== "other") {
      back = h("button", { class: "btn small", type: "button", "aria-label": `Put back, ${entry.name}`, "data-focus": `back:${entry.key}` }, "Put back");
      back.addEventListener("click", () => ui.whileBusy(back, () => putBack(ui, box, entry)));
    }
    const forever = h("button", { class: "btn danger small", type: "button", "aria-label": `Delete forever, ${entry.name}`,
      "data-focus": `delete:${entry.key}` }, "Delete forever…");
    forever.addEventListener("click", () => askDelete(ui, box, [entry], false));
    return h("li", { class: "trash-row", "data-key": entry.key },
      icon(KIND_ICON[entry.kind] || "file", 16),
      h("div", { class: "trash-text" },
        h("b", { title: entry.name }, entry.name),
        h("small", null, where),
        holds ? h("small", null, holds) : null,
        entry.note ? h("small", { class: "trash-note" }, entry.note) : null),
      h("div", { class: "trash-actions" }, back, forever));
  }

  async function act(ui, box, text, kind, open = null) {
    said = { text, kind, open, at: Date.now(), fresh: true };
    await render(ui, box);
  }

  async function putBack(ui, box, entry) {
    const reply = await ui.post("/api/trash/back", { key: entry.key });
    if (reply.error) return act(ui, box, reply.error, "bad");
    const words = {
      dashboard: `“${reply.name}” is back in the studio${reply.moved ? `, as ${reply.slug}: its name was taken` : ""}.`,
      draft: `The draft “${reply.name}” is back in the sidebar.`,
      chat: "The conversation is back on its dashboard.",
    }[reply.kind] || "It is back.";
    await act(ui, box, words, "ok", reply.kind === "draft" ? null : reply.slug);
    ui.refresh();
  }

  function goes(entry) {
    return {
      dashboard: "Its queries, its conversation, its attached files and every version go from this PC.",
      draft: "The draft’s conversation and attached files go from this PC.",
      chat: "This conversation and its attached files go from this PC.",
    }[entry.kind] || "These files go from this PC.";
  }

  // The confirmation names what goes; only its button sends the deletion, confirmed.
  function askDelete(ui, box, chosen, everything) {
    const { h, icon } = ui;
    if (!chosen.length) return;
    const title = everything ? "Empty the Trash?" : `Delete “${chosen[0].name}” forever?`;
    const counted = KINDS.map(([kind, one, many]) => [chosen.filter((e) => e.kind === kind).length, one, many])
      .filter(([n]) => n).map(([n, one, many]) => plural(n, one, many));
    const names = chosen.map((e) => e.name);
    const go = h("button", { class: "btn danger", type: "button" }, icon("trash", 15), everything ? "Empty trash" : "Delete forever");
    go.addEventListener("click", () => ui.whileBusy(go, async () => {
      const reply = everything ? await ui.post("/api/trash/empty", { confirmed: true, keys: chosen.map((e) => e.key) })
        : await ui.post("/api/trash/delete", { keys: [chosen[0].key], confirmed: true });
      if (reply.error) {
        ui.showDialog(h("h2", null, title), ui.liveList("Nothing was deleted", [reply.error], "stop"), ui.liveFooter(ui.closeButton("Close")));
        return render(ui, box);
      }
      ui.closeDialog();
      const failed = (reply.failed || []).length;
      const done = everything ? (failed ? `${plural(reply.count, "entry was", "entries were")} deleted forever.` : "The Trash is empty.")
        : `“${chosen[0].name}” is deleted forever.`;
      await act(ui, box, failed
        ? `${everything ? `${done} ` : ""}${plural(failed, "entry", "entries")} could not be deleted just now: a file is open in another program. Close it and try again.`
        : done, failed ? "warn" : "ok");
    }));
    ui.showDialog(h("h2", null, title),
      h("p", null, everything ? `Everything in it goes from this PC: ${joined(counted)}.` : goes(chosen[0])),
      everything ? h("ul", { class: "check-list trash-names" }, names.slice(0, 8).map((name) => h("li", null, name)),
        names.length > 8 ? h("li", null, `and ${names.length - 8} more`) : null) : null,
      h("p", null, "This can’t be undone. Metabase is not touched."),
      ui.liveFooter(ui.closeButton("Cancel"), go));
  }

  return { render };
})();
