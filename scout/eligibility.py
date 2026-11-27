"""Can someone living in Lagos actually take this job?

This is the piece Jobright doesn't have: its location model is a hardcoded six-country
enum. Everything here works on the messy location text real feeds publish, and each
decision records the evidence so a wrong call is easy to spot and fix.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

HOME_UTC_OFFSET = 1  # Lagos


@dataclass(frozen=True)
class Eligibility:
    lane: str  # global_remote | nigeria (Lagos in any setup, plus remote roles anywhere in Nigeria) | rejected
    work_model: str  # remote | hybrid | onsite
    scope: str  # worldwide | nigeria | africa | emea | lagos | restricted | elsewhere | unknown. Lagos jobs are scope lagos.
    confidence: str  # high | medium | low
    evidence: str
    flags: tuple[str, ...] = field(default_factory=tuple)


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.I)


# Lagos and the areas listings name on their own. Names shared with places abroad (Maryland, Marina) are left out:
# they only count alongside “Lagos”, which already matches. So is Epe, a Dutch town too.
LAGOS = _rx(
    r"\b(lagos|ikeja|ikoyi|lekki|victoria island|yaba|surulere|apapa|ajah|oshodi|gbagada|ikorodu|festac|agege|ojota|"
    r"ilupeju|isolo|oregun|magodo|ogba|ojodu|ketu|mushin|ebute[- ]metta|obalende|oniru|sangotedo|badagry|alimosho|"
    r"egbeda|iyana[- ]ipaja|opebi|alausa|amuwo[- ]odofin|ajao estate|osapa|ilasan|ikota|eko atlantic|"
    r"satellite town|ojo|ipaja|ogudu|anthony village|palmgrove|idimu|ikotun|igando|abule[- ]egba|ijora|idumota)\b")
NIGERIA = _rx(r"\bnigeria\b")
# Other cities and states. Ambiguous names need their qualifier: “Benin City”, “Rivers State”, “Delta State”,
# “Niger State” and “Plateau State” (Benin, Niger and a plain “Delta” are elsewhere too).
NIGERIAN_PLACE = _rx(
    r"\b(nigeria|abuja|fct|federal capital territory|port[- ]harcourt|ibadan|kano|kaduna|enugu|benin city|warri|owerri|"
    r"abeokuta|ilorin|jos|uyo|calabar|onitsha|asaba|akure|osogbo|oshogbo|ogbomoso|ogbomosho|sokoto|maiduguri|yola|bauchi|"
    r"minna|lokoja|makurdi|umuahia|awka|abakaliki|yenagoa|ado[- ]ekiti|gombe|jalingo|katsina|lafia|dutse|damaturu|"
    r"birnin[- ]kebbi|gusau|sapele|sagamu|shagamu|ota|ijebu[- ]ode|nnewi|eket|bonny|onne|ikot ekpene|zaria|suleja|"
    r"oyo|ogun|osun|ondo|ekiti|kwara|kogi|benue|edo|imo|abia|anambra|ebonyi|bayelsa|akwa ibom|cross river|nasarawa|"
    r"taraba|adamawa|borno|yobe|jigawa|kebbi|zamfara|rivers state|delta state|niger state|plateau state)\b")


@dataclass
class Area:
    """Where on-site and hybrid work is open to you: your home city, plus anywhere you said you'd move to."""
    label: str = "Lagos"
    home: re.Pattern = LAGOS
    also: re.Pattern | None = None  # Other places you'd move to.
    anywhere: bool = False  # Anywhere in Nigeria.

    @property
    def slug(self) -> str:
        return self.label.lower()


def _places(names: list[str]) -> re.Pattern:
    parts = [LAGOS.pattern if n.strip().lower() == "lagos" else rf"\b{re.escape(n.strip())}\b" for n in names if n.strip()]
    return _rx("|".join(f"(?:{p})" for p in parts))


def area_for(home: str = "Lagos", relocate: str | list[str] = "no") -> Area:
    """`relocate` is "no", "anywhere" (in Nigeria) or a list of places."""
    home = home.strip() or "Lagos"
    also = relocate if isinstance(relocate, list) and relocate else None
    return Area(home, _places([home]), _places(also) if also else None, relocate == "anywhere")


