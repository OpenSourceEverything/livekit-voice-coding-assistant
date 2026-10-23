# Voice Coding Assistant: telephone setup

This guide configures optional inbound SIP for an existing LiveKit project.
Use the project's configured local worker and agent deployment; do not assume
that a Cloud demo, local development worker, and production worker share a
dispatch rule. Configure PHONE_ACCESS_PIN privately before enabling telephone
access.

The useful first deliverable is a browser voice interface to a local coding
assistant. Telephone access defaults to a separate read-only path. A spoken PIN,
caller ID, or CLI login does not authorize remote desktop writes.

Optional supervised change initiation: ENABLE_PHONE_PROPOSALS=true on the local
worker permits an authenticated caller to stage an existing-file proposal when
ENABLE_PROPOSALS is also enabled. Source remains unchanged until separate human
local review/apply. The flag defaults false; it is ineffective in the Cloud
demo. Tests, apply, PR creation, push, and deployment are not available through
the phone interface.

Use `make status` for local readiness and recorded usage. It does not query an
account balance.

## Use an existing Cloud project

1. Open https://cloud.livekit.io/ and select the existing project intended for
   this checkout. Replace PROJECT_NAME below with its configured CLI name.
   These instructions do not create a project or other Cloud resources.
2. In a local terminal, run:

   ```bash
   cd /path/to/checkout
   export PATH="$PWD/.tools/bin:$PATH"
   lk --project PROJECT_NAME project list
   ```

   If this machine is not authenticated, run `lk cloud auth` and select the
   intended existing project in the browser.
3. Keep the existing `.env.local`. Only if missing, run
   `lk --project PROJECT_NAME app env --write --destination .env.local` here.
   Alternatively fill it from `.env.example` using that project's keys.
4. Configure the allowed workspace in the local settings described by README.md.
   Start with a disposable example repository; test execution remains opt-in.
5. Run `uv sync --locked`, then `lk --project PROJECT_NAME agent dev`. In the
   browser Agent Console, select the configured local worker. For `agent dev`,
   also select the development deployment printed in the terminal. A SIP rule
   must target the intended agent and deployment, not silently default to
   production.

