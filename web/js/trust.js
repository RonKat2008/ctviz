// Trust rail: verifier seal, data coverage funnel, agent trace, notes, entity resolution,
// network summary, provenance and raw JSON. Everything is read from `meta`; nothing is inferred.

import { h, clear, fmtInt, humanize, trialUrl, externalLink } from "./dom.js";

const RAW_JSON_CHAR_CAP = 250_000;
const ALIAS_CAP = 24;
const EXCLUDED_LIST_CAP = 40;

const panel = (title, ...body) => h("section", { class: "panel" }, h("h3", {}, title), ...body);
const kv = (pairs) => h("dl", { class: "kv" }, pairs.filter(([, v]) => v != null && v !== "").map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));

function verificationPanel(check) {
  if (!check) {
    return panel("Verification", h("div", { class: "verified" }, h("span", { class: "seal", "data-state": "none", "aria-hidden": "true" }, "–"),
      h("div", {}, h("p", { class: "verified-title" }, "Not checked"), h("p", {}, "This response carries no independent citation check."))));
  }
  const ok = check.passed && check.recount_ok !== false;
  return panel("Verification",
    h("div", { class: "verified" },
      h("span", { class: "seal", "data-state": ok ? "passed" : "failed", "aria-hidden": "true" }, ok ? "✓" : "✕"),
      h("div", {}, h("p", { class: "verified-title" }, ok ? "Verified" : "Verification failed"),
        h("p", {}, `Independent verifier re-checked every ${check.mode === "sample" ? "sampled " : ""}citation against the raw records in ${fmtInt(check.ms)} ms.`))),
    kv([["Citations", fmtInt(check.citations_checked)], ["Evidence", fmtInt(check.evidence_checked)], ["Predicates", fmtInt(check.predicates_checked)], ["Recount", check.recount_ok ? "matches" : "mismatch"], ["Mode", check.mode]]));
}

function funnelRow(label, value, max) {
  const pct = max > 0 ? Math.max(1, Math.round((value / max) * 100)) : 0;
  return h("div", { class: "funnel-row" },
    h("span", { class: "lbl" }, label),
    h("span", { class: "meter", role: "presentation" }, h("span", { style: `width:${pct}%` })),
    h("span", { class: "num" }, fmtInt(value)));
}

function coveragePanel(cov, cohorts, urlTemplate) {
  if (!cov) return null;
  const max = Math.max(cov.api_total_count, cov.records_fetched, 1);
  const stages = Object.entries(cov.excluded || {}).filter(([, reasons]) => Object.keys(reasons).length);
  const excludedList = (cov.excluded_trials || []).slice(0, EXCLUDED_LIST_CAP);
  return panel("Data coverage",
    h("div", { class: "funnel", role: "group", "aria-label": "Records at each stage" },
      funnelRow("API total", cov.api_total_count, max), funnelRow("Fetched", cov.records_fetched, max),
      funnelRow("Matched", cov.records_matched, max), funnelRow("Plotted", cov.records_plotted, max)),
    cov.truncated ? h("p", {}, `⚠ Truncated: ${cov.truncation_rule || "fetch cap reached"}.`) : null,
    stages.length ? h("div", { class: "excl" }, stages.map(([stage, reasons]) => [
      h("p", { class: "excl-stage" }, `Excluded at ${stage}`),
      h("ul", {}, Object.entries(reasons).map(([r, n]) => h("li", {}, `${humanize(r)}: ${fmtInt(n)}`))),
    ])) : h("p", {}, "No trials were excluded."),
    excludedList.length ? h("details", {}, h("summary", {}, `Excluded trials (${fmtInt(cov.excluded_trials.length)})`),
      h("ul", {}, excludedList.map((t) => h("li", {}, externalLink(trialUrl(urlTemplate, t.nct_id), t.nct_id), ` · ${t.stage} · ${humanize(t.reason)}`))),
      cov.excluded_trials.length > EXCLUDED_LIST_CAP ? h("p", {}, `…and ${fmtInt(cov.excluded_trials.length - EXCLUDED_LIST_CAP)} more in the raw JSON.`) : null) : null,
    cohorts?.length > 1 ? h("div", { class: "table-wrap" }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, ["Cohort", "API", "Matched", "Plotted"].map((c) => h("th", { scope: "col" }, c)))),
      h("tbody", {}, cohorts.map((c) => h("tr", {}, h("td", {}, c.label), h("td", { class: "num" }, fmtInt(c.api_total_count)), h("td", { class: "num" }, fmtInt(c.records_matched)), h("td", { class: "num" }, fmtInt(c.records_plotted))))))) : null);
}

