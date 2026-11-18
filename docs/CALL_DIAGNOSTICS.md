# Debug a call without dumping private conversation

Run `make call-report` for a safe count summary of the most recently observed
live session. No recorded calls yet is normal before the first call on this
version; old calls are not reconstructed. `make usage` and the latency reporter
answer different questions. The troubleshooting history is not a replacement
for these logs, but preserves the cause, fix and retest for each actual incident.

## Watch a local call live

Start the local worker in a terminal with INFO logging, using the development
deployment selected by the existing phone dispatch rule:

```bash
.tools/bin/lk --project YOUR_PROJECT_ALIAS agent dev --log-level INFO --deployment YOUR_PHONE_DISPATCH_DEPLOYMENT
```

Replace `YOUR_PROJECT_ALIAS` with the alias from `lk project list`, and
`YOUR_PHONE_DISPATCH_DEPLOYMENT` with the development deployment ID selected by
the existing phone dispatch rule. Watch this terminal while placing the call.
The added lines use this format:

```text
agent.py::agent::search_repository: tool_enter - params=query tool=search_repository
call_diagnostics.py::call_diagnostics::agent_state: agent_state - elapsed_ms=840 previous_state=listening state=speaking
```

Tool logs show which argument names were supplied, elapsed time, outcome, and
result shape. They deliberately do not print argument values or returned source,
diffs, transcripts, PIN digits, phone numbers, or provider error text. Partial
transcription notifications are omitted from the live console to avoid spam;
the privacy-filtered event file keeps only whether a transcript was final.
These application trace lines appear where the worker process runs. A Cloud
worker's terminal output must be viewed in its deployment logs instead.

For a deployed Cloud agent, the wrapper can list agents in the selected project,
prompt for one, check its status, then tail LiveKit's Cloud log stream:

```bash
make watch-agent
```

Or provide the agent ID directly. To select one deployment and save new output
as it arrives:

```bash
make watch-agent ARGS="CA_AGENT_ID --deployment DEPLOYMENT_ID --save var/cloud-agent.log"
```

The save file is append-only and owner-only. Cloud logs are not filtered by
the local privacy-safe trace helper; keep saved output private. This watches the
deployed version only. It does not deploy local edits or monitor a local worker.

## What is recorded

`src/call_diagnostics.py` writes ignored `var/calls.jsonl` with owner-only file
permissions, a random session ID shared with the local latency collector,
timestamps, allowlisted event names, numbers, flags and known enum values.

- RTC connection/reconnection, remote participant kind, incoming subscription
  failures, local/remote audio publication and known codec labels.
- SIP participant callStatus transitions, not phone/call/trunk identifiers.
- User/agent states, whether a final transcript arrived (not its content),
  interruption classifications, false-interruption resume and session closure.
- Explicit stop request and SDK completion, not a claim of remote-speaker silence.

No transcript, source, PIN, DTMF digit, phone number, room/participant identity,
provider error body, access key, IP address, SDP or packet payload is recorded
by this collector. Unknown fields/values are dropped rather than redacted with
fragile search/replace. Writes stop at roughly 8 MiB instead of growing forever;
the summary reader also has limits. A log write failure does not end the call.
Keep logs private. SDK terminal logs and LiveKit Cloud recording/traces have
their own content and retention policies; this collector does not sanitize them.

For a labeled synthetic debugger run only:

```bash
.tools/bin/uv run python tools/report_call.py --include-synthetic
```

## Small diagnosis tree

1. No session_start: inspect worker registration, dispatch and startup errors.
   Absence is not proof of a carrier problem; attachment occurs after model load.
2. SIP active plus subscribed/published audio: the room path exists; this does
   not prove the caller hears audio or that both RTP directions work.
3. User speaking without final transcription: investigate recognition/input
   and transcription_timeout before blaming the language model.
4. Final standalone stop with explicit_stop_requested/completed: the SDK accepted
   the interruption. If sound continues, investigate remote buffering/playout
   with a human observation; do not label SDK completion acoustic latency.
5. false_interruption with resumed=true: the SDK resumed speech. Compare with
   the actual listening test to distinguish noise/backchannel from a false resume.

## Stop behavior and verification

