# Code map and takeover walkthrough

src/agent.py configures the Cloud demo or local worker, binds the participant,
chooses transport policy, creates the model pipeline, installs telemetry and
starts Assistant. CLI fake jobs skip the remote-participant wait.

src/repository.py owns source boundaries, snapshots, bounded search, Git and
optional test execution. Tool parameters never become shell commands.
Filename filters run before reading; they are not a full content secret scan.

src/changes.py persists proposals and original source hashes.
tools/review_change.py owns local approval and apply. No voice apply tool exists.

src/codex_bridge.py optionally sends selected snapshots to a locally
authenticated Codex CLI and validates structured edit proposals. It is disabled
by default and unavailable by phone.

src/phone_auth.py checks keypad digits outside the LLM. src/telemetry.py records
only numeric measurements and tools/report_latency.py reports missing evidence.
src/usage.py separately replaces cumulative per-model SDK usage snapshots and
tools/status.py provides one local readiness/usage view. Account credit is not
queried or inferred from those counters. Phone proposals are a default-off
capability; the model still cannot approve, apply, push, or deploy a change.

Takeover method: map the boundaries, reproduce one failure, capture evidence,
form a distinguishable hypothesis, make a small change, run its regression, then
document rollback and remaining risks. docs/VALIDATION.md records this project's
actual native-VAD launch failure and investigation.
