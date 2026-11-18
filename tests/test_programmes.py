import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from scout import programmes, store
from scout.programmes import Programme, deadline_of, parse, wanted


@pytest.mark.parametrize("text, expected", [
    ("Deadline: 18 October 2026, 1:00 AM", "2026-10-18"),
    ("Deadline  August 11, 2026  Application Method", "2026-08-11"),
    ("Deadline:  6th October, 2026", "2026-10-06"),
    ("Deadline:  Monday, September 14, 2026", "2026-09-14"),
    ("Deadline - 15th July, 2026", "2026-07-15"),
    ("The application deadline is 20 December 2026. Positions", "2026-12-20"),
    ("Apply on or before 3rd of March 2027", "2027-03-03"),
    ("Deadline:  Not Specified    Eligibility", None),
    ("Deadline: Not Specified (internship runs June 7 – August 27, 2027)", None),
    ("Meet deadlines. Started 1 May 2020 in a growing team.", None),  # A date far from the word isn't the deadline.
    ("No closing information at all", None),
])
def test_deadline_of(text, expected):
    assert deadline_of(text) == expected


def item(**kw):
    base = {"id": "a1", "slug": "x", "title": "Acme Graduate Trainee Programme 2027", "company": "Acme", "location": "Lagos, Nigeria",
            "description": "<p>Deadline: 30 November 2026</p>", "created_at": "2026-11-10 09:00:00", "salary_min": None,
            "salary_max": None, "salary_currency": None, "salary_period": None, "contract_type": "Full-Time", "category": "Graduate Trainee",
            "redirect_url": "https://forms.gle/abc", "is_active": True}
    return {**base, **kw}


def test_wanted_keeps_trainee_and_internship_schemes():
    assert wanted(item())
    assert wanted(item(title="Egbin PowerUp Programme 2027", category="Internship"))
    assert wanted(item(title="Norrenberger Software Engineering Placement – NYSC Corps Members", category="Technology"))
    assert wanted(item(title="UCL African Graduate Scholarship 2027/28", category="Scholarship"))
    assert wanted(item(title="Fully Funded Masters at Erasmus 2027", category="Education", post_type="scholarship"))
    assert parse(item(title="UCL African Graduate Scholarship 2027/28", category="Scholarship")).kind == "scholarship"
    assert not wanted(item(title="Graduate Research Grant", post_type="grant", category="Grants"))
    assert not wanted(item(title="Digital Marketing Manager", category="Marketing", contract_type="Full-Time (Hybrid)"))


def test_wanted_drops_roundups_unless_they_name_a_tech_role():
    roundup = dict(title="Acme Graduate Recruitment for Multiple Positions", description="<p>Accountant, Sales Officer</p>")
    assert not wanted(item(**roundup))
    assert wanted(item(**{**roundup, "description": "<p>Accountant, Frontend Developer</p>"}))


def test_parse():
    p = parse(item(title="Acme Internship &amp; NYSC Placement", salary_min="100000", salary_max="150000", salary_currency="NGN",
                   salary_period="monthly"))
    assert (p.title, p.kind, p.deadline, p.apply_url) == ("Acme Internship & NYSC Placement", "internship", "2026-11-30", "https://forms.gle/abc")
    assert p.url == "https://jobs.revicemycv.com/job/x" and p.salary == "NGN 100,000-150,000 monthly"
    ad = parse(item(description="<p>Join us.</p><p>Get your CV prepared: https://revicemycv.com/services</p><p>Deadline: 1 Dec 2026</p>"))
    assert ad.description == "Join us.\nDeadline: 1 Dec 2026" and ad.deadline == "2026-12-01"
    assert parse(item()).kind == "graduate" and parse(item()).posted_at.startswith("2026-11-10")


def prog(pid, deadline=None, posted="2026-11-01T00:00:00+00:00"):
    return Programme(pid, f"Programme {pid}", "Acme", "Lagos", "graduate", "Graduate Trainee", "u", "", "", "", deadline, posted)


