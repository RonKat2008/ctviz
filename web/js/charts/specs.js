// Pure spec builders: response visualization + theme → Vega-Lite / Vega spec. No DOM access, so
// this module also runs under Node for verification. Rows handed to Vega drop citations and
// predicates (they can be MBs); each carries `__i`, the index into `datums(viz)`, so a click on a
// mark maps back to that datum's citations.

import { networkSpec } from "./network.js";

const VL_SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json";
const BAR_SIZE = 18;
const BAR_STEP = 28;
const LOG_RATIO = 200;
const LOG_MIN_MAX = 1000;
const MIN_WIDTH = 220;
const DEFAULT_WIDTH = 640;
const ENROLLMENT_TYPES = ["actual", "estimated", "untyped"];

/** Every clickable datum in chart order: {kind, label, count, citations, predicate, flags, row}. */
export function datums(viz) {
  if (viz.type === "network_graph") {
    const nodes = viz.data.nodes;
    const byId = new Map(nodes.map((n) => [n.id, n.label]));
    return [
      ...nodes.map((n) => ({ kind: "node", label: n.label, count: n.weight, citations: n.citations, predicate: n.predicate, flags: [], row: n })),
      ...viz.data.edges.map((e) => ({
        kind: "edge", label: `${byId.get(e.source)} → ${byId.get(e.target)}`, count: e.weight,
        citations: e.citations, predicate: e.predicate, flags: e.flags || [], row: e,
      })),
    ];
  }
  return (viz.data || []).map((row) => ({
    kind: "row", label: rowLabel(viz, row), count: row.trial_count ?? null,
    citations: row.citations || [], predicate: row.predicate || null, flags: row.flags || [], row,
  }));
}

export function rowLabel(viz, row) {
  switch (viz.type) {
    case "time_series": return row.cohort && viz.encoding.color ? `${row.cohort}, ${row.year}` : String(row.year);
    case "histogram": return String(row.bin_label);
    case "scatter_plot": return String(row.nct_id);
    case "grouped_bar_chart": return `${row.category} · ${row.cohort}`;
    case "metric": return String(row.label ?? "Total");
    case "table": return String(row.nct_id ?? row.category ?? "Row");
    default: return String(row.category ?? "");
  }
}

function light(row, i) {
  const { citations, predicate, cell_fields: _cells, evidence: _e, ...rest } = row;
  return { ...rest, __i: i, __cites: citations ? citations.length : 0 };
}

export function vlConfig(t, compact) {
  const axis = {
    labelFont: t.fontMono, labelFontSize: compact ? 9 : 11, labelColor: t.muted,
    titleFont: t.fontMono, titleFontSize: 11, titleFontWeight: 500, titleColor: t.ink2,
    gridColor: t.grid, gridWidth: 1, domainColor: t.baseline, tickColor: t.baseline, labelPadding: 4, titlePadding: 8,
  };
  return {
    background: null,
    font: t.fontMono,
    view: { stroke: null },
    axis,
    legend: { labelFont: t.fontMono, labelColor: t.ink2, labelFontSize: 11, titleFont: t.fontMono, titleColor: t.ink2, titleFontSize: 11, titleFontWeight: 500, orient: "top", symbolType: "circle", symbolSize: 90 },
    range: { category: t.series },
    text: { font: t.fontMono, fontSize: 11, color: t.ink2 },
  };
}

const selParam = { name: "sel", value: -1 };
const dim = { value: 1, condition: { test: "sel >= 0 && datum.__i !== sel", value: 0.28 } };
const citeTip = { field: "__cites", type: "quantitative", title: "Citations", format: ",~f" };

function base(t, opts, extra) {
  return {
    $schema: VL_SCHEMA,
    // Measured host width (charts re-render on resize) rather than "container", which warns
    // on step-height specs.
    width: Math.max(MIN_WIDTH, Math.round(opts.width || DEFAULT_WIDTH)),
    autosize: { type: "fit-x", contains: "padding" },
    config: vlConfig(t, opts.compact),
    params: [selParam],
    ...extra,
  };
}

function valueChannel(viz) {
  const y = viz.encoding.y || viz.encoding.value || { field: "trial_count", title: "Trials" };
  const isShare = y.unit === "share" || y.field === "share";
  return { field: y.field, title: y.title || "Trials", format: isShare ? ".0%" : ",~f", isShare };
}

