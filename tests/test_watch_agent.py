"""Cloud log streaming and private append-file handling without network calls."""

import stat
import sys
from types import SimpleNamespace

import pytest

import tools.watch_agent as watch_agent
from tools.watch_agent import follow_logs, open_private_log, select_agent, stream_logs


def test_stream_logs_prints_and_saves_each_output_chunk(tmp_path, capsys):
    destination = tmp_path / "private-agent.log"
    destination.write_text("earlier event\n")
    command = [
        sys.executable,
        "-u",
        "-c",
        "import sys; print('first event', flush=True); "
        "print('second event', file=sys.stderr, flush=True)",
    ]

    assert stream_logs(command, destination) == 0

    assert "first event" in capsys.readouterr().out
    assert destination.read_text().splitlines() == [
        "earlier event",
        "first event",
        "second event",
    ]
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_stream_logs_recognizes_cloud_inactivity_without_saving_the_error(
    tmp_path, capsys
):
    destination = tmp_path / "private-agent.log"
    message = (
        "failed to get logs: The agent has shut down due to inactivity. "
        "It will automatically start again when a new session begins."
    )
    command = [
        sys.executable,
        "-u",
        "-c",
        f"import sys; print('Using project [demo]'); print({message!r}); sys.exit(1)",
    ]

    assert stream_logs(command, destination) is None

    output = capsys.readouterr().out
    assert "Using project" not in output
    assert "shut down due to inactivity" not in output
    assert destination.read_bytes() == b""


def test_follow_logs_waits_and_retries_after_inactivity(monkeypatch, capsys):
    results = iter((None, 0))
    sleeps = []
    monkeypatch.setattr(
        watch_agent, "stream_logs", lambda _command, _path: next(results)
    )
    monkeypatch.setattr(watch_agent.time, "sleep", sleeps.append)

    assert follow_logs(["lk", "agent", "logs"], retry_seconds=3) == 0

    assert sleeps == [3]
    assert "Agent is sleeping; waiting for a call/session to wake it" in (
        capsys.readouterr().out
    )


def test_private_log_refuses_to_follow_a_symlink(tmp_path):
    target = tmp_path / "target.log"
    target.write_text("keep me")
    link = tmp_path / "agent.log"
    link.symlink_to(target)

    with pytest.raises(OSError):
        open_private_log(link)

    assert target.read_text() == "keep me"


def test_agent_selection_lists_project_agents_and_prompts(monkeypatch, capsys):
    response = SimpleNamespace(
        returncode=0,
        stdout=(
            '{"agents":[{"agentId":"CA_one","agentName":"Browser bot"},'
            '{"agentId":"CA_two","agentName":"Phone bot"}]}'
        ),
        stderr="",
    )
    monkeypatch.setattr(watch_agent.subprocess, "run", lambda *a, **k: response)
    choices = iter(("x", "2"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(choices))

    assert select_agent() == "CA_two"
    output = capsys.readouterr().out
    assert '1. "Browser bot" [CA_one]' in output
    assert "Choose one of the listed numbers." in output
