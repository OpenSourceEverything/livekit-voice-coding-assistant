"""Keypad gate cases use no telephony provider and never print entered digits.

The fake clock makes timeout behavior deterministic. The tests exercise one
call's gate at a time; they do not claim cross-worker abuse protection.
"""

import pytest

from phone_auth import DtmfGate


def enter(gate: DtmfGate, digits: str) -> bool:
    result = False
    for digit in digits:
        result = gate.feed(digit)
    return result


@pytest.mark.parametrize(
    "pin",
    [
        "12345",
        "1234567890123",
        "abcdef",
        "\uff11\uff12\uff13\uff14\uff15\uff16",
        "123 456",
        "",
        None,
    ],
)
def test_pin_requires_six_to_twelve_ascii_digits(pin) -> None:
    """Accept only PINs in the fixed ASCII-digit length range."""
    with pytest.raises(ValueError, match="six to twelve"):
        DtmfGate(pin)


@pytest.mark.parametrize("pin", ["012345", "123456789012"])
def test_matching_pin_needs_submit_and_preserves_leading_zeroes(pin: str) -> None:
    """Require the terminator and compare the exact configured digit string."""
    gate = DtmfGate(pin)
    assert not enter(gate, pin)
    assert not gate.authenticated
    assert gate.feed("#")
    assert gate.authenticated
    assert not gate.locked


def test_star_clears_current_entry_without_consuming_attempt() -> None:
    """Let the caller correct an entry without counting a failed submission."""
    gate = DtmfGate("123456")
    assert not enter(gate, "999999*999999*999999*")
    assert enter(gate, "123456#")


def test_three_wrong_submissions_permanently_lock_only_this_call() -> None:
    """Lock the current call after its retry limit, without global lock state."""
    gate = DtmfGate("123456")
    assert not enter(gate, "000000#")
    assert not gate.locked
    assert not enter(gate, "000000#")
    assert not gate.locked
    assert not enter(gate, "000000#")
    assert gate.locked
    assert not enter(gate, "*123456#")
    assert not gate.authenticated
    assert enter(DtmfGate("123456"), "123456#")


def test_correct_third_submission_is_allowed() -> None:
    """Permit the final allowed attempt when it contains the right PIN."""
    gate = DtmfGate("123456")
    assert not enter(gate, "000000#111111#")
    assert enter(gate, "123456#")


def test_empty_submissions_consume_attempts() -> None:
    """Do not let empty terminator presses bypass the attempt limit."""
    gate = DtmfGate("123456")
    assert not enter(gate, "###")
    assert gate.locked


def test_deadline_is_per_call_and_cannot_be_extended_by_activity() -> None:
    """Enforce an absolute call-local deadline even while keys are arriving."""
    now = [10.0]
    gate = DtmfGate("123456", clock=lambda: now[0])
    now[0] = 69.9
    assert not enter(gate, "123456")
    now[0] = 70.0
    assert gate.locked
    assert not gate.feed("#")
    now[0] = 71.0
    assert not enter(gate, "*123456#")


def test_success_before_deadline_stays_authenticated_for_call() -> None:
    """Keep successful authentication scoped to this gate's call lifetime."""
    now = [0.0]
    gate = DtmfGate("123456", clock=lambda: now[0])
    now[0] = 59.9
    assert enter(gate, "123456#")
    now[0] = 61.0
    assert gate.authenticated
    assert not gate.locked
    assert gate.feed("*")


def test_overlength_entry_cannot_authenticate_by_truncation() -> None:
    """Reject an overlong buffer instead of comparing a truncated PIN prefix."""
    gate = DtmfGate("123456789012")
    assert not enter(gate, "1234567890123#")
    assert not gate.authenticated
    assert enter(gate, "123456789012#")


def test_unsupported_dtmf_events_do_not_change_entry() -> None:
    """Ignore unsupported DTMF event types without changing the typed buffer."""
    gate = DtmfGate("123456")
    assert not gate.feed("A")
    assert not gate.feed("\uff11\uff12")
    assert not gate.feed("123456")
    assert not gate.feed(1)
    assert enter(gate, "123456#")


def test_digits_do_not_produce_console_output(capsys) -> None:
    """Protect the central privacy property: keypad digits never reach output."""
    assert enter(DtmfGate("123456"), "123456#")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
