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
GREENHOUSE = ["moniepoint", "remote", "gitlab", "canonical", "grafanalabs", "elastic", "mozilla", "wikimedia", "vercel"]
LEVER = ["tala", "kraken", "binance"]
ASHBY = ["andela", "sabi", "deel", "zapier", "posthog", "supabase"]


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


BAMBOOHR = {"chowdeck": "Chowdeck", "paga": "Paga", "yellowcard": "Yellow Card", "cellulant": "Cellulant", "grey": "Grey", "risevest": "Risevest"}
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


# ---------- Lagos boards (HTML listing pages and feeds; excerpts only) ----------
# Tech-focused listings first, then the general Lagos feed.
JOBBERMAN_PAGES = ["/jobs/lagos/software-data", "/jobs/software-data", "/jobs/lagos"]
MYJOBMAG_PAGES = ["/jobs-by-field/information-technology", "/jobs-location/lagos"]


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
    "bamboohr": bamboohr, "teamtailor": teamtailor, "jobberman": jobberman, "myjobmag": myjobmag,
    "hotnigerianjobs": hotnigerianjobs,
}
