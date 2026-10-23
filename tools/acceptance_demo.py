#!/usr/bin/env python3
"""Prove the local tool/review lifecycle in a disposable synthetic Git checkout.

No AgentSession, model adapter, LiveKit connection, or Codex process is started.
The only applied changes are inside the automatically removed temporary fixture.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from livekit.agents.llm import ToolError
from livekit.agents.llm.tool_context import get_fnc_tool_names

from agent import Assistant
from changes import ChangeStore
from repository import RepositoryService

ROOT = Path(__file__).resolve().parents[1]
READ_TOOLS = {
    "workspace_info",
    "search_repository",
    "read_source_file",
    "inspect_git",
}
CHECKS = (
    "tracked_source_search_and_read",
    "git_status_history_and_diff",
    "fixed_test_alias_fails_before_change",
    "proposal_staging_leaves_source_unchanged",
    "local_review_cli_shows_exact_diff",
    "local_review_cli_denies_missing_approval",
    "local_review_cli_applies_approved_change",
    "fixed_test_alias_passes_after_change",
    "local_review_cli_rejects_stale_source",
    "phone_locked_until_authentication",
    "authenticated_phone_read_only",
    "cloud_demo_read_only",
    "unknown_test_alias_denied",
    "fixed_test_alias_timeout_enforced",
    "temporary_workspace_removed",
)
_FIXTURE_TEST = """import pathlib
import unittest


class BufferContract(unittest.TestCase):
    def test_requested_fixture_size(self):
        source = pathlib.Path('uart.c').read_text(encoding='utf-8')
        self.assertIn('static uint8_t uart5_dma_buffer[128];', source)


if __name__ == '__main__':
    unittest.main()
