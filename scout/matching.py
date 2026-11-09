"""Scoring: Jobright's visible signals (title, skills, seniority, industry) plus your feedback."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np

SKILLS: dict[str, str] = {
    # name -> regex. Names are lowercase and are what profile.json refers to.
    "typescript": r"typescript|\bts\b", "javascript": r"javascript|\bes6\b", "python": r"python", "sql": r"\bsql\b",
    "react": r"\breact(\.?js)?\b(?! native)", "react native": r"react native", "next.js": r"next\.?js", "vue": r"\bvue(\.?js)?\b",
    "angular": r"\bangular", "svelte": r"\bsvelte", "tanstack query": r"tanstack|react query", "redux": r"\bredux",
    "zustand": r"zustand", "tailwind": r"tailwind", "html": r"\bhtml5?\b", "css": r"\bcss3?\b", "graphql": r"graphql",
    "node.js": r"node\.?js|\bnode\b", "express": r"express\.?js", "nestjs": r"nest\.?js", "django": r"\bdjango", "flask": r"\bflask",
    "fastapi": r"fastapi", "java": r"\bjava\b", "spring": r"\bspring( boot)?\b", "c#": r"\bc#|\.net\b", "php": r"\bphp\b",
    "laravel": r"laravel", "ruby": r"\bruby\b|\brails\b", "go": r"\bgolang\b|\bgo\b(?= (developer|engineer|services|programming))",
    "rust": r"\brust\b", "kotlin": r"kotlin", "swift": r"\bswift\b", "flutter": r"flutter", "c++": r"c\+\+",
    "supabase": r"supabase", "postgresql": r"postgres", "mysql": r"mysql", "mongodb": r"mongo", "redis": r"\bredis\b", "firebase": r"firebase",
    "appwrite": r"appwrite", "aws": r"\baws\b|amazon web services", "gcp": r"\bgcp\b|google cloud", "azure": r"\bazure\b",
    "docker": r"docker|containeri[sz]", "kubernetes": r"kubernetes|\bk8s\b", "terraform": r"terraform", "linux": r"\blinux\b",
    "git": r"\bgit\b", "github actions": r"github actions", "ci/cd": r"ci/cd|continuous integration",
    "rest api": r"\brest(ful)?\b", "rbac": r"\brbac\b|role[- ]based access", "websocket": r"websocket|socket\.io", "microservices": r"microservice",
    "llm": r"\bllms?\b|large language model|generative ai|\bgenai\b", "claude": r"\bclaude\b|anthropic", "openai": r"openai|\bgpt-?\d?", "rag": r"\brag\b|retrieval[- ]augmented",
    "langchain": r"langchain|langgraph", "context engineering": r"context engineering|prompt engineering", "ai agents": r"\bai agents?\b|agentic",
    "machine learning": r"machine learning|\bml\b", "pytorch": r"pytorch", "tensorflow": r"tensorflow", "pandas": r"\bpandas\b", "spark": r"\bspark\b", "airflow": r"airflow",
    "n8n": r"\bn8n\b", "automation": r"workflow automation|automation pipelines?|process automation", "ffmpeg": r"ffmpeg", "qa": r"\bqa\b|test automation|cypress|playwright|jest\b|testing library",
    "accessibility": r"\bwcag\b|\ba11y\b|web accessibility|accessibility (standards|best practices|guidelines|requirements)|accessible (ui|uis|interfaces?|design|web)",
    "design systems": r"design systems?\b|component librar", "storybook": r"storybook", "web performance": r"web vitals|web performance|(frontend|front-end|ui) performance|performance optimi[sz]ation",
    "pwa": r"\bpwas?\b|progressive web|offline[- ]first", "sass": r"\bsass\b|\bscss\b", "vite": r"\bvite\b", "webpack": r"webpack",
    "web3": r"\bweb3\b|blockchain|solidity", "figma": r"\bfigma\b", "salesforce": r"salesforce", "hubspot": r"hubspot", "excel": r"\bexcel\b", "seo": r"\bseo\b",
}
_SKILL_RX = {k: re.compile(v, re.I) for k, v in SKILLS.items()}

INDUSTRIES: dict[str, str] = {
    "fintech": r"fintech|payments?\b|banking|neobank|lending", "ai": r"\bai\b|artificial intelligence|machine learning",
    "developer tools": r"developer tools?|devtools|developer platform|open[- ]source", "social media": r"social (media|network|platform)|creator",
    "media": r"streaming|video platform|media platform", "gaming": r"\bgaming\b|\bgames?\b|esports", "web3": r"web3|crypto|blockchain",
    "healthcare": r"healthcare|healthtech", "e-commerce": r"e-?commerce|marketplace", "edtech": r"edtech", "saas": r"\bsaas\b",
}
_INDUSTRY_RX = {k: re.compile(v, re.I) for k, v in INDUSTRIES.items()}

_LEVELS = [(5, r"\b(principal|director|head of|vp|vice president|chief|cto)\b"), (4, r"\b(staff|lead|architect|manager)\b"),
           (3, r"\b(senior|sr\.?)\b"), (0, r"\b(intern|internship|nysc|trainee|graduate)\b"), (1, r"\b(junior|jr\.?|entry[- ]level|associate)\b")]
_YEARS = re.compile(r"\b(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years?|yrs?)\b(?:\s+of)?(?:\s+\w+){0,4}\s+experience", re.I)

WEIGHTS = {"role": 0.32, "profile": 0.23, "skills": 0.25, "seniority": 0.10, "industry": 0.05, "freshness": 0.05}
FEEDBACK_MAX_POINTS = 12.0
FEEDBACK_MIN_POINTS = -3.0  # What being unlike a reaction costs: a nudge, not a punishment.
LOCATION_DISLIKE_WEIGHT = 0.25  # "Not open to me" says the eligibility was misread, not that the job was wrong for you.
STACK_POINTS = 6.0  # Per unwanted skill a job asks for, up to two.
SENIORITY_POINTS = 8.0
OFF_DIRECTION_FACTOR = 0.65  # Title score multiplier for roles you listed as low priority.
SECONDARY_TITLE_DISCOUNT = 0.18  # On the 0-1 similarity scale. Must exceed the top of the role scale's headroom (1 - 0.88), or exact secondary matches still max out.
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_DIM = 384
_embedder = None


def embed(texts: list[str]) -> np.ndarray:
    global _embedder
    if not texts:
        return np.zeros((0, EMBED_DIM), dtype=np.float32)
    if _embedder is None:
        from fastembed import TextEmbedding
        _embedder = TextEmbedding(EMBED_MODEL)
    vecs = np.array(list(_embedder.embed(texts)), dtype=np.float32)
    return vecs / np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9)


def extract_skills(text: str) -> list[str]:
    return sorted(k for k, rx in _SKILL_RX.items() if rx.search(text))


def extract_industries(text: str) -> list[str]:
    return sorted(k for k, rx in _INDUSTRY_RX.items() if rx.search(text))


@lru_cache(maxsize=50_000)  # Job text never changes, and this runs for every job on every click.
def job_level(title: str) -> float | None:
    for level, pattern in _LEVELS:
        if re.search(pattern, title, re.I):
            return float(level)
    return None


@lru_cache(maxsize=50_000)
def required_years(text: str) -> int | None:
    found = [int(n) for n in _YEARS.findall(text) if int(n) <= 20]
    return max(found) if found else None


Vecs = tuple[np.ndarray, np.ndarray]  # Title vectors, text vectors.


@dataclass
class Dislikes:
    """The jobs you passed on, sorted by the reason you gave. Each reason pushes on a different signal."""
    general: Vecs | None = None  # No reason given: everything similar sinks.
    location: Vecs | None = None  # Similar jobs sink only a little.
    role_titles: np.ndarray | None = None  # Jobs with similar titles sink.
    stack: frozenset[str] = frozenset()  # Jobs asking for these sink, apart from skills in your profile.
    level: float | None = None  # Jobs at this level or above sink.
    years: int | None = None  # Jobs asking for this many years or more sink.


@dataclass
class Profile:
    data: dict
    title_vecs: np.ndarray  # Primary titles.
    summary_vec: np.ndarray
    secondary_vecs: np.ndarray | None = None

    @classmethod
    def load(cls, path: Path) -> "Profile":
        data = json.loads(path.read_text())
        data["skills"] = [s.lower() for s in data["skills"]]
        unknown = [s for s in data["skills"] if s not in SKILLS]
        if unknown:
            raise ValueError(f"profile.json skills missing from matching.SKILLS: {unknown}")
        return cls(data, embed(data["primary_titles"]), embed([data["summary"]])[0], embed(data.get("secondary_titles", [])))


def _scale(values: np.ndarray, low: float, high: float) -> np.ndarray:
    return np.clip((values - low) / (high - low), 0, 1)


def skill_fit(wanted: set[str], have: set[str], core: set[str] = frozenset()) -> float:
    """Coverage of the job's asks, discounted when only one or two things happen to overlap."""
    if not wanted:
        return 0.3  # The posting names no recognisable skills, so this tells us nothing either way.
    hit = len(wanted & have)
    fit = (hit / len(wanted)) ** 0.7 * min(1.0, (hit + 1) / 4)
    return fit * 0.75 if core and not wanted & core else fit  # Overlap that misses your main stack counts for less.


