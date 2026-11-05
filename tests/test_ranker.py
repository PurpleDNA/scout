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
    assert data["counts"] == {"global_remote": 1, "lagos": 1}  # 1 and 2 merged; 4 and 6 rejected; 5 unverified
    assert r.recommend(verified_only=False)["counts"]["global_remote"] == 2
    assert [j["work_model"] for j in r.recommend(lane="lagos")["jobs"]] == ["onsite"]
    assert r.recommend()["reactions"] == {"like": 0, "applied": 0, "dislike": 0}

    target = r.recommend(lane="lagos")["jobs"][0]["id"]
    store.set_feedback(db, target, "dislike")
    assert r.recommend(lane="lagos")["total"] == 0
    assert r.recommend(lane="lagos", status="dislike")["total"] == 1
    store.set_feedback(db, target, None)
    assert r.recommend(lane="lagos")["total"] == 1


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
