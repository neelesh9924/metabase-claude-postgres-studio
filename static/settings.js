// The Settings page, which is also the first-run setup. Loaded before app.js, which calls
// studioSettings.render with its own helpers. A saved password or key is never sent here:
// the page only learns that one is saved, and an empty field keeps it.
// A button at work keeps the focus (ui.whileBusy), a redrawn list gives it back to the same control, a field's hint is
// its description rather than part of its name, and what a check or a save found is said (each .results is a status).
window.studioSettings = (() => {
  "use strict";

  const SSL_MODES = ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"];
  let ui = null;      // the page's ui (h, icon, get, post, whileBusy, showDialog, focusFirst…) and onDone
  let saved = {};     // settings as the server has them
  let setup = {};     // what is ready
  let found = null;   // the last answer from Metabase: databases and collections
  let openDb = null;  // the database whose form is open: its id, or "new"
  let redrawMetabase = () => {};  // the Metabase section names the databases too
  let fields = 0;     // gives each field's control and hint an id of their own

  // A labelled control, its hint linked as its description (aria-describedby), not read as part of its name.
  function field(label, input, hint) {
    const id = `set-field-${++fields}`;
    const control = input instanceof Element && input.matches("input, select, textarea") ? input : null;
    const note = hint ? ui.h("span", { class: "field-hint", id: `${id}-hint` }, hint) : null;
    if (control) {
      control.id = control.id || id;
      if (note) control.setAttribute("aria-describedby", [control.getAttribute("aria-describedby"), note.id].filter(Boolean).join(" "));
    }
    return ui.h("div", { class: "field" },
      control ? ui.h("label", { class: "field-label", for: control.id }, label) : ui.h("span", { class: "field-label" }, label), input, note);
  }

  // The box a check's or a save's result goes in: a status, so what it says is heard.
  function results() {
    return ui.h("div", { class: "results", role: "status" });
  }

  function text(name, attrs) {
    return ui.h("input", { class: "input", name, value: saved[name] ?? "", autocomplete: "off", spellcheck: "false", ...attrs });
  }

  function secret(name, isSaved) {
    return ui.h("input", { class: "input", name, type: "password", autocomplete: "new-password", placeholder: isSaved ? "Saved. Type to replace it." : "" });
  }

  function select(name, options, current) {
    return ui.h("select", { class: "input", name }, options.map(([value, label]) =>
      ui.h("option", { value, selected: String(value) === String(current ?? "") }, label)));
  }

  function values(form) {
    const out = {};
    for (const el of form.querySelectorAll("[name]")) out[el.name] = el.value.trim();
    return out;
  }

  function note(kind, ...lines) {
    return ui.h("div", { class: `result ${kind}` }, ui.icon(kind === "ok" ? "check" : "alert", 16), ui.h("div", null, lines.flat().filter(Boolean).map((line) => ui.h("div", null, line))));
  }

  // replaceChildren would print a missing piece as the word "null".
  function put(box, ...pieces) {
    box.replaceChildren(...pieces.flat().filter(Boolean));
  }

  function section(id, iconName, title, lede, body) {
    return ui.h("section", { class: "panel", id: `set-${id}` },
      ui.h("header", { class: "panel-head" }, ui.h("span", { class: "panel-mark" }, ui.icon(iconName, 17)), ui.h("div", null, ui.h("h2", null, title), ui.h("p", null, lede))),
      body);
  }

  // A button's work: it says "Working…" and stays where it is, focused, its clicks ignored until the work is done. It is
  // never disabled, which would drop the focus on the page.
  async function act(button, work) {
    const label = button.textContent;
    button.textContent = "Working…";
    try {
      return await ui.whileBusy(button, work);
    } finally {
      button.textContent = label;
    }
  }

  // What a reply says is wrong, or null when it went through.
  function trouble(reply) {
    if (reply.problems) return reply.problems;
    return reply.error ? [reply.error] : null;
  }

  function took(reply) {
    saved = reply.settings;
    setup = reply.setup;
  }

  async function save(form, out, extra) {
    const reply = await ui.post("/api/settings", { values: { ...values(form), ...(extra || {}) } });
    if (trouble(reply)) {
      put(out, note("bad", trouble(reply)));
      return false;
    }
    took(reply);
    for (const el of form.querySelectorAll('input[type="password"]')) {
      el.value = "";
      el.placeholder = saved[`${el.name}_set`] ? "Saved. Type to replace it." : "";
    }
    return true;
  }

  // ---------- databases ----------

  // "12 tables on record, read yesterday, 5:43 pm.", the time in the studio's one way (static/time.js).
  function tablesLine(db) {
    const t = db && db.tables;
    return t ? [`${t.tables.toLocaleString("en-US")} tables on record, read `, window.studioTime.tag(t.generated_at, "inline"), "."]
      : ["The table list has not been read yet."];
  }

  // One database's form: an existing one to change, or an empty one to add. redraw(message, focus) draws the whole list
  // again, the focus on the control whose data-key is focus (else on the one that had it).
  function databaseForm(db, redraw, message) {
    const d = db || { port: 5432, sslmode: "require", schemas: ["public"] };
    const which = db ? db.id : "new";
    const out = results();
    // Put in once the box is on the page, so that a status drawn with the list is still heard.
    if (message) requestAnimationFrame(() => put(out, message));
    const input = (name, attrs) => ui.h("input", { class: "input", name, value: d[name] ?? "", autocomplete: "off", spellcheck: "false", ...attrs });
    const form = ui.h("form", { class: "form" },
      ui.h("div", { class: "fields" },
        field("Name", input("name", { placeholder: "Sales", "data-key": `name:${which}` }), "What you call it. You pick it by this name when you start a dashboard."),
        field("Host", input("host", { placeholder: "db.example.com" })),
        field("Port", input("port", { inputmode: "numeric", class: "input short" })),
        field("Database", input("dbname")),
        field("User", input("user"), "A user that can only read is the safe choice."),
        field("Password", secret("password", d.password_set)),
        field("SSL mode", select("sslmode", SSL_MODES.map((m) => [m, m]), d.sslmode)),
        field("Schemas", input("schemas", { value: (d.schemas || []).join(", ") }), "Comma-separated. Usually just public.")),
      out);
    const filled = () => ({ ...values(form), id: d.id });
    const test = ui.h("button", { class: "btn", type: "button", "data-key": `test:${which}` }, "Test connection");
    const keep = ui.h("button", { class: "btn primary", type: "submit", "data-key": `save:${which}` }, db ? "Save" : "Add");
    test.addEventListener("click", () => act(test, async () => {
      const reply = await ui.post("/api/settings/check", { what: "database", values: filled() });
      if (!reply.ok) return void put(out, note("bad", reply.error || "Could not connect."));
      put(out,
        note("ok", `Connected as ${reply.user} to ${reply.server}. ${reply.tables.toLocaleString("en-US")} tables can be read.`),
        reply.notes.length ? note("warn", reply.notes) : null);
    }));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      act(keep, async () => {
        const reply = await ui.post("/api/settings/database", { values: filled() });
        if (trouble(reply)) return void put(out, note("bad", trouble(reply)));
        took(reply);
        openDb = reply.id;
        const now = saved.databases.find((one) => one.id === reply.id);
        // A database just added is Saved from now on: the focus goes to its own Save.
        redraw(note("ok", now && now.tables ? "Saved." : "Saved. Now read the table list, so Claude knows what there is."), `save:${reply.id}`);
        redrawMetabase();
        refreshDone();
      });
    });
    const foot = [ui.h("span", { class: "field-hint" }, db ? tablesLine(db) : ""), ui.h("span", { class: "grow" })];
    if (db) {
      const remove = ui.h("button", { class: "btn ghost danger", type: "button", "data-key": `remove:${which}` }, "Remove");
      remove.addEventListener("click", () => askRemove(db, remove, out, redraw));
      const read = ui.h("button", { class: "btn", type: "button", "data-key": `read:${which}`, title: "Reads table and column names from the catalog. No table's rows are read." }, "Read the table list");
      read.addEventListener("click", () => act(read, async () => {
        const reply = await ui.post("/api/settings/tables", { database: db.id });
        if (trouble(reply)) return void put(out, note("bad", trouble(reply)));
        took(reply);
        redraw(note("ok", `Read ${reply.tables.toLocaleString("en-US")} tables.`));
        refreshDone();
      }));
      foot.push(remove, read);
    } else if ((saved.databases || []).length) {
      foot.push(ui.h("button", { class: "btn ghost", type: "button", "data-key": "cancel:new", onclick: () => { openDb = null; redraw(null, "add"); } }, "Cancel"));
    }
    form.append(ui.h("div", { class: "form-foot" }, foot, test, keep));
    return form;
  }

  // Remove, asked in the dialog: it names the database, and the studio refuses while a dashboard is on it.
  function askRemove(db, button, out, redraw) {
    const keep = ui.closeButton("Keep it");
    const go = ui.h("button", { class: "btn danger", type: "button" }, `Remove “${db.name}”`);
    go.addEventListener("click", () => act(go, async () => {
      const reply = await ui.post("/api/settings/database/remove", { database: db.id });
      if (trouble(reply)) {
        return void ui.showDialog(ui.h("h2", null, `“${db.name}” stays`), ui.h("p", null, trouble(reply).join(" ")),
          ui.liveFooter(ui.closeButton("Close")));
      }
      took(reply);
      openDb = null;
      ui.closeDialog();
      redraw(null, "add");
      redrawMetabase();
      refreshDone();
      ui.announce(`“${db.name}” was removed from the studio.`);
    }));
    ui.dialog.addEventListener("close", () => ui.focusFirst(button), { once: true });   // gone once removed: then Add has it
    ui.showDialog(
      ui.h("h2", null, `Remove “${db.name}”?`),
      ui.h("p", null, "The studio forgets its connection, its saved password and its table list. The database itself is not touched. "
        + "It cannot be removed while a dashboard is on it: remove those dashboards first."),
      ui.liveFooter(keep, go));
  }

  function databasesSection() {
    const box = ui.h("div", { class: "db-list" });
    // Drawn afresh each time: the focus goes back to the control with the same data-key (or the one asked for).
    const draw = (message, focus) => {
      const had = box.contains(document.activeElement) ? document.activeElement.dataset.key : null;
      const all = saved.databases || [];
      if (!all.length) openDb = "new";
      const rows = all.map((db) => {
        const open = openDb === db.id;
        const toggle = () => {
          openDb = open ? null : db.id;
          draw();
        };
        return ui.h("div", { class: "db" },
          ui.h("div", { class: "db-row" },
            ui.h("div", { class: "db-text" }, ui.h("strong", null, db.name), ui.h("span", { class: "field-hint" }, `${db.dbname} on ${db.host} · `, tablesLine(db))),
            ui.h("button", { class: "btn small", type: "button", "data-key": `toggle:${db.id}`, "aria-expanded": String(open),
              "aria-label": `${open ? "Close" : "Edit"} “${db.name}”`, onclick: toggle }, open ? "Close" : "Edit")),
          open ? databaseForm(db, draw, message) : null);
      });
      const add = openDb === "new"
        ? ui.h("div", { class: "db" },
            all.length ? ui.h("div", { class: "db-row" }, ui.h("div", { class: "db-text" }, ui.h("strong", null, "Another database"))) : null,
            databaseForm(null, draw, message))
        : ui.h("div", { class: "db-row" }, ui.h("button", { class: "btn", type: "button", "data-key": "add", onclick: () => { openDb = "new"; draw(null, "name:new"); } }, ui.icon("plus", 15), "Add a database"));
      put(box, rows, add);
      const wanted = focus || had;
      if (wanted) ui.focusFirst(box.querySelector(`[data-key="${CSS.escape(wanted)}"]`), box.querySelector('[data-key="add"]'), box.querySelector('[data-key="name:new"]'));
    };
    draw();
    return section("database", "table", "Databases", "The PostgreSQL databases your dashboards read. Each dashboard uses one of them. Every query runs in a read-only transaction, one at a time.", box);
  }

  // ---------- metabase ----------

  function metabaseChoices(box) {
    const all = saved.databases || [];
    if (!found) {
      const paired = all.filter((d) => d.metabase_database_id).map((d) => (all.length > 1 ? `“${d.name}” uses Metabase database ${d.metabase_database_id}` : `Using Metabase database ${d.metabase_database_id}`));
      const chosen = paired.length
        ? `${paired.join(", ")}, and the collection “${saved.metabase_collection || saved.metabase_collection_id || "not chosen"}”. Press Check to change them.`
        : "Press Check to list the databases and collections this key can use.";
      return void box.replaceChildren(ui.h("p", { class: "field-hint" }, chosen));
    }
    const usable = found.databases.filter((d) => d.can_query);
    const writable = found.collections.filter((c) => c.can_write);
    box.replaceChildren(
      field("Collection for new dashboards", writable.length
        ? select("metabase_collection_id", [["", "Choose…"], ...writable.map((c) => [c.id, c.inside ? `${c.name}  (in ${c.inside})` : c.name])], saved.metabase_collection_id)
        : ui.h("span", { class: "field-hint" }, "This key cannot write to any collection yet. In Metabase, give its group Curate on one."),
        "Go live writes only here."),
      ...all.map((d) => field(all.length > 1 ? `Database in Metabase for “${d.name}”` : "Database in Metabase", usable.length
        ? select(`pair:${d.id}`, [["", "Choose…"], ...usable.map((m) => [m.id, m.name])], d.metabase_database_id)
        : ui.h("span", { class: "field-hint" }, "This key cannot write SQL questions on any database."),
        `The Metabase connection to the same database as ${all.length > 1 ? `“${d.name}”` : "above"}.`)));
  }

  function metabaseSection() {
    const out = results();
    const choices = ui.h("div", { class: "fields" });
    const form = ui.h("form", { class: "form" },
      ui.h("div", { class: "fields" },
        field("Address", text("metabase_url", { placeholder: "https://metabase.example.com" })),
        field("API key", secret("metabase_api_key", saved.metabase_api_key_set), "Metabase: Admin > Settings > Authentication > API keys.")),
      choices, out);
    redrawMetabase = () => metabaseChoices(choices);
    redrawMetabase();
    const check = ui.h("button", { class: "btn", type: "button" }, "Check");
    const keep = ui.h("button", { class: "btn primary", type: "submit" }, "Save");
    check.addEventListener("click", () => act(check, async () => {
      const reply = await ui.post("/api/settings/check", { what: "metabase", values: values(form) });
      if (!reply.ok) return void put(out, note("bad", reply.error || "Metabase did not answer."));
      found = reply;
      metabaseChoices(choices);
      put(out,
        note("ok", `Metabase accepts the key (${reply.who || "API key"}).`),
        reply.admin ? note("warn", "This key belongs to an admin group. A key that can write to one collection only is safer.") : null,
        reply.root_can_write ? note("warn", "This key can also write to the top-level collection. The studio never does.") : null);
    }));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      act(keep, async () => {
        const picked = values(form);
        const general = Object.fromEntries(Object.entries(picked).filter(([name]) => !name.startsWith("pair:")));
        const collection = found && found.collections.find((c) => String(c.id) === picked.metabase_collection_id);
        const reply = await ui.post("/api/settings", { values: { ...general, ...(collection ? { metabase_collection: collection.name } : {}) } });
        if (trouble(reply)) return void put(out, note("bad", trouble(reply)));
        took(reply);
        // Each database keeps its own Metabase database.
        for (const db of saved.databases) {
          const choice = picked[`pair:${db.id}`];
          if (choice === undefined || choice === String(db.metabase_database_id ?? "")) continue;
          const paired = await ui.post("/api/settings/database", { values: { id: db.id, metabase_database_id: choice } });
          if (trouble(paired)) return void put(out, note("bad", trouble(paired)));
          took(paired);
        }
        const key = form.querySelector('[name="metabase_api_key"]');
        key.value = "";
        key.placeholder = saved.metabase_api_key_set ? "Saved. Type to replace it." : "";
        const unpaired = saved.databases.filter((d) => !d.metabase_database_id).map((d) => `“${d.name}”`);
        put(out,
          note("ok", setup.metabase ? "Saved. Go live is set up." : "Saved. Choose a database and a collection to finish."),
          setup.metabase && unpaired.length ? note("warn", `${unpaired.join(", ")} ${unpaired.length === 1 ? "has" : "have"} no Metabase database yet, so ${unpaired.length === 1 ? "its" : "their"} dashboards cannot go live.`) : null);
        refreshDone();
      });
    });
    form.append(ui.h("div", { class: "form-foot" }, ui.h("span", { class: "grow" }), check, keep));
    return section("metabase", "upload", "Metabase", "Needed only for Go live. Without it you can still build and preview dashboards.", form);
  }

  // ---------- claude ----------

  function claudeSection() {
    const out = results();
    const form = ui.h("form", { class: "form" },
      ui.h("div", { class: "fields" },
        field("Model", text("model", { placeholder: "Account default", list: "models" }), "Empty uses the account's default. “sonnet” uses less of the limit."),
        field("Path to Claude Code", text("claude_bin", { placeholder: "Found automatically" }), "Only if it is not found by itself.")),
      ui.h("datalist", { id: "models" }, ["sonnet", "opus", "haiku"].map((m) => ui.h("option", { value: m }))),
      out);
    const show = (reply) => put(out, reply.ok
      ? note("ok", `${reply.version} at ${reply.path}`, reply.answered ? `It answered, using ${reply.model}.` : null)
      : note("bad", reply.error));
    const look = ui.h("button", { class: "btn", type: "button", title: "Sends one short request. Uses a little of your Claude limit." }, "Check the sign-in");
    const keep = ui.h("button", { class: "btn primary", type: "submit" }, "Save");
    look.addEventListener("click", () => act(look, async () => show(await ui.post("/api/settings/check", { what: "claude", run: true }))));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      act(keep, async () => {
        if (await save(form, out)) show(await ui.post("/api/settings/check", { what: "claude" }));
      });
    });
    form.append(ui.h("div", { class: "form-foot" }, ui.h("span", { class: "grow" }), look, keep));
    ui.post("/api/settings/check", { what: "claude" }).then(show);
    return section("claude", "sparkle", "Claude", "The Ask Claude panel runs Claude Code on this PC, with the sign-in you already use in a terminal.", form);
  }

  // ---------- time zone and limits ----------

  function limitsSection() {
    const out = results();
    const number = (name, attrs) => text(name, { inputmode: "numeric", class: "input short", ...attrs });
    const form = ui.h("form", { class: "form" },
      ui.h("div", { class: "fields" },
        field("Time zone", text("timezone", { placeholder: "Asia/Kolkata" }), "Days and “today” are counted in this zone, in every database."),
        field("Query time limit (seconds)", number("statement_timeout_s", { value: Math.round((saved.statement_timeout_ms || 0) / 1000) }), "The database stops a query after this long."),
        field("Heavy-query limit (plan cost)", number("max_plan_cost"), "A query the planner rates above this waits for your “Run anyway”."),
        field("Rows per card", number("preview_row_limit"), "Most rows fetched for one card."),
        field("Queries per request", number("max_queries"), "Most queries Claude may run for one request.")),
      out);
    const keep = ui.h("button", { class: "btn primary", type: "submit" }, "Save");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      act(keep, async () => {
        const seconds = Number(values(form).statement_timeout_s);
        form.querySelector('[name="statement_timeout_s"]').name = "";
        const ok = await save(form, out, { statement_timeout_ms: Math.round(seconds * 1000) });
        form.querySelector('[name=""]').name = "statement_timeout_s";
        if (ok) put(out, note("ok", "Saved."));
      });
    });
    form.append(ui.h("div", { class: "form-foot" }, ui.h("span", { class: "grow" }), keep));
    return section("limits", "lock", "Time zone and limits", "How days are counted, and what keeps a mistake from loading a database. They apply to every database.", form);
  }

  // ---------- page ----------

  let doneBox = null;
  function refreshDone() {
    if (!doneBox) return;
    const ready = setup.database && setup.tables;
    doneBox.replaceChildren(
      ui.h("div", { class: "done-text" },
        ui.h("strong", null, ready ? "The database is connected." : "Connect a database to start."),
        ui.h("span", null, ready
          ? (setup.metabase ? " Metabase is set up too." : " Metabase can wait until you want to go live.")
          : " Fill in the Databases section, save it and read the table list.")),
      ui.h("button", { class: "btn primary", type: "button", disabled: !ready, onclick: () => ui.onDone() }, "Open dashboards"));
  }

  async function render(helpers, container, extra) {
    ui = helpers;
    found = null;
    openDb = null;
    container.replaceChildren(ui.h("p", { class: "lede" }, "Loading…"));
    const info = await ui.get("/api/settings");
    saved = info.settings;
    setup = info.setup;
    doneBox = ui.h("div", { class: "done" });
    container.replaceChildren(
      ui.h("div", { class: "settings" }, doneBox, databasesSection(), metabaseSection(), claudeSection(), limitsSection(), extra || null,
        ui.h("p", { class: "field-hint" }, "Settings, saved secrets, conversations and logs are kept in the data folder beside the app. Passwords and the key are stored protected and are never shown again.")));
    refreshDone();
  }

  return { render };
})();