def seniority_fit(title: str, description: str, profile: dict) -> tuple[float, int | None]:
    level = job_level(title)
    low, high = profile["level_range"]
    gap = 0 if level is None else max(low - level, level - high, 0)  # Anywhere in your range is equally welcome.
    fit = max(0.0, 1 - 0.35 * gap)
    years = required_years(description)
    if years is not None and years > profile["years_experience"]:
        fit *= max(0.25, 1 - 0.2 * (years - profile["years_experience"]))
    return fit, years


def freshness(posted_at: str | None, now: datetime | None = None) -> float:
    if not posted_at:
        return 0.4
    now = now or datetime.now(timezone.utc)
    age = max(0.0, (now - datetime.fromisoformat(posted_at)).total_seconds() / 86400)
    return float(0.5 ** (age / 14))


def label(score: float) -> str:
    return "Strong match" if score >= 75 else "Good match" if score >= 60 else "Fair match" if score >= 45 else "Weak match"


def _affinity(titles: np.ndarray, texts: np.ndarray, reacted: Vecs | None, title_weight: float = 0.5) -> np.ndarray:
    """Points for closeness to the jobs you reacted to, relative to the whole pool.

    Averages the nearest few reactions so varied tastes don't blur together. Measuring against the pool's top
    decile means a reaction separates similar jobs from the rest instead of inflating every score.
    """
    if reacted is None or not len(reacted[1]):
        return np.zeros(len(titles))
    sims = title_weight * (titles @ reacted[0].T) + (1 - title_weight) * (texts @ reacted[1].T)
    k = min(3, sims.shape[1])
    near = np.sort(sims, axis=1)[:, -k:].mean(axis=1)
    return np.clip((near - np.percentile(near, 90)) * 60, FEEDBACK_MIN_POINTS, FEEDBACK_MAX_POINTS)


