# Optional local Codex patch proposals

`src/codex_bridge.py` implements an optional synchronous Codex CLI adapter. It is
disabled by default. The voice application enables it only for an authorized
local browser session. Telephone callers never receive this capability.

```python
from codex_bridge import CodexBridge, CodexBridgeError

bridge = CodexBridge(enabled=True, timeout_seconds=90)
result = bridge.propose(
    "Explain this function and propose a small change",
    [{"path": "src/example.py", "content": original, "sha256": original_hash}],
)
```

Call it with `await asyncio.to_thread(bridge.propose, request, files)` from async
code so the subprocess does not block the audio event loop. The returned object
contains `summary` and `edits`; each edit has `path`, `original_sha256`, and the
full replacement `new_content`. Only supplied paths are accepted. Current file
hashes must be checked again by the separate local staging/apply service.
This adapter does not apply edits, commit files, run repository tests, or expose
shell commands as voice tools.

## Local prerequisites and execution

Install and sign into the Codex CLI locally with your existing account. No new
API key is required by this adapter. Saved authentication is resolved through
the existing `HOME`/`CODEX_HOME` location. Account access, allowance, and provider
availability remain dependent on that local login. LiveKit/model-provider
credentials are not copied into the child process environment.

Each invocation copies only the explicitly supplied text snapshots into a new
temporary working directory. It launches a fixed argument list with no shell:

```text
codex exec --ignore-user-config --ignore-rules --sandbox read-only --ephemeral --skip-git-repo-check --color never --output-schema <temporary-schema> -
```

The prompt arrives on standard input; it is not placed in shell arguments.
`--ignore-user-config` prevents loading personal configuration and configured
integrations. `--ignore-rules` skips local execution-policy rule files. The
temporary directory is removed after completion. There are no dangerous bypass,
workspace-write, or extra writable-directory flags.

Source paths must be relative and cannot traverse outside the snapshot or name
hidden/private directories, credential/password/secret files, `.env` files, or
private-key containers. SHA-256 must match the provided source content.
Known private-key/API-token patterns are rejected. These heuristics do not prove
that arbitrary source text has no secrets: review/filter the selected files
before enabling transmission. The request and selected source are sent to the
model provider by the CLI when this opt-in adapter is used.

The adapter caps files, source/input size, returned output, and runtime. It drops
raw stderr so CLI traces cannot leak into spoken responses. Errors use
`CodexBridgeError.code`: `disabled`, `input_invalid`, `codex_missing`, `timeout`,
`output_limit`, `codex_failed`, or `invalid_output`. Returned JSON is validated
independently even though the model also receives a response schema.

## Limits of isolation

The CLI's read-only sandbox prevents ordinary workspace writes; it is not a
host-file confidentiality boundary. A temporary snapshot, a no-tools prompt,
and ignored configuration do not prove that the model cannot read other files
available to the local account. This implementation does not claim to disable
every CLI tool. An explicit local opt-in is required; for stronger isolation run
the CLI in a separately provisioned container/account containing only reviewed
snapshots and its own scoped authentication. Do not expose the adapter over SIP,
to untrusted remote users, or to an unauthenticated network endpoint.

## Verification status

Only fake Codex subprocesses are exercised by automated tests: argv/stdin,
environment filtering, temporary snapshots, hash/path validation, output limits,
timeouts, nonzero exits, and structured-response validation. No real model call,
CLI login, external submission, or source upload was performed during this
implementation. Verify a real opt-in planning request locally after account
setup, then inspect the returned proposal before staging/applying anything.

Official OpenAI documentation, checked October 22, 2026:

- [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [CLI developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli)