AREA = Area()


def set_area(area: Area) -> None:
    global AREA
    AREA = area


WORLDWIDE = _rx(r"\b(world\s?wide|anywhere|global(ly)?|international)\b")
AFRICA = _rx(r"(?<!south )(?<!north )(?<!east )(?<!west )\bafrica\b|\bsub-saharan\b")
EMEA = _rx(r"\bemea\b")

# After removing the words below, whatever remains in a location label names a place.
_NOT_A_PLACE = _rx(r"\b(remote|hybrid|on-?site|in-?office|home[- ]based|work from home|wfh|full[- ]?time|only|based|location|flexible|tbd|n/?a)\b")

HYBRID_TEXT = _rx(r"\bhybrid\b(?! cloud| app| mobile| model of| approach)")
REMOTE_TEXT = _rx(r"\b(fully[- ]remote|100% remote|remote[- ](role|position|job|opportunity|work)|work(ing)? from home|location:\s*remote)\b")

# Case-sensitive abbreviations, so “contact us only” is never read as “US only”.
_PLACES = (
    r"(the\s+)?((?-i:US|USA|U\.S\.A?\.?|UK|U\.K\.|EU|E\.U\.|APAC|LATAM)|united states|north america|canada|united kingdom|"
    r"great britain|england|ireland|australia|new zealand|europe|european union|germany|france|spain|poland|portugal|"
    r"netherlands|india|philippines|pakistan|latin america|south america|brazil|mexico|argentina|colombia|asia|"
    r"singapore|japan|israel|south africa|kenya|egypt)(?![A-Za-z])"
)
_WHO = r"(candidates?|applicants?|residents?)"
RESTRICTIONS = [
    _rx(rf"\b(must|need to|required to|have to)\s+(be\s+)?(currently\s+)?(based|located|residing|reside|live|living)\s+(remotely\s+)?(in|within)\s+{_PLACES}"),
    _rx(rf"\b(authori[sz]ed|eligible|right|legally able|permitted)\s+to\s+work\s+(in|within|for)\s+{_PLACES}"),
    _rx(rf"{_PLACES}[- ]based\s+{_WHO}\s+only\b"),
    _rx(rf"\b(looking for|seeking|hiring|open to)\s+(an?\s+)?{_PLACES}[- ]based\b"),
    _rx(rf"\b{_WHO}\s+(located\s+|based\s+)?(in|from|of)\s+{_PLACES}\s+(only\b|will be considered)"),
    _rx(rf"\bonly\s+{_WHO}\s+(located\s+|based\s+)?(in|from|of)\s+{_PLACES}"),
    _rx(rf"\b(for|seeking|hiring)\s+{_WHO}\s+(located|based)\s+in\s+{_PLACES}"),
    _rx(rf"\b(role|position|job|opportunity)\s+(is\s+|will\s+(need\s+to\s+)?be\s+)?(\w+\s+)?based\s+(remotely\s+)?(in|within)\s+{_PLACES}"),
    _rx(rf"\b{_WHO}\s+(located|based)\s+in\s+the\s+(pacific|mountain|eastern|central)\b.{{0,20}}time\s?zones?"),
    re.compile(rf"(?:^|\n|[.:;]\s*)Based in (?i:{_PLACES})"),
    re.compile(rf"(?<![A-Za-z])(?:(?-i:US|USA|UK|EU)|(?i:united states|canada|united kingdom|north america|europe))\s+(?i:(residents?\s+)?only)\b"),
    re.compile(r"(?<![A-Za-z])(?:(?:US|USA|U\.S\.|UK)|(?i:united states|canadian|british))\s+(?i:citizens?(hip)?)\b"),
    _rx(rf"\b(you are|you're|must be)\s+based\s+in\s+{_PLACES}"),
    _rx(rf"\b(role|position|job)\b.{{0,25}}\brequires?\s+you\s+to\s+be\s+based\s+in\s+{_PLACES}"),
    _rx(rf"\b(remote\s+(position|role|job)\s+)within\s+{_PLACES}"),
    _rx(rf"\bremote\s+within\s+{_PLACES}"),
    _rx(rf"\bopen\s+to\s+(remote\s+)?{_WHO}\s+based\s+in\s+{_PLACES}"),
    _rx(rf"\b{_WHO}\s+who\s+(reside|live)\s+in\s+{_PLACES}"),
    _rx(r"\bsecurity clearance\b"),
    _rx(r"\b(not|unable to)\s+(open|available|hiring)\s+(to|in)\s+(candidates\s+(in|from)\s+)?(africa|nigeria)\b"),
]
# “Pay for US-based staff is X” is a carve-out, and implies people elsewhere are hired too.
_PAY_CONTEXT = _rx(r"\b(pay|compensation|salary|salaries|range|benefits?|insurance|bonus|equity|union|level)\b")
# “Ideally based in Poland” states a preference, which doesn't rule anyone out.
_SOFT_CONTEXT = _rx(r"\b(ideally|preferably|preferred|preference|plus(?! or minus)|nice to have)\b")
_REGION_IN_TITLE = re.compile(r"\b(Europe|European|EU|CET|LATAM|DACH|Nordics?|Benelux|APAC|US|USA|UK|U\.S\.|Canada|North America|Americas)(?![A-Za-z])")
# Himalayas expands a region into its countries. A long list naming Nigeria means “EMEA”, not “hiring in Nigeria”.
_EXPLICIT_LIST_MAX = 20
_NEAR_WORLDWIDE_MIN = 150
WORLDWIDE_TEXT = _rx(
    r"\b(work(ing)? from anywhere|anywhere in the world|hir(e|es|ing) (globally|worldwide|from anywhere)|"
    r"open to (candidates|applicants|talent)? ?(worldwide|globally|from anywhere)|global(ly)? remote|remote[- ]first,? (global|worldwide)|"
    r"(fully )?distributed (global|worldwide) team)\b"
)
AFRICA_TEXT = _rx(r"\b(candidates?|applicants?|talent|engineers?|developers?|based|hiring)\b.{0,40}\b(africa|nigeria)\b")
US_HOURS = re.compile(
    r"(?i:overlap|hours?|time ?zones?|working|availab\w+|between|available in)\b.{0,40}"
    r"(?<![a-zA-Z])(?:US|U\.S\.|EST|EDT|PST|PDT|CST|ET|PT|(?i:eastern|pacific|north american?))(?![a-zA-Z])"
)


