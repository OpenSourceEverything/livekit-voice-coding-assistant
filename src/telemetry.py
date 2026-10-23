"""Numeric-only local latency evidence. No transcripts or audio are collected."""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TRANSPORTS = frozenset({"browser", "sip", "unknown"})
METRICS = {
    "sdk_e2e_latency": "seconds",
    "sdk_llm_ttft": "seconds",
    "sdk_llm_first_sentence_latency": "seconds",
    "sdk_tts_ttfb": "seconds",
    "sdk_playback_latency": "seconds",
    "sdk_transcription_delay": "seconds",
    "sdk_end_of_turn_delay": "seconds",
    "sdk_user_callback_delay": "seconds",
    "component_llm_ttft": "seconds",
    "component_tts_ttfb": "seconds",
    "component_nonstreaming_stt_duration": "seconds",
    "component_interruption_detection_delay": "seconds",
    "state_response_latency_estimate": "seconds",
    "state_overlap_to_agent_stop_estimate": "seconds",
    "client_response_latency_manual": "seconds",
    "client_barge_in_stop_latency_manual": "seconds",
    "assistant_turn_count": "count",
    "assistant_interrupted_turn_count": "count",
    "sdk_interruption_classification_count": "count",
    "sdk_backchannel_classification_count": "count",
    "sdk_false_interruption_event_count": "count",
    "manual_interruption_trial_count": "count",
    "manual_false_interruption_count": "count",
}
_TURN_FIELDS = {
    "assistant": {
        "e2e_latency": "sdk_e2e_latency",
        "llm_node_ttft": "sdk_llm_ttft",
        "llm_node_ttfs": "sdk_llm_first_sentence_latency",
        "tts_node_ttfb": "sdk_tts_ttfb",
        "playback_latency": "sdk_playback_latency",
    },
    "user": {
        "transcription_delay": "sdk_transcription_delay",
        "end_of_turn_delay": "sdk_end_of_turn_delay",
        "on_user_turn_completed_delay": "sdk_user_callback_delay",
    },
}