`src/interruption.py` matches only short final commands such as "stop",
"please stop", or merged repetitions such as "Stop. Stop.". The entire text
must consist of recognized commands; this is not substring matching.
It calls the public session.interrupt() API without waiting
for an LLM. Interim "stop" is ignored because the completed sentence might be
"stop function ...". "Don't stop" and normal questions are not commands.
The speech-completed hook suppresses a fresh acknowledgment. Direct text
generation bypasses that hook, so the LLM node applies the same silence rule.
Stopping speech never approves, cancels or rolls back a persisted coding job.

Local VAD interruption is now the default, with the SDK's 0.5-second minimum.
It avoids cloud adaptive-classifier requests, not other call/model charges.
Tradeoff: acknowledgments such as "okay" and background speech may also cut
the agent off. ADAPTIVE_INTERRUPTION=true explicitly restores the classifier
for a controlled comparison; do not assume an upgrade fixes application bugs.
EXPLICIT_STOP=false disables the added final-command path. Timing is controlled
by INTERRUPTION_MIN_DURATION (validated 0.1–2 seconds). Keep timing unchanged
for the first retest; quicker interruption can also create false stops.
Restart the worker after configuration changes. `make status` shows configured
mode only; `make call-report` reports the mode used by the latest recorded call.

### Successful real-phone Stop retest (2026-10-23)

One manual real-phone Stop retest completed successfully. The count-only local
report recorded one real SIP session in VAD mode, four assistant turns, two
assistant turns marked `interrupted: true`, and normal participant hangup/session
closure. The report contains no transcript; the listening observation confirms
that the requested stop behavior worked in this single trial.

This is one functional pass. The log does not prove that
each interrupted turn was caused by a Stop command, does not show exactly when
the caller first spoke, and does not measure when the last audio reached the
caller. Do not report an acoustic stop-latency number, interruption accuracy,
or production reliability from this trial. An earlier call failed with
adaptive-classifier quota errors. The current default VAD path avoids that
classifier request, but does not fix the cloud classifier quota itself.

When the local worker is registered and the human is ready, use one short call:

1. Authenticate by keypad as usual. Ask: "Read uart.c and explain its buffer."
   If it cannot locate the source or is silent, that is a failed source-response
   trial: report it and stop. Do not wait on an unresponsive call indefinitely.
2. While it is talking, say "Stop" once. Stay silent three seconds. Record whether
   it stopped on the first attempt and whether it resumed unexpectedly.
3. Ask: "Which file did we just inspect?" It should answer uart.c. If there is
   time, request another explanation and interrupt with "Stop. Stop." to exercise
   merged-command handling. Hang up within one minute; do not keep retrying.
4. Inspect make call-report and the numeric latency report. Repeat a separate
   "okay" backchannel trial only if useful and within the remaining allowance.

Text-debugger silence/recovery passed. The judged simulator rejects empty
responses, so it failed the intentional-silence scenario; this is recorded as
a failed/unsupported run, not a pass. Offline event tests are not audio trials.
An earlier real phone trial failed with unresponsiveness; worker logs showed
adaptive error 2005, "quota exceeded", with retries. Its
transcript also contained merged "Stop. Stop." that the old matcher rejected.
The exact exhausted quota and the timing of SDK automatic fallback were not
established. A later caller-heard test succeeded after the merged-command match
and local-VAD default changes; the count-only SIP trace and evidence limits for
that pass are recorded above. No SIP signaling/media defect was established.

## What these logs cannot provide

This local agent is a WebRTC participant connected to LiveKit's managed SIP
gateway. These are SDK lifecycle logs, **not** raw SIP INVITE/SDP/RTP captures,
browser ICE-candidate traces, or carrier-side evidence. Inspect the selected
project's Sessions → matching call → Agent insights for retained Cloud data.
Raw signaling/media diagnosis requires the relevant gateway/browser/provider
evidence, handled separately and redacted before sharing. Do not enable a global
TRACE dump or change codecs/NAT simply because a lifecycle event is missing.

References: [session events](https://docs.livekit.io/reference/agents/events/),
[turn control](https://docs.livekit.io/agents/logic/turns/),
[adaptive interruptions](https://docs.livekit.io/agents/logic/turns/adaptive-interruption-handling/),
[telephony testing](https://docs.livekit.io/telephony/testing/).
