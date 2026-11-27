"""`scout setup`: a few questions in the terminal, and your CV, turned into profile.json.

Your CV is read on this computer only. Nothing you type here is sent anywhere.
"""
from __future__ import annotations

import json
import re
import shutil
import zipfile
from pathlib import Path

from .fields import FIELDS
from .matching import INDUSTRIES, SKILL_GROUPS, SKILLS, extract_skills

LEVELS = [
    ("Intern, NYSC or graduate trainee", 0),
    ("Entry level or junior (under 2 years)", 1),
    ("Mid-level (about 2 to 5 years)", 2),
    ("Senior (5 years or more)", 3),
    ("Lead or manager of a team", 4),
    ("Head of department or director", 5),
]
# Phone numbers have 10 or more digits, which keeps date ranges such as "2020 - 2022" in.
_CONTACT = re.compile(r"\S+@\S+|\+?(?:\d[\s().-]{0,2}){9,14}\d|https?://\S+|www\.\S+|linkedin\.com\S*")


# ---------- reading the CV ----------
def read_cv(path: Path) -> str:
    """Text from a PDF, Word (.docx) or plain-text CV."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    if suffix == ".docx":
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        xml = re.sub(r"</w:p>", "\n", xml)
        return re.sub(r"<[^>]+>", "", xml)
    if suffix in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="ignore")
    raise ValueError("Scout can read a CV saved as PDF, Word (.docx) or text. Save it as a PDF and try again.")


def summary_from_cv(text: str, limit: int = 1200) -> str:
    """The CV without contact details, squeezed to one paragraph. Used when you'd rather not write a summary."""
    return " ".join(_CONTACT.sub(" ", text).split())[:limit]


def split_list(answer: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,;\n]", answer) if x.strip()]


# ---------- the profile itself ----------
def build_profile(a: dict, cv_text: str = "") -> dict:
    """Turn the answers into profile.json's shape. `a` holds what `run` asked; this does no I/O, so it can be tested."""
    groups = sorted({FIELDS[f].skills for f in a["fields"]})
    known = set().union(*(SKILL_GROUPS[g] for g in groups))
    skills = [s for s in a["skills"] if s in SKILLS]
    custom = [s for s in a["skills"] if s not in SKILLS]
    level = a["level"]
    return {
        "name": a["name"],
        "fields": a["fields"],
        "skill_groups": groups,
        "primary_titles": a["titles"],
        "secondary_titles": a.get("other_titles", []),
        "low_priority_title_terms": [t.lower() for t in a.get("avoid", [])],
        "summary": a.get("summary") or summary_from_cv(cv_text) or ". ".join(a["titles"]),
        "skills": [s for s in skills if s in known] + [s for s in skills if s not in known],
        "custom_skills": custom,
        "core_skills": a.get("core", []),
        "years_experience": a["years"],
        "level_range": [max(0, level - 1), min(5, level + 1)],
        "industries": a.get("industries", []),
        "exclude_companies": a.get("exclude", []),
        "home_city": a["home"],
        "relocate": a["relocate"],
        "global_remote": a["global_remote"],
    }


# ---------- asking ----------
def ask(question: str, default: str = "", required: bool = False) -> str:
    hint = f" [{default}]" if default else ""
    while True:
        answer = input(f"\n{question}{hint}\n> ").strip()
        if answer or not required or default:
            return answer or default
        print("Please type an answer.")


def ask_yes(question: str, default: bool) -> bool:
    while True:
        answer = ask(question + " (y/n)", "y" if default else "n").lower()
        if answer[:1] in ("y", "n"):
            return answer.startswith("y")
        print("Type y for yes or n for no.")


def ask_number(question: str, low: int, high: int, default: int | None = None) -> int:
    while True:
        answer = ask(question, "" if default is None else str(default), required=True)
        if answer.isdigit() and low <= int(answer) <= high:
            return int(answer)
        print(f"Type a number from {low} to {high}.")


def ask_choices(question: str, options: list[str], many: bool, default: list[int] | None = None) -> list[int]:
    """Numbered menu. Returns the chosen positions, counting from 0."""
    print(f"\n{question}")
    for i, option in enumerate(options, 1):
        print(f"  {i:2}. {option}")
    how = "Type the numbers, separated by commas" if many else "Type a number"
    fallback = ",".join(str(i + 1) for i in default) if default else ""
    while True:
        answer = ask(how + ".", fallback, required=not many)
        if not answer:
            return []
        picks = [p for p in re.split(r"[,\s]+", answer) if p]
        if all(p.isdigit() and 1 <= int(p) <= len(options) for p in picks) and (many or len(picks) == 1):
            return list(dict.fromkeys(int(p) - 1 for p in picks))
        print(f"Use numbers from 1 to {len(options)}.")


def ask_cv() -> tuple[str, str]:
    """Returns (path, text). An empty path means no CV."""
    print("\nWhere is your CV? Drag the file into this window and press Enter, or press Enter to skip.")
    while True:
        answer = input("> ").strip().strip('"').strip("'").strip()
        if not answer:
            return "", ""
        path = Path(answer).expanduser()
        if not path.is_file():
            print("Scout can't find that file. Drag it in again, or press Enter to skip.")
            continue
        try:
            text = read_cv(path)
        except Exception as e:  # A damaged or scanned file shouldn't end setup.
            print(f"Scout couldn't read that file ({e}). Try a PDF, or press Enter to skip.")
            continue
        if len(text.split()) < 30:
            print("That file has almost no text Scout can read: it may be a scanned picture. Scout will rely on your answers instead.")
        return str(path), text