"""


class AcceptanceError(RuntimeError):
    """Expose a stable check name, never raw source or command logs."""


def _require(condition: bool, check: str) -> None:
    if not condition:
        raise AcceptanceError(check)


def _environment() -> dict[str, str]:
    environment = {
        name: os.environ[name]
        for name in ("PATH", "HOME", "LANG", "LC_ALL")
        if name in os.environ
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "NO_COLOR": "1",
        }
    )
    return environment


def _git(root: Path, *arguments: str) -> None:
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *arguments],
        cwd=root,
        env=_environment(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=10,
        check=False,
    )
    _require(result.returncode == 0, "synthetic_git_setup")


def _initialize(root: Path) -> None:
    root.mkdir()
    source = (ROOT / "examples/demo/uart.c").read_text(encoding="utf-8")
    _require(
        "static uint8_t uart5_dma_buffer[256];" in source, "synthetic_fixture_contract"
    )
    (root / "uart.c").write_text(source, encoding="utf-8")
    (root / "test_buffer.py").write_text(_FIXTURE_TEST, encoding="utf-8")
    _git(root, "init", "--quiet")
    _git(root, "add", "--", "uart.c", "test_buffer.py")
    _git(
        root,
        "-c",
        "user.name=Acceptance Demo",
        "-c",
        "user.email=acceptance@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "Add synthetic acceptance fixture",
    )


def _review(
    cwd: Path,
    root: Path,
    store: Path,
    action: str,
    identifier: str,
    *,
    approve: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-I",
        str(ROOT / "tools/review_change.py"),
        action,
        identifier,
        "--repo",
        str(root),
        "--store",
        str(store),
    ]
    if approve:
        command.append("--approve")
    return subprocess.run(
        command,
        cwd=cwd,
        env=_environment(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


async def _denied(operation: Any, check: str) -> None:
    try:
        await operation
    except ToolError:
        return
    raise AcceptanceError(check)


async def run_acceptance() -> dict[str, Any]:
    """Return only stable observations; no paths, identifiers, timings, or logs."""
    evidence: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="coding-buddy-acceptance-") as temporary:
        work = Path(temporary)
        root = work / "source"
        _initialize(root)
        repository = RepositoryService(
            root,
            allow_tests=True,
            test_suites={"fixture": [sys.executable, "-I", "-m", "unittest", "-q"]},
        )
        store = ChangeStore(repository, work / "proposals")
        desktop = Assistant(
            repository,
            store,
            transport="browser",
            allow_proposals=True,
        )
        await desktop.configure_tools()
        original = repository.snapshot("uart.c")

        matches = await desktop.search_repository("uart5_dma_transmit")
        _require(
            bool(matches["matches"]) and matches["matches"][0]["path"] == "uart.c",
            CHECKS[0],
        )
        read = await desktop.read_source_file("uart.c", start_line=1, lines=20)
        _require(
            read["sha256"] == original["sha256"]
            and "uart5_dma_buffer[256]" in read["content"],
            CHECKS[0],
        )
        status = await desktop.inspect_git("status")
        history = await desktop.inspect_git("history")
        initial_diff = await desktop.inspect_git("diff", "uart.c")
        _require(
            status["entries"] == []
            and initial_diff["output"] == ""
            and "Add synthetic acceptance fixture" in history["output"],
            CHECKS[1],
        )
        before = await desktop.run_tests("fixture")
        _require(before["returncode"] == 1 and not before["timed_out"], CHECKS[2])

        replacement = original["content"].replace(
            "static uint8_t uart5_dma_buffer[256];",
            "static uint8_t uart5_dma_buffer[128];",
        )
        proposal = await desktop.propose_file_change(
            "uart.c",
            replacement,
            original["sha256"],
        )
        _require(
            proposal["status"] == "pending_local_review"
            and repository.snapshot("uart.c") == original,
            CHECKS[3],
        )
        shown = _review(work, root, store.directory, "show", proposal["id"])
        _require(
            shown.returncode == 0 and shown.stdout == proposal["diff"] + "\n", CHECKS[4]
        )
        denied = _review(work, root, store.directory, "apply", proposal["id"])
        _require(
            denied.returncode == 2
            and "explicit local approval" in denied.stderr
            and repository.snapshot("uart.c") == original,
            CHECKS[5],
        )
        applied = _review(
            work,
            root,
            store.directory,
            "apply",
            proposal["id"],
            approve=True,
        )
        _require(
            applied.returncode == 0
            and "applied_locally uart.c" in applied.stdout
            and repository.snapshot("uart.c")["content"] == replacement,
            CHECKS[6],
        )
        diff = await desktop.inspect_git("diff", "uart.c")
        _require("+static uint8_t uart5_dma_buffer[128];" in diff["output"], CHECKS[1])
        after = await desktop.run_tests("fixture")
        _require(after["returncode"] == 0 and not after["timed_out"], CHECKS[7])

        current = repository.snapshot("uart.c")
        stale = await desktop.propose_file_change(
            "uart.c",
            replacement + "/* stale proposal */\n",
            current["sha256"],
        )
        concurrent = replacement + "/* independent local edit */\n"
        (root / "uart.c").write_text(concurrent, encoding="utf-8")
        rejected = _review(
            work, root, store.directory, "apply", stale["id"], approve=True
        )
        _require(
            rejected.returncode == 2
            and "Source changed" in rejected.stderr
            and repository.snapshot("uart.c")["content"] == concurrent,
            CHECKS[8],
        )

        phone = Assistant(repository, store, transport="sip", allow_proposals=True)
        await phone.configure_tools()
        _require(get_fnc_tool_names(phone.tools) == [], CHECKS[9])
        await _denied(phone.search_repository("uart5"), CHECKS[9])
        phone.phone_authenticated = True  # Simulates the code gate, not a SIP call.
        await phone.configure_tools()
        _require(set(get_fnc_tool_names(phone.tools)) == READ_TOOLS, CHECKS[10])
        _require(bool((await phone.search_repository("uart5"))["matches"]), CHECKS[10])
        for operation in (
            phone.run_tests("fixture"),
            phone.propose_file_change("uart.c", replacement, current["sha256"]),
            phone.ask_codex_for_proposal("Change the buffer", ["uart.c"]),
        ):
            await _denied(operation, CHECKS[10])
        cloud = Assistant(
            repository,
            store,
            transport="browser",
            cloud_demo=True,
            allow_proposals=True,
        )
        await cloud.configure_tools()
        _require(set(get_fnc_tool_names(cloud.tools)) == READ_TOOLS, CHECKS[11])
        for operation in (
            cloud.run_tests("fixture"),
            cloud.propose_file_change("uart.c", replacement, current["sha256"]),
            cloud.ask_codex_for_proposal("Change the buffer", ["uart.c"]),
        ):
            await _denied(operation, CHECKS[11])
        await _denied(desktop.run_tests("python -c arbitrary"), CHECKS[12])

        bounded = RepositoryService(
            root,
            allow_tests=True,
            timeout_seconds=1,
            test_suites={
                "deadline": [sys.executable, "-I", "-c", "import time; time.sleep(60)"]
            },
        )
        timer = Assistant(bounded, store, transport="browser")
        await timer.configure_tools()
        deadline = await timer.run_tests("deadline")
        _require(
            deadline["timed_out"] is True and deadline["returncode"] != 0, CHECKS[13]
        )
        _require(
            repository.snapshot("uart.c")["content"] == concurrent,
            "policy_checks_leave_source_unchanged",
        )
        evidence = {
            "test_exit_before": before["returncode"],
            "test_exit_after": after["returncode"],
            "cli_show_exit": shown.returncode,
            "cli_unapproved_apply_exit": denied.returncode,
            "cli_approved_apply_exit": applied.returncode,
            "cli_stale_apply_exit": rejected.returncode,
            "phone_locked_tool_count": 0,
            "phone_authenticated_tools": sorted(READ_TOOLS),
            "cloud_demo_tools": sorted(READ_TOOLS),
            "timeout_enforced": deadline["timed_out"],
        }
    _require(not work.exists(), CHECKS[14])
    return {
        "status": "passed",
        "scope": "offline_synthetic_fixture",
        "checks": list(CHECKS),
        "evidence": evidence,
        "provider_calls": 0,
        "existing_workspaces_modified": False,
        "limits": "No model selection, human audio, SIP, or production claim.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="Print deterministic sanitized evidence"
    )
    arguments = parser.parse_args()
    try:
        result = asyncio.run(run_acceptance())
    except AcceptanceError as exc:
        print(json.dumps({"status": "failed", "check": str(exc)}))
        return 1
    except (OSError, ValueError, subprocess.SubprocessError, ToolError):
        print(json.dumps({"status": "failed", "check": "local_execution_error"}))
        return 1
    if arguments.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("Offline acceptance passed: disposable synthetic checkout.")
        for check in result["checks"]:
            print("PASS", check)
        print("No provider calls; existing source and proposals were not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
