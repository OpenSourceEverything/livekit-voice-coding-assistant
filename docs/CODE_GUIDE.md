# Code map and hygiene guide

Start with `src/agent.py` for the running LiveKit application. The surrounding
modules separate policy and persistence from the voice lifecycle:

```text
LiveKit session
  ├─ agent.py: transport, model pipeline, tools, lifecycle wiring
  ├─ interruption.py: exact final Stop-command detection
  ├─ phone_auth.py: keypad authentication outside the model
  ├─ repository.py: bounded read/search/Git operations
  ├─ changes.py: hash-bound review artifacts and local apply boundary
  ├─ jobs.py: durable queued proposal lifecycle
  ├─ codex_bridge.py: optional local proposal subprocess and result validation
  ├─ telemetry.py: numeric latency counters
  ├─ call_diagnostics.py: allowlisted RTC/SIP lifecycle events
  ├─ usage.py: local SDK usage ledger and partial price estimate
  └─ settings.py: validated environment configuration
```

`tools/` contains command-line entry points and reports. `tests/` checks local
contracts with temporary repositories, fake SDK events, or bounded fake child
processes. The fake fixtures make security and failure cases repeatable; they
do not simulate a carrier, caller audio, a real Codex model, or an invoice.

## Tests by responsibility

- `test_agent.py`: tool registration and policy boundaries by transport/auth.
- `test_repository.py`: checkout containment, bounded reads, secrets, subprocess
  timeouts, Git environment, and source snapshot integrity.
- `test_changes.py`: staged diff integrity, stale-source protection, and explicit
  local approval before apply.
- `test_jobs.py`: durable queue acknowledgments, deduplication, worker races,
  source drift, and no automatic replay after interruption.
- `test_codex_bridge.py`: safe subprocess invocation and validation of untrusted
  proposal output; its CLI is fake, so this is not model-quality evidence.
- `test_phone_auth.py`: per-call keypad limits, deadlines, and no digit output.
- `test_interruption.py`: exact/merged Stop matching, negative phrases, SDK
  interrupt request handling, silent Stop response, next-turn recovery, and
  explicit VAD/adaptive session settings. Unit tests are not acoustic tests.
- `test_call_diagnostics.py`, `test_telemetry.py`, and `test_usage.py`: numeric
  allowlists, privacy, bounded storage, SDK event adapters, report math, and
  missing-data behavior. None contacts billing or proves caller-heard timing.
- `test_voice_startup.py`: installed plugin registration thread-affinity and
  non-blocking model load using controlled fakes.
- `test_status.py`: safe local readiness and unknown-vs-zero accounting.
- `test_acceptance_demo.py`: offline end-to-end checks in a disposable checkout.
- `test_vms_preflight.py`: read-only deployment inventory using fake SSH/Git.

## Modularity review and known coupling

Core modules do not import CLI modules or tests, and their internal import graph
is acyclic. An architecture regression test protects those two boundaries.
`agent.py` is still the largest composition module because it registers SDK
tools and wires many session events; keep business rules in the focused modules
listed above when adding features. `jobs.py` needs the same proposal validator
used by `codex_bridge.py`, so that validator is a deliberate public boundary.
Process execution is intentionally separate: repository commands are local
test/Git operations, while the Codex adapter is a distinct proposal process
with its own enablement and output-validation contract.

Run the standard checks with `make check`. The format, lint, and offline test
commands are pinned through the repository's uv environment. For actual voice
or telephony claims, see `docs/VALIDATION.md` and `docs/CALL_DIAGNOSTICS.md`;
offline tests do not substitute for a call.
