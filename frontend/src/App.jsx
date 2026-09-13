import { useMemo, useState } from "react";
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

export default function App() {
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
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

  return (
    <div className="flex h-screen flex-col">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-zinc-800 px-5 py-3">
        <div className="flex items-baseline gap-3">
          <h1 className="font-mono text-sm font-semibold tracking-tight text-zinc-100">
            jobscrap
          </h1>
          <StatsBar stats={stats} />
        </div>

        <div className="flex items-center gap-1.5">
          {SORTS.map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={filters.order_by === value}
              onClick={() => setFilters({ ...filters, order_by: value, offset: 0 })}
              className={`rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset transition ${
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
        <div className="hidden md:block">
          <FilterPanel
            filters={filters}
            setFilters={setFilters}
            sources={sources}
            activeCount={activeCount}
            onReset={() => setFilters(DEFAULT_FILTERS)}
          />
        </div>

        <main className="min-h-0 overflow-y-auto p-4">
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
            {pages > 1 && (
              <div className="flex items-center gap-2 text-xs">
                <button
                  type="button"
                  disabled={filters.offset === 0}
                  onClick={() =>
                    setFilters({
                      ...filters,
                      offset: Math.max(0, filters.offset - PAGE_SIZE),
                    })
                  }
                  className="rounded px-2 py-1 text-zinc-400 ring-1 ring-inset ring-zinc-800 disabled:opacity-30 enabled:hover:text-zinc-200"
                >
                  ←
                </button>
                <span className="font-mono text-zinc-500">
                  {page}/{pages}
                </span>
                <button
                  type="button"
                  disabled={page >= pages}
                  onClick={() =>
                    setFilters({ ...filters, offset: filters.offset + PAGE_SIZE })
                  }
                  className="rounded px-2 py-1 text-zinc-400 ring-1 ring-inset ring-zinc-800 disabled:opacity-30 enabled:hover:text-zinc-200"
                >
                  →
                </button>
              </div>
            )}
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
        </main>
      </div>
    </div>
  );
}
