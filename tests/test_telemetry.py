"""Synthetic timings only; these tests establish no real voice performance."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from telemetry import SessionTelemetry, read_records, summarize_records


def event(**kwargs):
    return SimpleNamespace(**kwargs)


def test_sdk_fields_recorded_without_sensitive_content(tmp_path):
    path = tmp_path / "metrics.jsonl"
    telemetry = SessionTelemetry(path, transport="browser", synthetic=True)
    telemetry.on_conversation_item_added(
        event(
            item=event(
                type="message",
                role="assistant",
                interrupted=True,
                content=["DO NOT SAVE: patient secret"],
                id="DO NOT SAVE: phone number",
                metrics={
                    "e2e_latency": 0.9,
                    "llm_node_ttft": 0.2,
                    "tts_node_ttfb": 0.1,
                    "provider_request_ids": ["DO NOT SAVE: provider ID"],
                },
            )
        )
    )
    rows, invalid = read_records(path)
    assert invalid == 0
    assert {row["metric"] for row in rows} == {
        "sdk_e2e_latency",
        "sdk_llm_ttft",
        "sdk_tts_ttfb",
        "assistant_turn_count",
        "assistant_interrupted_turn_count",
    }
    assert "DO NOT SAVE" not in path.read_text()
    assert all(row["source"] == "synthetic" for row in rows)
    assert all(row["transport"] == "browser" for row in rows)


def test_missing_invalid_and_sentinel_values_do_not_become_zero(tmp_path):
    path = tmp_path / "metrics.jsonl"
    telemetry = SessionTelemetry(path, synthetic=True)
    for value in (None, True, float("nan"), float("inf"), -1):
        assert not telemetry.record("sdk_e2e_latency", value)
    assert not telemetry.record("assistant_turn_count", 0.5)
    telemetry.on_metrics_collected(
        event(metrics=event(type="stt_metrics", streamed=True, duration=0))
    )
    assert not path.exists()
    assert telemetry.record("sdk_e2e_latency", 0)
    assert read_records(path)[0][0]["value"] == 0
    with pytest.raises(ValueError):
        telemetry.record("patient phone number", 1)


def test_state_estimates_are_separate_from_client_audio_evidence(tmp_path):
    now = iter([1.0, 2.0, 2.2, 2.35])
    path = tmp_path / "metrics.jsonl"
    telemetry = SessionTelemetry(path, synthetic=True, clock=lambda: next(now))
    telemetry.on_user_state_changed(event(old_state="speaking", new_state="listening"))
    telemetry.on_agent_state_changed(event(old_state="thinking", new_state="speaking"))
    telemetry.on_user_state_changed(event(old_state="listening", new_state="speaking"))
    telemetry.on_agent_state_changed(event(old_state="speaking", new_state="listening"))
    rows, _ = read_records(path)
    assert rows[0]["metric"] == "state_response_latency_estimate"
    assert rows[0]["value"] == pytest.approx(1)
    assert rows[1]["metric"] == "state_overlap_to_agent_stop_estimate"
    assert rows[1]["value"] == pytest.approx(0.15)
    assert not any("manual" in row["metric"] for row in rows)


def test_transport_changes_do_not_relabel_existing_samples(tmp_path):
    path = tmp_path / "metrics.jsonl"
    telemetry = SessionTelemetry(path, synthetic=True)
    telemetry.record("sdk_e2e_latency", 1)
    telemetry.set_transport("sip")
    telemetry.record("sdk_e2e_latency", 2)
    assert [row["transport"] for row in read_records(path)[0]] == ["unknown", "sip"]
    with pytest.raises(ValueError):
        telemetry.set_transport("caller name")


def test_summary_labels_synthetic_and_reports_missing_live_data(tmp_path):
    path = tmp_path / "metrics.jsonl"
    telemetry = SessionTelemetry(path, transport="sip", synthetic=True)
    for number in range(1, 11):
        telemetry.record("sdk_e2e_latency", number / 10)
    rows, _ = read_records(path)
    excluded = summarize_records(rows)
    assert excluded["groups"] == []
    assert excluded["live_latency_samples"] == 0
    assert excluded["synthetic_samples_omitted"] == 10
    summary = summarize_records(rows, include_synthetic=True)
    group = summary["groups"][0]
    assert group["source"] == "synthetic"
    assert group["transport"] == "sip"
    assert group["n"] == 10
    assert group["p50"] == pytest.approx(0.55)
    assert group["p95"] == pytest.approx(0.955)
    assert group["max"] == 1
    assert "production SLO" in group["warning"]
    assert "sdk_e2e_latency" in summary["missing_live_measurements"]["sip"]


def test_reader_counts_invalid_rows(tmp_path):
    path = tmp_path / "metrics.jsonl"
    bad_row = {
        "schema_version": 1,
        "metric": "sdk_e2e_latency",
        "transport": [],
        "source": "synthetic",
        "value": 1,
        "unit": "seconds",
    }
    path.write_text(
        "bad secret text\n"
        + json.dumps(["not a record"])
        + "\n"
        + json.dumps(bad_row)
        + "\n"
    )
    rows, invalid = read_records(path)
    assert rows == []
    assert invalid == 3


def test_cli_empty_file_help_and_required_live_exit_codes(tmp_path):
    command = Path(__file__).resolve().parents[1] / "tools" / "report_latency.py"
    path = tmp_path / "empty.jsonl"
    path.touch()
    result = subprocess.run(
        [sys.executable, str(command), str(path)], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "No live latency measurements" in result.stdout
    required = subprocess.run(
        [sys.executable, str(command), str(path), "--require-live"],
        capture_output=True,
        text=True,
    )
    assert required.returncode == 1
    help_result = subprocess.run(
        [sys.executable, str(command), "--help"], capture_output=True, text=True
    )
    assert help_result.returncode == 0


def test_attach_registers_public_events_once(tmp_path):
    callbacks = {}
    session = event(on=lambda name, callback: callbacks.__setitem__(name, callback))
    telemetry = SessionTelemetry(tmp_path / "metrics.jsonl", synthetic=True)
    assert telemetry.attach(session) is telemetry
    assert "conversation_item_added" in callbacks
    assert "agent_state_changed" in callbacks
    assert "metrics_collected" not in callbacks
    with pytest.raises(RuntimeError):
        telemetry.attach(session)
    compatibility = SessionTelemetry(tmp_path / "legacy.jsonl", synthetic=True)
    compatibility.attach(session, legacy_component_metrics=True)
    assert "metrics_collected" in callbacks
