"""Company discovery: find employers that post jobs on their own careers pages instead of on job boards.

seed     pull company lists from public registers (NGX, CBN, Wikidata) into the companies table
crawl    for each company: find its website, then its careers page, then the hiring system behind it
report   how many companies have a careers page, use a known ATS, ask for CVs by email, or have nothing

A pilot: it measures yield and finds ATS slugs. It doesn't create jobs yet. Same manners as sources.py:
honest User-Agent, robots.txt respected, one company per worker, a few requests each.
"""
from __future__ import annotations

import csv
import gzip
import html
import json
import re
import socket
import sqlite3
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

from .sources import USER_AGENT

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
  key TEXT PRIMARY KEY, name TEXT NOT NULL, website TEXT, website_guessed INTEGER DEFAULT 0,
  industry TEXT, industry_raw TEXT, sources TEXT,
  status TEXT, careers_url TEXT, ats TEXT, ats_slug TEXT, note TEXT, checked_at TEXT
);
"""
RECHECK_DAYS = 7
MAX_BYTES = 1_500_000
TIMEOUT = 20

# What a crawl concludes about a company, best first.
STATUSES = {
    "ats": "careers run on a known hiring system (ATS)",
    "custom": "has its own careers page",
    "linkedin": "careers page sends you to LinkedIn",
    "email": "careers page only says to email a CV",
    "no_careers": "website up, no careers page found",
    "js_only": "website renders in JavaScript, nothing to read without a browser",
    "blocked": "website refused us, or robots.txt disallows it",
    "unreachable": "website down, or no website found",
}


def connect(db: sqlite3.Connection) -> sqlite3.Connection:
    db.executescript(SCHEMA)
    return db


# ---------- names and industries ----------
_LEGAL = r"\b(plc|ltd|limited|nigeria|nig|incorporated|inc|company|co|the|of|and|llc)\b"
GENERIC = {"bank", "microfinance", "mortgage", "finance", "financial", "services", "holdings", "holding", "group",
           "capital", "insurance", "international", "industries", "investment", "investments", "trust", "global"}


def name_key(name: str) -> str:
    """'MTN NIGERIA COMMUNICATIONS PLC' and 'MTN Nigeria Communications Plc.' are one company."""
    s = re.sub(r"[^a-z0-9 ]+", " ", html.unescape(name).lower().replace("&", " and "))
    return " ".join(re.sub(_LEGAL, " ", s).split())


def host_of(url: str | None) -> str:
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    host = (urllib.parse.urlsplit(url.strip()).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def normalise_website(url: str | None) -> str | None:
    if not url or not (h := host_of(url)) or "." not in h:
        return None
    url = url.strip()
    return url if url.lower().startswith("http") else "https://" + url


# One coarse industry per company, so the report can be filtered. Raw labels are kept alongside.
INDUSTRY_RULES = [
    ("Legal & professional services", r"solicitor|accountant|auditor|\blegal\b|law firm"),
    ("Fintech & payments", r"fintech|payment|processing systems|crowdfunding|robo adviser|digital (?:asset|sub-broker|investment)"),
    ("Banking", r"bank|mortgage|discount house|finance compan|development finance|non-interest"),
    ("Insurance & pensions", r"insurance|pension"),
    ("Investment & capital markets", r"invest|asset management|broker|capital market|other financial"),
    ("Telecoms", r"telecom"),
    ("Tech & ICT", r"\bict\b|software|it services|technology|computer|internet|e-commerce|video game"),
    ("Media & entertainment", r"media|broadcast|television|radio|news|journalism|publish|printing|film|music|entertainment"),
    ("Oil, gas & power", r"oil|petroleum|gas\b|energy|electric|utilit|power"),
    ("Consumer goods & retail", r"consumer|food|beverage|brew|retail|personal/household|gastronomy|agricultural product"),
    ("Industrial & construction", r"industrial|cement|construction|building|real estate|manufactur|chemical|natural resources|conglomerate|diversified"),
    ("Transport & logistics", r"transport|airline|aviation|logistic|shipping|haulage"),
    ("Healthcare", r"health|hospital|medical|pharma"),
    ("Agriculture", r"agricultur|farm"),
    ("Education", r"education|universit|school"),
    ("Financial services", r"financ"),
]


def industry_of(*labels: str | None) -> str:
    text = " ".join(x for x in labels if x).lower()
    for name, pattern in INDUSTRY_RULES:
        if re.search(pattern, text):
            return name
    return "Other"


# ---------- seeds ----------
@dataclass
class Seed:
    name: str
    website: str | None
    industry_raw: str
    source: str


def parse_ngx(data: list[dict]) -> list[Seed]:
    return [Seed(c["CompanyName"].strip(), normalise_website(c.get("Website")),
                 " / ".join(x.strip(' "') for x in (c.get("Sector"), c.get("SubSector")) if x), "ngx")
            for c in data if c.get("CompanyName")]


CBN_LISTS = {  # endpoint behind each cbn.gov.ng/supervision/Inst-*.html page
    "GetDMBs": "Commercial bank", "GetNIBs": "Non-interest bank", "GetDHs": "Discount house",
    "GetDFIs": "Development finance institution", "GetFCs": "Finance company", "GetPMIs": "Mortgage bank",
    "GetMFBs": "Microfinance bank",
}
CBN_DEFAULT = ["GetDMBs", "GetNIBs", "GetDHs", "GetDFIs", "GetFCs", "GetPMIs"]  # 790 MFBs are opt-in: few have careers pages.


def parse_cbn(data: list[dict], kind: str) -> list[Seed]:
    return [Seed(c["name"].strip(), normalise_website(c.get("website")), kind, "cbn") for c in data if c.get("name")]


WIKIDATA_QUERY = """
SELECT ?c ?cLabel ?site (GROUP_CONCAT(DISTINCT ?label; separator=" | ") AS ?labels) WHERE {
  { ?c wdt:P452 ?ind. ?ind rdfs:label ?label FILTER(lang(?label)="en") }
  UNION
  { VALUES ?t { wd:Q4830453 wd:Q783794 wd:Q891723 wd:Q6881511 wd:Q22687 wd:Q1589009 wd:Q167037 wd:Q1631111
                wd:Q2085381 wd:Q18388277 wd:Q658255 wd:Q1137109 wd:Q210167 wd:Q5225895 wd:Q17377208 wd:Q11032
                wd:Q1616075 wd:Q15265344 wd:Q46970 wd:Q740752 wd:Q2401749 wd:Q1331793 wd:Q4287745 wd:Q1762059 wd:Q7275 }
    ?c wdt:P31 ?t. ?t rdfs:label ?label FILTER(lang(?label)="en") }
  ?c wdt:P17 wd:Q1033; wdt:P856 ?site.
  FILTER NOT EXISTS { ?c wdt:P576 [] }
  ?c rdfs:label ?cLabel FILTER(lang(?cLabel)="en")
} GROUP BY ?c ?cLabel ?site
"""
# Wikidata lists anything with a website. These are rarely employers you'd find through a careers page.
WIKIDATA_SKIP = re.compile(r"higher education|universit|human development|non-governmental|voluntary sector|"
                           r"international activities|environmental protection|sport", re.I)


def parse_wikidata(data: dict) -> list[Seed]:
    out = []
    for b in data["results"]["bindings"]:
        labels = b.get("labels", {}).get("value", "")
        if WIKIDATA_SKIP.search(labels):
            continue
        out.append(Seed(b["cLabel"]["value"], normalise_website(b["site"]["value"]), labels, "wikidata"))
    return out


FREE_MAIL = re.compile(r"@(?:gmail|googlemail|yahoo|ymail|hotmail|outlook|live|aol|icloud|msn|rocketmail|mail|proton(?:mail)?)\.", re.I)


def website_from_email(text: str | None) -> str | None:
    """A company's own email domain is its website more often than not, and it isn't a guess. Free webmail doesn't count."""
    for m in re.finditer(r"[\w.+-]+@([a-z0-9-]+(?:\.[a-z0-9-]+)+)", text or "", re.I):
        if not FREE_MAIL.search(m[0]):
            return "https://" + m[1].lower().rstrip(".")
    return None


