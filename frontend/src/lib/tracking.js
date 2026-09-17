/**
 * Saved / applied jobs and "new since your last visit".
 *
 * Deliberately per-browser (localStorage): no login, no server, nothing to
 * pay for. Every read is guarded because storage throws in private windows
 * and when site data is blocked.
 */

const KEYS = { saved: "jobscrap.saved", applied: "jobscrap.applied", visit: "jobscrap.lastVisit" };

function readSet(key) {
  try {
    const raw = localStorage.getItem(key);
    return new Set(raw ? JSON.parse(raw) : []);
  } catch {
    return new Set();
  }
}

function writeSet(key, set) {
  try {
    localStorage.setItem(key, JSON.stringify([...set]));
  } catch {
    /* private window or storage disabled - the UI still works, it just forgets */
  }
}

export const jobKey = (job) => `${job.source}:${job.source_job_id}`;

export function loadTracking() {
  return { saved: readSet(KEYS.saved), applied: readSet(KEYS.applied) };
}

export function toggleIn(set, key, storageKey) {
  const next = new Set(set);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  writeSet(KEYS[storageKey], next);
  return next;
}

/**
 * The previous visit's timestamp, read once per session, and the current
 * visit stamped immediately - so "new" means "since you last opened this",
 * not "since this render".
 */
export function readAndStampVisit() {
  let previous = null;
  try {
    previous = localStorage.getItem(KEYS.visit);
    localStorage.setItem(KEYS.visit, new Date().toISOString());
  } catch {
    /* ignore */
  }
  return previous ? new Date(previous) : null;
}

export function isNewSince(job, since) {
  if (!since || !job.first_seen_at) return false;
  return new Date(job.first_seen_at) > since;
}
