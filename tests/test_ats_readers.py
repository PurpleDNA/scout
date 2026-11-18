import html
import json

from scout.sources import (oracle_wanted, parse_breezy, parse_oracle_job, parse_smartrecruiters_job, parse_successfactors_job,
                           parse_workable, parse_zoho, smartrecruiters_job_links, workday_nigeria_facets)


def test_workable_widget():
    data = {"name": "FIRST Exploration & Petroleum", "jobs": [{
        "title": "Cybersecurity Lead ", "shortcode": "0E1E5CCA5E", "employment_type": "Full-time", "telecommuting": False,
        "department": None, "function": "", "url": "https://apply.workable.com/j/0E1E5CCA5E", "published_on": "2026-05-12",
        "country": "Nigeria", "city": "Lagos", "locations": [{"country": "Nigeria", "city": "Lagos", "region": "Lagos"}],
        "description": "<p>Protect &amp; defend</p>"}]}
    j = next(iter(parse_workable(data, "firstepdcng")))
    assert (j.title, j.company, j.location, j.employment) == ("Cybersecurity Lead", "FIRST E&P", "Lagos, Lagos, Nigeria", "full_time")
    assert j.external_id == "firstepdcng:0E1E5CCA5E" and j.description == "Protect & defend" and j.posted_at.startswith("2026-05-12")


def zoho_page(jobs):
    return f'<input type="hidden" value="{html.escape(json.dumps(jobs))}" id="jobs">'


def test_zoho_reads_published_jobs_and_skips_cv_drops():
    jobs = [
        {"id": "1", "Posting_Title": "Full-Stack Developer", "City": "Lagos", "State": "Lagos", "Country": "Nigeria", "Publish": "True",
         "Remote_Job": "False", "Job_Type": "Full time", "Date_Opened": "2026-05-13", "Job_Description": "<p>Build</p>"},
        {"id": "2", "Posting_Title": "Internal Audit", "Publish": "False", "Country": "Nigeria"},
        {"id": "3", "Posting_Title": "Interested in other roles? Submit your CV to us!", "Publish": "True"},
        {"id": "4", "Posting_Title": "Analyst", "City": "Abuja", "Country": "Nigeria", "Publish": "True", "Remote_Job": "True",
         "Date_Opened": "None"},
    ]
    got = list(parse_zoho(zoho_page(jobs), "polarisbanklimited"))
    assert [j.title for j in got] == ["Full-Stack Developer", "Analyst"]
    a, b = got
    assert (a.company, a.location, a.workplace, a.employment) == ("Polaris Bank", "Lagos, Nigeria", None, "full_time")
    assert a.url == "https://polarisbanklimited.zohorecruit.com/jobs/Careers/1" and a.description == "Build"
    assert b.workplace == "remote" and b.posted_at is None


def test_zoho_page_without_job_data_is_an_error():
    try:
        list(parse_zoho("<html></html>", "acme"))
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_workday_prefers_the_country_facet():
    facets = [{"facetParameter": "locationMainGroup", "values": [
        {"facetParameter": "locationCountry", "descriptor": "Country", "values": [
            {"descriptor": "Ghana", "id": "gh"}, {"descriptor": "Nigeria", "id": "ng"}]},
        {"facetParameter": "locations", "descriptor": "Location", "values": [{"descriptor": "NG - Lagos, Nigeria", "id": "lagos"}]}]}]
    assert workday_nigeria_facets(facets) == {"locationCountry": ["ng"]}


def test_workday_falls_back_to_city_facets_and_to_nothing():
    city_only = [{"facetParameter": "locations", "values": [{"descriptor": "Lagos, Nigeria", "id": "l"}, {"descriptor": "Accra", "id": "a"}]}]
    assert workday_nigeria_facets(city_only) == {"locations": ["l"]}
    assert workday_nigeria_facets([{"facetParameter": "locations", "values": [{"descriptor": "Accra", "id": "a"}]}]) == {}


SF_PAGE = '''<html lang="en-GB"><span itemprop="title" class="x">Shift Operator </span>
<div class="joblayouttoken"><div class="inner"><div class="row"><div class="col">
<span class="joblayouttoken-label" role="heading">Job Location: </span> <span lang="en-GB" class="r"> </span></div></div></div></div>
<div class="joblayouttoken"><div class="inner"><div class="row"><div class="col">
<span class="joblayouttoken-label" role="heading">Posting Start Date: </span> <span lang="en-GB" class="r">03/11/2026 </span></div></div></div></div>
<div class="joblayouttoken"><div class="inner"><div class="row"><div class="col">
<span lang="en-GB" itemprop="description" class="r"><div><p>Run the plant.</p></div></span>
</div>
</div>
</div>
</div>'''


def test_successfactors_job_page():
    j = parse_successfactors_job(SF_PAGE, "https://careers.nlng.com/job/Katirje-Shift-Operator-RI/1374488955/", "careers.nlng.com")
    assert (j.title, j.company, j.location) == ("Shift Operator", "Nigeria LNG", "Nigeria")  # blank location takes the site default
    assert j.posted_at.startswith("2026-11-03") and j.description == "Run the plant." and j.external_id == "careers.nlng.com:1374488955"


