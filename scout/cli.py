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


def cmd_serve(args) -> int:
    from .ranker import Ranker
    from .server import serve
    serve(Ranker(store.connect(DB), PROFILE), args.port)
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
    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--port", type=int, default=8787)
    s.set_defaults(fn=cmd_serve)
    args = p.parse_args()
    sys.exit(args.fn(args))
