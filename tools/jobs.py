#!/usr/bin/env python3
"""Operate the private proposal queue independently of a voice call."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from changes import sha256
from codex_bridge import CodexBridge
from jobs import JobError, JobStore, process_next
from repository import RepositoryService
from settings import Settings

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    load_dotenv(ROOT / ".env.local")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("work", "status", "probe"))
    parser.add_argument("job_id", type=int, nargs="?")
    parser.add_argument("--max-jobs", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    database = Path(os.getenv("JOBS_DATABASE", str(ROOT / "var/jobs.sqlite3")))
    if not database.is_absolute():
        database = ROOT / database
    store = JobStore(database)
    try:
        if args.action == "status":
            if args.job_id is None:
                parser.error("status needs a job number")
            print(json.dumps(store.status(args.job_id, settings.repo_root), indent=2))
            return 0
        if args.action == "probe":
            # Explicit, tiny provider check; never source from the configured repo.
            content = "example_value = 1\n"
            result = CodexBridge(enabled=True, timeout_seconds=90).propose(
                "Explain this synthetic line in one sentence. Return no edits.",
                [{"path": "example.py", "content": content, "sha256": sha256(content)}],
            )
            if result["edits"]:
                raise JobError("Explanation probe unexpectedly proposed an edit.")
            print(
                json.dumps(
                    {
                        "probe": "passed",
                        "synthetic_only": True,
                        "source_applied": False,
                        "summary": result["summary"],
                    }
                )
            )
            return 0
        if settings.cloud_demo or not settings.allow_background_jobs:
            raise JobError(
                "Enable background jobs locally; Cloud execution is prohibited."
            )
        if os.getenv("ENABLE_JOB_CODEX", "false").lower() != "true":
            raise JobError("Enable the separate background model worker explicitly.")
        # One worker across this database, not one worker per phone call.
        database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(
            str(database) + ".worker.lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise JobError("A background worker already owns this queue.") from None
            interrupted = store.recover_interrupted()
            print(
                json.dumps(
                    {
                        "worker": "ready",
                        "max_jobs": args.max_jobs,
                        "interrupted_not_replayed": interrupted,
                    }
                ),
                flush=True,
            )
            repository = RepositoryService(settings.repo_root.resolve())
            bridge = CodexBridge(enabled=True, timeout_seconds=90)
            processed = 0
            while processed < args.max_jobs:
                if process_next(store, repository, settings.proposals_path, bridge):
                    processed += 1
                    print(
                        json.dumps({"processed": processed, "source_applied": False}),
                        flush=True,
                    )
                elif args.once:
                    break
                else:
                    time.sleep(2)
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "error": getattr(error, "code", "job_operation_failed"),
                    "message": str(error)
                    if isinstance(error, JobError)
                    else "Job operation failed; inspect safe local status.",
                }
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
