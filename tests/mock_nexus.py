#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Minimal mock of the Nexus 2 staging REST API for action testing.

Implements enough of the endpoints nexus-staging-action calls to validate
the request flow of every mode without a live Nexus server. Paths are
relative to ``/service/local/staging``:

* ``POST /profiles/{profile}/start``
    Parses the promoteRequest XML (400 when malformed), opens a repository
    named ``{profile}-{n}`` and returns 201.
* ``PUT  /deployByRepositoryId/{id}/{path}``
    Accepts an artifact into an open repository and returns 201.
* ``POST /profiles/{profile}/finish``
    Requests the close of an open repository and returns 201. The close
    then moves on one step per status request: the first still reports the
    repository open and not transitioning (accepted, not started, so the
    close activity is absent), the second starts it (``transitioning``), and
    the ``CLOSE_POLLS``-th completes it, so the action has to poll.
* ``POST /profiles/{profile}/drop``
    Drops a repository and returns 201; 400 while it is transitioning.
* ``GET  /repository/{id}``
    Returns the repository status XML (``type``, ``transitioning``).
* ``GET  /repository/{id}/activity``
    Returns the staging activity XML (``repositoryClosed``, ``ruleFailed``
    and so on), pretty-printed the way Nexus prints it.
* ``POST /bulk/promote``
    Releases the repositories named in the JSON body and returns 201.

The staging profile ID selects how the repositories it creates behave:

* ``upload-fail``: deploying a ``.pom`` file answers HTTP 400.
* ``drop-hang``: as ``upload-fail``, and drop requests answer after
  ``HANG_SECONDS``, so the cleanup drop of a failed stage hangs.
* ``rule-fail``: closing fails the signature staging rule.
* ``rule-once``: the first close fails the signature rule; later ones pass.
* ``close-hang``: closing never completes.
* ``status-hang``: once a close is requested, status requests answer after
  ``HANG_SECONDS``, the way an unresponsive Nexus would.
* ``verify-hang``: every status request answers after ``HANG_SECONDS``,
  so close mode hangs on its first lookup of the repository.
* ``activity-error``: activity requests answer HTTP 503.
* anything else: closing succeeds.

Repository ``mock-repo-1001`` exists from start-up in the closed state, for
the release test.

