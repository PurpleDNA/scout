from scout.sources import parse_jobberman, parse_myjobmag, relative_date, text_of

JOBBERMAN = '''<article data-cy="listing-cards-components"><div><h3><a href="https://www.jobberman.com/listings/qa-engineer-abc123" data-cy="listing-title-link" title="QA &amp; Test Engineer" onclick="x">QA</a></h3>
<p><svg><path/></svg><span>Acme Ltd</span></p></div><div><span>Posted 2 weeks ago</span></div>
<div><div data-cy="description-tile"><p><a href="/jobs/x">Software</a> - Test our apps.</p></div></div>
<div><span><svg></svg>Lagos</span><span>Full Time</span><span>Confidential</span></div></article>'''
MYJOBMAG = '''<li class="job-info"><ul><li class="mag-b"><h2><a href="/job/dev-at-acme">Developer at Acme Ltd</a></h2></li>
<li class="job-desc"> Build things </li><li class="job_detail_tag"><span class="job-salary">₦100,000 - ₦150,000</span></li>
<li id="job-date">03 November <span><a style='color:blue;' href='/jobs-location/lagos'>Lagos</a></span></li></ul></li>'''


def test_jobberman_card():
    j = next(iter(parse_jobberman(JOBBERMAN)))
    assert (j.title, j.company, j.location, j.external_id) == ("QA & Test Engineer", "Acme Ltd", "Lagos", "abc123")
    assert j.salary == "" and j.posted_at and "Test our apps" in j.description


def test_jobberman_remote_means_nigeria():
    page = JOBBERMAN.replace("Lagos</span>", "Remote (Work From Home)</span>")
    assert next(iter(parse_jobberman(page))).location == "Remote, Nigeria"


def test_myjobmag_card():
    j = next(iter(parse_myjobmag(MYJOBMAG)))
    assert (j.title, j.company, j.location) == ("Developer", "Acme Ltd", "Lagos")
    assert j.salary.startswith("₦") and j.url.endswith("/job/dev-at-acme")


def test_text_of_handles_double_escaping_and_mojibake():
    assert text_of("&lt;p&gt;Hi &amp;amp; bye&lt;/p&gt;") == "Hi & bye"
    assert text_of("Lead â\u0080\u0094 Logistics") == "Lead — Logistics"
    assert text_of("Café ₦100") == "Café ₦100"


def test_relative_dates():
    assert relative_date("Posted 4 weeks ago") and relative_date("today") and relative_date("03 November")
    assert relative_date("whenever") is None


def test_salary_shows_its_period():
    from scout.sources import money
    assert money(100, 200, "USD", "hourly") == "USD 100-200/hr"
    assert money(50000, None, "USD", "annual") == "USD 50,000/yr"
    assert money(None, None) == ""


def test_mojibake_mixed_with_real_unicode():
    assert text_of("every detailâ\u0080\u0094from “venue” selection, dafÃ¼r") == "every detail—from “venue” selection, dafür"


def test_descriptions_keep_their_lines_but_titles_dont():
    html_in = "<h2>About</h2><p>We build.</p><ul><li>React</li><li>CSS</li></ul>"
    assert text_of(html_in, True) == "About\nWe build.\nReact\nCSS"
    assert text_of(html_in) == "About We build. React CSS"


def test_employment_labels():
    from scout.sources import employment_of
    assert employment_of("Full Time") == "full_time"
    assert employment_of("Contractor") == "contract"
    assert employment_of("", "IT Support (NYSC)") == "internship"
    assert employment_of("Part-time") == "part_time"
    assert employment_of("", "Frontend Engineer") == ""


def test_hotnigerianjobs_feed():
    from scout.sources import parse_hotnigerianjobs
    feed = """<rss><channel><item><title>Sales Rep at Med-In Limited (3 Openings)</title><link>https://h/1</link>
    <description>Med-In is recruiting. The position is located in Lagos State. Interested candidates apply.</description>
    <pubDate>Tue, 03 Nov 2026 18:09:56 +0100</pubDate></item>
    <item><title>Talent Specialist at Moniepoint Incorporated</title><link>https://h/2</link>
    <description>Moniepoint is hiring. This is a Remote Role in Lagos State. Apply.</description></item>
    <item><title>Rep at Acme</title><link>https://h/3</link><description>Located in Abeokuta - Ogun.</description></item></channel></rss>"""
    a, b, c = list(parse_hotnigerianjobs(feed))
    assert (a.title, a.company, a.location) == ("Sales Rep", "Med-In Limited", "Lagos State")
    assert b.location == "Remote, Lagos State"
    assert c.location.startswith("Abeokuta")


def test_teamtailor_feed():
    from scout.sources import parse_teamtailor
    feed = """<rss xmlns:tt="https://teamtailor.com/locations"><channel><item><title>Engineer</title><description>&lt;p&gt;Build&lt;/p&gt;</description>
    <pubDate>Tue, 08 Sep 2026 14:04:18 +0100</pubDate><link>https://x/1</link><remoteStatus>hybrid</remoteStatus><guid>g1</guid>
    <tt:locations><tt:location><tt:city>Lagos</tt:city><tt:country>Nigeria</tt:country></tt:location></tt:locations></item></channel></rss>"""
    j = next(iter(parse_teamtailor(feed, "paystack")))
    assert (j.location, j.workplace, j.company, j.description) == ("Lagos, Nigeria", "hybrid", "Paystack", "Build")


def test_pagination_ends_cleanly_on_a_404_but_a_first_page_404_is_a_real_error(monkeypatch):
    import urllib.error
    import pytest
    from scout import sources

    def fake_get(url, retries=2):
        if url.endswith("/3") or url == "missing":
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        return "page"

    monkeypatch.setattr(sources, "get", fake_get)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    parse = lambda page: [sources.RawJob("t", "1", "T", "C", "u")]
    assert len(list(sources.pages_of("base", lambda n: f"base/{n}", parse, 8))) == 2
    with pytest.raises(urllib.error.HTTPError):
        list(sources.pages_of("missing", lambda n: "x", parse, 8))


def test_hotnigerianjobs_recruitment_titles_dont_invent_a_company():
    from scout.sources import parse_hotnigerianjobs
    feed = """<rss><channel><item><title>Premierhub Job Recruitment (3 Positions)</title><link>u</link><description>Located in Lagos State.</description></item></channel></rss>"""
    j = next(iter(parse_hotnigerianjobs(feed)))
    assert (j.title, j.company) == ("Premierhub Job Recruitment", "")
