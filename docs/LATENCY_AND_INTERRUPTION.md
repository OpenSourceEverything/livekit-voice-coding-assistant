# Latency and interruption evidence

Status: instrumentation and offline tests implemented; real browser/SIP audio
performance has not been measured. Synthetic tests verify event handling,
arithmetic, and privacy only. Reports exclude synthetic samples by default.

CLI fake jobs and all SDK SimulationContext jobs are classified as synthetic,
including simulations that connect to a real room. Synthetic samples are not
used for human-performance claims. Human Console playback was confirmed in a
separate session. One real-phone Stop retest succeeded functionally; the
matching SIP session has two assistant turns marked interrupted. This is a
single-call observation, not a stop-latency measurement. The safe local trace
does not retain transcript text or caller-audio timing.

## Collected evidence

`SessionTelemetry` in `src/telemetry.py` writes JSONL containing a generated
session ID, transport label, numeric metric, unit, and timestamp. It never writes
transcripts, source code, phone numbers, room names, caller identities, tool
arguments, audio, medical information, or provider request IDs. This covers this local collector;
LiveKit Cloud recording and retention are separately configured. Use invented
demo conversations.

LiveKit Agents 1.8 per-turn metrics come from `conversation_item_added` and the
event item's `metrics` dictionary:

| Local metric | SDK field | Scope |
| --- | --- | --- |
| `sdk_e2e_latency` | `e2e_latency` | User speech end to response within SDK |
| `sdk_llm_ttft` | `llm_node_ttft` | First token from LLM node |
| `sdk_tts_ttfb` | `tts_node_ttfb` | First audio chunk from TTS node |
| `sdk_end_of_turn_delay` | `end_of_turn_delay` | Speech end to turn decision |
| `sdk_transcription_delay` | `transcription_delay` | Speech end to transcript |
| `sdk_playback_latency` | `playback_latency` | SDK audio-output handoff |

None of these proves when audio reaches the remote speaker. Room playback
latency is normally near zero because it reports track submission, not network
delivery. LLM/TTS node fields are absent for realtime models. Missing values,
negative sentinels, NaN, and infinity are omitted rather than converted to zero.

The optional compatibility `metrics_collected` callback captures component
latency where available. It is disabled by default; enable it only with
`telemetry.attach(session, legacy_component_metrics=True)`. The session-level
event is deprecated; per-turn metrics are primary.
Component TTFT/TTFB are reported separately and must not be added to the SDK
end-to-end total. Streaming STT's `duration=0` is a sentinel and is omitted.

User/agent state events also produce separately named timing estimates using a
monotonic local clock. `state_overlap_to_agent_stop_estimate` can include a
naturally ending utterance; it does not prove successful cancellation, and the
client can still have buffered audio. Adaptive `overlapping_speech` events add
model detection delays and interruption/backchannel classification counters.
`agent_false_interruption` events and assistant `interrupted` flags are counters,
not manually verified accuracy or a false-positive rate.

## Installation and reporting

Attach once before `session.start()`:

```python
from telemetry import SessionTelemetry

telemetry = SessionTelemetry("artifacts/latency.jsonl", transport="unknown")
telemetry.attach(session)
```

Set `telemetry.set_transport("sip")` after confirming the actual participant kind
is SIP. Set `"browser"` for a known browser/WebRTC client; otherwise retain
`"unknown"`. A room-name prefix is not proof of SIP. New labels affect future
rows only. Use one collector per session.

```sh
uv run python tools/report_latency.py artifacts/latency.jsonl
uv run python tools/report_latency.py artifacts/latency.jsonl --json
```

The report separates browser/SIP/unknown and real/synthetic groups. Latencies
include n, min, p50, p95, and max in seconds using linearly interpolated
percentiles. Counters show totals. `--include-synthetic` shows labeled test rows.
An empty file reports no live measurements and exits zero; absent/malformed
input exits two. `--require-live` exits one without a real latency sample; that
checks data presence only, not performance qualification. Fewer than ten samples
fails the minimum demo gate. Ten turns is still a small descriptive sample and
cannot establish a production percentile objective.

## Account-backed test procedure

1. Record SDK/model versions, region, network, browser/device, date, and transport
   in an experiment report. Use a new numeric file per configuration.
2. Run at least ten normal turns on browser and SIP separately. Keep slow turns,
   errors, and retries visible. Save reports beside a consented demo recording.
   Voice coding examples include asking for a codebase map, explaining a failing
   test, and requesting a focused patch. Telephone sessions are read-only: do not
   execute edits or shell commands through an unauthenticated phone call.
3. On a shared client recording timeline mark user speech end and first audible
   agent speech. Their difference is actual client response latency. Record it
   as `client_response_latency_manual` using `telemetry.record`.
4. Interrupt the agent mid-sentence. Mark audible user onset and final audible
   agent audio on that same timeline. Record the difference as
   `client_barge_in_stop_latency_manual`. Verify it answers the new request and
   does not perform a stale tool action.
5. Test backchannels ("mhm"), mid-thought pauses, noise, repetition, tool timeout,
   and repeated interruptions. Human-label trials and record
   `manual_interruption_trial_count` and `manual_false_interruption_count` when
   applicable. Accuracy needs labels and denominator; SDK classifications do
   not supply that denominator.
6. Compare median/p95/worst case by transport. Examine endpointing, model first
   token/audio, and tool work separately. Document tuning with before/after
   measurements. Preemptive generation and adaptive interruption settings need
   live verification before making any performance claim.

## Primary sources checked October 22, 2026

- [LiveKit session events](https://docs.livekit.io/reference/agents/events/)
- [Per-turn metrics and data hooks](https://docs.livekit.io/testing/observability/data/)
- [Turn handling options](https://docs.livekit.io/reference/agents/turn-handling-options/)
