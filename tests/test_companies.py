import sqlite3

import pytest

from scout import companies as c
from scout.companies import Page, Seed


def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return c.connect(conn)


def test_name_key_ignores_legal_suffixes():
    assert c.name_key("MTN NIGERIA COMMUNICATIONS PLC") == c.name_key("MTN Nigeria Communications Plc.") == "mtn communications"
    assert c.name_key("Dangote Cement & Co. Limited") == "dangote cement"


def test_industry_buckets():
    assert c.industry_of("ICT / Processing Systems") == "Fintech & payments"
    assert c.industry_of("Commercial bank") == "Banking"
    assert c.industry_of("FINANCIAL SERVICES / \"Insurance Carriers, Brokers a") == "Insurance & pensions"
    assert c.industry_of("CONSUMER GOODS / Food Products") == "Consumer goods & retail"
    assert c.industry_of("ICT / Telecommunications Services") == "Telecoms"
    assert c.industry_of("") == "Other"


def test_seeds_merge_across_registers_by_name_then_host():
    conn = db()
    c.save_seeds(conn, [Seed("Access Bank Plc", None, "Commercial bank", "cbn"),
                        Seed("Fidson Healthcare Plc", "www.fidson.com", "HEALTHCARE", "ngx")])
    added = c.save_seeds(conn, [Seed("ACCESS BANK PLC", "https://www.accessbankplc.com", "FINANCIAL SERVICES", "ngx"),
                                Seed("Fidson", "https://fidson.com/", "pharmaceutical industry", "wikidata")])
    assert added == 0
    rows = {r["key"]: r for r in conn.execute("SELECT * FROM companies")}
    assert set(rows) == {"access bank", "fidson healthcare"}
    assert rows["access bank"]["website"] == "https://www.accessbankplc.com"
    assert '"cbn", "ngx"' in rows["access bank"]["sources"] and rows["access bank"]["industry"] == "Banking"
    assert "wikidata" in rows["fidson healthcare"]["sources"]


def test_reseeding_keeps_crawl_results():
    conn = db()
    c.save_seeds(conn, [Seed("Acme Plc", "acme.com", "ICT", "ngx")])
    c.save_result(conn, c.Result("acme", "ats", ats="lever", ats_slug="acme"))
    c.save_seeds(conn, [Seed("Acme Plc", "acme.com", "ICT", "ngx")])
    assert conn.execute("SELECT status, ats_slug FROM companies").fetchone()[:] == ("ats", "acme")


def test_parse_ngx_and_wikidata():
    ngx = c.parse_ngx([{"CompanyName": "CWG PLC", "Website": "www.cwg-plc.com", "Sector": "ICT", "SubSector": "IT Services"},
                       {"CompanyName": "UPDC REIT", "Website": None, "Sector": "CONSTRUCTION/REAL ESTATE", "SubSector": None}])
    assert ngx[0].website == "https://www.cwg-plc.com" and ngx[0].industry_raw == "ICT / IT Services" and ngx[1].website is None
    wd = c.parse_wikidata({"results": {"bindings": [
        {"cLabel": {"value": "LemFi"}, "site": {"value": "https://lemfi.com"}, "labels": {"value": "company | fintech"}},
        {"cLabel": {"value": "University of Ibadan"}, "site": {"value": "https://ui.edu.ng"}, "labels": {"value": "higher education"}}]}})
    assert [s.name for s in wd] == ["LemFi"]


