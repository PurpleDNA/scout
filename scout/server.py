"""Local web app. Binds to 127.0.0.1 only: this holds your profile and has no auth."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlparse

from . import programmes, store
from .ranker import Ranker

WEB = Path(__file__).resolve().parent.parent / "web"
ACTIONS = {"like", "dislike", "applied", None}


def serve(ranker: Ranker, port: int) -> None:
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/":
                return self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
            if url.path == "/programmes":
                return self._send(200, (WEB / "programmes.html").read_bytes(), "text/html; charset=utf-8")
            if url.path == "/api/programmes":
                view = parse_qs(url.query).get("view", ["open"])[0]
                if view not in ("open", *programmes.STATUSES):
                    return self._send(400, b'{"error":"bad view"}')
                with lock:
                    data = {"programmes": programmes.listing(ranker.db, view), "counts": programmes.counts(ranker.db)}
                return self._send(200, json.dumps(data).encode())
            if url.path == "/api/jobs":
                q = {k: v[0] for k, v in parse_qs(url.query).items()}
                try:
                    with lock:
                        data = ranker.recommend(lane=q.get("lane") or None, work_model=q.get("work_model") or None,
                                                query=q.get("q", ""), min_score=int(q.get("min_score") or 0),
                                                verified_only=q.get("unverified") != "1", status=q.get("status", "new"),
                                                limit=min(int(q.get("limit") or 100), 300), employment=q.get("employment") or None,
                                                posted_within=float(q["posted_within"]) if q.get("posted_within") else None,
                                                sort=q.get("sort", "best"))
                except ValueError:
                    return self._send(400, b'{"error":"bad number"}')
                return self._send(200, json.dumps(data).encode())
            self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            # Requiring JSON stops other websites posting here from your browser with a plain form.
            if self.path not in ("/api/feedback", "/api/stage", "/api/reason", "/api/programme") \
                    or self.headers.get("Content-Type") != "application/json":
                return self._send(404, b'{"error":"not found"}')
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                job_id = body["job_id"]
            except (ValueError, KeyError, TypeError):
                return self._send(400, b'{"error":"bad request"}')
            if not isinstance(job_id, str):
                return self._send(400, b'{"error":"bad job"}')
            if self.path == "/api/programme":
                status = body.get("status")
                if status is not None and status not in programmes.STATUSES:
                    return self._send(400, b'{"error":"bad status"}')
                with lock:
                    if not programmes.set_status(ranker.db, job_id, status):
                        return self._send(404, b'{"error":"no such programme"}')
                return self._send(200, b'{"ok":true}')
            if self.path == "/api/stage":
                stage = body.get("stage")
                if stage is not None and stage not in store.STAGES:
                    return self._send(400, b'{"error":"bad stage"}')
                with lock:
                    if not store.set_stage(ranker.db, job_id, stage):
                        return self._send(409, b'{"error":"mark it as applied first"}')
                return self._send(200, b'{"ok":true}')
            if self.path == "/api/reason":
                reason = body.get("reason")
                if reason is not None and reason not in store.REASONS:
                    return self._send(400, b'{"error":"bad reason"}')
                with lock:
                    if not store.set_reason(ranker.db, job_id, reason):
                        return self._send(409, b'{"error":"mark it as not for me first"}')
                return self._send(200, b'{"ok":true}')
            action = body.get("action")
            if action not in ACTIONS:
                return self._send(400, b'{"error":"bad action"}')
            with lock:
                store.set_feedback(ranker.db, job_id, action)
            self._send(200, b'{"ok":true}')

        def log_message(self, *_):
            pass

    print(f"Scout running at http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
