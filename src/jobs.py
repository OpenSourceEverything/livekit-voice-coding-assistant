"""Private durable proposal jobs. No call lifecycle, source apply, Git or deploy."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from changes import ChangeError, ChangeStore, sha256
from codex_bridge import CodexBridgeError, validate_proposal
from repository import RepositoryError, RepositoryService


class JobError(ValueError):
    pass


class JobStore:
    """One owner/PIN, one trusted local database; never a public queue API."""

    def __init__(self, database: str | Path):
        self.database = Path(database).absolute()

    @contextmanager
    def connection(self):
        self.database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.database.is_symlink():
            raise JobError("Job database cannot be a symbolic link.")
        descriptor = os.open(
            self.database, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS jobs ("
                "id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, "
                "repo_root TEXT NOT NULL, request TEXT NOT NULL, path TEXT NOT NULL, "
                "source_hash TEXT NOT NULL, start_line INTEGER NOT NULL, "
                "line_count INTEGER NOT NULL, excerpt TEXT NOT NULL, "
                "state TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', "
                "proposal_ids TEXT NOT NULL DEFAULT '[]', "
                "error_code TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, "
                "updated_at REAL NOT NULL)"
            )
            connection.commit()
            yield connection
        finally:
            connection.close()

    def submit(
        self, repository, request, path, original_sha256, start_line=1, lines=100
    ) -> dict:
        if (
            not isinstance(request, str)
            or not request.strip()
            or len(request.encode("utf-8")) > 4000
            or type(start_line) is not int
            or type(lines) is not int
            or start_line < 1
            or not 1 <= lines <= 200
        ):
            raise JobError("Provide a bounded request and one to 200 source lines.")
        snapshot = repository.snapshot(path)
        if snapshot["sha256"] != original_sha256:
            raise JobError("Source changed; read the file again before queueing.")
        excerpt = "".join(
            snapshot["content"].splitlines(keepends=True)[
                start_line - 1 : start_line - 1 + lines
            ]
        )
        if not excerpt or len(excerpt.encode("utf-8")) > 32_000:
            raise JobError("Select a nonempty source excerpt of at most 32000 bytes.")
        payload = (
            str(repository.root),
            request.strip(),
            snapshot["path"],
            snapshot["sha256"],
            start_line,
            lines,
            excerpt,
        )
        fingerprint = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
        now = time.time()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT id FROM jobs WHERE fingerprint=?", (fingerprint,)
            ).fetchone()
            if existing:
                connection.rollback()
                identifier = existing["id"]
            else:
                total = connection.execute("SELECT count(*) FROM jobs").fetchone()[0]
                pending = connection.execute(
                    "SELECT count(*) FROM jobs WHERE state IN ('queued','running')"
                ).fetchone()[0]
                if total >= 1000 or pending >= 3:
                    raise JobError("Job queue is full; inspect existing jobs locally.")
                cursor = connection.execute(
                    "INSERT INTO jobs (fingerprint,repo_root,request,path,source_hash,"
                    "start_line,line_count,excerpt,state,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (fingerprint, *payload, "queued", now, now),
                )
                identifier = cursor.lastrowid
                connection.commit()  # Durable before the voice tool acknowledges.
        return {
            "job_id": identifier,
            "state": self.status(identifier)["state"],
            "saved": True,
            "source_applied": False,
            "message": "Saved. A separate worker prepares a proposal; call back with this job number. No PR, text or deployment is promised.",
        }

    def status(self, identifier: int, repo_root: Path | None = None) -> dict:
        if type(identifier) is not int or identifier < 1:
            raise JobError("Use the positive job number returned at submission.")
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id=?", (identifier,)
            ).fetchone()
        if row is None or (
            repo_root is not None and row["repo_root"] != str(repo_root.resolve())
        ):
            raise JobError("Job number not found.")
        return {
            "job_id": row["id"],
            "state": row["state"],
            "summary": row["summary"],
            "proposal_ids": json.loads(row["proposal_ids"]),
            "error_code": row["error_code"],
            "job_can_apply_or_deploy": False,
        }

    def claim(self, repo_root: Path) -> dict | None:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE state='queued' AND repo_root=? ORDER BY id LIMIT 1",
                (str(repo_root.resolve()),),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            connection.execute(
                "UPDATE jobs SET state='running',updated_at=? WHERE id=?",
                (time.time(), row["id"]),
            )
            connection.commit()
            return dict(row)

    def finish(self, identifier, state, *, summary="", proposal_ids=(), error_code=""):
        if state not in {"review_ready", "explained", "failed"}:
            raise JobError("Invalid terminal job state.")
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET state=?,summary=?,proposal_ids=?,error_code=?,updated_at=? "
                "WHERE id=? AND state='running'",
                (
                    state,
                    summary[:1200],
                    json.dumps(list(proposal_ids)),
                    error_code,
                    time.time(),
                    identifier,
                ),
            )
            if cursor.rowcount != 1:
                raise JobError("Job no longer belongs to a running operation.")
            connection.commit()

    def recover_interrupted(self) -> int:
        """Call only while holding the external worker lock. Never replay a model call."""
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET state='interrupted',error_code='worker_interrupted',updated_at=? "
                "WHERE state='running'",
                (time.time(),),
            )
            connection.commit()
            return cursor.rowcount


def process_next(
    store, repository: RepositoryService, proposal_directory, bridge
) -> bool:
    if not bridge.enabled:
        raise JobError("Background model execution is disabled.")
    job = store.claim(repository.root)
    if job is None:
        return False
    try:
        original = repository.snapshot(job["path"])
        if original["sha256"] != job["source_hash"]:
            raise JobError("stale_source")
        fragment = {
            "path": job["path"],
            "content": job["excerpt"],
            "sha256": sha256(job["excerpt"]),
        }
        result = bridge.propose(
            job["request"]
            + "\nOnly the supplied source excerpt may change. Preserve its surrounding indentation and newline boundaries. Do not assume the excerpt is the whole file.",
            [fragment],
        )
        # Revalidate at the job boundary; an adapter result is still untrusted.
        result = validate_proposal(json.dumps(result).encode(), [fragment])
        current = repository.snapshot(job["path"])
        if current["sha256"] != job["source_hash"]:
            raise JobError("stale_source")
        identifiers = []
        changes = ChangeStore(repository, proposal_directory, max_file_bytes=512_000)
        for edit in result["edits"]:
            source_lines = current["content"].splitlines(keepends=True)
            start = job["start_line"] - 1
            end = start + job["line_count"]
            if (
                source_lines[end:]
                and edit["new_content"]
                and not edit["new_content"].endswith("\n")
            ):
                raise JobError("Replacement would join the following source line.")
            replacement = (
                "".join(source_lines[:start])
                + edit["new_content"]
                + "".join(source_lines[end:])
            )
            proposal = changes.stage(job["path"], replacement, job["source_hash"])
            identifiers.append(proposal["id"])
        store.finish(
            job["id"],
            "review_ready" if identifiers else "explained",
            summary=("Proposal only: " if identifiers else "Explanation only: ")
            + result["summary"],
            proposal_ids=identifiers,
        )
    except (JobError, RepositoryError, ChangeError, CodexBridgeError) as error:
        code = (
            "stale_source"
            if str(error) == "stale_source"
            else getattr(error, "code", "proposal_rejected")
        )
        store.finish(job["id"], "failed", error_code=code)
    except Exception:
        # Provider errors can contain source, prompts or credentials. Never persist them.
        store.finish(job["id"], "failed", error_code="worker_failed")
    return True
