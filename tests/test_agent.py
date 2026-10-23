"""Exercise installed LiveKit SDK tool registration without model/network calls."""

import subprocess
import sys
from pathlib import Path

import pytest
from livekit.agents.llm import ToolError
from livekit.agents.llm.tool_context import get_fnc_tool_names

from agent import Assistant, make_assistant
from changes import ChangeStore, sha256
from jobs import JobStore
from repository import RepositoryService
from settings import Settings

SOURCE = "def uart5_dma_transmit(data):\n    return len(data)\n"
READ_TOOLS = {"search_repository", "read_source_file", "inspect_git", "workspace_info"}
LOCAL_TOOLS = {"run_tests", "propose_file_change", "ask_codex_for_proposal"}
JOB_TOOLS = {"queue_change_request", "check_change_job"}


@pytest.mark.asyncio
async def test_phone_background_jobs_require_pin_and_explicit_store(
    components, tmp_path
):
    bot = assistant(
        components, transport="sip", jobs=JobStore(tmp_path / "jobs/db.sqlite3")
    )
    await bot.configure_tools()
    assert get_fnc_tool_names(bot.tools) == []
    with pytest.raises(ToolError):
        await bot.check_change_job(1)
    bot.phone_authenticated = True
    await bot.configure_tools()
    assert set(get_fnc_tool_names(bot.tools)) == READ_TOOLS | JOB_TOOLS
    source = await bot.read_source_file("app.py")
    result = await bot.queue_change_request(
        "Explain it", "app.py", source["sha256"], 1, 2
    )
    assert result["saved"]
    assert (await bot.check_change_job(result["job_id"]))["state"] == "queued"
    assert "run_tests" not in get_fnc_tool_names(bot.tools)


@pytest.mark.asyncio
async def test_cloud_cannot_register_or_invoke_background_jobs(components, tmp_path):
    bot = assistant(
        components,
        transport="browser",
        cloud_demo=True,
        jobs=JobStore(tmp_path / "jobs/db.sqlite3"),
    )
    await bot.configure_tools()
    assert not (set(get_fnc_tool_names(bot.tools)) & JOB_TOOLS)
    with pytest.raises(ToolError):
        await bot.queue_change_request("Change", "app.py", "0" * 64)
    with pytest.raises(ToolError):
        await bot.check_change_job(1)


class StubBridge:
    enabled = True

    def __init__(self) -> None:
        self.calls = []

    def propose(self, request: str, snapshots: list[dict]) -> dict:
        self.calls.append((request, snapshots))
        return {
            "summary": "Synthetic local proposal",
            "edits": [
                {
                    "path": snapshots[0]["path"],
                    "original_sha256": snapshots[0]["sha256"],
                    "new_content": snapshots[0]["content"] + "# proposed change\n",
                }
            ],
        }


@pytest.fixture
def components(tmp_path: Path) -> tuple[RepositoryService, ChangeStore, StubBridge]:
    root = tmp_path / "checkout"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    (root / "app.py").write_text(SOURCE, encoding="utf-8")
    (root / ".env").write_text("SYNTHETIC_SECRET=private\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    repository = RepositoryService(
        root,
        allow_tests=True,
        test_suites={"smoke": [sys.executable, "-c", "print('synthetic pass')"]},
    )
    return repository, ChangeStore(repository, tmp_path / "proposals"), StubBridge()


def assistant(components: tuple, **options) -> Assistant:
    repository, store, bridge = components
    return Assistant(repository, store, allow_proposals=True, bridge=bridge, **options)


@pytest.mark.asyncio
async def test_desktop_registers_installed_sdk_read_and_enabled_local_tools(
    components: tuple,
) -> None:
    agent = assistant(components, transport="browser")
    await agent.configure_tools()
    assert set(get_fnc_tool_names(agent.tools)) == READ_TOOLS | LOCAL_TOOLS


@pytest.mark.asyncio
async def test_phone_has_no_tools_until_authentication_then_read_only(
    components: tuple,
) -> None:
    agent = assistant(components, transport="sip")
    await agent.configure_tools()
    assert get_fnc_tool_names(agent.tools) == []
    with pytest.raises(ToolError):
        await agent.search_repository("uart5")
    agent.phone_authenticated = True
    await agent.configure_tools()
    assert set(get_fnc_tool_names(agent.tools)) == READ_TOOLS
    assert (await agent.search_repository("uart5"))["matches"][0]["path"] == "app.py"


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["unknown", "unrecognized"])
async def test_unknown_transport_exposes_no_tools_or_repository_access(
    components: tuple,
    transport: str,
) -> None:
    agent = assistant(components, transport=transport)
    await agent.configure_tools()
    assert get_fnc_tool_names(agent.tools) == []
    with pytest.raises(ToolError):
        await agent.read_source_file("app.py")


