/**
 * Loading the job list.
 *
 * Primary source is the static snapshot the scrape publishes
 * (`/jobs.json`): it comes off Vercel's CDN in milliseconds and works while
 * the API is asleep - Render's free tier suspends after 15 idle minutes, so
 * the first visit of the day used to wait up to a minute.
 *
 * The API is the fallback (and what `npm run dev` uses before a snapshot has
 * ever been exported). Either way the rest of the app sees one shape and
 * filters in the browser: the whole corpus is a few thousand rows.
 */
import { api } from "./client.js";

const SNAPSHOT_URL = "/jobs.json";

/** Expand the snapshot's short keys ({t: "..."} -> {title: "..."}). */
function expand(rows, fields) {
  const names = Object.entries(fields);
  return rows.map((row) => {
    const job = {
      location_raw: null,
      min_yoe: null,
      max_yoe: null,
      is_new_grad: false,
      is_remote: false,
      hiring_regions: [],
      tech_stack: [],
      posted_at: null,
      age_days: null,
      pay: null,
      match_score: 0,
    };
    for (const [key, name] of names) {
      if (row[key] !== undefined) job[name] = row[key];
    }
    job.reachable_from_india = !!row.x;
    return job;
  });
}

export async function loadJobs(signal) {
  try {
    const res = await fetch(SNAPSHOT_URL, { signal, cache: "no-cache" });
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data.jobs)) {
        return {
          jobs: expand(data.jobs, data.fields ?? {}),
          generatedAt: data.generated_at ?? null,
          origin: "snapshot",
        };
      }
    }
  } catch (err) {
    if (err.name === "AbortError") throw err;
    // fall through to the API
  }

  const page = await api.listJobs({ limit: 2000, offset: 0, order_by: "match_score" }, signal);
  return {
    jobs: (page.items ?? []).map((j) => ({ ...j, pay: j.pay ?? null })),
    generatedAt: null,
    origin: "api",
  };
}