def _cells(row: str) -> list[str]:
    return [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", c)).split()) for c in re.findall(r"<t[dh]\b.*?</t[dh]>", row, re.S)]


SEC_OPERATORS = "https://sec.gov.ng/for-investors/find-a-registered-operator/"


def parse_sec_operators(page: str) -> list[Seed]:
    """One page of the SEC register of capital market operators. Each entry is an accordion with labelled fields."""
    out = []
    for block in re.split(r"toggleAccordion\('operator-", page)[1:]:
        name = re.search(r'font-medium">([^<]+)<', block)
        if not name:
            continue
        fields = {k.strip(" :"): html.unescape(re.sub(r"<[^>]+>", "", v)).strip()
                  for k, v in re.findall(r"<h4[^>]*>([^<]+)</h4>\s*<p[^>]*>(.*?)</p>", block, re.S)}
        functions = [v for k, v in fields.items() if k.startswith("Function") and v and v != "N/A"]
        out.append(Seed(" ".join(html.unescape(name[1]).split()), website_from_email(fields.get("Email")),
                        "Capital market operator: " + ", ".join(functions or ["unspecified"]), "sec"))
    return out


def sec_page_count(page: str) -> int:
    m = re.search(r"Page \d+ of (\d+)", page)
    return int(m[1]) if m else 1


def parse_sec_fintech(page: str) -> list[Seed]:
    """The SEC fintech directory: a matrix of firms by category, then tables of sandbox participants with contacts."""
    out = []
    for table in re.findall(r"<table\b.*?</table>", page, re.S):
        rows = [_cells(r) for r in re.findall(r"<tr\b.*?</tr>", table, re.S)]
        if not rows:
            continue
        head = rows[0]
        if "Company" in head:
            i, contact = head.index("Company"), next((n for n, h in enumerate(head) if h.startswith("Contact")), None)
            kind = next((n for n, h in enumerate(head) if h in ("Function", "Proposed Solution")), None)
            for r in rows[1:]:
                if len(r) <= i or not r[i]:
                    continue
                details = r[contact] if contact is not None and contact < len(r) else ""
                site = re.search(r"Website:?\s*((?:https?://)?[a-z0-9.-]+\.[a-z]{2,}[^\s]*)", details, re.I)
                out.append(Seed(r[i].split("Trading Name")[0].strip(), normalise_website(site[1]) if site else website_from_email(details),
                                "SEC fintech: " + (r[kind] if kind is not None and kind < len(r) else "fintech"), "sec"))
        else:  # the category matrix: each column is a category, each cell a firm
            for r in rows[1:]:
                for n, firm in enumerate(r):
                    if firm:
                        out.append(Seed(firm, None, "SEC fintech: " + (head[n] if n < len(head) else "fintech"), "sec"))
    return out


NCC_LICENSEES = "https://www.ncc.gov.ng/industry/licensing/list-licensees"


def parse_ncc(text: str, today: datetime | None = None) -> list[Seed]:
    """NCC's individual licences (ISPs, value-added services, MVNOs, infrastructure). Lapsed licences are skipped."""
    today = today or datetime.now(timezone.utc)
    out = []
    for row in csv.DictReader(text.lstrip("\ufeff").splitlines()):
        name = " ".join((row.get("COMPANY NAME") or "").split())
        if not name:
            continue
        try:
            if datetime.strptime((row.get("EXPIRY DATE") or "").strip(), "%d/%m/%Y").replace(tzinfo=timezone.utc) < today:
                continue
        except ValueError:
            pass  # No readable expiry: keep it.
        out.append(Seed(name, None, "Telecoms licence: " + (row.get("CATEGORY") or "").strip(), "ncc"))
    return out


def ncc_csv_links(page: str, base: str = NCC_LICENSEES) -> list[str]:
    """The individual-licence CSV. The class list (~10,000 cyber cafés and installers) is left out."""
    links = re.findall(r'href="([^"]+\.csv)"', page)
    return [urllib.parse.urljoin(base, h) for h in links if "class" not in h.lower()]


def fetch_seeds(mfbs: bool = False) -> dict[str, list[Seed] | Exception]:
    """Each register on its own, so one being down doesn't stop the others."""
    def get_json(url: str):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, application/sparql-results+json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))

    def page_text(url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode("utf-8", "ignore")

    out: dict[str, list[Seed] | Exception] = {}
    try:
        out["ngx"] = parse_ngx(get_json("https://doclib.ngxgroup.com/REST/api/issuers/companydirectory?$orderby=CompanyName"))
    except (OSError, ValueError) as e:
        out["ngx"] = e
    try:
        seeds = []
        for endpoint in CBN_DEFAULT + (["GetMFBs"] if mfbs else []):
            seeds += parse_cbn(get_json(f"https://www.cbn.gov.ng/api/{endpoint}"), CBN_LISTS[endpoint])
            time.sleep(1)
        out["cbn"] = seeds
    except (OSError, ValueError) as e:
        out["cbn"] = e
    try:
        q = urllib.parse.urlencode({"query": WIKIDATA_QUERY, "format": "json"})
        out["wikidata"] = parse_wikidata(get_json(f"https://query.wikidata.org/sparql?{q}"))
    except (OSError, ValueError) as e:
        out["wikidata"] = e
    try:
        first = page_text(SEC_OPERATORS)
        seeds = parse_sec_operators(first)
        for n in range(2, sec_page_count(first) + 1):
            time.sleep(1.5)
            seeds += parse_sec_operators(page_text(f"{SEC_OPERATORS}?page={n}"))
        seeds += parse_sec_fintech(page_text("https://sec.gov.ng/fintech-and-innovation-hub-finport/registered-fintech-operators/"))
        out["sec"] = seeds
    except (OSError, ValueError) as e:
        out["sec"] = e
    try:
        seeds = []
        for link in ncc_csv_links(page_text(NCC_LICENSEES)):
            seeds += parse_ncc(page_text(link))
        out["ncc"] = seeds if seeds else ValueError("no licensee CSV linked from the NCC page")
    except (OSError, ValueError) as e:
        out["ncc"] = e
    return out


def save_seeds(db: sqlite3.Connection, seeds: list[Seed]) -> int:
    """Merge on name, then on website, so a company two registers both list is crawled once. Crawl results survive."""
    rows = {r["key"]: dict(r) for r in db.execute("SELECT * FROM companies")}
    by_host = {host_of(r["website"]): k for k, r in rows.items() if r["website"]}
    added = 0
    for s in seeds:
        key = name_key(s.name)
        if not key:
            continue
        key = key if key in rows else by_host.get(host_of(s.website), key)
        row = rows.get(key)
        if row is None:
            row = rows[key] = {"key": key, "name": s.name, "website": None, "website_guessed": 0, "industry_raw": "", "sources": "[]"}
            added += 1
        if s.website and (not row["website"] or row["website_guessed"]):
            row["website"], row["website_guessed"] = s.website, 0
            by_host[host_of(s.website)] = key
        raws = [x for x in (row["industry_raw"] or "").split(" ; ") if x]
        if s.industry_raw and s.industry_raw not in raws:
            raws.append(s.industry_raw)
        row["industry_raw"] = " ; ".join(raws)
        row["sources"] = json.dumps(sorted(set(json.loads(row["sources"] or "[]")) | {s.source}))
        row["industry"] = industry_of(row["industry_raw"])
        db.execute("""INSERT INTO companies (key, name, website, website_guessed, industry, industry_raw, sources)
                      VALUES (?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET website=excluded.website,
                      website_guessed=excluded.website_guessed, industry=excluded.industry,
                      industry_raw=excluded.industry_raw, sources=excluded.sources""",
                   (key, row["name"], row["website"], row["website_guessed"], row["industry"], row["industry_raw"], row["sources"]))
    db.commit()
    return added


# ---------- fetching ----------
@dataclass
class Page:
    url: str      # after redirects
    status: int
    html: str


_LAX_SSL = ssl.create_default_context()
_LAX_SSL.check_hostname = False
_LAX_SSL.verify_mode = ssl.CERT_NONE


def ascii_url(url: str) -> str:
    """urllib only sends ASCII: punycode the host, percent-encode the rest."""
    parts = urllib.parse.urlsplit(url.strip())
    host = (parts.hostname or "").encode("idna").decode("ascii")
    netloc = host + (f":{parts.port}" if parts.port else "")
    quote = lambda x, safe: urllib.parse.quote(x, safe=safe + "%")
    return urllib.parse.urlunsplit((parts.scheme, netloc, quote(parts.path, "/:@!$&'()*+,;=-._~"),
                                    quote(parts.query, "=&/:?@!$'()*+,;-._~"), ""))


def fetch(url: str) -> Page:
    """GET a page. Many company sites have broken certificates; for read-only discovery we retry without checking."""
    try:
        url = ascii_url(url)
    except (UnicodeError, ValueError) as e:
        raise ValueError(f"bad URL {url!r}") from e
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.5"})
    for ctx in (None, _LAX_SSL):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
                return Page(r.geturl(), r.status, r.read(MAX_BYTES).decode("utf-8", "ignore"))
        except urllib.error.HTTPError as e:
            return Page(url, e.code, "")
        except urllib.error.URLError as e:
            if ctx is None and isinstance(e.reason, ssl.SSLError):
                continue
            raise
        except ssl.SSLError:
            if ctx is None:
                continue
            raise
    raise RuntimeError("unreachable")


class Robots:
    """robots.txt per host, fetched with our own User-Agent and timeout (RobotFileParser.read uses neither)."""

    def __init__(self, fetcher=fetch):
        self.fetch, self.cache = fetcher, {}

    def allowed(self, url: str) -> bool:
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.cache:
            rp = urllib.robotparser.RobotFileParser()
            try:
                page = self.fetch(origin + "/robots.txt")
                rp.parse(page.html.splitlines() if page.status == 200 else [])
            except (OSError, ValueError):
                rp.parse([])
            self.cache[origin] = rp
        return self.cache[origin].can_fetch(USER_AGENT, url)


# ---------- reading pages ----------
class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self._href, self._text = a["href"], [a.get("title") or "", a.get("aria-label") or ""]
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(" ".join(self._text).split())))
            self._href = None
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)
        if self._in_title:
            self.title += data


