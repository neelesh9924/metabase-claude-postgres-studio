---
name: dashboard-edit
description: Change an existing dashboard in Dashboard Studio - add, remove or alter cards, queries, chart types, titles or layout. Use when asked to change a dashboard that already exists. Not for a new one (use dashboard-build).
---

# Change a dashboard

CLAUDE.md in this folder holds the rules, the file format and the SQL conventions. Read
it if it is not already in your context.

Inside the app, use the studio tools `list_tables`, `describe_table`, `run_query` and
`check_dashboard`. In a terminal session they are `python studio.py tables`, `describe`,
`q` and `check`.

1. Read `dashboards/<slug>/dashboard.json` and the `.sql` files the request touches.
2. Change only what the request needs. A title, chart type, size or position lives in
   `dashboard.json` and costs no query. Leave working queries as they are, and leave
   the `"database"` line as it is: a dashboard stays on its database.
3. For a new or changed query: `describe_table` first if the table is new to this
   dashboard, write the `.sql` file, then test it with `run_query` on that file. A
   refused query is narrowed, never sent again unchanged.
4. When a card is removed, delete its entry from `dashboard.json`. Its `.sql` file can
   stay; an unused file is harmless.
5. Keep the grid tidy: no overlaps, no gaps left by a removed card.
   For a filter, follow "Filters" in CLAUDE.md: add it to `filters`, then give each
   card that should obey it the `{{key}}` in its query and the column in its `filters`.
6. Run `check_dashboard` and fix every problem it lists.
7. Say in one or two sentences what changed. If part of the request could not be done,
   say which part and why.

If the request is really a different dashboard, say so and change nothing.
