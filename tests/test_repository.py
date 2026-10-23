"""Local source boundaries and subprocess controls need no LiveKit credentials."""

import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from repository import RepositoryError, RepositoryService


def git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Portfolio Test")
    git(root, "config", "user.email", "portfolio@example.invalid")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text(
        "first line\nneedle = 'ordinary source'\n", encoding="utf-8"
    )
    (root / "README.md").write_text("Example source repository.\n", encoding="utf-8")
    (root / ".env").write_text("needle=DO_NOT_TRANSMIT\n", encoding="utf-8")
    (root / "passwords.txt").write_text("needle=DO_NOT_TRANSMIT\n", encoding="utf-8")
    (root / ".private").mkdir()
    (root / ".private" / "notes.md").write_text(
        "needle=DO_NOT_TRANSMIT\n", encoding="utf-8"
    )
    git(root, "add", ".")
    git(root, "commit", "-m", "Initial synthetic source")
    return root


def test_only_exact_checkout_root_is_accepted(checkout: Path) -> None:
    with pytest.raises(RepositoryError, match="exact top-level"):
        RepositoryService(checkout / "src")


def test_read_search_status_and_diff_exclude_secrets_before_open(
    checkout: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RepositoryService(checkout)
    original = service._read_bytes
    opened = []

    def guarded(path: str) -> bytes:
        assert path not in {".env", "passwords.txt", ".private/notes.md"}
        opened.append(path)
        return original(path)

    monkeypatch.setattr(service, "_read_bytes", guarded)
    for filename in (".env", "passwords.txt", ".private/notes.md"):
        with pytest.raises(RepositoryError) as error:
            service.read_file(filename)
        assert error.value.code == "blocked_path"
        with pytest.raises(RepositoryError):
            service.git_diff(filename)
    (checkout / "src" / "app.py").write_text("needle = 'updated'\n", encoding="utf-8")
    (checkout / ".env").write_text("needle=CHANGED_SECRET\n", encoding="utf-8")
    assert service.search("needle")["matches"][0]["path"] == "src/app.py"
    assert service.git_status()["entries"] == [{"status": " M", "path": "src/app.py"}]
    diff = service.git_diff()["output"]
    assert "updated" in diff
    assert "CHANGED_SECRET" not in diff
    assert ".env" not in diff
    assert opened


def test_reads_are_line_scoped_and_untracked_needs_explicit_allowlist(
    checkout: Path,
) -> None:
    service = RepositoryService(checkout)
    assert (
        service.read_file("src/app.py", start=2, lines=1)["content"]
        == "needle = 'ordinary source'\n"
    )
    (checkout / "new.py").write_text("new source\n", encoding="utf-8")
    with pytest.raises(RepositoryError) as error:
        service.read_file("new.py")
    assert error.value.code == "unapproved_file"
    approved = RepositoryService(checkout, allowed_untracked=["new.py"])
    assert approved.resolve_file("new.py") == checkout / "new.py"
    assert "new.py" in approved.allowed_files()
    assert approved.read_file("new.py")["content"] == "new source\n"


@pytest.mark.parametrize(
    "path",
    [
        "../outside.py",
        "/etc/passwd",
        "src/../../x.py",
        ".git/config",
        ".env.local",
        "private_key.py",
    ],
)
def test_traversal_and_sensitive_locations_are_rejected(
    checkout: Path, path: str
) -> None:
    with pytest.raises(RepositoryError):
        RepositoryService(checkout).read_file(path)


def test_symlink_files_and_parent_directories_cannot_escape(
    checkout: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("NEVER_SEARCH_THIS\n", encoding="utf-8")
    (checkout / "link.py").symlink_to(outside)
    (checkout / "linked").symlink_to(tmp_path, target_is_directory=True)
    git(checkout, "add", "link.py", "linked")
    service = RepositoryService(checkout, allowed_untracked=["linked/outside.py"])
    for path in ("link.py", "linked/outside.py"):
        with pytest.raises(RepositoryError):
            service.read_file(path)
    assert service.search("NEVER_SEARCH_THIS")["matches"] == []


def test_binary_content_is_not_transmitted_even_with_source_extension(
    checkout: Path,
) -> None:
    (checkout / "binary.py").write_bytes(b"needle\x00sensitive binary")
    git(checkout, "add", "binary.py")
    service = RepositoryService(checkout)
    with pytest.raises(RepositoryError) as error:
        service.read_file("binary.py")
    assert error.value.code == "binary_file"
    assert all(m["path"] != "binary.py" for m in service.search("needle")["matches"])


def test_queries_are_literal_and_never_shell_fragments(checkout: Path) -> None:
    (checkout / "src" / "app.py").write_text(
        "--help\n$(touch PWNED)\n", encoding="utf-8"
    )
    service = RepositoryService(checkout)
    assert service.search("--help")["matches"][0]["text"] == "--help"
    assert service.search("$(touch PWNED)")["matches"][0]["text"] == "$(touch PWNED)"
    assert not (checkout / "PWNED").exists()
    assert service.search("--help", glob="*.md")["matches"] == []


def test_test_suites_are_disabled_by_default_and_blocked_on_sip(checkout: Path) -> None:
    suites = {"smoke": [sys.executable, "-c", "print('synthetic pass')"]}
    with pytest.raises(RepositoryError) as disabled:
        RepositoryService(checkout, test_suites=suites).run_tests("smoke")
    assert disabled.value.code == "tests_disabled"
    service = RepositoryService(checkout, test_suites=suites, allow_tests=True)
    with pytest.raises(RepositoryError) as sip:
        service.run_tests("smoke", transport="sip")
    assert sip.value.code == "transport_blocked"
    with pytest.raises(RepositoryError) as arbitrary:
        service.run_tests("python -c 'arbitrary'", transport="browser")
    assert arbitrary.value.code == "unknown_suite"
    result = service.run_tests("smoke", transport="browser")
    assert result["returncode"] == 0
    assert result["output"] == "synthetic pass\n"


def test_output_and_file_reads_are_bounded(checkout: Path) -> None:
    service = RepositoryService(
        checkout,
        max_output_chars=256,
        allow_tests=True,
        test_suites={"noisy": [sys.executable, "-c", "print('x' * 100000)"]},
    )
    result = service.run_tests("noisy")
    assert result["truncated"] is True
    assert len(result["output"]) <= 256
    (checkout / "src" / "app.py").write_text("x" * 1000, encoding="utf-8")
    read = service.read_file("src/app.py")
    assert read["truncated"] is True
    assert len(read["content"]) == 256


def test_timeout_kills_child_process_group(checkout: Path) -> None:
    marker = checkout / "CHILD_SURVIVED"
    child = "import pathlib,time;time.sleep(1.8);pathlib.Path('CHILD_SURVIVED').write_text('bad')"
    parent = f"import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',{child!r}]);print('spawned',flush=True);time.sleep(10)"
    service = RepositoryService(
        checkout,
        timeout_seconds=1,
        allow_tests=True,
        test_suites={"slow": [sys.executable, "-c", parent]},
    )
    started = time.monotonic()
    result = service.run_tests("slow")
    assert result["timed_out"] is True
    assert "spawned" in result["output"]
    assert time.monotonic() - started < 4
    time.sleep(2)
    assert not marker.exists()


def test_git_root_environment_cannot_redirect_commands(
    checkout: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "incorrect"))
    assert (
        "Initial synthetic source"
        in RepositoryService(checkout).git_history()["output"]
    )


def test_invalid_ranges_are_rejected(checkout: Path) -> None:
    service = RepositoryService(checkout)
    for start, lines in ((0, 2), (1, 201), (True, 3)):
        with pytest.raises(RepositoryError):
            service.read_file("src/app.py", start=start, lines=lines)


@pytest.mark.parametrize("operation", ["search", "git_diff"])
def test_symlink_swap_after_validation_cannot_change_snapshot(
    checkout: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("needle=EXTERNAL_SECRET\n", encoding="utf-8")
    target = checkout / "src" / "app.py"
    target.write_text("needle=APPROVED_SOURCE\n", encoding="utf-8")
    service = RepositoryService(checkout)
    original = service._read_bytes
    swapped = False

    def swap_after_read(path: str) -> bytes:
        nonlocal swapped
        result = original(path)
        if path == "src/app.py" and not swapped:
            target.unlink()
            target.symlink_to(outside)
            swapped = True
        return result

    monkeypatch.setattr(service, "_read_bytes", swap_after_read)
    if operation == "search":
        result = service.search("needle")
    else:
        result = service.git_diff("src/app.py")
    assert "EXTERNAL_SECRET" not in str(result)
    assert "APPROVED_SOURCE" in str(result)


def test_large_diff_returns_bounded_partial_output(checkout: Path) -> None:
    (checkout / "src" / "app.py").write_text("added line\n" * 200, encoding="utf-8")
    result = RepositoryService(checkout, max_output_chars=256).git_diff("src/app.py")
    assert result["truncated"] is True
    assert len(result["output"]) <= 256


def test_provider_credentials_are_not_inherited_by_local_commands(
    checkout: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIVEKIT_API_SECRET", "FAKE_ENV_SECRET")
    monkeypatch.setenv("OPENAI_API_KEY", "FAKE_ENV_SECRET")
    monkeypatch.setenv("CUSTOM_TOKEN", "FAKE_ENV_SECRET")
    service = RepositoryService(
        checkout,
        allow_tests=True,
        test_suites={
            "env": [sys.executable, "-c", "import os;print(sorted(os.environ.items()))"]
        },
    )
    output = service.run_tests("env")["output"]
    assert "FAKE_ENV_SECRET" not in output
    assert "LIVEKIT_API_SECRET" not in output
    assert "CUSTOM_TOKEN" not in output


def test_snapshot_binds_digest_to_the_returned_content(checkout: Path) -> None:
    import hashlib

    result = RepositoryService(checkout).snapshot("src/app.py")
    assert result["sha256"] == hashlib.sha256(result["content"].encode()).hexdigest()
    assert result["path"] == "src/app.py"


def test_missing_search_binary_fails_clearly_without_unsafe_fallback(
    checkout: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git_binary = shutil.which("git")
    assert git_binary is not None
    binaries = tmp_path / "git-only-bin"
    binaries.mkdir()
    (binaries / "git").symlink_to(git_binary)
    monkeypatch.setenv("PATH", str(binaries))
    service = RepositoryService(checkout)
    assert "needle" in service.read_file("src/app.py")["content"]
    with pytest.raises(RepositoryError) as error:
        service.search("needle")
    assert error.value.code == "command_unavailable"
    assert "needle" not in str(error.value)
