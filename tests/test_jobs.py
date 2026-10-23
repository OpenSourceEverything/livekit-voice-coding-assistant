"""Durable job and proposal boundaries; fake model, no account/network calls."""

import json
import stat
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from changes import ChangeStore, sha256
from jobs import JobError, JobStore, process_next
from repository import RepositoryService


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    (root / "app.py").write_text("first = 1\nvalue = 256\nlast = 3\n")
    (root / ".env").write_text("EXAMPLE_SECRET=not-for-model\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    repository = RepositoryService(root)
    database = tmp_path / "private" / "jobs.sqlite3"
    return repository, database, tmp_path / "proposals"


def submit(store, repository, **kwargs):
    source = repository.snapshot("app.py")
    return store.submit(
        repository, "Reduce the value", "app.py", source["sha256"], 2, 1, **kwargs
    )


class FakeBridge:
    enabled = True

    def propose(self, request, files):
        source = files[0]
        assert source["content"] == "value = 256\n"
        assert source["sha256"] == sha256(source["content"])
        return {
            "summary": "Reduce the example value; human review required.",
            "edits": [
                {
                    "path": source["path"],
                    "original_sha256": source["sha256"],
                    "new_content": "value = 128\n",
                }
            ],
        }


def test_saved_before_ack_reopens_after_call_and_idempotent(setup):
    """Commit the request before acknowledgment and deduplicate repeated submits."""
    repository, database, _ = setup
    queued = submit(JobStore(database), repository)
    reopened = JobStore(database)
    assert reopened.status(queued["job_id"])["state"] == "queued"
    assert submit(reopened, repository)["job_id"] == queued["job_id"]
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700


def test_separate_worker_stages_only_excerpt_and_source_unchanged(setup):
    """Send only the selected excerpt and stage a review without editing source."""
    repository, database, proposals = setup
    queued = submit(JobStore(database), repository)
    before = repository.snapshot("app.py")
    assert process_next(JobStore(database), repository, proposals, FakeBridge())
    status = JobStore(database).status(queued["job_id"])
    assert status["state"] == "review_ready"
    artifact = ChangeStore(repository, proposals, max_file_bytes=512_000).read(
        status["proposal_ids"][0]
    )
    assert artifact["new_content"] == "first = 1\nvalue = 128\nlast = 3\n"
    assert repository.snapshot("app.py") == before
    assert "new_content" not in json.dumps(status)
    assert not process_next(JobStore(database), repository, proposals, FakeBridge())


def test_stale_source_fails_before_calling_model(setup):
    """Compare the queued source hash before spending a model request."""
    repository, database, proposals = setup
    queued = submit(JobStore(database), repository)
    (repository.root / "app.py").write_text("someone else changed this\n")

    class NeverBridge:
        enabled = True

        def propose(self, *_args):
            pytest.fail("Stale source must not reach a model")

    process_next(JobStore(database), repository, proposals, NeverBridge())
    assert JobStore(database).status(queued["job_id"])["error_code"] == "stale_source"


def test_excerpt_cannot_join_untouched_following_line(setup):
    """Preserve line boundaries so excerpt replacement cannot merge adjacent code."""
    repository, database, proposals = setup
    queued = submit(JobStore(database), repository)
    before = repository.snapshot("app.py")

    class MissingBoundary(FakeBridge):
        def propose(self, request, files):
            result = super().propose(request, files)
            result["edits"][0]["new_content"] = "value = 128"
            return result

    process_next(JobStore(database), repository, proposals, MissingBoundary())
    assert JobStore(database).status(queued["job_id"])["state"] == "failed"
    assert repository.snapshot("app.py") == before
    assert not proposals.exists()


def test_source_changed_during_model_cannot_stage(setup):
    """Recheck after model work to catch concurrent edits during the request."""
    repository, database, proposals = setup
    queued = submit(JobStore(database), repository)

    class EditingBridge(FakeBridge):
        def propose(self, request, files):
            result = super().propose(request, files)
            (repository.root / "app.py").write_text("concurrent edit\n")
            return result

    process_next(JobStore(database), repository, proposals, EditingBridge())
    assert JobStore(database).status(queued["job_id"])["state"] == "failed"
    assert not proposals.exists()


def test_single_transaction_claim_prevents_two_workers(setup):
    """Use the database claim transaction so workers cannot own one job twice."""
    repository, database, _ = setup
    queued = submit(JobStore(database), repository)

    def claim(_):
        return JobStore(database).claim(repository.root)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, range(2)))
    assert sum(item is not None for item in claims) == 1
    assert next(item for item in claims if item)["id"] == queued["job_id"]


