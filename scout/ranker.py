"""Ranked recommendations: the one query the CLI and the web server share."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from datetime import datetime, timedelta, timezone

from . import store
from .matching import Profile, score_jobs


UNCERTAINTY_PENALTY = {"high": 0.0, "medium": 5.0, "low": 10.0}


def diversify(results: list[dict], penalty: float = 5.0) -> list[dict]:
    """Best-first, but further jobs from one company sink, and so do jobs you may not be eligible for.

    This changes the order only. The score shown is still the job's real score.
    """
    seen: dict[str, int] = {}
    ranked = []
    value = lambda r: r.get("rank_value", r["score"])
    for r in sorted(results, key=lambda r: (-value(r), r["posted_at"] or "")):
        nth = seen.get(r["company"].lower(), 0)
        seen[r["company"].lower()] = nth + 1
        ranked.append((value(r) - penalty * nth - UNCERTAINTY_PENALTY[r.get("confidence", "high")], len(ranked), r))
    return [r for _, _, r in sorted(ranked, key=lambda t: (-t[0], t[1]))]


class Ranker:
    _NOT_LOADED = object()  # Distinct from None, which means “no ingest has finished yet”.

    def __init__(self, db: sqlite3.Connection, profile_path: Path):
        self.db = db
        self.profile = Profile.load(profile_path)
        self._loaded_at = self._NOT_LOADED
        self._load()

    def _load(self) -> None:
        row = self.db.execute("SELECT value FROM meta WHERE key='last_ingest'").fetchone()
        stamp = row["value"] if row else None
        if stamp != self._loaded_at:
            self.jobs, self.titles, self.texts = store.active_jobs(self.db)
            self._loaded_at = stamp

    def recommend(self, *, lane: str | None = None, work_model: str | None = None, query: str = "", min_score: int = 0,
                  verified_only: bool = True, status: str = "new", limit: int = 100, employment: str | None = None,
                  posted_within: float | None = None, sort: str = "best") -> dict:
        self._load()
        fb = store.feedback_map(self.db)
        liked = store.feedback_vectors(self.db, ("like", "applied"))
        disliked = store.feedback_vectors(self.db, ("dislike",))
        scores = score_jobs(self.jobs, self.titles, self.texts, self.profile, liked, disliked)
        excluded = {c.lower() for c in self.profile.data.get("exclude_companies", [])}
        terms = query.lower().split()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=posted_within)).isoformat() if posted_within else None
        states = [f["action"] for f in fb.values()]
        tracking = status in store.TRACKED  # Your own history: show every job you marked, closed or not.
        results, counts = [], {"global_remote": 0, "lagos": 0}
        for job, s in zip(self.jobs, scores):
            mark = fb.get(job["id"])
            state = mark["action"] if mark else None
            if not tracking and (job["closed"] or job["stale"]):
                continue
            if job["company"].lower() in excluded and not tracking:
                continue
            if status == "new" and state in ("dislike", "applied"):
                continue
            if status in ("like", "applied", "dislike") and state != status:
                continue
            if verified_only and job["confidence"] == "low" and not tracking:
                continue
            if s["score"] < min_score or (work_model and job["work_model"] != work_model):
                continue
            if employment and job.get("employment") != employment:
                continue
            if cutoff and (not job["posted_at"] or job["posted_at"] < cutoff):  # Undated jobs can't satisfy a date filter.
                continue
            if terms:
                hay = f"{job['title']} {job['company']} {job['description']}".lower()
                if not all(t in hay for t in terms):
                    continue
            counts[job["lane"]] += 1
            if lane and job["lane"] != lane:
                continue
            results.append({**job, **s, "state": state, "marked_at": mark["at"] if mark else None})
        results = sorted(results, key=lambda r: r["posted_at"] or "", reverse=True) if sort == "newest" else diversify(results)
        return {"total": len(results), "counts": counts, "jobs": results[:limit], "last_ingest": self._loaded_at,
                "reactions": {a: states.count(a) for a in ("like", "applied", "dislike")}}