@pytest.fixture
def db():
    return programmes.connect(sqlite3.connect(":memory:", check_same_thread=False))


def ids(rows):
    return [r["id"] for r in rows]


def test_listing_hides_past_deadlines_and_orders_by_closing_date(db):
    db.row_factory = sqlite3.Row
    programmes.save(db, [prog("late", "2026-12-20"), prog("soon", "2026-11-20"), prog("gone", "2026-11-01"),
                         prog("undated-old", posted="2026-10-01T00:00:00+00:00"), prog("undated-new", posted="2026-11-15T00:00:00+00:00")],
                    "2026-11-16T00:00:00+00:00")
    assert ids(programmes.listing(db, today="2026-11-17")) == ["soon", "late", "undated-new", "undated-old"]
    assert programmes.set_status(db, "soon", "applied") and not programmes.set_status(db, "nope", "applied")
    assert ids(programmes.listing(db, today="2026-11-17")) == ["late", "undated-new", "undated-old"]
    assert ids(programmes.listing(db, "applied")) == ["soon"]
    assert programmes.counts(db, today="2026-11-17") == {"open": 3, "applied": 1, "hidden": 0}


def test_cleanup_keeps_applied(db):
    db.row_factory = sqlite3.Row
    programmes.save(db, [prog("old"), prog("old-applied")], "2026-09-01T00:00:00+00:00")
    programmes.save(db, [prog("recent")], "2026-11-01T00:00:00+00:00")
    programmes.set_status(db, "old-applied", "applied")
    assert programmes.cleanup(db, datetime(2026, 11, 17, tzinfo=timezone.utc)) == 1
    assert {r["id"] for r in db.execute("SELECT id FROM programmes")} == {"old-applied", "recent"}


def test_purge_old_jobs(tmp_path):
    db = store.connect(tmp_path / "t.db")
    now = datetime(2026, 11, 17, tzinfo=timezone.utc)
    old, recent = (now - timedelta(days=70)).isoformat(), (now - timedelta(days=1)).isoformat()
    def add(jid, first_seen, last_seen, posted_at=None):
        db.execute("INSERT INTO jobs (id, source, title, company, first_seen, last_seen, posted_at) VALUES (?,?,?,?,?,?,?)",
                   (jid, "b", jid, "c", first_seen, last_seen, posted_at))
    add("closed", old, old)                        # Its board stopped listing it: goes.
    add("stale", old, recent, posted_at=old)        # Still listed but posted over 60 days ago: goes.
    add("listed-undated", old, recent)              # Still listed with no date: stays, or it would come back as new.
    add("young", recent, recent)                    # Not held for 60 days yet: stays.
    for jid, action in (("applied", "applied"), ("liked", "like"), ("disliked", "dislike")):
        add(jid, old, old)                          # Anything you reacted to stays.
        db.execute("INSERT INTO feedback (job_id, action, at) VALUES (?,?,?)", (jid, action, old))
    store.mark_fetched(db, "b", recent)
    assert store.purge_old(db, now) == 2
    assert {r["id"] for r in db.execute("SELECT id FROM jobs")} == {"listed-undated", "young", "applied", "liked", "disliked"}


def test_fetch_stops_at_the_page_reaching_what_we_have(monkeypatch):
    pages = {1: [item(id="n1", created_at="2026-11-16 10:00:00"), item(id="x", title="Accountant", category="Finance", created_at="2026-11-15 10:00:00")],
             2: [item(id="n2", created_at="2026-11-12 10:00:00"), item(id="old", created_at="2026-11-10 09:00:00")],
             3: [item(id="never", created_at="2026-11-01 10:00:00")]}
    asked = []
    def fake_get(url):
        n = int(url.split("page=")[1].split("&")[0])
        asked.append(n)
        return json.dumps({"data": pages.get(n, [])})
    monkeypatch.setattr(programmes, "get", fake_get)
    got = list(programmes.fetch(since="2026-11-10T09:00:00+00:00", pause=0))
    assert asked == [1, 2] and [p.id for p in got] == ["n1", "n2", "old"]