def links_of(markup: str, base: str) -> tuple[list[tuple[str, str]], str]:
    p = _Links()
    try:
        p.feed(markup)
    except Exception:  # Broken markup still gives us the links read before it.
        pass
    out = []
    for href, text in p.links:
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        out.append((urllib.parse.urljoin(base, html.unescape(href.strip())), text))
    return out, " ".join(p.title.split())


# (ats, regex). Group 1 is the slug when there is one. Checked against the raw HTML, so iframes and scripts count.
ATS_PATTERNS: list[tuple[str, str]] = [
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([a-z0-9_-]+)"),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([a-z0-9_-]+)"),
    ("lever", r"jobs(?:\.eu)?\.lever\.co/([a-z0-9_.-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([a-z0-9_.%-]+)"),
    ("workable", r"apply\.workable\.com/(?:api/v\d/(?:widget/)?accounts/)?([a-z0-9_-]+)"),
    ("workable", r"//([a-z0-9-]+)\.workable\.com"),
    ("bamboohr", r"//([a-z0-9-]+)\.bamboohr\.com"),
    ("teamtailor", r"//([a-z0-9-]+)\.teamtailor\.com"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([a-z0-9_-]+)"),
    ("recruitee", r"//([a-z0-9-]+)\.recruitee\.com"),
    ("breezy", r"//([a-z0-9-]+)\.breezy\.hr"),
    ("zohorecruit", r"//([a-z0-9-]+)\.zohorecruit\.(?:com|eu|in)"),
    ("personio", r"//([a-z0-9-]+)\.jobs\.personio\.(?:de|com)"),
    ("freshteam", r"//([a-z0-9-]+)\.freshteam\.com"),
    ("jobvite", r"jobs\.jobvite\.com/([a-z0-9_-]+)"),
    ("workday", r"//([a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?[A-Za-z0-9_-]+)"),
    ("successfactors", r"successfactors\.(?:com|eu)/(?:career|sfcareer)[^\"'\s]*?company=([A-Za-z0-9_-]+)"),
    ("successfactors", r"//(?:[a-z0-9-]+\.)?successfactors\.(?:com|eu)()"),
    ("oracle", r"//([a-z0-9-]+\.fa\.[a-z0-9-]+\.oraclecloud\.com/hcmUI/CandidateExperience/(?:[a-z]{2}(?:-[a-z]{2})?/)?sites/[A-Za-z0-9_]+)"),
    ("oracle", r"//([a-z0-9-]+\.fa\.[a-z0-9-]+\.oraclecloud\.com)/hcmUI/CandidateExperience"),
    ("taleo", r"//([a-z0-9-]+)\.taleo\.net"),
    ("icims", r"//careers-([a-z0-9-]+)\.icims\.com"),
    ("manatal", r"careers-page\.com/([a-z0-9_-]+)"),
]
# Path segments that aren't slugs (asset folders, API words).
_NOT_SLUG = {"embed", "v1", "api", "js", "css", "assets", "static", "widget", "www", "jobs", "careers", "app", "cdn", "help", "support", "j", "o"}
# Ones Scout already reads (sources.py), so a slug found here can go straight into a list there.
READABLE_ATS = {"greenhouse", "lever", "ashby", "bamboohr", "teamtailor", "workable", "zohorecruit", "workday",
                "smartrecruiters", "breezy", "oracle", "recruitee", "freshteam"}


