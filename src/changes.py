"""Stage existing-file changes; only a separate local CLI can apply them."""

import difflib
import hashlib
import json
import os
import re
import stat
import uuid
from contextlib import suppress
from pathlib import Path

from repository import RepositoryService


class ChangeError(ValueError):
    pass


def sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def review_digest(proposal: dict) -> str:
    """Bind the CLI's displayed proposal to the same artifact at apply time."""
    return sha256(json.dumps(proposal, sort_keys=True, separators=(",", ":")))


def _diff(path: str, original: str, replacement: str) -> str:
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            replacement.splitlines(keepends=True),
            fromfile="a/" + path,
            tofile="b/" + path,
        )
    )


class ChangeStore:
    def __init__(
        self,
        repository: RepositoryService,
        directory: str | Path,
        *,
        max_file_bytes: int = 128_000,
    ):
        if type(max_file_bytes) is not int or not 128_000 <= max_file_bytes <= 512_000:
            raise ChangeError("Proposal limit must be between 128000 and 512000 bytes.")
        self.repository = repository
        self.directory = Path(directory).resolve()
        self.max_file_bytes = max_file_bytes

    def stage(self, path: str, new_content: str, original_sha256: str) -> dict:
        """Save a review artifact; the repository file remains unchanged."""
        if (
            not isinstance(new_content, str)
            or len(new_content.encode("utf-8")) > self.max_file_bytes
        ):
            raise ChangeError(
                f"A proposal must contain at most {self.max_file_bytes} UTF-8 bytes."
            )
        snapshot = self.repository.snapshot(path)
        path, old = snapshot["path"], snapshot["content"]
        if sha256(old) != original_sha256:
            raise ChangeError(
                "The source changed. Read the current file and propose again."
            )
        if old == new_content:
            raise ChangeError("The proposal makes no change.")
        if "\x00" in new_content:
            raise ChangeError("Only UTF-8 text changes are supported.")
        identifier = uuid.uuid4().hex
        diff = _diff(path, old, new_content)
        proposal = {
            "id": identifier,
            "repo_root": str(self.repository.root),
            "path": path,
            "original_sha256": original_sha256,
            "new_sha256": sha256(new_content),
            "new_content": new_content,
            "diff": diff,
            "status": "pending",
        }
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = self.directory / (identifier + ".json")
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(proposal, stream, indent=2)
        return {
            "status": "pending_local_review",
            "id": identifier,
            "path": path,
            "diff": diff[:12000],
            "diff_truncated": len(diff) > 12000,
            "review_file": str(destination),
            "message": "Source is unchanged. Review and apply from the local terminal.",
        }

    def read(self, identifier: str) -> dict:
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise ChangeError("Invalid proposal ID.")
        proposal = json.loads((self.directory / (identifier + ".json")).read_text())
        if (
            not isinstance(proposal, dict)
            or proposal.get("id") != identifier
            or proposal.get("repo_root") != str(self.repository.root)
        ):
            raise ChangeError("Proposal belongs to a different repository.")
        replacement = proposal.get("new_content")
        if (
            not isinstance(replacement, str)
            or "\x00" in replacement
            or len(replacement.encode("utf-8")) > self.max_file_bytes
            or sha256(replacement) != proposal.get("new_sha256")
        ):
            raise ChangeError("Proposal contents failed their integrity check.")
        snapshot = self.repository.snapshot(proposal.get("path"))
        if snapshot["sha256"] != proposal.get("original_sha256"):
            raise ChangeError(
                "Source changed after the proposal. Review the current file and propose again."
            )
        expected = _diff(snapshot["path"], snapshot["content"], replacement)
        if (
            not expected
            or proposal.get("path") != snapshot["path"]
            or proposal.get("diff") != expected
        ):
            raise ChangeError(
                "Proposal diff does not match its source and replacement."
            )
        return proposal

    def apply(
        self,
        identifier: str,
        *,
        local_approval: bool = False,
        reviewed_digest: str | None = None,
    ) -> dict:
        """Called by tools/review_change.py, never registered as an agent tool."""
        if local_approval is not True:
            raise ChangeError("Review the diff and supply explicit local approval.")
        proposal = self.read(identifier)
        if reviewed_digest is not None and review_digest(proposal) != reviewed_digest:
            raise ChangeError(
                "Proposal changed after display. Review it again before applying."
            )
        self.repository.resolve_file(proposal["path"])
        if (
            self.repository.snapshot(proposal["path"])["sha256"]
            != proposal["original_sha256"]
        ):
            raise ChangeError(
                "Source changed after the proposal. Refusing a stale overwrite."
            )
        # Open the directory chain without following links. The stage and apply
        # boundaries both revalidate paths; no patch-supplied command is executed.
        parent_fd = os.open(self.repository.root, os.O_RDONLY | os.O_DIRECTORY)
        temporary = ".voice-change-" + uuid.uuid4().hex
        filename = Path(proposal["path"]).name
        try:
            for component in Path(proposal["path"]).parts[:-1]:
                child_fd = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent_fd,
                )
                os.close(parent_fd)
                parent_fd = child_fd
            source_fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
            with os.fdopen(source_fd, "rb") as source:
                source_info = os.fstat(source.fileno())
                current_sha = hashlib.sha256(source.read(512_001)).hexdigest()
            if current_sha != proposal["original_sha256"]:
                raise ChangeError("Source changed before saving. Propose again.")
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                stat.S_IMODE(source_info.st_mode),
                dir_fd=parent_fd,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.write(proposal["new_content"])
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, filename, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=parent_fd)
            os.close(parent_fd)
        return {"status": "applied_locally", "id": identifier, "path": proposal["path"]}
