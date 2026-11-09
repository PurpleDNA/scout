import numpy as np
from datetime import datetime, timedelta, timezone
from scout.matching import Profile, extract_skills, freshness, job_level, required_years, score_jobs, seniority_fit, skill_fit

PROFILE = {"skills": ["react", "typescript", "python"], "core_skills": ["react"], "industries": ["fintech"], "level_range": [0, 3], "years_experience": 3}


def v(*xs):
    a = np.array(xs, dtype=np.float32)
    return a / np.linalg.norm(a)


def job(title="Frontend Engineer", skills=("react", "typescript"), **kw):
    return {"title": title, "description": "", "skills": list(skills), "industries": ["fintech"],
            "posted_at": datetime.now(timezone.utc).isoformat(), **kw}


def test_skill_extraction_avoids_lookalikes():
    assert "react" in extract_skills("Built with React.js")
    assert "react" not in extract_skills("React Native developer")
    assert "java" not in extract_skills("JavaScript only")
    assert "go" not in extract_skills("We go the extra mile")
    assert "git" not in extract_skills("digital")


def test_skill_fit_rewards_real_overlap_not_a_lucky_one():
    assert skill_fit({"react", "typescript", "python"}, {"react", "typescript", "python"}) > 0.9
    assert skill_fit({"react"}, {"react"}) < 0.6
    assert skill_fit({"java", "spring", "kafka", "react"}, {"react"}) < 0.25


def test_levels_and_years():
    assert job_level("Senior Software Engineer") == 3
    assert job_level("Engineering Manager") == 4
    assert job_level("Software Engineer") is None
    assert required_years("5+ years of experience with React") == 5
    assert required_years("3-5 years of relevant professional experience") == 3
    assert required_years("Founded 10 years ago") is None


def test_big_experience_gap_costs_more():
    close, _ = seniority_fit("Senior Engineer", "", PROFILE)
    far, years = seniority_fit("Senior Engineer", "10+ years of experience", PROFILE)
    assert years == 10 and far < close


def test_freshness_decays():
    now = datetime.now(timezone.utc)
    assert freshness(now.isoformat(), now) == 1
    assert 0.45 < freshness((now - timedelta(days=14)).isoformat(), now) < 0.55


def _profile():
    return Profile(PROFILE, np.stack([v(1, 0, 0)]), v(1, 0, 0))


def test_entry_level_is_as_welcome_as_senior_but_staff_is_not():
    entry, _ = seniority_fit("Junior Frontend Developer", "", PROFILE)
    trainee, _ = seniority_fit("Graduate Trainee", "", PROFILE)
    senior, _ = seniority_fit("Senior Frontend Engineer", "", PROFILE)
    staff, _ = seniority_fit("Staff Engineer", "", PROFILE)
    assert entry == trainee == senior == 1 and staff < 1


def test_skills_outside_your_main_stack_count_for_less():
    assert skill_fit({"python", "react"}, {"python", "react"}, {"react"}) > skill_fit({"python", "typescript"}, {"python", "typescript"}, {"react"})


def test_frontend_lean_beats_equal_secondary_match():
    p = Profile(PROFILE, np.stack([v(1, 0, 0)]), v(1, 0, 1), np.stack([v(0, 1, 0)]))
    jobs = [job("Frontend"), job("Backend")]
    front, back = score_jobs(jobs, np.stack([v(1, 0, 0), v(0, 1, 0)]), np.stack([v(1, 0, 0), v(0, 1, 0)]), p)
    assert front["parts"]["role"] > back["parts"]["role"]


def test_relevant_job_outranks_other_profession():
    jobs = [job(), job("Sales Representative", skills=())]
    titles, texts = np.stack([v(1, 0, 0), v(0, 1, 0)]), np.stack([v(1, 0, 0), v(0, 1, 0)])
    good, bad = score_jobs(jobs, titles, texts, _profile())
    assert good["score"] > 60 > 40 > bad["score"]
    assert good["matched_skills"] == ["react", "typescript"]


def test_feedback_moves_similar_jobs_and_is_bounded():
    jobs = [job(), job()]
    titles = np.stack([v(1, .2, 0), v(1, 0, .2)])
    texts = np.stack([v(1, 1, 0), v(1, 0, 1)])
    base = score_jobs(jobs, titles, texts, _profile())
    liked = (np.stack([v(1, .2, 0)]), np.stack([v(1, 1, 0)]))
    disliked = (np.stack([v(1, 0, .2)]), np.stack([v(1, 0, 1)]))
    after = score_jobs(jobs, titles, texts, _profile(), liked=liked, disliked=disliked)
    assert after[0]["score"] > base[0]["score"] and after[1]["score"] < base[1]["score"]
    assert abs(after[0]["feedback_points"]) <= 15  # At most 12 up from likes, plus 3 from being unlike a dislike.