def _valid_value(value: Any, unit: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return False
    try:
        finite = math.isfinite(value)
    except OverflowError:
        return False
    if not finite or value < 0:
        return False
    return unit != "count" or float(value).is_integer()


class SessionTelemetry:
    """Write whitelisted numbers and subscribe to the public AgentSession API.

    A random session ID replaces room/caller identifiers. `synthetic=True` is
    reserved for test inputs and is excluded from reports unless requested.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        transport: str = "unknown",
        synthetic: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = Path(path)
        self.session_id = uuid.uuid4().hex
        self.synthetic = synthetic
        self.clock = clock
        self._lock = threading.Lock()
        self._attached = False
        self._agent_speaking = False
        self._last_user_stop: float | None = None
        self._overlap_start: float | None = None
        self.set_transport(transport)

    def set_transport(self, transport: str) -> None:
        """Label future samples after checking the actual participant kind."""
        if transport not in TRANSPORTS:
            raise ValueError("transport must be browser, sip, or unknown")
        self.transport = transport

    def record(self, metric: str, value: float | int) -> bool:
        """Write a valid metric, returning False for missing/invalid values.

        Unknown metric names raise ValueError to prevent accidentally recording
        caller-supplied text. A disk failure does not terminate the voice call.
        """
        if metric not in METRICS:
            raise ValueError("metric is not in the numeric telemetry allowlist")
        unit = METRICS[metric]
        if not _valid_value(value, unit):
            return False
        row = {
            "schema_version": 1,
            "session_id": self.session_id,
            "transport": self.transport,
            "source": "synthetic" if self.synthetic else "live",
            "metric": metric,
            "value": value,
            "unit": unit,
            "recorded_at": time.time(),
        }
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(
                    self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
                )
                with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row, allow_nan=False) + "\n")
            return True
        except OSError:
            logger.warning("Latency telemetry write failed; a measurement was lost")
            return False

    def attach(
        self, session: Any, *, legacy_component_metrics: bool = False
    ) -> SessionTelemetry:
        """Install event callbacks once, before session.start()."""
        if self._attached:
            raise RuntimeError("telemetry is already attached to a session")
        session.on("conversation_item_added", self.on_conversation_item_added)
        session.on("user_state_changed", self.on_user_state_changed)
        session.on("agent_state_changed", self.on_agent_state_changed)
        session.on("overlapping_speech", self.on_overlapping_speech)
        session.on("agent_false_interruption", self.on_false_interruption)
        # Compatibility only: current SDK emits a deprecation warning on
        # registering this event. Per-turn ChatMessage.metrics is primary.
        if legacy_component_metrics:
            session.on("metrics_collected", self.on_metrics_collected)
        self._attached = True
        return self

    def on_conversation_item_added(self, event: Any) -> None:
        item = event.item
        role = getattr(item, "role", None)
        if getattr(item, "type", None) != "message" or role not in _TURN_FIELDS:
            return
        metrics = getattr(item, "metrics", {})
        if isinstance(metrics, Mapping):
            for field, metric in _TURN_FIELDS[role].items():
                self.record(metric, metrics.get(field))
        if role == "assistant":
            self.record("assistant_turn_count", 1)
            if getattr(item, "interrupted", False):
                self.record("assistant_interrupted_turn_count", 1)

    def on_metrics_collected(self, event: Any) -> None:
        metric = event.metrics
        metric_type = getattr(metric, "type", None)
        if metric_type == "llm_metrics":
            self.record("component_llm_ttft", getattr(metric, "ttft", None))
        elif metric_type == "tts_metrics":
            self.record("component_tts_ttfb", getattr(metric, "ttfb", None))
        elif metric_type == "stt_metrics" and not getattr(metric, "streamed", True):
            # Streaming STT duration=0 is a sentinel, not zero-latency speech.
            self.record(
                "component_nonstreaming_stt_duration",
                getattr(metric, "duration", None),
            )

    def on_user_state_changed(self, event: Any) -> None:
        now = self.clock()
        if event.new_state == "speaking":
            self._last_user_stop = None
            if self._agent_speaking and self._overlap_start is None:
                self._overlap_start = now
        elif event.old_state == "speaking" and event.new_state == "listening":
            self._last_user_stop = now

    def on_agent_state_changed(self, event: Any) -> None:
        now = self.clock()
        speaking = event.new_state == "speaking"
        if speaking and not self._agent_speaking and self._last_user_stop is not None:
            self.record("state_response_latency_estimate", now - self._last_user_stop)
            self._last_user_stop = None
        if self._agent_speaking and not speaking and self._overlap_start is not None:
            self.record(
                "state_overlap_to_agent_stop_estimate", now - self._overlap_start
            )
            self._overlap_start = None
        self._agent_speaking = speaking

    def on_overlapping_speech(self, event: Any) -> None:
        self.record(
            "component_interruption_detection_delay",
            getattr(event, "detection_delay", None),
        )
        self.record(
            "sdk_interruption_classification_count"
            if event.is_interruption
            else "sdk_backchannel_classification_count",
            1,
        )

    def on_false_interruption(self, event: Any) -> None:
        self.record("sdk_false_interruption_event_count", 1)


def read_records(path: str | Path) -> tuple[list[dict[str, Any]], int]:
    """Read recognized numeric rows and count malformed input lines."""
    rows = []
    invalid = 0
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                invalid += 1
                continue
            if not isinstance(row, dict):
                invalid += 1
                continue
            metric = row.get("metric")
            transport = row.get("transport")
            source = row.get("source")
            if (
                row.get("schema_version") != 1
                or not isinstance(metric, str)
                or metric not in METRICS
                or not isinstance(transport, str)
                or transport not in TRANSPORTS
                or not isinstance(source, str)
                or source not in {"live", "synthetic"}
                or row.get("unit") != METRICS.get(metric)
                or not _valid_value(row.get("value"), METRICS.get(metric, ""))
            ):
                invalid += 1
                continue
            rows.append(row)
    return rows, invalid


def _percentile(values: list[float], fraction: float) -> float:
    index = (len(values) - 1) * fraction
    low = math.floor(index)
    high = math.ceil(index)
    return values[low] + (values[high] - values[low]) * (index - low)


def summarize_records(
    rows: Iterable[dict[str, Any]], *, include_synthetic: bool = False
) -> dict[str, Any]:
    """Keep source/transport groups distinct and explicitly expose missing data."""
    groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    omitted = 0
    for row in rows:
        if row["source"] == "synthetic" and not include_synthetic:
            omitted += 1
            continue
        groups[(row["source"], row["transport"], row["metric"])].append(row["value"])
    summaries = []
    for (source, transport, metric), values in sorted(groups.items()):
        values.sort()
        result = {
            "source": source,
            "transport": transport,
            "metric": metric,
            "unit": METRICS[metric],
            "n": len(values),
        }
        if METRICS[metric] == "count":
            result["total"] = sum(values)
        else:
            result.update(
                min=values[0],
                p50=_percentile(values, 0.5),
                p95=_percentile(values, 0.95),
                max=values[-1],
                warning=(
                    "Fewer than 10 samples; minimum demo gate not met"
                    if len(values) < 10
                    else "Small sample; p95 is descriptive, not a production SLO"
                    if len(values) < 100
                    else None
                ),
            )
        summaries.append(result)
    required = (
        "sdk_e2e_latency",
        "client_response_latency_manual",
        "client_barge_in_stop_latency_manual",
    )
    missing = {
        transport: [
            metric for metric in required if ("live", transport, metric) not in groups
        ]
        for transport in ("browser", "sip")
    }
    live_latency_samples = sum(
        len(values)
        for (source, _transport, metric), values in groups.items()
        if source == "live" and METRICS[metric] == "seconds"
    )
    return {
        "percentile_method": "linear interpolation between sorted samples",
        "groups": summaries,
        "live_latency_samples": live_latency_samples,
        "synthetic_samples_omitted": omitted,
        "missing_live_measurements": missing,
        "limitations": [
            "SDK and component measurements exclude remote speaker acoustic delivery.",
            "State timing is an estimate and may include a natural end of agent speech.",
            "SDK interruption classifications are not manually verified accuracy.",
            "No latency budget or production performance claim is implied by this report.",
        ],
    }
