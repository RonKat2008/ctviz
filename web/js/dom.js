// Tiny DOM helpers. Every string goes through textContent / createTextNode — never HTML parsing —
// so API-provided text (titles, excerpts, error messages) can't inject markup.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = String(value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  appendAll(el, children);
  return el;
}

export function appendAll(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function clear(el) {
  el.replaceChildren();
  return el;
}

export const fmtInt = (n) => (n == null || Number.isNaN(Number(n)) ? "—" : Math.round(Number(n)).toLocaleString("en-US"));

export function fmtNum(n, digits = 1) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  const v = Number(n);
  return Number.isInteger(v) ? v.toLocaleString("en-US") : v.toLocaleString("en-US", { maximumFractionDigits: digits });
}

/** "ACTIVE_NOT_RECRUITING" / "api_fulltext_match_only" → "Active not recruiting". */
export function humanize(value) {
  const text = String(value ?? "").replace(/_/g, " ").trim().toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

const NCT_RE = /^NCT\d{8}$/;

/** Deep link for a trial; only https templates with a {nct_id} slot are trusted. */
export function trialUrl(template, nctId) {
  if (!NCT_RE.test(String(nctId))) return null;
  const safeTemplate = typeof template === "string" && template.startsWith("https://") && template.includes("{nct_id}")
    ? template
    : "https://clinicaltrials.gov/study/{nct_id}";
  return safeTemplate.replace("{nct_id}", encodeURIComponent(nctId));
}

export function externalLink(href, text, cls) {
  if (!href) return h("span", { class: cls }, text);
  return h("a", { href, target: "_blank", rel: "noopener noreferrer", class: cls }, text, h("span", { "aria-hidden": "true" }, " ↗"));
}

export function prefersReducedMotion() {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

export function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}
