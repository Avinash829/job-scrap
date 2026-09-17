"""Write the job list as a static file the frontend can load directly.

Why: Render's free tier sleeps after 15 idle minutes, so the first visit of
the day waited up to a minute for the API to wake. The corpus is small (a few
thousand jobs, ~60 KB gzipped), so the scrape publishes it as a JSON file that
Vercel serves from its CDN. The page then renders instantly, filters and sorts
client-side, and works even while the API is asleep - and every page view that
doesn't reach the API is Neon compute we don't spend.

The API stays as-is for live queries and health.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.db.repository import JobRepository
from app.db.session import session_scope
from app.domain.entities import Job

# Keys are short: repeated 1,300 times, names are a real share of the payload.
FIELDS = (
    ("s", "source"),
    ("i", "source_job_id"),
    ("u", "url"),
    ("t", "title"),
    ("c", "company"),
    ("l", "location_raw"),
    ("r", "role_category"),
    ("e", "employment_type"),
    ("y", "min_yoe"),
    ("Y", "max_yoe"),
    ("g", "is_new_grad"),
    ("m", "is_remote"),
    ("R", "hiring_regions"),
    ("k", "tech_stack"),
    ("p", "posted_at"),
    ("f", "first_seen_at"),
    ("a", "age_days"),
    ("$", "pay"),
    ("v", "match_score"),
)


def _row(job: Job) -> dict:
    out: dict = {}
    for key, attr in FIELDS:
        if attr == "pay":
            value = (job.raw_payload or {}).get("pay")
        elif attr in ("role_category", "employment_type"):
            value = getattr(job, attr).value
        elif attr == "hiring_regions":
            value = [r.value for r in job.hiring_regions]
        else:
            value = getattr(job, attr)
        if isinstance(value, datetime):
            value = value.isoformat()
        if value in (None, "", [], False):
            continue        # absent means false/empty on the client
        out[key] = round(value, 1) if attr == "match_score" else value
    out["x"] = job.reachable_from_india
    return out


def export_snapshot(out_path: str) -> dict:
    """Write {generated_at, total, fields, jobs} to out_path. Returns a summary."""
    with session_scope() as s:
        jobs = sorted(JobRepository(s).active_jobs_lean(), key=lambda j: -j.match_score)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": len(jobs),
        # short key -> field name, so the client needs no hardcoded mapping
        "fields": {k: v for k, v in FIELDS},
        "jobs": [_row(j) for j in jobs],
    }
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return {
        "path": str(path),
        "jobs": len(jobs),
        "kb": round(len(text.encode("utf-8")) / 1024, 1),
        "internships": sum(1 for j in jobs if j.employment_type.value == "internship"),
    }
