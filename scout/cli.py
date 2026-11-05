from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import store
from .sources import SOURCES

ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get("SCOUT_DB", ROOT / "data" / "scout.db"))  # Point this at a copy to test without touching your likes.
PROFILE = ROOT / "profile.json"


def cmd_ingest(args) -> int:
    db = store.connect(DB)
    names = args.only or list(SOURCES)
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
        note = f"  PARTIAL/FAILED: {error!r}"[:140] if error else ""
        print(f"{name:16}{len(jobs):6} jobs  {time.time() - started:5.1f}s{note}", flush=True)
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


def cmd_serve(args) -> int:
    from .ranker import Ranker
    from .server import serve
    serve(Ranker(store.connect(DB), PROFILE), args.port)
    return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="scout")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ingest", help="fetch all sources, classify, embed")
    s.add_argument("--only", nargs="*", choices=list(SOURCES))
    s.set_defaults(fn=cmd_ingest)
    sub.add_parser("reprocess", help="reclassify stored jobs").set_defaults(fn=cmd_reprocess)
    s = sub.add_parser("top", help="print the best matches")
    s.add_argument("--lane", choices=["global_remote", "lagos"])
    s.add_argument("-n", type=int, default=20)
    s.add_argument("--unverified", action="store_true")
    s.set_defaults(fn=cmd_top)
    s = sub.add_parser("audit", help="inspect classifier decisions")
    s.add_argument("--lane", default="rejected")
    s.add_argument("--scope", default="restricted")
    s.add_argument("-n", type=int, default=25)
    s.set_defaults(fn=cmd_audit)
    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--port", type=int, default=8787)
    s.set_defaults(fn=cmd_serve)
    args = p.parse_args()
    sys.exit(args.fn(args))