def test_successfactors_closed_job_page_is_skipped():
    assert parse_successfactors_job("<title>Job Details</title>", "https://x/job/a/1/", "x") is None


def test_breezy_listing():
    jobs = [{"id": "98323abf2296", "name": "Backend Engineer ", "url": "https://cowrywise.breezy.hr/p/98323abf2296-backend-engineer",
             "published_date": "2026-11-02T14:37:22.684Z", "type": {"id": "fullTime", "name": "Full-Time"}, "department": "Engineering",
             "salary": "", "company": {"name": "Cowrywise"},
             "locations": [{"country": {"name": "Nigeria", "id": "NG"}, "city": "Lagos", "is_remote": False, "name": "Lagos"},
                           {"country": {"name": "Nigeria", "id": "NG"}, "is_remote": True, "name": "Remote, Nigeria"}]}]
    job, page = next(iter(parse_breezy(jobs, "cowrywise")))
    assert (job.title, job.company, job.location, job.workplace) == ("Backend Engineer", "Cowrywise", "Lagos, Nigeria; Remote, Nigeria", "remote")
    assert job.external_id == "cowrywise:98323abf2296"
    assert job.employment == "full_time" and job.tags == ["Engineering"] and page.endswith("-backend-engineer")


SR_GROUPS = (
    '<section data-qty="1" class="openings-section opening js-group"><header><h3 class="opening-title title">Accra, Ghana</h3></header>'
    '<a href="https://jobs.smartrecruiters.com/Deloitte6/744000000000001-tax-manager" class="details">Tax</a></section>'
    '<section data-qty="1" class="openings-section opening js-group"><header><h3 class="opening-title title">Lagos , Nigeria</h3></header>'
    '<a href="https://jobs.smartrecruiters.com/Deloitte6/744000000000002-cyber-academy" class="details">Cyber</a></section>')


def test_smartrecruiters_reads_only_nigerian_and_remote_groups():
    assert smartrecruiters_job_links(SR_GROUPS) == [
        ("Lagos , Nigeria", "https://jobs.smartrecruiters.com/Deloitte6/744000000000002-cyber-academy", "744000000000002")]


def test_smartrecruiters_job_page():
    page = ('<h1 class="job-title" itemprop="title">Deloitte Cyber Academy</h1><meta itemprop="addressCountry" content="Nigeria">'
            '<meta itemprop="addressLocality" content="Lagos"><meta itemprop="addressRegion">'
            '<li class="job-detail" itemprop="employmentType">Full-time</li><meta itemprop="datePosted" content="2026-11-13T14:28:15.148Z">'
            '<div itemprop="description"><section class="job-section"><p>Train as a cyber analyst.</p></section></div>'
            '<section class="job-section"><div class="video-disclaimer">Video</div></section>')
    j = parse_smartrecruiters_job(page, "https://jobs.smartrecruiters.com/Deloitte6/744000000000002-cyber-academy", "744000000000002",
                                  "Deloitte (West Africa)", "Lagos , Nigeria")
    assert (j.title, j.location, j.employment, j.workplace) == ("Deloitte Cyber Academy", "Lagos, Nigeria", "full_time", None)
    assert j.description == "Train as a cyber analyst." and j.posted_at.startswith("2026-11-13")


def test_oracle_keeps_nigerian_and_remote_requisitions():
    assert oracle_wanted({"PrimaryLocationCountry": "NG"})
    assert oracle_wanted({"PrimaryLocationCountry": "ZA", "secondaryLocations": [{"Name": "Ikoyi, Lagos, Nigeria"}]})
    assert oracle_wanted({"PrimaryLocationCountry": "KE", "WorkplaceType": "Remote"})
    assert not oracle_wanted({"PrimaryLocationCountry": "ZA", "WorkplaceType": "Hybrid", "secondaryLocations": []})


def test_oracle_job_splits_department_from_title():
    req = {"Id": "7502", "Title": "Developer - Software Development.Information Technology", "PrimaryLocation": "Ikoyi, Lagos, Nigeria",
           "WorkplaceType": "Hybrid", "PostedDate": "2026-11-09"}
    detail = {"Title": req["Title"], "ExternalDescriptionStr": "<div><b>Division:</b> IT</div>", "ExternalPostedStartDate": "2026-11-09T11:00:00+00:00",
              "JobSchedule": "Full time", "PrimaryLocation": "Ikoyi, Lagos, South West Region, Nigeria"}
    j = parse_oracle_job(detail, req, "MTN", "ehle.fa.em2.oraclecloud.com", "CX_1")
    assert (j.title, j.tags, j.workplace, j.employment) == ("Developer - Software Development", ["Information Technology"], "hybrid", "full_time")
    assert j.url == "https://ehle.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/7502"
    assert j.external_id == "ehle:7502" and "Division" in j.description
