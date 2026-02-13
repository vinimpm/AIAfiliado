"""Lightweight health check HTTP server for ECS container health probes.

Runs a simple HTTP server on port 8080 in a background daemon thread.
``GET /health`` checks database and Redis connectivity, returning 200 or 503.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.config import settings


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return

        checks: dict[str, str] = {}
        healthy = True

        # Database check
        try:
            from sqlalchemy import text

            from models.database import get_session

            session = get_session()
            try:
                session.execute(text("SELECT 1"))
                checks["database"] = "ok"
            finally:
                session.close()
        except Exception as exc:
            checks["database"] = str(exc)
            healthy = False

        # Redis check
        try:
            import redis

            ssl = settings.REDIS_TLS
            r = redis.from_url(settings.REDIS_URL, socket_connect_timeout=3, ssl=ssl)
            r.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = str(exc)
            healthy = False

        status_code = 200 if healthy else 503
        body = json.dumps({"status": "healthy" if healthy else "unhealthy", "checks": checks})

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, format, *args):
        """Suppress default access logs."""
        pass


def start_health_server(port: int = 8080) -> None:
    """Start the health check HTTP server in a daemon thread."""
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
