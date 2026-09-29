// Light/dark theme: follows the OS until the reader picks one; the choice is remembered locally.
// Charts read their colours from the same CSS tokens, so both themes stay one system.

const STORAGE_KEY = "ctviz-theme";
const listeners = new Set();

function stored() {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function systemDark() {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

export function currentTheme() {
  const explicit = document.documentElement.dataset.theme;
  if (explicit === "light" || explicit === "dark") return explicit;
  return systemDark() ? "dark" : "light";
}

function notify() {
  for (const fn of listeners) fn(currentTheme());
}

function label(button) {
  const next = currentTheme() === "dark" ? "light" : "dark";
  button.querySelector(".theme-toggle-label").textContent = next === "dark" ? "Dark" : "Light";
  button.setAttribute("aria-label", `Switch to ${next} theme`);
}

export function initTheme(button) {
  const saved = stored();
  if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
  label(button);
  button.addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* storage unavailable: the choice lasts for this page view only */
    }
    label(button);
    notify();
  });
  window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", () => {
    if (!document.documentElement.dataset.theme) {
      label(button);
      notify();
    }
  });
}

export function onThemeChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Resolved token values for chart specs (Vega can't read CSS variables itself). */
export function chartTheme() {
  const cs = getComputedStyle(document.documentElement);
  const v = (name) => cs.getPropertyValue(name).trim();
  return {
    surface: v("--surface"),
    ink: v("--ink"),
    ink2: v("--ink-2"),
    muted: v("--muted"),
    grid: v("--grid"),
    baseline: v("--rule-strong"),
    edge: v("--edge"),
    cite: v("--cite"),
    neutral: v("--series-neutral"),
    series: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => v(`--series-${i}`)),
    fontMono: "IBM Plex Mono, ui-monospace, Menlo, monospace",
    fontSerif: "Newsreader, Georgia, serif",
  };
}