CLI sign-in and `.env.local` are separate configuration surfaces. Keep keys in
the local file, never in chat or committed source. The
[official quickstart](https://docs.livekit.io/agents/start/voice-ai/) documents
Cloud linking. The [Agent Console](https://docs.livekit.io/testing/agent-console/)
shows live audio, function calls, events, and model timing.

The agent process stays on the computer holding the selected code workspace.
Deploying that worker to LiveKit Cloud would not give it the desktop's local
files. The hosted audio/inference service and the local worker have different
roles.

## Browser acceptance

Open the selected project's Agents page, choose Launch Console, select the
intended deployment, start a session, and grant microphone permission. Use
synthetic/sample code that may be sent to the configured model service.

This is an acceptance checklist, not a claim that every live test has passed.
Keep operator account details and raw call identifiers out of public evidence.

| Test | Pass condition |
| --- | --- |
| Search | Finds an existing function and explains its role |
| Read | Reads only within the configured workspace |
| Boundary | Parent traversal, sensitive paths, and escapes are rejected |
| Git | Reports actual status/diff without modifying history |
| Checks | Only explicitly enabled allowlisted commands run |
| Patch | Saves a proposal for local review before apply |
| Interrupt | A clear correction stops speech and updates the request |
| Backchannel | Brief acknowledgment differs from a real interruption |
| Slow tool | Clear progress/result; interruption does not imply rollback |

`lk --project PROJECT_NAME agent console` runs terminal/microphone mode. The browser Agent Console
is a separate tool. Neither alone proves a SIP trunk works.
[Source: Agent Console](https://docs.livekit.io/testing/agent-console/)

## Choose and configure an inbound number

Check the current LiveKit number capabilities, account entitlements, and price
before renting or assigning a number. Included allowances and pricing can
change. Review billing and usage separately from capacity-limit graphs; a usage
limit is not itself an invoice.

After the browser test passes and the operator has checked number availability,
capabilities, and price:

1. In LiveKit Cloud, open Telephony → Phone Numbers → Rent a number.
2. Search a US local area code and inspect the displayed account entitlement
   and price before confirming the rental. Stop if it would incur a charge.
3. Configure an individual-call SIP dispatch rule with the intended agent
   identity and deployment. For a local development worker, use its printed
   development deployment.
4. Assign the number to that dispatch rule using its Options menu.
5. Keep the local worker running and call the configured number. Test a bounded
   code question against the disposable workspace.

Direct LiveKit numbers do not need a third-party inbound trunk. An individual
dispatch rule isolates calls, but its room names can include caller numbers;
keep original call evidence private and publish redacted copies.
[Sources: number setup](https://docs.livekit.io/telephony/start/phone-numbers/),
[dispatch rules](https://docs.livekit.io/telephony/accepting-calls/dispatch-rule/)

## Existing Google Voice number

Google documents forwarding to verified linked phone numbers. However, the same
official help page explicitly says forwarding to an automated system is
unsupported. It does not guarantee that a given external provider's PSTN number
can be linked or that it will work as a bot destination. Consequently this
project's supported first test is to call the LiveKit number directly, rather
than putting consumer Google Voice forwarding in front of the bot.
[Source: Google Voice setup](https://support.google.com/voice/answer/165221?hl=en)

The consumer setup guide provides Google sign-in/apps and linked-number
forwarding, not a generic SIP username/password trunk to configure in LiveKit.
Google's documented SIP Link is a separate business integration requiring
Standard/Premier Voice and a certified Session Border Controller. Do not equate
the two products. [Source: Google SIP Link requirements](https://support.google.com/a/answer/11606486?hl=en)

These are narrow support/integration findings. They are not a blanket assertion
that Google forbids all automation or a guarantee that forwarding is technically
impossible in every configuration. No Google Voice account changes are required
for the recommended direct-number path.

## External SIP provider option

Use an external trunk later if its configuration/debugging experience justifies
the separate provider account and number. This is optional; a direct LiveKit
number plus a documented dispatch investigation provides an earlier telephone
demo.

The Twilio inbound-only lab path is:

`test phone → provider lab number → Twilio origination URI → LiveKit inbound trunk → isolated room + selected agent`

1. Copy the LiveKit SIP URI from Project settings, not the WebSocket URL.
2. Create a Twilio Elastic SIP Trunking lab trunk. On Origination, set the
   documented destination format, e.g.
   `sip:PROJECT.sip.livekit.cloud;transport=tcp` (placeholder, not a real endpoint).
3. Associate a provider lab number; review its charges and restrictions first.
4. Create a LiveKit inbound trunk matching that E.164 destination and the chosen
   provider's supported authentication/source restrictions. A number match is
   routing, not proof of caller identity.
5. Bind an individual dispatch rule explicitly to this trunk's ID and configure
   `room_config.agents` with the selected dispatch name.
6. List/review the project, number, trunk binding, and agent before the test call.

Twilio calls provider-to-application traffic **origination**. **Termination**
is the outbound application-to-PSTN direction. No outbound trunk or REGISTER
flow is required merely to answer this inbound lab number.
[Sources: Twilio trunking](https://www.twilio.com/docs/sip-trunking),
[LiveKit Twilio setup](https://docs.livekit.io/telephony/start/providers/twilio/)

Telnyx is another option, but the current LiveKit guide requires a paid Telnyx
account. Its trial restrictions also limit calls to/from verified numbers; do
not promise that a trial supports this lab. [Sources: LiveKit Telnyx setup](https://docs.livekit.io/telephony/start/providers/telnyx/),
[Telnyx trial capabilities](https://developers.telnyx.com/docs/account-setup/levels-and-capabilities/trial)

## Telephone acceptance and evidence

Phone access is disabled unless PHONE_ACCESS_PIN is set to six to twelve ASCII
digits in the local/deployment environment. Keep that value out of source and
chat. On the call, enter it using the telephone keypad and press # to submit;
* clears the current entry. Three incorrect submissions or sixty seconds lock
authentication for that call. The application handles these DTMF events directly
from the actual SIP participant and does not pass the PIN to the LLM transcript.
Do not speak the PIN aloud.

The telephone tool set must enforce read-only access in application code.
PSTN callers must not enable tests, apply patches, invoke general shell commands,
or unlock writes using a spoken PIN. Test this even if the instructions already
tell the model to refuse.

1. Confirm a SIP participant and the selected agent join an isolated room. Verify
   repository tools are unavailable before keypad authentication.
2. Authenticate with the keypad, then ask about a sample function, search its
   references, and verify its answer.
3. Attempt a test/write request and verify the actual tool boundary rejects it.
4. Try a clear correction and a brief acknowledgment during agent speech.
5. End the call and privately correlate provider/SIP/LiveKit IDs and local traces.

For an intentionally reproduced fault, follow SIP_DEBUGGING_RUNBOOK.md. Use
actual call evidence; managed SIP traffic is not visible to laptop Wireshark
unless it passes through that laptop.

Record each actual call's result separately: authenticated read-only question,
write/test rejection, isolation, and a controlled fault followed by restoration.
See [validation](VALIDATION.md) for observed browser evidence and unqualified
telephone behavior rather than treating this reusable procedure as a test log.

Verify current provider documentation and account entitlements before
provisioning or changing a plan.
