"""Deterministic Stop control; unit behavior is not a caller-heard benchmark.

These tests separate text classification, the SDK interrupt request, and
suppression of a new response. Their fake session proves the callback contract
only. Real audio behavior is recorded in the call diagnostics and validation
notes.
"""

import asyncio
from types import SimpleNamespace

import pytest
from livekit.agents import Agent
from livekit.agents.llm import ChatContext, ChatMessage, StopResponse

import agent
from agent import Assistant
from interruption import SpeechStopControl, is_stop_request
from settings import Settings


@pytest.mark.parametrize(
    "text", ["Stop.", " PLEASE stop! ", "Stop talking", "be quiet", "stop please"]
)
def test_exact_stop_requests(text):
    """Accept supported commands after case and punctuation normalization."""
    assert is_stop_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "Stop. Stop.",
        "stop stop",
        "STOP! Please stop!",
        "stop, stop talking",
        "be quiet; stop",
    ],
)
def test_merged_repeated_stop_commands(text):
    """Accept short repetitions combined into one final STT transcript."""
    assert is_stop_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "don't stop",
        "explain the stop function",
        "stop and explain uart.c",
        "Stop. Don't stop.",
        "stop stop explaining uart.c",
        "stopwatch",
        "nonstop",
        "stop.stop function",
        "okay",
        "",
        None,
        "stop" * 100,
    ],
)
def test_non_commands_not_interrupted(text):
    """Reject substrings, questions, negation, and invalid or oversized input."""
    assert not is_stop_request(text)


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["Stop!", "Stop. Stop."])
async def test_final_stop_clears_speech_without_llm_or_task_cancellation(command):
    """Only a final command interrupts; repeated callbacks respect cooldown."""
    future = asyncio.get_running_loop().create_future()
    calls = []
    events = []
    session = SimpleNamespace(
        agent_state="speaking", interrupt=lambda: calls.append("interrupt") or future
    )
    control = SpeechStopControl(
        session, record=lambda name: events.append(name), clock=lambda: 1
    )
    control.on_transcript(SimpleNamespace(is_final=False, transcript="stop"))
    control.on_transcript(SimpleNamespace(is_final=True, transcript="don't stop"))
    assert calls == []
    control.on_transcript(SimpleNamespace(is_final=True, transcript=command))
    control.on_transcript(SimpleNamespace(is_final=True, transcript=command))
    assert calls == ["interrupt"]
    future.set_result(None)
    await asyncio.sleep(0)
    assert events == ["explicit_stop_requested", "explicit_stop_completed"]
    # The controller has no repository, saved-job or shutdown methods.
    assert session.agent_state == "speaking"


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["Stop.", "Stop. Stop."])
async def test_stop_node_suppresses_new_reply_but_next_turn_can_proceed(command):
    """Stop is silent without disabling the next conversational turn."""
    bot = SimpleNamespace(explicit_stop=True)
    with pytest.raises(StopResponse):
        await Assistant.on_user_turn_completed(
            bot, ChatContext(), ChatMessage(role="user", content=[command])
        )
    await Assistant.on_user_turn_completed(
        bot, ChatContext(), ChatMessage(role="user", content=["Explain uart.c"])
    )
    bot.explicit_stop = False
    await Assistant.on_user_turn_completed(
        bot, ChatContext(), ChatMessage(role="user", content=["Stop."])
    )


def test_interrupt_unavailable_is_logged_without_exception_text():
    """Count SDK failure safely and discard provider exception text."""

    def fail():
        raise RuntimeError("PRIVATE SHOULD NOT BE LOGGED")

    events = []
    control = SpeechStopControl(
        SimpleNamespace(agent_state="speaking", interrupt=fail),
        record=lambda e: events.append(e),
    )
    control.on_transcript(SimpleNamespace(is_final=True, transcript="stop"))
    assert events == ["explicit_stop_requested", "explicit_stop_unavailable"]


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["Stop.", "Stop. Stop."])
async def test_direct_text_generation_cannot_bypass_stop_hook(monkeypatch, command):
    """Guard direct text calls that bypass the speech-complete hook."""
    bot = object.__new__(Assistant)
    bot.explicit_stop = True
    context = ChatContext()
    context.add_message(role="user", content=command)
    marker = object()
    calls = []
    monkeypatch.setattr(Agent, "llm_node", lambda *args: calls.append(True) or marker)
    assert await bot.llm_node(context, [], None) is None
    assert calls == []
    context.add_message(role="user", content="Which file did we inspect?")
    assert bot.llm_node(context, [], None) is marker
    assert calls == [True]


@pytest.mark.parametrize("value", ["nan", "inf", "0", "-1", "3", "not-a-number"])
def test_invalid_interruption_timing_rejected(monkeypatch, value):
    """Reject invalid thresholds rather than silently altering turn behavior."""
    monkeypatch.setenv("INTERRUPTION_MIN_DURATION", value)
    with pytest.raises(ValueError):
        Settings.from_env()


def test_local_vad_is_default_and_adaptive_is_explicit_opt_in(monkeypatch):
    """Use local VAD by default while allowing deliberate adaptive opt-in."""
    monkeypatch.delenv("ADAPTIVE_INTERRUPTION", raising=False)
    assert Settings.from_env().adaptive_interruptions is False
    monkeypatch.setenv("ADAPTIVE_INTERRUPTION", "true")
    assert Settings.from_env().adaptive_interruptions is True


@pytest.mark.parametrize("adaptive,expected_mode", [(False, "vad"), (True, "adaptive")])
def test_session_passes_explicit_interruption_mode_to_installed_sdk(
    monkeypatch, adaptive, expected_mode
):
    """Verify the selected mode reaches the installed SDK session options."""
    monkeypatch.setenv("ADAPTIVE_INTERRUPTION", str(adaptive).lower())
    for name in ("LLM", "STT", "TTS", "TurnDetector"):
        monkeypatch.setattr(agent.inference, name, lambda **kwargs: None)
    monkeypatch.setattr(agent, "AgentSession", lambda **kwargs: kwargs)
    result = agent.build_session(Settings.from_env(), vad_model=object())
    options = result["turn_handling"]["interruption"]
    assert options["mode"] == expected_mode
    assert options["enabled"] is True
    assert options["min_duration"] == 0.5
    assert options["min_words"] == 0
