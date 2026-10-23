# LiveKit Voice Coding Assistant

Ask questions about a local Git repository and request reviewable changes by
voice, from a browser or an authenticated phone call. Select the checkout with
`REPO_ROOT`; the same assistant works across supported source languages without
adding application-specific tools. Each worker uses one selected repository.

The aim is to keep development accessible while away from the keyboard:
understand code, locate a problem, and leave a concrete proposal to review.
Optional persistent jobs let a separate worker prepare a proposal after the
conversation ends.

## How it works

```text
Browser / phone → LiveKit → STT → LLM + checked repository tools → TTS
                                          |
                                selected local Git checkout
                                          |
                              saved job / proposed diff
                                          |
                               human review → local apply
```

Python 3.12 and LiveKit Agents run on the computer holding the checkout. The
default pipeline uses AssemblyAI speech recognition, Gemma 4, Fish Audio speech
output, Silero voice activity detection, and LiveKit turn detection. A browser
uses LiveKit's Agent Console; a phone uses an optional inbound SIP route.
Models and the source checkout are configurable.

## Implementation

Built on LiveKit's MIT-licensed [official Python agent starter](https://github.com/livekit-examples/agent-starter-python)
and [Voice AI quickstart](https://docs.livekit.io/agents/start/voice-ai/).
The starter supplies the agent foundation. This project adds:

- Bounded source search/read and Git inspection, with checked paths and output
  limits in Python, independent of model instructions.
- Staged changes bound to the original source hash, with a separate local
  review/apply command that rejects stale source.
- Keypad authentication for incoming calls, plus transport-specific tool
  permissions and an opt-in phone proposal mode.
- Local VAD barge-in and transcript-based Stop handling, including repeated
  Stop commands, with numeric timing and call-lifecycle diagnostics.
- SQLite background jobs and an optional local proposal worker, so a request
  can survive the conversation that submitted it.
- Recorded token/speech usage reports and a locked, repeatable Python setup.

## Demonstrated results

The local suite passes 245 tests and 15 offline acceptance scenarios. Real
provider sessions exercised voice and repository tools. An actual incoming-call
startup failure was traced to plugin registration on a background thread; after
the correction, an incoming call completed. A later phone test confirmed Stop
behavior after fixing merged repeated commands and using local VAD to avoid
adaptive-classifier quota failures.

Detached proposal preparation has been exercised against synthetic source.
This is a recent working prototype; [validation and evidence](docs/VALIDATION.md)
records the checks, observed outcomes, and measurement limits. Automatic PR
creation, phone-triggered deployment/merge, SMS, and callbacks are future work.

## Get started

On Linux, install Git, ripgrep, curl and Make, then:

```bash
git clone https://github.com/OpenSourceEverything/livekit-voice-coding-assistant.git
cd livekit-voice-coding-assistant
make setup
make check
make acceptance
make status
```

Setup installs checksum-checked tools locally and uses `uv.lock` to create
`.venv`. No global pip install, manual venv activation, or duplicate
`requirements.txt` is needed. The checks above use synthetic source and make
no speech/model calls. Follow the [setup guide](docs/SETUP.md) to connect an
existing LiveKit project and start browser or phone audio.

To use another existing checkout, select it before starting the worker:

```bash
export REPO_ROOT="/absolute/path/to/your/repo"
make status
make dev PROJECT=YOUR_LIVEKIT_PROJECT_ALIAS
```

Ask: "Find the main entry point and explain it."

Use a checkout suitable for sharing excerpts with the configured model
providers. Source tools support common tracked UTF-8 files, including Python,
C/C++, JavaScript/TypeScript, Rust, Go, Java/Kotlin, Swift and documentation.
They need no target build environment for reading and search. Switch repositories
by changing the path and restarting the worker; this is not a concurrent
multi-repository service. Test commands and deployment need an explicit recipe
for the target application's build/runtime. The default optional test alias
runs pytest; it is not a universal build adapter.

## Operating and extending it

- [Setup and everyday operation](docs/SETUP.md): credentials, selecting a repo,
  browser/phone use, review/apply, and how the project was assembled.
- [Background proposal jobs](docs/BACKGROUND_JOBS.md): submit, hang up, run a
  separate worker, and retrieve a saved result.
- [Call diagnostics](docs/CALL_DIAGNOSTICS.md) and [telephone troubleshooting](docs/SIP_DEBUGGING_RUNBOOK.md):
  call evidence, the startup fix, and repeatable interruption checks.
- [Latency and interruptions](docs/LATENCY_AND_INTERRUPTION.md) and [billing](docs/BILLING.md):
  component timing, usage counters, and account billing boundaries.
- [Code map](docs/CODE_GUIDE.md) and [takeover walkthrough](docs/CODEBASE_TAKEOVER.md):
  module boundaries, tests, and extension points.

The Docker Cloud demo contains only a synthetic checkout. Real repository work
uses the local worker. Inbound phone access requires a locally configured PIN;
phone proposals and background jobs require explicit local opt-in. Applying a
proposal remains a separate human-reviewed local action.