def test_detect_ats_from_links_iframes_and_scripts():
    assert c.detect_ats('<a href="https://boards.greenhouse.io/moniepoint/jobs/1">') == [("greenhouse", "moniepoint")]
    assert c.detect_ats('<script src="https://boards.greenhouse.io/embed/job_board/js?for=kuda"></script>') == [("greenhouse", "kuda")]
    assert c.detect_ats('<iframe src="https://jobs.lever.co/tala">') == [("lever", "tala")]
    assert c.detect_ats('href="https://apply.workable.com/flutterwave/"') == [("workable", "flutterwave")]
    assert c.detect_ats('https://chowdeck.bamboohr.com/careers') == [("bamboohr", "chowdeck")]
    assert c.detect_ats('https://unilever.wd3.myworkdayjobs.com/en-US/Unilever_Experienced_Professionals') == \
        [("workday", "unilever.wd3.myworkdayjobs.com/Unilever_Experienced_Professionals")]
    assert c.detect_ats('<link href="https://rmkcdn.successfactors.com/abc/style.css">') == [("successfactors", None)]
    assert c.detect_ats("https://ehle.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/7502") == \
        [("oracle", "ehle.fa.em2.oraclecloud.com/CX_1")]
    assert c.detect_ats("https://ehle.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/") == [("oracle", "ehle.fa.em2.oraclecloud.com")]
    assert c.detect_ats('<a href="https://twitter.com/acme">') == []


def test_readable_ats_ranks_first():
    hits = c.detect_ats('//acme.taleo.net/x  https://jobs.lever.co/acme')
    assert hits[0] == ("lever", "acme")


def test_career_links_prefers_careers_pages_on_the_same_site():
    links = [("https://acme.com/about", "About us"), ("https://acme.com/careers", "Careers"),
             ("https://acme.com/news/jobs-boost-2024", "We created 500 jobs"), ("https://other.com/careers", "Careers"),
             ("https://careers.acme.com/", "Join us"), ("https://jobs.lever.co/acme", "Open roles")]
    found = c.career_links(links, "acme.com")
    assert found[0] == "https://jobs.lever.co/acme"
    assert "https://acme.com/careers" in found and "https://careers.acme.com/" in found
    assert not any("news" in u or "other.com" in u for u in found)


def test_classify_careers_pages():
    assert c.classify_careers('<p>Interested? Send your CV to recruitment@acme.com</p>')[:2] == ("email", None)
    assert c.classify_careers('<a href="https://www.linkedin.com/company/acme/jobs">See our openings</a>')[0] == "linkedin"
    assert c.classify_careers('<iframe src="https://apply.workable.com/acme/"></iframe>')[:3] == ("ats", "workable", "acme")
    assert c.classify_careers('<h1>Careers</h1><ul><li><a href="/careers/frontend">Frontend Developer</a></li></ul>')[0] == "custom"


class FakeWeb:
    def __init__(self, pages: dict[str, str]):
        self.pages, self.asked = pages, []

    def __call__(self, url):
        self.asked.append(url)
        if url.endswith("/robots.txt"):
            return Page(url, 404, "")
        if url not in self.pages:
            return Page(url, 404, "")
        return Page(url, 200, self.pages[url])


def crawl(pages, website="https://acme.com", name="Acme Plc"):
    web = FakeWeb(pages)
    return c.crawl_one("acme", name, website, fetcher=web, robots=c.Robots(web), pause=0), web


def test_crawl_follows_careers_link_to_ats():
    r, _ = crawl({"https://acme.com": '<title>Acme</title><a href="/careers">Careers</a>',
                  "https://acme.com/careers": '<a href="https://jobs.lever.co/acme">See open roles</a>'})
    assert (r.status, r.ats, r.ats_slug) == ("ats", "lever", "acme")


def test_crawl_custom_page():
    r, _ = crawl({"https://acme.com": '<a href="/join-us">Join our team</a>',
                  "https://acme.com/join-us": '<h1>Vacancies</h1><a href="/join-us/dev">Developer</a>'})
    assert (r.status, r.careers_url) == ("custom", "https://acme.com/join-us")


def test_crawl_tries_common_paths_but_needs_a_careers_title():
    home = "<p>" + "word " * 50 + "</p>" + '<a href="/a">a</a>' * 6
    r, _ = crawl({"https://acme.com": home, "https://acme.com/careers": "<title>Careers | Acme</title>"})
    assert r.status == "custom"
    r, _ = crawl({"https://acme.com": home, "https://acme.com/careers": "<title>Acme | Home</title>"})
    assert r.status == "no_careers"


