---
name: dashboard-build
description: Plan and build a new dashboard in Dashboard Studio from a description. Use when asked for a new dashboard, in two phases, "plan" then "build". Not for changing an existing one (use dashboard-edit).
---

# Build a new dashboard

CLAUDE.md in this folder holds the rules, the file format and the SQL conventions. Read
it if it is not already in your context. This skill is the order of work.

Two phases. The request says which one to do. In a chat with no phase named, do the
plan, show it, and build only after the user agrees.

## Tools

Inside the app, use the studio tools `list_tables`, `describe_table`, `run_query` and
`check_dashboard`. In a terminal session they are `python studio.py tables`, `describe`,
`q` and `check`. `list_tables` and `describe_table` read a local snapshot and cost the
database nothing.

## Phase "plan"

No queries in this phase.

1. Find the tables with `list_tables`. Search for the subject's own words (`order`,
   `invoice`, `user`, `event`), singular and plural.
2. `describe_table` each candidate. Note the row count, the time column and whether it
   is indexed. A card on a table of more than about 1 million rows needs a bounded
   filter on an indexed column.
3. Choose the cards. Follow "Choosing a card" in CLAUDE.md. Four to eight cards is
   usually enough: the numbers first, then the trends, then the detail.
4. Leave out what the data does not support, and say so. Never guess what a column or a
   status value means; if a card depends on a guess, list it under the assumptions.
5. Reply with the plan in the form the request gives. For every table say its size and
   the filter the queries will use, so the user sees what will be read before it is.

## Phase "build"

Build the approved plan and nothing beyond it.

1. Write each card's query to `dashboards/<slug>/<key>.sql`, then test it with
   `run_query` on that file. Testing by file lets the preview reuse the result, so the
   database is not asked twice.
2. Fix a failing query and test again. If a query is refused as heavy, narrow it; do not
   send it again unchanged. If a card cannot be made to work in two or three tries,
   drop it and say so.
3. Look at the rows before trusting them: are the numbers plausible, are there gaps or
   nulls, is a "Personal data hidden" warning shown? Remove any personal-data column.
4. Write `dashboards/<slug>/dashboard.json` last, once the queries work, so the preview
   never shows a half-made dashboard. The exception is a dashboard with filters (see
   "Filters" in CLAUDE.md): a query with `{{...}}` can only be tested once
   `dashboard.json` names the filters and the card, so write that file first.
5. Run `check_dashboard` and fix every problem it lists.
6. Say in two or three sentences what was built, and what was left out and why.

## Layout

24 columns. A heading (24 x 1) above each group. KPI cards 6 x 3, four in a row. Charts
12 x 6, two in a row, or 8 x 6, three in a row. Rows must not overlap: a card's `row`
is the previous group's `row` plus its `size_y`.
