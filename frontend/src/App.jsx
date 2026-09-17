import { useEffect, useMemo, useRef, useState } from "react";
import FilterPanel from "./components/FilterPanel.jsx";
import JobCard from "./components/JobCard.jsx";
import { loadJobs } from "./api/jobs.js";
import {
  DEFAULT_FILTERS,
  PAGE_SIZE,
  applyFilters,
  countActive,
  sortJobs,
  summarize,
} from "./lib/filters.js";
import { jobKey, loadTracking, readAndStampVisit, toggleIn } from "./lib/tracking.js";

const SORTS = [
  ["match_score", "Best match"],
  ["posted_at", "Newest"],
  ["first_seen_at", "Recently found"],
];

function relativeTime(iso) {
  if (!iso) return null;
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 6e4);
  if (mins < 2) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function Skeleton() {
  return (
    <div className="space-y-2" aria-hidden="true">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-4">
          <div className="h-4 w-2/3 animate-pulse rounded bg-zinc-800" />
          <div className="mt-2 h-3 w-2/5 animate-pulse rounded bg-zinc-800/70" />
          <div className="mt-4 flex gap-2">
            <div className="h-5 w-20 animate-pulse rounded-md bg-zinc-800/70" />
            <div className="h-5 w-28 animate-pulse rounded-md bg-zinc-800/60" />
          </div>
        </div>
      ))}
    </div>
  );
}

function LoadError({ message, onRetry }) {
  return (
    <div className="rounded-xl border border-red-900/50 bg-red-950/30 p-5 text-sm text-red-200">
      <p className="font-medium">Could not load jobs</p>
      <p className="mt-1 text-xs text-red-300/80">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 rounded-lg bg-red-500/15 px-3 py-1.5 text-xs font-medium text-red-100 ring-1 ring-inset ring-red-500/30 hover:bg-red-500/25"
      >
        Try again
      </button>
    </div>
  );
}