def score_jobs(jobs: list[dict], title_vecs: np.ndarray, text_vecs: np.ndarray, profile: Profile,
               liked: Vecs | None = None, disliked: Vecs | Dislikes | None = None) -> list[dict]:
    """Return one breakdown per job, in input order. Each job needs title, description, skills, industries, posted_at."""
    if not jobs:
        return []
    role_sim = (title_vecs @ profile.title_vecs.T).max(axis=1)
    if profile.secondary_vecs is not None and len(profile.secondary_vecs):
        role_sim = np.maximum(role_sim, (title_vecs @ profile.secondary_vecs.T).max(axis=1) - SECONDARY_TITLE_DISCOUNT)
    role = _scale(role_sim, 0.55, 0.88)
    fit = _scale(text_vecs @ profile.summary_vec, 0.42, 0.78)
    have, my_industries = set(profile.data["skills"]), set(profile.data["industries"])
    core = set(profile.data.get("core_skills", []))

    d = disliked if isinstance(disliked, Dislikes) else Dislikes(general=disliked)
    roles = None if d.role_titles is None else (d.role_titles, d.role_titles)
    push = (_affinity(title_vecs, text_vecs, d.general) + LOCATION_DISLIKE_WEIGHT * _affinity(title_vecs, text_vecs, d.location)
            + _affinity(title_vecs, text_vecs, roles, title_weight=1.0))
    unwanted = d.stack - have
    like = _affinity(title_vecs, text_vecs, liked)

    off_direction = [t.lower() for t in profile.data.get("low_priority_title_terms", [])]

    out = []
    for i, job in enumerate(jobs):
        wanted = set(job["skills"])
        sen, years = seniority_fit(job["title"], job["description"], profile.data)
        level = job_level(job["title"])
        too_senior = ((d.level is not None and level is not None and level >= d.level)
                      or (d.years is not None and years is not None and years >= d.years))
        against = push[i] + STACK_POINTS * min(len(wanted & unwanted), 2) + SENIORITY_POINTS * too_senior
        points = float(like[i] - np.clip(against, FEEDBACK_MIN_POINTS, FEEDBACK_MAX_POINTS))
        title_fit = float(role[i])
        if any(term in job["title"].lower() for term in off_direction):
            title_fit *= OFF_DIRECTION_FACTOR
        parts = {
            "role": title_fit, "profile": float(fit[i]), "skills": skill_fit(wanted, have, core), "seniority": sen,
            "industry": 1.0 if my_industries & set(job["industries"]) else 0.3, "freshness": freshness(job["posted_at"]),
        }
        total = 100 * sum(WEIGHTS[k] * v for k, v in parts.items())
        # A different profession can't score well on the strength of soft signals alone.
        if parts["role"] < 0.25:
            total *= 0.6 + 1.6 * parts["role"]
        total = float(total + points)
        shown = float(np.clip(total, 0, 99))
        out.append({
            # Ranking uses the uncapped value: at the 99 cap, a Like would otherwise have no effect on order.
            "score": round(shown), "rank_value": total, "label": label(shown), "parts": {k: round(v, 2) for k, v in parts.items()},
            "feedback_points": round(points, 1), "level": level, "matched_skills": sorted(wanted & have),
            "missing_skills": sorted(wanted - have), "matched_industries": sorted(my_industries & set(job["industries"])),
            "years_required": years,
        })
    return out