@pytest.mark.asyncio
@pytest.mark.parametrize("authenticated", [False, True])
async def test_phone_policy_blocks_direct_mutation_test_and_codex_calls(
    components: tuple,
    authenticated: bool,
) -> None:
    repository, store, bridge = components
    agent = assistant(components, transport="sip")
    agent.phone_authenticated = authenticated
    await agent.configure_tools()
    operations = [
        agent.run_tests("smoke"),
        agent.propose_file_change("app.py", SOURCE + "# changed\n", sha256(SOURCE)),
        agent.ask_codex_for_proposal("Improve the function", ["app.py"]),
    ]
    for operation in operations:
        with pytest.raises(ToolError):
            await operation
    assert bridge.calls == []
    assert not store.directory.exists()
    assert (repository.root / "app.py").read_text() == SOURCE


@pytest.mark.asyncio
async def test_cloud_demo_is_read_only_even_with_locally_enabled_features(
    components: tuple,
) -> None:
    agent = assistant(components, transport="browser", cloud_demo=True)
    await agent.configure_tools()
    assert set(get_fnc_tool_names(agent.tools)) == READ_TOOLS
    for operation in (
        agent.run_tests("smoke"),
        agent.propose_file_change("app.py", SOURCE + "# changed\n", sha256(SOURCE)),
        agent.ask_codex_for_proposal("Improve the function", ["app.py"]),
    ):
        with pytest.raises(ToolError):
            await operation


@pytest.mark.asyncio
async def test_sdk_tool_calls_return_real_bounded_source_and_hash(
    components: tuple,
) -> None:
    agent = assistant(components, transport="browser")
    await agent.configure_tools()
    matches = await agent.search_repository("uart5_dma_transmit")
    assert matches["matches"] == [
        {
            "path": "app.py",
            "line": 1,
            "text": "def uart5_dma_transmit(data):",
        }
    ]
    source = await agent.read_source_file("app.py", start_line=2, lines=1)
    assert source["content"] == "    return len(data)\n"
    assert source["sha256"] == sha256(SOURCE)
    assert "SYNTHETIC_SECRET" not in str(await agent.workspace_info())
    with pytest.raises(ToolError):
        await agent.read_source_file(".env")


@pytest.mark.asyncio
async def test_local_staging_and_codex_stub_create_proposals_without_source_edits(
    components: tuple,
) -> None:
    repository, store, bridge = components
    agent = assistant(components, transport="browser")
    await agent.configure_tools()
    proposal = await agent.propose_file_change(
        "app.py", SOURCE + "# local proposal\n", sha256(SOURCE)
    )
    assert proposal["status"] == "pending_local_review"
    assert (repository.root / "app.py").read_text() == SOURCE
    delegated = await agent.ask_codex_for_proposal("Add a comment", ["app.py"])
    assert delegated["source_unchanged"] is True
    assert delegated["proposals"][0]["status"] == "pending_local_review"
    assert len(bridge.calls) == 1
    assert bridge.calls[0][1] == [repository.snapshot("app.py")]
    assert (repository.root / "app.py").read_text() == SOURCE
    assert len(list(store.directory.glob("*.json"))) == 2


@pytest.mark.asyncio
async def test_sensitive_codex_inputs_fail_before_bridge_is_called(
    components: tuple,
) -> None:
    agent = assistant(components, transport="browser")
    with pytest.raises(ToolError):
        await agent.ask_codex_for_proposal("Read this file", [".env"])
    assert components[2].calls == []


@pytest.mark.asyncio
async def test_local_test_alias_executes_and_arbitrary_alias_is_denied(
    components: tuple,
) -> None:
    agent = assistant(components, transport="browser")
    await agent.configure_tools()
    result = await agent.run_tests("smoke")
    assert result["returncode"] == 0
    assert result["output"] == "synthetic pass\n"
    with pytest.raises(ToolError):
        await agent.run_tests("python -c arbitrary")


@pytest.mark.parametrize(
    ("value", "enabled"),
    [(None, False), ("false", False), ("true", True), ("TRUE", True), ("yes", False)],
)
def test_phone_proposal_setting_is_opt_in(monkeypatch, value, enabled) -> None:
    if value is None:
        monkeypatch.delenv("ENABLE_PHONE_PROPOSALS", raising=False)
    else:
        monkeypatch.setenv("ENABLE_PHONE_PROPOSALS", value)
    assert Settings.from_env().allow_phone_proposals is enabled


