// Exhibit gallery: one card per example question. In replay mode thumbnails fetch lazily as cards
// scroll into view (instant, offline). In live mode each fetch costs LLM calls, so thumbnails wait
// until the reader opens the exhibit; the result is cached and reused by the viewer.

import { h, clear, fmtInt } from "./dom.js";
import { EXAMPLES, TYPE_LABELS } from "./examples.js";
import { visualize } from "./api.js";
import { renderViz } from "./charts/render.js";
import { totalCitations } from "./viewer.js";

const MAX_PARALLEL = 2;

export function createGallery(grid, { getMode, onOpen }) {
  const cards = new Map();
  const queue = [];
  let active = 0;

  function stats(card, response) {
    clear(card.stats);
    if (!response.ok) {
      card.stats.append(h("span", {}, `Returned ${response.error.code}`));
      return;
    }
    const meta = response.meta;
    const cov = meta?.data_coverage;
    card.tag.textContent = TYPE_LABELS[response.visualization.type] || card.tag.textContent;
    card.stats.append(
      cov ? h("span", {}, h("b", {}, fmtInt(cov.records_plotted)), " plotted") : null,
      h("span", {}, h("b", {}, fmtInt(totalCitations(response.visualization))), " citations"),
      meta?.citation_check?.passed ? h("span", { class: "verified-mini" }, `✓ verified in ${fmtInt(meta.citation_check.ms)} ms`) : null);
  }

  async function paint(card) {
    const response = card.response;
    if (!response) return;
    stats(card, response);
    clear(card.thumb);
    if (!response.ok) {
      card.thumb.append(h("p", { class: "placeholder" }, response.error.message));
      return;
    }
    const host = h("div", { class: "chart-host", "aria-hidden": "true" });
    card.thumb.append(host);
    try {
      await renderViz(host, response.visualization, { compact: true });
    } catch {
      clear(card.thumb).append(h("p", { class: "placeholder" }, "Preview unavailable"));
    }
  }

  async function load(card) {
    if (card.response || card.loading) return;
    card.loading = true;
    clear(card.thumb).append(h("p", { class: "placeholder" }, "Loading…"));
    card.response = await visualize({ query: card.example.query });
    card.loading = false;
    await paint(card);
  }

  function pump() {
    while (active < MAX_PARALLEL && queue.length) {
      const card = queue.shift();
      active += 1;
      load(card).finally(() => { active -= 1; pump(); });
    }
  }

  const observer = "IntersectionObserver" in window
    ? new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        observer.unobserve(e.target);
        const card = cards.get(e.target.dataset.id);
        if (card && getMode() === "replay") { queue.push(card); pump(); }
      }
    }, { rootMargin: "200px" })
    : null;

  function build() {
    clear(grid);
    for (const ex of EXAMPLES) {
      const tag = h("span", { class: "tag" }, ex.kind);
      const thumb = h("div", { class: "exhibit-thumb" });
      const statsEl = h("div", { class: "exhibit-stats" });
      const el = h("article", { class: "exhibit", dataset: { id: ex.id } },
        h("div", { class: "exhibit-top" }, h("span", { class: "exhibit-label" }, `Exhibit ${ex.id}`), tag),
        h("h3", {}, h("button", { type: "button", class: "exhibit-open", onclick: () => onOpen(ex.query) }, ex.query)),
        thumb, statsEl);
      const card = { example: ex, el, tag, thumb, stats: statsEl, response: null, loading: false };
      cards.set(ex.id, card);
      grid.append(el);
    }
  }

  /** Called when mode is known: replay → lazy auto-load; live → click-to-run placeholders. */
  function start(mode) {
    for (const card of cards.values()) {
      if (card.response) continue;
      if (mode === "replay" && observer) observer.observe(card.el);
      else if (mode === "replay") { queue.push(card); pump(); }
      else clear(card.thumb).append(h("p", { class: "placeholder" }, "Open to run live — the preview fills in from the result"));
    }
  }

  /** The viewer finished a query: fill a matching card, mark it current. */
  function sync(query, response) {
    for (const card of cards.values()) {
      const match = card.example.query === query;
      card.el.setAttribute("aria-current", String(match));
      if (match && !card.response && response?.ok) { card.response = response; paint(card); }
    }
  }

  function repaintAll() {
    for (const card of cards.values()) if (card.response) paint(card);
  }

  build();
  return { start, sync, repaintAll };
}
