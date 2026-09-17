/**
 * A single posting.
 *
 * Deliberately minimal: what you need to decide whether to click Apply -
 * role, company, location, internship or job, experience required, stated pay
 * when there is one, and how long ago it appeared. Descriptions aren't stored
 * (the scrape reads them to extract skills and experience, then discards them).
 */
import { ageInDays } from "../lib/filters.js";
import { isNewSince } from "../lib/tracking.js";

/**
 * Age of the posting. Many employer boards (EA, most Workday tenants) publish
 * no date at all, so rather than "date not given" we say when WE first saw it,
 * which is the honest signal and the one worth acting on.
 */
function ageLabel(job) {
  const days = ageInDays(job);
  if (days === null) return null;
  const verb = job.posted_at ? "Posted" : "Found";
  if (days === 0) return `${verb} today`;
  if (days === 1) return `${verb} yesterday`;
  if (days < 60) return `${verb} ${days} days ago`;
  if (days < 365) return `${verb} ${Math.floor(days / 30)} months ago`;
  return `${verb} over a year ago`;
}

function Pill({ children, className = "" }) {
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${className}`}>
      {children}
    </span>
  );
}

const TYPE_LABELS = {
  internship: ["Internship", "bg-violet-500/10 text-violet-200 ring-violet-500/30"],
  full_time: ["Full-time job", "bg-sky-500/10 text-sky-200 ring-sky-500/30"],
  part_time: ["Part-time", "bg-zinc-500/10 text-zinc-300 ring-zinc-500/30"],
  contract: ["Contract", "bg-zinc-500/10 text-zinc-300 ring-zinc-500/30"],
  unknown: ["Job", "bg-sky-500/10 text-sky-200 ring-sky-500/30"],
};

/** Years of experience - the "can I apply?" signal. */
function experienceBadge(job) {
  if (job.min_yoe === 0 || (job.min_yoe == null && job.is_new_grad))
    return ["0 yrs · freshers ok", "bg-emerald-500/10 text-emerald-200 ring-emerald-500/30"];
  if (job.min_yoe == null)
    return ["Experience not stated", "bg-zinc-500/10 text-zinc-400 ring-zinc-500/30"];
  const range = job.max_yoe && job.max_yoe > job.min_yoe ? `${job.min_yoe}-${job.max_yoe}` : `${job.min_yoe}+`;
  const plural = job.min_yoe === 1 && !job.max_yoe ? "yr" : "yrs";
  return [`${range} ${plural} experience`, "bg-amber-500/10 text-amber-200 ring-amber-500/30"];
}

function IconButton({ active, onClick, label, activeLabel, className = "" }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      title={active ? activeLabel : label}
      className={`shrink-0 rounded-lg px-2.5 py-1.5 text-xs font-medium ring-1 ring-inset transition ${className}`}
    >
      {active ? activeLabel : label}
    </button>
  );
}

export default function JobCard({ job, lastVisit, saved, applied, onSave, onApplied }) {
  const [typeLabel, typeStyle] = TYPE_LABELS[job.employment_type] ?? TYPE_LABELS.unknown;
  const [expLabel, expStyle] = experienceBadge(job);
  const isNew = isNewSince(job, lastVisit);

  return (
    <article
      className={`group rounded-xl border bg-zinc-900/40 p-3.5 transition hover:border-zinc-700 hover:bg-zinc-900/70 sm:p-4 ${
        applied ? "border-zinc-800/60 opacity-60" : "border-zinc-800"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <a
            href={job.url}
            target="_blank"
            rel="noopener noreferrer"
            className="line-clamp-2 break-words font-medium text-zinc-100 hover:text-white hover:underline"
            title={job.title}
          >
            {job.title}
          </a>
          <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 text-sm text-zinc-400">
            <span className="truncate font-medium text-zinc-300">{job.company}</span>
            {job.location_raw && (
              <>
                <span className="text-zinc-700" aria-hidden="true">•</span>
                <span className="min-w-0 truncate">{job.location_raw}</span>
              </>
            )}
          </div>
        </div>
        {isNew && !applied && (
          <span className="shrink-0 rounded-md bg-emerald-500/15 px-2 py-0.5 text-[11px] font-semibold text-emerald-300 ring-1 ring-inset ring-emerald-500/30">
            New
          </span>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <Pill className={typeStyle}>{typeLabel}</Pill>
        <Pill className={expStyle}>{expLabel}</Pill>
        {job.pay && <Pill className="bg-emerald-500/10 text-emerald-200 ring-emerald-500/30">{job.pay}</Pill>}
        {job.is_remote && <Pill className="bg-zinc-800/80 text-zinc-300 ring-zinc-700">Remote</Pill>}
      </div>

      <div className="mt-3 flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-xs text-zinc-500">{ageLabel(job)}</span>
        <div className="flex shrink-0 items-center gap-1.5">
          <IconButton
            active={saved}
            onClick={onSave}
            label="☆ Save"
            activeLabel="★ Saved"
            className={saved
              ? "bg-amber-500/15 text-amber-200 ring-amber-500/30"
              : "bg-zinc-900 text-zinc-400 ring-zinc-800 hover:text-zinc-200"}
          />
          <IconButton
            active={applied}
            onClick={onApplied}
            label="Applied?"
            activeLabel="✓ Applied"
            className={applied
              ? "bg-emerald-500/15 text-emerald-200 ring-emerald-500/30"
              : "bg-zinc-900 text-zinc-400 ring-zinc-800 hover:text-zinc-200"}
          />
          <a
            href={job.url}
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 rounded-lg bg-zinc-100 px-3 py-1.5 text-xs font-semibold text-zinc-900 transition hover:bg-white"
          >
            Apply →
          </a>
        </div>
      </div>
    </article>
  );
}
