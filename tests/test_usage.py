"""Offline checks for private numeric SDK snapshots and estimate limits.

Fixtures mimic SDK event objects; they never query a model or billing account.
Tests distinguish cumulative snapshots from deltas and assert that unknown
account-level pricing never appears to be zero.
"""

import hashlib
import json
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from usage import (
    MAX_LINE_BYTES,
    SessionUsageMonitor,
    format_usage,
    read_usage,
    summarize_usage,
)


def model(kind="llm_usage", **overrides):
    values = {
        "type": kind,
        "provider": "livekit",
        "model": "google/gemma-4-31b-it",
        "input_tokens": 1000,
        "input_cached_tokens": 0,
        "output_tokens": 100,
        "characters_count": 1000,
        "audio_duration": 1.5,
        "total_requests": 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def event(*models):
    return SimpleNamespace(usage=SimpleNamespace(model_usage=list(models)))


def monitor(path, **kwargs):
    return SessionUsageMonitor(path, print_updates=False, **kwargs)


def test_cumulative_updates_replace_not_add(tmp_path):
    """Count the latest cumulative value per model, not every callback."""
    path = tmp_path / "usage.jsonl"
    first = monitor(path, transport="browser")
    first.on_session_usage_updated(event(model(input_tokens=1000)))
    first.on_session_usage_updated(event(model(input_tokens=2000)))
    first.on_session_usage_updated(event(model(input_tokens=2000)))
    second = monitor(path, transport="browser")
    second.on_session_usage_updated(event(model(input_tokens=3000)))
    summary = read_usage(path)
    assert summary["groups"][0]["sessions"] == 2
    assert summary["groups"][0]["totals"]["llm_input_tokens"] == 5000
    assert len(path.read_text().splitlines()) == 3
    assert summary["remaining_monthly_credit_usd"] is None
    assert summary["total_billed_cost_usd"] is None


def test_latest_sequence_not_largest_counter_wins(tmp_path):
    """A corrected lower SDK snapshot replaces an earlier higher snapshot."""
    path = tmp_path / "usage.jsonl"
    usage = monitor(path)
    usage.on_session_usage_updated(event(model(input_tokens=2000)))
    usage.on_session_usage_updated(event(model(input_tokens=1000)))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    with path.open("a") as stream:
        stream.write(json.dumps(rows[0]) + "\n")
    assert read_usage(path)["groups"][0]["totals"]["llm_input_tokens"] == 1000


def test_models_aggregate_and_cache_discount_is_not_double_counted(tmp_path):
    """Check model counters and keep unpriced STT outside known cost."""
    path = tmp_path / "usage.jsonl"
    usage = monitor(path)
    usage.on_session_usage_updated(
        event(
            model(
                input_tokens=1_000_000,
                input_cached_tokens=250_000,
                output_tokens=100_000,
            ),
            model(
                "tts_usage",
                model="fishaudio/s2.1-pro",
                characters_count=10_000,
                audio_duration=10,
            ),
            model(
                "stt_usage", model="assemblyai/universal-3-6-pro", audio_duration=600
            ),
            model("eot_usage", model="turn-detector-v1", total_requests=7),
            model("interruption_usage", model="adaptive", total_requests=13),
        )
    )
    report = read_usage(path)
    group = report["groups"][0]
    assert group["known_llm_tts_estimate_usd"] == pytest.approx(0.62)
    assert group["totals"] == {
        "llm_input_tokens": 1_000_000,
        "llm_cached_input_tokens": 250_000,
        "llm_output_tokens": 100_000,
        "tts_characters": 10_000,
        "tts_audio_seconds": 10,
        "stt_processed_audio_seconds": 600,
        "eot_requests": 7,
        "interruption_requests": 13,
    }
    assert report["stt_cost_usd"] is None
    assert report["total_billed_cost_usd"] is None
    assert (
        read_usage(path, estimate_costs=False)["groups"][0][
            "known_llm_tts_estimate_usd"
        ]
        is None
    )


def test_synthetic_and_transport_groups_never_mix(tmp_path):
    """Exclude test sessions by default and keep SIP/browser totals distinct."""
    path = tmp_path / "usage.jsonl"
    monitor(path, transport="browser").on_session_usage_updated(event(model()))
    monitor(path, transport="sip").on_session_usage_updated(event(model()))
    monitor(path, transport="browser", synthetic=True).on_session_usage_updated(
        event(model(input_tokens=9000))
    )
    summary = read_usage(path)
    assert len(summary["groups"]) == 2
    assert all(group["source"] == "live" for group in summary["groups"])
    assert summary["synthetic_rows_omitted"] == 1
    all_groups = read_usage(path, include_synthetic=True)["groups"]
    assert len(all_groups) == 3
    synthetic = next(group for group in all_groups if group["source"] == "synthetic")
    assert synthetic["totals"]["llm_input_tokens"] == 9000


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_tokens", True),
        ("input_tokens", 1.5),
        ("input_tokens", -1),
        ("input_tokens", float("inf")),
        ("input_cached_tokens", 1001),
        ("output_tokens", float("nan")),
        ("output_tokens", "secret"),
        ("input_tokens", 10**400),
    ],
)
def test_invalid_counter_snapshots_are_ignored(tmp_path, field, value):
    """Reject malformed, nonfinite, negative, boolean, and oversized counters."""
    path = tmp_path / "usage.jsonl"
    usage = monitor(path)
    usage.on_session_usage_updated(event(model(**{field: value})))
    assert not path.exists()
    assert usage.summary()["groups"] == []


