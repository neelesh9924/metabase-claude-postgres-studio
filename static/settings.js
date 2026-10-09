// The Settings page, which is also the first-run setup. Loaded before app.js, which calls
// studioSettings.render with its own helpers. A saved password or key is never sent here:
// the page only learns that one is saved, and an empty field keeps it.
window.studioSettings = (() => {
  "use strict";

  const SSL_MODES = ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"];
  let ui = null;      // { h, icon, get, post, onDone }
  let saved = {};     // settings as the server has them
  let setup = {};     // what is ready
  let found = null;   // the last answer from Metabase: databases and collections

  function field(label, input, hint) {
    return ui.h("label", { class: "field" }, ui.h("span", { class: "field-label" }, label), input, hint ? ui.h("span", { class: "field-hint" }, hint) : null);
  }

  function text(name, attrs) {
    return ui.h("input", { class: "input", name, value: saved[name] ?? "", autocomplete: "off", spellcheck: "false", ...attrs });
  }

  function secret(name) {
    const isSaved = saved[`${name}_set`];
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

  async function act(button, work) {
    const label = button.textContent;
    button.disabled = true;
    button.textContent = "Working…";
    try {
      await work();
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  }

  async function save(form, out, extra) {
    const reply = await ui.post("/api/settings", { values: { ...values(form), ...(extra || {}) } });
    if (reply.problems || reply.error) {
      put(out, note("bad", reply.problems || [reply.error]));
      return false;
    }
    saved = reply.settings;
    setup = reply.setup;
    for (const el of form.querySelectorAll('input[type="password"]')) {
      el.value = "";
      el.placeholder = saved[`${el.name}_set`] ? "Saved. Type to replace it." : "";
    }
    return true;
  }

  // ---------- database ----------

  function tablesLine() {
    const t = setup.tables;
    return t ? `${t.tables.toLocaleString("en-US")} tables on record, read ${t.generated_at.replace("T", " at ").slice(0, 19)}.` : "The table list has not been read yet.";
  }

  function databaseSection() {
    const out = ui.h("div", { class: "results" });
    const tables = ui.h("p", { class: "field-hint" }, tablesLine());
    const form = ui.h("form", { class: "form" },
      ui.h("div", { class: "fields" },
        field("Host", text("db_host", { placeholder: "db.example.com" })),
        field("Port", text("db_port", { inputmode: "numeric", class: "input short" })),
        field("Database", text("db_name")),
        field("User", text("db_user"), "A user that can only read is the safe choice."),
        field("Password", secret("db_password")),
        field("SSL mode", select("db_sslmode", SSL_MODES.map((m) => [m, m]), saved.db_sslmode)),
        field("Schemas", text("db_schemas", { value: (saved.db_schemas || []).join(", ") }), "Comma-separated. Usually just public."),
        field("Time zone", text("timezone", { placeholder: "Asia/Kolkata" }), "Days and “today” are counted in this zone.")),
      out);
    const test = ui.h("button", { class: "btn", type: "button" }, "Test connection");
    const keep = ui.h("button", { class: "btn primary", type: "submit" }, "Save");
    const read = ui.h("button", { class: "btn", type: "button", title: "Reads table and column names from the catalog. No table's rows are read." }, "Read the table list");
    test.addEventListener("click", () => act(test, async () => {
      const reply = await ui.post("/api/settings/check", { what: "database", values: values(form) });
      if (!reply.ok) return void put(out, note("bad", reply.error || "Could not connect."));
      put(out, 
        note("ok", `Connected as ${reply.user} to ${reply.server}. ${reply.tables.toLocaleString("en-US")} tables can be read.`),
        reply.notes.length ? note("warn", reply.notes) : null);
    }));
    read.addEventListener("click", () => act(read, async () => {
      const reply = await ui.post("/api/settings/tables", {});
      if (reply.error) return void put(out, note("bad", reply.error));
      setup = (await ui.get("/api/settings")).setup;
      tables.textContent = tablesLine();
      put(out, note("ok", `Read ${reply.tables.toLocaleString("en-US")} tables.`));
      refreshDone();
    }));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      act(keep, async () => {
        if (await save(form, out)) {
          put(out, note("ok", setup.tables ? "Saved." : "Saved. Now read the table list, so Claude knows what there is."));
          refreshDone();
        }
      });
    });
    form.append(ui.h("div", { class: "form-foot" }, tables, ui.h("span", { class: "grow" }), read, test, keep));
    return section("database", "table", "Database", "The PostgreSQL database your dashboards read. Every query runs in a read-only transaction, one at a time.", form);
  }

  // ---------- metabase ----------

  function metabaseChoices(box) {
    if (!found) {
      const chosen = saved.metabase_database_id
        ? `Using database ${saved.metabase_database_id} and the collection “${saved.metabase_collection || saved.metabase_collection_id || "not chosen"}”. Press Check to change them.`
        : "Press Check to list the databases and collections this key can use.";
      return void box.replaceChildren(ui.h("p", { class: "field-hint" }, chosen));
    }
    const databases = found.databases.filter((d) => d.can_query);
    const writable = found.collections.filter((c) => c.can_write);
    box.replaceChildren(
      field("Database in Metabase", databases.length
        ? select("metabase_database_id", [["", "Choose…"], ...databases.map((d) => [d.id, d.name])], saved.metabase_database_id)
        : ui.h("span", { class: "field-hint" }, "This key cannot write SQL questions on any database."),
        "The Metabase connection to the same database as above."),
      field("Collection for new dashboards", writable.length
        ? select("metabase_collection_id", [["", "Choose…"], ...writable.map((c) => [c.id, c.inside ? `${c.name}  (in ${c.inside})` : c.name])], saved.metabase_collection_id)
        : ui.h("span", { class: "field-hint" }, "This key cannot write to any collection yet. In Metabase, give its group Curate on one."),
        "Go live writes only here."));
  }

  function metabaseSection() {
    const out = ui.h("div", { class: "results" });
    const choices = ui.h("div", { class: "fields" });
    const form = ui.h("form", { class: "form" },
      ui.h("div", { class: "fields" },
        field("Address", text("metabase_url", { placeholder: "https://metabase.example.com" })),
        field("API key", secret("metabase_api_key"), "Metabase: Admin > Settings > Authentication > API keys.")),
      choices, out);
    metabaseChoices(choices);
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
        const collection = found && found.collections.find((c) => String(c.id) === picked.metabase_collection_id);
        if (await save(form, out, collection ? { metabase_collection: collection.name } : {})) {
          put(out, note("ok", setup.metabase ? "Saved. Go live is set up." : "Saved. Choose a database and a collection to finish."));
          refreshDone();
        }
      });
    });
    form.append(ui.h("div", { class: "form-foot" }, ui.h("span", { class: "grow" }), check, keep));
    return section("metabase", "upload", "Metabase", "Needed only for Go live. Without it you can still build and preview dashboards.", form);
  }

  // ---------- claude ----------

  function claudeSection() {
    const out = ui.h("div", { class: "results" });
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

  // ---------- limits ----------

  function limitsSection() {
    const out = ui.h("div", { class: "results" });
    const number = (name, attrs) => text(name, { inputmode: "numeric", class: "input short", ...attrs });
    const form = ui.h("form", { class: "form" },
      ui.h("div", { class: "fields" },
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
    return section("limits", "lock", "Limits", "What keeps a mistake from loading the database.", form);
  }

  // ---------- page ----------

  let doneBox = null;
  function refreshDone() {
    if (!doneBox) return;
    const ready = setup.database && setup.tables;
    doneBox.replaceChildren(
      ui.h("div", { class: "done-text" },
        ui.h("strong", null, ready ? "The database is connected." : "Connect the database to start."),
        ui.h("span", null, ready
          ? (setup.metabase ? " Metabase is set up too." : " Metabase can wait until you want to go live.")
          : " Fill in the Database section, save it and read the table list.")),
      ui.h("button", { class: "btn primary", type: "button", disabled: !ready, onclick: () => ui.onDone() }, "Open dashboards"));
  }

  async function render(helpers, container, extra) {
    ui = helpers;
    found = null;
    container.replaceChildren(ui.h("p", { class: "lede" }, "Loading…"));
    const info = await ui.get("/api/settings");
    saved = info.settings;
    setup = info.setup;
    doneBox = ui.h("div", { class: "done" });
    container.replaceChildren(
      ui.h("div", { class: "settings" }, doneBox, databaseSection(), metabaseSection(), claudeSection(), limitsSection(), extra || null,
        ui.h("p", { class: "field-hint" }, "Settings, saved secrets, conversations and logs are kept in the data folder beside the app. The password and the key are stored protected and are never shown again.")));
    refreshDone();
  }

  return { render };
})();