@pytest.mark.asyncio
async def test_factory_passes_phone_proposal_setting_without_authenticating(
    components: tuple, monkeypatch
) -> None:
    repository, store, _bridge = components
    monkeypatch.setenv("REPO_ROOT", str(repository.root))
    monkeypatch.setenv("PROPOSALS_PATH", str(store.directory))
    monkeypatch.setenv("ENABLE_PROPOSALS", "true")
    monkeypatch.setenv("ENABLE_PHONE_PROPOSALS", "true")
    monkeypatch.setenv("COPILOT_MODE", "local")
    agent = make_assistant(Settings.from_env(), "sip")
    assert agent.allow_phone_proposals is True
    assert agent.phone_authenticated is False
    await agent.configure_tools()
    assert get_fnc_tool_names(agent.tools) == []
    agent.phone_authenticated = True
    await agent.configure_tools()
    assert set(get_fnc_tool_names(agent.tools)) == READ_TOOLS | {"propose_file_change"}


@pytest.mark.asyncio
async def test_phone_opt_in_stages_only_until_separate_local_apply(
    components: tuple,
) -> None:
    repository, store, bridge = components
    agent = assistant(components, transport="sip", allow_phone_proposals=True)
    agent.phone_authenticated = True
    await agent.configure_tools()
    assert set(get_fnc_tool_names(agent.tools)) == READ_TOOLS | {"propose_file_change"}
    assert (await agent.workspace_info())["proposals_enabled"] is True
    current = await agent.read_source_file("app.py")
    replacement = SOURCE + "# supervised phone proposal\n"
    result = await agent.propose_file_change("app.py", replacement, current["sha256"])
    assert result["status"] == "pending_local_review"
    assert (repository.root / "app.py").read_text() == SOURCE
    assert store.read(result["id"])["new_content"] == replacement
    for operation in (
        agent.run_tests("smoke"),
        agent.ask_codex_for_proposal("Apply the edit", ["app.py"]),
    ):
        with pytest.raises(ToolError):
            await operation
    assert bridge.calls == []
    assert "apply" not in get_fnc_tool_names(agent.tools)
    # The separate operator path applies the exact reviewed artifact. The phone
    # only observes the resulting source afterward; it cannot approve or apply.
    store.apply(result["id"], local_approval=True)
    assert (await agent.read_source_file("app.py"))["content"] == replacement
    assert "+# supervised phone proposal" in (await agent.inspect_git("diff"))["output"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("authenticated", "cloud_demo", "allow_proposals"),
    [(False, False, True), (True, True, True), (True, False, False)],
)
async def test_phone_opt_in_cannot_bypass_authentication_cloud_or_global_disable(
    components: tuple,
    authenticated: bool,
    cloud_demo: bool,
    allow_proposals: bool,
) -> None:
    repository, store, bridge = components
    agent = Assistant(
        repository,
        store,
        transport="sip",
        cloud_demo=cloud_demo,
        allow_proposals=allow_proposals,
        allow_phone_proposals=True,
        bridge=bridge,
    )
    agent.phone_authenticated = authenticated
    await agent.configure_tools()
    assert set(get_fnc_tool_names(agent.tools)) == (
        READ_TOOLS if authenticated else set()
    )
    for operation in (
        agent.propose_file_change("app.py", SOURCE + "# denied\n", sha256(SOURCE)),
        agent.run_tests("smoke"),
        agent.ask_codex_for_proposal("Apply the edit", ["app.py"]),
    ):
        with pytest.raises(ToolError):
            await operation
    assert not store.directory.exists()
    assert (repository.root / "app.py").read_text() == SOURCE
    assert bridge.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("phone_opt_in", [False, True])
async def test_phone_proposal_flag_does_not_change_desktop_capabilities(
    components: tuple, phone_opt_in: bool
) -> None:
    agent = assistant(
        components, transport="browser", allow_phone_proposals=phone_opt_in
    )
    await agent.configure_tools()
    assert set(get_fnc_tool_names(agent.tools)) == READ_TOOLS | LOCAL_TOOLS


@pytest.mark.asyncio
async def test_phone_proposal_opt_in_still_checks_sensitive_paths_and_source_hash(
    components: tuple,
) -> None:
    repository, store, _bridge = components
    agent = assistant(components, transport="sip", allow_phone_proposals=True)
    agent.phone_authenticated = True
    with pytest.raises(ToolError):
        await agent.propose_file_change(".env", "fake secret\n", sha256("fake"))
    current = await agent.read_source_file("app.py")
    (repository.root / "app.py").write_text(SOURCE + "# intervening edit\n")
    with pytest.raises(ToolError):
        await agent.propose_file_change(
            "app.py", SOURCE + "# stale\n", current["sha256"]
        )
    assert not store.directory.exists()
    assert (repository.root / "app.py").read_text() == SOURCE + "# intervening edit\n"
