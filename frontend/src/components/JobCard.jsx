/**
 * A single posting.
 *
 * Deliberately minimal: what you need to decide whether to click Apply -
 * role, company, location, internship or job, experience required, and how
 * long ago it was posted. Descriptions aren't stored (the backend reads them
 * during a scrape to extract skills and experience, then discards them).
 */

/** Age of the POSTING, not of our sighting - the backend sends age_days. */
function postedLabel(job) {
  if (!job.posted_at) return "Posting date not given";
  const hours = (Date.now() - new Date(job.posted_at).getTime()) / 36e5;
  if (hours < 1) return "Posted just now";
  if (hours < 24) return `Posted ${Math.floor(hours)}h ago`;
  // The backend sends age_days = null for dates too old to be believable.
  if (job.age_days == null) return "Posted over a year ago";
  const days = job.age_days;
  if (days === 1) return "Posted yesterday";
  if (days < 60) return `Posted ${days} days ago`;
  if (days < 365) return `Posted ${Math.floor(days / 30)} months ago`;
  return "Posted over a year ago";
}

function Pill({ children, className = "" }) {
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${className}`}
    >
      {children}
    </span>
  );
}

const TYPE_LABELS = {
  internship: ["Internship", "bg-violet-500/10 text-violet-300 ring-violet-500/30"],
  full_time: ["Full-time job", "bg-sky-500/10 text-sky-300 ring-sky-500/30"],
  part_time: ["Part-time", "bg-zinc-500/10 text-zinc-300 ring-zinc-500/30"],
  contract: ["Contract", "bg-zinc-500/10 text-zinc-300 ring-zinc-500/30"],
  unknown: ["Job", "bg-sky-500/10 text-sky-300 ring-sky-500/30"],
};

function TypeBadge({ job }) {
  const [label, style] = TYPE_LABELS[job.employment_type] ?? TYPE_LABELS.unknown;
  return <Pill className={style}>{label}</Pill>;
}

/** Years of experience - the "can I apply?" signal. */
function ExperienceBadge({ job }) {
  if (job.min_yoe === 0 || (job.min_yoe == null && job.is_new_grad))
    return <Pill className="bg-emerald-500/10 text-emerald-300 ring-emerald-500/30">0 yrs · freshers ok</Pill>;
  if (job.min_yoe == null)
    return <Pill className="bg-zinc-500/10 text-zinc-400 ring-zinc-500/30">Experience not stated</Pill>;
  const range = job.max_yoe && job.max_yoe > job.min_yoe ? `${job.min_yoe}-${job.max_yoe}` : `${job.min_yoe}+`;
  return (
    <Pill className="bg-amber-500/10 text-amber-300 ring-amber-500/30">
      {range} yr{job.min_yoe === 1 && !job.max_yoe ? "" : "s"} experience
    </Pill>
  );
}

export default function JobCard({ job }) {
  return (
    <article className="group rounded-lg border border-zinc-800 bg-zinc-900/40 p-3.5 transition hover:border-zinc-700 hover:bg-zinc-900/80 sm:p-4">
      <a
        href={job.url}
        target="_blank"
        rel="noopener noreferrer"
        className="line-clamp-2 break-words font-medium text-zinc-100 hover:text-white hover:underline"
        title={job.title}
      >
        {job.title}
      </a>
      <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 text-sm text-zinc-400">
        <span className="truncate">{job.company}</span>
        {job.location_raw && (
          <>
            <span className="text-zinc-700" aria-hidden="true">•</span>
            <span className="min-w-0 truncate">{job.location_raw}</span>
          </>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <TypeBadge job={job} />
        <ExperienceBadge job={job} />
        {job.is_remote && (
          <Pill className="bg-zinc-800/80 text-zinc-300 ring-zinc-700">Remote</Pill>
        )}
      </div>

      <div className="mt-3 flex items-center justify-between gap-3">
        <span className="min-w-0 text-xs text-zinc-500">{postedLabel(job)}</span>
        <a
          href={job.url}
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0 rounded-md bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-200 ring-1 ring-inset ring-zinc-700 transition hover:bg-zinc-700 hover:text-white"
        >
          Apply →
        </a>
      </div>
    </article>
  );
}