def test_crawl_respects_robots():
    class Disallowing(FakeWeb):
        def __call__(self, url):
            if url.endswith("/robots.txt"):
                return Page(url, 200, "User-agent: *\nDisallow: /")
            return super().__call__(url)
    web = Disallowing({"https://acme.com": "x"})
    r = c.crawl_one("acme", "Acme", "https://acme.com", fetcher=web, robots=c.Robots(web), pause=0)
    assert r.status == "blocked" and "https://acme.com" not in web.asked


def test_crawl_spa_homepage():
    r, _ = crawl({"https://acme.com": '<div id="root"></div><script src="/app.js"></script>'})
    assert r.status == "js_only"


def test_website_guess_must_name_the_company(monkeypatch):
    monkeypatch.setattr(c.socket, "getaddrinfo", lambda *a: True)
    web = FakeWeb({"https://alphamorganbank.com": "<title>Alpha Morgan Bank | Home</title> Victoria Island, Lagos"})
    assert c.guess_website("Alpha Morgan Bank Limited", web) == "https://alphamorganbank.com"
    web = FakeWeb({"https://alphamorganbank.com": "<title>This domain is for sale</title> alpha morgan"})
    assert c.guess_website("Alpha Morgan Bank Limited", web) is None


def test_guess_rejects_a_redirect_to_another_company(monkeypatch):
    monkeypatch.setattr(c.socket, "getaddrinfo", lambda *a: True)

    class Redirecting(FakeWeb):
        def __call__(self, url):
            return Page("https://scottsdalehomewatch.com/", 200, "<title>Homes | AG Watch</title>") if "aghomes" in url else Page(url, 404, "")
    assert c.guess_website("AG HOMES", Redirecting({})) is None


def test_article_text_mentioning_careers_is_not_a_careers_link():
    links = [("https://radio.ng/ronaldo-bids-farewell", "Ronaldo ends his international career after 20 years")]
    assert c.career_links(links, "radio.ng") == []


def test_dns_failure_on_www_falls_back_to_bare_host():
    import socket
    import urllib.error

    def web(url):
        if "//www." in url:
            raise urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
        return Page(url, 200, "ok")
    assert c.fetch_home("https://www.abbey.com/", web).url == "https://abbey.com/"


def test_ascii_url():
    assert c.ascii_url("https://café.ng/carrières?q=é") == "https://xn--caf-dma.ng/carri%C3%A8res?q=%C3%A9"


def test_careers_on_the_parent_groups_site_count():
    links = [("https://careers.theheinekencompany.com/Nigerian-Breweries", "Careers"), ("https://twitter.com/careers", "Careers")]
    assert c.career_links(links, "nbplc.com") == ["https://careers.theheinekencompany.com/Nigerian-Breweries"]


def test_a_careers_page_that_errors_is_not_no_careers():
    class Down(FakeWeb):
        def __call__(self, url):
            return Page(url, 503, "") if "apply-now" in url else super().__call__(url)
    web = Down({"https://acme.com": '<a href="https://jobs.acme.com/apply-now/">Careers</a>'})
    r = c.crawl_one("acme", "Acme", "https://acme.com", fetcher=web, robots=c.Robots(web), pause=0)
    assert (r.status, r.careers_url) == ("blocked", "https://jobs.acme.com/apply-now/") and "503" in r.note


def test_guess_needs_a_nigerian_page_and_two_words(monkeypatch):
    monkeypatch.setattr(c.socket, "getaddrinfo", lambda *a: True)
    assert "pilot.com" not in c.domain_guesses("PILOT FINANCE LIMITED")
    assert "alphamorgan.com" in c.domain_guesses("Alpha Morgan Bank Limited")
    web = FakeWeb({"https://vivafinance.com": "<title>Viva Finance</title> Payments across Europe"})
    assert c.guess_website("VIVA FINANCE LIMITED", web) is None


