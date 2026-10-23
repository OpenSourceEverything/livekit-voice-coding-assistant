"""Narrow speech-stop control, never task cancellation or repository approval."""

from __future__ import annotations

import re
import time

_STOP = frozenset(
    {
        "stop",
        "please stop",
        "stop please",
        "stop talking",
        "please stop talking",
        "be quiet",
    }
)
_COMMAND = (
    "(?:" + "|".join(re.escape(s) for s in sorted(_STOP, key=len, reverse=True)) + ")"
)
_SEPARATOR = r"[\s.!?,;:]+"
_STOP_SEQUENCE = re.compile(rf"{_COMMAND}(?:{_SEPARATOR}{_COMMAND})*[\s.!?,;:]*")


def is_stop_request(text) -> bool:
    if not isinstance(text, str) or len(text) > 80:
        return False
    # STT can merge repeated final commands into one turn: "Stop. Stop.".
    # Full-string matching still rejects "don't stop" and source questions.
    normalized = " ".join(text.casefold().strip(" \t\r\n.!?,;:").split())
    return _STOP_SEQUENCE.fullmatch(normalized) is not None


class SpeechStopControl:
    """Final-transcript exact match avoids cutting on partial 'stop function ...'."""

    def __init__(
        self, session, *, record=lambda *_args, **_kwargs: None, clock=time.monotonic
    ):
        self.session = session
        self.record = record
        self.clock = clock
        self.last_request = float("-inf")

    def on_transcript(self, event):
        if (
            not event.is_final
            or not is_stop_request(event.transcript)
            or self.session.agent_state not in {"speaking", "thinking"}
            or self.clock() - self.last_request < 0.5
        ):
            return
        self.last_request = self.clock()
        self.record("explicit_stop_requested")
        try:
            future = self.session.interrupt()
        except RuntimeError:
            self.record("explicit_stop_unavailable")
            return

        def completed(result):
            if result.cancelled() or result.exception() is not None:
                self.record("explicit_stop_unavailable")
            else:
                # SDK completion is not evidence of remote speaker silence.
                self.record("explicit_stop_completed")

        future.add_done_callback(completed)

    def attach(self):
        self.session.on("user_input_transcribed", self.on_transcript)
        return self
