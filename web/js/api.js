// Same-origin client for the ctviz API. Successful responses are cached per request body so the
// gallery and the viewer share one fetch (and one LLM call in live mode).

const cache = new Map();

export const requestKey = (body) => JSON.stringify(body);

export async function getHealth() {
  try {
    const resp = await fetch("/health", { headers: { accept: "application/json" } });
    if (!resp.ok) return null;
    const json = await resp.json();
    return json && typeof json === "object" ? json : null;
  } catch {
    return null;
  }
}

function clientFailure(code, message) {
  return { schema_version: "1.0.0", ok: false, visualization: null, meta: null, error: { code, message, details: null } };
}

async function postVisualize(body) {
  let resp;
  try {
    resp = await fetch("/v1/visualize", {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    return clientFailure("NETWORK_ERROR", `Could not reach the ctviz API (${err?.message || "network error"}).`);
  }
  let json;
  try {
    json = await resp.json();
  } catch {
    return clientFailure("INTERNAL_ERROR", `The API answered HTTP ${resp.status} without a JSON body.`);
  }
  if (!json || typeof json.ok !== "boolean") {
    return clientFailure("INTERNAL_ERROR", `Unexpected response shape (HTTP ${resp.status}).`);
  }
  return { ...json, httpStatus: resp.status };
}

/** POST /v1/visualize. Concurrent identical requests share one promise; only ok results stay cached. */
export function visualize(body) {
  const key = requestKey(body);
  const hit = cache.get(key);
  if (hit) return hit;
  const pending = postVisualize(body).then((result) => {
    if (!result.ok) cache.delete(key);
    return result;
  });
  cache.set(key, pending);
  return pending;
}

export function cachedResult(body) {
  return cache.get(requestKey(body)) ?? null;
}
