"""Connectors. Each yields RawJob; a failing source never takes the others down.

Only public feeds, public ATS board APIs and listing pages that robots.txt permits, at a
polite rate, with an honest User-Agent. A source that refuses that client stays out:
Indeed answers 403, and we don't disguise ourselves to get past it.
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Iterable

USER_AGENT = "scout/0.1 (personal job search; low volume)"


@dataclass
class RawJob:
    source: str
    external_id: str
    title: str
    company: str
    url: str
    location: str = ""
    workplace: str | None = None
    restrictions: list[str] | None = None
    timezones: list[float] | None = None
    remote_board: bool = False
    description: str = ""
    tags: list[str] = field(default_factory=list)
    salary: str = ""
    posted_at: str | None = None  # ISO 8601
    employment: str = ""  # full_time | part_time | contract | internship, or "" when unstated


def pages_of(base: str, page_url, parse, max_pages: int) -> Iterable[RawJob]:
    """Walk numbered listing pages. Running out of pages shows up as an empty page or a 404, and neither is an error."""
    for n in range(1, max_pages + 1):
        try:
            page = get(base if n == 1 else page_url(n))
        except urllib.error.HTTPError as e:
            if e.code == 404 and n > 1:
                return
            raise
        jobs = list(parse(page))
        if not jobs:
            return
        yield from jobs
        time.sleep(2)


def get(url: str, retries: int = 2) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == retries:
                raise
            time.sleep(int(e.headers.get("Retry-After") or 20))
        except urllib.error.URLError:  # Usually a brief DNS or network drop.
            if attempt == retries:
                raise
            time.sleep(5)
    raise RuntimeError("unreachable")


def _fix_mojibake(s: str) -> str:
    """Repair UTF-8 runs that a feed decoded as Latin-1 (“â\x80\x94” instead of “—”), leaving other text alone."""
    def repair(m: re.Match) -> str:
        try:
            return m[0].encode("latin-1").decode("utf-8")
        except UnicodeError:
            return m[0]
    return re.sub(r"[\xc2-\xf4][\x80-\xbf]+", repair, s)


def text_of(markup: str | None, keep_lines: bool = False) -> str:
    """HTML to plain text. Some feeds double-escape, hence the first unescape.

    keep_lines holds on to paragraph and list breaks, so descriptions stay readable.
    """
    s = html.unescape(markup or "")
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s, flags=re.S | re.I)
    if keep_lines:
        s = re.sub(r"<(br|/p|/li|/h[1-6]|/div|/ul|/ol)\b[^>]*>", "\x00", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _fix_mojibake(html.unescape(s))
    if not keep_lines:
        return " ".join(s.split())
    lines = (" ".join(line.split()) for line in s.replace("\n", " ").split("\x00"))
    return "\n".join(line for line in lines if line)


def employment_of(*texts: str | None) -> str:
    """Normalise a feed's job type. Reads labels and titles, not prose, which mentions these words too freely."""
    t = " ".join(x for x in texts if x).lower()
    if re.search(r"\bintern(ship)?\b|\bnysc\b|\bgraduate trainee\b|\btrainee\b", t):
        return "internship"
    if re.search(r"part[-_ ]?time", t):
        return "part_time"
    if re.search(r"\b(contract(or)?|freelance|temporary|fixed[- ]term)\b", t):
        return "contract"
    if re.search(r"full[-_ ]?time|permanent", t):
        return "full_time"
    return ""


