"""Location strings below are copied from live feeds (Nov 2026)."""
import pytest
from scout.eligibility import classify


def c(location="", *, workplace=None, restrictions=None, timezones=None, description="", title="Engineer", remote_board=False):
    return classify(location=location, workplace=workplace, restrictions=restrictions, timezones=timezones,
                    description=description, title=title, remote_board=remote_board)


@pytest.mark.parametrize("loc", ["Worldwide", "Anywhere in the World", "Anywhere", "Remote, Global"])
def test_worldwide_labels_are_high_confidence_global(loc):
    r = c(loc, remote_board=True)
    assert (r.lane, r.scope, r.confidence) == ("global_remote", "worldwide", "high")


@pytest.mark.parametrize("loc", ["USA Only", "North America Only", "Canada Only", "USA", "Canada,  USA", "UK", "Brazil",
    "Europe", "Remote, United States", "Remote, Canada; Remote, United States", "Remote, Spain", "Remote, Poland",
    "Remote, AMER", "Remote, APAC", "Remote, San Francisco, CA", "Your Remote US Home Office", "Dallas, Texas",
    "Copenhagen", "USA, Canada, USA timezones", "Europe, USA, Canada, Australia, Singapore, Mexico, Saudi Arabia", "Nairobi Province"])
def test_named_elsewhere_is_rejected(loc):
    assert c(loc, remote_board=True).lane == "rejected"


def test_remote_tied_to_nigeria_lands_in_the_nigeria_lane():
    assert (c("Remote, Nigeria").lane, c("Remote, Nigeria").scope) == ("nigeria", "nigeria")
    assert (c("Remote, Lagos, Nigeria").lane, c("Remote, Lagos, Nigeria").scope) == ("nigeria", "lagos")
    assert (c("Remote - Abuja").lane, c("Remote - Abuja").scope) == ("nigeria", "nigeria")
    assert c("Remote, Nigeria").work_model == "remote"


def test_every_lagos_setup_is_kept():
    assert {c("Lagos").work_model, c("Lagos", workplace="hybrid").work_model, c("Lagos", workplace="remote").work_model} == {"onsite", "hybrid", "remote"}
    for r in (c("Lagos"), c("Lagos", workplace="hybrid"), c("Lagos", workplace="remote")):
        assert r.lane == "nigeria"


def test_emea_counts_but_is_medium_confidence():
    for loc in ["EMEA", "Remote, EMEA", "EMEA,  LATAM,  Canada,  USA"]:
        r = c(loc, remote_board=True)
        assert (r.lane, r.scope, r.confidence) == ("global_remote", "emea", "medium")


def test_africa_counts():
    assert c("Remote - Africa").scope == "africa"
    assert c("Remote - Africa").lane == "global_remote"


def test_south_africa_is_not_africa():
    assert c("Remote, South Africa").lane == "rejected"


@pytest.mark.parametrize("loc", ["Remote", ""])
def test_bare_remote_is_unverified(loc):
    r = c(loc, remote_board=True)
    assert (r.lane, r.scope, r.confidence) == ("global_remote", "unknown", "low")


def test_description_can_confirm_unverified():
    r = c("Remote", description="Our team hires globally. Work from anywhere in the world.")
    assert (r.scope, r.confidence) == ("worldwide", "medium")


@pytest.mark.parametrize("text", [
    "You must be based in the United States.",
    "Applicants must be authorized to work in the US without sponsorship.",
    "This role is open to US-based candidates only.",
    "Must reside in Canada.",
    "Must have an active security clearance.",
    "You must be a US citizen.",
    "Candidates located in LATAM only.",
    "Only candidates in the UK will be considered.",
])
def test_description_restriction_overrides_worldwide_label(text):
    r = c("Worldwide", remote_board=True, description=text)
    assert r.lane == "rejected"
    assert r.evidence


def test_description_without_restriction_keeps_worldwide():
    r = c("Worldwide", remote_board=True, description="We serve customers in the United States and Canada. Our HQ is in Austin.")
    assert r.lane == "global_remote"


def test_us_hours_is_flagged_not_rejected():
    r = c("Worldwide", remote_board=True, description="You will need 4 hours of overlap with US time zones (EST).")
    assert r.lane == "global_remote"
    assert "us_hours" in r.flags


def test_structured_restrictions():
    assert c(restrictions=[], remote_board=True).scope == "worldwide"
    assert c(restrictions=["Nigeria", "Kenya"], remote_board=True).scope == "nigeria"
    assert c(restrictions=["United States"], remote_board=True).lane == "rejected"


