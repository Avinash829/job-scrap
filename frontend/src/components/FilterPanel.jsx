/** Filter sidebar. Controlled - all state lives in App. */

const ROLES = [
  ["swe", "SWE / SDE"],
  ["frontend", "Frontend"],
  ["backend", "Backend"],
  ["fullstack", "Full stack"],
  ["ml", "AI / ML"],
  ["data", "Data"],
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
    <label className="flex cursor-pointer items-start gap-2.5 py-1">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-3.5 w-3.5 shrink-0 cursor-pointer rounded border-zinc-700 bg-zinc-800 accent-emerald-500"
      />
      <span className="text-sm leading-tight text-zinc-300">
        {label}
        {hint && <span className="mt-0.5 block text-[11px] text-zinc-600">{hint}</span>}
      </span>
    </label>
  );
}

function ChipGroup({ options, selected, onToggle }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map(([value, label]) => {
        const active = selected.includes(value);
        return (
          <button
            key={value}
            type="button"
            onClick={() => onToggle(value)}
            className={`rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset transition ${
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
          onClick={() => onChange(val)}
          className={`rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset transition ${
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

export default function FilterPanel({ filters, setFilters, sources = [], onReset }) {
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
    <aside className="flex h-full flex-col overflow-y-auto border-r border-zinc-800 bg-zinc-900/20">
      <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
        <span className="text-sm font-semibold text-zinc-200">Filters</span>
        <button
          type="button"
          onClick={onReset}
          className="text-[11px] text-zinc-500 hover:text-zinc-300"
        >
          Reset
        </button>
      </div>

      <Section title="Search">
        <input
          type="search"
          value={filters.q ?? ""}
          onChange={(e) => patch({ q: e.target.value })}
          placeholder="Title or company…"
          className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-2.5 py-1.5 text-sm text-zinc-200 placeholder-zinc-600 outline-none focus:border-zinc-600"
        />
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
          <Toggle
            checked={!!filters.new_grad_only}
            onChange={(v) => patch({ new_grad_only: v })}
            label="New grad / intern only"
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
        title="Posted within"
        hint="Employer ATS boards only list open roles, so an older date there still means live. Stale aggregator listings are already filtered out server-side."
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
