# LiveKit Voice Coding Assistant

Talk to a LiveKit voice agent about a configured source repository. Find symbols,
read bounded excerpts, inspect Git, run locally enabled test aliases, and stage
changes for review. The local CLI applies a reviewed diff. Telephone sessions
are authenticated with keypad digits and default to read-only. An explicit
local opt-in permits phone proposals, never phone apply/push/deployment.

Built on the MIT-licensed
[official Python starter](https://github.com/livekit-examples/agent-starter-python).
That starter supplied the initial agent/project structure; the repository tools,
approval checks, keypad authentication, usage reporting and saved-job workflow
were added here. Setup follows the
[official LiveKit Voice AI quickstart](https://docs.livekit.io/agents/start/voice-ai/)
and SDK documentation. The project uses uv to create a repeatable locked Python
environment; it is a project setup choice, not a LiveKit Agents SDK requirement.
See [validation](docs/VALIDATION.md) for reproducible checks and the scope of
observed voice and telephone behavior. See [the code map and hygiene guide](docs/CODE_GUIDE.md)
for module ownership and what the tests do and do not prove.

For the bounded phone interruption retest, use the
[call checklist](docs/CALL_DIAGNOSTICS.md#stop-behavior-and-verification).
Local VAD is now the default: no adaptive-classifier quota dependency, but
background speech/acknowledgments can also interrupt. `make status` shows the
configured mode; `make call-report` shows the latest recorded call's mode and
stop events. Neither command proves the caller heard silence.

## Quick operating guide — start here

This repository is the **assistant**, not the software it edits. A local Python
worker connects a browser microphone or an authenticated incoming call to one
chosen Git checkout. Cloud services handle speech/model inference. The normal
workflow is ask → inspect source → save a proposal → separately review/apply.
An optional background worker can prepare that proposal after a conversation
ends; automatic PR creation and home deployment are not implemented yet.

### First setup on another Linux computer

1. Install Git, ripgrep, curl and Make using your OS package manager. On Debian/
   Ubuntu, the prerequisite command is shown under Local setup below.
2. Clone this assistant only if you do not already have a checkout:

   ```bash
   git clone https://github.com/OpenSourceEverything/livekit-voice-coding-assistant.git
   cd livekit-voice-coding-assistant
   make setup
   make check
   make acceptance
   make status
   ```

   Setup downloads checksum-checked tools locally, creates .venv from the locked
   dependencies, initializes a synthetic Git fixture, and checks offline
   prerequisites. The other commands test/check; they do not make calls or
   deploy anything. No manual virtualenv activation or requirements.txt needed.
3. For real voice, follow Local setup below to log into the intended existing
   LiveKit project and write the ignored .env.local. Keep its permissions 600.
   Inspect configuration with make status; never paste .env.local contents.

### Use an existing repository or add another one

No copying into the assistant, special Git initialization or target-repo venv
is needed for source search/read. Choose an existing local Git checkout suitable
for sending excerpts to model providers. If it is only on GitHub, clone it into
an explicitly chosen folder first; do not clone over existing work.

In the terminal that will run the assistant:

```bash
export REPO_ROOT="/absolute/path/to/your/existing/repo"
make status
make dev PROJECT=YOUR_PROJECT_ALIAS
```

The quoted absolute path avoids ambiguity. This selection lasts in that shell;
it does not edit the target repo or private environment file. For a persistent
choice, set just REPO_ROOT in .env.local. Restart a running worker after changing
it. Shell environment overrides .env.local, so check make status in that same
terminal before starting. Do not run target setup/install/test commands merely
to let the assistant explain source. Any target test environment is a separate,
repo-specific setup; tests can execute that repository's code.

Open the intended LiveKit project's Agent Console, choose the local worker and
its printed development deployment, allow the microphone, and ask:
"Find the main entry point and explain it. Do not change anything."
Use Ctrl+C in the worker terminal to stop. The hosted Cloud demo cannot inspect
this local checkout. For an already configured phone rule, retain its exact
development deployment rather than blindly using the random make dev default;
see [SIP setup](docs/SIP_SETUP.md). Do not buy a second number or upgrade a plan
as a troubleshooting step.

### Request an edit and verify it

Ask for one small proposal, inspect its actual saved diff, and use the separate
local approval/apply command under Changes and tests below. Staging does not
edit source, run tests, push, open a PR or deploy. For saved requests that outlive
the call, follow [background jobs](docs/BACKGROUND_JOBS.md): jobs are default-off,
require a separate worker and return a real job number before hang-up. There is
no automatic text/callback. Use make status before/after for local usage; remaining
account credit is unknown, not a guessed dollar balance.

### How this repository was assembled

1. Started with the official Python agent starter; retained its MIT attribution.
2. Declared Python/dependencies in pyproject.toml and pinned them in uv.lock;
   bootstrap and Make targets make setup repeatable without global pip installs.
3. Added bounded Git/source tools, opt-in fixed tests, staged diffs and separate
   source-hash-bound approval. Model instructions are not the security boundary.
4. Connected STT → LLM/tools → TTS, then added numeric usage/latency reporting.
5. Added keypad-gated incoming telephony; investigated actual startup failures
   rather than deliberately breaking the call. Added regression tests for them.
6. Added SQLite saved jobs and a separately bounded Codex proposal worker;
   validated shutdown/retrieval using synthetic source without applying it.
7. Added read-only home/public baseline checks. PR/deployment integration remains
   a separate gate, not a feature implied by a configured GitHub login.

See [the code walkthrough](docs/CODEBASE_TAKEOVER.md) for module ownership,
[observed validation](docs/VALIDATION.md) for actual results and gaps, and the
runbooks below for deeper details. Renaming this checkout requires rebuilding
.venv with the locked dependencies because executable paths can be absolute.

## Architecture

```text
Browser microphone → LiveKit → speech recognition → language model → speech
                                                       |
                                             checked Python tools
                                                       |
                                          source / Git / staged diff
```

Python 3.12 and LiveKit Agents form the worker. The default model pipeline uses
AssemblyAI for transcription, Gemma 4 for reasoning/tool calls, and Fish Audio
for speech, with Silero ONNX voice activity detection. Models are configurable.
The normal desktop workflow needs no telephone number. Optional inbound SIP
provides authenticated access through the same repository tools. There is no
custom website or IDE extension: use LiveKit's browser Agent Console for audio,
and keep the Python worker running on the computer holding the source checkout.

Optional [background proposal jobs](docs/BACKGROUND_JOBS.md) persist a small
request before hang-up. A separate local worker prepares a review artifact and
a later authenticated call retrieves status. This is not automatic PR creation,
phone approval, deployment, callback or SMS; those remain separate integrations.
For an existing home installation, [deployment preparation](docs/HOME_DEPLOYMENT.md)
provides a read-only baseline check and the remaining approval gates.

## Local setup

Linux x86_64 or aarch64 is supported by the bootstrap. Install Git, ripgrep,
curl, and Make first (on Debian/Ubuntu: `sudo apt-get install git ripgrep curl make`).
From the checkout root, start without any account:

```bash
make setup
make check
make acceptance
make status
```

`make setup` installs pinned tools into `.tools/bin` and runs `uv sync --locked`.
uv creates the ignored `.venv` here, uses Python 3.12 from `.python-version`, and
downloads that Python version if it is missing. `pyproject.toml` declares the
dependencies; `uv.lock` records exact resolved versions. Rerunning setup reuses
matching tools and the locked environment. It does not install Python packages
globally, upgrade the lockfile, or need a duplicate `requirements.txt`.
[uv project guide](https://docs.astral.sh/uv/guides/projects/),
[Python installation](https://docs.astral.sh/uv/guides/install-python/).

The Make commands select `.venv` automatically; activation is optional. For an
interactive shell, use `source .venv/bin/activate` and later `deactivate`.

`make status` is the one local entry point: selected workspace, configuration
readiness, phone capability, recorded usage, and the direct billing link when
LIVEKIT_PROJECT_ID is configured privately in `.env.local`. It makes no network
requests. It does not claim the worker or a telephone route is live.

For live browser audio, connect the intended LiveKit project:

```bash
export PATH="$PWD/.tools/bin:$PATH"
lk cloud auth
lk --project YOUR_PROJECT_ALIAS app env --write --destination .env.local
chmod 600 .env.local
make demo
make doctor
make dev PROJECT=YOUR_PROJECT_ALIAS
```

Reuse an existing project where available; these steps do not create one.
Replace YOUR_PROJECT_ALIAS with the alias shown by `lk project list`.
Open that project in https://cloud.livekit.io/ and launch Agent Console.
Select the local worker's development deployment.
The Python project name in `pyproject.toml` is
`livekit-voice-coding-assistant`. A Cloud display name, saved CLI project alias,
project subdomain, and agent dispatch name are separate identifiers.
For `agent dev`, select the development deployment printed in the terminal;
it is separate from the Cloud worker's production deployment.
Use the intended project and deployment to avoid routing a local source request
to a Cloud fixture. Grant microphone permission. Ask:
"Find where UART5 DMA transmission is implemented."

Keep `.env.local` and per-account deployment configuration private. Running an
agent can consume Cloud/model allowances; inspect current account limits first.

For optional GitHub PR work, `make github-setup` installs a pinned, checksum-checked
GitHub CLI locally. Authenticate separately with
`.tools/bin/gh auth login --hostname github.com --git-protocol ssh --web --skip-ssh-key`.
Do not paste tokens into chat. Installing the tool does not log in, upload SSH
keys, create a PR, or grant the phone worker publishing/deployment permission.
`make acceptance` is different: it runs only a disposable local fixture, exercises
the actual tool/review boundary, and makes no provider calls. It never changes
the configured workspace. It is not a voice or telephone qualification.

## Two execution locations

Local mode connects this computer to LiveKit and can access the exact repository
specified in REPO_ROOT. Default: the synthetic var/demo-repo. Set REPO_ROOT in
.env.local to a checkout suitable for sharing with the configured model.

The Docker Cloud deployment includes only the synthetic fixture. It cannot read
your computer's repositories. It sets COPILOT_MODE=cloud-demo, disables tests,
proposals, and Codex, and remains a demonstration of voice/search/Git tools.
Use the local worker for real source work; a remote-PC connector is future work.

## Changes and tests

ENABLE_LOCAL_TESTS=false by default. Enabling it permits only a fixed pytest
alias. Tests execute repository code with your OS file access; use trusted repos.
No voice tool accepts arbitrary shell commands.

ENABLE_PROPOSALS=true locally permits staged changes to existing allowed files.
Review a proposal in VS Code or the terminal:

```bash
.tools/bin/uv run python tools/review_change.py show PROPOSAL_ID --repo var/demo-repo
.tools/bin/uv run python tools/review_change.py apply PROPOSAL_ID --repo var/demo-repo --approve
```

The apply function is never registered as a LiveKit tool. Source hashes reject
stale overwrites. ENABLE_CODEX=false by default; see docs/CODEX_BRIDGE.md for the
optional saved-login Codex integration and its local permission limits.
Known secret paths are excluded before reading; this is not a complete content
secret scanner. Choose a repository you are allowed to send to model providers.

## Telephone and measurement

Phone access is disabled unless PHONE_ACCESS_PIN is configured. It accepts
six to twelve keypad digits followed by pound; the PIN is not sent to the LLM.
Every telephone tool checks its configured policy, even if called directly.

ENABLE_PHONE_PROPOSALS=false by default. When explicitly enabled on the local
worker, a keypad-authenticated caller may stage a diff while source remains
unchanged. A human still reviews and applies it using the separate local CLI.
Phone tests, Codex, apply, PR creation, push, merge, and deployment are unavailable.
This is supervised proposal initiation, not an end-to-end phone release system.

## Usage without dashboard digging

For debugging, `make call-report` summarizes safe RTC/SIP lifecycle events.
See [call diagnostics and the stop test](docs/CALL_DIAGNOSTICS.md): it explains
what is logged, what is deliberately excluded, and what still needs a human
listening test. These are not raw SIP packet traces or acoustic benchmarks.

Run `make status` before and after a session. `make usage` shows per-model detail;
`.tools/bin/uv run python tools/status.py --json` returns structured local status.
New sessions save private numeric snapshots to the ignored `var/usage.jsonl`.
Successive SDK updates replace previous cumulative totals; they are not summed.
Synthetic sessions are excluded unless `--include-synthetic` is requested.

The report covers sessions recorded by this worker, not historic or account-wide
usage. It shows tokens, speech characters/processed audio, and a partial estimate
for the configured Gemma/Fish models using prices checked on 2026-10-22. STT
connection billing, hosting, media, telephone, other workers, and account credits
are not included. Remaining monthly credit is explicitly unknown, never a
guessed subtraction from a plan allowance. No balance endpoint or dashboard
session token is used. See [SDK usage hooks](https://docs.livekit.io/testing/observability/data/)
and [billing meters](https://docs.livekit.io/deploy/admin/billing/). For a
plain-language explanation of the local estimate, see [billing and usage](docs/BILLING.md).

See docs/SIP_SETUP.md and docs/SIP_DEBUGGING_RUNBOOK.md for inbound setup,
Google Voice constraints, call isolation, and troubleshooting evidence.
See docs/LATENCY_AND_INTERRUPTION.md for numeric metrics and honest measurement
limits. Never publish raw source, transcripts, call identifiers, or credentials.

## Technical review

See [validation](docs/VALIDATION.md) for observed results and remaining gates,
the [code walkthrough](docs/CODEBASE_TAKEOVER.md) for ownership boundaries,
and [deployment options](docs/DEPLOYMENT.md) for worker versus full-stack hosting.
The implementation keeps repository permissions separate from model instructions.
Inspect the source and tests alongside the runbooks when reviewing these boundaries.

Primary LiveKit sources:
[Voice quickstart](https://docs.livekit.io/agents/start/voice-ai/),
[function tools](https://docs.livekit.io/agents/logic/tools/definition/),
[workflows](https://docs.livekit.io/agents/logic/workflows/),
[simulations](https://docs.livekit.io/testing/simulations/).
