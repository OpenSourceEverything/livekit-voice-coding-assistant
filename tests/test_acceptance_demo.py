"""One executable workflow joins the real SDK tools, service, and approval CLI."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.acceptance_demo import CHECKS, ROOT, run_acceptance


@pytest.mark.asyncio
async def test_complete_acceptance_uses_real_services_and_cleans_disposable_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "var" / "demo-repo").mkdir(parents=True)
    marker = tmp_path / "var" / "demo-repo" / "untouched.txt"
    marker.write_text("existing workspace\n", encoding="utf-8")
    result = await run_acceptance()
    assert result["status"] == "passed"
    assert result["checks"] == list(CHECKS)
    assert len(result["checks"]) == 15
    assert result["evidence"]["test_exit_before"] == 1
    assert result["evidence"]["test_exit_after"] == 0
    assert result["evidence"]["cli_unapproved_apply_exit"] == 2
    assert result["evidence"]["cli_approved_apply_exit"] == 0
    assert result["evidence"]["cli_stale_apply_exit"] == 2
    assert result["evidence"]["timeout_enforced"] is True
    assert result["provider_calls"] == 0
    assert result["existing_workspaces_modified"] is False
    assert marker.read_text() == "existing workspace\n"
    assert not (tmp_path / "var" / "proposals").exists()


def test_acceptance_cli_outputs_sanitized_repeatable_json_without_provider_credentials(
    tmp_path: Path,
) -> None:
    environment = {
        name: os.environ[name]
        for name in ("PATH", "HOME", "LANG", "LC_ALL")
        if name in os.environ
    }
    environment["LIVEKIT_API_SECRET"] = "SYNTHETIC_SECRET_MUST_NOT_APPEAR"
    environment["OPENAI_API_KEY"] = "SYNTHETIC_SECRET_MUST_NOT_APPEAR"
    command = [sys.executable, str(ROOT / "tools/acceptance_demo.py"), "--json"]
    runs = [
        subprocess.run(
            command,
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        for _ in range(2)
    ]
    for result in runs:
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stderr == ""
        assert "SYNTHETIC_SECRET_MUST_NOT_APPEAR" not in result.stdout
        assert str(tmp_path) not in result.stdout
        assert "coding-buddy-acceptance-" not in result.stdout
        assert json.loads(result.stdout)["status"] == "passed"
    assert runs[0].stdout == runs[1].stdout


def test_acceptance_failure_is_reported_without_raw_exception_or_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    from tools import acceptance_demo

    async def fail() -> None:
        raise OSError("SYNTHETIC_SECRET_MUST_NOT_APPEAR")

    monkeypatch.setattr(acceptance_demo, "run_acceptance", fail)
    monkeypatch.setattr(sys, "argv", ["acceptance_demo.py", "--json"])
    assert acceptance_demo.main() == 1
    result = capsys.readouterr()
    assert json.loads(result.out) == {
        "status": "failed",
        "check": "local_execution_error",
    }
    assert result.err == ""


def test_clean_pytest_collection_does_not_depend_on_developer_pythonpath() -> None:
    environment = {
        name: os.environ[name]
        for name in ("PATH", "HOME", "LANG", "LC_ALL")
        if name in os.environ
    }
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "tests/test_acceptance_demo.py",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_complete_acceptance_uses_real_services" in result.stdout
    assert "tests collected" in result.stdout


def test_importing_offline_tool_layer_does_not_initialize_onnx() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys; "
            f"sys.path.insert(0, {str(ROOT / 'src')!r}); "
            "import agent; "
            "assert 'onnxruntime' not in sys.modules; "
            "assert 'livekit.plugins.silero' not in sys.modules",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
