"""Cold emails to companies that ask for CVs by email.

The texts live in data/outreach/emails.json, one per company, or a reason to hold it back.
draft    save the next few as drafts in Outlook.com, CV attached. You read and send them yourself
sync     look through Sent Items and mark what went out as Emailed on /pilot

Nothing here can send: the Outlook bridge only asks Microsoft for permission to write drafts and read mail.
"""
from __future__ import annotations

import base64
import html
import json
import re
import sqlite3
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Timer

from . import companies

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "outlook.ps1"
MAX_CV = 2_500_000  # Graph takes attachments inside the draft up to about 3 MB.
DAILY = 10
SYNC_DAYS = 365  # Far enough back to catch what you sent by hand before Scout did this.
CLOSED = ("emailed", "applied", "skip")
# Kept apart from companies, like company_actions, so recrawling never touches it.
SCHEMA = """
CREATE TABLE IF NOT EXISTS outreach (
  key TEXT PRIMARY KEY, address TEXT NOT NULL, subject TEXT, drafted_at TEXT, sent_at TEXT
);
"""


class OutlookError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- the Outlook bridge ----------
def _windows(path: Path) -> str:
    return subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, check=True).stdout.strip()


def outlook(verb: str, job: dict | None = None, since: str | None = None, timeout: int = 120) -> Iterator[dict]:
    """Run the bridge and yield its events as they come, so each saved draft can be recorded at once."""
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", _windows(SCRIPT), verb]
    with tempfile.TemporaryDirectory() as tmp:
        if job is not None:
            (Path(tmp) / "job.json").write_text(json.dumps(job))
            cmd += ["-InFile", _windows(Path(tmp) / "job.json")]
        if since:
            cmd += ["-Since", since]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, cwd="/mnt/c")
        timer = Timer(timeout, p.kill)
        timer.start()
        finished = False
        try:
            for raw in p.stdout:
                try:
                    event = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    if "enter the code" in (line := raw.decode("utf-8", "replace").strip()).lower() and verb != "login":
                        raise OutlookError("Not signed in to Outlook. Run `uv run scout outreach login`.") from None
                    if line and verb == "login":
                        print(line, flush=True)
                    continue
                if event.get("type") == "error":
                    raise OutlookError(event["error"])
                if event.get("type") == "done":
                    finished = True
                    continue
                yield event
        finally:
            timer.cancel()
            p.kill()
            p.wait()
        if not finished:
            raise OutlookError("Outlook didn't answer in time. If it keeps happening, run `uv run scout outreach login`.")


# ---------- what goes out ----------
def body_html(text: str, signature: list[str]) -> str:
    paras = "".join(f"<p>{html.escape(p.strip())}</p>" for p in text.strip().split("\n\n"))
    return f'<div style="font-family:Calibri,Arial,sans-serif;font-size:11pt">{paras}<p>{"<br>".join(signature)}</p></div>'


def message(entry: dict, config: dict, cv: Path) -> dict:
    return {
        "subject": entry["subject"],
        "body": {"contentType": "HTML", "content": body_html(entry["body"], config["signature"])},
        "toRecipients": [{"emailAddress": {"address": entry["to"]}}],
        "attachments": [{"@odata.type": "#microsoft.graph.fileAttachment", "name": config.get("cv_name") or cv.name,
                         "contentType": "application/pdf", "contentBytes": base64.b64encode(cv.read_bytes()).decode()}],
    }


