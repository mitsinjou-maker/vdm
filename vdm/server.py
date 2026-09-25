"""API HTTP locale (127.0.0.1) utilisée par l'extension navigateur et par les commandes CLI.

Seuls les appels sans en-tête Origin (CLI) ou venant d'une extension sont acceptés :
une page web quelconque ne peut pas ajouter de téléchargements à votre insu.
"""

import json
import re
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, scheduler
from .config import Schedule

_ROUTE = re.compile(r"^/api/jobs/(\w+)/(pause|resume|remove|move|subs)$")


class _Handler(BaseHTTPRequestHandler):
    manager = None
    port = None

    def log_message(self, *args):
        pass

    def _allowed(self):
        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            return False  # protection contre le DNS rebinding
        origin = self.headers.get("Origin")
        return origin is None or origin.startswith(("chrome-extension://", "moz-extension://"))

    def _send(self, code, payload=None):
        body = json.dumps(payload if payload is not None else {}, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_OPTIONS(self):
        if not self._allowed():
            return self._send(403, {"error": "origine refusée"})
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", "*"))
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self):
        if not self._allowed():
            return self._send(403, {"error": "origine refusée"})
        m = self.manager
        if self.path == "/api/ping":
            return self._send(200, {"app": "vdm", "version": __version__})
        if self.path == "/api/jobs":
            with m.lock:
                jobs = [dict(j.to_dict(), name=j.name) for j in m.jobs.values()]
            for j in jobs:
                j.pop("headers", None)  # ne pas renvoyer les cookies
            return self._send(200, {"jobs": jobs, "limit": m.limiter.rate, "parallel": m.max_parallel,
                                    "schedule": m.schedule_status()})
        if self.path == "/api/schedule":
            return self._send(200, dict(asdict(m.config.schedule), status=m.schedule_status()))
        self._send(404, {"error": "introuvable"})

    def do_POST(self):
        if not self._allowed():
            return self._send(403, {"error": "origine refusée"})
        m = self.manager
        try:
            data = self._body()
            if self.path == "/api/add":
                url = (data.get("url") or "").strip()
                if not url.startswith(("http://", "https://")):
                    return self._send(400, {"error": "URL http(s) attendue"})
                job = m.add(url, dest=data.get("dest"), kind=data.get("kind") or "auto",
                            quality=data.get("quality") or "best", title=data.get("title"),
                            headers=data.get("headers") or {}, connections=data.get("connections"),
                            queue=data.get("queue") or "principale", playlist=bool(data.get("playlist")),
                            audio_lang=data.get("audio_lang"), subs=data.get("subs"),
                            subs_auto=data.get("subs_auto"), subs_embed=data.get("subs_embed"))
                return self._send(200, {"id": job.id})
            if self.path == "/api/limit":
                m.set_speed_limit(data.get("rate") or None)
                return self._send(200, {"limit": m.limiter.rate})
            if self.path == "/api/schedule":
                sched = Schedule(**{k: v for k, v in data.items() if k in Schedule.__dataclass_fields__})
                scheduler.parse_hhmm(sched.start)
                if sched.stop:
                    scheduler.parse_hhmm(sched.stop)
                m.set_schedule(sched)
                return self._send(200, {"status": m.schedule_status()})
            if self.path == "/api/clean":
                m.clean()
                return self._send(200, {"ok": True})
            route = _ROUTE.match(self.path)
            if route:
                ref, action = route.groups()
                if action == "remove":
                    m.remove(ref, delete_files=bool(data.get("delete")))
                elif action == "move":
                    m.set_queue(ref, data.get("queue"))
                elif action == "subs":
                    m.fetch_subs(ref, data.get("subs") or "", data.get("auto"), data.get("embed"))
                else:
                    getattr(m, action)(ref)
                return self._send(200, {"ok": True})
        except KeyError as e:
            return self._send(404, {"error": str(e.args[0]) if e.args else "introuvable"})
        except (ValueError, TypeError) as e:
            return self._send(400, {"error": str(e)})
        self._send(404, {"error": "introuvable"})


def serve(manager, port):
    handler = type("Handler", (_Handler,), {"manager": manager, "port": port})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
