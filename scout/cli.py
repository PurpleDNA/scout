from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import programmes, store
from .sources import SOURCES

ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get("SCOUT_DB", ROOT / "data" / "scout.db"))  # Point this at a copy to test without touching your likes.
PILOT_DB = Path(os.environ.get("SCOUT_PILOT_DB", ROOT / "data" / "pilot.db"))  # Company discovery, shown at /pilot.
PROFILE = ROOT / "profile.json"


def cmd_ingest(args) -> int:
    db = store.connect(DB)
    names = [n for n in args.only if n in SOURCES] if args.only else list(SOURCES)
    failed = []
    for name in names:
        started = time.time()
        jobs, error = [], None
        try:
            for job in SOURCES[name]():
                jobs.append(job)
        except Exception as e:  # A dead source shouldn't lose what it already returned, or block the rest.
            error = e
            failed.append(name)
        store.save(db, jobs)
        if not error and jobs:  # An empty answer is more likely a block or an outage than every listing closing at once.
            store.mark_fetched(db, name, store.now_iso())
        note = f"  PARTIAL/FAILED: {error!r}"[:140] if error else ""
        print(f"{name:16}{len(jobs):6} jobs  {time.time() - started:5.1f}s{note}", flush=True)
    if not args.only:  # A source taken out of SOURCES never fetches again, so its listings start closing from today.
        for gone in set(store.source_clocks(db)) - set(SOURCES):
            store.mark_fetched(db, gone, store.now_iso())
    if not args.only or "programmes" in args.only:
        started = time.time()
        try:
            fetched, new = programmes.refresh(db)
            print(f"{'programmes':16}{fetched:6} found {new:4} new  {time.time() - started:5.1f}s", flush=True)
        except Exception as e:
            failed.append("programmes")
            print(f"{'programmes':16}  FAILED: {e!r}"[:140], flush=True)
    print(f"cleanup: {store.purge_old(db)} jobs and {programmes.cleanup(db)} programmes held over {store.MAX_AGE_DAYS} days removed", flush=True)
    print("classifying and embedding...", flush=True)
    print(store.reprocess(db))
    return 1 if failed else 0


def cmd_reprocess(_) -> int:
    print(store.reprocess(store.connect(DB)))
    return 0


def cmd_top(args) -> int:
    from .ranker import Ranker
    data = Ranker(store.connect(DB), PROFILE).recommend(lane=args.lane, limit=args.n, verified_only=not args.unverified)
    print(f"{data['total']} matches  {data['counts']}")
    for j in data["jobs"]:
        print(f"{j['score']:3}  {j['title'][:50]:50} {j['company'][:22]:22} {j['work_model']:7}{j['scope']:10} {j['source'][:10]}")
    return 0


def cmd_audit(args) -> int:
    """See what the classifier threw out, and why."""
    db = store.connect(DB)
    rows = db.execute("SELECT lane, scope, confidence, COUNT(*) c FROM jobs GROUP BY 1,2,3 ORDER BY c DESC").fetchall()
    for r in rows:
        print(f"{r['c']:6}  {r['lane']:14}{r['scope']:12}{r['confidence']}")
    rows = db.execute("SELECT title, company, source, evidence FROM jobs WHERE lane=? AND scope=? ORDER BY RANDOM() LIMIT ?",
                      (args.lane, args.scope, args.n)).fetchall()
    print()
    for r in rows:
        print(f"[{r['source'][:8]}] {r['title'][:40]} @ {r['company'][:16]} :: {r['evidence'][:130]}")
    return 0


def cmd_misreads(_) -> int:
    """Jobs you marked "Not open to me": places where the eligibility rules got it wrong."""
    rows = store.connect(DB).execute("""SELECT j.title, j.company, j.location, j.scope, j.evidence, j.url FROM feedback f
                                        JOIN jobs j ON j.id=f.job_id WHERE f.action='dislike' AND f.reason='location'
                                        ORDER BY f.at DESC""").fetchall()
    for r in rows:
        print(f"{r['title'][:40]} @ {r['company'][:20]}  [{r['location'][:30]} -> {r['scope']}]\n  {r['evidence'][:140]}\n  {r['url']}")
    print(f"{len(rows)} misread{'s' if len(rows) != 1 else ''}")
    return 0


def cmd_companies(args) -> int:
    from . import companies
    db = companies.connect(store.connect(DB))
    if args.action == "seed":
        total = 0
        for register, seeds in companies.fetch_seeds(mfbs=args.mfbs).items():
            if isinstance(seeds, Exception):
                print(f"{register:10} FAILED: {seeds!r}"[:140])
                continue
            added = companies.save_seeds(db, seeds)
            total += added
            print(f"{register:10}{len(seeds):6} listed  {added:5} new  {sum(1 for s in seeds if s.website):5} with a website")
        print(f"{db.execute('SELECT COUNT(*) FROM companies').fetchone()[0]} companies in total")
        return 0
    if args.action == "crawl":
        rows = companies.due(db, args.all, args.limit, args.industry, args.status)
        print(f"crawling {len(rows)} companies with {args.workers} workers...", flush=True)
        tally = companies.crawl(db, rows, args.workers, progress=lambda line: print(line, flush=True))
        print(dict(tally.most_common()))
        return 0
    if args.action == "report":
        print(companies.report(db, args.industry))
        return 0
    for r in companies.listing(db, args.status, args.industry):  # list
        extra = f"{r['ats']}:{r['ats_slug']}" if r["ats"] else (r["careers_url"] or r["note"] or r["website"] or "")
        print(f"{(r['status'] or '-'):11} {r['industry'][:22]:22} {r['name'][:40]:40} {extra}")
    return 0