function barSpec(viz, t, opts) {
  const rows = viz.data.map(light);
  const v = valueChannel(viz);
  const labelLimit = opts.compact ? 90 : 190;
  const y = { field: "category", type: "nominal", sort: null, title: opts.compact ? null : viz.encoding.x?.title, axis: { labelLimit, ticks: false, domain: false } };
  const x = { field: v.field, type: "quantitative", title: opts.compact ? null : v.title, axis: { format: v.format, tickCount: 5, grid: true } };
  const tooltip = [{ field: "category", title: viz.encoding.x?.title || "Category" }, { field: v.field, title: v.title, format: v.format }, citeTip];
  const step = opts.compact ? 16 : BAR_STEP;
  return base(t, opts, {
    height: { step },
    data: { values: rows },
    encoding: { y, x },
    layer: [
      { mark: { type: "bar", size: opts.compact ? 10 : BAR_SIZE, cornerRadiusEnd: 4, color: t.series[0], cursor: "pointer" }, encoding: { opacity: dim, tooltip } },
      ...(opts.compact ? [] : [{ mark: { type: "text", align: "left", dx: 5, color: t.ink2 }, encoding: { text: { field: v.field, format: v.format } } }]),
    ],
  });
}

function groupedBarSpec(viz, t, opts) {
  const rows = viz.data.map(light);
  const v = valueChannel(viz);
  const cohorts = [...new Set(viz.data.map((r) => r.cohort))];
  const color = { field: "cohort", type: "nominal", title: viz.encoding.color?.title || "Cohort", scale: { domain: cohorts, range: t.series.slice(0, Math.max(cohorts.length, 1)) }, legend: opts.compact ? null : {} };
  return base(t, opts, {
    height: { step: (opts.compact ? 10 : 14) * Math.max(cohorts.length, 1) + 8 },
    data: { values: rows },
    mark: { type: "bar", cornerRadiusEnd: 4, cursor: "pointer" },
    encoding: {
      y: { field: "category", type: "nominal", sort: null, title: opts.compact ? null : viz.encoding.x?.title, axis: { labelLimit: opts.compact ? 90 : 190, ticks: false, domain: false } },
      yOffset: { field: "cohort", sort: cohorts },
      x: { field: v.field, type: "quantitative", title: opts.compact ? null : v.title, axis: { format: v.format, tickCount: 5 } },
      color, opacity: dim,
      tooltip: [{ field: "category", title: viz.encoding.x?.title || "Category" }, { field: "cohort", title: "Cohort" }, { field: v.field, title: v.title, format: v.format }, { field: "trial_count", title: "Trials", format: ",~f" }, citeTip],
    },
  });
}

/** Solid line through complete years; dashed tail into partial/projected years. */
function lineSegments(rows) {
  const byCohort = new Map();
  for (const r of rows) byCohort.set(r.cohort, [...(byCohort.get(r.cohort) || []), r]);
  const out = [];
  for (const [cohort, list] of byCohort) {
    const sorted = [...list].sort((a, b) => a.__x - b.__x);
    const firstFlag = sorted.findIndex((r) => r.__flag);
    sorted.forEach((r, idx) => {
      if (firstFlag < 0 || idx < firstFlag) out.push({ ...r, seg: "solid", line: `${cohort}|solid` });
      if (firstFlag >= 0 && idx >= firstFlag - 1) out.push({ ...r, seg: "tail", line: `${cohort}|tail` });
    });
  }
  return out;
}

