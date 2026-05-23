from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from healthcare_agent.agent import PatientAdvocateAgent


ROOT = Path(__file__).parent
STATIC_ROOT = ROOT / "static"
agent = PatientAdvocateAgent()


class AppHandler(BaseHTTPRequestHandler):
    server_version = "HealthcarePriceAgent/0.1"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._serve_file(STATIC_ROOT / "index.html")
            return

        requested = (STATIC_ROOT / path.removeprefix("/")).resolve()
        if STATIC_ROOT.resolve() not in requested.parents and requested != STATIC_ROOT.resolve():
            self._json({"error": "not found"}, status=404)
            return
        self._serve_file(requested)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/chat":
            self._json({"error": "not found"}, status=404)
            return

        try:
            payload = self._read_json()
            message = str(payload.get("message", "")).strip()
            case_id = payload.get("case_id")
            if not message:
                self._json({"error": "message is required"}, status=400)
                return
            response = agent.respond(message=message, case_id=case_id)
            self._json(response)
        except json.JSONDecodeError:
            self._json({"error": "invalid json"}, status=400)
        except Exception as exc:
            self._json({"error": str(exc)}, status=500)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        return json.loads(body or "{}")

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._json({"error": "not found"}, status=404)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"Healthcare Price Transparency Agent running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