def _slug_form(ats: str, slug: str | None) -> str | None:
    """Slugs in the form sources.py takes. Workday and Oracle ones are host/site, and their site names are case-sensitive."""
    if not slug:
        return None
    if ats == "workday":
        return re.sub(r"/[a-z]{2}-[A-Z]{2}/", "/", slug)  # The language segment isn't part of the board.
    if ats == "oracle":
        host, _, site = slug.partition("/hcmUI/CandidateExperience/")
        return f"{host.lower()}/{site.rsplit('/', 1)[-1]}" if site else host.lower()
    return slug.lower()


def detect_ats(markup: str) -> list[tuple[str, str | None]]:
    found: dict[str, str | None] = {}
    for ats, pattern in ATS_PATTERNS:
        for m in re.finditer(pattern, markup, re.I):
            slug = (m.group(1) or "").strip("./") or None
            if slug and slug.lower() in _NOT_SLUG:
                continue
            if ats not in found or (found[ats] is None and slug):
                found[ats] = _slug_form(ats, slug)
    return sorted(found.items(), key=lambda kv: (kv[0] not in READABLE_ATS, kv[1] is None))


_CAREER_TEXT = re.compile(r"\b(careers?|jobs?|vacanc(?:y|ies)|join (?:us|our team|the team)|work (?:with|for) us|"
                          r"working (?:at|with) us|recruitment|job openings?|open (?:roles|positions)|life at)\b", re.I)
