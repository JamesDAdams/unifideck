#!/usr/bin/env python3
"""Temporary dev server for Unifideck — receives live logs from the plugin.

Run with: python3 dev-server/server.py
Default port: 8765 (override DECKY_DEV_SERVER_PORT).
"""
from __future__ import annotations

import json
import os
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("DECKY_DEV_SERVER_PORT", "8765"))
DATA_DIR = os.path.dirname(os.path.abspath(__file__)) or "."
LOG_FILE = os.path.join(DATA_DIR, "logs.ndjson")
MAX_BUFFER = 10000
BUFFER: deque[str] = deque(maxlen=MAX_BUFFER)
TOKEN = os.environ.get("DECKY_DEV_SERVER_TOKEN", "")


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self) -> bool:
        if not TOKEN:
            return True
        return self.headers.get("X-Dev-Token", "") == TOKEN

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        path = url.path
        qs = parse_qs(url.query)
        if path == "/health":
            self._send(200, {"status": "ok", "port": PORT, "buffered": len(BUFFER)})
            return
        if path == "/Unifideck.zip":
            zip_p = os.path.abspath(os.path.join(DATA_DIR, "..", "out", "Unifideck.zip"))
            if os.path.isfile(zip_p):
                with open(zip_p, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
        if path == "/logs" or path == "/logs/tail":
            n = int(qs.get("limit", qs.get("n", ["100"]))[0])
            since = qs.get("since", ["0"])[0]
            try:
                since_i = int(since)
            except ValueError:
                since_i = 0
            rows = list(BUFFER)
            # filter by sequence id prefix
            out = []
            for line in rows:
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    out.append({"raw": line})
                    continue
                seq = obj.get("_seq", 0) if isinstance(obj, dict) else 0
                if seq > since_i:
                    out.append(obj)
            out = out[-n:]
            latest = out[-1].get("_seq", 0) if out and isinstance(out[-1], dict) else 0
            self._send(200, {"items": out, "count": len(out), "latest_seq": latest})
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        url = urlparse(self.path)
        if url.path != "/ingest":
            self._send(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            self._send(400, {"error": "bad_json"})
            return
        source = str(body.get("source", "plugin"))
        lines = body.get("lines", [])
        if isinstance(lines, str):
            lines = [lines]
        accepted = 0
        for i, line in enumerate(lines):
            seq = len(BUFFER) + 1
            rec = {"_seq": seq, "source": source, "line": line}
            encoded = json.dumps(rec)
            BUFFER.append(encoded)
            try:
                with open(LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(encoded + "\n")
            except OSError:
                pass

            # Print directly to stdout so logs show up live in OpenFox Dev Server interface
            try:
                parsed = json.loads(line)
                lvl = parsed.get("level", "INFO")
                logger_name = parsed.get("logger", source)
                msg = parsed.get("msg", line)
                print(f"[{source}] [{lvl}] {logger_name}: {msg}", flush=True)
            except Exception:
                print(f"[{source}] {line}", flush=True)

            accepted += 1
        self._send(200, {"accepted": accepted})

    def log_message(self, *args, **kwargs) -> None:  # silence
        return


def main() -> None:
    host = os.environ.get("DECKY_DEV_SERVER_HOST", "0.0.0.0")
    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer((host, PORT), _Handler)
    print(f"=== Dev server listening on {host}:{PORT} ===", flush=True)
    if os.path.isfile(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                recent = [l.strip() for l in f if l.strip()][-25:]
            if recent:
                print("--- Recent SteamOS logs ---", flush=True)
                for entry in recent:
                    try:
                        d = json.loads(entry)
                        src = d.get("source", "unifideck")
                        line = d.get("line", "")
                        parsed = json.loads(line)
                        lvl = parsed.get("level", "INFO")
                        logger_name = parsed.get("logger", src)
                        msg = parsed.get("msg", line)
                        print(f"[{src}] [{lvl}] {logger_name}: {msg}", flush=True)
                    except Exception:
                        print(entry, flush=True)
                print("--- End of recent logs (live streaming active) ---", flush=True)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":  # entry-point: dev server
    main()