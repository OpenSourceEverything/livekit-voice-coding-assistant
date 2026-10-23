# Background change requests

A call submits a persistent job and returns its number. A separately started
local process can prepare a proposal after hang-up. A later authenticated call
can retrieve status. Submission does not apply source, run repository tests,
create a PR, deploy, text, or call anyone back.

## Enable explicitly on the local worker

Set these in the private `.env.local` and restart the voice worker:

```dotenv
ENABLE_BACKGROUND_JOBS=true
ENABLE_JOB_CODEX=true
JOBS_DATABASE=var/jobs.sqlite3
```

Start the separate worker from the same checkout/environment:

```bash
make job-worker
```

It processes at most **one** queued job per invocation, then exits. Start another
invocation deliberately for more work. This bounds model usage; it is not an
always-on production service. Saved jobs remain if either process exits.

On a call, authenticate, request one small change, and wait for the actual saved
job number before hanging up. The assistant first reads the file and submits
its actual SHA-256 plus at most 200 lines/32,000 bytes. The model receives only
that selected excerpt, not a whole large UI file or private deployment config.
Do not include credentials in source or requests. Cloud mode cannot use jobs.

The separate worker uses the existing Codex CLI login and the bounded read-only
adapter. It does not inherit LiveKit/provider environment secrets. Read-only
execution is **not** a complete host-file confidentiality sandbox; see
[adapter limits](CODEX_BRIDGE.md). Do not expose this owner/PIN workflow to
untrusted callers. Source can be sent to model providers; choose it deliberately.

## Status and review

```bash
.tools/bin/uv run python tools/jobs.py status 1
```

Or call back, authenticate and ask "What is the status of job one?" States:
queued → running → review_ready / explained / failed. An interrupted worker's
running job becomes interrupted on the next worker start; it is not automatically
replayed because an interrupted model request might already have consumed usage.

The database is owner-only, uses SQLite transactions, and commits before the
voice acknowledgment. Identical request/source/excerpt submissions return the
same job number. There are at most three queued/running jobs and 1,000 total
records. A process lock prevents two CLI workers owning the queue. This is a
single-owner local design, not a multi-tenant authorization service.

The worker checks the whole-file source hash before **and after** model work,
validates returned paths/hash/schema, reconstructs only the selected excerpt,
and stages a review artifact. Existing source remains unchanged. The optional
512,000-byte proposal limit handles large UI modules; ordinary staging retains
its 128,000-byte default. Inspect the exact artifact locally:

```bash
.tools/bin/uv run python tools/review_change.py show PROPOSAL_ID --repo SELECTED_REPO --max-file-bytes 512000
```

Do not apply it until reviewed. A prepared proposal is not tested source or a
deployment. A separate exact-revision approval, isolated PR/test workspace,
deployment profile, backup, health check and rollback are still required for
a real installation. A public repo checkout does not grant recorder SSH access.

## Reproduce safely

`make check` exercises fake-model durability, idempotency, single claims,
interrupted-work recovery, source drift, path/range refusal, private database
permissions and phone/Cloud guards. `make acceptance` remains an offline fixture.
`make jobs-probe` is different: **one actual Codex model call** against a tiny
synthetic line, returning explanation only. It never reads the selected VMS repo.

The implementation follows the official
[Codex non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode)
for stdin, schema output and saved authentication. Tests retain independent
validation of model output; schema generation alone is not authorization.