# A whole path segment, at most one word before it ("/our-careers", "/careers.php"), never an article slug
# like "/asake-says-dancing-remains-inseparable-from-his-music-career/".
_CAREER_PATH = re.compile(r"/(?:[a-z]+[-_])?(?:careers?|jobs?|vacanc(?:y|ies)|join[-_]?us|join[-_]our[-_]team|"
                          r"work[-_]with[-_]us|recruitment|opportunities)(?:\.[a-z]+)?(?:[/?#]|$)", re.I)
_NOT_CAREERS = re.compile(r"/(?:news|blog|press|media|article|stories|post)s?/|/(?:19|20)\d\d/\d\d?/", re.I)
_CAREERS_HOSTS = {"careers", "career", "jobs", "talent", "recruitment", "join", "workwithus"}
FALLBACK_PATHS = ["/careers", "/career", "/jobs"]


def career_links(links: list[tuple[str, str]], site_host: str) -> list[str]:
    """Links that look like they lead to jobs, best first. LinkedIn and ATS links count too."""
    scored = {}
    for url, text in links:
        host = host_of(url)
        same_site = host == site_host or host.endswith("." + site_host) or site_host.endswith("." + host)
        ats = bool(detect_ats(url))
        # Groups host careers for every subsidiary: careers.unilever.com, careers.theheinekencompany.com.
        careers_host = host.split(".")[0] in _CAREERS_HOSTS
        path = urllib.parse.urlsplit(url).path
        score = 0
        if _CAREER_TEXT.search(text) and len(text.split()) <= 5:
            score += 2
        if _CAREER_PATH.search(path) or careers_host:
            score += 2
        if ats:  # A direct ATS link beats a landing page that would only point to it.
            score += 4
        if not score or _NOT_CAREERS.search(path) or not (same_site or ats or careers_host):
            continue
        if url.lower().endswith((".pdf", ".jpg", ".png", ".docx")):
            continue
        scored[url.split("#")[0]] = max(scored.get(url.split("#")[0], 0), score + same_site)
    return sorted(scored, key=lambda u: (-scored[u], len(u)))


_EMAIL_ONLY = re.compile(r"(?:send|forward|submit|email|mail)\b[^.<]{0,100}\b(?:cv|c\.v|resume|résumé|curriculum vitae|applications?)\b|"
                         r"\b(?:cv|resume)s?\b[^.<]{0,60}\b(?:to|at)\b[^.<]{0,20}@", re.I)
_LINKEDIN_JOBS = re.compile(r"linkedin\.com/(?:company/[^\"'/\s]+/jobs|jobs/)", re.I)
_JOB_WORDS = re.compile(r"\b(apply|vacanc|position|opening|role|job|engineer|officer|manager|analyst|developer)\b", re.I)


_EMAIL_ADDR = re.compile(r"(?<![\w.+-])[\w.+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?![\w-])", re.I)
# Addresses that are really asset names (logo@2x.png) or a site builder's own.
_NOT_AN_EMAIL = re.compile(r"\.(?:png|jpe?g|gif|svg|webp|css|js)$|@(?:sentry|wixpress|example|domain|email)\.|^(?:name|your|user)@", re.I)


_HIRING_MAILBOX = re.compile(r"career|recruit|hr|job|talent|cv|vacanc|hiring|people|resume", re.I)


def hiring_first(emails: list[str]) -> list[str]:
    """careers@ and hr@ before info@: the page's general address is often listed first."""
    return sorted(emails, key=lambda e: not _HIRING_MAILBOX.search(e.split("@")[0]))


def page_emails(markup: str, limit: int = 3) -> list[str]:
    """Addresses a careers page gives: hiring mailboxes first, then mailto links, then any in the text."""
    found = re.findall(r'mailto:([^"\'?\s>]+)', markup, re.I) + _EMAIL_ADDR.findall(re.sub(r"<[^>]+>", " ", markup))
    clean = [urllib.parse.unquote(e).strip(".,;").lower() for e in found]
    return hiring_first(list(dict.fromkeys(e for e in clean if _EMAIL_ADDR.fullmatch(e) and not _NOT_AN_EMAIL.search(e))))[:limit]


def classify_careers(markup: str) -> tuple[str, str | None, str | None, str]:
    """(status, ats, slug, note) for a careers page. Email notes start with the addresses found; LinkedIn ones are the jobs link."""
    hits = detect_ats(markup)
    if hits:
        ats, slug = hits[0]
        return "ats", ats, slug, ", ".join(f"{a}:{s or '?'}" for a, s in hits[1:])
    text = re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)\b.*?</\1>", " ", markup, flags=re.S | re.I))
    if (link := _LINKEDIN_JOBS.search(markup)) and len(_JOB_WORDS.findall(text)) < 15:
        url = re.match(r"[^\"'\s<>]*", markup[link.start():])[0]
        return "linkedin", None, None, html.unescape("https://www." + url if not url.startswith("http") else url)
    if m := _EMAIL_ONLY.search(text):
        said = " ".join(m[0].split())[:120]
        return "email", None, None, " · ".join(page_emails(markup) + [said])
    return "custom", None, None, ""


def looks_js_only(markup: str) -> bool:
    text = re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style|noscript)\b.*?</\1>", " ", markup, flags=re.S | re.I))
    return len(text.split()) < 40 and markup.count("<a ") < 5


# ---------- finding a website from a name ----------
_NIGERIAN = re.compile(r"nigeria|lagos|abuja|naira|₦|\bngn\b|\bcbn\b|\+234|\.ng\b", re.I)
_PARKED = re.compile(r"domain (?:is )?for sale|buy this domain|parked|this domain may be for sale|hugedomains|sedo", re.I)


