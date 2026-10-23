"""RTC/SIP diagnostics without real network calls or sensitive logging."""

import json
import stat
from types import SimpleNamespace

import pytest
from livekit import rtc
from livekit.agents.voice.events import (
    AgentStateChangedEvent,
    CloseEvent,
    CloseReason,
    UserInputTranscribedEvent,
)

from call_diagnostics import CallDiagnostics
from tools.report_call import summarize


class Emitter:
    def __init__(self):
        self.callbacks = {}

    def on(self, name, callback):
        self.callbacks[name] = callback


def test_real_sdk_events_and_sip_attributes_are_allowlisted(tmp_path):
    path = tmp_path / "private/calls.jsonl"
    log = CallDiagnostics(path, transport="sip", synthetic=True)
    session, room = Emitter(), Emitter()
    room.connection_state = rtc.ConnectionState.CONN_CONNECTED
    participant = SimpleNamespace(
        kind=rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
        identity="PRIVATE CALLER",
        attributes={
            "sip.callStatus": "active",
            "sip.phoneNumber": "+12025550123",
            "sip.callID": "PRIVATE CALL ID",
        },
        track_publications={},
    )
    log.attach(session, room, participant)
    session.callbacks["user_input_transcribed"](
        UserInputTranscribedEvent(transcript="PRIVATE PIN AND SOURCE", is_final=True)
    )
    session.callbacks["agent_state_changed"](
        AgentStateChangedEvent(old_state="thinking", new_state="speaking")
    )
    room.callbacks["participant_attributes_changed"](
        {"sip.phoneNumber": "PRIVATE"}, participant
    )
    room.callbacks["track_subscription_failed"](
        participant, "PRIVATE TRACK", "PRIVATE ERROR"
    )
    room.callbacks["local_track_published"](
        SimpleNamespace(mime_type="audio/opus", muted=False, sid="PRIVATE"),
        SimpleNamespace(),
    )
    session.callbacks["error"](
        SimpleNamespace(error=SimpleNamespace(recoverable=True, message="PRIVATE KEY"))
    )
    session.callbacks["close"](CloseEvent(reason=CloseReason.PARTICIPANT_DISCONNECTED))
    content = path.read_text()
    assert "PRIVATE" not in content and "+1202" not in content
    rows = [json.loads(line) for line in content.splitlines()]
    assert any(row.get("sip_status") == "active" for row in rows)
    assert any(row["event"] == "track_subscription_failed" for row in rows)
    assert any(row["event"] == "local_track_published" for row in rows)
    assert rows[-1]["reason"] == "participant_disconnected"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_unknown_fields_and_bad_enum_values_cannot_become_logs(tmp_path):
    path = tmp_path / "calls.jsonl"
    log = CallDiagnostics(path)
    log.record(
        "sip_status",
        sip_status="PRIVATE PHONE",
        transcript="PRIVATE",
        pin="PRIVATE",
        state=["PRIVATE"],
        delay=10**1000,
        code=True,
    )
    assert "PRIVATE" not in path.read_text()
    row = json.loads(path.read_text())
    assert not any(
        key in row
        for key in ("delay", "code", "pin", "transcript", "sip_status", "state")
    )
    with pytest.raises(ValueError):
        log.record("PRIVATE EVENT")
    with pytest.raises(ValueError):
        CallDiagnostics(path, session_id="PRIVATE CALL ID")


def test_log_symlink_refused_and_full_file_does_not_grow(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.write_text("untouched")
    alias = tmp_path / "calls.jsonl"
    alias.symlink_to(target)
    assert not CallDiagnostics(alias).record("session_start")
    assert target.read_text() == "untouched"
    monkeypatch.setattr("call_diagnostics.MAX_BYTES", 1)
    assert not CallDiagnostics(target).record("session_start")
    assert target.read_text() == "untouched"


def test_report_filters_synthetic_and_keeps_interleaved_session_counts(tmp_path):
    path = tmp_path / "calls.jsonl"
    first = CallDiagnostics(path)
    other = CallDiagnostics(path)
    synthetic = CallDiagnostics(path, synthetic=True)
    first.record("session_start", mode="vad")
    other.record("session_start", mode="adaptive")
    first.record("explicit_stop_requested")
    synthetic.record("session_close")
    report = summarize(path)
    assert report["latest_session"] == first.session_id
    assert report["records"] == 2
    assert report["interruption_mode"] == "vad"
    assert not report["caller_heard_stop_verified"]
    assert summarize(path, True)["latest_session"] == synthetic.session_id


def test_report_cannot_echo_injected_sensitive_fields(tmp_path):
    path = tmp_path / "calls.jsonl"
    log = CallDiagnostics(path)
    log.record("session_start", transcript="PRIVATE")
    assert "PRIVATE" not in json.dumps(summarize(path))
    path.write_text(
        json.dumps({"session_id": "a" * 32, "source": "live", "event": "secret_text"})
        + "\n"
    )
    with pytest.raises(ValueError):
        summarize(path)
