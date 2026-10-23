"""Bounded, read-only access to one explicitly configured local Git checkout.

File-name restrictions protect known secret locations before opening files. They
are a guardrail, not a secret scanner: configure a checkout suitable for sharing
with the model. Test commands execute repository code and require local opt-in.
"""

from __future__ import annotations

import difflib
import fnmatch
import hashlib
import json
import os
import re
import selectors
import signal
import subprocess
import tempfile
import time
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any


class RepositoryError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


_SENSITIVE = re.compile(
    r"(?:password|passwd|credential|secret|private[_-]?key|access[_-]?token|"
    r"api[_-]?key|auth[_-]?token|id_rsa|id_ed25519|id_ecdsa|id_dsa|"
    r"(?:^|[._-])(?:keys?|tokens?)(?:[._-]|$))",
    re.IGNORECASE,
)
_BLOCKED_DIRS = {
    ".git",
    ".private",
    ".ssh",
    ".aws",
    ".azure",
    ".config",
    ".gnupg",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "var",
}
_TEXT_SUFFIXES = {
    ".py",
    ".pyi",
    ".md",
    ".txt",
    ".rst",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".jsonc",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".html",
    ".css",
    ".scss",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".hpp",
    ".rs",
    ".go",
    ".java",
    ".kt",
    ".swift",
    ".sh",
    ".bash",
    ".sql",
    ".xml",
    ".ini",
    ".cfg",
    ".conf",
    ".cmake",
    ".proto",
    ".graphql",
    ".vue",
    ".svelte",
}
_TEXT_NAMES = {"makefile", "dockerfile", "license", "copying", ".gitignore"}


