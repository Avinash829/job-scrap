/** Thin API client. Keeps query-string assembly in one place. */

const BASE = import.meta.env.VITE_API_BASE ?? "";

function toQuery(params) {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    if (Array.isArray(value)) {
      // FastAPI reads repeated keys as a list
      value.forEach((v) => qs.append(key, v));
    } else if (typeof value === "boolean") {
      if (value) qs.append(key, "true");
    } else {
      qs.append(key, String(value));
    }
  }
  return qs.toString();
}

async function get(path, params = {}, signal) {
  const qs = toQuery(params);
  const res = await fetch(`${BASE}/api/v1${path}${qs ? `?${qs}` : ""}`, { signal });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const api = {
  listJobs: (filters, signal) => get("/jobs", filters, signal),
  stats: (signal) => get("/jobs/stats", {}, signal),
  facets: (signal) => get("/jobs/facets", {}, signal),
};
