"""Static dev server for the widget folder (stdlib only).

    python avatar_widget/serve.py --port 4300

Serves loader.js, panel.html and src/ with CORS + frame-ancestors * so any dealer page can embed it.
The conversation API itself is human_bot (default http://127.0.0.1:4001); pass it via the embed tag
(data-api) or ?api= on panel.html. In production put both behind one HTTPS origin (Caddy/Cloudflare
Tunnel), exactly as human_bot/web/EMBED.md describes.
"""
from __future__ import annotations

import argparse
import mimetypes
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("application/json", ".json")


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Security-Policy", "frame-ancestors *")
        self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        print("[widget] " + fmt % args)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4300)
    a = ap.parse_args()
    print(f"avatar_widget -> http://127.0.0.1:{a.port}/demo.html")
    ThreadingHTTPServer(("0.0.0.0", a.port), partial(Handler, directory=str(HERE))).serve_forever()
