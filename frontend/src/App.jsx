import { useEffect, useMemo, useState } from "react";
import FilterPanel from "./components/FilterPanel.jsx";
import JobCard from "./components/JobCard.jsx";
import { useJobs, useStats } from "./hooks/useJobs.js";

const PAGE_SIZE = 40;

const DEFAULT_FILTERS = {
  max_min_yoe: 0,
  include_unknown_yoe: true,
  reachable_only: false,
  remote_only: false,
  new_grad_only: false,
  role: [],
  source: [],
  employment_type: [],
  tech: [],
  q: "",
  // The backend already applies freshness per source: 2 days for aggregators
  // (which keep dead listings), no cap for employer ATS boards (which only
  // ever return open requisitions). Re-applying a cap here would hide live
  // Greenhouse/Lever roles that are simply older.
  posted_within_days: null,
  order_by: "match_score",
  limit: PAGE_SIZE,
  offset: 0,
};

const SORTS = [
  ["match_score", "Best match"],
  ["posted_at", "Newest posted"],
  ["first_seen_at", "Newest found"],
];

/** Filters that differ from the defaults - shown as a count on the panel. */
function countActive(filters) {
  let n = 0;
  for (const [key, value] of Object.entries(filters)) {
    if (key === "limit" || key === "offset" || key === "order_by") continue;
    const base = DEFAULT_FILTERS[key];
    if (Array.isArray(value)) {
      if (value.length) n += 1;
    } else if (value !== base && !(value === "" && base === "")) {
      n += 1;
    }
  }
  return n;
}

function StatsBar({ stats }) {
  if (!stats) return null;
  const stale = stats.connectors.filter((c) => !c.ok);

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-500">
      <span>
        <span className="font-mono text-zinc-300">{stats.active}</span> in scope
      </span>
      <span>
        <span className="font-mono text-emerald-400">{stats.reachable_from_india}</span>{" "}
        reachable
      </span>
      {stats.pending_enrichment > 0 && (
        <span title="Rows where rules could not resolve region or experience">
          <span className="font-mono text-amber-400">{stats.pending_enrichment}</span>{" "}
          unenriched
        </span>
      )}
      {stale.length > 0 && (
        <span className="text-red-400" title={stale.map((c) => c.connector).join(", ")}>
          {stale.length} connector{stale.length > 1 ? "s" : ""} failing
        </span>
      )}
    </div>
  );
}

function Skeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="h-32 animate-pulse rounded-lg bg-zinc-900/60" />
      ))}
    </div>
  );
}

function ApiError({ message, onRetry }) {
  return (
    <div className="mb-3 rounded-lg border border-red-900/50 bg-red-950/30 p-4 text-sm text-red-300">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-medium">Could not reach the API</p>
          <p className="mt-1 text-xs text-red-400/80">{message}</p>
          <p className="mt-2 font-mono text-[11px] text-red-400/60">
            cd backend &amp;&amp; .venv\Scripts\activate &amp;&amp; uvicorn app.main:app
            --reload --port 8000
          </p>
        </div>
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 rounded-md bg-red-500/15 px-2.5 py-1 text-xs font-medium text-red-200 ring-1 ring-inset ring-red-500/30 hover:bg-red-500/25"
        >
          Retry
        </button>
      </div>
    </div>
  );
}

function Pager({ page, pages, offset, onPage }) {
  if (pages <= 1) return null;
  const btn =
    "min-w-9 rounded-md px-3 py-1.5 text-zinc-400 ring-1 ring-inset ring-zinc-800 disabled:opacity-30 enabled:hover:text-zinc-200";
  return (
    <div className="flex items-center gap-2 text-xs">
      <button
        type="button"
        aria-label="Previous page"
        disabled={offset === 0}
        onClick={() => onPage(Math.max(0, offset - PAGE_SIZE))}
        className={btn}
      >
        ←
      </button>
      <span className="font-mono text-zinc-500">
        {page}/{pages}
      </span>
      <button
        type="button"
        aria-label="Next page"
        disabled={page >= pages}
        onClick={() => onPage(offset + PAGE_SIZE)}
        className={btn}
      >
        →
      </button>
    </div>
  );
}

/**
 * Full-screen filter sheet for phones. The desktop sidebar is hidden below
 * the md breakpoint, which left mobile with no way to filter at all.
 */