def test_invalid_speech_duration_is_not_logged(tmp_path):
    """Do not serialize an invalid duration as a valid zero-usage value."""
    path = tmp_path / "usage.jsonl"
    monitor(path).on_session_usage_updated(
        event(model("stt_usage", audio_duration=float("nan")))
    )
    assert not path.exists()


def test_private_fields_unknown_names_and_secrets_never_appear_in_logs(tmp_path):
    """Persist only allowlisted numeric data and safe labels in a private file."""
    path = tmp_path / "usage.jsonl"
    output = []
    usage = SessionUsageMonitor(path, output=output.append, print_interval_seconds=0)
    secret = "DO NOT PERSIST caller transcript / private source / secret-token"
    snapshot = model(model=secret, provider=secret, transcript=secret, api_key=secret)
    usage.on_session_usage_updated(event(snapshot))
    written = path.read_text()
    assert secret not in written + "\n".join(output)
    assert "api_key" not in written
    expected_label = "unknown_" + hashlib.sha256(secret.encode()).hexdigest()[:16]
    assert expected_label in written
    assert read_usage(path)["groups"][0]["known_llm_tts_estimate_usd"] is None
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_unknown_model_identities_remain_separate(tmp_path):
    path = tmp_path / "usage.jsonl"
    usage = monitor(path)
    usage.on_session_usage_updated(
        event(model(model="unknown-one"), model(model="unknown-two"))
    )
    summary = read_usage(path)
    assert len(summary["groups"][0]["models"]) == 2
    assert summary["groups"][0]["totals"]["llm_input_tokens"] == 2000


def test_existing_ledger_permissions_are_restricted(tmp_path):
    path = tmp_path / "usage.jsonl"
    path.touch(mode=0o644)
    path.chmod(0o644)
    monitor(path).on_session_usage_updated(event(model()))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_symlink_writer_reports_pending_then_retries(tmp_path):
    target = tmp_path / "original.txt"
    target.write_text("unchanged")
    path = tmp_path / "usage.jsonl"
    path.symlink_to(target)
    usage = monitor(path)
    usage.on_session_usage_updated(event(model()))
    assert target.read_text() == "unchanged"
    assert usage.summary()["ledger_write_failures"] == 1
    assert usage.summary()["unpersisted_model_snapshots"] == 1
    path.unlink()
    usage.on_session_usage_updated(event(model()))
    assert usage.summary()["unpersisted_model_snapshots"] == 0
    assert read_usage(path)["groups"][0]["totals"]["llm_input_tokens"] == 1000


def test_reader_skips_bad_and_oversized_lines_in_bounded_chunks(tmp_path):
    path = tmp_path / "usage.jsonl"
    monitor(path).on_session_usage_updated(event(model()))
    with path.open("ab") as stream:
        stream.write(b"not-json\n" + b"x" * (MAX_LINE_BYTES * 3) + b"\n")
        stream.write(b'{"caller":"not numeric"}\n')
    report = read_usage(path)
    assert report["invalid_rows"] == 3
    assert report["groups"][0]["totals"]["llm_input_tokens"] == 1000
    assert not report["reader_limit_reached"]
    assert read_usage(path, max_bytes=10)["reader_limit_reached"]


