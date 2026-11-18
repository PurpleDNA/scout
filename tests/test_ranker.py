import json
import numpy as np
from scout import matching, store
from scout.ranker import Ranker
from scout.sources import RawJob


def fake_embed(texts):
    out = np.zeros((len(texts), matching.EMBED_DIM), dtype=np.float32)
    for i, t in enumerate(texts):
        tech = any(w in t.lower() for w in ("engineer", "react", "developer"))
        out[i, 0 if tech else 1] = 1
    return out


def setup(tmp_path, monkeypatch, jobs):
    monkeypatch.setattr(matching, "embed", fake_embed)
    monkeypatch.setattr(store, "embed", fake_embed)
    db = store.connect(tmp_path / "t.db")
    (tmp_path / "p.json").write_text(json.dumps({"primary_titles": ["Engineer"], "secondary_titles": [], "summary": "engineer", "skills": ["react"],
                                                 "industries": [], "years_experience": 3, "level_range": [0, 3]}))
    store.save(db, jobs)
    return db, tmp_path / "p.json"


def job(i, title, **kw):
    return RawJob("t", str(i), title, kw.pop("company", f"Co{i}"), f"https://x/{i}", **kw)


def test_empty_database_before_first_ingest(tmp_path, monkeypatch):
    db, p = setup(tmp_path, monkeypatch, [])
    assert Ranker(db, p).recommend()["total"] == 0


def test_lanes_rejections_duplicates_and_feedback(tmp_path, monkeypatch):
    db, p = setup(tmp_path, monkeypatch, [
        job(1, "React Engineer", location="Worldwide", remote_board=True, company="A"),
        job(2, "React Engineer (Remote)", location="Anywhere", remote_board=True, company="A Inc"),
        job(3, "Developer", location="Lagos"),
        job(4, "Engineer", location="USA Only", remote_board=True),
        job(5, "Engineer", location="Remote", remote_board=True),
        job(6, "Engineer", location="London"),
    ])
    store.reprocess(db)
    r = Ranker(db, p)

    data = r.recommend()
    assert data["counts"] == {"global_remote": 1, "nigeria": 1}  # 1 and 2 merged; 4 and 6 rejected; 5 unverified
    assert r.recommend(verified_only=False)["counts"]["global_remote"] == 2
    assert [j["work_model"] for j in r.recommend(lane="nigeria")["jobs"]] == ["onsite"]
    assert r.recommend()["reactions"] == {"like": 0, "applied": 0, "dislike": 0}

    target = r.recommend(lane="nigeria")["jobs"][0]["id"]
    store.set_feedback(db, target, "dislike")
    assert r.recommend(lane="nigeria")["total"] == 0
    assert r.recommend(lane="nigeria", status="dislike")["total"] == 1
    store.set_feedback(db, target, None)
    assert r.recommend(lane="nigeria")["total"] == 1


def test_diversify_stops_one_company_flooding_but_keeps_true_scores():
    from scout.ranker import diversify
    rows = [{"company": "Hivex", "score": s, "posted_at": None} for s in (90, 89, 88, 87)] + [{"company": "Other", "score": 80, "posted_at": None}]
    out = diversify(rows)
    assert [r["company"] for r in out][:2] == ["Hivex", "Hivex"]
    assert out.index(rows[4]) == 2
    assert sorted(r["score"] for r in out) == [80, 87, 88, 89, 90]


def test_less_certain_eligibility_ranks_lower_with_same_score():
    from scout.ranker import diversify
    rows = [{"company": "A", "score": 80, "posted_at": None, "confidence": "medium"},
            {"company": "B", "score": 78, "posted_at": None, "confidence": "high"}]
    assert diversify(rows)[0]["company"] == "B"