def domain_guesses(name: str) -> list[str]:
    words = name_key(name).split()
    if not words:
        return []
    joined = "".join(words)
    bases = [joined, joined + "plc"]
    # "Alpha Morgan Bank" may live at alphamorgan.com, but "Pilot Finance" isn't pilot.com.
    if len(words) > 2 and words[-1] in GENERIC:
        bases.append("".join(words[:-1]))
    bases = list(dict.fromkeys(b for b in bases if len(b) >= 3))
    return [b + tld for b in bases for tld in (".com", ".com.ng", ".ng")]


def page_matches(name: str, page: Page) -> bool:
    """Does this page belong to the named company? Every distinctive word of the name must appear near the top."""
    if page.status != 200 or _PARKED.search(page.html[:20000]):
        return False
    words = [w for w in name_key(name).split() if w not in GENERIC and len(w) > 1] or name_key(name).split()
    host = re.sub(r"[^a-z0-9]", "", host_of(page.url))
    if not all(w in host for w in words):  # Redirected somewhere else entirely, as aghomes.com does.
        return False
    _, title = links_of(page.html[:60000], page.url)
    head = (title + " " + page.html[:60000]).lower()
    # Every register we guess for is Nigerian, so the real site says so somewhere near the top.
    return all(re.search(rf"\b{re.escape(w)}", head) for w in words) and bool(_NIGERIAN.search(head))


def guess_website(name: str, fetcher=fetch) -> str | None:
    for domain in domain_guesses(name):
        try:
            socket.getaddrinfo(domain, 443)
        except (socket.gaierror, UnicodeError):
            continue
        for url in (f"https://{domain}", f"http://www.{domain}"):
            try:
                page = fetcher(url)
            except (OSError, ValueError):
                continue
            if page_matches(name, page):
                return page.url
            break
    return None


# ---------- crawling one company ----------
@dataclass
class Result:
    key: str
    status: str
    website: str | None = None
    website_guessed: bool = False
    careers_url: str | None = None
    ats: str | None = None
    ats_slug: str | None = None
    note: str = ""


def _is_dns_error(e: Exception) -> bool:
    return isinstance(getattr(e, "reason", None), socket.gaierror)