function stepDetail(step) {
  switch (step.step) {
    case "plan": return `${step.answerable === false ? "Judged unanswerable" : "Plan produced"}; ${fmtInt(step.overrides ?? 0)} structured override(s).`;
    case "checks": return step.ok ? `Passed${step.adjustments?.length ? `, ${step.adjustments.length} adjustment(s)` : ""}.` : `Failed: ${(step.errors || []).join("; ")}`;
    case "probe": return `${humanize(step.verdict || "")}: ${Object.entries(step.totals || {}).map(([k, v]) => `${k} ${fmtInt(v)}`).join(", ")}${step.warnings?.length ? ` · ${step.warnings.join("; ")}` : ""}${step.feedback?.length ? ` · ${step.feedback.join("; ")}` : ""}`;
    case "judge": return step.available === false ? "Judge unavailable (fail-open)." : `${step.model || "judge"}: ${step.needs_revision ? "revision requested" : "no revision needed"}${step.confidence != null ? ` · confidence ${step.confidence}` : ""}${step.issues?.length ? ` · ${step.issues.join("; ")}` : ""}`;
    case "outcome": return `${humanize(step.status || "")}; executed attempt ${step.executed_attempt ?? "?"}.`;
    case "fast_path": return `Direct lookup of ${(step.nct_ids || []).join(", ")} (planner skipped).`;
    default: return Object.entries(step).filter(([k]) => !["step", "attempt"].includes(k)).map(([k, v]) => `${humanize(k)}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ");
  }
}

function tracePanel(validation) {
  if (!validation) return null;
  const judge = validation.judge || {};
  return panel("Agent trace",
    kv([["Judge", humanize(judge.status || "—")], ["Judge model", judge.model], ["Same family", judge.model ? (judge.same_family ? "yes — weaker independence" : "no") : null], ["Executed", `attempt ${validation.executed_attempt}`]]),
    judge.issues?.length ? h("ul", { class: "note-list" }, judge.issues.map((i) => h("li", { "data-kind": "warning" }, i))) : null,
    h("ol", { class: "timeline" }, (validation.trace || []).map((s) =>
      h("li", { "data-step": s.step, "data-ok": String(s.ok !== false && s.verdict !== "empty") },
        h("span", { class: "step-name" }, humanize(s.step || "step"), s.attempt ? h("span", { class: "step-attempt" }, `attempt ${s.attempt}`) : null),
        h("span", { class: "step-detail" }, stepDetail(s))))));
}

function notesPanel(meta) {
  const items = [
    ...meta.warnings.map((t) => ["warning", t]), ...meta.adjustments.map((t) => ["adjustment", t]), ...meta.assumptions.map((t) => ["assumption", t]),
  ];
  const filters = Object.entries(meta.filters || {});
  if (!items.length && !filters.length) return null;
  return panel("Notes",
    items.length ? h("ul", { class: "note-list" }, items.map(([kind, t]) => h("li", { "data-kind": kind }, h("span", { class: "visually-hidden" }, `${kind}: `), t))) : null,
    filters.length ? kv(filters.map(([k, v]) => [humanize(k), typeof v === "object" ? JSON.stringify(v) : String(v)])) : null);
}

function entityPanel(er) {
  if (!er || !Object.keys(er).length) return null;
  const aliases = Object.entries(er.aliases || {});
  const sponsors = er.top_sponsors || [];
  return panel("Entity resolution",
    aliases.map(([term, list]) => h("div", {},
      h("p", {}, h("b", {}, term), ` matched via ${fmtInt(list.length)} alias${list.length === 1 ? "" : "es"}`),
      h("div", { class: "alias-chips" }, list.slice(0, ALIAS_CAP).map((a) => h("span", {}, a)), list.length > ALIAS_CAP ? h("span", {}, `+${list.length - ALIAS_CAP}`) : null))),
    sponsors.length ? h("details", {}, h("summary", {}, `Top lead sponsors (${sponsors.length})`),
      h("ul", {}, sponsors.map((s) => h("li", {}, `${s.name} — ${fmtInt(s.count)}`)))) : null);
}

function networkPanel(ns) {
  if (!ns) return null;
  return panel("Network pruning", kv([
    ["Nodes", `${fmtInt(ns.nodes_before_pruning)} → ${fmtInt(ns.nodes_after_pruning)}`],
    ["Edges", `${fmtInt(ns.edges_before_pruning)} → ${fmtInt(ns.edges_after_pruning)}`],
    ["Min edge weight", fmtInt(ns.min_edge_weight)],
    ...Object.entries(ns.excluded_by_reason || {}).map(([r, n]) => [humanize(r), fmtInt(n)]),
  ]));
}

function provenancePanel(meta) {
  const p = meta.provenance || {};
  return panel("Provenance", kv([
    ["Source", meta.source], ["API version", p.api_version], ["Data as of", p.data_timestamp], ["Planner", p.planner_model],
    ["Code", p.code_version], ["Requests", fmtInt((p.api_requests || []).length)], ["Citations", meta.citation_policy ? `${meta.citation_policy.mode} · ${meta.citation_policy.pointer_format}` : null],
  ]));
}

function rawPanel(response) {
  const pre = h("pre", { class: "raw-json", tabindex: "0" });
  const details = h("details", {}, h("summary", {}, "Show raw response JSON"), pre);
  details.addEventListener("toggle", () => {
    if (!details.open || pre.textContent) return;
    const { httpStatus: _s, ...body } = response;
    const text = JSON.stringify(body, null, 2);
    pre.textContent = text.length > RAW_JSON_CHAR_CAP ? `${text.slice(0, RAW_JSON_CHAR_CAP)}\n… truncated (${fmtInt(text.length)} chars total; download for the full body)` : text;
  });
  const download = h("button", { type: "button", class: "btn btn-small", onclick: () => {
    const { httpStatus: _s, ...body } = response;
    const url = URL.createObjectURL(new Blob([JSON.stringify(body, null, 2)], { type: "application/json" }));
    const a = h("a", { href: url, download: "ctviz-response.json" });
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } }, "Download JSON");
  return panel("Raw JSON", details, h("div", {}, download));
}

export function renderTrust(host, response) {
  clear(host);
  const meta = response.meta;
  if (!meta) {
    host.append(rawPanel(response));
    return;
  }
  const url = meta.citation_policy?.url_template;
  host.append(...[
    verificationPanel(meta.citation_check), coveragePanel(meta.data_coverage, meta.cohorts, url), tracePanel(meta.validation),
    notesPanel(meta), entityPanel(meta.entity_resolution), networkPanel(meta.network_summary), provenancePanel(meta), rawPanel(response),
  ].filter(Boolean));
}