def test_like_still_reorders_jobs_already_at_the_score_cap():
    from scout.ranker import diversify
    p = Profile({**PROFILE, "skills": ["react", "typescript"]}, np.stack([v(1, 0, 0), v(1, 1, 0), v(1, 0, 1)]), v(1, 0, 0))
    jobs = [job(company="A"), job(company="B")]
    titles = np.stack([v(1, 1, 0), v(1, 0, 1)])
    texts = np.stack([v(1, .05, 0), v(1, 0, .05)])
    liked = (np.stack([v(1, 0, 1)]), np.stack([v(1, 0, .05)]))
    rows = [{**j, **s} for j, s in zip(jobs, score_jobs(jobs, titles, texts, p, liked=liked))]
    assert diversify(rows)[0]["company"] == "B"


def test_one_reaction_lifts_a_few_jobs_without_inflating_the_pool():
    rng = np.random.default_rng(1)
    vecs = rng.normal(size=(300, 8))
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    jobs = [job(company=str(i)) for i in range(300)]
    p = Profile(PROFILE, vecs[:1], vecs[0])
    points = np.array([s["feedback_points"] for s in score_jobs(jobs, vecs, vecs, p, liked=(vecs[5:6], vecs[5:6]))])
    assert points[5] == 12
    assert (points > 0).mean() <= 0.11
    assert points.min() >= -3


def test_low_priority_titles_sink_even_when_embeddings_call_them_close():
    p = Profile({**PROFILE, "low_priority_title_terms": ["backend"]}, np.stack([v(1, 0, 0)]), v(1, 0, 0))
    front, back = score_jobs([job("Frontend Engineer"), job("Backend Engineer")], np.stack([v(1, 0, 0)] * 2), np.stack([v(1, 0, 0)] * 2), p)
    assert back["score"] < front["score"]


def _points(jobs, titles, texts, disliked):
    p = Profile(PROFILE, np.stack([v(1, 0, 0, 0)]), v(1, 0, 0, 0))
    return [s["feedback_points"] for s in score_jobs(jobs, titles, texts, p, disliked=disliked)]


def _pool(n=40, seed=3):
    rng = np.random.default_rng(seed)
    vecs = rng.normal(size=(n, 4)).astype(np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def test_a_location_reason_barely_moves_similar_jobs():
    from scout.matching import Dislikes
    vecs = _pool()
    jobs = [job(company=str(i)) for i in range(len(vecs))]
    general = _points(jobs, vecs, vecs, Dislikes(general=(vecs[:1], vecs[:1])))
    location = _points(jobs, vecs, vecs, Dislikes(location=(vecs[:1], vecs[:1])))
    assert general[0] == -12 and -3.5 < location[0] < 0


def test_a_role_reason_follows_titles_not_descriptions():
    from scout.matching import Dislikes
    titles = np.stack([v(1, 0, 0, 0), v(0, 1, 0, 0)] + [v(0, 0, 1, 1)] * 10)
    texts = np.stack([v(0, 0, 1, 0), v(0, 0, 1, 0)] + [v(0, 0, 0, 1)] * 10)  # The first two share a description.
    jobs = [job(company=str(i)) for i in range(len(titles))]
    pts = _points(jobs, titles, texts, Dislikes(role_titles=titles[:1]))
    assert pts[0] < -10 and pts[1] > pts[0] + 5


def test_a_stack_reason_sinks_jobs_asking_for_those_skills_but_never_yours():
    from scout.matching import Dislikes
    vecs = np.stack([v(1, 0, 0, 0)] * 4)
    jobs = [job(skills=("angular",)), job(skills=("angular", "java")), job(skills=("react",)), job(skills=("vue",))]
    pts = _points(jobs, vecs, vecs, Dislikes(stack=frozenset({"angular", "java", "react"})))
    assert pts == [-6, -12, 0, 0]  # React is in the profile, so disliking a React posting for its stack can't sink React.


def test_a_seniority_reason_sinks_that_level_and_above():
    from scout.matching import Dislikes
    vecs = np.stack([v(1, 0, 0, 0)] * 4)
    jobs = [job("Senior Engineer"), job("Lead Engineer"), job("Engineer"), job("Engineer", description="5+ years of experience")]
    pts = _points(jobs, vecs, vecs, Dislikes(level=3.0, years=5))
    assert pts == [-8, -8, 0, -8]