def test_reader_handles_deep_malformed_json_and_bounds_unique_index(
    tmp_path, monkeypatch
):
    import usage as usage_module

    path = tmp_path / "usage.jsonl"
    monitor(path).on_session_usage_updated(event(model()))
    monitor(path).on_session_usage_updated(event(model()))
    with path.open("ab") as stream:
        stream.write(b"[" * 1100 + b"]" * 1100 + b"\n")
    assert read_usage(path)["invalid_rows"] == 1
    monkeypatch.setattr(usage_module, "MAX_SNAPSHOTS", 1)
    assert read_usage(path)["reader_limit_reached"]


def test_corrupt_cached_counts_and_extra_text_fields_are_rejected(tmp_path):
    path = tmp_path / "usage.jsonl"
    monitor(path).on_session_usage_updated(event(model()))
    row = json.loads(path.read_text())
    row["usage"]["input_cached_tokens"] = 2000
    with path.open("a") as stream:
        stream.write(json.dumps(row) + "\n")
        row["usage"]["input_cached_tokens"] = 0
        row["transcript"] = "not allowed"
        stream.write(json.dumps(row) + "\n")
    assert read_usage(path)["invalid_rows"] == 2


def test_empty_summary_does_not_claim_zero_account_cost():
    """Distinguish no local observations from a verified zero-dollar invoice."""
    report = summarize_usage([])
    assert report["remaining_monthly_credit_usd"] is None
    assert report["total_billed_cost_usd"] is None
    assert "unknown" in format_usage(report)
    assert "then run make status" in format_usage(report)


def test_installed_sdk_event_registration_and_final_capture(tmp_path):
    from livekit.agents import SessionUsageUpdatedEvent
    from livekit.agents.metrics.usage import AgentSessionUsage, LLMModelUsage

    path = tmp_path / "usage.jsonl"
    sdk_usage = AgentSessionUsage(
        model_usage=[
            LLMModelUsage(
                provider="livekit",
                model="google/gemma-4-31b-it",
                input_tokens=5,
                input_cached_tokens=1,
                output_tokens=2,
            )
        ]
    )
    callbacks = {}
    session = SimpleNamespace(
        on=lambda name, callback: callbacks.__setitem__(name, callback), usage=sdk_usage
    )
    usage = monitor(path)
    assert usage.attach(session) is usage
    callbacks["session_usage_updated"](SessionUsageUpdatedEvent(usage=sdk_usage))
    callbacks["close"](None)
    assert len(path.read_text().splitlines()) == 1
    assert read_usage(path)["groups"][0]["totals"]["llm_input_tokens"] == 5
    with pytest.raises(RuntimeError):
        usage.attach(session)


