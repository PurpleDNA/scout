import json
import sqlite3

import pytest

from scout import companies, outreach


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    companies.connect(conn)
    for key, note in (("a", "careers@a.ng · info@a.ng · send your CV"), ("b", "hr@b.ng"), ("c", "jobs@c.ng"), ("d", "hr@b.ng")):
        conn.execute("INSERT INTO companies (key, name, status, note) VALUES (?, ?, 'email', ?)", (key, key.upper(), note))
    return conn


def mail(to):
    return {"to": to, "subject": "Developer", "cv": "frontend", "body": "Hello,\n\nA < B & co."}


EMAILS = {"a": mail("careers@a.ng"), "b": mail("hr@b.ng"), "c": {"hold": "lawyers only"}, "d": mail("HR@b.ng"), "gone": mail("x@x.ng")}


def config(tmp_path):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF")
    return {"account": "me@outlook.com", "cvs": {"frontend": str(cv)}, "signature": ["Me"]}


def sent(*events, account="me@outlook.com"):
    return lambda verb, **_: iter([{"type": "account", "account": account}, *events])


def test_queue_skips_held_unknown_repeated_addresses_and_anything_dealt_with(db):
    assert outreach.queue(db, EMAILS) == ["a", "b"]
    companies.set_action(db, "a", "skip", False, "")
    companies.set_action(db, "b", "watching", False, "")
    assert outreach.queue(db, EMAILS) == ["b"]


def test_each_draft_is_recorded_as_it_is_saved_so_a_failure_never_repeats_one(db, tmp_path):
    def bridge(verb, job, timeout):
        assert verb == "draft" and job["account"] == "me@outlook.com"
        m = job["messages"][0]["message"]
        assert m["toRecipients"][0]["emailAddress"]["address"] == "careers@a.ng"
        assert "A &lt; B &amp; co." in m["body"]["content"] and m["attachments"][0]["contentBytes"] == "JVBERg=="
        yield {"type": "draft", "key": "a"}
        raise outreach.OutlookError("connection lost")
    with pytest.raises(outreach.OutlookError):
        outreach.draft(db, EMAILS, config(tmp_path), 10, bridge)
    assert outreach.queue(db, EMAILS) == ["b"]
    view = {c["key"]: c for c in companies.pilot_view(db)["companies"]}
    assert view["a"]["mail"]["drafted_at"] and not view["a"]["mail"]["sent_at"] and view["a"]["action"] is None
    assert view["b"]["mail"] is None


def test_draft_needs_a_cv(db):
    with pytest.raises(outreach.OutlookError, match="No frontend CV"):
        outreach.draft(db, EMAILS, {"account": "me@outlook.com", "cvs": {}, "signature": []}, 1)


def test_sync_marks_what_was_sent_with_its_date_and_keeps_your_own_marks(db):
    companies.set_action(db, "a", "watching", True, "good one")
    companies.set_action(db, "c", "applied", False, "")
    found = outreach.sync(db, "me@outlook.com", sent(
        {"type": "sent", "to": "Careers@A.ng", "subject": "Hi", "at": "2026-11-20T09:00:00Z"},  # PowerShell unwraps one-item lists.
        {"type": "sent", "to": ["jobs@c.ng", "friend@x.com"], "subject": "Yo", "at": "2026-11-21T09:00:00Z"},
        {"type": "sent", "to": ["careers@a.ng"], "subject": "Follow-up", "at": "2026-11-22T09:00:00Z"}))
    assert found == ["a", "c"]
    view = {c["key"]: c for c in companies.pilot_view(db)["companies"]}
    assert (view["a"]["action"], view["a"]["star"], view["a"]["note"]) == ("emailed", True, "good one")
    assert view["a"]["acted_at"] == view["a"]["mail"]["sent_at"] == "2026-11-20T09:00:00+00:00"
    assert view["c"]["action"] == "applied" and view["c"]["mail"]["subject"] == "Yo"
    assert outreach.sync(db, "me@outlook.com", sent({"type": "sent", "to": ["careers@a.ng"], "subject": "x", "at": "2026-11-23T09:00:00Z"})) == []


def test_sync_ignores_general_mailboxes_unless_we_drafted_to_them(db):
    assert outreach.sync(db, "me@outlook.com", sent({"type": "sent", "to": ["info@a.ng"], "subject": "Complaint", "at": "2026-11-20T09:00:00Z"})) == []


def test_sync_refuses_another_account(db):
    with pytest.raises(outreach.OutlookError, match="expected me@outlook.com"):
        outreach.sync(db, "me@outlook.com", sent(account="other@outlook.com"))
