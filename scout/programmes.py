"""Graduate trainee and internship programmes, from jobs.revicemycv.com.

A list to browse, not to rank: nothing here is classified, embedded or scored, and nothing here counts as
feedback for the job ranking. The board's strength is Nigerian trainee schemes; its tech coverage is thin.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .sources import get, iso, text_of

API = "https://jobs.revicemycv.com/api/get-jobs.php?page={page}&per_page=50"  # 50 is the most it returns per page.
PAGE = "https://jobs.revicemycv.com/job/{slug}"
MAX_PAGES = 20
PAUSE = 3          # Seconds between pages.
KEEP_DAYS = 60     # Fetched back this far; deleted this long after we first saw it, unless you applied.
STATUSES = {"applied", "hidden"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS programmes (
  id TEXT PRIMARY KEY, title TEXT, company TEXT, location TEXT, kind TEXT, category TEXT, url TEXT, apply_url TEXT,
  description TEXT, salary TEXT, deadline TEXT, posted_at TEXT, first_seen TEXT, last_seen TEXT,
  status TEXT, status_at TEXT
);
"""

_PROGRAMME = re.compile(r"\b(graduate|trainee|intern(ship)?s?|nysc|corps members?|young professionals?|entry[- ]level)\b", re.I)
_SCHOLARSHIP = re.compile(r"\bscholarships?\b", re.I)
_NOT_A_JOB = re.compile(r"grant|fellowship", re.I)  # Other funding stays out; only scholarships were asked for.
_INTERN = re.compile(r"\b(intern(ship)?s?|nysc|corps members?|siwes|industrial training)\b", re.I)
_ROUNDUP = re.compile(r"\b(multiple|several|various)\s+(positions|roles|openings|vacancies|job)|\bcheck vacanc", re.I)
_TECH = re.compile(r"\b(software|developers?|front[- ]?end|back[- ]?end|full[- ]?stack|web develop\w*|devops|data (analyst|engineer|scien\w+)|"
                   r"cyber ?security|ui/ux|product design\w*|information technology|ICT)\b", re.I)
ROUNDUP_ROLES = _TECH  # Round-up posts are kept when they name one of these. scout.fields sets it from your profile.
_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
_DAY_FIRST = re.compile(rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MON},?\s+(\d{{4}})", re.I)
_MONTH_FIRST = re.compile(rf"{_MON}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})", re.I)
_UNSTATED = re.compile(r"[\s:\-–]*(not (specified|stated|given|available)|n/?a\b|none\b|ongoing|rolling|open until filled)", re.I)
_DEADLINE = re.compile(r"\b(deadline|closing date|application(s)? close|closes on|on or before)\b", re.I)


@dataclass
class Programme:
    id: str
    title: str
    company: str
    location: str
    kind: str          # "graduate", "internship" or "scholarship"
    category: str
    url: str           # The listing on the board, which carries the full write-up.
    apply_url: str     # Where the board says to apply, when it gives a link.
    description: str
    salary: str
    deadline: str | None   # YYYY-MM-DD
    posted_at: str | None


def _date(day: str, month: str, year: str) -> str | None:
    month = month.lower()[:3]
    number = next((i for i, m in enumerate(_MONTHS.split("|"), 1) if m.startswith(month)), None)
    try:
        return datetime(int(year), number, int(day)).date().isoformat() if number else None
    except ValueError:
        return None


def deadline_of(text: str) -> str | None:
    """The first date written shortly after a word like “deadline”. None when it says “not specified”, or nothing."""
    for m in _DEADLINE.finditer(text):
        window = text[m.end():m.end() + 70]
        if _UNSTATED.match(window):  # “Deadline: Not Specified (runs June 7 – August 27)” names other dates.
            return None
        a, b = _DAY_FIRST.search(window), _MONTH_FIRST.search(window)
        if a and (not b or a.start() <= b.start()):
            return _date(a[1], a[2], a[3])
        if b:
            return _date(b[2], b[1], b[3])
    return None


def _is_scholarship(item: dict) -> bool:
    return (item.get("post_type") == "scholarship"
            or bool(_SCHOLARSHIP.search(f"{item.get('category') or ''} {item.get('title') or ''}")))


def wanted(item: dict) -> bool:
    """Trainee and internship schemes, and scholarships. Round-up posts (“multiple positions”) only when they name a role in your field."""
    if _is_scholarship(item):
        return True
    if (item.get("post_type") or "job") != "job" or _NOT_A_JOB.search(item.get("category") or ""):
        return False
    label = f"{item.get('title') or ''} {item.get('category') or ''} {item.get('contract_type') or ''}"
    if not _PROGRAMME.search(label):
        return False
    if _ROUNDUP.search(item.get("title") or ""):
        return bool(ROUNDUP_ROLES.search(f"{item.get('title') or ''} {text_of(item.get('description'))}"))
    return True


