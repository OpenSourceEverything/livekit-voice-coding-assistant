# Voice Coding Assistant: SIP debugging runbook

Observed issue: an inbound call reached the agent worker, which then failed
during application startup. Registering the VAD plugin on the main thread before
offloading model construction resolved that startup exception. A later inbound
call completed, and one phone Stop check passed functionally. No SIP signaling,
codec, NAT, or RTP fault has been confirmed; see [validation](VALIDATION.md) for
the scope and limits of those checks.

The diagnostic steps below distinguish transport, dispatch, application
startup, and media hypotheses. Do not inject faults into a live route. Complete
[SIP_SETUP.md](SIP_SETUP.md) before an isolated lab experiment.

Phone access requires PHONE_ACCESS_PIN in the environment and keypad DTMF
authentication. Enter six to twelve digits followed by #; * clears an entry.
Three failed submissions or sixty seconds lock that call's gate. PIN digits
are handled outside the model conversation. Do not copy them into investigation
notes, transcripts, or public evidence.

## Locate the failing boundary

1. Record direction, UTC time, local code revision, provider call ID, SIP
   Call-ID, LiveKit call ID, trunk ID, room, and agent dispatch name privately.
2. In the provider call view, establish whether the provider attempted the
   expected destination. In LiveKit Telephony → Calls, check whether its INVITE
   arrived and which final response was returned.
3. Check whether the SIP participant and the correct agent joined the room.
4. If setup succeeded, investigate media separately. Confirm offered/accepted
   codecs, SDP addresses/ports, and RTP in both directions.
5. If audio works but the dialogue fails, inspect STT, tool outcomes, model
   timing, and interruption events in the agent trace/Console.

The [LiveKit troubleshooting guide](https://docs.livekit.io/reference/telephony/troubleshooting/)
documents the call view, correlated identifiers, and downloadable PCAP.

## Read the protocol accurately

- SIP handles setup/control; RTP carries media, and SDP describes the proposed
  media path. A successful handshake does not prove working audio.
  [Source: SIP primer](https://docs.livekit.io/reference/telephony/sip-primer/)
- A 401 or 407 can be a normal digest challenge. Look for the authenticated retry
  and its final result before calling it an authentication fault. A final 403
  can reflect credentials or policy; it is not a universal bad-password code.
  [Source: RFC 3261, sections 21.4 and 22](https://www.rfc-editor.org/rfc/rfc3261.html)
- A trunk may use an FQDN/IP route rather than REGISTER. Test the authentication
  mechanism of the actual selected path, not a guessed softphone flow.
  [Source: SIP trunk setup](https://docs.livekit.io/telephony/start/sip-trunk-setup/)
- Codec mismatch may prevent call setup; media routing/NAT/firewall errors can
  leave an accepted call silent or one-way. Check evidence before choosing one.
  [Source: Twilio trunking](https://www.twilio.com/docs/sip-trunking)

## Capture where the traffic exists

For LiveKit Cloud, download an available call PCAP from Telephony → Calls →
the call → PCAP. Obtain provider-side evidence when the call never reaches
LiveKit. Laptop Wireshark cannot capture a private provider-to-Cloud network
leg; local capture is useful only for traffic actually traversing that host.
TLS/SRTP may prevent reading signaling or payloads in a capture.
[Sources: LiveKit troubleshooting](https://docs.livekit.io/reference/telephony/troubleshooting/),
[Twilio secure trunking](https://www.twilio.com/docs/sip-trunking#secure-trunking)

In Wireshark, inspect the SIP ladder and SDP, then RTP streams in both
directions. Use actual endpoint addresses to filter packets. Do not copy a
private-IP filter from an unrelated deployment and treat it as a universal
direction test. Keep original PCAPs private: they can expose numbers, addresses,
authentication material, and recorded audio.
[Wireshark's RTP guide](https://www.wireshark.org/docs/wsug_html_chunked/ChTelRTP.html)
explains stream pairs, analysis, timing, and audio playback. Finding a reverse
stream is a useful media check; lack of decoded playback alone does not prove
that no packets arrived.

## Controlled fault matrix

Use an isolated lab trunk/rule and save its working configuration first.
Inject one change, make one controlled test call, collect evidence, restore the
configuration, and retest the same acceptance path.

| Lab change | Expected symptom to test | Distinguishing evidence |
| --- | --- | --- |
| Dispatch rule names an unregistered agent | Caller reaches a room but bot is absent | SIP participant joined; agent dispatch/registration mismatch |
| Provider points at an incorrect lab endpoint | Call fails before expected room creation | Provider route detail; absence of INVITE in intended project |
| Wrong destination number match on lab inbound trunk | Provider reaches LiveKit but route/trunk lookup fails | Actual request destination, trunk number, returned status |
| Wrong credentials on a supported digest-auth path | Authenticated retry is rejected | Complete challenge/retry exchange and final provider reason |
| Incompatible codec offers on configurable lab trunk | Rejected negotiation or no usable media | Actual SDP and final response; do not predict one status |
| Inject unavailable sample workspace/file | Call remains connected; tool reports its actual error | Workspace tool result and verified unchanged files |
| Missing or incorrect keypad authentication | Repository tools remain inaccessible | Tool availability and private gate state, without recording PIN digits |
| Clear correction during bot speech | Bot yields and updates request | Aligned audio, interruption event, subsequent tool arguments/results |

The last two investigate the application after telephony transport works.
They are not evidence of a SIP-layer defect.

## Optional lab-only investigation: dispatch mismatch (not currently authorized)

1. Confirm one successful read-only code question over the telephone and save
   evidence of the actual search/read results.
2. Save the lab dispatch rule's current full configuration. Preserve its rule
   ID and trunk binding; do not alter a production/shared rule.
3. Change only the configured agent name to a unique nonexistent lab name.
4. Call the lab number. Observe the provider result, LiveKit room participants,
   and agent logs. A specific SIP code is not prescribed.
5. Test the hypothesis: caller routing works, but the named agent is unavailable.
6. Restore the saved rule. Repeat the same call and verify both the voice
   response and the correct bounded workspace tool results.
7. Describe this as an intentionally reproduced lab dispatch failure. Dispatch
   is a telephony integration failure boundary, not a digest-authentication bug.

Use the CLI's current update semantics carefully: replacing a resource with
incomplete JSON can clear omitted fields. Review its help and the saved full
configuration. [Source: telephony CLI](https://docs.livekit.io/reference/developer-tools/livekit-cli/telephony/)

## One incident record per experiment

Copy this template after the experiment actually runs:

```text
Experiment ID:
Date/time UTC:
Code revision and configuration version:
Lab fault deliberately injected:
Call direction and transport:
Private call-ID index location:
Expected behavior:
Observed behavior:
Observed SIP ladder and final response:
Room/SIP participant/agent state:
Negotiated codec and observed media direction:
Evidence files (private originals; redacted public copies):
Hypotheses considered:
Test that distinguished the cause:
Confirmed cause (or still unconfirmed):
Smallest change made:
Restored-call result and workspace-result verification:
Regression coverage:
Remaining uncertainty:
```

## Known observations

- Confirmed application issue: an inbound call reached the worker, then startup
  failed because Silero plugin registration ran on an executor thread. Moving
  registration to the main thread before model construction resolved it.
- A later inbound call completed. A separate single-call Stop check passed
  functionally; see [validation](VALIDATION.md) for its limits.
- No SIP authentication, dispatch, SDP, codec, NAT, RTP, or carrier fault has
  been confirmed. The fault matrix above is a lab method, not a report of a
  fault already reproduced.
