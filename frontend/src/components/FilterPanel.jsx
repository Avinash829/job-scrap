/** Filter sidebar. Fully controlled - all state lives in App. */

const ROLES = [
  ["swe", "SWE / SDE"],
  ["frontend", "Frontend"],
  ["backend", "Backend"],
  ["fullstack", "Full stack"],
  ["ml", "AI / ML"],
  ["data", "Data"],
];

const EMPLOYMENT = [
  ["internship", "Internship"],
  ["full_time", "Full-time"],
  ["unknown", "Unstated"],
];

const YOE = [
  [0, "Only 0 yrs"],
  [1, "Up to 1 yr"],
  [2, "Up to 2 yrs"],
  [null, "Any"],
];

const FRESHNESS = [
  [1, "Today"],
  [2, "2 days"],
  [7, "Week"],
  [null, "All"],
];

/** Your declared stack, from backend/config.yaml. Used for the skills filter. */
const MY_STACK = [
  "python", "javascript", "react", "next.js", "node.js", "express",
  "fastapi", "mongodb", "mysql", "sql", "firebase", "tailwind",
  "llm", "langchain", "langgraph", "ai agents", "rest", "socket.io",
  "docker", "git", "power bi", "typescript",
];

function Section({ title, children, hint }) {
  return (
    <div className="border-b border-zinc-800 px-4 py-3.5">
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
        {title}
      </h3>
      {children}
      {hint && <p className="mt-1.5 text-[11px] leading-snug text-zinc-600">{hint}</p>}
    </div>
  );
}

function Toggle({ checked, onChange, label, hint }) {
  return (
    <label className="flex cursor-pointer items-start gap-2.5 py-1.5 md:py-1">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer rounded border-zinc-700 bg-zinc-800 accent-emerald-500 md:h-3.5 md:w-3.5"
      />
      <span className="text-sm leading-tight text-zinc-300">
        {label}
        {hint && <span className="mt-0.5 block text-[11px] text-zinc-600">{hint}</span>}
      </span>
    </label>
  );
}