def test_failed_reguess_clears_the_old_guess():
    conn = db()
    c.save_seeds(conn, [Seed("AG Homes", None, "Mortgage bank", "cbn")])
    c.save_result(conn, c.Result("ag homes", "custom", website="https://wrong.com", website_guessed=True))
    c.save_result(conn, c.Result("ag homes", "unreachable"))
    assert conn.execute("SELECT website FROM companies").fetchone()[0] is None


def test_listed_website_survives_an_unreachable_crawl():
    conn = db()
    c.save_seeds(conn, [Seed("Acme Plc", "https://acme.com", "ICT", "ngx")])
    c.save_result(conn, c.Result("acme", "unreachable"))
    assert conn.execute("SELECT website FROM companies").fetchone()[0] == "https://acme.com"


def test_article_slugs_and_dated_paths_are_not_careers_pages():
    links = [("https://independent.ng/asake-says-dancing-is-his-music-career/", "Asake"),
             ("https://observer.ng/2019/01/jobs/", "Jobs"), ("https://acme.ng/our-careers/", "Work"),
             ("https://acme.ng/careers.php", "x")]
    assert c.career_links(links, "independent.ng") == []
    assert c.career_links(links, "observer.ng") == []
    assert c.career_links(links, "acme.ng") == ["https://acme.ng/careers.php", "https://acme.ng/our-careers/"]


def test_fallback_path_redirected_to_an_article_does_not_count():
    class Redirecting(FakeWeb):
        def __call__(self, url):
            if url.endswith("/careers"):
                return Page("https://news.ng/career-vs-home-where-to-draw-the-line/", 200, "<title>Career vs home</title>")
            return super().__call__(url)
    home = "<p>" + "word " * 50 + "</p>" + '<a href="/a">a</a>' * 6
    web = Redirecting({"https://news.ng": home})
    assert c.crawl_one("n", "News", "https://news.ng", fetcher=web, robots=c.Robots(web), pause=0).status == "no_careers"


def test_website_from_email_ignores_webmail():
    assert c.website_from_email("info@cfgafrica.com") == "https://cfgafrica.com"
    assert c.website_from_email("a.b@abdulaitaiwo.com.ng") == "https://abdulaitaiwo.com.ng"
    assert c.website_from_email("lizzyoris@yahoo.com") is None
    assert c.website_from_email("x@gmail.com; hr@acme.ng") == "https://acme.ng"
    assert c.website_from_email("N/A") is None


SEC_PAGE = '''<p>Page 1 of 65.</p>
<button onclick="toggleAccordion('operator-3035')"><p class="text-base sm:text-lg font-medium">CFG Africa Trustees Limited</p></button>
<div id="operator-3035"><h4 class="x">Address:</h4> <p class="y">Victoria Island, Lagos</p>
<h4 class="x">Email:</h4> <p class="y">info@cfgafrica.com</p><h4 class="x">Function 1</h4> <p class="y">Trustees</p></div>
<button onclick="toggleAccordion('operator-12')"><p class="text-lg font-medium">Abdulai Taiwo &amp; Co.</p></button>
<div><h4>Email:</h4> <p>firm@gmail.com</p><h4>Function 1</h4> <p>Solicitors</p></div>'''


def test_sec_operator_page():
    seeds = c.parse_sec_operators(SEC_PAGE)
    assert [(s.name, s.website) for s in seeds] == [("CFG Africa Trustees Limited", "https://cfgafrica.com"), ("Abdulai Taiwo & Co.", None)]
    assert c.sec_page_count(SEC_PAGE) == 65
    assert c.industry_of(seeds[0].industry_raw) == "Investment & capital markets"
    assert c.industry_of(seeds[1].industry_raw) == "Legal & professional services"