class RepositoryService:
    """No tool-supplied executable, shell fragment, root, or environment is used."""

    def __init__(
        self,
        root: Path | str,
        *,
        allowed_untracked: Sequence[str] = (),
        test_suites: Mapping[str, Sequence[str]] | None = None,
        allow_tests: bool = False,
        timeout_seconds: float = 10,
        max_output_chars: int = 12_000,
    ):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise RepositoryError(
                "invalid_root", "Repository root must be a directory."
            )
        if not 0.05 <= timeout_seconds <= 120:
            raise RepositoryError("invalid_config", "Timeout must be 0.05-120 seconds.")
        if not 256 <= max_output_chars <= 100_000:
            raise RepositoryError("invalid_config", "Output limit must be 256-100000.")
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars
        self.max_file_bytes = 512_000
        self.allow_tests = allow_tests is True
        self.allowed_untracked = tuple(self._relative(p) for p in allowed_untracked)
        self.test_suites: dict[str, tuple[str, ...]] = {}
        for alias, argv in (test_suites or {}).items():
            if (
                not isinstance(alias, str)
                or not alias
                or isinstance(argv, (str, bytes))
                or not argv
                or any(not isinstance(part, str) or "\x00" in part for part in argv)
            ):
                raise RepositoryError(
                    "invalid_config", "Test suites need fixed argv lists."
                )
            self.test_suites[alias] = tuple(argv)
        result = self._git("rev-parse", "--show-toplevel")
        if result["returncode"] != 0 or Path(result["output"].strip()) != self.root:
            raise RepositoryError(
                "invalid_root", "Configure the exact top-level Git checkout directory."
            )

    @staticmethod
    def _relative(path: str) -> str:
        if not isinstance(path, str) or not path or any(ord(c) < 32 for c in path):
            raise RepositoryError(
                "blocked_path", "Use a normal repository-relative path."
            )
        parsed = PurePosixPath(path)
        if parsed.is_absolute() or ".." in parsed.parts or "\\" in path:
            raise RepositoryError(
                "blocked_path", "Path must remain inside the checkout."
            )
        normalized = parsed.as_posix()
        if normalized == ".":
            raise RepositoryError(
                "blocked_path", "Choose a file, not the repository root."
            )
        return normalized

    @staticmethod
    def _name_allowed(path: str) -> bool:
        parts = PurePosixPath(path).parts
        if any(
            part.lower() in _BLOCKED_DIRS
            or part.lower().startswith(".env")
            or _SENSITIVE.search(part)
            for part in parts
        ):
            return False
        name = parts[-1].lower()
        return name in _TEXT_NAMES or Path(name).suffix in _TEXT_SUFFIXES

    def _read_bytes(self, path: str) -> bytes:
        """Open each component without following links, including racing links."""
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            parts = PurePosixPath(path).parts
            for part in parts[:-1]:
                child = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = child
            file_descriptor = os.open(
                parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=descriptor,
            )
            with os.fdopen(file_descriptor, "rb") as handle:
                import stat

                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise RepositoryError(
                        "blocked_path", "Only regular text files are allowed."
                    )
                data = handle.read(self.max_file_bytes + 1)
        except OSError as exc:
            raise RepositoryError(
                "blocked_path",
                "File is missing, inaccessible, or uses a symbolic link.",
            ) from exc
        finally:
            os.close(descriptor)
        if len(data) > self.max_file_bytes:
            raise RepositoryError(
                "file_too_large", "File exceeds the text-file size limit."
            )
        if b"\x00" in data:
            raise RepositoryError("binary_file", "Binary files are unavailable.")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RepositoryError(
                "binary_file", "Only UTF-8 text files are allowed."
            ) from exc
        return data

    def _tracked(self) -> set[str]:
        result = self._git("ls-files", "-z", output_limit=1_000_000)
        if result["returncode"] != 0 or result["truncated"] or result["timed_out"]:
            raise RepositoryError("git_error", "Cannot safely enumerate tracked files.")
        return set(filter(None, result["output"].split("\x00")))

    def _candidate_names(self) -> list[str]:
        candidates = self._tracked() | set(self.allowed_untracked)
        if len(candidates) > 5000:
            raise RepositoryError(
                "repository_too_large", "Limit the checkout to 5000 files."
            )
        return [
            self._relative(p)
            for p in sorted(candidates)
            if self._name_allowed(self._relative(p))
        ]

    def _allowed_files(self) -> list[str]:
        safe = []
        for candidate in self._candidate_names():
            try:
                self._read_bytes(candidate)
            except RepositoryError:
                continue
            safe.append(candidate)
        return safe

    def _checked(self, path: str) -> str:
        relative = self._relative(path)
        if not self._name_allowed(relative):
            raise RepositoryError(
                "blocked_path", "This file type or location is restricted."
            )
        if relative not in self._tracked() and relative not in self.allowed_untracked:
            raise RepositoryError(
                "unapproved_file", "File must be tracked or locally allowlisted."
            )
        return relative

    def resolve_file(self, path: str) -> Path:
        """Validate an existing approved text file before a local staging action."""
        relative = self._checked(path)
        self._read_bytes(relative)
        return self.root / relative

    def snapshot(self, path: str) -> dict[str, str]:
        """Local staging helper: content and digest from the same safe byte read."""
        relative = self._checked(path)
        data = self._read_bytes(relative)
        return {
            "path": relative,
            "content": data.decode("utf-8"),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def allowed_files(self) -> list[str]:
        return self._allowed_files()

    def read_file(self, path: str, start: int = 1, lines: int = 100) -> dict[str, Any]:
        if (
            type(start) is not int
            or type(lines) is not int
            or start < 1
            or not 1 <= lines <= 200
        ):
            raise RepositoryError("invalid_range", "Use start >= 1 and 1-200 lines.")
        relative = self._checked(path)
        all_lines = self._read_bytes(relative).decode("utf-8").splitlines(keepends=True)
        selected = all_lines[start - 1 : start - 1 + lines]
        content = "".join(selected)
        truncated = len(content) > self.max_output_chars or start - 1 + lines < len(
            all_lines
        )
        return {
            "path": relative,
            "start": start,
            "end": min(start - 1 + len(selected), len(all_lines)),
            "total_lines": len(all_lines),
            "content": content[: self.max_output_chars],
            "truncated": truncated,
        }

    def search(self, query: str, glob: str | None = None) -> dict[str, Any]:
        if (
            not isinstance(query, str)
            or not query
            or len(query) > 256
            or any(ord(c) < 32 for c in query)
        ):
            raise RepositoryError(
                "invalid_query", "Search needs 1-256 plain-text characters."
            )
        if glob is not None and (
            not isinstance(glob, str)
            or len(glob) > 256
            or glob.startswith("/")
            or ".." in PurePosixPath(glob).parts
            or any(ord(c) < 32 for c in glob)
        ):
            raise RepositoryError("invalid_glob", "Use a relative filename glob.")
        paths = [
            p
            for p in self._candidate_names()
            if glob is None or fnmatch.fnmatchcase(p, glob)
        ]
        matches: list[dict[str, Any]] = []
        truncated = False
        # Search an immutable snapshot of bytes opened with O_NOFOLLOW. Running rg
        # against checkout paths after validation would introduce a symlink race.
        starts: list[int] = []
        filenames: list[str] = []
        total_lines = total_bytes = 0
        with tempfile.TemporaryFile() as snapshot:
            for path in paths:
                try:
                    data = self._read_bytes(path)
                except RepositoryError:
                    continue
                if data and not data.endswith(b"\n"):
                    data += b"\n"
                count = data.count(b"\n")
                if total_bytes + len(data) > 4_000_000 or total_lines + count > 100_000:
                    truncated = True
                    break
                starts.append(total_lines + 1)
                filenames.append(path)
                total_lines += count
                total_bytes += len(data)
                snapshot.write(data)
            if not total_bytes:
                return {"query": query, "matches": [], "truncated": truncated}
            snapshot.seek(0)
            result = self._run(
                [
                    "rg",
                    "--no-config",
                    "--json",
                    "--fixed-strings",
                    "--max-count=51",
                    "--max-columns=500",
                    "--max-columns-preview",
                    "--",
                    query,
                ],
                stdin_file=snapshot,
            )
            if result["timed_out"]:
                raise RepositoryError(
                    "timeout", "Repository search exceeded its time limit."
                )
            if result["returncode"] not in (0, 1) and not result["truncated"]:
                raise RepositoryError("search_failed", "ripgrep search failed.")
            for line in result["output"].splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") != "match":
                    continue
                data = record["data"]
                source_index = bisect_right(starts, data["line_number"]) - 1
                matches.append(
                    {
                        "path": filenames[source_index],
                        "line": data["line_number"] - starts[source_index] + 1,
                        "text": data["lines"].get("text", "").rstrip("\n")[:500],
                    }
                )
                if len(matches) >= 50:
                    truncated = True
                    break
            if result["truncated"] or len(json.dumps(matches)) > self.max_output_chars:
                truncated = True
        while matches and len(json.dumps(matches)) > self.max_output_chars:
            matches.pop()
        return {"query": query, "matches": matches, "truncated": truncated}

    def git_status(self) -> dict[str, Any]:
        result = self._git("status", "--porcelain=v1", "-z", "--untracked-files=all")
        if result["returncode"] != 0 or result["timed_out"]:
            raise RepositoryError("git_error", "Git status failed.")
        paths = set(self._allowed_files())
        records = result["output"].split("\x00")
        entries = []
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if len(record) < 4:
                continue
            status, path = record[:2], record[3:]
            if "R" in status or "C" in status:
                index += 1  # The following NUL field is the original filename.
            if path in paths:
                entries.append({"status": status, "path": path})
        return {"entries": entries, "truncated": result["truncated"]}

    def git_history(self, limit: int = 5) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise RepositoryError("invalid_limit", "History limit must be 1-20.")
        result = self._git(
            "log", f"-{limit}", "--date=short", "--format=%h%x09%ad%x09%s"
        )
        if result["timed_out"]:
            raise RepositoryError("timeout", "Git history exceeded its time limit.")
        return {"output": result["output"], "truncated": result["truncated"]}

    def git_diff(self, path: str | None = None) -> dict[str, Any]:
        paths = [self._checked(path)] if path is not None else self._allowed_files()
        output: list[str] = []
        size = 0
        deadline = time.monotonic() + self.timeout_seconds
        for candidate in paths:
            if time.monotonic() > deadline:
                return {"output": "".join(output), "truncated": True}
            current = self._read_bytes(candidate).decode("utf-8")
            # Read the historical Git object, never a subsequently swapped checkout
            # path. No external diff/textconv/filter executable is invoked.
            previous = self._git(
                "cat-file",
                "blob",
                f"HEAD:{candidate}",
                output_limit=self.max_file_bytes + 1,
            )
            if previous["timed_out"]:
                raise RepositoryError("timeout", "Git diff exceeded its time limit.")
            if previous["truncated"]:
                raise RepositoryError(
                    "file_too_large", "Historical file exceeds the diff limit."
                )
            base = previous["output"] if previous["returncode"] == 0 else ""
            if "\x00" in base or "\ufffd" in base:
                continue
            if base == current:
                continue
            old_lines = base.splitlines(keepends=True)
            new_lines = current.splitlines(keepends=True)
            if len(old_lines) + len(new_lines) > 20_000:
                raise RepositoryError(
                    "file_too_large", "Choose a file with fewer diff lines."
                )
            for line in difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{candidate}",
                tofile=f"b/{candidate}",
            ):
                available = self.max_output_chars - size
                output.append(line[:available])
                size += min(len(line), available)
                if size >= self.max_output_chars:
                    return {"output": "".join(output), "truncated": True}
        return {"output": "".join(output), "truncated": False}

    def run_tests(self, suite: str, *, transport: str = "local") -> dict[str, Any]:
        if not self.allow_tests:
            raise RepositoryError("tests_disabled", "Local test execution is disabled.")
        if transport not in {"local", "browser"}:
            raise RepositoryError(
                "transport_blocked", "Test execution is unavailable on this transport."
            )
        if suite not in self.test_suites:
            raise RepositoryError(
                "unknown_suite", "Choose a locally configured test-suite alias."
            )
        return {"suite": suite, **self._run(list(self.test_suites[suite]))}

    def _git(self, *arguments: str, output_limit: int | None = None) -> dict[str, Any]:
        return self._run(
            [
                "git",
                "--literal-pathspecs",
                "-c",
                "core.pager=cat",
                "-c",
                "color.ui=false",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=/dev/null",
                *arguments,
            ],
            output_limit=output_limit,
        )

    def _run(
        self,
        argv: list[str],
        *,
        output_limit: int | None = None,
        stdin_file: Any = subprocess.DEVNULL,
    ) -> dict[str, Any]:
        limit = output_limit or self.max_output_chars
        # Local commands need no provider credentials from the voice agent's env.
        # Opt-in tests still execute trusted repository code with normal OS access.
        environment = {
            key: os.environ[key]
            for key in ("PATH", "LANG", "LC_ALL", "HOME")
            if key in os.environ
        }
        environment.update(
            {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "NO_COLOR": "1"}
        )
        try:
            process = subprocess.Popen(
                argv,
                cwd=self.root,
                env=environment,
                stdin=stdin_file,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                shell=False,
            )
        except OSError as exc:
            raise RepositoryError(
                "command_unavailable", "A configured local tool is unavailable."
            ) from exc
        assert process.stdout is not None
        output = bytearray()
        truncated = timed_out = False
        deadline = time.monotonic() + self.timeout_seconds
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output.extend(chunk[: max(0, limit - len(output))])
                    if len(output) >= limit:
                        truncated = True
                        break
                if truncated:
                    break
        if timed_out or truncated:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        try:
            process.wait(timeout=max(0.05, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        # A test command must not leave detached background children running.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.stdout.close()
        return {
            "output": output.decode("utf-8", errors="replace")[:limit],
            "returncode": process.returncode,
            "truncated": truncated,
            "timed_out": timed_out,
        }