def test_timezone_must_cover_utc_plus_one():
    assert c(restrictions=[], timezones=[-8, -7, -6], remote_board=True).lane == "rejected"
    assert c(restrictions=[], timezones=[0, 1, 2], remote_board=True).lane == "global_remote"
    assert c(restrictions=[], timezones=[], remote_board=True).lane == "global_remote"


@pytest.mark.parametrize("loc", ["Lagos, Nigeria", "Lagos", "Ikeja, Lagos", "Victoria Island, Lagos"])
def test_lagos_defaults_onsite(loc):
    r = c(loc)
    assert (r.lane, r.work_model, r.scope) == ("nigeria", "onsite", "lagos")


def test_lagos_hybrid_detected():
    assert c("Lagos, Nigeria", workplace="hybrid").work_model == "hybrid"
    assert c("Lagos", description="This is a hybrid role, three days in office.").work_model == "hybrid"
    assert c("Hybrid - Lagos").work_model == "hybrid"
    assert c("Lagos, Nigeria", workplace="hybrid").lane == "nigeria"


def test_lagos_listing_mentioning_remote_area_stays_onsite():
    assert c("Lagos", description="You will visit remote areas to service customers.").work_model == "onsite"


def test_lagos_listing_truly_remote_stays_in_the_nigeria_lane():
    r = c("Lagos", description="This is a fully remote role.")
    assert (r.work_model, r.lane) == ("remote", "nigeria")


@pytest.mark.parametrize("loc", ["Gbagada", "Ikorodu, Lagos State", "Festac Town", "Amuwo-Odofin", "Ebute Metta", "Eko Atlantic"])
def test_lagos_areas_named_alone(loc):
    assert (c(loc).lane, c(loc).scope) == ("nigeria", "lagos")


@pytest.mark.parametrize("loc", ["Abuja", "Edo, Nigeria", "Port Harcourt", "Ibadan, Oyo State", "Kano", "Nigeria", "Benin City",
                                 "Rivers State", "FCT", "South West Region, Nigeria", "Warri, Delta State"])
def test_onsite_elsewhere_in_nigeria_is_rejected(loc):
    r = c(loc)
    assert (r.lane, r.work_model, r.scope) == ("rejected", "onsite", "nigeria")
    assert "outside Lagos" in r.evidence
    assert c(loc, workplace="hybrid").lane == "rejected"


@pytest.mark.parametrize("loc", ["Abuja", "Port Harcourt", "Kano, Nigeria", "Benin City", "Rivers State", "Warri, Delta State"])
def test_remote_anywhere_in_nigeria_is_kept(loc):
    r = c(loc, workplace="remote")
    assert (r.lane, r.scope) == ("nigeria", "nigeria")


def test_nigerian_listing_saying_remote_in_its_text_is_remote():
    for loc in ("Nigeria", "Abuja", "Port Harcourt, Rivers State"):
        r = c(loc, description="This is a fully remote role.")
        assert (r.work_model, r.lane) == ("remote", "nigeria"), loc
    assert c("Abuja", description="This is a hybrid role, three days in office.").work_model == "hybrid"


@pytest.mark.parametrize("loc", ["London, United Kingdom", "Nairobi, Kenya", "Bangalore, India", "Benin", "Baltimore, Maryland",
                                 "Niamey, Niger", "Epe, Netherlands", "San Jose, CA", "Saudi Arabia", "Minnesota"])
def test_onsite_outside_nigeria_is_rejected(loc):
    assert c(loc).lane == "rejected"


def test_workplace_flag_wins_over_label():
    assert c("Remote, Global", workplace="onsite").lane == "rejected"
    assert c("London", workplace="remote").lane == "rejected"


def test_every_decision_explains_itself():
    assert c("USA Only", remote_board=True).evidence
    assert c("Lagos").evidence
    assert c("Worldwide", remote_board=True).evidence


# ---- Regressions found by auditing live data ----

@pytest.mark.parametrize("loc", ["Home based - Worldwide", "Home Based - Worldwide"])
def test_home_based_means_remote(loc):
    r = c(loc)
    assert (r.lane, r.work_model, r.scope) == ("global_remote", "remote", "worldwide")


def test_home_based_regions():
    assert c("Home based - EMEA").scope == "emea"
    assert c("Home based - Americas").lane == "rejected"
    assert c("Home Based - Americas; Home based - EMEA").scope == "emea"


