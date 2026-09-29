// The main viewer: figure (header, chart, notes, data table) + trust rail + citation drawer wiring.

import { h, clear, fmtInt } from "./dom.js";
import { renderViz, dataTable } from "./charts/render.js";
import { datums, summarize } from "./charts/specs.js";
import { renderTrust } from "./trust.js";
import { errorCard } from "./ask.js";
import { TYPE_LABELS } from "./examples.js";

export function totalCitations(viz) {
  return datums(viz).reduce((s, d) => s + d.citations.length, 0);
}

function flagNotes(viz) {
  if (viz.type === "time_series") {
    const flagged = viz.data.filter((r) => r.flags?.length);
    if (!flagged.length) return [];
    return [`Hollow points and the dashed tail mark incomplete periods: ${flagged.map((r) => `${r.year} (${r.flags.map((f) => f.replace(/_/g, " ")).join(", ")})`).join("; ")}.`];
  }
  if (viz.type === "histogram") return ["Bins are unequal in width. Segments split each bin by enrollment count type (actual, estimated, or not stated)."];
  if (viz.type === "scatter_plot") return ["One point per trial. Axes switch to a log-like (symlog) scale when values span more than two orders of magnitude."];
  if (viz.type === "network_graph") return ["Node size = trials; edge width = trials shared by the pair. Labels show the most connected nodes; hover or open the data table for the rest."];
  const folded = viz.data?.find?.((r) => r.other_categories > 0);
  return folded ? [`“${folded.category}” folds ${fmtInt(folded.other_categories)} smaller categories.`] : [];
}

export function createViewer({ figure, trust, drawer, getMode, onPick }) {
  let controller = null;
  let token = 0;
  let current = null;

  function reset() {
    controller?.destroy();
    controller = null;
    drawer.close();
  }

  function showEmpty() {
    reset();
    clear(trust);
    clear(figure).append(h("div", { class: "empty-state" },
      h("h3", {}, "No figure yet"),
      h("p", {}, "Ask a question above, or open one of the exhibits below.")));
  }

  function showLoading(query) {
    reset();
    const my = ++token;
    const started = performance.now();
    const clock = h("span", {}, "0.0 s");
    clear(figure).append(h("div", { class: "loading", role: "status" },
      h("p", { class: "figure-question" }, `“${query}”`),
      h("div", { class: "loading-bar", "aria-hidden": "true" }),
      h("p", {}, getMode() === "replay" ? "Replaying the pipeline offline… " : "Planning, fetching and citing… ", clock)));
    clear(trust);
    const timer = setInterval(() => {
      if (my !== token) return clearInterval(timer);
      clock.textContent = `${((performance.now() - started) / 1000).toFixed(1)} s`;
    }, 100);
    figure.closest(".viewer")?.setAttribute("aria-busy", "true");
    return () => { clearInterval(timer); figure.closest(".viewer")?.setAttribute("aria-busy", "false"); return my === token; };
  }

  function openDatum(viz, meta, i) {
    const ds = datums(viz);
    if (!ds[i]) return;
    controller?.select(i);
    drawer.open(ds[i], { urlTemplate: meta?.citation_policy?.url_template, figureTitle: viz.title, onClose: () => controller?.select(-1) });
  }

  async function showResponse(response, query) {
    reset();
    current = { response, query };
    if (!response.ok) {
      clear(figure).append(errorCard(response.error, { mode: getMode(), onPick, meta: response.meta }));
      if (response.meta) renderTrust(trust, response); else clear(trust);
      return;
    }
    const { visualization: viz, meta } = response;
    const cov = meta?.data_coverage;
    const host = h("div", { class: "chart-host", "aria-label": summarize(viz) });
    const notes = flagNotes(viz);
    clear(figure).append(
      h("header", { class: "figure-head" },
        h("div", { class: "figure-kicker" }, h("span", { class: "eyebrow" }, "Figure"), h("span", { class: "tag" }, TYPE_LABELS[viz.type] || viz.type),
          meta?.citation_check?.passed ? h("span", { class: "tag verified-mini" }, "✓ verified") : null),
        h("h3", { class: "figure-title" }, viz.title),
        query ? h("p", { class: "figure-question" }, `“${query}”`) : null,
        meta?.query_interpretation ? h("p", { class: "figure-interp" }, meta.query_interpretation) : null,
        h("div", { class: "stat-strip" },
          cov ? h("span", {}, h("b", {}, fmtInt(cov.records_plotted)), "trials plotted") : null,
          h("span", {}, h("b", {}, fmtInt(totalCitations(viz))), "citations"),
          cov ? h("span", {}, h("b", {}, fmtInt(cov.api_total_count)), "API matches") : null)),
      host,
      h("p", { class: "chart-hint" }, "Click any bar, point, node, edge or row to read its citations."),
      notes.length ? h("div", { class: "chart-notes" }, notes.map((n) => h("p", {}, n))) : null,
      viz.type === "table" || viz.type === "metric" ? null : dataTable(viz, (i) => openDatum(viz, meta, i)),
    );
    renderTrust(trust, response);
    try {
      controller = await renderViz(host, viz, { onSelect: (i) => openDatum(viz, meta, i) });
    } catch (err) {
      host.append(h("p", { class: "chart-hint" }, `Chart failed to render (${err?.message || err}). The data table still lists every datum.`));
    }
  }

  function rerender() {
    if (current) showResponse(current.response, current.query);
  }

  return { showEmpty, showLoading, showResponse, rerender, current: () => current };
}
