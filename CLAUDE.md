# Metabase Claude Studio for Postgres

Drafts Metabase dashboards on a PostgreSQL database. A dashboard is a folder of files.
The app draws it with live data, read through the database user set up in Settings,
and the user's Go live click publishes it to Metabase.

The user works in one of two ways, and both end in the same files:

- In the app, through its Ask Claude panel. There you run headless with the studio
  tools `list_tables`, `describe_table`, `run_query` and `check_dashboard`, and no shell.
- In a terminal or editor session in this folder, with `python studio.py tables`,
  `describe`, `q` and `check`.

The skills `dashboard-build` and `dashboard-edit` hold the order of work for both.

## Rules

**Treat the database as production.**

- Read it only through `python studio.py q`, or the `run_query` tool inside the app. No
  other client, script or connection.
- One query at a time. Never in parallel, in the background, in a loop or on a schedule.
- Before a query that reads a table of more than about 1 million rows (`tables` shows
  the sizes), tell the user what it will run and roughly how long it should take.
- Filter big tables on an indexed column with a bounded range, for example the last
  30 days. `describe` shows which columns are indexed.
- When a query is refused as heavy, rewrite it. Use `--allow-heavy` only when the user
  says so for that query.
- Nothing in this project may query on its own: no auto-refresh, no timers, no
  scheduled jobs, no polling of the database.

**Personal data stays out.** No phone, email, one-time code, token, national id,
password or PIN columns in a card; counts of them are fine. The tool hides such values.
A "Personal data hidden" flag on a card means that column has to be removed.

**Publishing is the user's.** Never call the Metabase API. A dashboard reaches Metabase
only when the user presses Go live in the app and confirms. `metabase.json` in a
published dashboard's folder is Go live's own record. Never edit or delete it.

**Settings and secrets are the user's.** Never read `data/`. It holds the settings, the
saved password and key, the conversations and the logs.

**Stay in this folder.** Never read or edit anything outside it.

## Making a dashboard

1. Find the tables: `python studio.py tables <text>`, then
   `python studio.py describe <table>`. Both read a local snapshot, not the database.
2. Try each query: `python studio.py q "select ..."` or
   `python studio.py q -f dashboards/<slug>/<key>.sql`.
3. Write `dashboards/<slug>/dashboard.json` and one `<key>.sql` per card.
4. `python studio.py check <slug>` must report no problems.
5. The open app picks the change up by itself. Tell the user which dashboard to look
   at.

For a new dashboard, show the plan first (the cards, and each table with its size and
the filter the queries will use) and build after the user agrees. The app enforces
this: its planning run has no query tool.

To change a dashboard, edit its files. Only cards whose SQL changed are queried again;
a layout or title change costs no query.

`python studio.py info` prints the time zone, the schemas and the limits in force.

## Files of a dashboard

`dashboards/<slug>/dashboard.json`, slug in a-z, 0-9, `-`, `_`:

```json
{
  "name": "Daily orders",
  "description": "Orders and revenue, today and the last 30 days.",
  "cards": [
    { "key": "kpis", "display": "heading", "text": "Today", "row": 0, "col": 0, "size_x": 24, "size_y": 1 },
    { "key": "orders_today", "name": "Orders today", "display": "scalar",
      "row": 1, "col": 0, "size_x": 6, "size_y": 3 },
    { "key": "daily_orders", "name": "Orders per day", "display": "line",
      "viz": { "graph.dimensions": ["Day"], "graph.metrics": ["Orders"] },
      "row": 4, "col": 0, "size_x": 12, "size_y": 6 }
  ]
}
```

- `key`: a-z, 0-9, `_`. The card's query is the file `<key>.sql` beside it. Go live
  recognises a card by its key: keep the key when a card changes; a new key means a new
  card in Metabase and the old one in its trash.
- `display`: `scalar`, `smartscalar`, `line`, `bar`, `area`, `combo`, `row`, `pie`,
  `table`. The text cards `heading` and `text` take `text` and have no query.
- `row`, `col`, `size_x`, `size_y`: Metabase's grid, 24 columns wide. Cards must not
  overlap. Sizes that work: KPI 6 x 3, chart 12 x 6, three across 8 x 6, heading 24 x 1.
- `viz`: Metabase `visualization_settings`. Go live passes it to Metabase unchanged.

`viz` keys the preview draws:

| Key | Use |
|---|---|
| `graph.dimensions` | x column; a second entry splits the metric into one series per value |
| `graph.metrics` | y columns |
| `stackable.stack_type` | `stacked` or `normalized` |
| `graph.show_values` | value labels on the marks |
| `graph.x_axis.title_text`, `graph.y_axis.title_text` | axis titles |
| `series_settings` | per series: `display` (`line`, `bar`, `area`), `title`, `color` |
| `pie.dimension`, `pie.metric`, `pie.slice_threshold` | pie |
| `scalar.field`, `scalar.switch_positive_negative` | which number; whether down is good |
| `column_settings` | per column `["name","<column>"]`: `number_style` (`currency`, `percent`), `currency`, `decimals`, `prefix`, `suffix`, `scale` |

Other keys are kept for Metabase but the preview ignores them.

`examples/sample` shows every card type with generated numbers.

## Filters

A dashboard may have filters: a date range, or a choice such as a status or a region.
They work in the preview and become native Metabase filters at Go live.

In `dashboard.json`, beside `cards`:

```json
"filters": [
  { "key": "date", "name": "Date", "type": "date", "default": "past30days" },
  { "key": "status", "name": "Status", "type": "text", "values": "status_list" }
]
```

- `type`: `date`, `text` or `number`.
- `default` for a date: `thisday`, `past1days`, `past7days`, `past30days`, `past90days`,
  `thismonth`, `past1months`, `thisyear`, or a range `2026-01-01~2026-01-31`. Leave it
  out for "all time".
- `values`: the name of a `.sql` file in the folder (here `status_list.sql`) whose first
  column lists the choices. Keep it cheap: a small table, or `select distinct` on an
  indexed column with a limit. Without it the user types the value.

A card takes part by using the filter's key in its query and saying which column it is:

```sql
select (created_at at time zone '<zone>')::date as "Day", count(*) as "Orders"
from orders
where {{date}} [[and {{status}}]]
group by 1 order by 1
```

```json
{ "key": "daily_orders", "name": "Orders per day", "display": "line",
  "filters": { "date": "orders.created_at", "status": "orders.status" }, ... }
```

- `{{key}}` stands for a whole condition on that column. Never write
  `created_at >= {{date}}`.
- Put a filter that may be empty inside `[[ ... ]]` with its `and`; the part is left out
  when nothing is picked. A `{{key}}` outside brackets becomes `TRUE` when empty.
- The column's table must appear in the query under its own name, with no alias:
  Metabase writes the condition as `"schema"."table"."column"`. `from orders o` breaks it.
- The column must be a real column of that table, as `describe` shows it: `table.column`,
  or `schema.table.column` outside `public`.
- A query with `{{...}}` can only be tested by file, after `dashboard.json` names the
  filters and the card. It runs with the filters' defaults.
- Add filters only when the user asks for them.

## SQL

- One SELECT per file.
- Name output columns the way they should read on the chart: `count(*) as "Orders"`.
  `viz` refers to these names. Column names must be unique.
- Days, "today" and months are counted in the user's time zone. Inside the app the
  request names it; in a terminal, `python studio.py info` prints it. Convert a
  timestamp before grouping by day: `(created_at at time zone '<zone>')::date as "Day"`.
- Start of today in that zone, comparable with a raw indexed `timestamptz` column:
  `date_trunc('day', now() at time zone '<zone>') at time zone '<zone>'`.
- Order the rows. A time series is oldest first.
- `smartscalar` compares the last row with the one before it: return one row per
  period, oldest first.

## Choosing a card

- One number: `scalar`. A number against the previous period: `smartscalar`.
- Change over time: `line`, or `bar` for a few periods. Ranking: `row`. Share of a
  whole with at most 6 slices: `pie`. Detail: `table`.
- No dual axes. Two measures of different scale go in two cards.
- At most 4 series in one chart; fold the rest into "Other" in the query.
- `combo`: give every series a `display` in `series_settings`.

## Code

Comments are short and concrete, and only where the code cannot say it.

`python -m unittest discover -s tests -t tests` runs the tests. They use a temporary
folder, a canned database answer and stand-ins for Claude and Metabase, so they touch
neither a database nor a Claude login. Keep it that way.

The app's Claude runs must stay as limited as they are: no shell, writes only inside
the one dashboard folder, the machine's own Claude Code login, and none of the saved
secrets in its environment.

Nothing personal belongs in git: `data/` and `dashboards/` are ignored on purpose.