def _sentence_around(text: str, start: int, end: int) -> str:
    """The sentence holding a match. “U.S.-based” doesn't end one: a boundary needs whitespace and a capital."""
    left = 0
    for m in re.finditer(r"[.!?]\s+(?=[A-Z])", text[:start]):
        left = m.end()
    right = re.search(r"[.!?](\s|$)", text[end:])
    return text[left:end + right.end()] if right else text[left:]


def _first_restriction(text: str) -> str | None:
    for rx in RESTRICTIONS:
        for m in rx.finditer(text):
            sentence = _sentence_around(text, m.start(), m.end())
            if _PAY_CONTEXT.search(sentence) or _SOFT_CONTEXT.search(sentence):
                continue
            return " ".join(text[max(0, m.start() - 30):m.end() + 30].split())
    return None


def _names_a_place(label: str) -> bool:
    return bool(re.search(r"[a-z]{2}", _NOT_A_PLACE.sub(" ", label), re.I))


def _work_model(location: str, workplace: str | None, description: str, remote_board: bool, local: bool) -> str:
    if workplace:
        w = workplace.lower().replace("-", "").replace(" ", "")
        if w in ("remote", "hybrid", "onsite"):
            return w
    if re.search(r"\bhybrid\b", location, re.I):
        return "hybrid"
    if re.search(r"\b(remote|home[- ]based|work from home|wfh)\b", location, re.I):
        return "remote"
    if remote_board:
        return "remote"
    if local:
        # Boards rarely label this, so read the posting, but only trust phrasing about the job itself.
        if REMOTE_TEXT.search(description):
            return "remote"
        if HYBRID_TEXT.search(description):
            return "hybrid"
    return "onsite"


def _covers_home_tz(timezones: list[float] | None) -> bool:
    return not timezones or HOME_UTC_OFFSET in timezones