def parse(item: dict) -> Programme:
    body = "\n".join(line for line in text_of(item.get("description"), keep_lines=True).split("\n")
                     if "revicemycv.com" not in line)  # The board's adverts for its own CV service.
    label = f"{item.get('title') or ''} {item.get('category') or ''} {item.get('contract_type') or ''}"
    lo, hi, cur = item.get("salary_min"), item.get("salary_max"), item.get("salary_currency") or ""
    salary = f"{cur} {'-'.join(f'{int(float(v)):,}' for v in (lo, hi) if v)}".strip() if (lo or hi) else ""
    if salary and item.get("salary_period"):
        salary += f" {item['salary_period']}"
    return Programme(
        id=str(item["id"]), title=text_of(item.get("title")), company=text_of(item.get("company")), location=text_of(item.get("location")),
        kind="scholarship" if _is_scholarship(item) else "internship" if _INTERN.search(label) else "graduate", category=(item.get("category") or "").strip(),
        url=PAGE.format(slug=item.get("slug") or ""), apply_url=(item.get("redirect_url") or "").strip(), description=body,
        salary=salary, deadline=deadline_of(" ".join(body.split())), posted_at=iso(item.get("created_at")))


def fetch(since: str | None = None, max_pages: int = MAX_PAGES, pause: float = PAUSE) -> Iterable[Programme]:
    """Newest first. Stops at the page that reaches back to `since` (the newest posting we already have),
    or to KEEP_DAYS ago, whichever comes first."""
    oldest = max(since or "", (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat())
    for page in range(1, max_pages + 1):
        if page > 1:
            time.sleep(pause)
        items = json.loads(get(API.format(page=page))).get("data") or []
        if not items:
            return
        for item in items:
            if item.get("is_active") is False or not wanted(item):
                continue
            yield parse(item)
        dates = [iso(i.get("created_at")) for i in items]
        if any(d and d <= oldest for d in dates):
            return


def connect(db: sqlite3.Connection) -> sqlite3.Connection:
    db.executescript(SCHEMA)
    return db


def save(db: sqlite3.Connection, programmes: list[Programme], now: str) -> int:
    before = db.execute("SELECT COUNT(*) FROM programmes").fetchone()[0]
    for p in programmes:
        db.execute("""INSERT INTO programmes (id, title, company, location, kind, category, url, apply_url, description, salary,
                        deadline, posted_at, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(id) DO UPDATE SET title=excluded.title, company=excluded.company, location=excluded.location,
                        kind=excluded.kind, category=excluded.category, url=excluded.url, apply_url=excluded.apply_url,
                        description=excluded.description, salary=excluded.salary, deadline=excluded.deadline,
                        posted_at=COALESCE(excluded.posted_at, posted_at), last_seen=excluded.last_seen""",
                   (p.id, p.title, p.company, p.location, p.kind, p.category, p.url, p.apply_url, p.description, p.salary,
                    p.deadline, p.posted_at, now, now))
    db.commit()
    return db.execute("SELECT COUNT(*) FROM programmes").fetchone()[0] - before


def refresh(db: sqlite3.Connection) -> tuple[int, int]:
    """Fetch and store. Returns (fetched, new)."""
    connect(db)
    since = db.execute("SELECT MAX(posted_at) FROM programmes").fetchone()[0]
    got = list(fetch(since))
    return len(got), save(db, got, datetime.now(timezone.utc).isoformat())


def cleanup(db: sqlite3.Connection, today: datetime | None = None) -> int:
    """Delete what we have held for KEEP_DAYS. Programmes you applied to stay."""
    connect(db)
    cutoff = ((today or datetime.now(timezone.utc)) - timedelta(days=KEEP_DAYS)).isoformat()
    n = db.execute("DELETE FROM programmes WHERE first_seen < ? AND COALESCE(status, '') != 'applied'", (cutoff,)).rowcount
    db.commit()
    return n


def listing(db: sqlite3.Connection, view: str = "open", today: str | None = None) -> list[dict]:
    """open: not marked and not past its deadline, soonest deadline first. applied / hidden: what you marked."""
    connect(db)
    today = today or datetime.now(timezone.utc).date().isoformat()
    if view == "open":
        rows = db.execute("SELECT * FROM programmes WHERE status IS NULL AND (deadline IS NULL OR deadline >= ?)", (today,)).fetchall()
    else:
        rows = db.execute("SELECT * FROM programmes WHERE status = ?", (view,)).fetchall()
    dated = sorted((dict(r) for r in rows if r["deadline"]), key=lambda p: p["deadline"])
    undated = sorted((dict(r) for r in rows if not r["deadline"]), key=lambda p: p["posted_at"] or "", reverse=True)
    return dated + undated  # Closing soonest first, then the undated ones, newest first.


def counts(db: sqlite3.Connection, today: str | None = None) -> dict:
    connect(db)
    today = today or datetime.now(timezone.utc).date().isoformat()
    open_ = db.execute("SELECT COUNT(*) FROM programmes WHERE status IS NULL AND (deadline IS NULL OR deadline >= ?)", (today,)).fetchone()[0]
    marked = dict(db.execute("SELECT status, COUNT(*) FROM programmes WHERE status IS NOT NULL GROUP BY status").fetchall())
    return {"open": open_, "applied": marked.get("applied", 0), "hidden": marked.get("hidden", 0)}


def set_status(db: sqlite3.Connection, programme_id: str, status: str | None) -> bool:
    if status is not None and status not in STATUSES:
        raise ValueError(status)
    cur = db.execute("UPDATE programmes SET status=?, status_at=? WHERE id=?",
                     (status, None if status is None else datetime.now(timezone.utc).isoformat(), programme_id))
    db.commit()
    return cur.rowcount == 1
