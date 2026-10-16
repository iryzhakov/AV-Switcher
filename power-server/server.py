#!/usr/bin/env python3

import json
import math
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = 5154


class Handler(BaseHTTPRequestHandler):
    def respond(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/info/version":
            self.respond({"version": "1.1.0"})
        elif self.path == "/info/status":
            self.respond({"status": True})
        else:
            self.respond({"error": "Not found"}, 404)

    def do_POST(self):
        actions = {
            "/control/sleep": ["systemctl", "suspend"],
            "/control/shutdown": ["systemctl", "poweroff"],
            "/control/reboot": ["systemctl", "reboot"],
        }
        command = actions.get(self.path)
        if not command:
            self.respond({"error": "Not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 <= length <= 4096:
                raise ValueError("Invalid body size")
            data = json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(data, dict):
                raise ValueError("Expected an object")
            timeout = float(data.get("timeout", 0))
            if not math.isfinite(timeout):
                raise ValueError("Invalid timeout")
            timeout = max(0, min(timeout, 300))
        except (ValueError, TypeError, OverflowError):
            self.respond({"error": "Invalid request"}, 400)
            return

        self.respond({"status": True})

        def execute():
            time.sleep(max(timeout, 1))
            try:
                result = subprocess.run(command, check=False)
                if result.returncode:
                    self.log_error("Power command failed: %s", result.returncode)
            except OSError as error:
                self.log_error("Power command failed: %s", error)

        threading.Thread(target=execute, daemon=True).start()

    def setup(self):
        super().setup()
        self.connection.settimeout(10)


if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
