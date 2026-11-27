"""What profile.json changes outside the scoring: the board pages to read, where you'd work, which skills count.

A field is a line of work. Each one names its pages on Jobberman and MyJobMag, the words that keep a round-up
programme post ("multiple positions") worth showing, and which skill vocabulary scores it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import eligibility, matching, programmes, sources


@dataclass(frozen=True)
class Field:
    label: str
    jobberman: tuple[str, ...]
    myjobmag: tuple[str, ...]
    roles: str  # Regex for round-up programme posts.
    skills: str  # A key of matching.SKILL_GROUPS.
    examples: str  # Example job titles, shown by `scout setup`.


FIELDS: dict[str, Field] = {
    "tech": Field("Tech and software", ("software-data",), ("information-technology",), programmes._TECH.pattern, "tech",
                  "Frontend Developer, Data Analyst, IT Support"),
    "sales": Field("Sales and business development", ("sales", "management-business-development"), ("sales-marketing",),
                   r"\b(sales|business develop\w*|relationship (manager|officer)|account (manager|officer|executive)|marketer)\b", "business",
                   "Sales Executive, Business Development Manager, Relationship Officer"),
    "marketing": Field("Marketing, media and communications", ("marketing-communications", "advertising-media-communications"),
                       ("marketing-communication", "media"), r"\b(marketing|brand|communications?|media|digital|content)\b", "business",
                       "Marketing Executive, Brand Manager, Social Media Manager"),
    "customer": Field("Customer service", ("customer-service-support",), ("customer-care",),
                      r"\b(customer (service|care|support|experience)|call cent(er|re)|front desk)\b", "business",
                      "Customer Service Representative, Customer Success Manager"),
    "finance": Field("Banking, finance and accounting", ("banking-finance-insurance", "accounting-auditing-finance"),
                     ("banking", "accounting-audit", "insurance"), r"\b(bank\w*|financ\w*|account\w*|audit\w*|insurance|credit)\b", "business",
                     "Accountant, Credit Analyst, Relationship Officer"),
    "hr_admin": Field("HR and administration", ("human-resources", "admin-office"), ("human-resources", "administration"),
                      r"\b(hr|human resources?|admin\w*|recruit\w*|office)\b", "business", "HR Officer, Admin Officer, Recruiter"),
    "operations": Field("Logistics, supply chain and operations", ("shipping-logistics", "supply-chain-procurement"),
                        ("logistics", "procurement-store-keeping"), r"\b(logistics|supply chain|procurement|operations?|warehouse|store ?keep\w*)\b",
                        "business", "Operations Officer, Logistics Coordinator, Procurement Officer"),
    "projects": Field("Product and project management", ("product-project-management",), ("product-management", "project-management"),
                      r"\b(product|project|programme|program) (manager|management|officer|associate)\b", "business",
                      "Project Manager, Product Manager, Programme Officer"),
}

# Each board has general city pages for only a few places. Elsewhere, its nationwide field pages stand in.
JOBBERMAN_CITIES = {"lagos": "lagos", "abuja": "abuja"}
MYJOBMAG_CITIES = {"lagos": "lagos", "abuja": "abuja", "port harcourt": "rivers", "rivers": "rivers", "abeokuta": "ogun",
                   "ogun": "ogun", "enugu": "enugu", "asaba": "delta", "warri": "delta", "delta": "delta", "uyo": "akwa-ibom"}


def board_pages(field_keys: list[str], home: str) -> tuple[list[str], list[str]]:
    """Jobberman and MyJobMag paths: each field in your city and nationwide, then your city's general feed."""
    fields = [FIELDS[k] for k in field_keys if k in FIELDS] or [FIELDS["tech"]]
    jb_city, mj_city = JOBBERMAN_CITIES.get(home.lower()), MYJOBMAG_CITIES.get(home.lower())
    jobberman = [p for f in fields for slug in f.jobberman for p in ((f"/jobs/{slug}/{jb_city}",) if jb_city else ()) + (f"/jobs/{slug}",)]
    myjobmag = [f"/jobs-by-field/{slug}" for f in fields for slug in f.myjobmag]
    return (jobberman + ([f"/jobs/{jb_city}"] if jb_city else []),
            list(dict.fromkeys(myjobmag)) + ([f"/jobs-location/{mj_city}"] if mj_city else []))


def configure(profile: dict) -> None:
    """Point the classifier, the boards and the programme filter at this profile. Missing keys mean the original
    setup: tech, living in Lagos, not moving, and wanting remote jobs abroad."""
    keys = profile.get("fields") or ["tech"]
    home = profile.get("home_city") or "Lagos"
    eligibility.set_area(eligibility.area_for(home, profile.get("relocate") or "no"))
    sources.JOBBERMAN_PAGES, sources.MYJOBMAG_PAGES = board_pages(keys, home)
    programmes.ROUNDUP_ROLES = re.compile("|".join(f"(?:{FIELDS[k].roles})" for k in keys if k in FIELDS) or programmes._TECH.pattern, re.I)
    matching.set_active_skills(matching.vocabulary(profile))
    matching.set_mid_managers("business" in (profile.get("skill_groups") or []))
    matching.add_custom_skills(profile.get("custom_skills", []))


def skipped_sources(profile: dict) -> set[str]:
    return set() if profile.get("global_remote", True) else set(sources.REMOTE_BOARDS)


def load(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
