// Mount a visualization into a host element: Vega(-Lite) via vega-embed, or HTML for table/metric.
// Returns a controller: { select(i), destroy() }. `onSelect(i)` fires when a mark/row is clicked.

import { h, clear, fmtInt, fmtNum, humanize } from "../dom.js";
import { buildSpec, datums } from "./specs.js";
import { chartTheme } from "../theme.js";

const TABLE_ROW_CAP = 300;

function runtimeReady() {
  return typeof window.vegaEmbed === "function" && typeof window.vegaLite?.compile === "function";
}

/** Compile Vega-Lite ourselves with an errors-only logger: VL warns spuriously ("Dropping
 * fit-y") on step-height bars, and vega-embed doesn't pass its log level to the compiler. */
function toVega(kind, spec) {
  if (kind !== "vega-lite") return spec;
  const logger = window.vega.logger(window.vega.Error);
  return window.vegaLite.compile(spec, { logger }).spec;
}

async function mountVega(host, kind, spec, onSelect) {
  const result = await window.vegaEmbed(host, toVega(kind, spec), {
    mode: "vega", renderer: "svg", actions: false, tooltip: { theme: "custom" },
  });
  const view = result.view;
  if (onSelect) {
    view.addEventListener("click", (_event, item) => {
      const i = item?.datum?.__i ?? item?.datum?.datum?.__i;
      if (Number.isInteger(i)) onSelect(i);
    });
  }
  return {
    select: (i) => view.signal("sel", Number.isInteger(i) ? i : -1).runAsync().catch(() => {}),
    destroy: () => result.finalize(),
  };
}

function metricTiles(viz, onSelect) {
  const unit = viz.encoding.value?.unit;
  return h("div", { class: "metric-grid" }, viz.data.map((row, i) =>
    h("button", { type: "button", class: "metric-tile", onclick: () => onSelect?.(i), "aria-label": `${row.label ?? "Total"}: ${fmtInt(row.trial_count)} ${unit || ""}. Show citations.` },
      h("span", { class: "label" }, String(row.label ?? "Total")),
      h("span", { class: "value" }, fmtInt(row.trial_count)),
      h("span", { class: "label" }, `${unit || "trials"} · ${fmtInt((row.citations || []).length)} citations`))));
}

function cellText(value) {
  if (Array.isArray(value)) return value.map((v) => humanizeEnum(v)).join("; ");
  return humanizeEnum(value);
}

function humanizeEnum(value) {
  if (value == null) return "—";
  const s = String(value);
  return /^[A-Z0-9_]+$/.test(s) && s.includes("_") ? humanize(s) : s;
}

function keyFactsRecord(viz, row, onSelect) {
  const fields = row.cell_fields || {};
  const keys = Object.keys(viz.encoding).filter((k) => k !== "nct_id");
  return h("div", {},
    h("dl", { class: "record" }, keys.map((k) => [
      h("dt", {}, viz.encoding[k].title || humanize(k)),
      h("dd", {},
        h("span", {}, cellText(row[k]), viz.encoding[k].unit && row[k] != null ? ` ${viz.encoding[k].unit}` : ""),
        (fields[k] || []).map((p) => h("span", { class: "ptr" }, p))),
    ])),
    h("p", { class: "chart-hint", style: "margin-top:0.8rem" },
      h("button", { type: "button", class: "btn btn-small", onclick: () => onSelect?.(0) }, "Show citation & evidence for this record")));
}

function genericTable(viz, onSelect) {
  const cols = Object.keys(viz.encoding);
  const tbody = h("tbody", {}, viz.data.slice(0, TABLE_ROW_CAP).map((row, i) =>
    h("tr", { dataset: { i } }, cols.map((c) => h("td", { class: typeof row[c] === "number" ? "num" : "" }, cellText(row[c]))),
      h("td", {}, h("button", { type: "button", class: "btn btn-small", onclick: () => onSelect?.(i) }, `Cite (${fmtInt((row.citations || []).length)})`)))));
  return h("div", { class: "table-wrap" }, h("table", { class: "data" },
    h("thead", {}, h("tr", {}, cols.map((c) => h("th", { scope: "col" }, viz.encoding[c].title || humanize(c))), h("th", { scope: "col" }, "Citations"))),
    tbody));
}

function mountHtml(host, viz, onSelect) {
  let node;
  if (viz.type === "metric") node = metricTiles(viz, onSelect);
  else if (viz.data.length === 1 && viz.data[0].cell_fields) node = keyFactsRecord(viz, viz.data[0], onSelect);
  else node = genericTable(viz, onSelect);
  host.append(node);
  return {
    select: (i) => host.querySelectorAll("tr[data-i]").forEach((tr) => tr.setAttribute("aria-selected", String(Number(tr.dataset.i) === i))),
    destroy: () => clear(host),
  };
}

/** Render `viz` into `host`. opts: {compact, onSelect}. */
export async function renderViz(host, viz, opts = {}) {
  clear(host);
  const width = host.clientWidth || 720;
  const { kind, spec } = buildSpec(viz, chartTheme(), { compact: !!opts.compact, width });
  if (kind === "html") return mountHtml(host, viz, opts.onSelect);
  if (kind === "unknown") {
    host.append(h("p", { class: "chart-hint" }, `Unsupported visualization type “${viz.type}”. The raw JSON is still available below.`));
    return { select() {}, destroy() {} };
  }
  if (!runtimeReady()) {
    host.append(h("p", { class: "chart-hint" }, "The chart runtime (Vega) did not load — check your network. The data table below still lists every datum."));
    return { select() {}, destroy() {} };
  }
  host.setAttribute("role", "img");
  return mountVega(host, kind, spec, opts.onSelect);
}

/** Accessible data-table alternative: every datum with a button that opens its citations. */
export function dataTable(viz, onCite) {
  const ds = datums(viz);
  const extra = viz.type === "scatter_plot"
    ? [[viz.encoding.x.title || "x", (d) => fmtNum(d.row[viz.encoding.x.field])], [viz.encoding.y.title || "y", (d) => fmtNum(d.row[viz.encoding.y.field])]]
    : [["Trials", (d) => fmtInt(d.count)]];
  const hasFlags = ds.some((d) => d.flags.length);
  const shown = ds.slice(0, TABLE_ROW_CAP);
  const table = h("table", { class: "data" },
    h("thead", {}, h("tr", {},
      h("th", { scope: "col" }, viz.type === "network_graph" ? "Node / edge" : "Datum"),
      extra.map(([t]) => h("th", { scope: "col" }, t)),
      hasFlags ? h("th", { scope: "col" }, "Flags") : null,
      h("th", { scope: "col" }, "Citations"))),
    h("tbody", {}, shown.map((d, i) => h("tr", { dataset: { i } },
      h("td", {}, d.kind === "row" ? d.label : `${d.kind === "node" ? humanize(d.row.type) : "Edge"}: ${d.label}`),
      extra.map(([, fn]) => h("td", { class: "num" }, fn(d))),
      hasFlags ? h("td", {}, d.flags.map((f) => f.replace(/_/g, " ")).join(", ")) : null,
      h("td", {}, h("button", { type: "button", class: "btn btn-small", onclick: () => onCite(i) }, `Cite (${fmtInt(d.citations.length)})`))))));
  return h("details", { class: "data-details" },
    h("summary", {}, `Data table · ${fmtInt(ds.length)} ${ds.length === 1 ? "datum" : "data"}${ds.length > TABLE_ROW_CAP ? ` (first ${TABLE_ROW_CAP} shown)` : ""}`),
    h("div", { class: "table-wrap" }, table));
}