def test_sec_fintech_tables():
    page = '''<table><tr><th>Crowdfunding</th><th>Digital Sub-Broker</th></tr><tr><td>PropCrowdy Ltd</td><td>Chaka Technologies Ltd</td></tr>
    <tr><td></td><td>Bamboo System Technology</td></tr></table>
    <table><tr><th>S/N</th><th>Company</th><th>Function</th><th>Contact</th></tr>
    <tr><td>1</td><td>Busha Digital Limited Trading Name: Busha</td><td>Digital Assets Exchange</td><td>Lekki Email: legal@busha.co</td></tr>
    <tr><td>2</td><td>NASD Plc</td><td>Exchange</td><td>Email: info@nasdng.com Website www.nasdng.com</td></tr></table>'''
    seeds = {s.name: s for s in c.parse_sec_fintech(page)}
    assert set(seeds) == {"PropCrowdy Ltd", "Chaka Technologies Ltd", "Bamboo System Technology", "Busha Digital Limited", "NASD Plc"}
    assert seeds["Busha Digital Limited"].website == "https://busha.co" and seeds["NASD Plc"].website == "https://www.nasdng.com"
    assert c.industry_of(seeds["PropCrowdy Ltd"].industry_raw) == "Fintech & payments"


def test_ncc_skips_lapsed_licences():
    from datetime import datetime, timezone
    text = ("﻿COMPANY NAME,CATEGORY,ADDRESS,START DATE,EXPIRY DATE\n"
            "Nitroswitch Limited,VAS Aggregator,\"Victoria Island, Lagos\",01/03/2024,28/02/2029\n"
            "Old Net Ltd,Internet Service Provider,Abuja,01/01/2015,31/12/2020\n"
            "No Date Ltd,MVNO,Lagos,,\n")
    seeds = c.parse_ncc(text, today=datetime(2026, 11, 13, tzinfo=timezone.utc))
    assert [s.name for s in seeds] == ["Nitroswitch Limited", "No Date Ltd"]
    assert c.industry_of(seeds[0].industry_raw) == "Telecoms"


def test_ncc_csv_links_skip_the_class_list():
    page = '<a href="/sites/default/files/2026-10/Indvd_Licensees.csv">x</a><a href="/sites/default/files/2026-10/class_licensee.csv">y</a>'
    assert c.ncc_csv_links(page) == ["https://www.ncc.gov.ng/sites/default/files/2026-10/Indvd_Licensees.csv"]


def test_email_pages_keep_their_addresses_hiring_ones_first():
    page = ('<p>Send your CV to us.</p><a href="mailto:info@acme.ng">info@acme.ng</a> <p>or careers@acme.ng.</p>'
            '<img src="logo@2x.png"> <a href="mailto:Recruitment%40acme.ng?subject=CV">x</a>')
    status, _, _, note = c.classify_careers(page)
    assert status == "email"
    assert note == "recruitment@acme.ng · careers@acme.ng · info@acme.ng · Send your CV"  # mailto links first among hiring ones


def test_linkedin_pages_keep_the_jobs_link():
    note = c.classify_careers('<a href="https://www.linkedin.com/company/acme/jobs/?x=1&amp;y=2">See openings</a>')[3]
    assert note == "https://www.linkedin.com/company/acme/jobs/?x=1&y=2"


def test_pilot_view_lists_what_a_person_can_follow_up():
    conn = db()
    rows = [("a", "Acme", "custom", None, ""), ("b", "Beta", "email", None, "hr@beta.ng · info@beta.ng · Send your CV"),
            ("l", "Lin", "linkedin", None, "https://www.linkedin.com/company/lin/jobs/"), ("t", "Tal", "ats", "taleo", ""),
            ("g", "Gee", "ats", "greenhouse", ""), ("n", "Non", "no_careers", None, ""), ("j", "Jay", "js_only", None, ""),
            ("u", "Unc", None, None, None)]
    for key, name, status, ats, note in rows:
        conn.execute("INSERT INTO companies (key, name, website, sources, industry, status, ats, note, careers_url) VALUES (?,?,?,?,?,?,?,?,?)",
                     (key, name, f"https://{key}.ng", '["sec"]', "Banking", status, ats, note, f"https://{key}.ng/careers"))
    v = c.pilot_view(conn)
    assert (v["total"], v["crawled"], v["readable_left_out"]) == (8, 7, 1)
    by = {x["key"]: x for x in v["companies"]}
    assert sorted(by) == ["a", "b", "n"]  # Careers pages, email-a-CV and no careers page; the rest are only counted.
    assert (by["a"]["group"], by["b"]["group"], by["n"]["group"]) == ("careers", "email", "none")
    assert by["b"]["emails"] == ["hr@beta.ng", "info@beta.ng"] and by["b"]["said"] == "Send your CV"
    assert by["a"]["action"] is None and by["a"]["note"] == "" and not by["a"]["star"]
    assert (v["plan"]["js_only"], v["plan"]["blocked"], v["plan"]["no_careers"]) == ("later", "hold", "act")
    assert v["registers"] == [{"register": "sec", "crawled": 7, "found": 5}]