def test_date_employment_and_newest_filters(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    days_ago = lambda n: (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()
    db, p = setup(tmp_path, monkeypatch, [
        job(1, "Engineer", location="Lagos", posted_at=days_ago(1), employment="full_time"),
        job(2, "Developer", location="Lagos", posted_at=days_ago(10), employment="contract"),
        job(3, "React Engineer", location="Lagos"),
    ])
    store.reprocess(db)
    r = Ranker(db, p)
    titles = lambda **kw: [j["title"] for j in r.recommend(**kw)["jobs"]]
    assert titles(posted_within=3) == ["Engineer"]
    assert sorted(titles(posted_within=30)) == ["Developer", "Engineer"]  # The undated job can't pass a date filter.
    assert titles(employment="contract") == ["Developer"]
    assert titles(sort="newest")[:2] == ["Engineer", "Developer"]


def _age(db, job_id, days):
    """Pretend this job was last returned by its source `days` ago."""
    from datetime import datetime, timedelta, timezone
    db.execute("UPDATE jobs SET last_seen=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(days=days)).isoformat(), job_id))
    db.commit()


def test_skipping_ingest_closes_nothing(tmp_path, monkeypatch):
    db, p = setup(tmp_path, monkeypatch, [job(1, "Engineer", location="Lagos"), job(2, "Developer", location="Lagos")])
    store.reprocess(db)
    _age(db, "t:1", 10)
    _age(db, "t:2", 10)  # Nobody ran ingest for 10 days: the source's clock is 10 days old too.
    assert Ranker(db, p).recommend()["total"] == 2


def test_a_listing_missing_from_successful_fetches_closes(tmp_path, monkeypatch):
    db, p = setup(tmp_path, monkeypatch, [job(1, "Engineer", location="Lagos"), job(2, "Developer", location="Lagos")])
    store.reprocess(db)
    _age(db, "t:2", 4)
    store.mark_fetched(db, "t", store.now_iso())
    assert [j["title"] for j in Ranker(db, p).recommend()["jobs"]] == ["Engineer"]


def test_a_failed_fetch_closes_nothing(tmp_path, monkeypatch):
    db, p = setup(tmp_path, monkeypatch, [job(1, "Engineer", location="Lagos"), job(2, "Developer", location="Lagos")])
    store.reprocess(db)
    from datetime import datetime, timedelta, timezone
    store.mark_fetched(db, "t", (datetime.now(timezone.utc) - timedelta(days=9)).isoformat())  # Last success, 9 days ago.
    _age(db, "t:1", 8)
    _age(db, "t:2", 8)
    assert Ranker(db, p).recommend()["total"] == 2


def test_marked_jobs_survive_closing_and_carry_a_stage(tmp_path, monkeypatch):
    db, p = setup(tmp_path, monkeypatch, [job(1, "Engineer", location="Lagos"), job(2, "Developer", location="Lagos"),
                                          job(3, "React Engineer", location="Lagos")])
    store.reprocess(db)
    store.set_feedback(db, "t:1", "applied")
    store.set_feedback(db, "t:2", "like")
    for i in (1, 2, 3):
        _age(db, f"t:{i}", 5)
    store.mark_fetched(db, "t", store.now_iso())
    r = Ranker(db, p)
    assert r.recommend()["total"] == 0
    applied = r.recommend(status="applied")["jobs"]
    assert [(j["title"], j["closed"], j["stage"]) for j in applied] == [("Engineer", True, None)]
    assert [j["title"] for j in r.recommend(status="like")["jobs"]] == ["Developer"]

    score = applied[0]["score"]
    assert store.set_stage(db, "t:1", "rejected")
    after = r.recommend(status="applied")
    assert after["jobs"][0]["stage"] == "rejected" and after["stages"]["rejected"] == 1
    assert after["jobs"][0]["score"] == score  # The stage is for your records; ranking ignores it.
    store.set_feedback(db, "t:1", "applied")
    assert r.recommend(status="applied")["jobs"][0]["stage"] == "rejected"  # Repeating the reaction keeps the stage.
    assert not store.set_stage(db, "t:2", "hired")  # Only applications have a stage.
    store.set_feedback(db, "t:1", "like")  # Switching reaction starts over.
    assert db.execute("SELECT stage FROM feedback WHERE job_id='t:1'").fetchone()["stage"] is None


def test_a_marked_duplicate_wins_over_an_open_one(tmp_path, monkeypatch):
    db, p = setup(tmp_path, monkeypatch, [job(1, "React Engineer", location="Lagos", company="A"),
                                          job(2, "React Engineer", location="Lagos", company="A")])
    store.reprocess(db)
    shown = Ranker(db, p).recommend()["jobs"][0]["id"]
    other = "t:2" if shown == "t:1" else "t:1"
    store.set_feedback(db, other, "applied")
    _age(db, other, 5)
    store.mark_fetched(db, "t", store.now_iso())
    r = Ranker(db, p)
    assert [j["id"] for j in r.recommend(status="applied")["jobs"]] == [other]
    assert r.recommend()["total"] == 0  # The role you applied to doesn't come back as new through its twin.


def test_reasons_belong_to_dislikes_and_reset_when_the_reaction_changes(tmp_path, monkeypatch):
    db, p = setup(tmp_path, monkeypatch, [job(1, "Senior React Engineer", location="Lagos", description="Angular and Java. 6+ years of experience"),
                                          job(2, "Developer", location="Lagos")])
    store.reprocess(db)
    assert not store.set_reason(db, "t:1", "stack")  # Nothing to explain yet.
    store.set_feedback(db, "t:1", "dislike")
    assert store.set_reason(db, "t:1", "stack")
    assert store.dislikes(db).stack >= {"angular", "java"} and store.dislikes(db).general is None
    store.set_reason(db, "t:1", "seniority")
    d = store.dislikes(db)
    assert (d.level, d.years, d.stack) == (3.0, 6, frozenset())
    assert Ranker(db, p).recommend(status="dislike")["jobs"][0]["reason"] == "seniority"
    store.set_feedback(db, "t:1", "dislike")
    assert store.dislikes(db).level == 3.0  # Repeating keeps the reason.
    store.set_feedback(db, "t:1", "like")
    store.set_feedback(db, "t:1", "dislike")
    assert store.dislikes(db).general is not None  # A fresh dislike starts without one.