def fetch_home(url: str, fetcher=fetch) -> Page:
    """The homepage, trying the other of www./bare host when DNS can't find the one given."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    other = host[4:] if host.startswith("www.") else "www." + host
    tries = [url, url, urllib.parse.urlunsplit(parts._replace(netloc=other))]
    for n, attempt in enumerate(tries):
        try:
            return fetcher(attempt)
        except urllib.error.URLError as e:
            if not _is_dns_error(e) or n == len(tries) - 1:
                raise
            if n == 0:
                time.sleep(2)
    raise RuntimeError("unreachable")


def crawl_one(key: str, name: str, website: str | None, fetcher=fetch, robots: Robots | None = None, pause: float = 1.0) -> Result:
    robots = robots or Robots(fetcher)
    guessed = False
    if not website:
        website = guess_website(name, fetcher)
        guessed = True
        if not website:
            return Result(key, "unreachable", note="no website listed, and none guessed from the name")
    r = Result(key, "unreachable", website=website, website_guessed=guessed)
    try:
        if not robots.allowed(website):
            r.status, r.note = "blocked", "robots.txt"
            return r
        home = fetch_home(website, fetcher)
    except (OSError, ValueError) as e:
        r.note = repr(e)[:160]
        return r
    if home.status in (401, 403, 429, 503):
        r.status, r.note = "blocked", f"HTTP {home.status}"
        return r
    if home.status != 200:
        r.note = f"HTTP {home.status}"
        return r
    r.website = home.url
    site_host = host_of(home.url)

    if hits := detect_ats(home.html):  # Some sites link straight to their ATS from the footer.
        ats, slug = hits[0]
        if ats in READABLE_ATS or slug:
            r.status, r.ats, r.ats_slug, r.careers_url = "ats", ats, slug, home.url
            return r

    links, _ = links_of(home.html, home.url)
    candidates = career_links(links, site_host)[:2]
    if not candidates:
        origin = f"{urllib.parse.urlsplit(home.url).scheme}://{urllib.parse.urlsplit(home.url).netloc}"
        candidates = [origin + p for p in FALLBACK_PATHS]
        guessing = True
    else:
        guessing = False

    refused = None  # A careers link we found but couldn't open is still worth knowing about.
    for url in candidates:
        if _LINKEDIN_JOBS.search(url):
            r.status, r.careers_url = "linkedin", url
            return r
        if hits := detect_ats(url):
            r.status, r.careers_url, (r.ats, r.ats_slug) = "ats", url, hits[0]
            return r
        if not robots.allowed(url):
            refused = refused or (url, "robots.txt disallows the careers page")
            continue
        time.sleep(pause)
        try:
            page = fetcher(url)
        except (OSError, ValueError) as e:
            refused = refused or (url, repr(e)[:120])
            continue
        if page.status != 200:
            if page.status in (401, 403, 429, 500, 502, 503) and not guessing:
                refused = refused or (url, f"careers page answered HTTP {page.status}")
            continue
        if guessing:  # A made-up path only counts if the page calls itself a careers page.
            _, title = links_of(page.html[:20000], page.url)
            # WordPress answers /careers by redirecting to the nearest article ("/career-vs-home-..."), so the
            # page we land on must still have a careers address.
            landed = urllib.parse.urlsplit(page.url).path
            if not _CAREER_TEXT.search(title) or not _CAREER_PATH.search(landed) or _NOT_CAREERS.search(landed):
                continue
        r.careers_url = page.url
        r.status, r.ats, r.ats_slug, r.note = classify_careers(page.html)
        if r.status == "custom":  # One more hop: a careers landing page often links on to the ATS.
            deeper = [u for u in career_links(links_of(page.html, page.url)[0], site_host) if detect_ats(u) or _LINKEDIN_JOBS.search(u)]
            if deeper:
                if _LINKEDIN_JOBS.search(deeper[0]):
                    r.status = "linkedin"
                else:
                    r.status, (r.ats, r.ats_slug) = "ats", detect_ats(deeper[0])[0]
                r.careers_url = deeper[0]
        return r

    if refused and not guessing:
        r.status, (r.careers_url, r.note) = "blocked", refused
        return r
    r.status = "js_only" if looks_js_only(home.html) else "no_careers"
    return r


# ---------- crawl, report ----------
def due(db: sqlite3.Connection, everything: bool, limit: int | None, industry: str | None,
        statuses: list[str] | None = None) -> list[sqlite3.Row]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RECHECK_DAYS)).isoformat()
    sql = "SELECT key, name, website, website_guessed FROM companies WHERE 1=1"
    args: list = []
    if not everything:
        sql += " AND (checked_at IS NULL OR checked_at < ?)"
        args.append(cutoff)
    if industry:
        sql += " AND industry = ?"
        args.append(industry)
    if statuses:
        sql += f" AND status IN ({','.join('?' * len(statuses))})"
        args += statuses
    sql += " ORDER BY checked_at IS NOT NULL, key"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return db.execute(sql, args).fetchall()


def save_result(db: sqlite3.Connection, r: Result) -> None:
    db.execute("""UPDATE companies SET status=?, careers_url=?, ats=?, ats_slug=?, note=?, checked_at=?,
                  website=CASE WHEN ? IS NULL AND website_guessed=0 THEN website ELSE ? END,
                  website_guessed=CASE WHEN ? IS NULL AND website_guessed=0 THEN 0 ELSE ? END
                  WHERE key=?""",
               (r.status, r.careers_url, r.ats, r.ats_slug, r.note, datetime.now(timezone.utc).isoformat(),
                r.website, r.website, r.website, int(r.website_guessed), r.key))
    db.commit()


def crawl(db: sqlite3.Connection, rows: list[sqlite3.Row], workers: int = 8, progress=print) -> Counter:
    """Different companies are different hosts, so a few run at once; requests to any one host stay sequential."""
    tally: Counter = Counter()
    with ThreadPoolExecutor(workers) as pool:
        futures = {pool.submit(crawl_one, r["key"], r["name"], None if r["website_guessed"] else r["website"]): r for r in rows}
        for n, f in enumerate(as_completed(futures), 1):
            row = futures[f]
            try:
                result = f.result()
            except Exception as e:  # A bug on one odd site shouldn't lose the rest of the run.
                result = Result(row["key"], "unreachable", note=f"crawler error: {e!r}"[:160])
            save_result(db, result)
            tally[result.status] += 1
            extra = f"{result.ats}:{result.ats_slug}" if result.ats else (result.careers_url or result.note or "")
            progress(f"[{n}/{len(rows)}] {result.status:11} {row['name'][:40]:40} {extra[:70]}")
    return tally


def report(db: sqlite3.Connection, industry: str | None = None) -> str:
    where, args = ("WHERE industry = ?", [industry]) if industry else ("", [])
    rows = db.execute(f"SELECT * FROM companies {where}", args).fetchall()
    checked = [r for r in rows if r["status"]]
    out = [f"{len(rows)} companies, {len(checked)} crawled" + (f"  (industry: {industry})" if industry else "")]
    if not checked:
        return "\n".join(out + ["Run `scout companies crawl` first."])

    by_status = Counter(r["status"] for r in checked)
    out.append("")
    for status, meaning in STATUSES.items():
        n = by_status.get(status, 0)
        out.append(f"{n:6}  {100 * n / len(checked):5.1f}%  {status:12} {meaning}")
    out.append(f"\n{sum(1 for r in checked if r['website_guessed'])} websites were guessed from the company name.")

    out.append("\nBy industry (crawled / with a careers page or ATS / ATS)")
    inds = Counter(r["industry"] for r in checked)
    for ind, n in inds.most_common():
        page = sum(1 for r in checked if r["industry"] == ind and r["status"] in ("ats", "custom", "email", "linkedin"))
        ats = sum(1 for r in checked if r["industry"] == ind and r["status"] == "ats")
        out.append(f"  {ind:32}{n:5}{page:6}{ats:6}")

    ats_rows = sorted((r for r in checked if r["status"] == "ats"), key=lambda r: (r["ats"], r["name"]))
    out.append(f"\nHiring systems found ({len(ats_rows)})")
    for ats, n in Counter(r["ats"] for r in ats_rows).most_common():
        out.append(f"  {ats:16}{n:4}{'   (Scout reads this)' if ats in READABLE_ATS else ''}")
    out.append("")
    for r in ats_rows:
        out.append(f"  {r['ats']:15} {r['ats_slug'] or '?':28} {r['name'][:38]:38} {r['careers_url'] or ''}")

    ready = [r for r in ats_rows if r["ats"] in READABLE_ATS and r["ats_slug"]]
    if ready:
        out.append("\nSlugs Scout can read today (lists in scout/sources.py):")
        for ats in sorted({r["ats"] for r in ready}):
            out.append(f"  {ats.upper()}: " + ", ".join(sorted({r['ats_slug'] for r in ready if r['ats'] == ats})))
    return "\n".join(out)


def listing(db: sqlite3.Connection, statuses: list[str] | None, industry: str | None) -> list[sqlite3.Row]:
    sql, args = "SELECT * FROM companies WHERE 1=1", []
    if statuses:
        sql += f" AND status IN ({','.join('?' * len(statuses))})"
        args += statuses
    if industry:
        sql += " AND industry = ?"
        args.append(industry)
    return db.execute(sql + " ORDER BY industry, name", args).fetchall()


# ---------- the /pilot page ----------
# What the page lists: companies a person can follow up by hand. Everything else is only counted.
PILOT_GROUPS = {"custom": "careers", "email": "email", "no_careers": "none"}
FOUND = ("ats", "custom", "email", "linkedin")
# Where each crawl result stands in the work: act on it now, come back for it with a browser, or leave it.
PILOT_PLAN = {"custom": "act", "email": "act", "no_careers": "act", "js_only": "later",
              "ats": "hold", "linkedin": "hold", "blocked": "hold", "unreachable": "hold"}
ACTIONS = ("emailed", "applied", "watching", "skip")
# Kept apart from companies so seeding and recrawling never touch what you did.
ACTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS company_actions (
  key TEXT PRIMARY KEY, action TEXT, star INTEGER DEFAULT 0, note TEXT DEFAULT '', updated_at TEXT
);
"""