def classify(*, location: str = "", workplace: str | None = None, restrictions: list[str] | None = None,
             timezones: list[float] | None = None, description: str = "", title: str = "",
             remote_board: bool = False) -> Eligibility:
    location = location or ""
    area = AREA
    in_lagos = bool(area.home.search(location))  # Your home city: Lagos unless your profile says otherwise.
    in_nigeria = in_lagos or bool(LAGOS.search(location)) or bool(NIGERIAN_PLACE.search(location))
    would_move = in_nigeria and not in_lagos and (area.anywhere or bool(area.also and area.also.search(location)))
    model = _work_model(location, workplace, description, remote_board, in_nigeria)
    flags = ("us_hours",) if US_HOURS.search(description) else ()

    # Local remote: an employer naming a Nigerian place is hiring where you live, so it belongs in the Nigeria lane.
    if model == "remote" and restrictions is None and in_nigeria:
        if _covers_home_tz(timezones):
            return Eligibility("nigeria", model, area.slug if in_lagos else "nigeria", "high", f"Location: “{location}”", flags)

    # Going into an office means living near it, so on-site and hybrid work only counts at home, or where you'd move.
    if model != "remote":
        if in_lagos or would_move:
            evidence = f"Location: “{location}”" + ("" if in_lagos else f", outside {area.label}, where you said you'd move")
            if model == "onsite" and not workplace:
                evidence += ". The listing doesn't mention remote or hybrid, so check the posting."
            return Eligibility("nigeria", model, area.slug if in_lagos else "nigeria", "high", evidence, flags)
        outside = area.label if not (area.also or area.anywhere) else f"{area.label} and the places you'd move to"
        return Eligibility("rejected", model, "nigeria" if in_nigeria else "elsewhere", "high",
                           f"{model.capitalize()} outside {outside}: “{location or 'no location given'}”", flags)

    def rejected(scope: str, why: str) -> Eligibility:
        return Eligibility("rejected", model, scope, "high", why, flags)

    if not _covers_home_tz(timezones):
        return rejected("restricted", "Time zones exclude UTC+1")

    # Named eligibility: what the posting's structured data or location label says.
    if restrictions is not None:
        joined = ", ".join(restrictions)
        if not restrictions:
            scope, confidence, evidence = "worldwide", "high", "No location restrictions listed"
        elif not any(NIGERIA.search(r) for r in restrictions):
            return rejected("restricted", f"Limited to {joined[:90]}")
        elif len(restrictions) <= _EXPLICIT_LIST_MAX:
            scope, confidence, evidence = "nigeria", "high", f"Eligible countries include Nigeria ({joined[:80]})"
        elif len(restrictions) >= _NEAR_WORLDWIDE_MIN:
            scope, confidence, evidence = "worldwide", "medium", f"Open to {len(restrictions)} countries, including Nigeria"
        else:
            scope, confidence, evidence = "emea", "medium", f"Region-wide list of {len(restrictions)} countries that includes Nigeria, which may really mean Europe"
    elif AFRICA.search(location):
        scope, confidence, evidence = "africa", "high", f"Location: “{location}”"
    elif WORLDWIDE.search(location):
        scope, confidence, evidence = "worldwide", "high", f"Location: “{location}”"
    elif EMEA.search(location):
        # EMEA nominally covers Nigeria, but many employers really mean Europe.
        scope, confidence, evidence = "emea", "medium", f"Location: “{location}” (EMEA may really mean Europe)"
    elif _names_a_place(location):
        return rejected("restricted", f"Limited to “{location}”")
    else:
        scope, confidence, evidence = "unknown", "low", "Says remote, but doesn't say where"

    # The title and prose can overrule the label: “Worldwide” postings often say “US only” further down.
    if scope != "nigeria":
        named = _REGION_IN_TITLE.search(title)
        if named:
            return rejected("restricted", f"Title names a region: “{title[:80]}”")
        restriction = _first_restriction(description)
        if restriction:
            return rejected("restricted", f"Posting says: “{restriction}”")

    if scope == "unknown":
        if WORLDWIDE_TEXT.search(description):
            scope, confidence, evidence = "worldwide", "medium", "Posting describes hiring worldwide"
        elif AFRICA_TEXT.search(description):
            scope, confidence, evidence = "africa", "medium", "Posting mentions hiring in Africa"

    return Eligibility("global_remote", model, scope, confidence, evidence, flags)