function timeSeriesSpec(viz, t, opts) {
  const rows = viz.data.map((r, i) => ({ ...light(r, i), __x: Number(r.year), __flag: (r.flags || []).length > 0, __flagText: (r.flags || []).map((f) => f.replace(/_/g, " ")).join(", ") || "complete" }));
  const cohorts = [...new Set(viz.data.map((r) => r.cohort))];
  const multi = cohorts.length > 1;
  const color = multi
    ? { field: "cohort", type: "nominal", title: "Cohort", scale: { domain: cohorts, range: t.series.slice(0, cohorts.length) }, legend: opts.compact ? null : {} }
    : { value: t.series[0] };
  const x = { field: "__x", type: "quantitative", title: opts.compact ? null : viz.encoding.x?.title || "Year", scale: { zero: false, nice: false }, axis: { format: "d", tickMinStep: 1, grid: false } };
  const y = { field: "trial_count", type: "quantitative", title: opts.compact ? null : viz.encoding.y?.title || "Trials", axis: { format: ",~f", tickCount: 5 } };
  const layers = [];
  if (!multi) layers.push({ data: { values: rows }, mark: { type: "area", opacity: 0.09, color: t.series[0], interpolate: "monotone" }, encoding: { x, y } });
  layers.push({
    data: { values: lineSegments(rows) },
    mark: { type: "line", strokeWidth: 2, strokeCap: "round", strokeJoin: "round", interpolate: "monotone" },
    encoding: { x, y, color, detail: { field: "line" }, strokeDash: { field: "seg", type: "nominal", scale: { domain: ["solid", "tail"], range: [[1, 0], [4, 4]] }, legend: null } },
  });
  layers.push({
    data: { values: rows },
    mark: { type: "point", filled: true, size: opts.compact ? 18 : 64, strokeWidth: 2, cursor: "pointer" },
    encoding: {
      x, y, opacity: dim,
      fill: { condition: { test: "datum.__flag", value: t.surface }, ...(multi ? { field: "cohort", type: "nominal", scale: { domain: cohorts, range: t.series.slice(0, cohorts.length) } } : { value: t.series[0] }) },
      stroke: multi ? { field: "cohort", type: "nominal", scale: { domain: cohorts, range: t.series.slice(0, cohorts.length) } } : { value: t.series[0] },
      tooltip: [...(multi ? [{ field: "cohort", title: "Cohort" }] : []), { field: "__x", title: "Year", format: "d" }, { field: "trial_count", title: "Trials", format: ",~f" }, { field: "__flagText", title: "Period" }, citeTip],
    },
  });
  return base(t, opts, { height: opts.compact ? 150 : 320, layer: layers });
}

function histogramSegments(viz) {
  return viz.data.flatMap((r, i) => {
    const counts = Object.fromEntries((r.flags || []).map((f) => f.split(":")).filter((p) => p.length === 2 && ENROLLMENT_TYPES.includes(p[0])).map(([k, n]) => [k, Number(n)]));
    const typed = Object.keys(counts).length > 0;
    const common = { __i: i, bin: String(r.bin_label), bin_start: r.bin_start, __total: r.trial_count, __cites: (r.citations || []).length };
    if (!typed) return [{ ...common, type: "all", n: r.trial_count }];
    return ENROLLMENT_TYPES.filter((k) => counts[k] > 0).map((k) => ({ ...common, type: k, n: counts[k] }));
  });
}

function histogramSpec(viz, t, opts) {
  const segs = histogramSegments(viz);
  const bins = [...viz.data].sort((a, b) => (a.bin_start ?? 0) - (b.bin_start ?? 0)).map((r) => String(r.bin_label));
  const typed = segs.some((s) => s.type !== "all");
  const narrow = (opts.width || 800) < 560 || opts.compact;
  const color = typed
    ? { field: "type", type: "nominal", title: "Enrollment count type", scale: { domain: ENROLLMENT_TYPES, range: [t.series[0], t.series[1], t.neutral] }, legend: opts.compact ? null : {} }
    : { value: t.series[0] };
  return base(t, opts, {
    height: opts.compact ? 150 : 300,
    data: { values: segs },
    mark: { type: "bar", stroke: t.surface, strokeWidth: 1.5, cursor: "pointer" },
    encoding: {
      x: { field: "bin", type: "ordinal", sort: bins, title: opts.compact ? null : viz.encoding.x?.title || "Bin", scale: { paddingInner: 0.12 }, axis: { labelAngle: narrow ? -40 : 0, ticks: false } },
      y: { field: "n", type: "quantitative", stack: "zero", title: opts.compact ? null : "Trials", axis: { format: ",~f", tickCount: 5 } },
      order: { field: "type", sort: "ascending" },
      color, opacity: dim,
      tooltip: [{ field: "bin", title: viz.encoding.x?.title || "Bin" }, { field: "type", title: "Count type" }, { field: "n", title: "Trials (segment)", format: ",~f" }, { field: "__total", title: "Trials (bin)", format: ",~f" }, citeTip],
    },
  });
}

function wantsLog(values) {
  const pos = values.filter((v) => v > 0);
  if (!pos.length) return false;
  const max = Math.max(...pos);
  return max > LOG_MIN_MAX && max / Math.min(...pos) > LOG_RATIO;
}

/** Explicit 0, 1, 10, 100… ticks: symlog's default ticks render as "2e+3". */
function logTicks(values) {
  const max = Math.max(...values, 1);
  const ticks = [0];
  for (let v = 1; v <= max * 10; v *= 10) ticks.push(v);
  return ticks;
}

function quantAxis(values, log) {
  return log
    ? { scale: { type: "symlog" }, axis: { values: logTicks(values), format: ",~f", labelFlush: true } }
    : { scale: { zero: true }, axis: { format: ",~f", tickCount: 6 } };
}

