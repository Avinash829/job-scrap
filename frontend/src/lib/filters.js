/**
 * Client-side filtering, sorting and paging.
 *
 * Mirrors the backend's filter semantics so the snapshot and the API give the
 * same answers. Doing it here means every keystroke is instant and costs no
 * request - and no database compute on a free plan.
 */

export const PAGE_SIZE = 40;

export const DEFAULT_FILTERS = {
  q: "",
  employment_type: [],
  role: [],
  tech: [],
  source: [],
  max_min_yoe: 0,
  include_unknown_yoe: true,
  reachable_only: false,
  remote_only: false,
  new_grad_only: false,
  paid_only: false,
  saved_only: false,
  hide_applied: false,
  // The backend already applies freshness per source (2 days for aggregators,
  // no cap for employer boards, which only list open roles), so this starts off.
  posted_within_days: null,
  order_by: "match_score",
};

/** Filters the user actually changed - shown as a count on the Filters button. */
export function countActive(filters) {
  let n = 0;
  for (const [key, value] of Object.entries(filters)) {
    if (key === "order_by") continue;
    const base = DEFAULT_FILTERS[key];
    if (Array.isArray(value)) {
      if (value.length) n += 1;
    } else if (value !== base) {
      n += 1;
    }
  }
  return n;
}

/** Posting date when the board gives one, else when we first saw it. */
export function effectiveDate(job) {
  return job.posted_at ?? job.first_seen_at ?? null;
}

export function ageInDays(job) {
  const iso = effectiveDate(job);
  if (!iso) return null;
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 864e5);
  return days < 0 ? 0 : days;
}

function matchesQuery(job, q) {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  return (
    job.title.toLowerCase().includes(needle) ||
    job.company.toLowerCase().includes(needle) ||
    (job.location_raw ?? "").toLowerCase().includes(needle)
  );
}

export function applyFilters(jobs, filters, { saved, applied } = {}) {
  return jobs.filter((job) => {
    if (!matchesQuery(job, filters.q)) return false;
    if (filters.employment_type.length && !filters.employment_type.includes(job.employment_type)) return false;
    if (filters.role.length && !filters.role.includes(job.role_category)) return false;
    if (filters.source.length && !filters.source.includes(job.source)) return false;
    // every selected skill must be mentioned
    if (filters.tech.length) {
      const stack = job.tech_stack.map((t) => t.toLowerCase());
      if (!filters.tech.every((t) => stack.includes(t))) return false;
    }
    if (filters.max_min_yoe !== null) {
      if (job.min_yoe === null || job.min_yoe === undefined) {
        if (!filters.include_unknown_yoe) return false;
      } else if (job.min_yoe > filters.max_min_yoe) {
        return false;
      }
    }
    if (filters.reachable_only && !job.reachable_from_india) return false;
    if (filters.remote_only && !job.is_remote) return false;
    if (filters.new_grad_only && !(job.is_new_grad || job.employment_type === "internship")) return false;
    if (filters.paid_only && !job.pay) return false;
    if (filters.posted_within_days !== null) {
      const age = ageInDays(job);
      if (age === null || age > filters.posted_within_days) return false;
    }
    const key = `${job.source}:${job.source_job_id}`;
    if (filters.saved_only && !saved?.has(key)) return false;
    if (filters.hide_applied && applied?.has(key)) return false;
    return true;
  });
}

export function sortJobs(jobs, orderBy) {
  const copy = [...jobs];
  if (orderBy === "match_score") {
    copy.sort((a, b) => b.match_score - a.match_score);
  } else if (orderBy === "posted_at") {
    copy.sort((a, b) => {
      const ad = effectiveDate(a);
      const bd = effectiveDate(b);
      if (!ad) return 1;
      if (!bd) return -1;
      return new Date(bd) - new Date(ad);
    });
  } else {
    copy.sort((a, b) => new Date(b.first_seen_at ?? 0) - new Date(a.first_seen_at ?? 0));
  }
  return copy;
}

/** Counts for the header, computed from the loaded snapshot. */
export function summarize(jobs) {
  const bySource = {};
  let internships = 0;
  let reachable = 0;
  for (const job of jobs) {
    bySource[job.source] = (bySource[job.source] ?? 0) + 1;
    if (job.employment_type === "internship") internships += 1;
    if (job.reachable_from_india) reachable += 1;
  }
  return { total: jobs.length, internships, reachable, sources: Object.keys(bySource).sort() };
}