function ChipGroup({ options, selected, onToggle, mono = false }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map(([value, label]) => {
        const active = selected.includes(value);
        return (
          <button
            key={value}
            type="button"
            aria-pressed={active}
            onClick={() => onToggle(value)}
            className={`rounded-md px-2.5 py-1.5 text-xs font-medium ring-1 ring-inset transition md:px-2 md:py-1 ${
              mono ? "font-mono text-[11px]" : ""
            } ${
              active
                ? "bg-emerald-500/15 text-emerald-300 ring-emerald-500/40"
                : "bg-zinc-800/50 text-zinc-400 ring-zinc-800 hover:bg-zinc-800 hover:text-zinc-300"
            }`}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

function RadioRow({ options, value, onChange }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map(([val, label]) => (
        <button
          key={label}
          type="button"
          aria-pressed={value === val}
          onClick={() => onChange(val)}
          className={`rounded-md px-2.5 py-1.5 text-xs font-medium ring-1 ring-inset transition md:px-2 md:py-1 ${
            value === val
              ? "bg-zinc-100 text-zinc-900 ring-zinc-100"
              : "bg-zinc-800/50 text-zinc-400 ring-zinc-800 hover:bg-zinc-800 hover:text-zinc-300"
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

export default function FilterPanel({
  filters,
  setFilters,
  sources = [],
  onReset,
  onClose = null,
  activeCount = 0,
}) {
  const patch = (changes) => setFilters({ ...filters, ...changes, offset: 0 });

  const toggleIn = (key, value) => {
    const current = filters[key] ?? [];
    patch({
      [key]: current.includes(value)
        ? current.filter((v) => v !== value)
        : [...current, value],
    });
  };

  return (
    <aside
      className={`flex flex-col bg-zinc-900/20 ${
        onClose ? "" : "h-full overflow-y-auto border-r border-zinc-800"
      }`}
    >
      <div
        className={`flex items-center justify-between border-b border-zinc-800 px-4 py-3 ${
          onClose ? "sticky top-0 z-10 bg-zinc-950" : ""
        }`}
      >
        <span className="text-sm font-semibold text-zinc-200">
          Filters
          {activeCount > 0 && (
            <span className="ml-1.5 rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300">
              {activeCount}
            </span>
          )}
        </span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={onReset}
            className="rounded-md px-2 py-1.5 text-xs text-zinc-400 hover:text-zinc-200 md:px-0 md:py-0 md:text-[11px] md:text-zinc-500"
          >
            Reset
          </button>
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Close filters"
              className="rounded-md p-2 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
            >
              <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" className="h-4 w-4">
                <path d="M4.3 4.3a1 1 0 0 1 1.4 0L10 8.6l4.3-4.3a1 1 0 1 1 1.4 1.4L11.4 10l4.3 4.3a1 1 0 0 1-1.4 1.4L10 11.4l-4.3 4.3a1 1 0 0 1-1.4-1.4L8.6 10 4.3 5.7a1 1 0 0 1 0-1.4Z" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* Phones already have search in the toolbar above the results. */}
      {!onClose && (
        <Section title="Search" hint="Matches role title or company name.">
          <input
            type="search"
            value={filters.q ?? ""}
            onChange={(e) => patch({ q: e.target.value })}
            placeholder="e.g. frontend intern, react, Swiggy…"
            className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-2.5 py-1.5 text-sm text-zinc-200 placeholder-zinc-600 outline-none focus:border-zinc-600"
          />
        </Section>
      )}

      <Section title="Type" hint="You graduate June 2027, so internships rank highest.">
        <ChipGroup
          options={EMPLOYMENT}
          selected={filters.employment_type ?? []}
          onToggle={(v) => toggleIn("employment_type", v)}
        />
        <div className="mt-2">
          <Toggle
            checked={!!filters.new_grad_only}
            onChange={(v) => patch({ new_grad_only: v })}
            label="New grad / intern signal only"
          />
        </div>
      </Section>

      <Section
        title="Reachability"
        hint="Most US 'Remote' roles require existing US work authorization."
      >
        <Toggle
          checked={!!filters.reachable_only}
          onChange={(v) => patch({ reachable_only: v })}
          label="Hireable from India"
          hint="Worldwide, India, or sponsors visas"
        />
        <Toggle
          checked={!!filters.remote_only}
          onChange={(v) => patch({ remote_only: v })}
          label="Remote only"
        />
      </Section>

      <Section title="Experience">
        <RadioRow
          options={YOE}
          value={filters.max_min_yoe ?? null}
          onChange={(v) => patch({ max_min_yoe: v })}
        />
        <div className="mt-2">
          <Toggle
            checked={filters.include_unknown_yoe !== false}
            onChange={(v) => patch({ include_unknown_yoe: v })}
            label="Include unstated"
            hint="Most genuine new-grad reqs don't state years"
          />
        </div>
      </Section>

      <Section title="Role">
        <ChipGroup
          options={ROLES}
          selected={filters.role ?? []}
          onToggle={(v) => toggleIn("role", v)}
        />
      </Section>

      <Section
        title="My skills"
        hint="Requires the posting to mention every skill you pick."
      >
        <ChipGroup
          mono
          options={MY_STACK.map((t) => [t, t])}
          selected={filters.tech ?? []}
          onToggle={(v) => toggleIn("tech", v)}
        />
      </Section>

      <Section
        title="Posted within"
        hint="Employer ATS boards only list open roles, so an older date there still means live. Stale aggregator listings are already filtered server-side."
      >
        <RadioRow
          options={FRESHNESS}
          value={filters.posted_within_days ?? null}
          onChange={(v) => patch({ posted_within_days: v })}
        />
      </Section>

      {sources.length > 0 && (
        <Section title="Source">
          <ChipGroup
            options={sources.map((s) => [s, s])}
            selected={filters.source ?? []}
            onToggle={(v) => toggleIn("source", v)}
          />
        </Section>
      )}
    </aside>
  );
}
