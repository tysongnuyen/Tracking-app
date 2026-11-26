"""Optional, authenticated localhost receiver for complete background-tab data."""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import secrets
import threading
import time
import math
from browser_reader import hostname

PORT = 43127

class Bridge:
    def __init__(self, store):
        self.lock = threading.Lock()
        self.snapshots = {}
        self.token = store.setting("bridge_token") or secrets.token_hex(32)
        store.set_setting("bridge_token", self.token)
        self.server = None
        self.error = ""

    def start(self):
        bridge = self
        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(3)
            def log_message(self, *args):
                pass
            def do_OPTIONS(self):
                self.handle_request(True)
            def do_POST(self):
                self.handle_request(False)
            def handle_request(self, preflight):
                self.connection.settimeout(3)
                origin = self.headers.get("Origin", "")
                valid_origin = not origin or bool(re.fullmatch(r"chrome-extension://[a-p]{32}", origin))
                if not valid_origin or self.headers.get("Host") != f"127.0.0.1:{PORT}":
                    self.send_error(403)
                    return
                if preflight:
                    self.send_response(204)
                    if origin:
                        self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                    self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Tracking-Token")
                    self.end_headers()
                    return
                if not secrets.compare_digest(self.headers.get("X-Tracking-Token", ""), bridge.token):
                    self.send_error(401)
                    return
                if self.path != "/api/browser":
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", -1))
                    if length < 0 or length > 262144 or self.headers.get("Transfer-Encoding"):
                        raise ValueError()
                    payload = json.loads(self.rfile.read(length))
                    client, hosts = payload.get("Client"), payload.get("Hosts")
                    if client not in {"chrome", "msedge", "brave"} or not isinstance(hosts, list) or len(hosts) > 5000:
                        raise ValueError()
                    # Accept hostnames only; a complete URL in this field is discarded.
                    hosts = {h for h in hosts if isinstance(h, str) and h == hostname(h)}
                    active = payload.get("ActiveHost")
                    snapshot = dict(hosts=set(sorted(hosts)[:1000]), active=active if active in hosts else None,
                                    focused=payload.get("Focused") is True, at=time.monotonic())
                    windows=[]
                    raw_windows=payload.get("Windows",[])
                    if not isinstance(raw_windows,list) or len(raw_windows)>64:raise ValueError()
                    for entry in raw_windows:
                        if not isinstance(entry,dict):raise ValueError()
                        wh=entry.get("Hosts",[]);vh=entry.get("VisibleHosts",[])
                        if not isinstance(wh,list) or not isinstance(vh,list) or len(wh)>1000 or len(vh)>1000:raise ValueError()
                        wh={h for h in wh if isinstance(h,str) and h in snapshot["hosts"]}
                        vh={h for h in vh if isinstance(h,str) and h in wh}
                        bounds=entry.get("Bounds")
                        if not (isinstance(bounds,list) and len(bounds)==4 and all(isinstance(n,(int,float)) and math.isfinite(n) and abs(n)<100000 for n in bounds)):
                            bounds=None
                        windows.append(dict(hosts=wh,visible=vh,bounds=bounds,
                            focused=entry.get("Focused") is True,minimized=entry.get("Minimized") is True,
                            complete=entry.get("VisibilityComplete") is True))
                    snapshot["windows"]=windows
                    with bridge.lock:
                        bridge.snapshots[client + ".exe"] = snapshot
                    self.send_response(200)
                    if origin:
                        self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", "11")
                    self.end_headers()
                    self.wfile.write(b'{"ok":true}')
                except (ValueError, TypeError, OSError):
                    self.send_error(400)
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
            self.server.daemon_threads = True
            threading.Thread(target=self.server.serve_forever, daemon=True, name="OptionalBrowserBridge").start()
        except OSError as exc:
            self.error = str(exc)

    def current(self):
        with self.lock:
            now = time.monotonic()
            return {k: dict(v) for k, v in self.snapshots.items() if now - v["at"] <= 70}

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