def cmd_outreach(args) -> int:
    import json
    import sqlite3
    from . import outreach
    folder = PILOT_DB.parent / "outreach"
    emails, config = (json.loads((folder / f).read_text()) for f in ("emails.json", "config.json"))
    db = sqlite3.connect(PILOT_DB)
    db.row_factory = sqlite3.Row
    try:
        if args.action == "login":
            for event in outreach.outlook("login", timeout=900):
                if event["type"] == "account":
                    print(f"Signed in as {event['account']}")
                    if event["account"].lower() != config["account"].lower():
                        print(f"Expected {config['account']}: drafts won't be saved to this one.")
        elif args.action == "preview":
            for key in outreach.queue(db, emails)[:args.n]:
                e = emails[key]
                print(f"=== {key}\nTo: {e['to']}\nSubject: {e['subject']}\nCV: {outreach.cv_for(e, config).name}\n\n{e['body']}\n")
        elif args.action == "draft":
            found = outreach.sync(db, config["account"])  # Never draft to someone you have already emailed by hand.
            if found:
                print(f"{len(found)} already sent, marked Emailed")
            print(f"{outreach.draft(db, emails, config, args.n)} drafts saved in Outlook. Read them, then send.")
        elif args.action == "sync":
            found = outreach.sync(db, config["account"])
            print(f"{len(found)} newly marked Emailed" + "".join(f"\n  {k}" for k in found))
        else:
            log = outreach.rows(db).values()
            sent = sum(1 for r in log if r["sent_at"])
            print(f"{len(outreach.queue(db, emails))} to draft, {len(log) - sent} waiting in Drafts, {sent} sent")
            for key, e in emails.items():
                if "hold" in e:
                    print(f"  held  {key[:36]:36} {e['hold']}")
    except outreach.OutlookError as e:
        print(e)
        return 1
    return 0


def cmd_serve(args) -> int:
    from .ranker import Ranker
    from .server import serve
    serve(Ranker(store.connect(DB), PROFILE), args.port, PILOT_DB)
    return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="scout")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ingest", help="fetch all sources, classify, embed")
    s.add_argument("--only", nargs="*", choices=[*SOURCES, "programmes"])
    s.set_defaults(fn=cmd_ingest)
    sub.add_parser("reprocess", help="reclassify stored jobs").set_defaults(fn=cmd_reprocess)
    s = sub.add_parser("top", help="print the best matches")
    s.add_argument("--lane", choices=["global_remote", "nigeria"])
    s.add_argument("-n", type=int, default=20)
    s.add_argument("--unverified", action="store_true")
    s.set_defaults(fn=cmd_top)
    s = sub.add_parser("audit", help="inspect classifier decisions")
    s.add_argument("--lane", default="rejected")
    s.add_argument("--scope", default="restricted")
    s.add_argument("-n", type=int, default=25)
    s.set_defaults(fn=cmd_audit)
    sub.add_parser("misreads", help="jobs you marked as not open to you").set_defaults(fn=cmd_misreads)
    s = sub.add_parser("companies", help="find employers' own careers pages (pilot)")
    s.add_argument("action", choices=["seed", "crawl", "report", "list"])
    s.add_argument("--mfbs", action="store_true", help="seed: include CBN's ~790 microfinance banks")
    s.add_argument("--all", action="store_true", help="crawl: recheck companies checked in the last week too")
    s.add_argument("--limit", type=int, help="crawl: at most this many companies")
    s.add_argument("--workers", type=int, default=8)
    s.add_argument("--industry", help='e.g. "Banking"; see `scout companies report`')
    s.add_argument("--status", nargs="+", choices=["ats", "custom", "linkedin", "email", "no_careers", "js_only", "blocked", "unreachable"],
                   help="list, or crawl only these (with --all to recheck recent ones)")
    s.set_defaults(fn=cmd_companies)
    s = sub.add_parser("outreach", help="cold emails to companies that ask for CVs by email")
    s.add_argument("action", choices=["status", "login", "preview", "draft", "sync"])
    s.add_argument("-n", type=int, default=10, help="preview or draft this many (default 10)")
    s.set_defaults(fn=cmd_outreach)
    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--port", type=int, default=8787)
    s.set_defaults(fn=cmd_serve)
    args = p.parse_args()
    sys.exit(args.fn(args))