def test_actions_survive_a_recrawl_and_keep_their_date():
    conn = db()
    conn.execute("INSERT INTO companies (key, name, status, note) VALUES ('b', 'Beta', 'email', 'hr@beta.ng')")
    assert c.set_action(conn, "b", "emailed", False, "  sent CV  ")
    when = conn.execute("SELECT updated_at FROM company_actions").fetchone()[0]
    assert c.set_action(conn, "b", "emailed", True, "sent CV, chase in a week")  # Same action: its date stays.
    conn.execute("UPDATE companies SET status = 'custom', checked_at = 'later' WHERE key = 'b'")  # A recrawl.
    got = c.pilot_view(conn)["companies"][0]
    assert (got["action"], got["star"], got["note"], got["acted_at"]) == ("emailed", True, "sent CV, chase in a week", when)
    assert not c.set_action(conn, "nope", "applied", False, "")
    with pytest.raises(ValueError):
        c.set_action(conn, "b", "hired", False, "")
    assert c.set_action(conn, "b", None, False, "")  # Clearing everything removes the row.
    assert conn.execute("SELECT COUNT(*) FROM company_actions").fetchone()[0] == 0


def test_pilot_action_only_writes_the_actions_table(tmp_path):
    from scout.server import pilot_action
    path = tmp_path / "pilot.db"
    assert pilot_action(path, {"key": "a", "action": "applied"}) == 404 and not path.exists()
    conn = sqlite3.connect(path)
    c.connect(conn).execute("INSERT INTO companies (key, name, status, note) VALUES ('a', 'Acme', 'custom', '')")
    conn.commit()
    conn.close()
    assert pilot_action(path, {"key": "a", "action": "hired"}) == 400
    assert pilot_action(path, {"key": "a", "star": "yes"}) == 400
    assert pilot_action(path, {"key": "a", "action": "applied", "note": "via careers page"}) == 200
    from scout.server import pilot_data
    got = pilot_data(path)["companies"][0]
    assert (got["action"], got["note"]) == ("applied", "via careers page")


def test_pilot_page_opens_its_database_read_only(tmp_path):
    from scout.server import pilot_data
    path = tmp_path / "pilot.db"
    assert pilot_data(path) is None and not path.exists()
    conn = sqlite3.connect(path)
    c.connect(conn).execute("INSERT INTO companies (key, name, status, note) VALUES ('a', 'Acme', 'custom', '')")
    conn.commit()
    conn.close()
    assert [x["name"] for x in pilot_data(path)["companies"]] == ["Acme"]


def test_pilot_page_saves_actions_only_through_its_table(tmp_path):
    from scout.server import pilot_action, pilot_data
    path = tmp_path / "pilot.db"
    assert pilot_action(path, {"key": "a", "action": "applied"}) == 404 and not path.exists()
    conn = sqlite3.connect(path)
    c.connect(conn).execute("INSERT INTO companies (key, name, status, note) VALUES ('a', 'Acme', 'custom', '')")
    conn.commit()
    conn.close()
    assert pilot_action(path, {"key": "a", "action": "hired?"}) == 400
    assert pilot_action(path, {"key": "a", "star": "yes"}) == 400
    assert pilot_action(path, {"key": "nope", "action": "applied"}) == 404
    assert pilot_action(path, {"key": "a", "action": "applied", "note": "via site"}) == 200
    assert pilot_data(path)["companies"][0]["action"] == "applied"
