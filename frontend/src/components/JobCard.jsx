/**
 * A single posting.
 *
 * Reachability is the most prominent signal, because a role you cannot legally
 * take is worth nothing however well it matches - so it gets colour and the
 * top-right slot rather than being buried in a metadata row.
 */

const REGION_LABELS = {
  worldwide: "Worldwide",
  us: "US only",
  canada: "Canada",
  emea: "EMEA",
  europe: "Europe",
  apac: "APAC",
  india: "India",
  latam: "LATAM",
  unknown: "Region unclear",
};

function regionStyle(job) {
  if (job.reachable_from_india) return "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30";
  if (job.hiring_regions.includes("unknown")) return "bg-zinc-500/10 text-zinc-400 ring-zinc-500/30";
  return "bg-amber-500/10 text-amber-300 ring-amber-500/30";
}

/** Age of the POSTING, not of our sighting - the backend sends age_days. */
function postedLabel(job) {
  const iso = job.posted_at ?? job.first_seen_at;
  const hours = (Date.now() - new Date(iso).getTime()) / 36e5;
  if (hours < 1) return "posted just now";
  if (hours < 24) return `posted ${Math.floor(hours)}h ago`;
  const days = job.age_days ?? Math.floor(hours / 24);
  return days === 1 ? "posted yesterday" : `posted ${days}d ago`;
}

function Pill({ children, className = "" }) {
  return (
    <span
      className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${className}`}
    >
      {children}
    </span>
  );
}

function ExperienceBadge({ job }) {
  if (job.employment_type === "internship")
    return <Pill className="bg-violet-500/10 text-violet-300 ring-violet-500/30">Internship</Pill>;
  if (job.is_new_grad)
    return <Pill className="bg-sky-500/10 text-sky-300 ring-sky-500/30">New grad</Pill>;
  if (job.min_yoe === 0)
    return <Pill className="bg-sky-500/10 text-sky-300 ring-sky-500/30">0 yrs</Pill>;
  if (job.min_yoe === null)
    return <Pill className="bg-zinc-500/10 text-zinc-400 ring-zinc-500/30">yrs n/a</Pill>;
  return <Pill className="bg-zinc-500/10 text-zinc-400 ring-zinc-500/30">{job.min_yoe}+ yrs</Pill>;
}

export default function JobCard({ job }) {
  const regionText = job.hiring_regions.map((r) => REGION_LABELS[r] ?? r).join(" · ");
  const unresolved = job.region_source === "none" || job.region_confidence === "low";

  return (
    <article className="group rounded-lg border border-zinc-800 bg-zinc-900/40 p-4 transition hover:border-zinc-700 hover:bg-zinc-900/80">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <a
            href={job.url}
            target="_blank"
            rel="noopener noreferrer"
            className="block truncate font-medium text-zinc-100 hover:text-white hover:underline"
            title={job.title}
          >
            {job.title}
          </a>
          <div className="mt-0.5 flex items-center gap-2 text-sm text-zinc-400">
            <span className="truncate">{job.company}</span>
            {job.location_raw && (
              <>
                <span className="text-zinc-700">•</span>
                <span className="truncate text-xs">{job.location_raw}</span>
              </>
            )}
          </div>
        </div>

        <div className="flex shrink-0 flex-col items-end gap-1.5">
          <Pill className={regionStyle(job)}>
            {regionText}
            {unresolved && <span className="ml-1 opacity-60">?</span>}
          </Pill>
          <span className="font-mono text-[11px] text-zinc-500">
            {job.match_score.toFixed(0)}
          </span>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <ExperienceBadge job={job} />
        <Pill className="bg-zinc-800/80 text-zinc-300 ring-zinc-700">{job.role_category}</Pill>
        {job.visa_sponsorship === true && (
          <Pill className="bg-emerald-500/10 text-emerald-300 ring-emerald-500/30">
            Sponsors visa
          </Pill>
        )}
        {job.work_auth_required === true && job.visa_sponsorship !== true && (
          <Pill className="bg-red-500/10 text-red-300 ring-red-500/30">Needs work auth</Pill>
        )}
        {job.grad_year && (
          <Pill className="bg-sky-500/10 text-sky-300 ring-sky-500/30">
            Class of {job.grad_year}
          </Pill>
        )}
        {job.tech_stack.slice(0, 5).map((t) => (
          <Pill key={t} className="bg-zinc-800/60 font-mono text-zinc-400 ring-zinc-800">
            {t}
          </Pill>
        ))}
      </div>

      {job.summary && (
        <p className="mt-3 line-clamp-2 text-xs leading-relaxed text-zinc-500">{job.summary}</p>
      )}

      <div className="mt-3 flex items-center justify-between text-[11px] text-zinc-600">
        <span>
          {job.ats ? `${job.company} · ${job.ats}` : job.source} · {postedLabel(job)}
          {job.link_status === "unknown" && (
            <span className="ml-1.5 text-amber-600/80" title="Apply link could not be verified">
              · link unverified
            </span>
          )}
        </span>
        <a
          href={job.url}
          target="_blank"
          rel="noopener noreferrer"
          className="font-medium text-zinc-400 opacity-0 transition group-hover:opacity-100 hover:text-zinc-200"
        >
          Apply →
        </a>
      </div>
    </article>
  );
}