function FilterSheet({ open, onClose, total, loading, children }) {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => e.key === "Escape" && onClose();
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 md:hidden"
      role="dialog"
      aria-modal="true"
      aria-label="Filters"
    >
      <button
        type="button"
        aria-label="Close filters"
        onClick={onClose}
        className="absolute inset-0 bg-black/60"
      />
      <div className="absolute inset-x-0 bottom-0 flex max-h-[92dvh] flex-col overflow-hidden rounded-t-2xl border-t border-zinc-800 bg-zinc-950 shadow-2xl">
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        <div className="shrink-0 border-t border-zinc-800 bg-zinc-950 p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
          <button
            type="button"
            onClick={onClose}
            className="w-full rounded-lg bg-emerald-500 py-3 text-sm font-semibold text-zinc-950 active:bg-emerald-400"
          >
            {loading ? "Updating…" : `Show ${total} result${total === 1 ? "" : "s"}`}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [sheetOpen, setSheetOpen] = useState(false);
  const { items, total, loading, error, reload } = useJobs(filters);
  const { stats, reload: reloadStats } = useStats();

  const sources = useMemo(
    () => (stats ? Object.keys(stats.by_source).sort() : []),
    [stats]
  );
  const activeCount = useMemo(() => countActive(filters), [filters]);

  const page = Math.floor(filters.offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const retry = () => {
    reload();
    reloadStats();
  };

  const goToPage = (offset) => {
    setFilters({ ...filters, offset });
    document.getElementById("results")?.scrollTo({ top: 0 });
  };

  const panel = (onClose) => (
    <FilterPanel
      filters={filters}
      setFilters={setFilters}
      sources={sources}
      activeCount={activeCount}
      onReset={() => setFilters(DEFAULT_FILTERS)}
      onClose={onClose}
    />
  );

  return (
    <div className="flex h-[100dvh] flex-col">
      <header className="flex shrink-0 flex-col gap-2 border-b border-zinc-800 px-4 py-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between sm:gap-3 sm:px-5">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="font-mono text-sm font-semibold tracking-tight text-zinc-100">
            jobscrap
          </h1>
          <StatsBar stats={stats} />
        </div>

        <div className="-mx-4 flex items-center gap-1.5 overflow-x-auto px-4 sm:mx-0 sm:px-0">
          {SORTS.map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={filters.order_by === value}
              onClick={() => setFilters({ ...filters, order_by: value, offset: 0 })}
              className={`shrink-0 whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs font-medium ring-1 ring-inset transition sm:px-2 sm:py-1 ${
                filters.order_by === value
                  ? "bg-zinc-100 text-zinc-900 ring-zinc-100"
                  : "bg-zinc-800/50 text-zinc-400 ring-zinc-800 hover:text-zinc-200"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[252px_1fr]">
        <div className="hidden min-h-0 md:block">{panel(null)}</div>

        <FilterSheet
          open={sheetOpen}
          onClose={() => setSheetOpen(false)}
          total={total}
          loading={loading}
        >
          {panel(() => setSheetOpen(false))}
        </FilterSheet>

        <main id="results" className="min-h-0 overflow-y-auto p-3 sm:p-4">
          {/* Phone toolbar: search stays one tap away, the rest lives in the sheet. */}
          <div className="mb-3 flex items-center gap-2 md:hidden">
            <input
              type="search"
              value={filters.q ?? ""}
              onChange={(e) => setFilters({ ...filters, q: e.target.value, offset: 0 })}
              placeholder="Search role or company"
              aria-label="Search role or company"
              className="min-w-0 flex-1 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2.5 text-base text-zinc-200 placeholder-zinc-600 outline-none focus:border-zinc-600"
            />
            <button
              type="button"
              onClick={() => setSheetOpen(true)}
              className="flex shrink-0 items-center gap-1.5 rounded-lg bg-zinc-800 px-3.5 py-2.5 text-sm font-medium text-zinc-100 ring-1 ring-inset ring-zinc-700 active:bg-zinc-700"
            >
              <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" className="h-4 w-4">
                <path d="M3 5a1 1 0 0 1 1-1h12a1 1 0 0 1 .8 1.6L12 11.93V16a1 1 0 0 1-1.45.9l-2-1A1 1 0 0 1 8 15v-3.07L3.2 5.6A1 1 0 0 1 3 5Z" />
              </svg>
              Filters
              {activeCount > 0 && (
                <span className="rounded bg-emerald-500 px-1.5 text-[11px] font-semibold text-zinc-950">
                  {activeCount}
                </span>
              )}
            </button>
          </div>

          {/* Only shown when there is genuinely nothing to display - an error
              banner sitting above a full list of results is just confusing. */}
          {error && !items.length && <ApiError message={error} onRetry={retry} />}

          <div className="mb-3 flex items-center justify-between">
            <p className="text-xs text-zinc-500">
              {loading
                ? "Loading…"
                : `${total} posting${total === 1 ? "" : "s"}`}
              {error && items.length > 0 && (
                <span className="ml-2 text-amber-500">· showing cached results</span>
              )}
            </p>
            <Pager page={page} pages={pages} offset={filters.offset} onPage={goToPage} />
          </div>

          {loading && !items.length && <Skeleton />}

          {!loading && !error && !items.length && (
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/30 p-8 text-center">
              <p className="text-sm text-zinc-400">Nothing matches these filters.</p>
              <p className="mt-1 text-xs text-zinc-600">
                Try clearing the skills filter, or widening Experience to "Any".
              </p>
              <button
                type="button"
                onClick={() => setFilters(DEFAULT_FILTERS)}
                className="mt-3 rounded-md bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:bg-zinc-700"
              >
                Reset filters
              </button>
            </div>
          )}

          <div className="space-y-2">
            {items.map((job) => (
              <JobCard key={`${job.source}:${job.source_job_id}`} job={job} />
            ))}
          </div>

          {items.length > 0 && pages > 1 && (
            <div className="mt-4 flex justify-center pb-[env(safe-area-inset-bottom)]">
              <Pager page={page} pages={pages} offset={filters.offset} onPage={goToPage} />
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
