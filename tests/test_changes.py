"""Existing-file proposals must remain separate from explicit local application."""

import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from changes import ChangeError, ChangeStore, review_digest, sha256
from repository import RepositoryError, RepositoryService

SOURCE = "def answer():\n    return 41\n"
REPLACEMENT = "def answer():\n    return 42\n"


@pytest.fixture
def store(tmp_path: Path) -> ChangeStore:
    root = tmp_path / "checkout"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    (root / "app.py").write_text(SOURCE, encoding="utf-8")
    (root / ".env").write_text("SYNTHETIC_SECRET=do-not-read\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    return ChangeStore(RepositoryService(root), tmp_path / "proposals")


def test_stage_creates_private_review_artifact_and_leaves_source_unchanged(
    store: ChangeStore,
) -> None:
    result = store.stage("app.py", REPLACEMENT, sha256(SOURCE))
    assert result["status"] == "pending_local_review"
    assert (store.repository.root / "app.py").read_text() == SOURCE
    assert "-    return 41" in result["diff"]
    assert "+    return 42" in result["diff"]
    artifact = Path(result["review_file"])
    assert artifact.is_file()
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
    assert store.read(result["id"])["new_content"] == REPLACEMENT


@pytest.mark.parametrize("approval", [False, None, 1, "yes"])
def test_apply_requires_literal_local_approval(
    store: ChangeStore, approval: object
) -> None:
    proposal = store.stage("app.py", REPLACEMENT, sha256(SOURCE))
    with pytest.raises(ChangeError, match="local approval"):
        store.apply(proposal["id"], local_approval=approval)
    assert (store.repository.root / "app.py").read_text() == SOURCE


def test_local_approval_applies_reviewed_content_and_preserves_mode(
    store: ChangeStore,
) -> None:
    path = store.repository.root / "app.py"
    path.chmod(0o640)
    proposal = store.stage("app.py", REPLACEMENT, sha256(SOURCE))
    result = store.apply(proposal["id"], local_approval=True)
    assert result["status"] == "applied_locally"
    assert path.read_text() == REPLACEMENT
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert not list(path.parent.glob(".voice-change-*"))


def test_staging_refuses_stale_original_hash(store: ChangeStore) -> None:
    with pytest.raises(ChangeError, match="source changed"):
        store.stage("app.py", REPLACEMENT, sha256("obsolete source"))
    assert not store.directory.exists()


def test_source_edit_after_staging_cannot_be_overwritten(store: ChangeStore) -> None:
    proposal = store.stage("app.py", REPLACEMENT, sha256(SOURCE))
    path = store.repository.root / "app.py"
    path.write_text("independent concurrent edit\n", encoding="utf-8")
    with pytest.raises(ChangeError, match="changed"):
        store.apply(proposal["id"], local_approval=True)
    assert path.read_text() == "independent concurrent edit\n"


@pytest.mark.parametrize(
    "path", ["../outside.py", "/etc/passwd", ".env", ".git/config", "new.py"]
)
def test_invalid_or_restricted_paths_cannot_be_staged(
    store: ChangeStore, path: str
) -> None:
    with pytest.raises((ChangeError, RepositoryError)):
        store.stage(path, REPLACEMENT, sha256(SOURCE))
    assert not store.directory.exists()


def test_symlink_path_cannot_be_staged_or_applied(
    store: ChangeStore, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text(SOURCE, encoding="utf-8")
    path = store.repository.root / "app.py"
    proposal = store.stage("app.py", REPLACEMENT, sha256(SOURCE))
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises((ChangeError, RepositoryError, OSError)):
        store.stage("app.py", REPLACEMENT, sha256(SOURCE))
    with pytest.raises((ChangeError, RepositoryError, OSError)):
        store.apply(proposal["id"], local_approval=True)
    assert outside.read_text() == SOURCE


@pytest.mark.parametrize(
    "field,value",
    [
        ("repo_root", "/different/checkout"),
        ("id", "0" * 32),
        ("new_sha256", "0" * 64),
        ("new_content", "tampered replacement\n"),
    ],
)
def test_tampered_proposal_identity_or_content_hash_is_rejected(
    store: ChangeStore,
    field: str,
    value: str,
) -> None:
    result = store.stage("app.py", REPLACEMENT, sha256(SOURCE))
    artifact = Path(result["review_file"])
    proposal = json.loads(artifact.read_text())
    proposal[field] = value
    artifact.write_text(json.dumps(proposal), encoding="utf-8")
    with pytest.raises(ChangeError):
        store.apply(result["id"], local_approval=True)
    assert (store.repository.root / "app.py").read_text() == SOURCE


def test_tampered_proposal_cannot_target_a_sensitive_path(store: ChangeStore) -> None:
    result = store.stage("app.py", REPLACEMENT, sha256(SOURCE))
    artifact = Path(result["review_file"])
    proposal = json.loads(artifact.read_text())
    proposal["path"] = ".env"
    artifact.write_text(json.dumps(proposal), encoding="utf-8")
    with pytest.raises((ChangeError, RepositoryError)):
        store.apply(result["id"], local_approval=True)
    assert (
        store.repository.root / ".env"
    ).read_text() == "SYNTHETIC_SECRET=do-not-read\n"


@pytest.mark.parametrize("identifier", ["../outside", "xyz", "0" * 33, ""])
def test_proposal_ids_cannot_traverse_review_directory(
    store: ChangeStore, identifier: str
) -> None:
    with pytest.raises(ChangeError, match="Invalid proposal"):
        store.read(identifier)


@pytest.mark.parametrize("replacement", [SOURCE, "bad\x00text", "x" * 128_001])
def test_noop_binary_and_oversized_proposals_are_rejected(
    store: ChangeStore,
    replacement: str,
) -> None:
    with pytest.raises(ChangeError):
        store.stage("app.py", replacement, sha256(SOURCE))
    assert not store.directory.exists()


def test_crlf_hash_and_bytes_survive_staging_and_application(
    store: ChangeStore,
) -> None:
    path = store.repository.root / "app.py"
    original = "def answer():\r\n    return 41\r\n"
    replacement = "def answer():\r\n    return 42\r\n"
    path.write_bytes(original.encode())
    proposal = store.stage("app.py", replacement, sha256(original))
    assert path.read_bytes() == original.encode()
    store.apply(proposal["id"], local_approval=True)
    assert path.read_bytes() == replacement.encode()


@pytest.mark.parametrize("tamper", ["diff", "replacement_and_hash"])
def test_self_inconsistent_review_artifact_cannot_be_shown_or_applied(
    store: ChangeStore,
    tamper: str,
) -> None:
    result = store.stage("app.py", REPLACEMENT, sha256(SOURCE))
    artifact = Path(result["review_file"])
    proposal = json.loads(artifact.read_text())
    if tamper == "diff":
        proposal["diff"] = "An innocent-looking review that hides the actual edit.\n"
    else:
        proposal["new_content"] = "def answer():\n    return 999\n"
        proposal["new_sha256"] = sha256(proposal["new_content"])
    artifact.write_text(json.dumps(proposal), encoding="utf-8")
    with pytest.raises(ChangeError, match="diff does not match"):
        store.read(result["id"])
    with pytest.raises(ChangeError, match="diff does not match"):
        store.apply(result["id"], local_approval=True)
    assert (store.repository.root / "app.py").read_text() == SOURCE


def test_stale_proposal_cannot_show_a_diff_for_a_different_current_source(
    store: ChangeStore,
) -> None:
    result = store.stage("app.py", REPLACEMENT, sha256(SOURCE))
    (store.repository.root / "app.py").write_text(
        "independent edit\n", encoding="utf-8"
    )
    with pytest.raises(ChangeError, match="Source changed"):
        store.read(result["id"])


def test_consistently_replaced_proposal_is_rejected_when_not_the_displayed_artifact(
    store: ChangeStore,
) -> None:
    result = store.stage("app.py", REPLACEMENT, sha256(SOURCE))
    displayed = store.read(result["id"])
    second = store.stage("app.py", "def answer():\n    return 999\n", sha256(SOURCE))
    replacement = store.read(second["id"])
    replacement["id"] = result["id"]
    Path(result["review_file"]).write_text(json.dumps(replacement), encoding="utf-8")
    with pytest.raises(ChangeError, match="changed after display"):
        store.apply(
            result["id"],
            local_approval=True,
            reviewed_digest=review_digest(displayed),
        )
    assert (store.repository.root / "app.py").read_text() == SOURCE


@pytest.mark.parametrize("action", ["show", "apply"])
def test_review_cli_rejects_tampered_diff_before_printing_it(
    store: ChangeStore,
    action: str,
) -> None:
    result = store.stage("app.py", REPLACEMENT, sha256(SOURCE))
    artifact = Path(result["review_file"])
    proposal = json.loads(artifact.read_text())
    proposal["diff"] = "UNVERIFIED_DIFF_MUST_NOT_BE_DISPLAYED"
    artifact.write_text(json.dumps(proposal), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "tools/review_change.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            action,
            result["id"],
            "--repo",
            str(store.repository.root),
            "--store",
            str(store.directory),
            "--approve",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "diff does not match" in completed.stderr
    assert (store.repository.root / "app.py").read_text() == SOURCE
