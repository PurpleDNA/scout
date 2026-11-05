"""SQLite storage, plus the merge of fetched jobs, classification and embeddings."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .eligibility import classify
from .matching import EMBED_DIM, embed, extract_industries, extract_skills
from .sources import RawJob

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, source TEXT, title TEXT, company TEXT, url TEXT, location TEXT, workplace TEXT,
  restrictions TEXT, timezones TEXT, remote_board INTEGER, description TEXT, tags TEXT, salary TEXT,
  posted_at TEXT, first_seen TEXT, last_seen TEXT,
  lane TEXT, work_model TEXT, scope TEXT, confidence TEXT, evidence TEXT, flags TEXT,
  skills TEXT, industries TEXT, content_hash TEXT, embedded_hash TEXT, title_vec BLOB, text_vec BLOB
);
CREATE INDEX IF NOT EXISTS jobs_lane ON jobs(lane);
CREATE TABLE IF NOT EXISTS feedback (job_id TEXT PRIMARY KEY, action TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""
ACTIVE_DAYS = 3      # A listing missing from feeds for this long has closed.
MAX_AGE_DAYS = 60    # Older postings are hidden, even if still listed.


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    if "employment" not in {r["name"] for r in db.execute("PRAGMA table_info(jobs)")}:
        db.execute("ALTER TABLE jobs ADD COLUMN employment TEXT DEFAULT ''")
    return db


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dedupe_key(title: str, company: str) -> str:
    def norm(s: str) -> str:
        s = re.sub(r"\(.*?\)", " ", s.lower())
        s = re.sub(r"\b(remote|inc|llc|ltd|limited|gmbh|hybrid)\b", " ", s)
        return re.sub(r"[^a-z0-9]+", " ", s).strip()
    return f"{norm(company)}|{norm(title)}"


def save(db: sqlite3.Connection, jobs: list[RawJob]) -> None:
    now = now_iso()
    for j in jobs:
        db.execute(
            """INSERT INTO jobs (id, source, title, company, url, location, workplace, restrictions, timezones, remote_board,
                 description, tags, salary, posted_at, first_seen, last_seen, employment)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET title=excluded.title, company=excluded.company, url=excluded.url,
                 location=excluded.location, workplace=excluded.workplace, restrictions=excluded.restrictions,
                 timezones=excluded.timezones, description=excluded.description, tags=excluded.tags,
                 salary=excluded.salary, posted_at=COALESCE(excluded.posted_at, posted_at), last_seen=excluded.last_seen,
                 employment=excluded.employment""",
            (f"{j.source}:{j.external_id}", j.source, j.title, j.company, j.url, j.location, j.workplace,
             None if j.restrictions is None else json.dumps(j.restrictions),
             None if j.timezones is None else json.dumps(j.timezones), int(j.remote_board), j.description,
             json.dumps([t for t in j.tags if t]), j.salary, j.posted_at, now, now, j.employment))
    db.commit()


def reprocess(db: sqlite3.Connection, batch: int = 128) -> dict:
    """Classify every job and embed the eligible ones. Safe to rerun after changing the classifier."""
    rows = db.execute("SELECT id, title, location, workplace, restrictions, timezones, remote_board, description, tags, embedded_hash FROM jobs").fetchall()
    to_embed = []
    for r in rows:
        tags = json.loads(r["tags"] or "[]")
        e = classify(location=r["location"], workplace=r["workplace"],
                     restrictions=None if r["restrictions"] is None else json.loads(r["restrictions"]),
                     timezones=None if r["timezones"] is None else json.loads(r["timezones"]),
                     description=r["description"], title=r["title"], remote_board=bool(r["remote_board"]))
        body = " ".join(f"{r['title']}. {' '.join(tags)}. {r['description']}".split())
        digest = hashlib.sha1(f"{r['title']}|{body[:1500]}".encode()).hexdigest()
        db.execute("""UPDATE jobs SET lane=?, work_model=?, scope=?, confidence=?, evidence=?, flags=?, skills=?, industries=?, content_hash=? WHERE id=?""",
                   (e.lane, e.work_model, e.scope, e.confidence, e.evidence, json.dumps(e.flags),
                    json.dumps(extract_skills(body)), json.dumps(extract_industries(body)), digest, r["id"]))
        if e.lane != "rejected" and r["embedded_hash"] != digest:
            to_embed.append((r["id"], r["title"], body[:1500], digest, e.lane))
    db.commit()
    to_embed.sort(key=lambda c: c[4] != "lagos")  # The small lane first, so a long first run is useful sooner.
    for i in range(0, len(to_embed), batch):
        chunk = to_embed[i:i + batch]
        titles, texts = embed([c[1] for c in chunk]), embed([c[2] for c in chunk])
        db.executemany("UPDATE jobs SET title_vec=?, text_vec=?, embedded_hash=? WHERE id=?",
                       [(t.tobytes(), x.tobytes(), c[3], c[0]) for c, t, x in zip(chunk, titles, texts)])
        db.commit()
        print(f"  embedded {min(i + batch, len(to_embed))}/{len(to_embed)}", flush=True)
    db.execute("INSERT OR REPLACE INTO meta VALUES ('last_ingest', ?)", (now_iso(),))
    db.commit()
    return {"total": len(rows), "embedded": len(to_embed)}


def active_jobs(db: sqlite3.Connection) -> tuple[list[dict], np.ndarray, np.ndarray]:
    """Eligible, live jobs, one row per posting, plus their title and text vectors."""
    now = datetime.now(timezone.utc)
    seen_after = (now - timedelta(days=ACTIVE_DAYS)).isoformat()
    posted_after = (now - timedelta(days=MAX_AGE_DAYS)).isoformat()
    rows = db.execute("""SELECT * FROM jobs WHERE lane != 'rejected' AND title_vec IS NOT NULL AND last_seen >= ?
                         AND (posted_at IS NULL OR posted_at >= ?)""", (seen_after, posted_after)).fetchall()
    # The same role on several boards: keep the most trustworthy eligibility, then the direct company posting.
    rank = {"high": 0, "medium": 1, "low": 2}
    best: dict[str, sqlite3.Row] = {}
    for r in rows:
        key = dedupe_key(r["title"], r["company"])
        cur = best.get(key)
        order = (rank[r["confidence"]], r["source"] not in ("greenhouse", "lever", "ashby"))
        if cur is None or order < (rank[cur["confidence"]], cur["source"] not in ("greenhouse", "lever", "ashby")):
            best[key] = r
    keep = list(best.values())
    jobs = []
    for r in keep:
        d = {k: r[k] for k in r.keys() if k not in ("title_vec", "text_vec", "restrictions", "timezones")}
        for k in ("tags", "skills", "industries", "flags"):
            d[k] = json.loads(d[k] or "[]")
        jobs.append(d)
    empty = np.zeros((0, EMBED_DIM), dtype=np.float32)
    if not keep:
        return jobs, empty, empty
    titles = np.frombuffer(b"".join(r["title_vec"] for r in keep), dtype=np.float32).reshape(-1, EMBED_DIM)
    texts = np.frombuffer(b"".join(r["text_vec"] for r in keep), dtype=np.float32).reshape(-1, EMBED_DIM)
    return jobs, titles, texts


def feedback_map(db: sqlite3.Connection) -> dict[str, str]:
    return {r["job_id"]: r["action"] for r in db.execute("SELECT job_id, action FROM feedback")}


def set_feedback(db: sqlite3.Connection, job_id: str, action: str | None) -> None:
    if action is None:
        db.execute("DELETE FROM feedback WHERE job_id=?", (job_id,))
    else:
        db.execute("INSERT OR REPLACE INTO feedback VALUES (?,?,?)", (job_id, action, now_iso()))
    db.commit()


def feedback_vectors(db: sqlite3.Connection, action_set: tuple[str, ...]) -> tuple[np.ndarray, np.ndarray]:
    """Title and text vectors of the jobs you reacted to."""
    marks = ",".join("?" * len(action_set))
    rows = db.execute(f"SELECT j.title_vec t, j.text_vec v FROM feedback f JOIN jobs j ON j.id=f.job_id WHERE f.action IN ({marks}) AND j.text_vec IS NOT NULL", action_set).fetchall()
    if not rows:
        empty = np.zeros((0, EMBED_DIM), dtype=np.float32)
        return empty, empty
    as_matrix = lambda key: np.frombuffer(b"".join(r[key] for r in rows), dtype=np.float32).reshape(-1, EMBED_DIM)
    return as_matrix("t"), as_matrix("v")
