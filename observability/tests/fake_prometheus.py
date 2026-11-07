"""Local-only fixture endpoint for dashboard rendering; no cluster access."""

import json
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.respond()

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        self.respond(self.rfile.read(size).decode())

    def respond(self, encoded=""):
        path = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(encoded or path.query)
        query = params.get("query", [""])[0]
        data = {"resultType": "vector", "result": []}
        if "buildinfo" in path.path:
            data = {"version": "3.10.0"}
        elif path.path.endswith("/labels"):
            data = ["k8s_cluster", "app", "domain"]
        elif "/label/" in path.path:
            data = ["homelab-wsl", "homelab-wsl-02"]
        elif path.path.endswith("query_range"):
            data = {"resultType": "matrix", "result": []}
        elif "homelab" in query:
            if query.startswith("count("):
                count = "2" if "homelab_expected_site_info" in query else "1"
                data["result"] = [{"metric": {}, "value": [time.time(), count]}]
            else:
                data["result"] = [
                    {"metric": {"k8s_cluster": "homelab-wsl", "app": "test"},
                     "value": [time.time(), "1"]},
                    {"metric": {"k8s_cluster": "homelab-wsl-02", "app": "test"},
                     "value": [time.time(), "0"]},
                ]
        body = json.dumps({"status": "success", "data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 13102), Handler).serve_forever()