def test_crashed_running_job_is_not_automatically_replayed(setup):
    """Require explicit recovery because a crashed request may already be billed."""
    repository, database, _ = setup
    store = JobStore(database)
    queued = submit(store, repository)
    assert store.claim(repository.root)
    assert store.recover_interrupted() == 1
    assert store.status(queued["job_id"])["state"] == "interrupted"
    assert store.claim(repository.root) is None


@pytest.mark.parametrize(
    "path,start,lines",
    [
        (".env", 1, 1),
        ("../escape", 1, 1),
        ("app.py", 0, 1),
        ("app.py", 1, 201),
        ("app.py", 500, 1),
    ],
)
def test_bad_scope_and_ranges_rejected(setup, path, start, lines):
    repository, database, _ = setup
    with pytest.raises((JobError, ValueError)):
        JobStore(database).submit(
            repository, "Change this", path, "0" * 64, start, lines
        )


def test_model_errors_sanitized_and_no_source_changes(setup):
    """Store safe error codes and leave repository contents unchanged on failure."""
    repository, database, proposals = setup
    queued = submit(JobStore(database), repository)

    class BadBridge:
        enabled = True

        def propose(self, *_args):
            raise RuntimeError("PRIVATE_SECRET_SHOULD_NOT_APPEAR")

    process_next(JobStore(database), repository, proposals, BadBridge())
    status = JobStore(database).status(queued["job_id"])
    assert status["state"] == "failed"
    assert "PRIVATE_SECRET" not in json.dumps(status)
    assert not proposals.exists()


def test_wrong_repo_cannot_claim_and_symlink_database_rejected(setup, tmp_path):
    repository, database, _ = setup
    submit(JobStore(database), repository)
    assert JobStore(database).claim(tmp_path) is None
    with pytest.raises(JobError):
        JobStore(database).status(1, tmp_path)
    alias = tmp_path / "linked.sqlite3"
    alias.symlink_to(database)
    with pytest.raises(JobError):
        JobStore(alias).status(1)


def test_proposal_limit_opt_in_handles_large_ui_without_sending_whole_file(setup):
    repository, database, proposals = setup
    content = "# retained large source\n" * 8000 + "value = 256\n"
    (repository.root / "app.py").write_text(content)
    source = repository.snapshot("app.py")
    store = JobStore(database)
    queued = store.submit(
        repository, "Reduce value", "app.py", source["sha256"], 8001, 1
    )
    process_next(store, repository, proposals, FakeBridge())
    status = store.status(queued["job_id"])
    assert status["state"] == "review_ready"
    artifact = ChangeStore(repository, proposals, max_file_bytes=512_000).read(
        status["proposal_ids"][0]
    )
    assert artifact["new_content"] == content[: -len("value = 256\n")] + "value = 128\n"
    assert repository.snapshot("app.py") == source


def test_queue_has_bounded_pending_work_and_status_checks_ids(setup):
    repository, database, _ = setup
    store = JobStore(database)
    source = repository.snapshot("app.py")
    for index in range(3):
        store.submit(repository, f"Request {index}", "app.py", source["sha256"], 2, 1)
    with pytest.raises(JobError, match="full"):
        store.submit(repository, "Fourth", "app.py", source["sha256"], 2, 1)
    for invalid in (True, 0, -1, "1", "../outside"):
        with pytest.raises(JobError):
            store.status(invalid)
