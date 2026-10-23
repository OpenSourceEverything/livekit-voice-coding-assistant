"""Allowlisted local RTC/SIP lifecycle events, not raw signaling or media logs."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import stat
import threading
import time
import uuid
from pathlib import Path

from livekit import rtc

logger = logging.getLogger(__name__)
MAX_BYTES = 8 * 1024 * 1024
EVENTS = frozenset(
    {
        "session_start",
        "session_close",
        "session_error",
        "agent_state",
        "user_state",
        "transcription",
        "transcription_timeout",
        "overlap",
        "false_interruption",
        "assistant_turn",
        "participant_connected",
        "participant_disconnected",
        "sip_status",
        "track_published",
        "track_subscribed",
        "track_unsubscribed",
        "local_track_published",
        "local_track_unpublished",
        "track_muted",
        "track_unmuted",
        "track_subscription_failed",
        "connection_state",
        "connection_quality",
        "reconnecting",
        "reconnected",
        "disconnected",
        "explicit_stop_requested",
        "explicit_stop_completed",
        "explicit_stop_unavailable",
    }
)
ENUMS = {
    "state": {"initializing", "idle", "listening", "thinking", "speaking", "away"},
    "sip_status": {"dialing", "ringing", "automation", "active", "hangup"},
    "mode": {"adaptive", "vad"},
    "kind": {"sip", "standard", "other"},
    "codec": {"audio/opus", "audio/red", "audio/pcmu", "audio/pcma", "unknown"},
    "reason": {
        "error",
        "job_shutdown",
        "participant_disconnected",
        "user_initiated",
        "task_completed",
    },
}
NUMBERS = {"delay", "min_duration", "code", "speech_duration"}
BOOLEANS = {
    "is_final",
    "is_interruption",
    "resumed",
    "interrupted",
    "muted",
    "recoverable",
}


class CallDiagnostics:
    def __init__(
        self,
        path,
        *,
        session_id=None,
        transport="unknown",
        synthetic=False,
        clock=time.monotonic,
    ):
        self.path = Path(path)
        self.session_id = session_id or uuid.uuid4().hex
        if not re.fullmatch(r"[0-9a-f]{32}", self.session_id):
            raise ValueError(
                "Use a random diagnostic session ID, not a caller identifier."
            )
        if transport not in {"browser", "sip", "unknown"}:
            raise ValueError("Invalid diagnostic transport.")
        self.transport = transport
        self.synthetic = bool(synthetic)
        self.clock = clock
        self.started = clock()
        self.lock = threading.Lock()

    def record(self, event, **fields):
        if event not in EVENTS:
            raise ValueError("Unknown diagnostic event.")
        safe = {}
        for key, value in fields.items():
            if (
                (key in ENUMS and isinstance(value, str) and value in ENUMS[key])
                or (key in BOOLEANS and type(value) is bool)
                or (
                    key in NUMBERS
                    and type(value) in {float, int}
                    and 0 <= value <= 1_000_000
                    and math.isfinite(value)
                )
            ):
                safe[key] = value
        row = {
            "schema_version": 1,
            "session_id": self.session_id,
            "transport": self.transport,
            "source": "synthetic" if self.synthetic else "live",
            "event": event,
            "recorded_at": time.time(),
            "elapsed": max(0, self.clock() - self.started),
            **safe,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self.lock:
                descriptor = os.open(
                    self.path,
                    os.O_WRONLY
                    | os.O_APPEND
                    | os.O_CREAT
                    | os.O_NOFOLLOW
                    | os.O_NONBLOCK,
                    0o600,
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size >= MAX_BYTES:
                        return False
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(json.dumps(row, allow_nan=False) + "\n")
            return True
        except OSError:
            logger.warning("Call diagnostic write failed; no event payload was logged")
            return False

    def participant(self, event, participant):
        kind = (
            "sip"
            if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
            else "standard"
            if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD
            else "other"
        )
        self.record(event, kind=kind)
        if kind == "sip":
            self.record(
                "sip_status", sip_status=participant.attributes.get("sip.callStatus")
            )

    def track(self, event, publication):
        self.record(
            event,
            codec=getattr(publication, "mime_type", "unknown"),
            muted=getattr(publication, "muted", None),
        )

    def attach(
        self, session, room=None, participant=None, *, mode="adaptive", min_duration=0.5
    ):
        self.record("session_start", mode=mode, min_duration=min_duration)
        session.on(
            "agent_state_changed",
            lambda ev: self.record("agent_state", state=ev.new_state),
        )
        session.on(
            "user_state_changed",
            lambda ev: self.record("user_state", state=ev.new_state),
        )
        session.on(
            "user_input_transcribed",
            lambda ev: self.record("transcription", is_final=ev.is_final),
        )
        session.on(
            "user_transcription_timeout",
            lambda ev: self.record(
                "transcription_timeout", speech_duration=ev.speech_duration
            ),
        )
        session.on(
            "overlapping_speech",
            lambda ev: self.record(
                "overlap", is_interruption=ev.is_interruption, delay=ev.detection_delay
            ),
        )
        session.on(
            "agent_false_interruption",
            lambda ev: self.record("false_interruption", resumed=ev.resumed),
        )
        session.on("conversation_item_added", self.on_item)
        session.on(
            "error",
            lambda ev: self.record(
                "session_error", recoverable=getattr(ev.error, "recoverable", None)
            ),
        )
        session.on(
            "close",
            lambda ev: self.record(
                "session_close", reason=getattr(ev.reason, "value", ev.reason)
            ),
        )
        if room is not None:
            room.on(
                "participant_connected",
                lambda p: self.participant("participant_connected", p),
            )
            room.on(
                "participant_disconnected",
                lambda p: self.participant("participant_disconnected", p),
            )
            room.on(
                "participant_attributes_changed",
                lambda _attrs, p: self.participant("sip_status", p),
            )
            room.on(
                "track_published", lambda pub, _p: self.track("track_published", pub)
            )
            room.on(
                "track_subscribed",
                lambda _track, pub, _p: self.track("track_subscribed", pub),
            )
            room.on(
                "track_unsubscribed",
                lambda _track, pub, _p: self.track("track_unsubscribed", pub),
            )
            room.on(
                "local_track_published",
                lambda pub, _track: self.track("local_track_published", pub),
            )
            room.on(
                "local_track_unpublished",
                lambda pub: self.track("local_track_unpublished", pub),
            )
            room.on("track_muted", lambda pub, _p: self.track("track_muted", pub))
            room.on("track_unmuted", lambda pub, _p: self.track("track_unmuted", pub))
            room.on(
                "track_subscription_failed",
                lambda _p, _sid, _err: self.record("track_subscription_failed"),
            )
            room.on(
                "connection_state_changed",
                lambda state: self.record("connection_state", code=int(state)),
            )
            room.on(
                "connection_quality_changed",
                lambda _p, quality: self.record(
                    "connection_quality", code=int(quality)
                ),
            )
            room.on(
                "disconnected",
                lambda reason: self.record("disconnected", code=int(reason)),
            )
            room.on("reconnecting", lambda: self.record("reconnecting"))
            room.on("reconnected", lambda: self.record("reconnected"))
            self.record("connection_state", code=int(room.connection_state))
        if participant is not None:
            self.participant("participant_connected", participant)
            for publication in participant.track_publications.values():
                self.track("track_published", publication)
        return self

    def on_item(self, ev):
        if getattr(ev.item, "type", None) == "message" and ev.item.role == "assistant":
            self.record("assistant_turn", interrupted=ev.item.interrupted)