def run(profile_path: Path) -> bool:
    print("=" * 70)
    print("Scout setup. A few questions, then Scout knows what jobs to look for.")
    print("Press Enter to accept the answer shown in [brackets]. Your answers stay on this computer.")
    print("=" * 70)
    old = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.exists() else {}
    if old and not ask_yes(f"You already have a profile for {old.get('name') or 'someone'}. Replace it?", False):
        return False

    a: dict = {"name": ask("What's your name?", old.get("name", ""), required=True)}
    _, cv_text = ask_cv()

    keys = list(FIELDS)
    picked = ask_choices("What kind of work do you want? You can pick more than one.",
                         [FIELDS[k].label for k in keys], many=True,
                         default=[keys.index(k) for k in old.get("fields", []) if k in keys] or None)
    a["fields"] = [keys[i] for i in picked] or ["sales"]
    examples = "; ".join(FIELDS[k].examples for k in a["fields"])
    a["titles"] = split_list(ask(f"Which job titles do you want most? Separate them with commas.\n(For example: {examples})",
                                 ", ".join(old.get("primary_titles", [])), required=True))
    a["other_titles"] = split_list(ask("Any other titles you'd also take, though they're not your first choice? (Enter to skip)",
                                       ", ".join(old.get("secondary_titles", []))))
    a["avoid"] = split_list(ask("Any words in a job title that mean it's NOT for you? For example: driver, intern, cashier. (Enter to skip)",
                                ", ".join(old.get("low_priority_title_terms", []))))

    a["level"] = LEVELS[ask_choices("What level are you now?", [l for l, _ in LEVELS], many=False,
                                    default=[2])[0]][1]
    a["years"] = ask_number("How many years of work experience do you have?", 0, 50, old.get("years_experience"))

    vocab = set().union(*(SKILL_GROUPS[FIELDS[f].skills] for f in a["fields"]))
    found = extract_skills(cv_text, vocab) if cv_text else []
    if found:
        print(f"\nFrom your CV, Scout found these skills:\n  {', '.join(found)}")
        drop = split_list(ask("Any of those that are wrong? Type them to remove them. (Enter to keep all)"))
        found = [s for s in found if s not in {d.lower() for d in drop}]
    print("\nSkills Scout recognises for your kind of work include:\n  " + ", ".join(sorted(vocab)[:60]))
    extra = split_list(ask("Add any other skills you have, separated by commas. Anything not on that list is fine too. (Enter to skip)"))
    a["skills"] = list(dict.fromkeys(found + [" ".join(e.lower().split()) for e in extra]))
    if a["skills"]:
        strongest = ask_choices("Which of these are your strongest? Jobs asking for none of them score a little lower. (Enter to skip)",
                                a["skills"], many=True)
        a["core"] = [a["skills"][i] for i in strongest]

    a["summary"] = ask("In two or three sentences, what have you done and what do you want next?\n"
                       "(Enter to let Scout use your CV instead)" if cv_text else
                       "In two or three sentences, what have you done and what do you want next?", required=not cv_text)

    industries = sorted(INDUSTRIES)
    a["industries"] = [industries[i] for i in ask_choices("Which industries would you most like to work in? (Enter to skip)",
                                                         industries, many=True)]

    a["home"] = ask("Which city do you live in?", old.get("home_city", "Lagos"), required=True)
    move = ask_choices(f"Would you move to another city for the right on-site or hybrid job?",
                       [f"No, only jobs in {a['home']} (or remote)", "Yes, anywhere in Nigeria", "Only to certain cities"], many=False,
                       default=[0])[0]
    a["relocate"] = ["no", "anywhere", None][move]
    if a["relocate"] is None:
        a["relocate"] = split_list(ask("Which cities? Separate them with commas. For example: Abuja, Port Harcourt", required=True))
    a["global_remote"] = ask_yes("Do you also want remote jobs with companies outside Nigeria? "
                                 "Most need very strong experience, and they make each day's fetch slower.",
                                 old.get("global_remote", False))
    a["exclude"] = split_list(ask("Any companies to leave out, such as where you work now? (Enter to skip)",
                                  ", ".join(old.get("exclude_companies", []))))

    profile = build_profile(a, cv_text)
    if profile_path.exists():
        shutil.copy(profile_path, profile_path.with_suffix(".json.bak"))
    profile_path.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("\n" + "=" * 70)
    print(f"Saved. Scout will look for: {', '.join(profile['primary_titles'])}")
    where = {"no": f"in {a['home']}", "anywhere": f"in {a['home']} or anywhere in Nigeria"}.get(
        a["relocate"] if isinstance(a["relocate"], str) else "", f"in {a['home']} or {', '.join(a['relocate'])}")
    print(f"On-site and hybrid jobs {where}, plus remote jobs in Nigeria" + (" and abroad." if a["global_remote"] else "."))
    print("To change any answer later, run setup again.")
    print("=" * 70)
    return True