@pytest.mark.parametrize("text", [
    "The salary range listed for this role applies to US-based candidates only. Compensation for others varies.",
    "This listed level is currently for residents of the United States only.",
    "We offer a wide range of benefits to U.S.-based employees, including medical and vision insurance.",
    "For US-based applicants: this position is part of the Communication Workers union.",
    "Our compensation for US based candidates lies between 48,000 USD to 70,000 USD.",
    "The range provided applies to candidates located in the United States. For others, ranges may vary.",
    "Eligibility for company-sponsored health benefits is limited to team members based in the United States.",
    "You will report to an Engineering Manager based in Europe.",
    "Our customer base is located across Europe and North America.",
    "Feel free to contact us only if you have questions.",
    "Please tell us citizens of any country are welcome.",
])
def test_pay_and_benefit_notes_are_not_restrictions(text):
    assert c("Worldwide", remote_board=True, description=text).lane == "global_remote"


@pytest.mark.parametrize("text", [
    "This position is based in Germany with hybrid or remote working options.",
    "Senior Software Engineer. Remote role based in Latin America.",
    "This role will need to be based remotely in UK, Ireland, Netherlands, France, Spain or Germany.",
    "Location: Fully remote for candidates based in Europe within CET plus or minus 3 hours.",
    "We are seeking candidates based in the Pacific or Mountain time zones.",
    "Based in the US or Canada and able to work EST hours.",
    "Looking for Philippines-based candidates.",
    "Applicants must be based in the USA and eligible for employment.",
    "This is a fully remote position, but you must be located within the United States.",
])
def test_prose_restrictions_seen_in_worldwide_postings(text):
    assert c("Worldwide", remote_board=True, description=text).lane == "rejected"


@pytest.mark.parametrize("text", ["Availability: Between 9 a.m. and 6 p.m. EST", "Time Zone : PST", "Available in UTC or EST"])
def test_us_hours_variants_flagged(text):
    assert "us_hours" in c("Worldwide", remote_board=True, description=text).flags


def test_unstated_lagos_setup_says_so():
    assert "doesn't mention" in c("Lagos").evidence
    assert "doesn't mention" not in c("Lagos", workplace="onsite").evidence


# ---- Himalayas' long country lists are region expansions, not a statement about Nigeria ----
EMEA_LIST = ["Afghanistan", "Albania"] + [f"C{i}" for i in range(140)] + ["Nigeria"]
ALMOST_ALL = EMEA_LIST + ["United States", "Canada", "Brazil"] + [f"D{i}" for i in range(20)]


def test_short_list_with_nigeria_is_explicit():
    r = c(restrictions=["Ghana", "Kenya", "Nigeria"], remote_board=True)
    assert (r.scope, r.confidence) == ("nigeria", "high")


def test_explicit_nigeria_isnt_overruled_by_prose():
    assert c(restrictions=["Nigeria"], remote_board=True, description="Must be based in the UK or Nigeria.").lane == "global_remote"


def test_region_sized_list_is_only_emea_confidence():
    r = c(restrictions=EMEA_LIST, remote_board=True)
    assert (r.lane, r.scope, r.confidence) == ("global_remote", "emea", "medium")


def test_almost_every_country_is_worldwide_medium():
    r = c(restrictions=ALMOST_ALL, remote_board=True)
    assert (r.scope, r.confidence) == ("worldwide", "medium")


@pytest.mark.parametrize("text", [
    "You are based in Europe and available to work 35 hours per week.",
    "This is a remote position within the EU/UK.",
    "Location: Remote within Europe.",
    "This role requires you to be based in Europe so you can collaborate.",
    "We are open to remote candidates based in the US or EU.",
    "We are currently prioritizing candidates who reside in the United States or Europe. Salary range: $180,000.",
])
def test_long_list_with_prose_restriction_is_rejected(text):
    assert c(restrictions=EMEA_LIST, remote_board=True, description=text).lane == "rejected"


@pytest.mark.parametrize("text", [
    "Being based in Europe is a big plus.",
    "This is a Global, remote role ideally based in Poland.",
    "Preference for candidates based in the Eastern Time time zone.",
    "Demonstrated success within US and/or EMEA markets.",
    "This is a remote position open to applications based in the Europe, Middle East and Africa region.",
])
def test_preferences_and_experience_arent_restrictions(text):
    assert c("Worldwide", remote_board=True, description=text).lane == "global_remote"


@pytest.mark.parametrize("title", ["Full Stack Engineer (Fully remote / Europe / CET)", "European Carrier Sales Director",
                                   "Product Support Specialist - LATAM", "Sales Rep - DACH", "Account Executive, US"])
def test_region_in_title_rejects(title):
    assert c(restrictions=EMEA_LIST, remote_board=True, title=title).lane == "rejected"


@pytest.mark.parametrize("title", ["Customer Success Engineer - EMEA", "Senior Engineer (Remote, Africa)", "Frontend Engineer"])
def test_acceptable_titles_pass(title):
    assert c(restrictions=EMEA_LIST, remote_board=True, title=title).lane == "global_remote"
