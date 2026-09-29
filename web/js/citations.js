// Citation drawer: lists one datum's citations (NCT link, JSON pointer, exact excerpt, evidence by
// role), filterable and paginated so bars with thousands of citations stay responsive.

import { h, clear, fmtInt, trialUrl, externalLink, debounce } from "./dom.js";

const PAGE_SIZE = 25;
const MAX_PREDICATE_CHARS = 1200;

/** Readable one-line rendering of the predicate grammar (all / any / not / op on a pointer). */
export function predicateText(p, depth = 0) {
  if (!p || typeof p !== "object") return String(p);
  if (depth > 6) return "…";
  if (Array.isArray(p.all)) return p.all.map((x) => predicateText(x, depth + 1)).join(" AND ");
  if (Array.isArray(p.any)) return `(${p.any.map((x) => predicateText(x, depth + 1)).join(" OR ")})`;
  if (p.not) return `NOT ${predicateText(p.not, depth + 1)}`;
  if (p.op === "any_element") return `some ${p.path}[*] where ${(p.where || []).map((x) => predicateText(x, depth + 1)).join(" AND ")}`;
  const value = p.value === undefined ? "" : ` ${JSON.stringify(p.value)}`;
  return `${p.path ?? ""} ${String(p.op ?? "?").replace(/_/g, " ")}${value}`.trim();
}

export function createCitationDrawer() {
  const el = document.getElementById("cite-drawer");
  const title = document.getElementById("cite-title");
  const eyebrow = document.getElementById("cite-eyebrow");
  const sub = document.getElementById("cite-sub");
  const predicate = document.getElementById("cite-predicate");
  const filter = document.getElementById("cite-filter");
  const count = document.getElementById("cite-count");
  const list = document.getElementById("cite-list");
  const pager = document.getElementById("cite-pager");
  const state = { citations: [], filtered: [], page: 0, urlTemplate: null, returnFocus: null, onClose: null };

  function evidenceItem(ev) {
    return h("li", {},
      h("span", { class: "role", "data-role": ev.role }, ev.role),
      h("span", { class: "ev-text" }, ev.excerpt),
      h("code", { class: "ev-ptr" }, ev.field));
  }

  function citationItem(c, n) {
    return h("li", { class: "cite" },
      h("div", { class: "cite-head" },
        h("span", { class: "cite-num" }, `[${n}]`),
        externalLink(trialUrl(state.urlTemplate, c.nct_id), c.nct_id, "cite-nct")),
      h("code", { class: "cite-ptr" }, c.field),
      h("blockquote", {}, c.excerpt),
      c.evidence?.length ? h("ul", { class: "evidence", "aria-label": "Supporting evidence" }, c.evidence.map(evidenceItem)) : null);
  }

  function renderPage() {
    const total = state.filtered.length;
    const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    state.page = Math.min(state.page, pages - 1);
    const start = state.page * PAGE_SIZE;
    const slice = state.filtered.slice(start, start + PAGE_SIZE);
    clear(list).append(...slice.map(({ c, n }) => citationItem(c, n)));
    count.textContent = total === state.citations.length
      ? `${fmtInt(total)} citation${total === 1 ? "" : "s"}${total ? ` · showing ${fmtInt(start + 1)}–${fmtInt(start + slice.length)}` : ""}`
      : `${fmtInt(total)} of ${fmtInt(state.citations.length)} match`;
    clear(pager);
    pager.hidden = pages <= 1;
    if (pages > 1) {
      const go = (p) => { state.page = p; renderPage(); list.firstElementChild?.scrollIntoView({ block: "nearest" }); };
      pager.append(
        h("button", { type: "button", class: "btn btn-small", disabled: state.page === 0, onclick: () => go(state.page - 1) }, "← Prev"),
        h("span", {}, `Page ${state.page + 1} of ${pages}`),
        h("button", { type: "button", class: "btn btn-small", disabled: state.page >= pages - 1, onclick: () => go(state.page + 1) }, "Next →"));
    }
  }

  function applyFilter() {
    const q = filter.value.trim().toLowerCase();
    const indexed = state.citations.map((c, i) => ({ c, n: i + 1 }));
    state.filtered = q
      ? indexed.filter(({ c }) => c.nct_id.toLowerCase().includes(q) || String(c.excerpt).toLowerCase().includes(q) || (c.evidence || []).some((e) => String(e.excerpt).toLowerCase().includes(q)))
      : indexed;
    state.page = 0;
    renderPage();
  }

  function renderPredicate(p) {
    clear(predicate);
    if (!p) return;
    const text = predicateText(p);
    predicate.append(h("details", {},
      h("summary", { class: "eyebrow" }, "Why these trials count (predicate)"),
      h("p", { class: "predicate-text" }, text.length > MAX_PREDICATE_CHARS ? `${text.slice(0, MAX_PREDICATE_CHARS)}…` : text)));
  }

  function close() {
    if (el.hidden) return;
    el.hidden = true;
    state.onClose?.();
    state.returnFocus?.focus?.({ preventScroll: true });
  }

  document.getElementById("cite-close").addEventListener("click", close);
  filter.addEventListener("input", debounce(applyFilter, 120));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !el.hidden) close(); });

  /** datum: {label, count, citations, predicate, flags, kind}; ctx: {urlTemplate, figureTitle, onClose}. */
  function open(datum, ctx = {}) {
    state.returnFocus = document.activeElement;
    state.citations = datum.citations || [];
    state.urlTemplate = ctx.urlTemplate;
    state.onClose = ctx.onClose;
    eyebrow.textContent = ctx.figureTitle ? `Citations · ${ctx.figureTitle}` : "Citations";
    title.textContent = datum.label;
    const bits = [];
    if (typeof datum.count === "number") bits.push(`${fmtInt(datum.count)} ${datum.kind === "edge" ? "shared trials" : "trials"}`);
    bits.push(`${fmtInt(state.citations.length)} citations`);
    if (datum.flags?.length) bits.push(datum.flags.map((f) => f.replace(/_/g, " ")).join(", "));
    sub.textContent = bits.join(" · ");
    renderPredicate(datum.predicate);
    filter.value = "";
    applyFilter();
    el.hidden = false;
    title.focus({ preventScroll: true });
  }

  return { open, close, isOpen: () => !el.hidden };
}