function FilterSheet({ open, onClose, count, children }) {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => e.key === "Escape" && onClose();
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previous;
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 md:hidden" role="dialog" aria-modal="true" aria-label="Filters">
      <button type="button" aria-label="Close filters" onClick={onClose} className="absolute inset-0 bg-black/70" />
      <div className="absolute inset-x-0 bottom-0 flex max-h-[92dvh] flex-col overflow-hidden rounded-t-2xl border-t border-zinc-800 bg-zinc-950 shadow-2xl">
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        <div className="shrink-0 border-t border-zinc-800 p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
          <button
            type="button"
            onClick={onClose}
            className="w-full rounded-xl bg-emerald-500 py-3 text-sm font-semibold text-zinc-950 active:bg-emerald-400"
          >
            Show {count} result{count === 1 ? "" : "s"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Pager({ page, pages, onPage }) {
  if (pages <= 1) return null;
  const btn =
    "min-w-10 rounded-lg px-3 py-2 text-zinc-300 ring-1 ring-inset ring-zinc-800 disabled:opacity-30 enabled:hover:bg-zinc-800";
  return (
    <div className="flex items-center gap-2 text-xs">
      <button type="button" aria-label="Previous page" disabled={page === 1} onClick={() => onPage(page - 1)} className={btn}>
        ←
      </button>
      <span className="font-mono text-zinc-500">
        {page}/{pages}
      </span>
      <button type="button" aria-label="Next page" disabled={page >= pages} onClick={() => onPage(page + 1)} className={btn}>
        →
      </button>
    </div>
  );
}

export default function App() {
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [page, setPage] = useState(1);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [data, setData] = useState({ jobs: [], generatedAt: null, origin: null });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [nonce, setNonce] = useState(0);

  const [tracking, setTracking] = useState(() => loadTracking());
  const lastVisit = useRef(readAndStampVisit()).current;
  const resultsRef = useRef(null);

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    loadJobs(ctrl.signal)
      .then((loaded) => setData(loaded))
      .catch((err) => {
        if (err.name !== "AbortError") setError(err.message);
      })
      .finally(() => setLoading(false));
    return () => ctrl.abort();
  }, [nonce]);

  const stats = useMemo(() => summarize(data.jobs), [data.jobs]);
  const visible = useMemo(() => {
    const matched = applyFilters(data.jobs, filters, tracking);
    return sortJobs(matched, filters.order_by);
  }, [data.jobs, filters, tracking]);

  const pages = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const current = Math.min(page, pages);
  const items = visible.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE);
  const activeCount = countActive(filters);

  const patch = (changes) => {
    setFilters((f) => ({ ...f, ...changes }));
    setPage(1);
  };
  const goToPage = (next) => {
    setPage(next);
    resultsRef.current?.scrollTo({ top: 0 });
  };
  const toggleTrack = (job, kind) => {
    const key = jobKey(job);
    setTracking((t) => ({ ...t, [kind]: toggleIn(t[kind], key, kind) }));
  };

  const panel = (onClose) => (
    <FilterPanel
      filters={filters}
      patch={patch}
      sources={stats.sources}
      activeCount={activeCount}
      onReset={() => patch(DEFAULT_FILTERS)}
      onClose={onClose}
      savedCount={tracking.saved.size}
      appliedCount={tracking.applied.size}
    />
  );

  return (
    <div className="flex h-[100dvh] flex-col bg-zinc-950">
      <header className="shrink-0 border-b border-zinc-800/80 bg-zinc-950/95 px-4 py-3 backdrop-blur sm:px-5">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
            <h1 className="font-mono text-sm font-semibold tracking-tight text-zinc-100">jobscrap</h1>
            <p className="flex flex-wrap items-center gap-x-3 text-xs text-zinc-500">
              <span>
                <span className="font-mono text-zinc-300">{stats.total}</span> matches
              </span>
              <span>
                <span className="font-mono text-violet-300">{stats.internships}</span> internships
              </span>
              {data.generatedAt && <span className="hidden sm:inline">updated {relativeTime(data.generatedAt)}</span>}
            </p>
          </div>

          <div className="-mx-4 flex items-center gap-1.5 overflow-x-auto px-4 pb-0.5 sm:mx-0 sm:px-0">
            {SORTS.map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={filters.order_by === value}
                onClick={() => patch({ order_by: value })}
                className={`shrink-0 whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-medium ring-1 ring-inset transition ${
                  filters.order_by === value
                    ? "bg-zinc-100 text-zinc-900 ring-zinc-100"
                    : "bg-zinc-900 text-zinc-400 ring-zinc-800 hover:text-zinc-200"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[256px_1fr]">
        <div className="hidden min-h-0 md:block">{panel(null)}</div>

        <FilterSheet open={sheetOpen} onClose={() => setSheetOpen(false)} count={visible.length}>
          {panel(() => setSheetOpen(false))}
        </FilterSheet>

        <main ref={resultsRef} className="min-h-0 overflow-y-auto px-3 pb-24 pt-3 sm:px-4 sm:pb-6 sm:pt-4">
          <div className="mb-3 md:hidden">
            <input
              type="search"
              value={filters.q}
              onChange={(e) => patch({ q: e.target.value })}
              placeholder="Search role, company or city"
              aria-label="Search role, company or city"
              className="w-full rounded-xl border border-zinc-800 bg-zinc-900 px-3.5 py-3 text-base text-zinc-100 placeholder-zinc-600 outline-none focus:border-zinc-600"
            />
          </div>

          <div className="mb-3 flex items-center justify-between gap-3">
            <p className="text-xs text-zinc-500">
              {loading ? "Loading…" : `${visible.length} of ${stats.total} shown`}
              {data.origin === "api" && !loading && (
                <span className="ml-2 text-amber-500/80">· live from API</span>
              )}
            </p>
            <Pager page={current} pages={pages} onPage={goToPage} />
          </div>

          {loading && <Skeleton />}
          {!loading && error && <LoadError message={error} onRetry={() => setNonce((n) => n + 1)} />}

          {!loading && !error && !visible.length && (
            <div className="rounded-xl border border-zinc-800 bg-zinc-900/30 p-8 text-center">
              <p className="text-sm text-zinc-300">No jobs match these filters.</p>
              <p className="mx-auto mt-1 max-w-xs text-xs text-zinc-500">
                Try clearing the skills filter, or widening Experience to “Any”.
              </p>
              <button
                type="button"
                onClick={() => patch(DEFAULT_FILTERS)}
                className="mt-4 rounded-lg bg-zinc-800 px-3.5 py-2 text-xs font-medium text-zinc-100 hover:bg-zinc-700"
              >
                Clear filters
              </button>
            </div>
          )}

          <div className="space-y-2.5">
            {items.map((job) => {
              const key = jobKey(job);
              return (
                <JobCard
                  key={key}
                  job={job}
                  lastVisit={lastVisit}
                  saved={tracking.saved.has(key)}
                  applied={tracking.applied.has(key)}
                  onSave={() => toggleTrack(job, "saved")}
                  onApplied={() => toggleTrack(job, "applied")}
                />
              );
            })}
          </div>

          {items.length > 0 && pages > 1 && (
            <div className="mt-5 flex justify-center">
              <Pager page={current} pages={pages} onPage={goToPage} />
            </div>
          )}
        </main>
      </div>

      {/* Phones: the Filters button stays fixed, so it is reachable at any
          scroll position. Bottom-RIGHT, not centred: centred, it sat on top
          of the card text underneath it. */}
      <button
        type="button"
        onClick={() => setSheetOpen(true)}
        className="fixed bottom-[max(1rem,env(safe-area-inset-bottom))] right-4 z-40 flex items-center gap-2 rounded-full bg-zinc-100 px-4 py-3 text-sm font-semibold text-zinc-900 shadow-xl shadow-black/50 active:bg-white md:hidden"
      >
        <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" className="h-4 w-4">
          <path d="M3 5a1 1 0 0 1 1-1h12a1 1 0 0 1 .8 1.6L12 11.93V16a1 1 0 0 1-1.45.9l-2-1A1 1 0 0 1 8 15v-3.07L3.2 5.6A1 1 0 0 1 3 5Z" />
        </svg>
        Filters
        {activeCount > 0 && (
          <span className="rounded-full bg-emerald-500 px-2 py-0.5 text-[11px] font-bold text-zinc-950">
            {activeCount}
          </span>
        )}
      </button>
    </div>
  );
}