Every request is appended to the log file named by the ``MOCK_LOG``
environment variable so tests can assert on the exact calls. Each parsed
promoteRequest also logs a ``PARSED {op} {repo} description={text}`` line
with the description as the mock decoded it.
"""

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

PORT = int(os.environ.get("MOCK_PORT", "8089"))
LOG = os.environ.get("MOCK_LOG", "/tmp/nexus_requests.log")
CLOSE_POLLS = 3
HANG_SECONDS = 60

API = "/service/local/staging"
PROFILE_OP = re.compile(rf"^{API}/profiles/([^/]+)/(start|finish|drop)$")
DEPLOY = re.compile(rf"^{API}/deployByRepositoryId/([^/]+)/(.+)$")
REPOSITORY = re.compile(rf"^{API}/repository/([^/]+)(/activity)?$")

SIGNATURE_FAILURE = (
    "Missing Signature: '/org/example/demo/1.0.0/demo-1.0.0.pom.asc'"
    " does not exist for 'demo-1.0.0.pom'."
)

# An event is its name plus its staging properties.
Event = tuple[str, dict[str, str]]


@dataclass
class Repo:
    """State of one mock staging repository."""

    profile: str
    type: str = "open"
    close_requested: bool = False
    transitioning: bool = False
    status_polls: int = 0
    closes: int = 0
    activities: list[tuple[str, list[Event]]] = field(default_factory=list)


REPOS: dict[str, Repo] = {
    "mock-repo-1001": Repo(
        profile="mock-profile",
        type="closed",
        activities=[
            ("open", [("repositoryCreated", {})]),
            ("close", [("repositoryClosed", {})]),
        ],
    )
}
COUNTER = {"next": 1001}


def _log(line: str) -> None:
    with open(LOG, "a", encoding="utf-8") as handle:
        _ = handle.write(line + "\n")


def _status_xml(repo_id: str, repo: Repo) -> str:
    transitioning = "true" if repo.transitioning else "false"
    return (
        "<stagingProfileRepository>\n"
        f"  <profileId>{escape(repo.profile)}</profileId>\n"
        "  <profileType>repository</profileType>\n"
        f"  <repositoryId>{escape(repo_id)}</repositoryId>\n"
        f"  <type>{repo.type}</type>\n"
        "  <policy>release</policy>\n"
        f"  <transitioning>{transitioning}</transitioning>\n"
        "</stagingProfileRepository>\n"
    )


def _activity_xml(repo: Repo) -> str:
    lines = ["<list>"]
    for name, events in repo.activities:
        lines += ["  <stagingActivity>", f"    <name>{name}</name>", "    <events>"]
        for event, properties in events:
            lines += [
                "      <stagingActivityEvent>",
                f"        <name>{event}</name>",
                "        <properties>",
            ]
            for key, value in properties.items():
                lines += [
                    "          <stagingProperty>",
                    f"            <name>{key}</name>",
                    f"            <value>{escape(value)}</value>",
                    "          </stagingProperty>",
                ]
            lines += ["        </properties>", "      </stagingActivityEvent>"]
        lines += ["    </events>", "  </stagingActivity>"]
    lines.append("</list>")
    return "\n".join(lines) + "\n"


def _advance_close(repo: Repo) -> None:
    """Move a requested close on by one step per status request."""
    if not repo.close_requested:
        return
    repo.status_polls += 1
    if repo.status_polls == CLOSE_POLLS - 1:
        repo.transitioning = True
        repo.activities.append(("close", []))
    if repo.status_polls < CLOSE_POLLS or repo.profile == "close-hang":
        return
    repo.close_requested = False
    repo.transitioning = False
    repo.closes += 1
    events = repo.activities[-1][1]
    if repo.profile == "rule-fail" or (
        repo.profile == "rule-once" and repo.closes == 1
    ):
        events.append(
            (
                "ruleFailed",
                {"typeId": "signature-staging", "failureMessage": SIGNATURE_FAILURE},
            )
        )
        events.append(
            ("repositoryCloseFailed", {"cause": "One or more rules have failed"})
        )
    else:
        repo.type = "closed"
        events.append(("repositoryClosed", {}))


class Handler(BaseHTTPRequestHandler):
    """Route the handful of endpoints the action calls."""

    def _send(self, code: int, body: str = "", ctype: str = "application/xml") -> None:
        payload = body.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            _ = self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            # The client gave up waiting (status-hang, verify-hang,
            # drop-hang); nothing to answer.
            return

    def _error(self, code: int, message: str) -> None:
        body = f"<nexus-error><errors><error><msg>{escape(message)}</msg></error></errors></nexus-error>"
        self._send(code, body)

    def _body(self) -> str:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length).decode("utf-8") if length else ""

    def do_GET(self) -> None:
        _log(f"GET {self.path}")
        match = REPOSITORY.match(self.path)
        if not match:
            self._error(404, "Not found")
            return
        repo_id, activity = match.groups()
        repo = REPOS.get(repo_id)
        if repo is None:
            self._error(404, f"No such repository: {repo_id}")
        elif activity and repo.profile == "activity-error":
            self._error(503, "Activity temporarily unavailable")
        elif activity:
            self._send(200, _activity_xml(repo))
        else:
            if repo.profile == "verify-hang" or (
                repo.profile == "status-hang" and repo.close_requested
            ):
                time.sleep(HANG_SECONDS)
            _advance_close(repo)
            self._send(200, _status_xml(repo_id, repo))

    def do_PUT(self) -> None:
        _ = self._body()
        _log(f"PUT {self.path}")
        match = DEPLOY.match(self.path)
        if not match:
            self._error(404, "Not found")
            return
        repo_id, path = match.groups()
        repo = REPOS.get(repo_id)
        if repo is None:
            self._error(404, f"No such repository: {repo_id}")
        elif repo.type != "open" or repo.close_requested:
            self._error(400, f"Repository {repo_id} is not open")
        elif repo.profile in ("upload-fail", "drop-hang") and path.endswith(".pom"):
            self._error(400, f"Rejected {path}")
        else:
            self._send(201)

    def do_POST(self) -> None:
        body = self._body()
        _log(f"POST {self.path} BODY {body}")
        if self.path == f"{API}/bulk/promote":
            self._bulk_promote(body)
            return
        match = PROFILE_OP.match(self.path)
        if not match:
            self._error(404, "Not found")
            return
        profile, op = match.groups()
        try:
            data = ET.fromstring(body).find("data")
        except ET.ParseError as exc:
            self._error(400, f"Malformed XML: {exc}")
            return
        if data is None:
            self._error(400, "Missing <data>")
            return
        repo_id = data.findtext("stagedRepositoryId", "")
        _log(f"PARSED {op} {repo_id} description={data.findtext('description', '')}")
        if op == "start":
            self._start(profile)
        else:
            self._finish_or_drop(op, repo_id)

    def _start(self, profile: str) -> None:
        repo_id = f"{profile}-{COUNTER['next']}"
        COUNTER["next"] += 1
        REPOS[repo_id] = Repo(
            profile=profile, activities=[("open", [("repositoryCreated", {})])]
        )
        response = (
            "<promoteResponse><data>"
            f"<stagedRepositoryId>{escape(repo_id)}</stagedRepositoryId>"
            "</data></promoteResponse>"
        )
        self._send(201, response)

    def _finish_or_drop(self, op: str, repo_id: str) -> None:
        repo = REPOS.get(repo_id)
        if repo is None:
            self._error(404, f"No such repository: {repo_id}")
        elif repo.transitioning:
            self._error(400, f"Repository {repo_id} is transitioning")
        elif op == "drop":
            if repo.profile == "drop-hang":
                time.sleep(HANG_SECONDS)
            _ = REPOS.pop(repo_id, None)
            self._send(201)
        elif repo.type != "open" or repo.close_requested:
            self._error(400, f"Repository {repo_id} is already {repo.type}")
        else:
            repo.close_requested = True
            repo.status_polls = 0
            self._send(201)

    def _bulk_promote(self, body: str) -> None:
        try:
            data = json.loads(body)  # pyright: ignore[reportAny]
            ids = data["data"]["stagedRepositoryIds"]  # pyright: ignore[reportAny]
        except (ValueError, KeyError):
            self._error(400, "Malformed JSON")
            return
        if not ids or not isinstance(ids, list):
            self._error(400, "No repositories named")
            return
        repos = [REPOS.get(str(repo_id)) for repo_id in ids]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        for repo in repos:
            if repo is None or repo.type != "closed":
                self._error(400, "Repository missing or not closed")
                return
        for repo in repos:
            if repo is not None:
                repo.type = "released"
                repo.activities.append(("release", [("repositoryReleased", {})]))
        self._send(201)

    def log_message(self, format: str, *args: object) -> None:  # pyright: ignore[reportImplicitOverride]
        return


def main() -> None:
    open(LOG, "w", encoding="utf-8").close()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.daemon_threads = True
    print(f"mock-nexus listening on {PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
