// Ask form → VisualizeRequest body (client-side validation mirrors schemas/request.py), plus
// readable rendering of every error code the API can return.

import { h } from "./dom.js";

const NCT_RE = /^NCT\d{8}$/;
const TEXT_FIELDS = ["drug_name", "condition", "sponsor", "country"];

function intOrNull(raw, name, min, max, errors) {
  if (raw === "" || raw == null) return null;
  const n = Number(raw);
  if (!Number.isInteger(n) || n < min || n > max) {
    errors.push(`${name} must be a whole number between ${min} and ${max}.`);
    return null;
  }
  return n;
}

/** Returns {body, errors[]} — never throws. */
export function readForm(form) {
  const fd = new FormData(form);
  const errors = [];
  const query = String(fd.get("query") || "").trim();
  if (query.length < 3) errors.push("Ask a question of at least 3 characters.");
  if (query.length > 500) errors.push("Keep the question under 500 characters.");
  const body = { query };
  for (const f of TEXT_FIELDS) {
    const v = String(fd.get(f) || "").trim();
    if (v) body[f] = v;
  }
  if (body.sponsor) body.sponsor_role = fd.get("sponsor_role") === "any" ? "any" : "lead";
  const phases = fd.getAll("trial_phase").map(String);
  if (phases.length) body.trial_phase = phases;
  const statuses = fd.getAll("status").map(String);
  if (statuses.length) body.status = statuses;
  const studyType = String(fd.get("study_type") || "");
  if (studyType) body.study_type = studyType;
  const maxYear = new Date().getFullYear() + 5;
  const start = intOrNull(fd.get("start_year"), "Start year", 1900, maxYear, errors);
  const end = intOrNull(fd.get("end_year"), "End year", 1900, maxYear, errors);
  if (start != null) body.start_year = start;
  if (end != null) body.end_year = end;
  if (start != null && end != null && end < start) errors.push("End year must be on or after the start year.");
  const ncts = String(fd.get("nct_ids") || "").toUpperCase().split(/[\s,;]+/).filter(Boolean);
  const badNct = ncts.filter((x) => !NCT_RE.test(x));
  if (badNct.length) errors.push(`Not NCT ids: ${badNct.join(", ")} (expected NCT + 8 digits).`);
  else if (ncts.length > 50) errors.push("At most 50 NCT ids.");
  else if (ncts.length) body.nct_ids = [...new Set(ncts)];
  const topN = intOrNull(fd.get("top_n"), "Top N", 3, 50, errors);
  if (topN != null) body.options = { top_n: topN };
  return { body, errors };
}

/** Put a request body back into the form (used by exhibits, chips and ?q= links). */
export function fillForm(form, body) {
  form.reset();
  form.elements.query.value = body.query || "";
}

const ERROR_COPY = {
  OUT_OF_SCOPE: ["Outside what ClinicalTrials.gov can answer", "info"],
  NO_MATCHING_TRIALS: ["No trials matched", "info"],
  PLAN_INVALID: ["The query plan did not pass validation", "error"],
  INVALID_REQUEST: ["The request was rejected", "error"],
  UPSTREAM_API_ERROR: ["ClinicalTrials.gov did not respond", "error"],
  LLM_UNAVAILABLE: ["The planner model is unavailable", "error"],
  CITATION_CHECK_FAILED: ["The citation verifier rejected this result", "error"],
  INTERNAL_ERROR: ["Something broke on the server", "error"],
  NETWORK_ERROR: ["Can't reach the ctviz API", "error"],
};

function hintFor(code, mode) {
  switch (code) {
    case "LLM_UNAVAILABLE": return "Check the OpenAI key on the server, or run the keyless demo with `make demo-offline` (PLANNER_MODE=replay).";
    case "UPSTREAM_API_ERROR": return "The registry API may be rate-limiting or down. Try again in a moment.";
    case "NO_MATCHING_TRIALS": return "Loosen a filter (phase, status, years) or check the spelling of the drug or condition.";
    case "OUT_OF_SCOPE": return "ctviz answers questions about registered trials: counts, trends, distributions, networks and key facts.";
    case "PLAN_INVALID": return mode === "replay" ? "Replay mode answers only its offline example questions — pick one below." : "Try rephrasing the question more concretely.";
    case "NETWORK_ERROR": return "Is the server running? Start it with `make run` or `make demo-offline`.";
    default: return null;
  }
}

/** Error card. `onPick(query)` runs a suggested query (replay's available_queries). */
export function errorCard(error, { mode, onPick, meta } = {}) {
  const [title, tone] = ERROR_COPY[error.code] || ["Request failed", "error"];
  const details = error.details || {};
  const suggestions = Array.isArray(details.available_queries) ? details.available_queries : [];
  const lists = Object.entries(details).filter(([k, v]) => k !== "available_queries" && Array.isArray(v) && v.length);
  const message = error.code === "PLAN_INVALID" && suggestions.length ? String(error.message).split("Available example queries:")[0] : error.message;
  const hint = hintFor(error.code, mode);
  return h("div", { class: "error-card", "data-tone": tone, role: "alert" },
    h("p", { class: "error-code" }, error.code),
    h("h3", {}, title),
    h("p", {}, message),
    meta?.query_interpretation ? h("p", { class: "figure-question" }, `Interpreted as: ${meta.query_interpretation}`) : null,
    lists.map(([k, v]) => h("div", {}, h("p", { class: "eyebrow" }, k.replace(/_/g, " ")), h("ul", {}, v.slice(0, 20).map((x) => h("li", {}, typeof x === "string" ? x : JSON.stringify(x)))))),
    hint ? h("p", { class: "ask-hint" }, hint) : null,
    suggestions.length ? h("div", { class: "chip-row" }, suggestions.map((q) => h("button", { type: "button", class: "chip", onclick: () => onPick?.(q) }, q))) : null);
}