def set_action(db: sqlite3.Connection, key: str, action: str | None, star: bool, note: str, when: str | None = None) -> bool:
    """Record what you did about a company, now or at `when`. False if there is no such company."""
    if action is not None and action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    if not db.execute("SELECT 1 FROM companies WHERE key = ?", (key,)).fetchone():
        return False
    db.executescript(ACTIONS_SCHEMA)
    note = note.strip()
    if action is None and not star and not note:
        db.execute("DELETE FROM company_actions WHERE key = ?", (key,))
    else:
        old = db.execute("SELECT action, updated_at FROM company_actions WHERE key = ?", (key,)).fetchone()
        # The date says when the action happened, so a later note or star doesn't move it.
        if when is None:
            when = old[1] if old and old[0] == action and action else datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.execute("INSERT OR REPLACE INTO company_actions (key, action, star, note, updated_at) VALUES (?,?,?,?,?)",
                   (key, action, int(star), note, when))
    db.commit()
    return True


def _actions(db: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'company_actions'").fetchone():
        return {}  # Nothing recorded yet, and a read-only connection can't create the table.
    return {r["key"]: r for r in db.execute("SELECT * FROM company_actions")}


def pilot_view(db: sqlite3.Connection) -> dict:
    from . import outreach  # It imports this module.
    rows = db.execute("SELECT * FROM companies").fetchall()
    checked = [r for r in rows if r["status"]]
    acts = _actions(db)
    mails = outreach.rows(db)
    shown, left_out = [], 0
    for r in checked:
        if r["status"] == "ats" and r["ats"] in READABLE_ATS:
            left_out += 1  # Its jobs already come in through sources.py, or can once its slug is added.
        group = PILOT_GROUPS.get(r["status"])
        if not group:
            continue
        note = r["note"] or ""
        emails = hiring_first(_EMAIL_ADDR.findall(note)) if group == "email" else []
        said = note.rsplit(" · ", 1)[-1] if group == "email" and not _EMAIL_ADDR.fullmatch(note.rsplit(" · ", 1)[-1]) else ""
        a, m = acts.get(r["key"]), mails.get(r["key"])
        shown.append({
            "key": r["key"], "name": r["name"], "group": group, "industry": r["industry"] or "Other",
            "industry_raw": r["industry_raw"] or "", "registers": json.loads(r["sources"] or "[]"),
            "website": r["website"], "website_guessed": bool(r["website_guessed"]), "careers_url": r["careers_url"],
            "emails": emails, "said": said, "checked_at": r["checked_at"],
            "action": a["action"] if a else None, "star": bool(a and a["star"]), "note": a["note"] if a else "",
            "acted_at": a["updated_at"] if a and a["action"] else None,
            "mail": {"to": m["address"], "subject": m["subject"], "drafted_at": m["drafted_at"], "sent_at": m["sent_at"]} if m else None})
    registers = Counter(s for r in checked for s in json.loads(r["sources"] or "[]"))
    found = Counter(s for r in checked if r["status"] in FOUND for s in json.loads(r["sources"] or "[]"))
    statuses = Counter(r["status"] for r in checked)
    return {
        "total": len(rows), "crawled": len(checked), "readable_left_out": left_out,
        "statuses": dict(statuses.most_common()), "meanings": STATUSES, "plan": PILOT_PLAN,
        "registers": [{"register": k, "crawled": n, "found": found.get(k, 0)} for k, n in registers.most_common()],
        "last_checked": max((r["checked_at"] for r in checked if r["checked_at"]), default=None),
        "companies": sorted(shown, key=lambda c: c["name"].lower()),
    }


# ---------- the list that ships with Scout ----------
# The crawl takes over half an hour, so a finished one is kept in the repo. Only what the registers and the
# crawl found: none of your stars, notes, actions or emails, which live in other tables.
SEED = Path(__file__).resolve().parent.parent / "seeds" / "companies.json.gz"
SEED_COLUMNS = ("key", "name", "website", "website_guessed", "industry", "industry_raw", "sources",
                "status", "careers_url", "ats", "ats_slug", "note", "checked_at")


def export_seed(db: sqlite3.Connection, path: Path = SEED) -> int:
    rows = [dict(zip(SEED_COLUMNS, r)) for r in db.execute(f"SELECT {', '.join(SEED_COLUMNS)} FROM companies ORDER BY key")]
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
    return len(rows)


def load_seed(db_path: Path, path: Path = SEED) -> int:
    """Add the shipped companies that db_path doesn't have yet. Companies already there are left as they are."""
    if not path.exists():
        return 0
    with gzip.open(path, "rt", encoding="utf-8") as f:
        rows = json.load(f)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = connect(sqlite3.connect(db_path))
    try:
        before = db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        db.executemany(f"INSERT OR IGNORE INTO companies ({', '.join(SEED_COLUMNS)}) VALUES ({', '.join('?' * len(SEED_COLUMNS))})",
                       [tuple(r.get(c) for c in SEED_COLUMNS) for r in rows])
        db.commit()
        return db.execute("SELECT COUNT(*) FROM companies").fetchone()[0] - before
    finally:
        db.close()
