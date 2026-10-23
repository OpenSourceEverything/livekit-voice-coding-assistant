# Validation and evidence limits

This report separates repeatable local tests, provider-backed integration
checks, and the limited manual audio observations. Results apply to the local
checkout/revision where the commands were run; they are not a production SLA.

## Repeatable local checks

Run from the repository root:

```bash
make check
make acceptance
```

The current local run passed 245 tests, formatting/lint checks, and all 15
offline acceptance scenarios. Coverage includes keypad authentication,
workspace boundaries, source-hash-bound proposals, interruption handling,
usage/latency filtering, bounded subprocesses, saved-job recovery, and
read-only cloud/phone policy. Acceptance runs against a disposable synthetic
repository, made no provider calls, and did not modify any existing workspace.
It verifies application boundaries, not model quality, phone audio, SIP, or
deployment behavior.

## Integration observations

- Browser/debugger sessions exercised real STT → LLM/tool → TTS paths and
  repository search/read/Git operations against synthetic source.
- One generated-speech synthetic turn measured 2.832 seconds SDK end-to-end,
  0.236 seconds LLM first-token, and 0.493 seconds TTS first-audio. This is one
  synthetic sample, includes generated input/playback overhead in CLI time, and
  is not a caller-heard latency benchmark.
- An inbound call reached the agent worker but failed before greeting because
  the Silero plugin's LiveKit registration ran on an executor thread. Moving
  registration to the main thread before blocking model construction fixed the
  startup exception; model-load and regression checks passed. A subsequent
  inbound call completed. This was an application-startup issue, not an
  established SIP signaling, codec, NAT, or RTP fault.
- A real-phone Stop trial first exposed adaptive-classifier quota failures and
  merged repeated Stop transcription. The matcher now accepts short repeated
  Stop commands, and local VAD is the default to avoid that classifier request.
  One later phone Stop retest succeeded functionally; the count-only trace
  recorded interrupted assistant turns and normal hangup. It does not establish
  stop latency, interruption accuracy, or reliability rates. The classifier
  quota issue itself remains unresolved.
- A detached proposal exercise used only synthetic source and left its source
  unchanged. It did not prove a real phone hang-up/reconnect workflow, a change
  to another repository, PR creation, deployment, outbound calling, or SMS.

## Known limits

No repeated caller-audio benchmark, false-interruption denominator, verified
SIP signaling/media fault, provider failover, load test, or production
deployment qualification is available. Automatic PR creation, phone approval,
merge, deployment, callbacks, and SMS are not implemented. Treat measured
provider/debugger values as individual observations, not expected production
performance.

The demonstration uses synthetic source. Numeric telemetry distinguishes
synthetic sessions. Do not publish raw source, transcripts, phone numbers,
call identifiers, or credentials. See [measurement limits](LATENCY_AND_INTERRUPTION.md)
and the [SIP runbook](SIP_DEBUGGING_RUNBOOK.md).
