#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Minimal mock of the Nexus 2 staging REST API for action testing.

Implements just enough of the endpoints exercised by nexus-staging-action
``release`` mode to validate request flow without a live Nexus server:

* ``GET  /service/local/staging/repository/{id}/activity``
    Returns a ``repositoryClosed`` activity before release and a
    ``repositoryReleased`` activity afterwards.
* ``POST /service/local/staging/bulk/promote``
    Records the request body, flips internal state to released, returns 201.

Every request is appended (path + method + body) to the log file given by the
``MOCK_LOG`` environment variable so the test can assert on the exact calls.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("MOCK_PORT", "8089"))
LOG = os.environ.get("MOCK_LOG", "/tmp/nexus_requests.log")

# Module-level state shared across requests.
STATE = {"released": False}

CLOSED_XML = (
    "<list><stagingActivity><name>close</name><events>"
    "<stagingActivityEvent><name>repositoryClosed</name>"
    "</stagingActivityEvent></events></stagingActivity></list>"
)

RELEASED_XML = (
    "<list><stagingActivity><name>release</name><events>"
    "<stagingActivityEvent><name>repositoryClosed</name>"
    "</stagingActivityEvent>"
    "<stagingActivityEvent><name>repositoryReleased</name>"
    "</stagingActivityEvent></events></stagingActivity></list>"
)


def _log(line: str) -> None:
    with open(LOG, "a", encoding="utf-8") as handle:
        _ = handle.write(line + "\n")


class Handler(BaseHTTPRequestHandler):
    """Route the handful of endpoints the action calls."""

    def _send(self, code: int, body: str, ctype: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        _ = self.wfile.write(payload)

    def do_GET(self) -> None:
        _log(f"GET {self.path}")
        if self.path.endswith("/activity"):
            xml = RELEASED_XML if STATE["released"] else CLOSED_XML
            self._send(200, xml, "application/xml")
        else:
            self._send(404, "<error/>", "application/xml")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        _log(f"POST {self.path} BODY {body}")
        if self.path.endswith("/staging/bulk/promote"):
            try:
                data = json.loads(body)  # pyright: ignore[reportAny]
                ids = data["data"]["stagedRepositoryIds"]  # pyright: ignore[reportAny]
            except (ValueError, KeyError):
                self._send(400, "<error/>", "application/xml")
                return
            if not ids:
                self._send(400, "<error/>", "application/xml")
                return
            STATE["released"] = True
            self._send(201, "", "application/xml")
        else:
            self._send(404, "<error/>", "application/xml")

    def log_message(self, format: str, *args: object) -> None:  # pyright: ignore[reportImplicitOverride]
        return


def main() -> None:
    open(LOG, "w", encoding="utf-8").close()
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"mock-nexus listening on {PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
