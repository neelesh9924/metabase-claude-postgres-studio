(() => {
  "use strict";

  // Metabase's default series colours, in its order, so the preview matches what gets published.
  const SERIES = ["#509EE3", "#88BF4D", "#A989C5", "#EF8C8C", "#F9D45C", "#F2A86F", "#98D9D9", "#7172AD"];
  const OTHER = "#949AAB";
  // The charts' inks follow the theme, so they are read from the stylesheet.
  let INK, INK_SOFT, GRIDLINE, AXIS, SURFACE;
  function readTheme() {
    const sheet = getComputedStyle(document.documentElement);
    [INK, INK_SOFT, GRIDLINE, AXIS, SURFACE] = ["--text", "--text-3", "--line", "--line-2", "--surface"].map((name) => sheet.getPropertyValue(name).trim());
  }
  readTheme();
  const FONT = '"Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, sans-serif';
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const TABLE_ROWS = 200;
  const POLL_MS = 2000;
  const GRID_COLUMNS = 24;
  const GAP = 12;
  const MIN_ROW = 40;
  const ROW_RATIO = 0.9;

  const $ = (id) => document.getElementById(id);
  const state = {
    slug: null, spec: null, cards: {}, view: {}, gen: 0, chain: Promise.resolve(), offline: false,
    page: "dashboards", setup: null, picked: {}, listsAsked: new Set(),
    mode: "edit", assist: { rev: -1, key: null, job: null, messages: [], followed: undefined, flash: "", again: false },
  };
  const elements = new Map();
  const charts = new Map();

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (name === "class") el.className = value;
      else if (name.startsWith("on")) el.addEventListener(name.slice(2), value);
      else el.setAttribute(name, value === true ? "" : value);
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  const SVG = "http://www.w3.org/2000/svg";
  // Line icons on a 24 x 24 grid, drawn with stroke only.
  const ICONS = {
    plus: ["M12 5v14", "M5 12h14"],
    refresh: ["M20 11a8 8 0 1 0-2.3 6.3", "M20 4v7h-7"],
    external: ["M14 5h5v5", "M19 5l-8 8", "M17 14v4a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1h4"],
    upload: ["M12 16V5", "M7 10l5-5 5 5", "M5 19h14"],
    sparkle: ["M11 3l1.9 5.1L18 10l-5.1 1.9L11 17l-1.9-5.1L4 10l5.1-1.9z", "M18.5 15l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"],
    table: ["M4 5h16v14H4z", "M4 10h16", "M4 14.5h16", "M10 5v14"],
    code: ["M9 8l-4 4 4 4", "M15 8l4 4-4 4"],
    chart: ["M5 20V11", "M12 20V4", "M19 20v-6"],
    stop: ["M7 7h10v10H7z"],
    send: ["M12 19V5", "M6 11l6-6 6 6"],
    trash: ["M4 7h16", "M9 7V4h6v3", "M6.5 7l1 13h9l1-13"],
    panel: ["M4 5h16v14H4z", "M15 5v14"],
    lock: ["M6 11h12v9H6z", "M9 11V8a3 3 0 0 1 6 0v3"],
    alert: ["M12 4l9 16H3z", "M12 10v4", "M12 17.2v.3"],
    check: ["M5 12.5l4.5 4.5L19 7.5"],
    settings: ["M4 7h9", "M19 7h1", "M4 17h3", "M13 17h7", "M16 5a2 2 0 1 0 0 4 2 2 0 0 0 0-4z", "M10 15a2 2 0 1 0 0 4 2 2 0 0 0 0-4z"],
    moon: ["M20 14.5A8 8 0 0 1 9.5 4a7 7 0 1 0 10.5 10.5z"],
    sun: ["M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z", "M12 3v2", "M12 19v2", "M3 12h2", "M19 12h2", "M5.6 5.6L7 7", "M17 17l1.4 1.4", "M5.6 18.4L7 17", "M17 7l1.4-1.4"],
  };

  function icon(name, size = 16) {
    const svg = document.createElementNS(SVG, "svg");
    const attrs = { viewBox: "0 0 24 24", width: size, height: size, fill: "none", stroke: "currentColor", "stroke-width": 1.9,
      "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", class: "icon" };
    for (const [key, value] of Object.entries(attrs)) svg.setAttribute(key, value);
    for (const d of ICONS[name] || []) {
      const shape = document.createElementNS(SVG, "path");
      shape.setAttribute("d", d);
      svg.append(shape);
    }
    return svg;
  }

  async function api(path, body) {
    const options = body
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
      : undefined;
    return (await fetch(path, options)).json();
  }

  // ---------- formatting ----------

  function parseDate(text) {
    const m = /^(\d{4})-(\d\d)-(\d\d)(?:[T ](\d\d):(\d\d))?/.exec(String(text));
    return m ? new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0)) : null;
  }

  function clock(d) {
    const hours = d.getHours() % 12 || 12;
    return `${hours}:${String(d.getMinutes()).padStart(2, "0")} ${d.getHours() < 12 ? "AM" : "PM"}`;
  }

  function fmtDate(text, withTime) {
    const d = parseDate(text);
    if (!d) return String(text);
    const day = `${MONTHS[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
    return withTime && (d.getHours() || d.getMinutes()) ? `${day}, ${clock(d)}` : day;
  }

  function columnSettings(card, name) {
    return (card.viz.column_settings || {})[JSON.stringify(["name", name])] || {};
  }

  function fmtNumber(value, settings, compact) {
    if (value === null || value === undefined || Number.isNaN(value)) return "";
    const s = settings || {};
    let n = value * (s.scale ?? 1);
    const options = {};
    if (s.number_style === "percent") {
      options.style = "percent";
    } else if (s.number_style === "currency") {
      options.style = "currency";
      options.currency = s.currency || "INR";
    }
    if (compact && Math.abs(n) >= 1000) {
      options.notation = "compact";
      options.maximumFractionDigits = 1;
    } else if (s.decimals !== undefined && s.decimals !== null) {
      options.minimumFractionDigits = options.maximumFractionDigits = s.decimals;
    } else if (s.number_style === "currency") {
      options.minimumFractionDigits = options.maximumFractionDigits = 2;
    } else {
      options.maximumFractionDigits = 2;
    }
    let text;
    try {
      text = new Intl.NumberFormat("en-US", options).format(n);
    } catch {
      text = String(n);
    }
    return (s.prefix || "") + text + (s.suffix || "");
  }

  function fmtCell(value, column, card) {
    if (value === null || value === undefined) return "";
    if (column.type === "number" && typeof value === "number") return fmtNumber(value, columnSettings(card, column.name));
    if (column.type === "date") return fmtDate(value, false);
    if (column.type === "datetime") return fmtDate(value, true);
    return String(value);
  }

  function axisLabels(values, column) {
    const plain = values.map((v) => (v === null || v === undefined || v === "" ? "(empty)" : String(v)));
    if (column.type !== "date" && column.type !== "datetime") return plain;
    const dates = values.map((v) => parseDate(v));
    if (dates.some((d) => !d)) return plain;
    const oneYear = new Set(dates.map((d) => d.getFullYear())).size === 1;
    const hasTime = dates.some((d) => d.getHours() || d.getMinutes());
    const oneDay = new Set(dates.map((d) => d.toDateString())).size === 1;
    const monthly = dates.length > 1 && !hasTime && dates.every((d) => d.getDate() === 1);
    return dates.map((d) => {
      if (monthly) return `${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
      if (hasTime && oneDay) return clock(d);
      let label = `${MONTHS[d.getMonth()]} ${d.getDate()}`;
      if (!oneYear) label += `, ${d.getFullYear()}`;
      if (hasTime) label += ` ${clock(d)}`;
      return label;
    });
  }

  function fmtDuration(ms) {
    return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
  }

  function fmtRanAt(text) {
    const d = parseDate(text);
    if (!d) return "";
    const time = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    return d.toDateString() === new Date().toDateString() ? time : `${d.getDate()} ${MONTHS[d.getMonth()]}, ${time}`;
  }

  function metaText(result) {
    const rows = `${result.row_count.toLocaleString("en-US")}${result.truncated ? "+" : ""} ${result.row_count === 1 ? "row" : "rows"}`;
    return `${rows} · ${fmtDuration(result.ms)} · as of ${fmtRanAt(result.ran_at)}`;
  }

  // ---------- charts ----------

  function columnIndex(result, name) {
    return result.columns.findIndex((c) => c.name === name);
  }

  function firstNumber(result, skip) {
    return result.columns.findIndex((c, i) => c.type === "number" && i !== skip);
  }

  function tooltipStyle() {
    return {
      confine: true,
      backgroundColor: SURFACE,
      borderColor: AXIS,
      borderWidth: 1,
      padding: [8, 10],
      textStyle: { color: INK, fontFamily: FONT, fontSize: 12 },
    };
  }

  function mount(key, box, option) {
    const chart = echarts.init(box);
    chart.setOption(option);
    charts.set(key, chart);
  }

  function cartesianOption(card, result) {
    const viz = card.viz;
    const kind = card.display;
    const columns = result.columns;
    let dims = (viz["graph.dimensions"] || []).filter((n) => columnIndex(result, n) >= 0);
    let metrics = (viz["graph.metrics"] || []).filter((n) => columnIndex(result, n) >= 0);
    if (!dims.length) {
      const i = columns.findIndex((c) => c.type !== "number");
      dims = [columns[i >= 0 ? i : 0].name];
    }
    if (!metrics.length) {
      metrics = columns.filter((c) => c.type === "number" && !dims.includes(c.name)).map((c) => c.name);
    }
    if (!metrics.length) return null;

    const xi = columnIndex(result, dims[0]);
    let xs;
    let series;
    if (dims.length > 1) {
      const bi = columnIndex(result, dims[1]);
      const mi = columnIndex(result, metrics[0]);
      const groups = [];
      const lookup = new Map();
      xs = [];
      for (const row of result.rows) {
        const x = row[xi];
        const group = row[bi] === null ? "(empty)" : String(row[bi]);
        if (!xs.includes(x)) xs.push(x);
        if (!groups.includes(group)) groups.push(group);
        lookup.set(JSON.stringify([x, group]), row[mi]);
      }
      series = groups.map((group) => ({
        id: group,
        column: metrics[0],
        data: xs.map((x) => lookup.get(JSON.stringify([x, group])) ?? null),
      }));
    } else {
      xs = result.rows.map((row) => row[xi]);
      series = metrics.map((metric) => {
        const mi = columnIndex(result, metric);
        return { id: metric, column: metric, data: result.rows.map((row) => row[mi]) };
      });
    }

    const stackType = viz["stackable.stack_type"];
    const stacked = stackType === "stacked" || stackType === "normalized";
    if (stackType === "normalized") {
      const totals = xs.map((_, i) => series.reduce((sum, s) => sum + (Number(s.data[i]) || 0), 0));
      for (const s of series) s.data = s.data.map((v, i) => (totals[i] ? (Number(v) || 0) / totals[i] : 0));
    }

    const horizontal = kind === "row";
    const showValues = !!viz["graph.show_values"];
    const labels = axisLabels(xs, columns[xi]);
    const showDots = xs.length <= 31;
    const built = series.map((s, index) => {
      const own = (viz.series_settings || {})[s.id] || {};
      const settings = stackType === "normalized" ? { number_style: "percent" } : columnSettings(card, s.column);
      let type = kind === "bar" || kind === "row" ? "bar" : "line";
      let area = kind === "area";
      if (kind === "combo") {
        const shown = own.display || (index === 0 ? "bar" : "line");
        type = shown === "bar" ? "bar" : "line";
        area = shown === "area";
      }
      const out = {
        name: own.title || s.id,
        type,
        data: s.data,
        color: own.color || SERIES[index % SERIES.length],
        emphasis: { focus: series.length > 1 ? "series" : "none" },
        tooltip: { valueFormatter: (v) => (v === null || v === undefined ? "–" : fmtNumber(v, settings)) },
        label: {
          show: showValues,
          position: horizontal ? "right" : "top",
          color: INK_SOFT,
          fontSize: 11,
          formatter: (p) => fmtNumber(p.value, settings, true),
        },
      };
      if (type === "bar") {
        out.barMaxWidth = 40;
        if (stacked) {
          out.stack = "all";
          out.itemStyle = { borderColor: SURFACE, borderWidth: 1 };
        } else {
          out.itemStyle = { borderRadius: horizontal ? [0, 3, 3, 0] : [3, 3, 0, 0] };
        }
      } else {
        out.lineStyle = { width: 2 };
        out.symbol = "circle";
        out.symbolSize = 7;
        out.showSymbol = showDots;
        if (area) out.areaStyle = { opacity: 0.18 };
        if (area && stacked) out.stack = "all";
      }
      return out;
    });

    const valueSettings = stackType === "normalized" ? { number_style: "percent" } : columnSettings(card, series[0].column);
    const categoryAxis = {
      type: "category",
      data: labels,
      inverse: horizontal,
      axisLine: { lineStyle: { color: AXIS } },
      axisTick: { show: false },
      axisLabel: { color: INK_SOFT, fontSize: 11, hideOverlap: true },
      name: (horizontal ? viz["graph.y_axis.title_text"] : viz["graph.x_axis.title_text"]) || "",
      nameLocation: "middle",
      nameGap: horizontal ? 80 : 28,
      nameTextStyle: { color: INK_SOFT, fontSize: 12, fontWeight: 700 },
    };
    const valueAxis = {
      type: "value",
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: GRIDLINE } },
      axisLabel: { color: INK_SOFT, fontSize: 11, formatter: (v) => fmtNumber(v, valueSettings, true) },
      name: (horizontal ? viz["graph.x_axis.title_text"] : viz["graph.y_axis.title_text"]) || "",
      nameLocation: "middle",
      nameGap: horizontal ? 28 : 44,
      nameTextStyle: { color: INK_SOFT, fontSize: 12, fontWeight: 700 },
    };
    const many = built.length > 1;
    return {
      animation: false,
      textStyle: { fontFamily: FONT },
      grid: {
        left: valueAxis.name && !horizontal ? 24 : 6,
        right: 18,
        top: many ? 34 : 14,
        bottom: (horizontal ? valueAxis.name : categoryAxis.name) ? 24 : 4,
        containLabel: true,
      },
      legend: many
        ? { type: "scroll", top: 0, left: 0, itemWidth: 12, itemHeight: 12, icon: "circle", textStyle: { color: INK, fontSize: 12 } }
        : undefined,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: built.every((s) => s.type === "bar") ? "shadow" : "line", lineStyle: { color: AXIS } },
        ...tooltipStyle(),
      },
      xAxis: horizontal ? valueAxis : categoryAxis,
      yAxis: horizontal ? categoryAxis : valueAxis,
      series: built,
    };
  }

  function pieOption(card, result, width) {
    const viz = card.viz;
    let di = columnIndex(result, viz["pie.dimension"]);
    if (di < 0) di = result.columns.findIndex((c) => c.type !== "number");
    if (di < 0) di = 0;
    let mi = columnIndex(result, viz["pie.metric"]);
    if (mi < 0) mi = firstNumber(result, di);
    if (mi < 0) return null;
    const settings = columnSettings(card, result.columns[mi].name);
    const names = axisLabels(result.rows.map((r) => r[di]), result.columns[di]);
    const items = result.rows.map((r, i) => ({ name: names[i], value: Number(r[mi]) || 0 })).filter((d) => d.value > 0);
    const total = items.reduce((sum, d) => sum + d.value, 0);
    if (!total) return null;
    const threshold = (viz["pie.slice_threshold"] ?? 2.5) / 100;
    const small = items.filter((d) => d.value / total < threshold);
    let slices = items;
    if (small.length > 1) {
      slices = items.filter((d) => d.value / total >= threshold);
      slices.push({ name: "Other", value: small.reduce((sum, d) => sum + d.value, 0), itemStyle: { color: OTHER } });
    }
    const share = new Map(slices.map((d) => [d.name, d.value / total]));
    const wide = width >= 340;
    const centre = wide ? "32%" : "50%";
    return {
      animation: false,
      color: SERIES,
      textStyle: { fontFamily: FONT },
      title: {
        text: fmtNumber(total, settings, Math.abs(total) >= 1e6),
        subtext: "Total",
        left: centre,
        top: "middle",
        textAlign: "center",
        itemGap: 2,
        textStyle: { color: INK, fontSize: 18, fontWeight: 700 },
        subtextStyle: { color: INK_SOFT, fontSize: 11 },
      },
      legend: wide
        ? {
            type: "scroll",
            orient: "vertical",
            left: "62%",
            top: "middle",
            itemWidth: 12,
            itemHeight: 12,
            icon: "circle",
            textStyle: { color: INK, fontSize: 12 },
            formatter: (name) => `${name}   ${(100 * (share.get(name) || 0)).toFixed(1)}%`,
          }
        : undefined,
      tooltip: { trigger: "item", valueFormatter: (v) => fmtNumber(v, settings), ...tooltipStyle() },
      series: [
        {
          type: "pie",
          radius: ["52%", "80%"],
          center: [centre, "50%"],
          data: slices,
          label: { show: false },
          itemStyle: { borderColor: SURFACE, borderWidth: 2 },
        },
      ],
    };
  }

  function scalarView(card, result) {
    let mi = columnIndex(result, card.viz["scalar.field"]);
    if (mi < 0) mi = firstNumber(result, -1);
    if (mi < 0) mi = 0;
    const column = result.columns[mi];
    const row = result.rows[result.rows.length - 1];
    const box = h("div", { class: "scalar" }, h("div", { class: "scalar-value" }, fmtCell(row[mi], column, card)));
    if (card.display === "scalar") {
      box.append(h("div", { class: "scalar-title" }, card.name));
      return box;
    }
    const di = result.columns.findIndex((c, i) => i !== mi && c.type !== "number");
    if (di >= 0) box.append(h("div", { class: "scalar-period" }, fmtCell(row[di], result.columns[di], card)));
    const before = result.rows.length > 1 ? result.rows[result.rows.length - 2][mi] : null;
    const now = row[mi];
    if (typeof before === "number" && typeof now === "number") {
      let change;
      if (before === 0) {
        change = h("b", null, "no earlier value to compare");
      } else {
        const ratio = (now - before) / Math.abs(before);
        const good = (ratio >= 0) !== !!card.viz["scalar.switch_positive_negative"];
        const arrow = ratio > 0 ? "↑" : ratio < 0 ? "↓" : "→";
        change = h("b", { class: ratio === 0 ? "" : good ? "up" : "down" }, `${arrow} ${(Math.abs(ratio) * 100).toFixed(1)}%`);
      }
      box.append(h("div", { class: "delta" }, change, ` vs previous: ${fmtCell(before, column, card)}`));
    }
    return box;
  }

  function tableView(card, result) {
    const head = h("tr", null, result.columns.map((c) => h("th", { class: c.type === "number" ? "num" : "" }, c.name)));
    const body = result.rows.slice(0, TABLE_ROWS).map((row) =>
      h("tr", null, row.map((v, i) => h("td", { class: result.columns[i].type === "number" ? "num" : "" }, fmtCell(v, result.columns[i], card))))
    );
    const wrap = h("div", { class: "table-wrap" }, h("table", null, h("thead", null, head), h("tbody", null, body)));
    if (result.rows.length > TABLE_ROWS || result.truncated) {
      const total = `${result.row_count.toLocaleString("en-US")}${result.truncated ? "+" : ""}`;
      wrap.append(h("div", { class: "table-note" }, `Showing the first ${Math.min(TABLE_ROWS, result.rows.length)} of ${total} rows.`));
    }
    return wrap;
  }

  // ---------- cards ----------

  function toolButton(name, label, pressed, title, onclick) {
    const attrs = { class: "tool", type: "button", "aria-label": label, "aria-pressed": pressed === null ? null : String(pressed), title, onclick };
    return h("button", attrs, icon(name, 15));
  }

  function setView(key, view) {
    state.view[key] = state.view[key] === view ? "chart" : view;
    renderCard(key);
  }

  function renderCard(key) {
    const card = state.spec && state.spec.cards.find((c) => c.key === key);
    const el = elements.get(key);
    if (!card || !el) return;
    const old = charts.get(key);
    if (old) {
      old.dispose();
      charts.delete(key);
    }
    el.replaceChildren();
    el.className = `card d-${card.display}`;
    if (card.display === "heading") return void el.append(h("h2", { class: "heading" }, card.text));
    if (card.display === "text") return void el.append(h("div", { class: "text" }, card.text));

    const entry = state.cards[key] || {};
    const waiting = entry.loading || entry.queued;
    const result = entry.result || (waiting ? entry.stale : null);
    const view = state.view[key] || "chart";
    const isScalar = card.display === "scalar";
    const hidden = result ? result.columns.filter((c) => c.pii).map((c) => c.name) : [];

    if (!isScalar || view !== "chart" || !result) {
      el.append(
        h(
          "header",
          { class: "card-head" },
          h("h2", { class: "card-title", title: card.name }, card.name),
          hidden.length ? h("span", { class: "flag", title: `Hidden: ${hidden.join(", ")}. Leave these columns out.` }, "Personal data hidden") : null,
          result ? h("span", { class: "card-meta" }, metaText(result)) : null
        )
      );
    }
    el.append(
      h(
        "div",
        { class: "card-tools" },
        card.display === "table" ? null : toolButton("table", "Table", view === "table", "Show the rows behind this card", () => setView(key, "table")),
        toolButton("code", "SQL", view === "sql", "Show the query", () => setView(key, "sql")),
        toolButton("refresh", "Refresh", null, result ? `Run again. Last run: ${metaText(result)}` : "Run the query", () => enqueue(key, true, false))
      )
    );

    const body = h("div", { class: "card-body" });
    el.append(body);
    if (view === "sql") return void body.append(h("pre", { class: "sql" }, card.sql || "The SQL file is missing."));
    if (entry.error && !waiting) {
      return void body.append(h("div", { class: "problem" }, h("strong", null, "The query failed"), h("pre", null, entry.error)));
    }
    if (entry.heavy && !waiting) {
      const cost = Math.round(entry.heavy.cost).toLocaleString("en-US");
      const limit = Math.round(entry.heavy.limit).toLocaleString("en-US");
      return void body.append(
        h(
          "div",
          { class: "problem" },
          h("strong", null, "Not run: this query looks heavy"),
          h("span", null, `Estimated cost ${cost}, limit ${limit}.`),
          h("button", { class: "btn small", type: "button", onclick: () => enqueue(key, true, true) }, "Run anyway")
        )
      );
    }
    if (!result) {
      const idle = card.sql ? "No data yet." : `The file ${card.key}.sql is missing or not allowed.`;
      const text = entry.loading ? "Running the query…" : entry.queued ? "Waiting for its turn…" : idle;
      return void body.append(h("div", { class: "placeholder" }, text));
    }
    if (waiting) body.classList.add("dim");
    if (!result.rows.length) return void body.append(h("div", { class: "placeholder" }, "No results."));
    if (view === "table" || card.display === "table") return void body.append(tableView(card, result));
    if (isScalar || card.display === "smartscalar") return void body.append(scalarView(card, result));

    const box = h("div", { class: "chart" });
    body.append(box);
    const option = card.display === "pie" ? pieOption(card, result, box.clientWidth) : cartesianOption(card, result);
    if (!option) {
      box.remove();
      return void body.append(h("div", { class: "placeholder" }, "This chart needs a number column."));
    }
    mount(key, box, option);
  }

  function enqueue(key, force, allowHeavy) {
    const entry = state.cards[key];
    if (!entry) return;
    const gen = state.gen;
    const slug = state.slug;
    entry.queued = true;
    renderCard(key);
    state.chain = state.chain.then(async () => {
      const now = state.cards[key];
      if (state.gen !== gen || state.slug !== slug || !now) return;
      now.queued = false;
      if (!force && (now.result || now.error || now.heavy)) return void renderCard(key);
      now.loading = true;
      renderCard(key);
      let reply;
      try {
        reply = await api("/api/run", { slug, key, allow_heavy: !!allowHeavy, values: state.picked[slug] });
      } catch {
        reply = { error: "The studio server did not answer." };
      }
      const target = state.slug === slug ? state.cards[key] : null;
      if (!target) return;
      target.loading = false;
      if (reply.result && reply.sql_hash === target.hash) {
        Object.assign(target, { result: reply.result, error: null, heavy: null, stale: null });
      } else if (reply.heavy) {
        Object.assign(target, { heavy: reply.heavy, error: null });
      } else if (!reply.result) {
        Object.assign(target, { error: reply.error || "Unknown error.", heavy: null });
      }
      renderCard(key);
    });
  }

  // ---------- page ----------

  function sizeGrid() {
    const grid = $("grid");
    const cell = (grid.clientWidth - GAP * (GRID_COLUMNS - 1)) / GRID_COLUMNS;
    grid.style.setProperty("--row", `${Math.max(MIN_ROW, Math.floor(cell * ROW_RATIO + GAP * ROW_RATIO))}px`);
  }

  function renderNotice(title, items) {
    const notice = $("notice");
    notice.hidden = !items.length && !title;
    notice.replaceChildren();
    if (notice.hidden) return;
    const list = items.length ? h("ul", null, items.map((p) => h("li", null, p))) : null;
    notice.append(icon("alert", 17), h("div", null, h("strong", null, title), list));
  }

  // Where a dashboard stands: only here, live in Metabase, or live but no longer matching.
  function statusOf(d) {
    if (!d.live) return { text: "Draft", kind: "" };
    if (d.metabase === "trashed") return { text: "In Metabase’s trash", kind: "bad" };
    if (d.metabase === "gone") return { text: "Deleted in Metabase", kind: "bad" };
    if (d.metabase === "moved") return { text: "Moved in Metabase", kind: "warn" };
    if (d.changed) return { text: "Changes not live", kind: "warn" };
    return { text: "Live", kind: "ok" };
  }

  function renderTop() {
    const spec = state.spec;
    const status = statusOf(spec);
    $("status").replaceChildren(h("span", { class: `chip ${status.kind}` }, h("i", { class: "dot" }), status.text));
    $("openLive").hidden = !spec.live || spec.metabase === "trashed" || spec.metabase === "gone";
    $("goLive").disabled = false;
  }

  function renderDashboard() {
    for (const chart of charts.values()) chart.dispose();
    charts.clear();
    elements.clear();
    const spec = state.spec;
    $("top").hidden = false;
    $("empty").hidden = true;
    $("title").textContent = spec.name;
    $("title").title = spec.name;
    $("desc").textContent = spec.description;
    $("desc").hidden = !spec.description;
    renderTop();
    document.title = `${spec.name} · Metabase Claude Studio`;
    renderNotice(spec.problems.length ? "This dashboard's files need fixing" : "", spec.problems);
    renderFilters();
    const grid = $("grid");
    grid.replaceChildren();
    sizeGrid();
    for (const card of spec.cards) {
      const el = h("section", { class: "card" });
      el.style.gridColumn = `${card.col + 1} / span ${card.size_x}`;
      el.style.gridRow = `${card.row + 1} / span ${card.size_y}`;
      elements.set(card.key, el);
      grid.append(el);
    }
    for (const card of spec.cards) renderCard(card.key);
  }

  // ---------- filters ----------

  const DATE_CHOICES = [
    ["", "All time"], ["thisday", "Today"], ["past1days", "Yesterday"], ["past7days", "Previous 7 days"],
    ["past30days", "Previous 30 days"], ["past90days", "Previous 90 days"], ["thismonth", "This month"],
    ["past1months", "Previous month"], ["thisyear", "This year"],
  ];

  // A changed filter reloads the dashboard for the new values; only the cards that use it run again.
  function pick(key, value) {
    const slug = state.slug;
    state.picked[slug] = { ...state.picked[slug], [key]: value };
    loadDashboard(slug);
  }

  function dateControl(def, value) {
    const current = value || "";
    const listed = DATE_CHOICES.some(([v]) => v === current);
    const range = /^(\d{4}-\d\d-\d\d)?~(\d{4}-\d\d-\d\d)?$/.exec(current) || [];
    const choices = [...DATE_CHOICES, ["custom", "Custom range…"]];
    const select = h("select", { class: "input", "aria-label": def.name },
      choices.map(([v, label]) => h("option", { value: v, selected: listed ? v === current : v === "custom" }, label)));
    const from = h("input", { class: "input", type: "date", value: range[1] || "", "aria-label": `${def.name} from` });
    const to = h("input", { class: "input", type: "date", value: range[2] || "", "aria-label": `${def.name} to` });
    const custom = h("span", { class: "filter-range", hidden: listed }, from, "to", to);
    const apply = () => {
      if (from.value || to.value) pick(def.key, `${from.value}~${to.value}`);
    };
    select.addEventListener("change", () => {
      if (select.value !== "custom") return pick(def.key, select.value);
      custom.hidden = false;
      from.focus();
    });
    from.addEventListener("change", apply);
    to.addEventListener("change", apply);
    return [select, custom];
  }

  function choiceControl(def, value) {
    const list = (state.spec.options || {})[def.key];
    if (Array.isArray(list)) {
      const select = h("select", { class: "input", "aria-label": def.name },
        h("option", { value: "" }, "All"),
        list.map((item) => h("option", { value: String(item), selected: String(item) === String(value ?? "") }, String(item))));
      select.addEventListener("change", () => pick(def.key, select.value));
      return [select];
    }
    const input = h("input", { class: "input", value: value ?? "", "aria-label": def.name, placeholder: def.values ? "Loading the list…" : "Any",
      inputmode: def.type === "number" ? "decimal" : null });
    input.addEventListener("change", () => pick(def.key, input.value.trim()));
    return [input];
  }

  function loadList(key) {
    const slug = state.slug;
    if (state.listsAsked.has(`${slug}/${key}`)) return;
    state.listsAsked.add(`${slug}/${key}`);
    state.chain = state.chain.then(async () => {
      const reply = await post("/api/options", { slug, key });
      if (!reply.options || state.slug !== slug || !state.spec) return;
      state.spec.options[key] = reply.options;
      renderFilters();
    });
  }

  function renderFilters() {
    const defs = state.spec.filters || [];
    const values = state.spec.values || {};
    $("filters").hidden = !defs.length;
    $("filters").replaceChildren(...defs.map((def) =>
      h("div", { class: "filter" }, h("span", { class: "filter-name" }, def.name),
        h("div", { class: "filter-controls" }, def.type === "date" ? dateControl(def, values[def.key]) : choiceControl(def, values[def.key])))));
    for (const def of defs) if (def.values && !(state.spec.options || {})[def.key]) loadList(def.key);
  }

  function renderList(dashboards) {
    $("list").replaceChildren(
      ...dashboards.map((d) => {
        const status = statusOf(d);
        const count = `${d.cards} ${d.cards === 1 ? "card" : "cards"}`;
        const current = state.page === "dashboards" && d.slug === state.slug && state.mode === "edit";
        return h(
          "button",
          { class: "nav-item", type: "button", "aria-current": String(current), title: d.name, onclick: () => select(d.slug) },
          icon("chart", 17),
          h("span", { class: "nav-text" }, h("span", { class: "name" }, d.name), h("span", { class: "sub" }, `${count} · ${d.problems ? `${d.problems} to fix` : status.text}`)),
          h("i", { class: `dot ${status.kind}`, title: status.text })
        );
      })
    );
  }

  async function loadDashboard(slug) {
    const picked = state.picked[slug];
    const chosen = picked ? `&values=${encodeURIComponent(JSON.stringify(picked))}` : "";
    const spec = await api(`/api/dashboard?slug=${encodeURIComponent(slug)}${chosen}`);
    if (spec.error) return;
    state.picked[slug] = spec.values || {};
    const previous = state.slug === slug ? state.cards : {};
    if (state.slug !== slug) state.view = {};
    state.gen += 1;
    state.slug = slug;
    state.spec = spec;
    const cards = {};
    for (const card of spec.cards) {
      if (!card.sql) continue;
      const cached = spec.data[card.key];
      const before = previous[card.key];
      if (cached) {
        cards[card.key] = { hash: card.sql_hash, result: cached };
      } else if (before && before.hash === card.sql_hash) {
        cards[card.key] = Object.assign(before, { queued: false, loading: false });
      } else {
        cards[card.key] = { hash: card.sql_hash, stale: before ? before.result || before.stale : null };
      }
    }
    state.cards = cards;
    if (decodeURIComponent(location.hash.slice(1)) !== slug) history.replaceState(null, "", `#${encodeURIComponent(slug)}`);
    renderDashboard();
    checkLive();
    for (const card of spec.cards) {
      const entry = cards[card.key];
      if (entry && !entry.result && !entry.error && !entry.heavy) enqueue(card.key, false, false);
    }
  }

  function select(slug) {
    if (state.page !== "dashboards") showPage("dashboards");
    state.mode = "edit";
    history.replaceState(null, "", `#${encodeURIComponent(slug)}`);
    poll();
  }

  // ---------- Ask Claude ----------

  const NEW = "_new";
  const DISPLAY_WORDS = {
    scalar: "number", smartscalar: "number with trend", line: "line chart", bar: "bar chart", area: "area chart",
    combo: "bars and line", row: "ranking", pie: "pie", table: "table",
  };
  const WORKING = { plan: "Claude is planning", build: "Claude is building", edit: "Claude is working" };

  function assistKey() {
    return state.mode === "new" ? NEW : state.slug;
  }

  function busy() {
    const job = state.assist.job;
    return !!job && job.status === "running";
  }

  async function act(path, body) {
    let reply;
    try {
      reply = await api(path, body);
    } catch {
      reply = { error: "The studio did not answer." };
    }
    state.assist.flash = reply.error || "";
    await poll();
    return reply;
  }

  function planEl(message, latest) {
    const plan = message.plan;
    const section = (title, items, render) =>
      items && items.length ? h("div", null, h("h3", null, title), h("ul", null, items.map(render))) : null;
    const note = message.built
      ? "Built."
      : latest
        ? "Nothing has been read yet. To change the plan, type below."
        : "Replaced by a later request.";
    return h(
      "div",
      { class: "plan-card" },
      h("div", null, h("div", { class: "plan-name" }, plan.name), plan.description ? h("div", { class: "plan-desc" }, plan.description) : null),
      section("Cards", plan.cards, (c) =>
        h("li", null, c.name, h("span", null, ` · ${DISPLAY_WORDS[c.display] || c.display}${c.shows ? ` · ${c.shows}` : ""}`))),
      section("Will read", plan.reads, (r) =>
        h("li", null, r.table, h("span", null, `${r.size ? ` (${r.size})` : ""}${r.filter ? ` · ${r.filter}` : ""}`))),
      section("Assumed", plan.assumptions, (text) => h("li", null, text)),
      section("Left out", plan.left_out, (text) => h("li", null, text)),
      h(
        "div",
        { class: "plan-foot" },
        h("span", { class: "assist-hint" }, note),
        latest && !message.built && !busy()
          ? h("button", { class: "btn primary small", type: "button", onclick: () => act("/api/build", { plan: message.id }) }, icon("check", 14), "Build this dashboard")
          : null
      )
    );
  }

  function messageEl(message, latestPlan) {
    if (message.role === "user") return h("div", { class: "msg user" }, h("div", { class: "bubble" }, message.text));
    if (message.role === "info") return h("div", { class: "msg info" }, message.text);
    const meta = message.meta ? h("div", { class: "msg-meta" }, message.meta) : null;
    if (message.role === "error") return h("div", { class: "msg error" }, icon("alert", 16), h("div", null, message.text, meta));
    return h(
      "div",
      { class: "msg claude" },
      h("div", { class: "msg-who" }, icon("sparkle", 14), message.role === "plan" ? "Claude’s plan" : "Claude"),
      message.text ? h("div", { class: "msg-text" }, message.text) : null,
      message.role === "plan" ? planEl(message, latestPlan) : null,
      meta
    );
  }

  function suggestion(text) {
    const use = () => {
      $("askText").value = text;
      $("askText").focus();
    };
    return h("button", { class: "suggest", type: "button", onclick: use }, text);
  }

  function emptyHint() {
    const fresh = state.mode === "new";
    const ideas = fresh
      ? ["Orders and revenue today against yesterday, and the last 30 days", "Top 10 customers by orders this month", "Sign-ups per day, with a date filter"]
      : ["Stack the daily chart by channel", "Add a table of the top 10 customers", "Add a date filter"];
    return h(
      "div",
      { class: "assist-empty" },
      h("b", null, fresh ? "Describe a new dashboard" : "Ask for a change"),
      fresh
        ? "Claude first shows a plan: the cards, and the tables it will read. It queries the database only after you press Build."
        : "Say what should be different. Claude edits this dashboard and the preview follows.",
      h("div", { class: "suggests" }, h("div", { class: "suggest-label" }, "Try"), ideas.map(suggestion))
    );
  }

  function renderMessages() {
    const messages = state.assist.messages;
    const roles = messages.map((m) => m.role);
    const lastPlan = roles.lastIndexOf("plan");
    const latest = lastPlan > roles.lastIndexOf("user") ? lastPlan : -1;
    $("messages").replaceChildren(...(messages.length ? messages.map((m, i) => messageEl(m, i === latest)) : [emptyHint()]));
  }

  function renderActivity() {
    const job = state.assist.job;
    if (!job || job.status !== "running" || job.key !== assistKey()) return void $("activity").replaceChildren();
    const seconds = Math.max(0, Math.round((Date.now() - new Date(job.started).getTime()) / 1000));
    const elapsed = seconds >= 60 ? `${Math.floor(seconds / 60)} min ${seconds % 60} s` : `${seconds} s`;
    const queries = job.queries ? ` · ${job.queries} ${job.queries === 1 ? "query" : "queries"}` : "";
    $("activity").replaceChildren(
      h(
        "div",
        { class: "activity" },
        h("div", { class: "activity-head" }, h("i", { class: "pulse" }), WORKING[job.kind] || "Claude is working", h("span", { class: "activity-time" }, elapsed + queries)),
        h("ol", null, job.activity.slice(-12).map((a) => h("li", null, h("time", null, a.at), a.text, a.detail ? h("small", null, a.detail) : null)))
      )
    );
  }

  function renderComposer() {
    const job = state.assist.job;
    const running = busy();
    const blocked = running || state.offline || (state.mode === "edit" && !state.slug);
    $("askText").disabled = blocked;
    $("sendBtn").disabled = blocked;
    $("stopBtn").hidden = !running;
    $("askText").placeholder = state.mode === "new" ? "Describe the dashboard you want…" : "Ask for a change to this dashboard…";
    let hint = state.mode === "new" ? "Plan first. Nothing is read until you press Build." : "Changes run directly.";
    if (running) hint = job.key === assistKey() ? "Claude is working." : "Claude is working on another request.";
    $("askHint").textContent = state.assist.flash || hint;
    const planFirst = "Claude shows a plan first. Nothing is read from the database until you press Build.";
    $("assistContext").textContent = state.mode === "new" ? planFirst : state.spec ? `Changing: ${state.spec.name}` : "";
    $("newDash").setAttribute("aria-current", String(state.mode === "new"));
    $("assistClear").hidden = !state.assist.messages.length || (running && job.key === assistKey());
  }

  async function updateAssist(assistant) {
    const a = state.assist;
    const job = assistant.job;
    if (a.followed === undefined) a.followed = job && job.status !== "running" ? job.id : null;
    a.job = job;
    // A finished build opens its dashboard, once.
    if (job && job.kind === "build" && job.status === "done" && job.slug && a.followed !== job.id) {
      a.followed = job.id;
      if (state.page === "new") showPage("dashboards");
      state.mode = "edit";
      history.replaceState(null, "", `#${encodeURIComponent(job.slug)}`);
      a.again = true;
    }
    const key = assistKey();
    const thread = $("thread");
    const atBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 60;
    const changed = assistant.rev !== a.rev || key !== a.key;
    if (changed) {
      a.rev = assistant.rev;
      a.key = key;
      a.messages = key ? (await api(`/api/thread?key=${encodeURIComponent(key)}`)).messages || [] : [];
      renderMessages();
    }
    renderActivity();
    renderComposer();
    if (changed || atBottom) thread.scrollTop = thread.scrollHeight;
  }

  function showAssist(show) {
    $("app").classList.toggle("no-assist", !show);
    $("showAssist").hidden = show;
    try {
      localStorage.setItem("assist", show ? "1" : "0");
    } catch {}
  }

  // ---------- polling ----------

  let polling = false;
  let timer = 0;

  function showOffline(message) {
    if (state.offline) return;
    state.offline = true;
    renderNotice(message, []);
    renderComposer();
  }

  async function poll() {
    if (polling) return;
    polling = true;
    clearTimeout(timer);
    try {
      const data = await api("/api/state");
      if (!data.dashboards) {
        showOffline("This window is no longer connected. Open the studio again from its shortcut.");
        return;
      }
      if (state.offline) {
        state.offline = false;
        const problems = state.spec ? state.spec.problems : [];
        renderNotice(problems.length ? "This dashboard's files need fixing" : "", problems);
      }
      const firstLook = !state.setup;
      state.setup = data.setup;
      if (firstLook && !data.setup.database) showPage("settings");
      if (state.page === "settings") return void renderList(data.dashboards);
      if (state.page === "new") {
        renderList(data.dashboards);
        return void (await updateAssist(data.assistant));
      }
      const slugs = data.dashboards.map((d) => d.slug);
      const wanted = decodeURIComponent(location.hash.slice(1));
      const slug = slugs.includes(wanted) ? wanted : slugs.includes(state.slug) ? state.slug : slugs[0] || null;
      if (!slug) {
        state.slug = null;
        state.spec = null;
        $("app").classList.add("no-dashboards");
        renderList([]);
        $("top").hidden = true;
        $("desc").hidden = true;
        $("filters").hidden = true;
        $("grid").replaceChildren();
        renderNotice("", []);
        $("empty").hidden = false;
      } else {
        $("app").classList.remove("no-dashboards");
        const current = data.dashboards.find((d) => d.slug === slug);
        if (slug !== state.slug || !state.spec || current.version !== state.spec.version) await loadDashboard(slug);
        renderList(data.dashboards);
      }
      await updateAssist(data.assistant);
    } catch {
      showOffline("The studio is not running. Open it again from its shortcut.");
    } finally {
      polling = false;
      const soon = state.assist.again;
      state.assist.again = false;
      // Asks the local server what changed. This never reaches the database.
      timer = setTimeout(poll, soon ? 50 : busy() ? 1000 : POLL_MS);
    }
  }

  // ---------- Go live ----------

  async function post(path, body) {
    try {
      return await api(path, body);
    } catch {
      return { error: "The studio did not answer." };
    }
  }

  function liveList(title, items, kind) {
    return items.length ? h("div", { class: `live-box ${kind}` }, h("strong", null, title), h("ul", null, items.map((text) => h("li", null, text)))) : null;
  }

  function liveFooter(...buttons) {
    return h("div", { class: "live-foot" }, buttons);
  }

  function closeButton(label) {
    return h("button", { class: "btn", type: "button", onclick: () => $("liveDialog").close() }, label);
  }

  function showLive(...content) {
    $("liveDialog").replaceChildren(...content.flat().filter(Boolean));
    const first = $("liveDialog").querySelector("button");
    if (first) first.focus();
  }

  async function openGoLive() {
    const slug = state.slug;
    if (!slug) return;
    showLive(h("h2", null, "Go live"), h("p", { class: "live-soft" }, "Checking with Metabase…"));
    $("liveDialog").showModal();
    const reply = await post("/api/golive/plan", { slug });
    if (!reply.plan) return showLive(h("h2", null, "Go live"), liveList("Go live is not possible", [reply.error || "Unknown error."], "stop"), liveFooter(closeButton("Close")));
    const plan = reply.plan;
    const where = plan.target;
    const counts = [];
    if (plan.cards.create) counts.push(`${plan.cards.create} new`);
    if (plan.cards.update) counts.push(`${plan.cards.update} updated`);
    if (plan.cards.trash) counts.push(`${plan.cards.trash} to Metabase’s trash`);
    const blocked = plan.blockers.length > 0;
    const go = h("button", { class: "btn primary", type: "button", disabled: blocked, onclick: () => publish(slug, plan.name) }, "Go live");
    showLive(
      h("h2", null, `Go live: ${plan.name}`),
      where
        ? h(
            "dl",
            { class: "live-facts" },
            h("dt", null, "What happens"),
            h("dd", null, plan.mode === "update" ? "The dashboard already in Metabase is updated." : "A new dashboard is created in Metabase."),
            h("dt", null, "Collection"),
            h("dd", null, where.collection),
            h("dt", null, "Database"),
            h("dd", null, where.database),
            h("dt", null, "Cards"),
            h("dd", null, counts.join(", ") || "none")
          )
        : null,
      liveList("Fix this first", plan.blockers, "stop"),
      liveList("Before you confirm", plan.warnings, "warn"),
      blocked ? null : h("p", { class: "live-soft" }, "No query runs now. Metabase runs the cards when someone opens the dashboard."),
      liveFooter(closeButton("Cancel"), go)
    );
  }

  async function publish(slug, name) {
    showLive(h("h2", null, `Go live: ${name}`), h("p", { class: "live-soft" }, "Publishing to Metabase…"));
    const reply = await post("/api/golive", { slug });
    await poll();
    if (!reply.published) {
      return showLive(h("h2", null, `Go live: ${name}`), liveList("Go live did not finish", [reply.error || "Unknown error."], "stop"),
        h("p", { class: "live-soft" }, "Anything already created is remembered, so trying again continues and does not make a copy."),
        liveFooter(closeButton("Close")));
    }
    showLive(
      h("h2", null, `${name} is live`),
      h("p", null, reply.published.mode === "update" ? "The dashboard in Metabase was updated." : `The dashboard was created in “${reply.published.target.collection}”.`),
      h("p", { class: "live-link" }, reply.published.url),
      liveFooter(closeButton("Close"), h("button", { class: "btn primary", type: "button", onclick: () => openInMetabase(slug) }, "Open in Metabase"))
    );
  }

  async function openInMetabase(slug) {
    const reply = await post("/api/open", { slug });
    if (reply.error) renderNotice(reply.error, []);
  }

  // Asks whether the published dashboard is still in Metabase. This goes to Metabase, never to the database.
  const liveCheck = { slug: null, at: 0 };
  async function checkLive() {
    const slug = state.slug;
    if (!slug || !state.spec || !state.spec.live) return;
    if (liveCheck.slug === slug && Date.now() - liveCheck.at < 8000) return;
    Object.assign(liveCheck, { slug, at: Date.now() });
    let reply;
    try {
      reply = await api(`/api/live?slug=${encodeURIComponent(slug)}`);
    } catch {
      return;
    }
    if (state.slug !== slug || !state.spec || !reply.state || reply.state === "unknown" || reply.state === state.spec.metabase) return;
    state.spec.metabase = reply.state;
    renderTop();
    poll();
  }

  $("goLive").addEventListener("click", openGoLive);
  $("openLive").addEventListener("click", () => openInMetabase(state.slug));
  window.addEventListener("focus", checkLive);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) checkLive();
  });

  // ---------- remove ----------

  function askRemove() {
    const spec = state.spec;
    if (!spec) return;
    const title = h("h2", null, `Remove ${spec.name}?`);
    const stillLive = spec.live && spec.metabase !== "trashed" && spec.metabase !== "gone";
    const remove = h("button", { class: "btn danger", type: "button" }, "Remove");
    remove.addEventListener("click", async () => {
      remove.disabled = true;
      const reply = await post("/api/remove", { slug: spec.slug });
      if (reply.error) return showLive(title, liveList("It was not removed", [reply.error], "stop"), liveFooter(closeButton("Close")));
      $("liveDialog").close();
      state.slug = null;
      state.spec = null;
      history.replaceState(null, "", "#");
      poll();
    });
    showLive(
      title,
      h("p", null, "It leaves the studio. Its files move to the trash folder inside data, so it can be put back by hand."),
      stillLive ? h("p", { class: "live-soft" }, "It is live in Metabase and stays there. Remove it in Metabase yourself if it should go.") : null,
      liveFooter(closeButton("Cancel"), remove)
    );
    $("liveDialog").showModal();
  }

  $("removeDash").addEventListener("click", askRemove);

  // ---------- settings and theme ----------

  function themeChanged() {
    readTheme();
    for (const key of elements.keys()) renderCard(key);
    $("themeBtn").replaceChildren(icon(document.documentElement.dataset.theme === "dark" ? "sun" : "moon", 15));
  }

  function setTheme(choice) {
    window.studioTheme.set(choice);
    themeChanged();
  }

  function appearanceSection() {
    const choices = [["auto", "Match the system"], ["light", "Light"], ["dark", "Dark"]];
    const row = h("div", { class: "segmented" });
    const draw = () =>
      row.replaceChildren(...choices.map(([value, label]) =>
        h("button", { class: "btn", type: "button", "aria-pressed": String(window.studioTheme.get() === value),
          onclick: () => { setTheme(value); draw(); } }, label)));
    draw();
    return h("section", { class: "panel" },
      h("header", { class: "panel-head" }, h("span", { class: "panel-mark" }, icon("moon", 17)),
        h("div", null, h("h2", null, "Appearance"),
          h("p", null, "Metabase itself shows dashboards on a light background, unless the viewer turns on its night mode."))),
      h("div", { class: "form" }, row));
  }

  function showPage(page) {
    state.page = page;
    const onSettings = page === "settings";
    const onNew = page === "new";
    // A new dashboard has its own screen: the conversation fills the page until the dashboard exists.
    state.mode = onNew ? "new" : "edit";
    $("app").classList.toggle("on-new", onNew);
    document.querySelector(".assist-title").textContent = onNew ? "New dashboard" : "Ask Claude";
    if (onNew) document.title = "New dashboard · Metabase Claude Studio";
    $("settingsPage").hidden = !onSettings;
    $("dashPage").hidden = onSettings;
    $("app").classList.toggle("on-settings", onSettings);
    $("openSettings").setAttribute("aria-current", String(onSettings));
    // Coming back, the dashboard is drawn afresh: its cards were hidden and may have changed size.
    state.slug = null;
    state.spec = null;
    renderComposer();
    if (!onSettings) return;
    $("top").hidden = false;
    $("title").textContent = state.setup && !state.setup.database ? "Set up" : "Settings";
    $("status").replaceChildren();
    document.title = "Settings · Metabase Claude Studio";
    const done = () => {
      showPage("dashboards");
      poll();
    };
    window.studioSettings.render({ h, icon, get: api, post, onDone: done }, $("settingsPage"), appearanceSection());
  }

  $("openSettings").addEventListener("click", () => {
    showPage("settings");
    poll();
  });
  $("themeBtn").addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => setTimeout(themeChanged, 0));
  themeChanged();

  // The fixed buttons get their icons here, so the markup stays plain.
  for (const [id, name, size] of [
    ["newDash", "plus"], ["emptyNew", "plus"], ["refreshAll", "refresh"], ["openLive", "external"], ["goLive", "upload"],
    ["showAssist", "sparkle"], ["assistMark", "sparkle", 18], ["emptyMark", "chart", 26], ["assistClear", "trash", 15],
    ["hideAssist", "panel", 15], ["sendBtn", "send", 15], ["stopBtn", "stop", 14], ["sideFoot", "lock", 14],
    ["openSettings", "settings", 17], ["removeDash", "trash", 16],
  ]) {
    $(id).prepend(icon(name, size));
  }
  $("emptyNew").addEventListener("click", () => $("newDash").click());

  $("refreshAll").addEventListener("click", () => {
    if (!state.spec) return;
    for (const card of state.spec.cards) if (card.sql) enqueue(card.key, true, false);
  });

  $("askForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = $("askText").value.trim();
    if (!text || busy()) return;
    const reply = await act("/api/ask", { mode: state.mode, slug: state.slug, text });
    if (reply.ok) $("askText").value = "";
  });
  $("askText").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      $("askForm").requestSubmit();
    }
  });
  $("askText").addEventListener("input", () => {
    state.assist.flash = "";
  });
  $("stopBtn").addEventListener("click", () => act("/api/stop", {}));
  $("assistClear").addEventListener("click", () => act("/api/clear", { key: assistKey() }));
  $("newDash").addEventListener("click", () => {
    showPage("new");
    poll();
    $("askText").focus();
  });
  $("hideAssist").addEventListener("click", () => showAssist(false));
  $("showAssist").addEventListener("click", () => showAssist(true));
  try {
    if (localStorage.getItem("assist") === "0") showAssist(false);
  } catch {}

  let frame = 0;
  new ResizeObserver(() => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      sizeGrid();
      for (const chart of charts.values()) chart.resize();
    });
  }).observe($("grid"));

  window.addEventListener("hashchange", () => {
    state.mode = "edit";
    poll();
  });
  poll();
})();