def rows(db: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'outreach'").fetchone():
        return {}  # A read-only connection can't create it.
    return {r["key"]: r for r in db.execute("SELECT * FROM outreach")}


def queue(db: sqlite3.Connection, emails: dict) -> list[str]:
    """Companies still to draft: not held, not drafted or sent, not dealt with on /pilot, one per address."""
    done, acts = rows(db), companies._actions(db)
    known = {r["key"] for r in db.execute("SELECT key FROM companies")}
    used = {r["address"].lower() for r in done.values()}
    out = []
    for key, e in emails.items():
        if "hold" in e or key not in known or key in done or (key in acts and acts[key]["action"] in CLOSED) or e["to"].lower() in used:
            continue
        used.add(e["to"].lower())
        out.append(key)
    return out


def cv_for(entry: dict, config: dict) -> Path:
    """The CV that backs up what this email says."""
    cv = Path(config["cvs"].get(entry["cv"], ""))
    if not cv.is_file():
        raise OutlookError(f"No {entry['cv']} CV at {cv}. Fix its path under \"cvs\" in data/outreach/config.json.")
    if cv.stat().st_size > MAX_CV:
        raise OutlookError(f"{cv.name} is too big to attach ({cv.stat().st_size / 1e6:.1f} MB).")
    return cv


def draft(db: sqlite3.Connection, emails: dict, config: dict, limit: int, bridge: Callable = outlook) -> int:
    db.executescript(SCHEMA)
    todo = queue(db, emails)[:limit]
    if not todo:
        return 0
    job = {"account": config["account"],
           "messages": [{"key": k, "message": message(emails[k], config, cv_for(emails[k], config))} for k in todo]}
    saved = 0
    for event in bridge("draft", job=job, timeout=60 + 15 * len(todo)):
        if event["type"] == "draft":
            e = emails[event["key"]]
            db.execute("INSERT OR REPLACE INTO outreach (key, address, subject, drafted_at) VALUES (?,?,?,?)",
                       (event["key"], e["to"], e["subject"], now()))
            db.commit()  # One at a time: a failure halfway must not lead to a second draft for the same company.
            saved += 1
    return saved


# ---------- what went out ----------
def addresses(db: sqlite3.Connection) -> dict[str, list[str]]:
    """Address -> companies it belongs to. Hiring mailboxes and ones we drafted to: an email to info@ may be about anything."""
    out: dict[str, list[str]] = {}
    for r in db.execute("SELECT key, note FROM companies WHERE status = 'email'"):
        for a in companies._EMAIL_ADDR.findall(r["note"] or ""):
            if companies._HIRING_MAILBOX.search(a.split("@")[0]):
                out.setdefault(a.lower(), []).append(r["key"])
    for r in rows(db).values():
        if r["key"] not in out.setdefault(r["address"].lower(), []):
            out[r["address"].lower()].append(r["key"])
    return out


def sync(db: sqlite3.Connection, account: str, bridge: Callable = outlook) -> list[str]:
    """Mark companies you have emailed, with the date it was sent. Returns the ones newly found."""
    db.executescript(SCHEMA)
    known, log, acts = addresses(db), rows(db), companies._actions(db)
    since = (datetime.now(timezone.utc) - timedelta(days=SYNC_DAYS)).strftime("%Y-%m-%dT00:00:00Z")
    found = []
    for event in bridge("sent", since=since):
        if event["type"] == "account" and event["account"].lower() != account.lower():
            raise OutlookError(f"Signed in as {event['account']}, expected {account}.")
        if event["type"] != "sent":
            continue
        to = event.get("to") or []
        for address in ([to] if isinstance(to, str) else to):
            for key in known.get((address or "").lower(), []):
                if key in found or (key in log and log[key]["sent_at"]):
                    continue
                at = re.sub(r"(\.\d+)?Z$", "+00:00", event["at"])
                db.execute("""INSERT INTO outreach (key, address, subject, sent_at) VALUES (?,?,?,?)
                              ON CONFLICT(key) DO UPDATE SET address=excluded.address, subject=excluded.subject, sent_at=excluded.sent_at""",
                           (key, address.lower(), event.get("subject") or "", at))
                a = acts.get(key)
                if not a or a["action"] not in CLOSED:  # What you marked yourself stays.
                    companies.set_action(db, key, "emailed", bool(a and a["star"]), (a["note"] if a else "") or "", when=at)
                db.commit()
                found.append(key)
    return found
