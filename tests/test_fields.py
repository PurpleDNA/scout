"""Profiles other than the original one: other fields, other cities, moving, no remote jobs abroad."""
import json

import pytest

from scout import eligibility, fields, programmes, sources
from scout.eligibility import area_for, classify, set_area
from scout.matching import job_level, vocabulary
from scout.setup import build_profile, summary_from_cv


@pytest.fixture(autouse=True)
def original_setup():
    """configure() changes module settings. Put them back after each test."""
    from scout import matching
    saved = (eligibility.AREA, sources.JOBBERMAN_PAGES, sources.MYJOBMAG_PAGES, programmes.ROUNDUP_ROLES, set(matching.ACTIVE_SKILLS))
    yield
    eligibility.AREA, sources.JOBBERMAN_PAGES, sources.MYJOBMAG_PAGES, programmes.ROUNDUP_ROLES, active = saved
    matching.set_active_skills(active)
    matching.set_mid_managers(False)


def test_a_profile_without_the_new_keys_reads_the_same_pages_as_before():
    fields.configure({})
    assert sources.JOBBERMAN_PAGES == ["/jobs/software-data/lagos", "/jobs/software-data", "/jobs/lagos"]
    assert sources.MYJOBMAG_PAGES == ["/jobs-by-field/information-technology", "/jobs-location/lagos"]
    assert fields.skipped_sources({}) == set()


def test_sales_reads_sales_pages_and_skips_remote_boards_when_asked():
    profile = {"fields": ["sales"], "home_city": "Lagos", "global_remote": False}
    fields.configure(profile)
    assert sources.JOBBERMAN_PAGES[:2] == ["/jobs/sales/lagos", "/jobs/sales"]
    assert "/jobs-by-field/sales-marketing" in sources.MYJOBMAG_PAGES
    assert "himalayas" in fields.skipped_sources(profile) and "jobberman" not in fields.skipped_sources(profile)


def test_a_city_without_its_own_board_pages_uses_the_nationwide_ones():
    jobberman, myjobmag = fields.board_pages(["sales"], "Ibadan")
    assert jobberman == ["/jobs/sales", "/jobs/management-business-development"] and myjobmag == ["/jobs-by-field/sales-marketing"]
    assert fields.board_pages(["sales"], "Port Harcourt")[1][-1] == "/jobs-location/rivers"


def test_round_up_programme_posts_follow_the_field():
    roundup = {"title": "Dangote Graduate Trainee Programme - Multiple Positions", "category": "Graduate Trainee",
               "description": "<p>Sales Executive, Accountant</p>"}
    assert not programmes.wanted(roundup)  # The original profile only keeps round-ups naming a tech role.
    fields.configure({"fields": ["sales"]})
    assert programmes.wanted(roundup)


def test_office_jobs_count_only_where_you_live_or_would_move():
    abuja = dict(location="Wuse 2, Abuja, Nigeria", workplace="onsite")
    assert classify(**abuja).lane == "rejected"
    set_area(area_for("Lagos", ["Abuja"]))
    e = classify(**abuja)
    assert (e.lane, e.scope) == ("nigeria", "nigeria") and "you'd move" in e.evidence
    assert classify(location="Kano, Nigeria", workplace="onsite").lane == "rejected"
    set_area(area_for("Lagos", "anywhere"))
    assert classify(location="Kano, Nigeria", workplace="onsite").lane == "nigeria"
    assert classify(location="London, UK", workplace="onsite").lane == "rejected"


def test_another_home_city():
    set_area(area_for("Abuja"))
    assert (classify(location="Abuja, Nigeria", workplace="hybrid").scope) == "abuja"
    assert classify(location="Ikeja, Lagos", workplace="onsite").lane == "rejected"
    assert classify(location="Lagos", workplace="remote").lane == "nigeria"  # Remote work in Nigeria is open wherever you live.


def test_relationship_and_account_managers_are_mid_level_for_business_profiles():
    assert job_level("Relationship Manager (Lagos)") == 4  # The original profile is unchanged.
    fields.configure({"fields": ["sales"], "skill_groups": ["business"]})
    assert job_level("Relationship Manager (Lagos)") == 2
    assert job_level("Senior Key Account Manager") == 3
    assert job_level("Head of Sales") == 5 and job_level("Engineering Manager") == 4


def test_business_skills_only_count_for_business_profiles():
    from scout.matching import extract_skills
    assert "negotiation" not in vocabulary({"skills": ["react"]})
    assert "negotiation" in vocabulary({"skill_groups": ["business"], "skills": []})
    text = "React developer who can negotiate B2B deals"
    assert extract_skills(text) == ["react"]  # The original profile doesn't even look for sales skills.
    fields.configure({"fields": ["sales"], "skill_groups": ["business"]})
    assert extract_skills(text) == ["b2b sales", "negotiation"]


def test_setup_builds_a_profile_scout_can_load(tmp_path, monkeypatch):
    profile = build_profile({"name": "Ada", "fields": ["sales"], "titles": ["Sales Executive"], "level": 2, "years": 4,
                             "skills": ["b2b sales", "forklift operation"], "home": "Lagos", "relocate": "anywhere",
                             "global_remote": False}, "Ada Example\nada@example.com +234 803 000 0000\nSold FMCG to supermarkets.")
    assert profile["skill_groups"] == ["business"] and profile["level_range"] == [1, 3]
    assert profile["skills"] == ["b2b sales"] and profile["custom_skills"] == ["forklift operation"]
    assert "@" not in profile["summary"] and "803" not in profile["summary"]
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    from scout import matching
    monkeypatch.setattr(matching, "embed", lambda texts: __import__("numpy").ones((len(texts), 4)))
    loaded = matching.Profile.load(path)  # Custom skills are registered, so loading doesn't reject them.
    assert "forklift operation" in matching.extract_skills("Forklift operation is a plus")
    assert loaded.data["skills"] == ["b2b sales"]


def test_summary_from_cv_drops_contact_details():
    assert summary_from_cv("Jo Bloggs  jo@x.com  linkedin.com/in/jo  https://jo.dev\nSales lead") == "Jo Bloggs Sales lead"


def test_summary_from_cv_keeps_date_ranges():
    assert summary_from_cv("Call 0803-123-4567. Sales Rep, 2020 - 2022") == "Call . Sales Rep, 2020 - 2022"
