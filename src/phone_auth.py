"""Per-call keypad authentication; digits never enter model conversation state."""

from __future__ import annotations

import hmac
import re
import time
from collections.abc import Callable


class DtmfGate:
    """Accept a six-to-twelve-digit PIN with three attempts in sixty seconds.

    ``#`` submits and ``*`` clears the current entry. The owner creates a new
    instance for each call. Authentication only permits the caller's existing
    read-only tool set; it cannot authorize desktop writes.
    """

    def __init__(self, pin: str, *, clock: Callable[[], float] = time.monotonic):
        if not isinstance(pin, str) or re.fullmatch(r"[0-9]{6,12}", pin) is None:
            raise ValueError("Phone PIN must contain six to twelve ASCII digits")
        self._pin = pin
        self._clock = clock
        self._deadline = clock() + 60.0
        self._entry = ""
        self._overflow = False
        self._attempts = 0
        self._authenticated = False
        self._locked = False

    @property
    def authenticated(self) -> bool:
        return self._authenticated

    @property
    def locked(self) -> bool:
        if not self._authenticated and self._clock() >= self._deadline:
            self._locked = True
            self._entry = ""
            self._overflow = False
        return self._locked

    def feed(self, digit: str) -> bool:
        """Consume one keypad event, returning whether this call is authenticated."""
        if self._authenticated:
            return True
        if self.locked:
            return False
        if not isinstance(digit, str) or len(digit) != 1:
            return False
        if digit == "*":
            self._entry = ""
            self._overflow = False
        elif digit == "#":
            matches = hmac.compare_digest(self._entry, self._pin)
            self._authenticated = matches and not self._overflow
            self._entry = ""
            self._overflow = False
            if not self._authenticated:
                self._attempts += 1
                self._locked = self._attempts >= 3
        elif "0" <= digit <= "9":
            if len(self._entry) < 12:
                self._entry += digit
            else:
                self._overflow = True
        return self._authenticated