def iso(value) -> str | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc).isoformat()
        if re.match(r"\d{4}-\d{2}-\d{2}", str(value)):
            d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).isoformat()
        return parsedate_to_datetime(str(value)).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def relative_date(text: str) -> str | None:
    """'Posted 4 weeks ago' / 'today' / '03 November' to ISO."""
    now = datetime.now(timezone.utc)
    t = text.lower()
    if "today" in t or "hour" in t or "just now" in t:
        return now.isoformat()
    if "yesterday" in t:
        return (now - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\s*(day|week|month)", t)
    if m:
        days = int(m[1]) * {"day": 1, "week": 7, "month": 30}[m[2]]
        return (now - timedelta(days=days)).isoformat()
    m = re.search(r"(\d{1,2})\s+([a-z]+)", t)
    if m:
        try:
            d = datetime.strptime(f"{m[1]} {m[2]} {now.year}", "%d %B %Y").replace(tzinfo=timezone.utc)
            return (d if d <= now + timedelta(days=1) else d.replace(year=now.year - 1)).isoformat()
        except ValueError:
            return None
    return None


def money(lo, hi, currency="USD", period: str | None = None) -> str:
    if not lo and not hi:
        return ""
    per = {"hourly": "/hr", "hour": "/hr", "daily": "/day", "weekly": "/wk", "monthly": "/mo", "month": "/mo",
           "annual": "/yr", "yearly": "/yr", "year": "/yr"}.get((period or "").lower(), "")
    return f"{currency} {'-'.join(f'{int(v):,}' for v in (lo, hi) if v)}{per}"


# ---------- remote boards ----------

def himalayas() -> Iterable[RawJob]:
    # country=Nigeria asks Himalayas itself which jobs a Nigerian resident can take.
    seen_page = 1
    while seen_page <= 120:
        d = json.loads(get(f"https://himalayas.app/jobs/api/search?country=Nigeria&page={seen_page}&limit=20"))
        jobs = d.get("jobs") or []
        if not jobs:
            return
        for j in jobs:
            yield RawJob("himalayas", j.get("guid") or j["applicationLink"], j["title"], j["companyName"],
                         j.get("applicationLink") or j.get("guid"), restrictions=j.get("locationRestrictions") or [],
                         timezones=j.get("timezoneRestrictions") or [], remote_board=True,
                         description=text_of(j.get("description"), True), tags=j.get("categories") or [],
                         employment=employment_of(j.get("employmentType"), j["title"]),
                         salary=money(j.get("minSalary"), j.get("maxSalary"), j.get("currency") or "USD", j.get("salaryPeriod")),
                         posted_at=iso(j.get("pubDate")))
        seen_page += 1
        time.sleep(1.0)


def remotive() -> Iterable[RawJob]:
    for j in json.loads(get("https://remotive.com/api/remote-jobs"))["jobs"]:
        yield RawJob("remotive", str(j["id"]), j["title"], j["company_name"], j["url"],
                     location=j.get("candidate_required_location") or "", remote_board=True,
                     description=text_of(j.get("description"), True), tags=j.get("tags") or [],
                     salary=j.get("salary") or "", posted_at=iso(j.get("publication_date")),
                     employment=employment_of(j.get("job_type"), j["title"]))


def remoteok() -> Iterable[RawJob]:
    for j in json.loads(get("https://remoteok.com/api"))[1:]:
        yield RawJob("remoteok", str(j["id"]), text_of(j.get("position")), text_of(j.get("company")), j["url"],
                     location=text_of(j.get("location")), remote_board=True, description=text_of(j.get("description"), True),
                     tags=j.get("tags") or [], salary=money(j.get("salary_min"), j.get("salary_max")),
                     posted_at=iso(j.get("epoch")), employment=employment_of(j.get("position")))


def jobicy() -> Iterable[RawJob]:
    seen = set()
    for geo in ("anywhere", "emea"):
        for j in json.loads(get(f"https://jobicy.com/api/v2/remote-jobs?count=100&geo={geo}")).get("jobs", []):
            if j["id"] in seen:
                continue
            seen.add(j["id"])
            ind = j.get("jobIndustry")
            yield RawJob("jobicy", str(j["id"]), text_of(j["jobTitle"]), text_of(j["companyName"]), j["url"],
                         location=j.get("jobGeo") or "", remote_board=True,
                         description=text_of(j.get("jobDescription") or j.get("jobExcerpt"), True),
                         tags=ind if isinstance(ind, list) else [], posted_at=iso(j.get("pubDate")),
                         employment=employment_of(str(j.get("jobType") or ""), j["jobTitle"]))
        time.sleep(2)


def weworkremotely() -> Iterable[RawJob]:
    for it in ET.fromstring(get("https://weworkremotely.com/remote-jobs.rss")).iter("item"):
        company, _, title = (it.findtext("title") or "").partition(":")
        link = it.findtext("link") or it.findtext("guid") or ""
        skills = [s.strip() for s in re.split(r",|\band\b", it.findtext("skills") or "") if s.strip()]
        yield RawJob("weworkremotely", link, html.unescape(title.strip() or company), html.unescape(company.strip()), link,
                     location=it.findtext("region") or "", remote_board=True,
                     description=text_of(it.findtext("description"), True), tags=skills, posted_at=iso(it.findtext("pubDate")),
                     employment=employment_of(it.findtext("type"), title))


# ---------- company boards (public ATS APIs) ----------
# Remote-friendly or Lagos-hiring companies. Add any company by its slug on that ATS.
GREENHOUSE = ["moniepoint", "remote", "gitlab", "canonical", "grafanalabs", "elastic", "mozilla", "wikimedia", "vercel", "jumia"]
LEVER = ["tala", "kraken", "binance"]
ASHBY = ["andela", "sabi", "deel", "zapier", "posthog", "supabase", "lemfi"]


def _each_board(slugs: list[str], url: str) -> Iterable[tuple[str, object]]:
    """Fetch every company's board, reporting failures at the end so one bad board can't hide the others."""
    failed = []
    for slug in slugs:
        try:
            data = json.loads(get(url.format(slug)))
        except (OSError, ValueError) as e:
            failed.append(f"{slug}: {e}")
            continue
        yield slug, data
    if failed:
        raise RuntimeError("; ".join(failed))


def greenhouse() -> Iterable[RawJob]:
    for slug, data in _each_board(GREENHOUSE, "https://boards-api.greenhouse.io/v1/boards/{}/jobs?content=true"):
        for j in data["jobs"]:
            yield RawJob("greenhouse", f"{slug}:{j['id']}", j["title"], j.get("company_name") or slug, j["absolute_url"],
                         location=(j.get("location") or {}).get("name") or "", description=text_of(j.get("content"), True),
                         posted_at=iso(j.get("first_published") or j.get("updated_at")), employment=employment_of(j["title"]))


def lever() -> Iterable[RawJob]:
    for slug, data in _each_board(LEVER, "https://api.lever.co/v0/postings/{}?mode=json"):
        for j in data:
            cat = j.get("categories") or {}
            locations = cat.get("allLocations") or [cat.get("location") or ""]
            workplace = j.get("workplaceType")
            yield RawJob("lever", j["id"], j["text"], slug.capitalize(), j["hostedUrl"], location="; ".join(locations),
                         workplace=None if workplace == "unspecified" else workplace,
                         description=j.get("descriptionPlain") or text_of(j.get("description"), True),
                         tags=[t for t in (cat.get("team"), cat.get("department")) if t], posted_at=iso(j.get("createdAt")),
                         employment=employment_of(cat.get("commitment"), j["text"]))


def ashby() -> Iterable[RawJob]:
    for slug, data in _each_board(ASHBY, "https://api.ashbyhq.com/posting-api/job-board/{}"):
        for j in data["jobs"]:
            if not j.get("isListed", True):
                continue
            locations = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
            workplace = j.get("workplaceType") or ("Remote" if j.get("isRemote") else None)
            yield RawJob("ashby", j["id"], j["title"], slug.capitalize(), j.get("jobUrl") or j.get("applyUrl"),
                         location="; ".join(filter(None, locations)), workplace=workplace,
                         description=j.get("descriptionPlain") or text_of(j.get("descriptionHtml"), True),
                         tags=[t for t in (j.get("department"), j.get("team")) if t], posted_at=iso(j.get("publishedAt")),
                         employment=employment_of(j.get("employmentType"), j["title"]))


BAMBOOHR = {"chowdeck": "Chowdeck", "paga": "Paga", "yellowcard": "Yellow Card", "cellulant": "Cellulant", "grey": "Grey", "risevest": "Risevest",
            "lekoil": "Lekoil", "verodcapitalmanagement": "Verod Capital",
            "completefarmer": "Complete Farmer"}  # Ghanaian; listed on the careers page of its investor Ingressive Capital.
# Inferred from live boards (Paga on-site in Yaba, Chowdeck hybrid in Ikoyi, Yellow Card remote). BambooHR doesn't document this.
_BAMBOO_SETUP = {"0": "onsite", "1": "remote", "2": "hybrid"}


def bamboohr() -> Iterable[RawJob]:
    for slug, data in _each_board(list(BAMBOOHR), "https://{}.bamboohr.com/careers/list"):
        for j in data["result"]:
            place = j.get("location") or {}
            ats = j.get("atsLocation") or {}
            parts = [place.get("city"), place.get("state"), ats.get("city"), ats.get("state"), ats.get("country")]
            try:  # The list has no description or date, so each job costs one extra request.
                detail = json.loads(get(f"https://{slug}.bamboohr.com/careers/{j['id']}/detail"))["result"]["jobOpening"]
            except (OSError, ValueError, KeyError):
                detail = {}
            time.sleep(0.5)
            yield RawJob("bamboohr", f"{slug}:{j['id']}", j["jobOpeningName"].strip(), BAMBOOHR[slug],
                         detail.get("jobOpeningShareUrl") or f"https://{slug}.bamboohr.com/careers/{j['id']}",
                         location=", ".join(dict.fromkeys(p for p in parts if p)),
                         workplace="remote" if j.get("isRemote") else _BAMBOO_SETUP.get(str(j.get("locationType"))),
                         description=text_of(detail.get("description"), True), tags=[j.get("departmentLabel") or ""],
                         posted_at=iso(detail.get("datePosted")), employment=employment_of(j.get("employmentStatusLabel"), j["jobOpeningName"]))


TEAMTAILOR = ["bamboo", "paystack"]  # “bamboo” is the Nigerian investment company.
_TT = "{https://teamtailor.com/locations}"
_TT_SETUP = {"fully": "remote", "hybrid": "hybrid", "none": "onsite"}


def parse_teamtailor(feed: str, slug: str) -> Iterable[RawJob]:
    for it in ET.fromstring(feed).iter("item"):
        places = [", ".join(filter(None, (loc.findtext(f"{_TT}city"), loc.findtext(f"{_TT}country")))) for loc in it.iter(f"{_TT}location")]
        title = it.findtext("title") or ""
        yield RawJob("teamtailor", f"{slug}:{it.findtext('guid')}", title, slug.capitalize(), it.findtext("link") or "",
                     location="; ".join(filter(None, places)), workplace=_TT_SETUP.get(it.findtext("remoteStatus") or ""),
                     description=text_of(it.findtext("description"), True), tags=[it.findtext(f"{_TT}department") or ""],
                     posted_at=iso(it.findtext("pubDate")), employment=employment_of(title))


def teamtailor() -> Iterable[RawJob]:
    for slug in TEAMTAILOR:
        yield from parse_teamtailor(get(f"https://{slug}.teamtailor.com/jobs.rss"), slug)


# The hiring systems Nigerian corporates use, found by `scout companies crawl`.
WORKABLE = {"firstepdcng": "FIRST E&P", "dangote": "Dangote"}


def parse_workable(data: dict, slug: str) -> Iterable[RawJob]:
    company = WORKABLE.get(slug) or data.get("name") or slug
    for j in data.get("jobs", []):
        places = [", ".join(filter(None, (p.get("city"), p.get("region"), p.get("country")))) for p in j.get("locations") or []]
        place = "; ".join(dict.fromkeys(p for p in places if p)) or ", ".join(filter(None, (j.get("city"), j.get("country"))))
        yield RawJob("workable", f"{slug}:{j['shortcode']}", j["title"].strip(), company, j.get("url") or j.get("shortlink"),
                     location=place, workplace="remote" if j.get("telecommuting") else None,
                     description=text_of(j.get("description"), True), tags=[t for t in (j.get("department"), j.get("function")) if t],
                     posted_at=iso(j.get("published_on") or j.get("created_at")),
                     employment=employment_of(j.get("employment_type"), j["title"]))


def workable() -> Iterable[RawJob]:
    for slug, data in _each_board(list(WORKABLE), "https://apply.workable.com/api/v1/widget/accounts/{}?details=true"):
        yield from parse_workable(data, slug)


ZOHO = {"polarisbanklimited": "Polaris Bank", "babbangona": "Babban Gona", "sfhnigeria": "Society for Family Health", "hamulegal": "HAMU Legal"}
_ZOHO_JOBS = re.compile(r'<input type="hidden" value="([^"]*)" id="jobs"')
_GENERAL_APPLICATION = re.compile(r"submit your (cv|resume)|talent (pool|community)|general application|other roles|"
                                  r"speculative application|initiativbewerbung", re.I)


def parse_zoho(page: str, slug: str) -> Iterable[RawJob]:
    """Zoho's careers page carries its openings as JSON in a hidden input."""
    m = _ZOHO_JOBS.search(page)
    if not m:
        raise ValueError(f"{slug}: no job data on the careers page")
    for j in json.loads(html.unescape(m[1])):
        title = (j.get("Posting_Title") or j.get("Job_Opening_Name") or "").strip()
        if str(j.get("Publish")) != "True" or not title or _GENERAL_APPLICATION.search(title):
            continue
        place = ", ".join(dict.fromkeys(x for x in (j.get("City"), j.get("State"), j.get("Country")) if x and x != "None"))
        opened = j.get("Date_Opened")
        yield RawJob("zoho", f"{slug}:{j['id']}", title, ZOHO.get(slug, slug), f"https://{slug}.zohorecruit.com/jobs/Careers/{j['id']}",
                     location=place, workplace="remote" if str(j.get("Remote_Job")) == "True" else None,
                     description=text_of(j.get("Job_Description"), True), tags=[j.get("Industry") or ""],
                     posted_at=iso(opened) if opened and opened != "None" else None,
                     employment=employment_of(j.get("Job_Type"), title))


def zoho() -> Iterable[RawJob]:
    failed = []
    for slug in ZOHO:
        try:
            yield from parse_zoho(get(f"https://{slug}.zohorecruit.com/jobs/Careers"), slug)
        except (OSError, ValueError) as e:
            failed.append(f"{slug}: {e}")
        time.sleep(1)
    if failed:
        raise RuntimeError("; ".join(failed))


# Workday boards are global, so only their Nigerian openings are read.
WORKDAY = {
    "Shell": "shell.wd3.myworkdayjobs.com/ShellCareers",
    "Visa": "visa.wd5.myworkdayjobs.com/Visa",
    "Mastercard": "mastercard.wd1.myworkdayjobs.com/CorporateCareers",
    "Unilever": "unilever.wd3.myworkdayjobs.com/Unilever_Experienced_Professionals",
    "Absa": "absa.wd3.myworkdayjobs.com/ABSAcareersite",
    "Rand Merchant Bank": "firstrand.wd3.myworkdayjobs.com/FRB",
    "NCR": "ncr.wd1.myworkdayjobs.com/ext_apac",
}


def post_json(url: str, body: dict):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"User-Agent": USER_AGENT, "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def workday_nigeria_facets(facets: list[dict]) -> dict[str, list[str]]:
    """Facet values naming Nigeria. Country facets win over city ones, since the country already covers its cities."""
    found: dict[str, list[str]] = {}

    def walk(param: str | None, values: list[dict]):
        for v in values:
            if "values" in v:
                walk(v.get("facetParameter") or param, v["values"])
            elif param and "nigeria" in str(v.get("descriptor")).lower():
                found.setdefault(param, []).append(v["id"])
    for f in facets:
        walk(f.get("facetParameter"), f.get("values") or [])
    for country in ("locationCountry", "Location_Country"):
        if country in found:
            return {country: found[country]}
    return found


def workday() -> Iterable[RawJob]:
    failed = []
    for company, board in WORKDAY.items():
        host, site = board.split("/", 1)
        api = f"https://{host}/wday/cxs/{host.split('.')[0]}/{site}"
        try:
            facets = workday_nigeria_facets(post_json(f"{api}/jobs", {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}).get("facets", []))
            if not facets:
                continue  # Nothing open in Nigeria right now.
            offset, total = 0, 1
            while offset < total:
                page = post_json(f"{api}/jobs", {"appliedFacets": facets, "limit": 20, "offset": offset, "searchText": ""})
                total = page.get("total") or 0
                for j in page.get("jobPostings", []):
                    info = json.loads(get(api + j["externalPath"])).get("jobPostingInfo", {})
                    time.sleep(0.5)
                    yield RawJob("workday", f"{host.split('.')[0]}:{j['externalPath'].rsplit('_', 1)[-1]}", j["title"], company,
                                 info.get("externalUrl") or f"https://{host}/{site}{j['externalPath']}",
                                 location=info.get("location") or j.get("locationsText") or "",
                                 workplace=(info.get("remoteType") or "").lower() or None,
                                 description=text_of(info.get("jobDescription"), True),
                                 posted_at=iso(info.get("startDate")) or relative_date(j.get("postedOn") or ""),
                                 employment=employment_of(info.get("timeType"), j["title"]))
                offset += 20
        except (OSError, ValueError, KeyError) as e:
            failed.append(f"{company}: {e!r}"[:200])
    if failed:
        raise RuntimeError("; ".join(failed))


# SuccessFactors career sites (Career Site Builder). Their RSS feed sits under /services/, which their robots.txt
# disallows, so jobs come from the sitemap and each job's own page. Default location for sites that leave it blank.
SUCCESSFACTORS = {"careers.nlng.com": ("Nigeria LNG", "Nigeria"), "careers.jubailiagrotecgroup.com": ("Jubaili Agrotec", "")}


def _sf_field(page: str, label: str) -> str:
    m = re.search(rf'joblayouttoken-label"[^>]*>\s*{label}:?\s*</span>\s*<span[^>]*>(.*?)</span>', page, re.S | re.I)
    return text_of(m[1]) if m else ""


def parse_successfactors_job(page: str, url: str, host: str) -> RawJob | None:
    company, default_place = SUCCESSFACTORS.get(host, (host, ""))
    title = re.search(r'itemprop="title"[^>]*>(.*?)</span>', page, re.S)
    if not title:
        return None
    desc = re.search(r'itemprop="description"[^>]*>(.*?)</span>\s*</div>\s*</div>\s*</div>\s*</div>', page, re.S)
    date = _sf_field(page, r"(?:Posting Start Date|Date Posted|Posted Date|Date)")
    posted = None
    if m := re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", date):
        day, month = (m[1], m[2]) if 'lang="en-US"' not in page[:3000] else (m[2], m[1])
        posted = iso(f"{m[3]}-{int(month):02d}-{int(day):02d}")
    job_id = re.search(r"/(\d{6,})/?$", url)
    return RawJob("successfactors", f"{host}:{job_id[1] if job_id else url}", text_of(title[1]), company, url,
                  location=_sf_field(page, r"(?:Job Location|Location)") or default_place,
                  description=text_of(desc[1] if desc else "", True), posted_at=posted, employment=employment_of(text_of(title[1])))


def successfactors() -> Iterable[RawJob]:
    failed = []
    for host in SUCCESSFACTORS:
        try:
            urls = re.findall(r"<loc>([^<]+/job/[^<]+)</loc>", get(f"https://{host}/sitemap.xml"))
            for url in urls:
                time.sleep(1)
                if job := parse_successfactors_job(get(html.unescape(url)), html.unescape(url), host):
                    yield job
        except (OSError, ValueError) as e:
            failed.append(f"{host}: {e!r}"[:200])
    if failed:
        raise RuntimeError("; ".join(failed))


# Breezy's /json lists a board's openings without descriptions, so each job's own page supplies those.
BREEZY = {"cowrywise": "Cowrywise", "oando": "Oando"}
_BREEZY_DESC = re.compile(r'<div class="description">(.*?)</div><div class="apply-container">', re.S)


def _breezy_place(p: dict) -> str:
    name, country = p.get("name") or "", (p.get("country") or {}).get("name") or ""
    return name if country in name else ", ".join(filter(None, (name, country)))


def parse_breezy(jobs: list[dict], slug: str) -> Iterable[tuple[RawJob, str]]:
    """Each job, with the page its description comes from."""
    for j in jobs:
        places = j.get("locations") or [j.get("location") or {}]
        place = "; ".join(dict.fromkeys(filter(None, map(_breezy_place, places))))
        remote = any(p.get("is_remote") for p in places)
        job = RawJob("breezy", f"{slug}:{j['id']}", j["name"].strip(), BREEZY.get(slug) or (j.get("company") or {}).get("name") or slug,
                     j["url"], location=place, workplace="remote" if remote else None, tags=[j["department"]] if j.get("department") else [],
                     salary=j.get("salary") or "", posted_at=iso(j.get("published_date")),
                     employment=employment_of((j.get("type") or {}).get("name"), j["name"]))
        yield job, j["url"]


def breezy() -> Iterable[RawJob]:
    for slug, data in _each_board(list(BREEZY), "https://{}.breezy.hr/json"):
        for job, page_url in parse_breezy(data, slug):
            try:
                if m := _BREEZY_DESC.search(get(page_url)):
                    job.description = text_of(m[1], True)
            except OSError:
                pass  # The listing alone is still worth keeping.
            time.sleep(1)
            yield job


# SmartRecruiters' Posting API sits on api.smartrecruiters.com, whose robots.txt disallows everyone but LinkedIn's bot,
# so jobs come from the public careers page instead. It groups openings by location; only Nigerian and remote groups are read.
SMARTRECRUITERS = {"Deloitte6": "Deloitte (West Africa)"}
_SR_GROUP = re.compile(r'<section[^>]*class="openings-section[^"]*"[^>]*>(.*?)</section>', re.S)
_SR_GROUP_TITLE = re.compile(r'class="opening-title[^"]*"[^>]*>(.*?)</h3>', re.S)
_SR_JOB_LINK = re.compile(r'<a href="(https://jobs\.smartrecruiters\.com/[^"]+/(\d+)-[^"]*)"', re.S)


def smartrecruiters_job_links(page: str) -> list[tuple[str, str, str]]:
    """(location heading, job url, job id) for the groups worth reading."""
    out = []
    for group in _SR_GROUP.findall(page):
        heading = text_of(m[1]).lstrip(":").strip() if (m := _SR_GROUP_TITLE.search(group)) else ""
        if re.search(r"nigeria|lagos|remote", heading, re.I):
            out += [(heading, url, job_id) for url, job_id in _SR_JOB_LINK.findall(group)]
    return out


def _itemprop(page: str, name: str) -> str:
    m = re.search(rf'<meta itemprop="{name}" content="([^"]*)"', page)
    return html.unescape(m[1]).strip() if m else ""


def parse_smartrecruiters_job(page: str, url: str, job_id: str, company: str, heading: str) -> RawJob | None:
    title = re.search(r'itemprop="title"[^>]*>(.*?)</h1>', page, re.S)
    if not title:
        return None
    start = page.find('itemprop="description"')
    desc = page[start:page.find("</section></div>", start)] if start >= 0 else ""
    place = ", ".join(x for x in (_itemprop(page, "addressLocality"), _itemprop(page, "addressRegion"), _itemprop(page, "addressCountry")) if x)
    kind = re.search(r'itemprop="employmentType"[^>]*>(.*?)<', page)
    remote = "remote" in heading.lower() or bool(re.search(r'class="job-detail[^"]*"[^>]*>\s*Remote', page))
    return RawJob("smartrecruiters", f"{company}:{job_id}", text_of(title[1]), company, url, location=place or heading,
                  workplace="remote" if remote else None, description=text_of(desc.split(">", 1)[-1], True),
                  posted_at=iso(_itemprop(page, "datePosted")), employment=employment_of(kind[1] if kind else "", text_of(title[1])))


def smartrecruiters() -> Iterable[RawJob]:
    failed = []
    for slug, company in SMARTRECRUITERS.items():
        try:
            pages = [get(f"https://careers.smartrecruiters.com/{slug}")]
            for n in range(1, 30):  # Further groups load in pages until one comes back empty.
                time.sleep(1)
                more = get(f"https://careers.smartrecruiters.com/{slug}/api/groups?page={n}")
                if not more.strip():
                    break
                pages.append(more)
            seen = set()
            for heading, url, job_id in (link for page in pages for link in smartrecruiters_job_links(page)):
                if job_id in seen:
                    continue
                seen.add(job_id)
                time.sleep(1)
                if job := parse_smartrecruiters_job(get(url), url, job_id, company, heading):
                    yield job
        except OSError as e:
            failed.append(f"{slug}: {e!r}"[:200])
    if failed:
        raise RuntimeError("; ".join(failed))


# Oracle Recruiting Cloud (Candidate Experience) boards, as host/site. Boards are often group-wide, so only
# Nigerian and remote openings are read.
ORACLE = {
    "MTN": "ehle.fa.em2.oraclecloud.com/CX_1",
    "Ecobank": "fa-emqf-saasfaprod1.fa.ocs.oraclecloud.com/CX_1004",
}
_ORACLE_LIST = ("https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
                "&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber={site},limit=25,offset={offset},sortBy=POSTING_DATES_DESC")
_ORACLE_JOB = ("https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
               "&finder=ById;Id=%22{id}%22,siteNumber={site}")


def oracle_wanted(req: dict) -> bool:
    places = [req.get("PrimaryLocation") or ""] + [s.get("Name") or "" for s in req.get("secondaryLocations") or []]
    return (req.get("PrimaryLocationCountry") == "NG" or any("nigeria" in p.lower() for p in places)
            or (req.get("WorkplaceType") or "").lower() == "remote")


# Some boards append the department after a full stop: "Developer - Software Development.Information Technology".
_ORACLE_DEPARTMENT = re.compile(r"^(.*\S)\.([A-Z][A-Za-z&,' ]+)$")


def parse_oracle_job(detail: dict, req: dict, company: str, host: str, site: str) -> RawJob:
    parts = (detail.get(k) for k in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr"))
    title, department = (detail.get("Title") or req["Title"]).strip(), ""
    if m := _ORACLE_DEPARTMENT.match(title):
        title, department = m[1].strip(), m[2].strip()
    return RawJob("oracle", f"{host.split('.')[0]}:{req['Id']}", title, company,
                  f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{req['Id']}",
                  location=detail.get("PrimaryLocation") or req.get("PrimaryLocation") or "",
                  workplace=(req.get("WorkplaceType") or "").lower().replace("on-site", "onsite") or None,
                  description=text_of("\n".join(p for p in parts if p), True) or req.get("ShortDescriptionStr") or "",
                  tags=[department] if department else [],
                  posted_at=iso(detail.get("ExternalPostedStartDate") or req.get("PostedDate")),
                  employment=employment_of(detail.get("JobSchedule"), detail.get("RequisitionType"), title))


def oracle() -> Iterable[RawJob]:
    failed = []
    for company, board in ORACLE.items():
        host, site = board.split("/", 1)
        try:
            offset, total = 0, 1
            while offset < total:
                search = json.loads(get(_ORACLE_LIST.format(host=host, site=site, offset=offset)))["items"][0]
                total = search.get("TotalJobsCount") or 0
                for req in filter(oracle_wanted, search.get("requisitionList") or []):
                    items = json.loads(get(_ORACLE_JOB.format(host=host, id=req["Id"], site=site))).get("items") or [{}]
                    time.sleep(0.5)
                    yield parse_oracle_job(items[0], req, company, host, site)
                offset += 25
                time.sleep(1)
        except (OSError, ValueError, KeyError, IndexError) as e:
            failed.append(f"{company}: {e!r}"[:200])
    if failed:
        raise RuntimeError("; ".join(failed))


# Recruitee's public offers API. Boards can be group-wide (Julius Berger's is run from Germany), so only Nigerian
# and remote offers are read.
RECRUITEE = {"juliusbergerinternationalgmbh": "Julius Berger"}


def parse_recruitee(data: dict, slug: str) -> Iterable[RawJob]:
    for o in data.get("offers") or []:
        places = o.get("locations") or [o]
        if not (o.get("remote") or any(p.get("country_code") == "NG" for p in places)):
            continue
        title = (o.get("title") or "").strip()
        if not title or _GENERAL_APPLICATION.search(title):
            continue
        setup = "remote" if o.get("remote") else "hybrid" if o.get("hybrid") else "onsite" if o.get("on_site") else None
        yield RawJob("recruitee", f"{slug}:{o['id']}", title, RECRUITEE.get(slug) or o.get("company_name") or slug,
                     o.get("careers_url") or f"https://{slug}.recruitee.com/o/{o['slug']}", location=o.get("location") or "",
                     workplace=setup, description=text_of("\n".join(filter(None, (o.get("description"), o.get("requirements")))), True),
                     tags=[o["department"]] if o.get("department") else [], posted_at=iso(o.get("published_at")),
                     employment=employment_of((o.get("employment_type_code") or "").replace("_", " "), title))


def recruitee() -> Iterable[RawJob]:
    for slug, data in _each_board(list(RECRUITEE), "https://{}.recruitee.com/api/offers/"):
        yield from parse_recruitee(data, slug)


# Freshteam careers portals. Their robots.txt allows only /jobs, which lists the openings; each job page carries a
# schema.org JobPosting with the full description.
FRESHTEAM = {"emergencyresponseafrica": "Emergency Response Africa"}
_FRESHTEAM_LINK = re.compile(r'href="(/jobs/([A-Za-z0-9_-]+)/[^"]*)"')
_LD_JSON = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


def job_posting_ld(page: str) -> dict:
    """The page's schema.org JobPosting, or {}."""
    for block in _LD_JSON.findall(page):
        try:
            data = json.loads(block, strict=False)
        except ValueError:
            continue
        for item in data.get("@graph", [data]) if isinstance(data, dict) else data:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return {}


def parse_freshteam_job(page: str, url: str, slug: str, job_id: str) -> RawJob | None:
    ld = job_posting_ld(page)
    if not ld.get("title"):
        return None
    address = ((ld.get("jobLocation") or {}).get("address")) or {}
    place = ", ".join(dict.fromkeys(x for x in (address.get("addressLocality"), address.get("addressRegion"), address.get("addressCountry")) if x))
    title = html.unescape(ld["title"]).strip()
    return RawJob("freshteam", f"{slug}:{job_id}", title, FRESHTEAM.get(slug) or (ld.get("hiringOrganization") or {}).get("name") or slug,
                  url, location=place, workplace="remote" if str(ld.get("remote")).lower() == "true" else None,
                  description=text_of(html.unescape(ld.get("description") or ""), True), posted_at=iso(ld.get("datePosted")),
                  employment=employment_of(str(ld.get("employmentType") or "").replace("_", " "), title))


def freshteam() -> Iterable[RawJob]:
    failed = []
    for slug in FRESHTEAM:
        try:
            links = dict((job_id, path) for path, job_id in _FRESHTEAM_LINK.findall(get(f"https://{slug}.freshteam.com/jobs")))
            for job_id, path in links.items():
                time.sleep(1)
                url = f"https://{slug}.freshteam.com{path}"
                if job := parse_freshteam_job(get(url), url, slug, job_id):
                    yield job
        except OSError as e:
            failed.append(f"{slug}: {e!r}"[:200])
    if failed:
        raise RuntimeError("; ".join(failed))


# ---------- Lagos boards (HTML listing pages and feeds; excerpts only) ----------
# Your field's listings first, then your city's general feed. scout.fields sets these from profile.json.
JOBBERMAN_PAGES = ["/jobs/software-data/lagos", "/jobs/software-data", "/jobs/lagos"]
MYJOBMAG_PAGES = ["/jobs-by-field/information-technology", "/jobs-location/lagos"]
REMOTE_BOARDS = {"himalayas", "remotive", "remoteok", "jobicy", "weworkremotely"}  # Only remote jobs, almost all abroad.


def parse_jobberman(page: str) -> Iterable[RawJob]:
    for art in re.findall(r"<article\b.*?</article>", page, re.S):
        link = re.search(r'href="(https://www\.jobberman\.com/listings/[^"]+)"[^>]*data-cy="listing-title-link"[^>]*title="([^"]*)"', art, re.S)
        if not link:
            continue
        clean = re.sub(r"<svg\b.*?</svg>", "", art, flags=re.S)
        company = re.search(r"</h3>\s*<p[^>]*>(.*?)</p>", clean, re.S)
        desc = re.search(r'data-cy="description-tile"[^>]*>(.*?)</div>', clean, re.S)
        tail = clean[desc.end():] if desc else clean
        chips = [text_of(s) for s in re.findall(r"<span[^>]*>(.*?)</span>", tail, re.S)]
        chips = [c for c in chips if c]
        posted = re.search(r"Posted[^<]+", clean)
        if chips and "remote" in chips[0].lower():
            chips[0] = "Remote, Nigeria"  # A Nigerian board's “Remote (Work From Home)” means remote within Nigeria.
        yield RawJob("jobberman", link[1].rsplit("-", 1)[-1], html.unescape(link[2]),
                     text_of(company[1]) if company else "Confidential", link[1],
                     location=chips[0] if chips else "", description=text_of(desc[1]) if desc else "",
                     tags=chips[1:2], employment=employment_of(chips[1] if len(chips) > 1 else "", html.unescape(link[2])),
                     salary=chips[2] if len(chips) > 2 and chips[2] != "Confidential" else "",
                     posted_at=relative_date(posted[0]) if posted else None)


def jobberman(max_pages: int = 8) -> Iterable[RawJob]:
    for path in JOBBERMAN_PAGES:
        url = f"https://www.jobberman.com{path}"
        yield from pages_of(url, lambda n: f"{url}?page={n}", parse_jobberman, max_pages)


def parse_myjobmag(page: str) -> Iterable[RawJob]:
    for block in re.findall(r'<li class="job-info">.*?(?=<li class="job-info">|\Z)', page, re.S):
        h = re.search(r'<h2><a href="(/job/[^"]+)">(.*?)</a>', block, re.S)
        if not h:
            continue
        title, _, company = text_of(h[2]).rpartition(" at ")
        desc = re.search(r'<li class="job-desc">(.*?)</li>', block, re.S)
        date = re.search(r'id="job-date">([^<]+)', block)
        places = re.findall(r"href='/jobs-location/[^']*'>([^<]+)<", block)
        salary = re.search(r'class="job-salary">([^<]+)', block)
        yield RawJob("myjobmag", h[1], title or company, company if title else "", "https://www.myjobmag.com" + h[1],
                     location=", ".join(places), description=text_of(desc[1]) if desc else "",
                     salary=html.unescape(salary[1]) if salary else "", posted_at=relative_date(date[1]) if date else None,
                     employment=employment_of(title or company))


def myjobmag(max_pages: int = 8) -> Iterable[RawJob]:
    for path in MYJOBMAG_PAGES:
        url = f"https://www.myjobmag.com{path}"
        yield from pages_of(url, lambda n: f"{url}/{n}", parse_myjobmag, max_pages)


_HN_PLACE = re.compile(r"(?:located in|positions? in|role in|based in)\s+([^.:]{2,90})", re.I)
_HN_TITLE_TAIL = re.compile(r"\s*\(\d+ (Openings?|Positions?)\)\s*$", re.I)


def parse_hotnigerianjobs(feed: str) -> Iterable[RawJob]:
    """Title, link and the feed's own excerpt: the “search” use its Content-Signal allows, nothing more."""
    for it in ET.fromstring(feed).iter("item"):
        raw_title = _HN_TITLE_TAIL.sub("", html.unescape(it.findtext("title") or ""))
        title, _, company = raw_title.rpartition(" at ")
        excerpt = text_of(it.findtext("description"))
        place = _HN_PLACE.search(excerpt)
        location = place[1].strip() if place else ""
        if location and re.search(r"\bremote role\b", excerpt, re.I):
            location = f"Remote, {location}"
        link = it.findtext("link") or ""
        yield RawJob("hotnigerianjobs", link, title or raw_title, company if title else "", link, location=location, description=excerpt,
                     posted_at=iso(it.findtext("pubDate")), employment=employment_of(raw_title))


def hotnigerianjobs() -> Iterable[RawJob]:
    yield from parse_hotnigerianjobs(get("https://www.hotnigerianjobs.com/feed/rss.xml"))


SOURCES: dict[str, Callable[[], Iterable[RawJob]]] = {
    "himalayas": himalayas, "remotive": remotive, "remoteok": remoteok, "jobicy": jobicy,
    "weworkremotely": weworkremotely, "greenhouse": greenhouse, "lever": lever, "ashby": ashby,
    "bamboohr": bamboohr, "teamtailor": teamtailor, "workable": workable, "zoho": zoho, "workday": workday,
    "successfactors": successfactors, "breezy": breezy, "smartrecruiters": smartrecruiters, "oracle": oracle,
    "recruitee": recruitee, "freshteam": freshteam,
    "jobberman": jobberman, "myjobmag": myjobmag, "hotnigerianjobs": hotnigerianjobs,
}