def test_real_sdk_collector_events_cover_every_configured_component(tmp_path):
    from livekit.agents import SessionUsageUpdatedEvent
    from livekit.agents.metrics.base import (
        EOTInferenceMetrics,
        InterruptionMetrics,
        LLMMetrics,
        Metadata,
        STTMetrics,
        TTSMetrics,
    )
    from livekit.agents.metrics.usage import AgentSessionUsage, ModelUsageCollector

    path = tmp_path / "usage.jsonl"
    monitor_instance = monitor(path, synthetic=True)
    collector = ModelUsageCollector()
    # Exact metadata strings used by the installed inference model properties.
    llm_metadata = Metadata(
        model_provider="livekit", model_name="google/gemma-4-31b-it"
    )
    llm_delta = LLMMetrics(
        label="PRIVATE-do-not-copy",
        request_id="PRIVATE-provider-request",
        timestamp=1,
        duration=0.5,
        ttft=0.1,
        cancelled=False,
        completion_tokens=20,
        prompt_tokens=100,
        prompt_cached_tokens=25,
        total_tokens=120,
        tokens_per_second=40,
        metadata=llm_metadata,
    )
    collector.collect(llm_delta)
    monitor_instance.on_session_usage_updated(
        SessionUsageUpdatedEvent(
            usage=AgentSessionUsage(model_usage=collector.flatten())
        )
    )
    collector.collect(llm_delta)
    collector.collect(
        TTSMetrics(
            label="PRIVATE-tts",
            request_id="PRIVATE-tts-request",
            timestamp=2,
            ttfb=0.1,
            duration=0.5,
            audio_duration=2.5,
            cancelled=False,
            characters_count=200,
            streamed=True,
            metadata=Metadata(
                model_provider="livekit", model_name="fishaudio/s2.1-pro"
            ),
        )
    )
    collector.collect(
        STTMetrics(
            label="PRIVATE-stt",
            request_id="PRIVATE-stt-request",
            timestamp=2,
            duration=0,
            audio_duration=5.5,
            streamed=True,
            metadata=Metadata(
                model_provider="livekit", model_name="assemblyai/universal-3-6-pro"
            ),
        )
    )
    collector.collect(
        EOTInferenceMetrics(
            timestamp=2,
            total_duration=0.1,
            detection_delay=0.1,
            prediction_duration=0.05,
            num_requests=2,
            metadata=Metadata(model_provider="livekit", model_name="turn-detector-v1"),
        )
    )
    collector.collect(
        InterruptionMetrics(
            timestamp=2,
            total_duration=0.1,
            prediction_duration=0.05,
            detection_delay=0.1,
            num_interruptions=1,
            num_backchannels=0,
            num_requests=4,
            metadata=Metadata(
                model_provider="livekit", model_name="adaptive interruption"
            ),
        )
    )
    final_snapshot = SessionUsageUpdatedEvent(
        usage=AgentSessionUsage(model_usage=collector.flatten())
    )
    monitor_instance.on_session_usage_updated(final_snapshot)
    monitor_instance.on_session_usage_updated(final_snapshot)
    report = read_usage(path, include_synthetic=True)
    totals = report["groups"][0]["totals"]
    assert totals["llm_input_tokens"] == 200
    assert totals["llm_cached_input_tokens"] == 50
    assert totals["llm_output_tokens"] == 40
    assert totals["tts_characters"] == 200
    assert totals["tts_audio_seconds"] == 2.5
    assert totals["stt_processed_audio_seconds"] == 5.5
    assert totals["eot_requests"] == 2
    assert totals["interruption_requests"] == 4
    assert report["groups"][0]["known_llm_tts_estimate_usd"] == pytest.approx(0.003118)
    assert report["groups"][0]["models"][0]["model"] == "turn-detector-v1"
    assert any(
        component["model"] == "adaptive" for component in report["groups"][0]["models"]
    )
    assert "PRIVATE" not in path.read_text()
    assert read_usage(path)["groups"] == []  # These are synthetic test events.


def test_mixed_priced_and_unknown_models_warn_about_partial_coverage(tmp_path):
    path = tmp_path / "usage.jsonl"
    monitor(path).on_session_usage_updated(
        event(model(), model(model="some-other-model"))
    )
    rendered = format_usage(read_usage(path))
    assert "Unpriced LLM/TTS models: 1" in rendered
    assert "cost is unknown" in rendered


def test_cli_empty_json_malformed_input_project_link_and_help(tmp_path):
    command = Path(__file__).resolve().parents[1] / "tools" / "report_usage.py"
    path = tmp_path / "usage.jsonl"
    result = subprocess.run(
        [sys.executable, str(command), str(path)], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "Remaining monthly account credit: unknown" in result.stdout
    assert (
        "Invoice, plan allowances, and phone rental: check LiveKit Billing."
        in result.stdout
    )
    assert "then run make status" in result.stdout
    structured = subprocess.run(
        [sys.executable, str(command), str(path), "--json", "--project-id", "p_ABC123"],
        capture_output=True,
        text=True,
    )
    data = json.loads(structured.stdout)
    assert data["remaining_monthly_credit_usd"] is None
    assert (
        data["account_billing_url"]
        == "https://cloud.livekit.io/projects/p_ABC123/billing"
    )
    path.write_text("private malformed input\n")
    bad = subprocess.run(
        [sys.executable, str(command), str(path)], capture_output=True, text=True
    )
    assert bad.returncode == 2
    assert "private malformed" not in bad.stdout + bad.stderr
    help_result = subprocess.run(
        [sys.executable, str(command), "--help"], capture_output=True, text=True
    )
    assert help_result.returncode == 0
    invalid_id = subprocess.run(
        [sys.executable, str(command), "--project-id", "p_../../escape"],
        capture_output=True,
        text=True,
    )
    assert invalid_id.returncode == 2
