// ctviz frontend bootstrap: health/mode badge, ask form, viewer, exhibits, theme.

import { h, clear, debounce } from "./js/dom.js";
import { getHealth, visualize } from "./js/api.js";
import { initTheme, onThemeChange } from "./js/theme.js";
import { readForm, fillForm } from "./js/ask.js";
import { createCitationDrawer } from "./js/citations.js";
import { createViewer } from "./js/viewer.js";
import { createGallery } from "./js/gallery.js";
import { EXAMPLES } from "./js/examples.js";

const state = { mode: "unknown" };
const $ = (id) => document.getElementById(id);

function renderEdition(health) {
  const badge = $("mode-badge");
  const meta = clear($("edition-meta"));
  if (!health) {
    state.mode = "offline";
    badge.dataset.mode = "offline";
    badge.textContent = "Server unreachable";
    meta.append("Start it with make run or make demo-offline.");
    return;
  }
  state.mode = health.mode === "replay" ? "replay" : "live";
  badge.dataset.mode = state.mode;
  badge.textContent = state.mode === "replay" ? "Replay edition · offline" : "Live edition";
  const providers = health.providers || {};
  meta.append(state.mode === "replay"
    ? "canned plans · recorded registry fixtures · no keys"
    : `ClinicalTrials.gov ${health.ctgov || "API v2"} · planner ${providers.openai ? "ready" : "missing key"} · judge ${providers.openrouter ? "ready" : "missing key"}`);
  $("q-hint").textContent = state.mode === "replay"
    ? "Replay mode answers the example questions below (plus “Details of NCT…” lookups) with no network or keys."
    : "Live mode plans with an LLM and queries ClinicalTrials.gov — expect a few seconds to a minute.";
}

function setUrlQuery(query) {
  try {
    const url = new URL(window.location.href);
    url.searchParams.set("q", query);
    history.replaceState(null, "", url);
  } catch {
    /* non-fatal: URL state is a convenience */
  }
}

async function main() {
  initTheme($("theme-toggle"));
  const form = $("ask-form");
  const submit = $("ask-submit");
  const drawer = createCitationDrawer();
  let gallery = null;

  async function run(body) {
    $("q-error").textContent = "";
    submit.disabled = true;
    const done = viewer.showLoading(body.query);
    $("viewer").scrollIntoView({ block: "start" });
    const response = await visualize(body);
    submit.disabled = false;
    if (!done()) return;
    setUrlQuery(body.query);
    await viewer.showResponse(response, body.query);
    gallery?.sync(body.query, response);
  }

  const runQuery = (query) => {
    fillForm(form, { query });
    run({ query });
  };

  const viewer = createViewer({ figure: $("figure"), trust: $("trust"), drawer, getMode: () => state.mode, onPick: runQuery });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const { body, errors } = readForm(form);
    if (errors.length) {
      $("q-error").textContent = errors.join(" ");
      return;
    }
    run(body);
  });
  form.elements.query.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) form.requestSubmit();
  });
  $("clear-structured").addEventListener("click", () => {
    const q = form.elements.query.value;
    form.reset();
    form.elements.query.value = q;
  });
  $("quick-chips").append(...EXAMPLES.slice(0, 4).map((ex) => h("button", { type: "button", class: "chip", onclick: () => runQuery(ex.query) }, ex.query)));

  gallery = createGallery($("exhibit-grid"), { getMode: () => state.mode, onOpen: runQuery });
  viewer.showEmpty();

  const health = await getHealth();
  renderEdition(health);
  gallery.start(state.mode);

  const linked = new URL(window.location.href).searchParams.get("q");
  if (linked && linked.trim().length >= 3) runQuery(linked.trim().slice(0, 500));
  else if (state.mode === "replay") {
    fillForm(form, { query: EXAMPLES[0].query });
    const response = await visualize({ query: EXAMPLES[0].query });
    await viewer.showResponse(response, EXAMPLES[0].query);
    gallery.sync(EXAMPLES[0].query, response);
  }

  const repaint = debounce(() => { viewer.rerender(); gallery.repaintAll(); }, 150);
  onThemeChange(repaint);
  let lastWidth = window.innerWidth;
  window.addEventListener("resize", () => {
    if (Math.abs(window.innerWidth - lastWidth) < 40) return;
    lastWidth = window.innerWidth;
    repaint();
  });
}

// Classic <script defer> tags (Vega) run before deferred module scripts, so vegaEmbed is ready here.
main().catch((err) => {
  const fig = document.getElementById("figure");
  if (fig) fig.replaceChildren(h("div", { class: "error-card", role: "alert" }, h("p", { class: "error-code" }, "CLIENT_ERROR"), h("h3", {}, "The page failed to start"), h("p", {}, String(err?.message || err))));
});