function scatterSpec(viz, t, opts) {
  const xf = viz.encoding.x.field;
  const yf = viz.encoding.y.field;
  const rows = viz.data.map(light).filter((r) => Number.isFinite(r[xf]) && Number.isFinite(r[yf]));
  const axisTitle = (ch, log) => (opts.compact ? null : `${ch.title || ch.field}${ch.unit ? ` (${ch.unit})` : ""}${log ? " · log scale" : ""}`);
  const xLog = wantsLog(rows.map((r) => r[xf]));
  const yLog = wantsLog(rows.map((r) => r[yf]));
  const types = [...new Set(rows.map((r) => r.date_type).filter(Boolean))];
  const order = [...ENROLLMENT_TYPES.filter((k) => types.includes(k)), ...types.filter((k) => !ENROLLMENT_TYPES.includes(k))];
  const palette = { actual: t.series[0], estimated: t.series[1], untyped: t.neutral };
  const color = order.length
    ? { field: "date_type", type: "nominal", title: "Date type", scale: { domain: order, range: order.map((k, i) => palette[k] || t.series[i + 2] || t.neutral) }, legend: opts.compact ? null : {} }
    : { value: t.series[0] };
  return base(t, opts, {
    height: opts.compact ? 160 : 340,
    data: { values: rows },
    mark: { type: "point", filled: true, size: opts.compact ? 22 : 70, stroke: t.surface, strokeWidth: 1.5, cursor: "pointer" },
    encoding: {
      x: { field: xf, type: "quantitative", title: axisTitle(viz.encoding.x, xLog), ...quantAxis(rows.map((r) => r[xf]), xLog) },
      y: { field: yf, type: "quantitative", title: axisTitle(viz.encoding.y, yLog), ...quantAxis(rows.map((r) => r[yf]), yLog) },
      color,
      opacity: { value: 0.85, condition: { test: "sel >= 0 && datum.__i !== sel", value: 0.15 } },
      tooltip: [{ field: "nct_id", title: "Trial" }, { field: xf, title: viz.encoding.x.title || xf, format: ",.1f" }, { field: yf, title: viz.encoding.y.title || yf, format: ",.0f" }, { field: "date_type", title: "Date type" }],
    },
  });
}

/** Returns {kind: "vega-lite"|"vega"|"html", spec} for a visualization. */
export function buildSpec(viz, theme, opts = {}) {
  switch (viz.type) {
    case "bar_chart": return { kind: "vega-lite", spec: barSpec(viz, theme, opts) };
    case "grouped_bar_chart": return { kind: "vega-lite", spec: groupedBarSpec(viz, theme, opts) };
    case "time_series": return { kind: "vega-lite", spec: timeSeriesSpec(viz, theme, opts) };
    case "histogram": return { kind: "vega-lite", spec: histogramSpec(viz, theme, opts) };
    case "scatter_plot": return { kind: "vega-lite", spec: scatterSpec(viz, theme, opts) };
    case "network_graph": return { kind: "vega", spec: networkSpec(viz, theme, opts) };
    case "table":
    case "metric": return { kind: "html", spec: null };
    default: return { kind: "unknown", spec: null };
  }
}

/** One-sentence text alternative for the chart (screen readers + figure caption). */
export function summarize(viz) {
  const ds = datums(viz);
  const label = viz.type.replace(/_/g, " ");
  if (viz.type === "network_graph") {
    const nodes = ds.filter((d) => d.kind === "node").sort((a, b) => b.count - a.count);
    return `Network graph with ${viz.data.nodes.length} nodes and ${viz.data.edges.length} edges; most connected: ${nodes.slice(0, 3).map((n) => `${n.label} (${n.count} trials)`).join(", ")}.`;
  }
  if (viz.type === "scatter_plot") return `Scatter plot of ${ds.length} trials: ${viz.encoding.x.title} against ${viz.encoding.y.title}.`;
  const counted = ds.filter((d) => typeof d.count === "number");
  if (!counted.length) return `${label} with ${ds.length} rows.`;
  const top = counted.reduce((a, b) => (b.count > a.count ? b : a));
  const total = counted.reduce((s, d) => s + d.count, 0);
  return `${label[0].toUpperCase()}${label.slice(1)} with ${ds.length} data points totalling ${total.toLocaleString("en-US")} trial counts; largest: ${top.label} (${top.count.toLocaleString("en-US")}).`;
}
