"""Private numeric session usage, not an account balance or an invoice.

SDK session_usage_updated events contain cumulative per-model snapshots.
Replace those snapshots; never add successive updates from the same session.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import stat
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PRICING_URL = "https://livekit.com/pricing/inference"
PRICING_DATE = "2026-10-22"
TRANSPORTS = {"browser", "sip", "unknown"}
SOURCES = {"live", "synthetic"}
MAX_LINE_BYTES = 4096
MAX_LEDGER_BYTES = 64 * 1024 * 1024
MAX_SNAPSHOTS = 50_000
MAX_MODELS_PER_SESSION = 64
FIELDS = {
    "llm_usage": ("input_tokens", "input_cached_tokens", "output_tokens"),
    "tts_usage": ("characters_count", "audio_duration"),
    "stt_usage": ("audio_duration",),
    "eot_usage": ("total_requests",),
    "interruption_usage": ("total_requests",),
}
_PROVIDERS = {"livekit", "google", "assemblyai", "fishaudio", "unknown"}
_MODELS = {
    "google/gemma-4-31b-it": "google/gemma-4-31b-it",
    "gemma-4-31b-it": "google/gemma-4-31b-it",
    "assemblyai/universal-3-6-pro": "assemblyai/universal-3-6-pro",
    "universal-3-6-pro": "assemblyai/universal-3-6-pro",
    "fishaudio/s2.1-pro": "fishaudio/s2.1-pro",
    "s2.1-pro": "fishaudio/s2.1-pro",
    "turn-detector-v1": "turn-detector-v1",
    "v1": "turn-detector-v1",
    "adaptive": "adaptive",
    "adaptive interruption": "adaptive",
}
_ROW_FIELDS = {
    "schema_version",
    "session_id",
    "transport",
    "source",
    "sequence",
    "recorded_at",
    "kind",
    "provider",
    "model",
    "usage",
}


def _opaque_label(value: Any) -> str:
    """Unknown model/provider names are fingerprints, never arbitrary log text."""
    if not isinstance(value, str):
        return "unknown"
    return (
        "unknown_"
        + hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
    )


def _label_allowed(value: Any, known: set[str]) -> bool:
    return isinstance(value, str) and (
        value in known or re.fullmatch(r"unknown_[a-f0-9]{16}", value) is not None
    )


def _number(value: Any, *, integer: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if integer and type(value) is not int:
        return False
    try:
        return math.isfinite(value) and 0 <= value <= (2**63 - 1 if integer else 1e12)
    except OverflowError:
        return False


def _usage_valid(kind: str, counters: Any) -> bool:
    if not isinstance(counters, dict) or set(counters) != set(FIELDS[kind]):
        return False
    if not all(
        _number(value, integer=field != "audio_duration")
        for field, value in counters.items()
    ):
        return False
    return (
        kind != "llm_usage"
        or counters["input_cached_tokens"] <= counters["input_tokens"]
    )


def _valid_row(row: Any) -> bool:
    if not isinstance(row, dict) or set(row) != _ROW_FIELDS:
        return False
    kind = row.get("kind")
    if not isinstance(kind, str) or kind not in FIELDS:
        return False
    return (
        type(row["schema_version"]) is int
        and row["schema_version"] == 1
        and isinstance(row["session_id"], str)
        and re.fullmatch(r"[a-f0-9]{32}", row["session_id"]) is not None
        and isinstance(row["source"], str)
        and row["source"] in SOURCES
        and isinstance(row["transport"], str)
        and row["transport"] in TRANSPORTS
        and _number(row["sequence"], integer=True)
        and row["sequence"] > 0
        and _number(row["recorded_at"])
        and _label_allowed(row["provider"], _PROVIDERS)
        and _label_allowed(row["model"], {*_MODELS.values(), "unknown"})
        and _usage_valid(kind, row["usage"])
    )


def _key(row: dict) -> tuple:
    return tuple(
        row[name]
        for name in ("source", "transport", "session_id", "kind", "provider", "model")
    )


def summarize_usage(
    rows: Iterable[dict],
    *,
    include_synthetic: bool = False,
    estimate_costs: bool = True,
) -> dict:
    """Keep the latest snapshot per session/model, then aggregate distinct sessions."""
    latest = {}
    invalid = 0
    synthetic_omitted = 0
    for row in rows:
        if not _valid_row(row):
            invalid += 1
            continue
        if row["source"] == "synthetic" and not include_synthetic:
            synthetic_omitted += 1
            continue
        key = _key(row)
        previous = latest.get(key)
        if previous is None or row["sequence"] > previous["sequence"]:
            if previous is None and len(latest) >= MAX_SNAPSHOTS:
                raise ValueError("Usage index limit reached; rotate the ledger")
            latest[key] = row
    grouped = {}
    sessions = {}
    for row in latest.values():
        group_key = (row["source"], row["transport"])
        models = grouped.setdefault(group_key, {})
        sessions.setdefault(group_key, set()).add(row["session_id"])
        model_key = (row["kind"], row["provider"], row["model"])
        if model_key not in models:
            models[model_key] = dict.fromkeys(FIELDS[row["kind"]], 0)
        for name, value in row["usage"].items():
            models[model_key][name] += value
    groups = []
    for (source, transport), models in sorted(grouped.items()):
        totals = {
            "llm_input_tokens": 0,
            "llm_cached_input_tokens": 0,
            "llm_output_tokens": 0,
            "tts_characters": 0,
            "tts_audio_seconds": 0,
            "stt_processed_audio_seconds": 0,
            "eot_requests": 0,
            "interruption_requests": 0,
        }
        known_cost = 0.0
        cost_components = 0
        unknown_price_models = 0
        model_rows = []
        for (kind, provider, model), counters in sorted(models.items()):
            model_rows.append(
                {
                    "kind": kind,
                    "provider": provider,
                    "model": model,
                    "usage": counters,
                }
            )
            if kind == "llm_usage":
                totals["llm_input_tokens"] += counters["input_tokens"]
                totals["llm_cached_input_tokens"] += counters["input_cached_tokens"]
                totals["llm_output_tokens"] += counters["output_tokens"]
                if model == "google/gemma-4-31b-it":
                    known_cost += (
                        (counters["input_tokens"] - counters["input_cached_tokens"])
                        * 0.4
                        + counters["input_cached_tokens"] * 0.2
                        + counters["output_tokens"] * 1.2
                    ) / 1_000_000
                    cost_components += 1
                else:
                    unknown_price_models += 1
            elif kind == "tts_usage":
                totals["tts_characters"] += counters["characters_count"]
                totals["tts_audio_seconds"] += counters["audio_duration"]
                if model == "fishaudio/s2.1-pro":
                    known_cost += counters["characters_count"] * 15 / 1_000_000
                    cost_components += 1
                else:
                    unknown_price_models += 1
            elif kind == "stt_usage":
                totals["stt_processed_audio_seconds"] += counters["audio_duration"]
            elif kind == "eot_usage":
                totals["eot_requests"] += counters["total_requests"]
            elif kind == "interruption_usage":
                totals["interruption_requests"] += counters["total_requests"]
        groups.append(
            {
                "source": source,
                "transport": transport,
                "sessions": len(sessions[(source, transport)]),
                "totals": totals,
                "models": model_rows,
                "known_llm_tts_estimate_usd": round(known_cost, 10)
                if estimate_costs and cost_components
                else None,
                "unpriced_llm_tts_models": unknown_price_models,
            }
        )
    return {
        "scope": "Recorded local session ledger only; not account-wide usage",
        "groups": groups,
        "synthetic_rows_omitted": synthetic_omitted,
        "invalid_rows": invalid,
        "remaining_monthly_credit_usd": None,
        "total_billed_cost_usd": None,
        "stt_cost_usd": None,
        "pricing_date": PRICING_DATE if estimate_costs else None,
        "pricing_source": PRICING_URL if estimate_costs else None,
        "limitations": [
            "Known-cost estimate covers identified Gemma LLM and Fish TTS usage only.",
            "STT processed audio is not billable connection time; STT cost is unknown.",
            "Hosting, media, telephony, observability, credits and other sessions are excluded.",
            "Remaining monthly account credit is unknown; no account balance API is queried.",
        ],
    }


def read_usage(
    path: str | Path,
    *,
    include_synthetic: bool = False,
    estimate_costs: bool = True,
    max_bytes: int = MAX_LEDGER_BYTES,
) -> dict:
    """Read bounded lines and retain only latest numeric snapshots, never full files."""
    latest = {}
    invalid = 0
    omitted = 0
    bytes_read = 0
    truncated = False
    with Path(path).open("rb") as stream:
        while True:
            chunk = stream.readline(MAX_LINE_BYTES + 1)
            if not chunk:
                break
            bytes_read += len(chunk)
            if bytes_read > max_bytes:
                truncated = True
                break
            if len(chunk) > MAX_LINE_BYTES:
                # Discard the rest of a long physical line in bounded chunks.
                while not chunk.endswith(b"\n"):
                    chunk = stream.readline(MAX_LINE_BYTES + 1)
                    bytes_read += len(chunk)
                    if not chunk or bytes_read > max_bytes:
                        break
                invalid += 1
                if bytes_read > max_bytes:
                    truncated = True
                    break
                continue
            if not chunk.strip():
                continue
            try:
                row = json.loads(chunk)
            except (ValueError, UnicodeDecodeError, RecursionError):
                invalid += 1
                continue
            if not _valid_row(row):
                invalid += 1
                continue
            if row["source"] == "synthetic" and not include_synthetic:
                omitted += 1
                continue
            key = _key(row)
            previous = latest.get(key)
            if previous is None and len(latest) >= MAX_SNAPSHOTS:
                truncated = True
                break
            if previous is None or row["sequence"] > previous["sequence"]:
                latest[key] = row
    summary = summarize_usage(
        latest.values(), include_synthetic=True, estimate_costs=estimate_costs
    )
    summary.update(
        invalid_rows=invalid,
        synthetic_rows_omitted=omitted,
        reader_limit_reached=truncated,
    )
    return summary


def format_usage(summary: dict, *, compact: bool = False) -> str:
    lines = ["Coding Buddy usage: recorded sessions only"]
    if not summary["groups"]:
        lines.append(
            "No recorded sessions yet. Run the worker, ask one question, end the session, then run make status."
        )
    for group in summary["groups"]:
        totals = group["totals"]
        lines.append(
            f"{group['source']} / {group['transport']}: {group['sessions']} session(s); "
            f"tokens input={totals['llm_input_tokens']:,} "
            f"cached={totals['llm_cached_input_tokens']:,} "
            f"output={totals['llm_output_tokens']:,}; "
            f"speech={totals['tts_characters']:,} characters; "
            f"STT processed={totals['stt_processed_audio_seconds']:.1f}s"
        )
        estimate = group["known_llm_tts_estimate_usd"]
        lines.append(
            "Known LLM + TTS estimate: "
            + (f"${estimate:.6f}" if estimate is not None else "unknown/disabled")
            + "; partial estimate, STT and other charges excluded."
        )
        if group["unpriced_llm_tts_models"]:
            lines.append(
                f"Unpriced LLM/TTS models: {group['unpriced_llm_tts_models']}; their cost is unknown."
            )
        if not compact:
            lines.append(
                f"TTS audio={totals['tts_audio_seconds']:.1f}s; "
                f"turn checks={totals['eot_requests']:,}; "
                f"interruption checks={totals['interruption_requests']:,}"
            )
            for model in group["models"]:
                counters = ", ".join(
                    f"{name}={value:,}" if type(value) is int else f"{name}={value:g}"
                    for name, value in model["usage"].items()
                )
                lines.append(
                    f"  {model['kind']} {model['provider']} / {model['model']}: {counters}"
                )
    lines.append("Remaining monthly account credit: unknown (not queried).")
    lines.append("Invoice, plan allowances, and phone rental: check LiveKit Billing.")
    if summary.get("synthetic_rows_omitted"):
        lines.append(
            f"Synthetic updates excluded: {summary['synthetic_rows_omitted']:,}"
        )
    if summary.get("invalid_rows"):
        lines.append(f"Invalid rows skipped: {summary['invalid_rows']:,}")
    if summary.get("reader_limit_reached"):
        lines.append("Reader limit reached: report is incomplete; rotate the ledger.")
    if summary.get("unpersisted_model_snapshots"):
        lines.append(
            "Ledger write failed: current usage may be missing from saved reports."
        )
    if summary.get("account_billing_url"):
        lines.append("Account billing: " + summary["account_billing_url"])
    return "\n".join(lines)


class SessionUsageMonitor:
    """Listen to Agents 1.8 cumulative usage updates and print a private summary."""

    def __init__(
        self,
        path: str | Path,
        *,
        transport: str = "unknown",
        synthetic: bool = False,
        print_updates: bool = True,
        print_interval_seconds: float = 60,
        output: Callable[[str], Any] = print,
    ) -> None:
        if not isinstance(transport, str) or transport not in TRANSPORTS:
            raise ValueError("transport must be browser, sip, or unknown")
        if type(synthetic) is not bool or type(print_updates) is not bool:
            raise ValueError("synthetic and print_updates must be booleans")
        if not _number(print_interval_seconds) or print_interval_seconds > 3600:
            raise ValueError("print interval must be zero to 3600 seconds")
        self.path = Path(path)
        self.transport = transport
        self.synthetic = synthetic
        self.session_id = uuid.uuid4().hex
        self.print_updates = print_updates
        self.print_interval_seconds = print_interval_seconds
        self.output = output
        self._sequence = 0
        self._latest: dict[tuple, dict] = {}
        self._lock = threading.Lock()
        self._attached = False
        self._session: Any = None
        self._last_print: float | None = None
        self._pending: set[tuple] = set()
        self._write_failures = 0

    def attach(self, session: Any) -> SessionUsageMonitor:
        if self._attached:
            raise RuntimeError("usage monitor is already attached")
        session.on("session_usage_updated", self.on_session_usage_updated)
        session.on("close", self.on_close)
        self._session = session
        self._attached = True
        return self

    def capture(self, usage: Any) -> int:
        models = getattr(usage, "model_usage", None)
        if not isinstance(models, list) or len(models) > MAX_MODELS_PER_SESSION:
            return 0
        changed = 0
        with self._lock:
            for model in models:
                kind = getattr(model, "type", None)
                if not isinstance(kind, str) or kind not in FIELDS:
                    continue
                counters = {name: getattr(model, name, None) for name in FIELDS[kind]}
                if not _usage_valid(kind, counters):
                    continue
                provider_name = getattr(model, "provider", None)
                model_name = getattr(model, "model", None)
                provider = (
                    provider_name
                    if isinstance(provider_name, str) and provider_name in _PROVIDERS
                    else _opaque_label(provider_name)
                )
                label = _MODELS.get(model_name) if isinstance(model_name, str) else None
                self._sequence += 1
                row = {
                    "schema_version": 1,
                    "session_id": self.session_id,
                    "source": "synthetic" if self.synthetic else "live",
                    "transport": self.transport,
                    "sequence": self._sequence,
                    "recorded_at": time.time(),
                    "kind": kind,
                    "provider": provider,
                    "model": label or _opaque_label(model_name),
                    "usage": counters,
                }
                key = _key(row)
                if (
                    key in self._latest
                    and self._latest[key]["usage"] == counters
                    and key not in self._pending
                ):
                    continue
                if (
                    key not in self._latest
                    and len(self._latest) >= MAX_MODELS_PER_SESSION
                ):
                    continue
                self._latest[key] = row
                if self._append(row):
                    self._pending.discard(key)
                else:
                    self._pending.add(key)
                    self._write_failures += 1
                changed += 1
        return changed

    def _append(self, row: dict) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
            )
            with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise OSError("Usage destination is not a regular file")
                os.fchmod(stream.fileno(), 0o600)
                stream.write(json.dumps(row, allow_nan=False) + "\n")
            return True
        except OSError:
            logger.warning("Usage ledger write failed; this run may be incomplete")
            return False

    def summary(self, *, include_synthetic: bool = False) -> dict:
        with self._lock:
            rows = list(self._latest.values())
        summary = summarize_usage(rows, include_synthetic=include_synthetic)
        summary["ledger_write_failures"] = self._write_failures
        summary["unpersisted_model_snapshots"] = len(self._pending)
        return summary

    def print_summary(self) -> None:
        self.output(format_usage(self.summary(include_synthetic=True), compact=True))
        self._last_print = time.monotonic()

    def on_session_usage_updated(self, event: Any) -> None:
        if not self.capture(event.usage) or not self.print_updates:
            return
        now = time.monotonic()
        if (
            self._last_print is None
            or now - self._last_print >= self.print_interval_seconds
        ):
            self.print_summary()

    def on_close(self, _event: Any) -> None:
        # Capture one final cumulative SDK snapshot; no account request is made.
        if self._session is not None:
            self.capture(self._session.usage)
        if self.print_updates:
            self.print_summary()
