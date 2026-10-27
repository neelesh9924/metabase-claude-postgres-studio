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
    page: "dashboards", setup: null, databases: [], newDb: null, picked: {}, listsAsked: new Set(),
    // A new dashboard's conversation is a draft. draft is the open one's key; null on the New dashboard screen means not begun.
    // listed is the open draft once the server's list has had it.
    draft: null, drafts: [], listed: null,
    mode: "edit", assist: { rev: -1, key: null, job: null, messages: [], followed: undefined, again: false },
    // answered: the studio has answered once (until then its buttons wait); lastSlug: the dashboard to come back to.
    answered: false, lastSlug: null,
  };
  const APP = "Metabase Claude Studio";
  const elements = new Map();
  const charts = new Map();
  let attach = null;     // static/attach.js, once it has loaded
  let attached = null;   // the composer's attacher

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
    gear: ["M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z", "M19.4 13.5l1.6 1-2 3.4-1.8-.6a7 7 0 0 1-2.4 1.4L14.4 21h-4l-.4-2.3a7 7 0 0 1-2.4-1.4l-1.8.6-2-3.4 1.6-1a7 7 0 0 1 0-3l-1.6-1 2-3.4 1.8.6A7 7 0 0 1 10 5.3L10.4 3h4l.4 2.3a7 7 0 0 1 2.4 1.4l1.8-.6 2 3.4-1.6 1a7 7 0 0 1 0 3z"],
    moon: ["M20 14.5A8 8 0 0 1 9.5 4a7 7 0 1 0 10.5 10.5z"],
    sun: ["M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z", "M12 3v2", "M12 19v2", "M3 12h2", "M19 12h2", "M5.6 5.6L7 7", "M17 17l1.4 1.4", "M5.6 18.4L7 17", "M17 7l1.4-1.4"],
    copy: ["M9 9h11v11H9z", "M5 15H4V4h11v1"],
    chat: ["M20.5 11.5a8.5 8.5 0 0 1-12.3 7.6L3.5 20.5l1.4-4.5A8.5 8.5 0 1 1 20.5 11.5z"],
    file: ["M6 3h8l4 4v14H6z", "M14 3v4h4", "M9 12h6", "M9 16h6"],
    search: ["M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13z", "M15.5 15.5L20 20"],
    x: ["M6 6l12 12", "M18 6L6 18"],
    history: ["M3.5 12a8.5 8.5 0 1 0 2.6-6.1", "M3 4v4.5h4.5", "M12 8v4.5l3 2"],
    keyboard: ["M3 6h18v12H3z", "M7 10h.01", "M11 10h.01", "M15 10h.01", "M7 14h10"],
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
  // The data's own dates and times, as Metabase writes them ("Oct 27, 2026, 5:43 PM"), so the preview reads as the
  // published dashboard will. The studio's own times (a reply's, a card's last run) are static/time.js's.

  function parseDate(text) {
    const m = /^(\d{4})-(\d\d)-(\d\d)(?:[T ](\d\d):(\d\d))?/.exec(String(text));
    return m ? new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0)) : null;
  }

  function dataClock(d) {
    const hours = d.getHours() % 12 || 12;
    return `${hours}:${String(d.getMinutes()).padStart(2, "0")} ${d.getHours() < 12 ? "AM" : "PM"}`;
  }

  function fmtDate(text, withTime) {
    const d = parseDate(text);
    if (!d) return String(text);
    const day = `${MONTHS[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
    return withTime && (d.getHours() || d.getMinutes()) ? `${day}, ${dataClock(d)}` : day;
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
      if (hasTime && oneDay) return dataClock(d);
      let label = `${MONTHS[d.getMonth()]} ${d.getDate()}`;
      if (!oneYear) label += `, ${d.getFullYear()}`;
      if (hasTime) label += ` ${dataClock(d)}`;
      return label;
    });
  }

  function fmtDuration(ms) {
    return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
  }

  const studioTime = window.studioTime;

  // What a card's last run gave: "12 rows · 31 ms".
  function runWords(result) {
    const rows = `${result.row_count.toLocaleString("en-US")}${result.truncated ? "+" : ""} ${result.row_count === 1 ? "row" : "rows"}`;
    return `${rows} · ${fmtDuration(result.ms)}`;
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

  // A chart's words for a screen reader: ECharts describes the chart and its data, under the card's name (the first
  // title, never drawn).
  function spoken(card) {
    return { aria: { enabled: true }, title: { text: card.name || "", show: false } };
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
      ...spoken(card),
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
      aria: spoken(card).aria,
      color: SERIES,
      textStyle: { fontFamily: FONT },
      title: [spoken(card).title, {
        text: fmtNumber(total, settings, Math.abs(total) >= 1e6),
        subtext: "Total",
        left: centre,
        top: "middle",
        textAlign: "center",
        itemGap: 2,
        textStyle: { color: INK, fontSize: 18, fontWeight: 700 },
        subtextStyle: { color: INK_SOFT, fontSize: 11 },
      }],
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

  // ---------- helpers every part of the page shares (as in the owner's other studios) ----------

  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

  function setText(el, text) {
    if (el.textContent !== text) el.textContent = text;
  }

  // Says text once to a screen reader: politely through #announce (role=status), or at once through #alarm (role=alert)
  // when urgent. The region is emptied first and the words put in on the next frame, so the same words twice are said
  // twice. While the dialog is open the rest of the page is inert: meanwhile the region speaks from inside the dialog,
  // and goes back to the end of <body> when it closes.
  const regions = {};
  const speaking = new Map();
  function announce(text, { urgent = false } = {}) {
    const id = urgent ? "alarm" : "announce";
    const region = regions[id] || (regions[id] = $(id));
    if (!region || !text) return;
    const dialog = $("liveDialog");
    const home = dialog.open ? dialog : document.body;
    if (region.parentNode !== home) {
      home.append(region);
      if (home === dialog) dialog.addEventListener("close", () => document.body.append(region), { once: true });
    }
    cancelAnimationFrame(speaking.get(region));
    region.textContent = "";
    speaking.set(region, requestAnimationFrame(() => {
      speaking.delete(region);
      region.textContent = text;
    }));
  }

  // Runs work() with the button busy: aria-disabled and aria-busy while it runs and its clicks ignored, but never the
  // disabled property, so the button keeps the focus and a screen reader stays on it. Returns work()'s result.
  async function whileBusy(button, work) {
    const before = ["aria-disabled", "aria-busy"].map((name) => [name, button.getAttribute(name)]);
    const ignore = (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
    };
    button.addEventListener("click", ignore, true);
    button.setAttribute("aria-disabled", "true");
    button.setAttribute("aria-busy", "true");
    try {
      return await work();
    } finally {
      button.removeEventListener("click", ignore, true);
      for (const [name, value] of before) {
        if (value === null) button.removeAttribute(name);
        else button.setAttribute(name, value);
      }
    }
  }

  const isOff = (button) => button.getAttribute("aria-disabled") === "true";

  // A copy button, as in the owner's other studios: a copy icon and a label; pressed, "Copied" and the copied look for
  // a moment, said to a screen reader. square: the icon alone, named by the label, and a check for that moment instead.
  // text: the words, or a function that gives them when it is pressed.
  const COPIED_MS = 1400;
  function copyButton(text, label = "Copy", small = true, square = false) {
    const button = h("button", { class: `btn${small ? " small" : ""}${square ? " square" : ""}`, type: "button",
      "aria-label": square ? label : null, title: square ? label : null }, icon("copy", 14), square ? null : h("span", null, label));
    let timer = 0;
    const look = (copied) => {
      button.classList.toggle("copied", copied);
      if (square) button.firstChild.replaceWith(icon(copied ? "check" : "copy", 14));
      else button.lastChild.textContent = copied ? "Copied" : label;
    };
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(String(typeof text === "function" ? text() : text));
      } catch {
        return void announce("Could not copy. Select the text and press Ctrl+C.", { urgent: true });
      }
      clearTimeout(timer);
      look(true);
      announce("Copied");
      timer = setTimeout(() => look(false), COPIED_MS);
    });
    return button;
  }

  // Moves the focus to the first of the candidates (elements, or selectors for one) that is in the page, shown and not
  // disabled; one that can't take the focus by itself is given tabindex="-1" first. Returns the element, or null.
  function focusFirst(...candidates) {
    for (const candidate of candidates.flat()) {
      const el = typeof candidate === "string" ? document.querySelector(candidate) : candidate;
      if (!(el instanceof Element) || !el.isConnected || !el.getClientRects().length || getComputedStyle(el).visibility === "hidden"
        || el.matches(":disabled")) continue;
      el.focus();
      if (document.activeElement === el) return el;
      if (el.hasAttribute("tabindex")) continue;
      el.setAttribute("tabindex", "-1");
      el.focus();
      if (document.activeElement === el) return el;
      el.removeAttribute("tabindex");
    }
    return null;
  }

  // Draws into box only when key (what it would show, as a string) differs from the key last drawn there.
  const drawn = new WeakMap();
  function drawIfChanged(box, key, draw) {
    if (drawn.has(box) && drawn.get(box) === key) return false;
    draw();
    drawn.set(box, key);
    return true;
  }

  // The element with this data-key in box, for giving the focus back after box is drawn again.
  const keyed = (box, key) => (key ? [...box.querySelectorAll("[data-key]")].find((el) => el.dataset.key === key) || null : null);
  const focusedKey = (box) => (box.contains(document.activeElement) ? document.activeElement.dataset.key || null : null);

  // The window's title: what a run says of itself (studioProgress.mark(): "", or Working / Ready / Needs you), then base.
  let titleBase = APP;
  function setTitle(base) {
    titleBase = base;
    retitle();
  }

  function retitle() {
    let mark = "";
    try {
      mark = (window.studioProgress && window.studioProgress.mark()) || "";
    } catch (error) {
      console.error(error);
    }
    const text = `${mark}${titleBase}`;
    if (document.title !== text) document.title = text;
  }

  // ---------- the dialog ----------

  // Each opening of the dialog takes a token, and closing it gives the token up: an answer that comes late is shown only
  // while its own opening is still there.
  let dialogToken = 0;
  const ownsDialog = (token) => $("liveDialog").open && token === dialogToken;
  $("liveDialog").addEventListener("close", () => {
    dialogToken += 1;
  });

  function liveList(title, items, kind) {
    return items.length ? h("div", { class: `live-box ${kind}` }, h("strong", null, title), h("ul", null, items.map((text) => h("li", null, text)))) : null;
  }

  function liveFooter(...buttons) {
    return h("div", { class: "live-foot" }, buttons);
  }

  function closeDialog() {
    if ($("liveDialog").open) $("liveDialog").close();
  }

  function closeButton(label) {
    return h("button", { class: "btn", type: "button", onclick: closeDialog }, label);
  }

  function showDialog(...content) {
    const dialog = $("liveDialog");
    const spoken = [...dialog.querySelectorAll(":scope > #announce, :scope > #alarm")];   // a region speaking from here stays
    dialog.className = "modal";
    dialog.replaceChildren(...content.flat().filter(Boolean), ...spoken);
    nameDialog();
    if (!dialog.open) {
      dialogToken += 1;
      dialog.showModal();
    }
    focusFirst(dialog.querySelector("input, select, textarea, button"));
    return dialogToken;
  }

  // A screen reader names the dialog by its first heading, and describes it by the paragraph right after that heading
  // (the question a confirmation asks, the word while something is under way); done again whenever its content changes.
  function nameDialog() {
    const dialog = $("liveDialog");
    const title = dialog.querySelector("h2");
    const said = title && title.nextElementSibling && title.nextElementSibling.tagName === "P" ? title.nextElementSibling : null;
    for (const [attribute, el, id] of [["aria-labelledby", title, "dialog-title"], ["aria-describedby", said, "dialog-said"]]) {
      if (!el) {
        dialog.removeAttribute(attribute);
        continue;
      }
      if (!el.id) el.id = id;
      dialog.setAttribute(attribute, el.id);
    }
  }
  new MutationObserver(nameDialog).observe($("liveDialog"), { childList: true });

  // ---------- cards ----------

  function toolButton(name, label, pressed, title, onclick) {
    const attrs = { class: "tool", type: "button", "aria-label": label, "aria-pressed": pressed === null ? null : String(pressed), title,
      "data-tool": name, "aria-disabled": state.offline || !state.answered ? "true" : null, onclick: (event) => !isOff(event.currentTarget) && onclick() };
    return h("button", attrs, icon(name, 15));
  }

  function setView(key, view) {
    state.view[key] = state.view[key] === view ? "chart" : view;
    renderCard(key);
  }

  // The focus on one of a card's buttons comes back to the same one when the card is drawn again: the same tool, else
  // the flag that took its place, else the card's Refresh.
  function focusTool(el, tool) {
    const at = (name) => el.querySelector(`[data-tool="${name}"]`);
    return focusFirst(at(tool), at(tool === "show" ? "hide" : "show"), at("refresh"));
  }

  function renderCard(key) {
    const card = state.spec && state.spec.cards.find((c) => c.key === key);
    const el = elements.get(key);
    if (!card || !el) return;
    const focused = el.contains(document.activeElement) ? document.activeElement.dataset.tool || null : null;
    const old = charts.get(key);
    if (old) {
      old.dispose();
      charts.delete(key);
    }
    el.replaceChildren();
    el.className = `card d-${card.display}`;
    if (card.display === "heading") return void el.append(h("h2", { class: "heading" }, card.text));
    if (card.display === "text") return void el.append(h("div", { class: "text" }, card.text));
    drawCard(card, el);
    renderRefresh();
    if (focused) focusTool(el, focused);
  }

  function drawCard(card, el) {
    const key = card.key;
    const entry = state.cards[key] || {};
    const waiting = entry.loading || entry.queued;
    const result = entry.result || (waiting ? entry.stale : null);
    const view = state.view[key] || "chart";
    const isScalar = card.display === "scalar";
    // Hidden values: personal data the user may choose to show, and secrets that are never shown.
    const columns = result ? result.columns : [];
    const personal = columns.filter((c) => c.pii && c.sensitive === "personal").map((c) => c.name);
    const secret = columns.filter((c) => c.pii && c.sensitive !== "personal").map((c) => c.name);
    const revealed = columns.filter((c) => c.revealed).map((c) => c.name);
    const flags = [
      personal.length
        ? h("button", { class: "flag", type: "button", "data-tool": "show", title: `Hidden: ${personal.join(", ")}. Press to choose whether to show the values.`,
            onclick: () => askShow(card, personal) }, "Personal data hidden · Show")
        : null,
      revealed.length
        ? h("button", { class: "flag shown", type: "button", "data-tool": "hide", title: `Shown because you said so: ${revealed.join(", ")}. Press to hide the values again.`,
            onclick: () => setShown(card, revealed, false) }, "Personal data shown · Hide")
        : null,
      secret.length
        ? h("span", { class: "flag", title: `Hidden: ${secret.join(", ")}. Passwords, codes and tokens are never shown. Leave these columns out.` }, "Secret hidden")
        : null,
    ];
    const failed = entry.error && !waiting;
    const heavy = entry.heavy && !waiting;
    // A number says its card's name under it; in every other state (failed, heavy, loading, empty) the head says it.
    const ownTitle = isScalar && view === "chart" && result && result.rows.length && !failed && !heavy && !flags.some(Boolean);
    if (isScalar || card.display === "smartscalar") {
      el.setAttribute("role", "group");
      el.setAttribute("aria-label", card.name);
    } else {
      el.removeAttribute("role");
      el.removeAttribute("aria-label");
    }
    if (!ownTitle) {
      el.append(
        h(
          "header",
          { class: "card-head" },
          h("h2", { class: "card-title", title: card.name }, card.name),
          result ? h("span", { class: "card-meta" }, `${runWords(result)} · as of `, studioTime.tag(result.ran_at, "inline")) : null
        )
      );
      if (flags.some(Boolean)) el.append(h("div", { class: "card-flags" }, flags));
    }
    el.append(
      h(
        "div",
        { class: "card-tools" },
        card.display === "table" ? null : toolButton("table", "Table", view === "table", "Show the rows behind this card", () => setView(key, "table")),
        toolButton("code", "SQL", view === "sql", "Show the query", () => setView(key, "sql")),
        toolButton("refresh", "Refresh", null, result ? `Run again. Last run: ${runWords(result)} · ${studioTime.full(result.ran_at)}` : "Run the query",
          () => enqueue(key, true, false))
      )
    );

    const body = h("div", { class: "card-body" });
    el.append(body);
    if (view === "sql") return void body.append(sqlView(card));
    if (failed) {
      return void body.append(h("div", { class: "problem" }, h("strong", null, "The query failed"), h("pre", null, entry.error)));
    }
    if (heavy) {
      const cost = Math.round(entry.heavy.cost).toLocaleString("en-US");
      const limit = Math.round(entry.heavy.limit).toLocaleString("en-US");
      return void body.append(
        h(
          "div",
          { class: "problem" },
          h("strong", null, "Not run: this query looks heavy"),
          h("span", null, `Estimated cost ${cost}, limit ${limit}.`),
          h("button", { class: "btn small", type: "button", "data-tool": "anyway", onclick: () => enqueue(key, true, true) }, "Run anyway")
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

    // A picture to a screen reader, named by its card: ECharts puts its own description of the chart and its data here.
    const box = h("div", { class: "chart", role: "img", "aria-label": card.name });
    body.append(box);
    const option = card.display === "pie" ? pieOption(card, result, box.clientWidth) : cartesianOption(card, result);
    if (!option) {
      box.remove();
      return void body.append(h("div", { class: "placeholder" }, "This chart needs a number column."));
    }
    mount(key, box, option);
  }

  // The card's query as its file has it, under a head with the file's name and Copy, as the owner's other studios put
  // Copy above what it copies. Both keep the focus when the card is drawn again (data-tool); the query takes it so
  // that the keyboard can scroll it.
  function sqlView(card) {
    if (!card.sql) return h("pre", { class: "sql" }, "The SQL file is missing.");
    const copy = copyButton(card.sql);
    copy.setAttribute("aria-label", `Copy the SQL of “${card.name || card.key}”`);
    copy.dataset.tool = "copy";
    return h("div", { class: "sql-view" },
      h("div", { class: "sql-head" }, h("span", { class: "sql-file" }, `${card.key}.sql`), copy),
      h("pre", { class: "sql", tabindex: "0", "data-tool": "sql" }, card.sql));
  }

  const pending = () => Object.values(state.cards).some((entry) => entry.queued || entry.loading);

  // Refresh data waits while any card's query waits or runs, and says so: pressed again it would only queue them twice.
  function renderRefresh() {
    const button = $("refreshAll");
    const running = pending();
    setText(button.querySelector(".label"), running ? "Running…" : "Refresh data");
    button.setAttribute("aria-disabled", String(running || state.offline || !state.answered));
    if (running) button.setAttribute("aria-busy", "true");
    else button.removeAttribute("aria-busy");
  }

  // One query at a time, in turn. A card already waiting or running is not asked for again: a forced run only upgrades a
  // waiting one that wasn't forced.
  function enqueue(key, force, allowHeavy) {
    const entry = state.cards[key];
    if (!entry || entry.loading) return;
    if (entry.queued) {
      if (force) entry.force = true;
      if (allowHeavy) entry.allowHeavy = true;
      return;
    }
    Object.assign(entry, { queued: true, force: !!force, allowHeavy: !!allowHeavy });
    const gen = state.gen;
    const slug = state.slug;
    renderCard(key);
    state.chain = state.chain.then(async () => {
      const now = state.cards[key];
      if (state.gen !== gen || state.slug !== slug || !now) return;
      now.queued = false;
      if (!now.force && (now.result || now.error || now.heavy)) return void renderCard(key);
      now.loading = true;
      renderCard(key);
      const reply = await post("/api/run", { slug, key, allow_heavy: !!now.allowHeavy, values: state.picked[slug] });
      const target = state.slug === slug ? state.cards[key] : null;
      if (!target) return;
      Object.assign(target, { loading: false, force: false, allowHeavy: false });
      if (reply.result && reply.sql_hash === target.hash) {
        Object.assign(target, { result: reply.result, error: null, heavy: null, stale: null });
      } else if (reply.heavy) {
        Object.assign(target, { heavy: reply.heavy, error: null });
      } else if (!reply.result) {
        Object.assign(target, { error: reply.error || "Unknown error.", heavy: null });
        const card = state.spec && state.spec.cards.find((c) => c.key === key);
        announce(`${card ? card.name : "A card"}: the query failed.`);
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

  // A word at the top of the dashboard page about what just happened (a removal with Put back, a Go live that ended
  // after its window was closed); kept until the next one, or until the owner opens another dashboard.
  function flash(words, action, kind = "info") {
    const box = $("flash");
    box.hidden = !words;
    if (!words) return void box.replaceChildren();
    box.className = `notice ${kind}`;
    box.replaceChildren(icon(kind === "bad" ? "alert" : "check", 17), h("div", { class: "grow" }, words), action || null);
  }

  function severalDatabases() {
    return state.databases.length > 1;
  }

  const noDatabase = () => !state.databases.some((d) => d.ready);

  // Where a dashboard stands: only here (built, not published), live in Metabase, or live but no longer matching.
  function statusOf(d) {
    if (!d.live) return { text: "Not live", kind: "" };
    if (d.metabase === "trashed") return { text: "In Metabase’s trash", kind: "bad" };
    if (d.metabase === "gone") return { text: "Deleted in Metabase", kind: "bad" };
    if (d.metabase === "moved") return { text: "Moved in Metabase", kind: "warn" };
    if (d.changed) return { text: "Changes not live", kind: "warn" };
    return { text: "Live", kind: "ok" };
  }

  // The top bar's buttons wait until the studio answers, and while it does not.
  function renderActions() {
    const off = state.offline || !state.answered;
    for (const id of ["goLive", "dashSettings"]) {
      if ($(id).getAttribute("aria-busy") !== "true") $(id).setAttribute("aria-disabled", String(off));
    }
    renderRefresh();
  }

  function clearStatus() {
    drawIfChanged($("status"), "", () => $("status").replaceChildren());
  }

  function renderTop() {
    const spec = state.spec;
    const status = statusOf(spec);
    const database = severalDatabases() && spec.database_name ? spec.database_name : "";
    // Changed in place, only when it changes: a screen reader hears where the dashboard stands when that is news.
    drawIfChanged($("status"), JSON.stringify([status, database]), () => {
      const chips = [h("span", { class: `chip ${status.kind}` }, h("i", { class: "dot", "aria-hidden": "true" }), status.text)];
      if (database) chips.push(h("span", { class: "chip", title: "The database this dashboard reads" }, icon("table", 13), database));
      $("status").replaceChildren(...chips);
    });
    $("openLive").hidden = !spec.live || spec.metabase === "trashed" || spec.metabase === "gone";
    copyLive.hidden = $("openLive").hidden;
    renderActions();
  }

  // Copy the Metabase link: right after Open in Metabase, shown exactly when it is.
  const copyLive = copyButton(() => (state.spec && state.spec.url) || "", "Copy the Metabase link", false, true);
  copyLive.id = "copyLive";
  copyLive.hidden = true;
  $("openLive").after(copyLive);

  function renderDashboard() {
    const grid = $("grid");
    // The focus on a card's button stays on the same button of the same card, drawn afresh.
    const was = grid.contains(document.activeElement) ? document.activeElement : null;
    const focusCard = was && was.closest(".card") ? was.closest(".card").dataset.key : null;
    const focusName = was ? was.dataset.tool || null : null;
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
    setTitle(`${spec.name} · ${APP}`);
    if (!state.offline) renderNotice(spec.problems.length ? "This dashboard's files need fixing" : "", spec.problems);
    renderFilters();
    grid.replaceChildren();
    sizeGrid();
    // On a phone the cards go one under another in the order they are read: by row, then by column.
    const order = [...spec.cards].sort((a, b) => a.row - b.row || a.col - b.col).map((c) => c.key);
    for (const card of spec.cards) {
      const el = h("section", { class: "card", "data-key": card.key });
      el.style.gridColumn = `${card.col + 1} / span ${card.size_x}`;
      el.style.gridRow = `${card.row + 1} / span ${card.size_y}`;
      el.style.setProperty("--order", String(order.indexOf(card.key)));
      elements.set(card.key, el);
      grid.append(el);
    }
    for (const card of spec.cards) renderCard(card.key);
    if (focusCard && elements.get(focusCard)) focusTool(elements.get(focusCard), focusName || "refresh");
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
    loadDashboard(slug).catch(() => {});
  }

  function dateControl(def, value) {
    const current = value || "";
    const listed = DATE_CHOICES.some(([v]) => v === current);
    const range = /^(\d{4}-\d\d-\d\d)?~(\d{4}-\d\d-\d\d)?$/.exec(current) || [];
    const choices = [...DATE_CHOICES, ["custom", "Custom range…"]];
    const select = h("select", { class: "input", "aria-label": def.name, "data-key": def.key },
      choices.map(([v, label]) => h("option", { value: v, selected: listed ? v === current : v === "custom" }, label)));
    const from = h("input", { class: "input", type: "date", value: range[1] || "", "aria-label": `${def.name} from`, "data-key": `${def.key}~from` });
    const to = h("input", { class: "input", type: "date", value: range[2] || "", "aria-label": `${def.name} to`, "data-key": `${def.key}~to` });
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
      const select = h("select", { class: "input", "aria-label": def.name, "data-key": def.key },
        h("option", { value: "" }, "All"),
        list.map((item) => h("option", { value: String(item), selected: String(item) === String(value ?? "") }, String(item))));
      select.addEventListener("change", () => pick(def.key, select.value));
      return [select];
    }
    const input = h("input", { class: "input", value: value ?? "", "aria-label": def.name, placeholder: def.values ? "Loading the list…" : "Any",
      inputmode: def.type === "number" ? "decimal" : null, "data-key": def.key });
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

  // Drawn again only when a filter, its value or its list changed; then the focus goes back to the same control.
  function renderFilters() {
    const defs = state.spec.filters || [];
    const values = state.spec.values || {};
    const options = state.spec.options || {};
    const box = $("filters");
    box.hidden = !defs.length;
    drawIfChanged(box, JSON.stringify([state.slug, defs, values, defs.map((d) => options[d.key] || null)]), () => {
      const on = focusedKey(box);
      box.replaceChildren(...defs.map((def) =>
        h("div", { class: "filter" }, h("span", { class: "filter-name" }, def.name),
          h("div", { class: "filter-controls" }, def.type === "date" ? dateControl(def, values[def.key]) : choiceControl(def, values[def.key])))));
      if (on) focusFirst(keyed(box, on));
    });
    for (const def of defs) if (def.values && !options[def.key]) loadList(def.key);
  }

  // ---------- the sidebar ----------

  const working = (job) => !!job && job.status === "running";

  // The dashboards, drawn again only when one of them or where it stands changes, so the button the owner is on keeps
  // the focus; when they are drawn again, the focus stays on the same dashboard's button.
  function renderList(dashboards) {
    const job = state.assist.job;
    const rows = dashboards.map((d) => {
      const status = statusOf(d);
      const edited = working(job) && (job.slug === d.slug || job.key === d.slug);
      // With several databases, the line under the name says which one; with one, how many cards.
      const lead = severalDatabases() && d.database_name ? d.database_name : plural(d.cards, "card", "cards");
      const stands = edited ? "Claude is working" : d.problems ? `${d.problems} to fix` : status.text;
      return { slug: d.slug, name: d.name, sub: `${lead} · ${stands}`, kind: edited ? "busy" : status.kind,
        current: state.page === "dashboards" && d.slug === state.slug && state.mode === "edit" };
    });
    const list = $("list");
    drawIfChanged(list, JSON.stringify(rows), () => {
      const on = focusedKey(list);
      list.replaceChildren(...rows.map((row) => h(
        "button",
        { class: "nav-item", type: "button", "aria-current": String(row.current), title: row.name, "data-key": row.slug, onclick: () => select(row.slug) },
        icon("chart", 17),
        h("span", { class: "nav-text" }, h("span", { class: "name" }, row.name), h("span", { class: "sub" }, row.sub)),
        h("i", { class: `dot ${row.kind}`, "aria-hidden": "true" })
      )));
      if (on) focusFirst(keyed(list, on));
    });
  }

  function renderDrafts(job) {
    const drafts = state.drafts;
    $("draftsLabel").hidden = !drafts.length;
    $("drafts").hidden = !drafts.length;
    const rows = drafts.map((d) => {
      const busyHere = working(job) && job.key === d.key;
      const stands = busyHere ? (job.kind === "build" ? "Claude is building" : "Claude is planning")
        : d.last === "failed" ? "Plan failed" : d.last === "stopped" ? "Stopped" : d.plan ? "Plan ready" : "No plan yet";
      const lead = severalDatabases() && d.database_name ? `${d.database_name} · ` : "";
      // Worked out at every look, so "just now" turns into the time by itself.
      return { key: d.key, title: d.title, sub: `${lead}${stands}${d.at ? ` · ${studioTime.inline(d.at)}` : ""}`,
        kind: busyHere ? "busy" : d.last === "failed" ? "bad" : d.plan && !d.last ? "warn" : "",
        current: state.page === "new" && state.draft === d.key };
    });
    const box = $("drafts");
    drawIfChanged(box, JSON.stringify(rows), () => {
      const on = focusedKey(box);
      box.replaceChildren(...rows.map((row) => {
        const open = () => {
          state.draft = row.key;
          showPage("new");
          poll();
        };
        return h("button", { class: "nav-item", type: "button", "aria-current": String(row.current), title: row.title, "data-key": row.key, onclick: open },
          icon("sparkle", 17),
          h("span", { class: "nav-text" }, h("span", { class: "name" }, row.title), h("span", { class: "sub" }, row.sub)),
          h("i", { class: `dot ${row.kind}`, "aria-hidden": "true" }));
      }));
      if (on) focusFirst(keyed(box, on));
    });
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
    state.lastSlug = slug;
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
    if (slug !== state.slug) flash("");
    if (state.page !== "dashboards") showPage("dashboards");
    state.mode = "edit";
    holdBox(slug);   // at once: what is typed from now on is for this dashboard
    history.replaceState(null, "", `#${encodeURIComponent(slug)}`);
    poll();
  }

  // Opens a dashboard and waits (a few seconds at most) until it is the one shown.
  async function openSlug(slug) {
    select(slug);
    for (let i = 0; i < 60 && state.slug !== slug; i += 1) await new Promise((done) => setTimeout(done, 50));
  }

  // ---------- Ask Claude ----------

  const DISPLAY_WORDS = {
    scalar: "number", smartscalar: "number with trend", line: "line chart", bar: "bar chart", area: "area chart",
    combo: "bars and line", row: "ranking", pie: "pie", table: "table",
  };

  // The conversation in the panel: the open draft on the New dashboard screen, else the open dashboard's.
  function assistKey() {
    return state.mode === "new" ? state.draft : state.slug;
  }

  // The conversation the box writes to: a dashboard's slug, "~<draft key>", "~new" (a draft not begun), or null.
  function convKey() {
    if (state.page === "new") return `~${state.draft || "new"}`;
    return state.page === "dashboards" ? state.slug : null;
  }

  function openDraft() {
    return state.mode === "new" && state.draft ? state.drafts.find((d) => d.key === state.draft) : null;
  }

  function busy() {
    return working(state.assist.job);
  }

  // The chat in sight: the New dashboard screen, or a dashboard's with its panel (or drawer) open.
  function chatInSight() {
    return state.page === "new" || (state.page === "dashboards" && !!state.slug && assistShown());
  }

  function focusBox() {
    return focusFirst($("askText"));
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
      h("div", null, h("div", { class: "plan-name" }, plan.name), plan.description ? h("div", { class: "plan-desc" }, plan.description) : null,
        severalDatabases() && message.database_name ? h("div", { class: "plan-db" }, icon("table", 13), `On the database ${message.database_name}`) : null),
      section("Cards", plan.cards, (c) =>
        h("li", null, c.name, h("span", null, ` · ${DISPLAY_WORDS[c.display] || c.display}${c.shows ? ` · ${c.shows}` : ""}`))),
      section("Will read", plan.reads, (r) =>
        h("li", null, r.table, h("span", null, `${r.size ? ` (${r.size})` : ""}${r.filter ? ` · ${r.filter}` : ""}`))),
      section("Assumed", plan.assumptions, (text) => h("li", null, text)),
      section("Personal data", plan.sensitive, (text) => h("li", null, text, h("span", null, " · hidden until you choose to show it"))),
      section("Left out", plan.left_out, (text) => h("li", null, text)),
      h(
        "div",
        { class: "plan-foot" },
        h("span", { class: "assist-hint" }, note),
        latest && !message.built && !busy()
          ? h("button", { class: "btn primary small", type: "button", onclick: (event) => build(message.id, event.currentTarget) },
              icon("check", 14), "Build this dashboard")
          : null
      )
    );
  }

  function messageEl(message, latestPlan) {
    if (message.role === "user") {
      // A sent file opens (a picture or a PDF) or downloads from the studio, through the link the server gives it.
      const files = attach && message.files && message.files.length
        ? h("div", { class: "msg-files", role: "list", "aria-label": "Attached files" }, message.files.map((f) => attach.chip(f, { href: f.href, view: f.view })))
        : null;
      return h("div", { class: "msg user", "data-id": message.id }, h("div", { class: "bubble" }, message.text), files);
    }
    if (message.role === "info") return h("div", { class: "msg info", "data-id": message.id }, message.text);
    const meta = message.meta ? h("div", { class: "msg-meta" }, message.meta) : null;
    if (message.role === "error") return h("div", { class: "msg error", "data-id": message.id }, icon("alert", 16), h("div", null, message.text, meta));
    // The head line, as in the owner's other studios: who, when, and Copy for the reply's words.
    const copy = message.text ? copyButton(message.text) : null;
    if (copy) {
      copy.classList.add("ghost");
      copy.setAttribute("aria-label", "Copy this reply from Claude");
    }
    return h(
      "div",
      { class: "msg claude", "data-id": message.id },
      h("div", { class: "msg-who" }, icon("sparkle", 14), message.role === "plan" ? "Claude’s plan" : "Claude",
        message.at ? studioTime.tag(message.at) : null, copy),
      message.text ? h("div", { class: "msg-text" }, message.text) : null,
      message.role === "plan" ? planEl(message, latestPlan) : null,
      meta
    );
  }

  function suggestion(text) {
    const use = () => {
      $("askText").value = text;
      unrefuse();
      $("askText").focus();
    };
    return h("button", { class: "suggest", type: "button", onclick: use }, text);
  }

  function emptyHint() {
    const fresh = state.mode === "new";
    if (fresh && noDatabase()) {
      return h("div", { class: "assist-empty" },
        h("b", null, "Connect a database first"),
        "A new dashboard is planned on one of your databases. Add one in Settings and read its table list, then come back here.",
        h("div", null, h("button", { class: "btn primary", type: "button", onclick: () => go("settings") }, icon("settings", 15), "Open Settings")));
    }
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

  // The conversation is a log a screen reader follows (#messages, role=log): drawn a message at a time, so what is new
  // is said and nothing else; another conversation, or this one cleared, is drawn afresh in a log of its own.
  const log = { key: undefined, ids: [], keys: [] };

  function renderMessages() {
    const messages = state.assist.messages;
    const key = state.assist.key;
    const emptyBox = $("assistEmpty");
    emptyBox.hidden = messages.length > 0;
    if (!messages.length) {
      drawIfChanged(emptyBox, JSON.stringify([state.mode, state.mode === "new" && noDatabase()]), () => emptyBox.replaceChildren(emptyHint()));
    }
    const roles = messages.map((m) => m.role);
    const lastPlan = roles.lastIndexOf("plan");
    const latest = lastPlan > roles.lastIndexOf("user") ? lastPlan : -1;
    const running = busy();
    const ids = messages.map((m) => m.id);
    const keys = messages.map((m, i) => JSON.stringify([m, i === latest, m.role === "plan" && running, !!attach, severalDatabases()]));
    const box = $("messages");
    const same = log.key === key && log.ids.length <= ids.length && log.ids.every((id, i) => id === ids[i]);
    if (same) {
      const nodes = [...box.children];
      log.keys.forEach((k, i) => {
        if (k !== keys[i] && nodes[i]) nodes[i].replaceWith(messageEl(messages[i], i === latest));
      });
      box.append(...messages.slice(log.ids.length).map((m, j) => messageEl(m, log.ids.length + j === latest)));
    } else {
      const fresh = h("div", { id: "messages", role: "log", "aria-label": box.getAttribute("aria-label"), "aria-relevant": "additions" });
      fresh.append(...messages.map((m, i) => messageEl(m, i === latest)));
      box.replaceWith(fresh);
    }
    Object.assign(log, { key, ids, keys });
  }

  // On the New dashboard screen, with more than one database, the user says which one the dashboard is for.
  function renderDatabaseChoice() {
    const choosing = state.mode === "new" && severalDatabases();
    $("askDb").hidden = !choosing;
    if (!choosing) return;
    const ready = state.databases.filter((d) => d.ready);
    if (!ready.some((d) => d.id === state.newDb)) state.newDb = ready.length ? ready[0].id : null;
    // A draft stays on the database it began on.
    const draft = openDraft();
    const kept = draft && state.databases.some((d) => d.id === draft.database) ? draft.database : null;
    const value = kept || state.newDb;
    const select = $("askDbSelect");
    // Drawn again only when something changed, so an open list is not closed under the user's hand.
    const shown = JSON.stringify([state.databases, value]);
    if (select.dataset.shown !== shown) {
      select.dataset.shown = shown;
      select.replaceChildren(...state.databases.map((d) =>
        h("option", { value: d.id, selected: d.id === value, disabled: !d.ready }, d.ready ? d.name : `${d.name} (read its table list in Settings first)`)));
    }
    select.disabled = busy() || !!kept;
    select.title = kept ? "This draft was begun on this database." : "";
  }

  // The panel's heading: an h2 beside a dashboard, the page's h1 on the New dashboard screen.
  function setAssistHeading(text) {
    const tag = state.page === "new" ? "H1" : "H2";
    let heading = $("assistTitle");
    if (heading.tagName !== tag) {
      const next = document.createElement(tag);
      next.id = "assistTitle";
      next.className = "assist-title";
      heading.replaceWith(next);
      heading = next;
    }
    setText(heading, text);
  }

  function renderComposer() {
    const job = state.assist.job;
    const running = busy();
    renderDatabaseChoice();
    const fresh = state.mode === "new";
    const here = running && !!assistKey() && job.key === assistKey();
    const noDb = fresh && noDatabase();
    const shut = state.offline || !heldFor || (state.mode === "edit" && !state.slug);
    const box = $("askText");
    box.readOnly = sending;
    $("sendBtn").setAttribute("aria-disabled", String(running || shut || sending || noDb));
    $("attachBtn").setAttribute("aria-disabled", String(shut || noDb));
    $("stopBtn").hidden = !running;
    setText($("stopBtn").querySelector(".label"), running && !here && job.name ? `Stop ${job.name}` : "Stop");
    const placeholder = running ? "Write your next request: it can be sent once Claude is done…"
      : fresh ? "Describe the dashboard you want…" : "Ask for a change to this dashboard…";
    if (box.placeholder !== placeholder) box.placeholder = placeholder;
    let hint = fresh ? "Plan first. Nothing is read until you press Build." : "Changes run directly.";
    if (state.offline) hint = "The studio isn't answering. Trying again…";
    else if (noDb) hint = "Connect a database first, in Settings.";
    else if (running) hint = here ? "Claude is working." : `Claude is working on ${job.name || "another request"}.`;
    setText($("askHint"), hint);
    const planFirst = "Claude shows a plan first. Nothing is read from the database until you press Build.";
    setText($("assistContext"), fresh ? planFirst : state.spec ? `Changing: ${state.spec.name}` : "");
    const draft = openDraft();
    const heading = fresh ? (draft ? draft.title : "New dashboard") : "Ask Claude";
    setAssistHeading(heading);
    if (state.page === "new") setTitle(`${heading} · ${APP}`);
    $("newDash").setAttribute("aria-current", String(state.page === "new" && !state.draft));
    const clear = $("assistClear");
    clear.hidden = !state.assist.messages.length || here;
    const name = fresh ? "Remove this draft…" : "Clear this conversation…";
    if (clear.getAttribute("aria-label") !== name) {
      clear.setAttribute("aria-label", name);
      clear.title = fresh ? "Remove this draft. It moves to the Trash." : "Clear this conversation. It moves to the Trash; the dashboard itself is not changed.";
      clear.replaceChildren(icon(fresh ? "trash" : "chat", 15));
    }
  }

  async function updateAssist(assistant) {
    const a = state.assist;
    const job = assistant.job;
    if (a.followed === undefined) a.followed = job && job.status !== "running" ? job.id : null;
    a.job = job;
    // A finished build opens its dashboard, once, when the owner is on its draft; elsewhere it is said, and it is in the
    // sidebar.
    if (job && job.kind === "build" && job.status === "done" && job.slug && a.followed !== job.id) {
      a.followed = job.id;
      if (state.page === "new" && state.draft === job.key) {
        state.draft = null;
        showPage("dashboards");
        state.mode = "edit";
        history.replaceState(null, "", `#${encodeURIComponent(job.slug)}`);
        a.again = true;
      } else {
        announce(`${job.name || "The new dashboard"} is built. It is in the sidebar.`);
      }
    }
    holdBox(convKey());
    const key = assistKey();
    const thread = $("thread");
    const atBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 60;
    const changed = assistant.rev !== a.rev || key !== a.key;
    if (changed) {
      const reply = key ? await api(`/api/thread?key=${encodeURIComponent(key)}`) : {};
      a.rev = assistant.rev;
      a.key = key;
      a.messages = reply.messages || [];
      renderMessages();
    }
    renderComposer();
    if (changed || atBottom) thread.scrollTop = thread.scrollHeight;
  }

  // Claude's steps while it works (#activity, in place) and Try again or Carry on after a run that did not finish
  // (#assistOffer): static/progress.js draws both, after every look at the studio.
  function drawProgress() {
    if (!window.studioProgress) return;
    const key = assistKey();
    try {
      window.studioProgress.draw({ activity: $("activity"), offer: $("assistOffer") },
        { job: state.assist.job, key, messages: key === state.assist.key ? state.assist.messages : [], inSight: chatInSight(),
          mode: state.mode, draft: openDraft() }, ui);
    } catch (error) {
      console.error(error);
    }
  }

  // ---------- the box: kept per conversation ----------

  const held = new Map();   // a conversation (convKey) -> {text, files}: what was being written for it while another is shown
  let heldFor;              // the conversation whose request the box holds now
  let sending = false;

  // Another conversation shown: what was written in the box, and the files attached to it, are kept for the one they
  // were meant for, and the one shown gets back its own. Never attached.clear() here: it empties the kept list in place.
  function holdBox(to) {
    if (heldFor === to) return;
    if (heldFor) held.set(heldFor, { text: $("askText").value, files: attached ? attached.files : [] });
    const next = (to && held.get(to)) || { text: "", files: [] };
    $("askText").value = next.text;
    if (attached) attached.set(next.files);
    heldFor = to;
    unrefuse();
  }

  function refuse(text) {
    $("askRefused").textContent = text;
    $("askRefused").hidden = false;
    $("askHint").hidden = true;
    announce(text, { urgent: true });
  }

  function unrefuse() {
    if ($("askRefused").hidden) return;
    $("askRefused").hidden = true;
    $("askRefused").textContent = "";
    $("askHint").hidden = false;
  }

  function newDraftKey() {
    const bytes = crypto.getRandomValues(new Uint8Array(6));
    return `draft-${[...bytes].map((b) => b.toString(16).padStart(2, "0")).join("")}`;
  }

  // What /api/ask takes for a conversation's next request.
  function askBody(conv, text, files) {
    const fresh = conv.startsWith("~");
    return { mode: fresh ? "new" : "edit", slug: fresh ? null : conv, text, files,
      database: fresh && severalDatabases() ? state.newDb : undefined, draft: fresh ? conv.slice(1) : undefined };
  }

  function busyWords(conv) {
    const job = state.assist.job;
    const mine = job.key === (conv.startsWith("~") ? conv.slice(1) : conv);
    return mine ? "Claude is still working on the last request. What you wrote stays here: send it once Claude is done."
      : `Claude is working on ${job.name || "another request"}. What you wrote stays here: send it once Claude is done.`;
  }

  async function send(event) {
    event.preventDefault();
    if (sending) return;
    const conv = heldFor;
    const box = $("askText");
    const text = box.value.trim();
    if (state.offline) return refuse("The studio isn't answering. What you wrote stays here.");
    if (!conv) return;
    if (conv.startsWith("~") && noDatabase()) return refuse("Connect a database first: add one in Settings and read its table list.");
    if (!text) return refuse("Say what you want first.");
    if (busy()) return refuse(busyWords(conv));
    // The first message begins a draft, which the sidebar lists from now on: the box's words are that draft's.
    const begun = conv === "~new";
    const sentFor = begun ? `~${newDraftKey()}` : conv;
    if (begun) {
      state.draft = sentFor.slice(1);
      heldFor = sentFor;
      history.replaceState(null, "", `#${sentFor}`);
    }
    const kept = $("askForm").contains(document.activeElement);
    sending = true;
    box.readOnly = true;   // what is being sent stays as it is, and keeps the focus
    box.setAttribute("aria-busy", "true");
    renderComposer();
    let reply;
    try {
      reply = await whileBusy($("sendBtn"), () => post("/api/ask", askBody(sentFor, text, attached ? attached.payload() : [])));
    } finally {
      sending = false;
      box.readOnly = false;
      box.removeAttribute("aria-busy");
    }
    if (reply.error) {
      refuse(reply.error);
      if (begun && heldFor === sentFor) {
        // The draft did not begin: the words are the New dashboard screen's again.
        state.draft = null;
        heldFor = "~new";
        history.replaceState(null, "", "#~new");
      } else if (begun && held.has(sentFor)) {
        held.set("~new", held.get(sentFor));
        held.delete(sentFor);
      }
    } else {
      unrefuse();
      held.delete(sentFor);
      if (heldFor === sentFor) {   // still shown: emptied (another conversation shown meanwhile keeps its own)
        box.value = "";
        if (attached) attached.clear();
      }
    }
    await poll();
    if (kept) focusBox();
  }

  // The shown conversation's next request, by the same path as the box, with no files (Try again, Carry on).
  async function ask(text, button) {
    const conv = convKey();
    if (!conv || conv === "~new") return { error: "There is no conversation to send it to." };
    if (busy()) {
      refuse(busyWords(conv));
      return { error: busyWords(conv) };
    }
    const run = () => post("/api/ask", askBody(conv, text, []));
    const reply = button ? await whileBusy(button, run) : await run();
    if (reply.error) refuse(reply.error);
    else unrefuse();
    await poll();
    focusBox();
    return reply;
  }

  async function build(planId, button) {
    const run = () => post("/api/build", { plan: planId, draft: state.draft });
    const reply = button ? await whileBusy(button, run) : await run();
    if (reply.error) refuse(reply.error);
    else unrefuse();
    await poll();
    focusBox();
    return reply;
  }

  // ---------- the panel, and the drawer it becomes on a laptop ----------

  // From 900 to 1180 px the panel is a drawer over the dashboard's right side: closed at load, opened by Ask Claude.
  const drawerMedia = window.matchMedia("(min-width: 900px) and (max-width: 1180px)");
  let drawerOpen = false;

  function assistShown() {
    return drawerMedia.matches ? drawerOpen : !$("app").classList.contains("no-assist");
  }

  function layoutAssist() {
    const drawer = drawerMedia.matches;
    $("app").classList.toggle("drawer-open", drawer && drawerOpen);
    $("showAssist").hidden = !drawer && assistShown();
    if (drawer) $("showAssist").setAttribute("aria-expanded", String(drawerOpen));
    else $("showAssist").removeAttribute("aria-expanded");
  }

  function showAssist(show, { focus = false } = {}) {
    if (drawerMedia.matches) {
      drawerOpen = show;
    } else {
      $("app").classList.toggle("no-assist", !show);
      try {
        localStorage.setItem("assist", show ? "1" : "0");
      } catch {}
    }
    layoutAssist();
    if (focus) focusFirst(show ? $("askText") : $("showAssist"));   // never left on a button that is gone
    drawProgress();
  }

  // ---------- polling ----------

  let polling = false;
  let timer = 0;

  // After a reload, or a link followed: the Trash, Settings, or the draft (or New dashboard screen) that was open.
  function reopen() {
    const mark = decodeURIComponent(location.hash.slice(1));
    if (mark === "~trash" || mark === "~settings") return void showPage(mark.slice(1));
    if (!mark.startsWith("~")) return;
    const key = mark.slice(1);
    if (key !== "new" && !state.drafts.some((d) => d.key === key)) return;
    state.draft = key === "new" ? null : key;
    showPage("new");
  }

  function showOffline(message) {
    const news = !state.offline || $("notice").textContent !== message;
    const was = state.offline;
    state.offline = true;
    if (!news) return;
    renderNotice(message, []);
    announce(message, { urgent: true });
    if (!was) {
      renderActions();
      for (const key of elements.keys()) renderCard(key);
    }
    renderComposer();
  }

  function backOnline() {
    if (!state.offline) return;
    state.offline = false;
    const problems = state.spec ? state.spec.problems : [];
    renderNotice(problems.length ? "This dashboard's files need fixing" : "", problems);
    renderActions();
    for (const key of elements.keys()) renderCard(key);
    announce("The studio is answering again.");
  }

  // The empty page offers the Trash when a removed dashboard is there to put back.
  let emptyAsked = false;
  async function offerTrash() {
    if (emptyAsked) return;
    emptyAsked = true;
    let reply = null;
    try {
      reply = await api("/api/trash");
    } catch {
      emptyAsked = false;
    }
    $("emptyTrash").hidden = !(reply && Array.isArray(reply.entries) && reply.entries.some((e) => e.kind === "dashboard"));
  }

  async function look(data) {
    const firstLook = !state.setup;
    state.setup = data.setup;
    state.databases = data.databases || [];
    state.drafts = data.drafts || [];
    state.assist.job = data.assistant.job;
    if (firstLook && !data.setup.database) showPage("settings");
    else if (firstLook) reopen();
    // A draft that was in the list and no longer is has been built or removed. One just begun may not be in it yet:
    // this answer can have left the server before the first message arrived. One just built stays open until
    // updateAssist has opened its dashboard.
    const job = data.assistant.job;
    const built = !!job && job.kind === "build" && job.status === "done" && job.key === state.draft && state.assist.followed !== job.id;
    if (state.draft && state.drafts.some((d) => d.key === state.draft)) {
      state.listed = state.draft;
    } else if (state.draft && state.listed === state.draft && !built) {
      state.draft = null;
      if (state.page === "new") history.replaceState(null, "", "#~new");
    }
    renderDrafts(data.assistant.job);
    if (state.page === "settings" || state.page === "trash") return void renderList(data.dashboards);
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
      if (!state.offline) renderNotice("", []);
      if ($("empty").hidden) setTitle(APP);
      $("empty").hidden = false;
      offerTrash();
    } else {
      $("app").classList.remove("no-dashboards");
      emptyAsked = false;
      const current = data.dashboards.find((d) => d.slug === slug);
      if (slug !== state.slug || !state.spec || current.version !== state.spec.version) await loadDashboard(slug);
      renderList(data.dashboards);
    }
    await updateAssist(data.assistant);
  }

  async function poll() {
    // Asked for while one is under way: look again as soon as it ends, since its answer may predate what just happened.
    if (polling) return void (state.assist.again = true);
    polling = true;
    clearTimeout(timer);
    try {
      let data;
      try {
        data = await api("/api/state");
      } catch {
        return void showOffline("The studio isn't answering. Trying again…");
      }
      // Only a window whose session has ended (a 401) is sent to the shortcut.
      if (!data.dashboards) return void showOffline("This window is no longer connected. Open the studio again from its shortcut.");
      if (!state.answered) {
        state.answered = true;
        renderActions();
        for (const key of elements.keys()) renderCard(key);
      }
      backOnline();
      await look(data);
      drawProgress();
    } catch (error) {
      console.error(error);
    } finally {
      retitle();
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

  async function openGoLive() {
    const slug = state.slug;
    const button = $("goLive");
    if (!slug || isOff(button)) return;
    await whileBusy(button, async () => {
      const token = showDialog(h("h2", null, "Go live"), h("p", { class: "live-soft" }, "Checking with Metabase…"), liveFooter(closeButton("Cancel")));
      const reply = await post("/api/golive/plan", { slug });
      if (!ownsDialog(token)) return;   // cancelled, or another dialog in its place: this answer is not wanted now
      if (!reply.plan) {
        return void showDialog(h("h2", null, "Go live"), liveList("Go live is not possible", [reply.error || "Unknown error."], "stop"), liveFooter(closeButton("Close")));
      }
      showPlan(slug, reply.plan, token);
    });
  }

  function showPlan(slug, plan, token) {
    const where = plan.target;
    const counts = [];
    if (plan.cards.create) counts.push(`${plan.cards.create} new`);
    if (plan.cards.update) counts.push(`${plan.cards.update} updated`);
    if (plan.cards.trash) counts.push(`${plan.cards.trash} to Metabase’s trash`);
    const blocked = plan.blockers.length > 0;
    // Metabase runs each query as it is written, so personal data hidden here is shown there. The user says yes to that.
    const sensitive = blocked ? [] : plan.sensitive || [];
    const agree = sensitive.length ? h("input", { type: "checkbox" }) : null;
    const go = h("button", { class: "btn primary", type: "button", disabled: blocked || !!agree }, "Go live");
    go.addEventListener("click", () => publish(slug, plan.name, !!agree, go, token));
    if (agree) agree.addEventListener("change", () => { go.disabled = !agree.checked; });
    showDialog(
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
            h("dd", null, where.studio_database && where.studio_database !== where.database ? `${where.studio_database} (in Metabase: ${where.database})` : where.database),
            h("dt", null, "Cards"),
            h("dd", null, counts.join(", ") || "none")
          )
        : null,
      liveList("Fix this first", plan.blockers, "stop"),
      liveList("Before you confirm", plan.warnings, "warn"),
      agree
        ? h("div", { class: "live-box warn" }, h("strong", null, "This dashboard shows personal data"),
            h("ul", null, sensitive.map((s) => h("li", null, `“${s.card}”: ${s.columns.join(", ")}`))),
            h("label", { class: "check" }, agree, "Everyone who can open this dashboard in Metabase will see these values. Publish it with them."))
        : null,
      blocked ? null : h("p", { class: "live-soft live-said" }, "No query runs now. Metabase runs the cards when someone opens the dashboard."),
      liveFooter(closeButton("Cancel"), go)
    );
  }

  // The result goes in the dialog while it is still Go live's; closed meanwhile, it is a word on the page instead.
  async function publish(slug, name, sensitiveOk, button, token) {
    const said = $("liveDialog").querySelector(".live-said");
    if (said) said.textContent = "Publishing to Metabase…";
    const reply = await whileBusy(button, () => post("/api/golive", { slug, sensitive_ok: !!sensitiveOk }));
    await poll();
    const mine = ownsDialog(token);
    if (!reply.published) {
      const error = reply.error || "Unknown error.";
      if (mine) {
        showDialog(h("h2", null, `Go live: ${name}`), liveList("Go live did not finish", [error], "stop"),
          h("p", { class: "live-soft" }, "Anything already created is remembered, so trying again continues and does not make a copy."),
          liveFooter(closeButton("Close")));
      } else {
        flash(`Go live of ${name} did not finish: ${error}`, null, "bad");
      }
      return void announce(`Go live of ${name} did not finish: ${error}`, { urgent: true });
    }
    const open = () => h("button", { class: "btn small", type: "button", onclick: () => openInMetabase(slug) }, icon("external", 14), "Open in Metabase");
    if (mine) {
      showDialog(
        h("h2", null, `${name} is live`),
        h("p", null, reply.published.mode === "update" ? "The dashboard in Metabase was updated." : `The dashboard was created in “${reply.published.target.collection}”.`),
        h("p", { class: "live-link" }, reply.published.url),
        liveFooter(closeButton("Close"), h("button", { class: "btn primary", type: "button", onclick: () => openInMetabase(slug) }, "Open in Metabase"))
      );
    } else {
      flash(`${name} is live in Metabase.`, open());
    }
    announce(`${name} is live in Metabase.`);
  }

  async function openInMetabase(slug) {
    const reply = await post("/api/open", { slug });
    if (reply.error) {
      flash(reply.error, null, "bad");
      announce(reply.error, { urgent: true });
    }
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
  window.addEventListener("focus", () => {
    checkLive();
    setTimeout(retitle, 0);   // a run's Ready or Needs you goes once the window is looked at
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) checkLive();
  });

  // ---------- remove, and Put back ----------

  function askRemove(spec) {
    const shown = spec || state.spec;
    if (!shown) return;
    const title = h("h2", null, `Remove ${shown.name}?`);
    const stillLive = shown.live && shown.metabase !== "trashed" && shown.metabase !== "gone";
    const remove = h("button", { class: "btn danger", type: "button" }, "Remove");
    remove.addEventListener("click", () => removeDashboard(shown, remove, title));
    showDialog(
      title,
      h("p", null, "It leaves the studio and moves to the Trash, beside Settings, with its conversation. You can put it back from there."),
      stillLive ? h("p", { class: "live-soft" }, "It is live in Metabase and stays there. Remove it in Metabase yourself if it should go.") : null,
      liveFooter(closeButton("Cancel"), remove)
    );
  }

  async function removeDashboard(spec, button, title) {
    const reply = await whileBusy(button, () => post("/api/remove", { slug: spec.slug }));
    if (reply.error) return void showDialog(title, liveList("It was not removed", [reply.error], "stop"), liveFooter(closeButton("Close")));
    closeDialog();
    held.delete(spec.slug);
    if (state.slug === spec.slug) {
      state.slug = null;
      state.spec = null;
      history.replaceState(null, "", "#");
    }
    const name = reply.name || spec.name;
    flash(`${name} moved to the Trash.`, reply.key ? putBack(reply.key, name) : trashButton());
    announce(`${name} moved to the Trash.`);
    await poll();
    focusFirst($("top").hidden ? $("emptyTitle") : $("title"));   // the dashboard now shown, or the empty page
  }

  function trashButton() {
    return h("button", { class: "btn small", type: "button", onclick: () => go("trash") }, "Open the Trash");
  }

  function putBack(key, name) {
    const button = h("button", { class: "btn small", type: "button", "aria-label": `Put back ${name}` }, icon("history", 14), "Put back");
    button.addEventListener("click", async () => {
      const reply = await whileBusy(button, () => post("/api/trash/back", { key }));
      if (reply.error) {
        flash(`${name} could not be put back: ${reply.error}`, trashButton(), "bad");
        return void announce(`${name} could not be put back: ${reply.error}`, { urgent: true });
      }
      flash("");
      announce(`${reply.name || name} is back.`);
      if (reply.slug) await openSlug(reply.slug);
      else await poll();
      focusFirst($("title"));
    });
    return button;
  }

  // ---------- personal data ----------

  async function setShown(card, columns, show) {
    const slug = state.slug;
    const reply = await post("/api/show", { slug, key: card.key, columns, show });
    if (!reply.error && state.slug === slug) await loadDashboard(slug).catch(() => {});
    return reply;
  }

  // Hidden personal data is shown only on the user's own word, given here.
  function askShow(card, columns) {
    const title = h("h2", null, "Show personal data?");
    const boxes = columns.map((name) => h("input", { type: "checkbox", checked: true, value: name }));
    const show = h("button", { class: "btn primary", type: "button" }, "Show values");
    show.addEventListener("click", async () => {
      const picked = boxes.filter((box) => box.checked).map((box) => box.value);
      if (!picked.length) return void closeDialog();
      const reply = await whileBusy(show, () => setShown(card, picked, true));
      if (reply.error) return void showDialog(title, liveList("Nothing was changed", [reply.error], "stop"), liveFooter(closeButton("Close")));
      closeDialog();
      if (elements.get(card.key)) focusTool(elements.get(card.key), "hide");
    });
    showDialog(
      title,
      h("p", null, `“${card.name}” has ${columns.length === 1 ? "a column that looks" : "columns that look"} like personal data. The values stay hidden unless you say otherwise.`),
      boxes.map((box) => h("label", { class: "check" }, box, box.value)),
      liveList("If you show them", [
        "They appear in this card, and stay shown until you hide them again.",
        "The card's query runs once more.",
        "Claude still gets *** in their place, so the values are not sent to it.",
      ], "warn"),
      liveFooter(closeButton("Keep hidden"), show)
    );
  }

  // ---------- clearing a conversation, removing a draft ----------

  function askClear() {
    const key = assistKey();
    const spec = state.spec;
    const messages = state.assist.messages;
    if (!key || !spec || state.assist.key !== key) return;
    const files = messages.reduce((n, m) => n + (m.files || []).length, 0);
    const parts = [plural(messages.length, "message", "messages")];
    if (files) parts.push(plural(files, "attached file", "attached files"));
    const title = h("h2", null, `Clear the conversation about ${spec.name}?`);
    const clear = h("button", { class: "btn danger", type: "button" }, "Clear");
    clear.addEventListener("click", async () => {
      const reply = await whileBusy(clear, () => post("/api/clear", { key }));
      if (reply.error) return void showDialog(title, liveList("It was not cleared", [reply.error], "stop"), liveFooter(closeButton("Close")));
      closeDialog();
      await poll();
      focusBox();   // the button goes with the conversation
      announce(`The conversation about ${spec.name} moved to the Trash.`);
    });
    showDialog(title,
      h("p", null, `Its ${parts.join(" and ")} ${messages.length === 1 && !files ? "moves" : "move"} to the Trash. The dashboard is not changed.`),
      liveFooter(closeButton("Cancel"), clear));
  }

  function askRemoveDraft() {
    const draft = openDraft();
    if (!draft) return;
    const title = h("h2", null, `Remove the draft “${draft.title}”?`);
    const remove = h("button", { class: "btn danger", type: "button" }, "Remove");
    remove.addEventListener("click", async () => {
      const reply = await whileBusy(remove, () => post("/api/draft/remove", { key: draft.key }));
      if (reply.error) return void showDialog(title, liveList("It was not removed", [reply.error], "stop"), liveFooter(closeButton("Close")));
      closeDialog();
      held.delete(`~${draft.key}`);
      await poll();
      focusBox();
      announce(`The draft “${draft.title}” moved to the Trash.`);
    });
    showDialog(
      title,
      h("p", null, "It leaves the sidebar and moves to the Trash, beside Settings, with its conversation. You can put it back from there."),
      liveFooter(closeButton("Cancel"), remove)
    );
  }

  // ---------- settings, the Trash and the theme ----------

  function themeChanged() {
    readTheme();
    for (const key of elements.keys()) renderCard(key);
    const dark = document.documentElement.dataset.theme === "dark";
    const name = dark ? "Switch to light" : "Switch to dark";
    $("themeBtn").replaceChildren(icon(dark ? "sun" : "moon", 15));
    $("themeBtn").setAttribute("aria-label", name);
    $("themeBtn").title = name;
  }

  function setTheme(choice) {
    window.studioTheme.set(choice);
    themeChanged();
  }

  function appearanceSection() {
    const choices = [["auto", "Match the system"], ["light", "Light"], ["dark", "Dark"]];
    const row = h("div", { class: "segmented" });
    const draw = () => {
      const on = focusedKey(row);
      row.replaceChildren(...choices.map(([value, label]) =>
        h("button", { class: "btn", type: "button", "aria-pressed": String(window.studioTheme.get() === value), "data-key": value,
          onclick: () => { setTheme(value); draw(); } }, label)));
      if (on) focusFirst(keyed(row, on));
    };
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
    const onTrash = page === "trash";
    const onNew = page === "new";
    // A new dashboard has its own screen: the conversation fills the page until the dashboard exists.
    state.mode = onNew ? "new" : "edit";
    if (onNew) history.replaceState(null, "", `#~${state.draft || "new"}`);
    else if (onSettings || onTrash) history.replaceState(null, "", `#~${page}`);
    else if (location.hash.startsWith("#~")) history.replaceState(null, "", state.lastSlug ? `#${encodeURIComponent(state.lastSlug)}` : "#");
    drawerOpen = false;
    $("app").classList.toggle("on-new", onNew);
    $("app").classList.toggle("on-settings", onSettings);
    $("app").classList.toggle("on-trash", onTrash);
    layoutAssist();
    $("settingsPage").hidden = !onSettings;
    $("trashPage").hidden = !onTrash;
    $("dashPage").hidden = onSettings || onTrash;
    $("openSettings").setAttribute("aria-current", String(onSettings));
    $("openTrash").setAttribute("aria-current", String(onTrash));
    // Coming back, the dashboard is drawn afresh: its cards were hidden and may have changed size.
    state.slug = null;
    state.spec = null;
    holdBox(convKey());
    renderComposer();
    if (!onSettings && !onTrash) return;
    $("top").hidden = false;
    $("title").textContent = onTrash ? "Trash" : state.setup && !state.setup.database ? "Set up" : "Settings";
    $("title").title = "";
    clearStatus();
    setTitle(`${$("title").textContent} · ${APP}`);
    if (onTrash) {
      if (window.studioTrash) window.studioTrash.render(ui, $("trashPage"));
      else $("trashPage").replaceChildren(h("p", { class: "field-hint" }, "The Trash could not be shown just now."));
      return;
    }
    const done = () => {
      showPage("dashboards");
      poll();
    };
    window.studioSettings.render({ ...ui, onDone: done }, $("settingsPage"), appearanceSection());
  }

  // Where the page goes: "trash", "settings", "new" (a new dashboard) or a dashboard's slug.
  function go(target) {
    if (target === "trash" || target === "settings") {
      showPage(target);
      return void poll();
    }
    if (target === "new") {
      state.draft = null;
      showPage("new");
      poll();
      return void focusBox();
    }
    if (target) select(target);
  }

  // What the other parts of the page (static/settings.js, trash.js, versions.js, progress.js) are given to work with.
  const ui = {
    h, icon,
    get: (path) => api(path),
    post,
    dialog: $("liveDialog"),
    showDialog, closeDialog, liveList, liveFooter, closeButton, copyButton,
    announce, whileBusy, focusFirst, drawIfChanged,
    refresh: () => poll(),
    go,
    askRemove,
    ask,
    build,
    retitle,
  };

  $("openSettings").addEventListener("click", () => go("settings"));
  $("openTrash").addEventListener("click", () => go("trash"));
  $("emptyTrash").addEventListener("click", () => go("trash"));
  $("themeBtn").addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => setTimeout(themeChanged, 0));
  themeChanged();

  // The fixed buttons get their icons here, so the markup stays plain.
  for (const [id, name, size] of [
    ["newDash", "plus"], ["emptyNew", "plus"], ["refreshAll", "refresh"], ["openLive", "external"], ["goLive", "upload"],
    ["showAssist", "sparkle"], ["assistMark", "sparkle", 18], ["emptyMark", "chart", 26], ["assistClear", "chat", 15],
    ["hideAssist", "panel", 15], ["sendBtn", "send", 15], ["stopBtn", "stop", 14], ["sideFoot", "lock", 14],
    ["openSettings", "settings", 17], ["openTrash", "trash", 17], ["dashSettings", "gear", 17], ["emptyTrash", "trash", 15],
  ]) {
    $(id).prepend(icon(name, size));
  }
  $("emptyNew").addEventListener("click", () => go("new"));
  $("newDash").addEventListener("click", () => go("new"));

  $("dashSettings").addEventListener("click", () => {
    if (isOff($("dashSettings")) || !state.spec) return;
    if (window.studioDashboard) window.studioDashboard.open(ui, state.spec);
    else askRemove(state.spec);
  });

  $("refreshAll").addEventListener("click", () => {
    if (!state.spec || isOff($("refreshAll"))) return;
    for (const card of state.spec.cards) if (card.sql) enqueue(card.key, true, false);
  });

  $("askForm").addEventListener("submit", send);
  $("askText").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      $("askForm").requestSubmit();
    }
  });
  // Send stays pressable while it waits (aria-disabled): send() then says why it can't go, and keeps the words.
  $("askText").addEventListener("input", unrefuse);
  $("askDbSelect").addEventListener("change", () => {
    state.newDb = $("askDbSelect").value;
    try {
      localStorage.setItem("new-db", state.newDb);
    } catch {}
  });
  try {
    state.newDb = localStorage.getItem("new-db");
  } catch {}
  $("stopBtn").addEventListener("click", async () => {
    const reply = await whileBusy($("stopBtn"), () => post("/api/stop", {}));
    if (reply.error) refuse(reply.error);
    await poll();
    focusBox();   // the button goes once the run has ended
  });
  $("assistClear").addEventListener("click", () => (state.mode === "new" ? askRemoveDraft() : askClear()));
  $("hideAssist").addEventListener("click", () => showAssist(false, { focus: true }));
  $("showAssist").addEventListener("click", () => showAssist(drawerMedia.matches ? !drawerOpen : true, { focus: true }));
  $("assist").addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !drawerMedia.matches || !drawerOpen || $("liveDialog").open) return;
    event.preventDefault();
    showAssist(false, { focus: true });
  });
  drawerMedia.addEventListener("change", () => {
    drawerOpen = false;
    layoutAssist();
  });
  try {
    if (localStorage.getItem("assist") === "0") $("app").classList.add("no-assist");
  } catch {}
  layoutAssist();

  let frame = 0;
  new ResizeObserver(() => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      sizeGrid();
      for (const chart of charts.values()) chart.resize();
    });
  }).observe($("grid"));

  window.addEventListener("hashchange", () => {
    const mark = decodeURIComponent(location.hash.slice(1));
    if (mark && !mark.startsWith("~")) return void select(mark);
    reopen();
    poll();
  });

  // Attachments: the paperclip, files dropped on the panel or pasted, their chips. static/attach.js is shared, byte for
  // byte, with the other studios; it is a module, so it comes in by import().
  import("/static/attach.js").then((module) => {
    attach = module;
    attached = module.attacher({
      button: $("attachBtn"), chips: $("askFiles"), zones: [$("assist")], paste: [$("askForm")],
      blocked: () => isOff($("attachBtn")), announce, onError: refuse,
    });
    renderMessages();
  }).catch(() => {
    $("attachBtn").hidden = true;
  });
  renderActions();
  poll();
  window.studioPalette.start(ui);
})();
