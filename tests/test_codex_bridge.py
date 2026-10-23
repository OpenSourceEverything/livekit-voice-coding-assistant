"""Fake CLI processes only: no real Codex model or network requests occur."""

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

import codex_bridge
from codex_bridge import CodexBridge, CodexBridgeError


def snapshot(path="src/example.py", content="value = 1\n"):
    return {
        "path": path,
        "content": content,
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
    }


def fake_cli(monkeypatch, tmp_path, body):
    executable = tmp_path / "fake-codex"
    executable.write_text(f"#!{sys.executable}\n" + body)
    executable.chmod(0o700)
    monkeypatch.setattr(codex_bridge.shutil, "which", lambda name: str(executable))
    return executable


def test_disabled_by_default_does_not_invoke_cli(monkeypatch):
    def unexpected_cli(_name):
        pytest.fail("Disabled bridge must not locate or invoke Codex")

    monkeypatch.setattr(codex_bridge.shutil, "which", unexpected_cli)
    with pytest.raises(CodexBridgeError) as caught:
        CodexBridge().propose("Explain the file", [snapshot()])
    assert caught.value.code == "disabled"


def test_fixed_argv_filtered_stdin_env_and_no_original_file_mutation(
    monkeypatch, tmp_path
):
    captured = tmp_path / "capture.json"
    marker = tmp_path / "shell-injection-marker"
    original = tmp_path / "original.py"
    original.write_text("value = 1\n")
    body = (
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "prompt = sys.stdin.read()\n"
        "payload = json.loads(prompt.split('\\n', 1)[1])\n"
        f"Path({str(captured)!r}).write_text(json.dumps({{'argv': sys.argv[1:], "
        "'prompt': prompt, 'env': dict(os.environ), 'cwd': str(Path.cwd()), "
        "'snapshot': {str(p.relative_to(Path.cwd())): p.read_text() "
        "for p in Path.cwd().rglob('*') if p.is_file()}}))\n"
        "source = payload['files'][0]\n"
        "print(json.dumps({'summary': 'Prepared a patch', 'edits': ["
        "{'path': source['path'], 'original_sha256': source['sha256'], "
        "'new_content': 'value = 2\\n'}]}))\n"
    )
    fake_cli(monkeypatch, tmp_path, body)
    monkeypatch.setenv("LIVEKIT_API_KEY", "do-not-forward-livekit")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "do-not-forward-livekit-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-forward-provider")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "saved-login-location"))
    request = f"Change the value; literal input: $(touch {marker})"
    result = CodexBridge(enabled=True).propose(request, [snapshot()])
    capture = json.loads(captured.read_text())
    arguments = capture["argv"]
    assert arguments[:10] == [
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--skip-git-repo-check",
        "--color",
        "never",
        "--output-schema",
    ]
    assert arguments[-1] == "-"
    assert "--dangerously-bypass-approvals-and-sandbox" not in arguments
    assert request in capture["prompt"]
    assert capture["snapshot"] == {"src/example.py": "value = 1\n"}
    assert "LIVEKIT_API_KEY" not in capture["env"]
    assert "LIVEKIT_API_SECRET" not in capture["env"]
    assert "OPENAI_API_KEY" not in capture["env"]
    assert capture["env"]["CODEX_HOME"] == str(tmp_path / "saved-login-location")
    assert not Path(capture["cwd"]).exists()  # temporary snapshot removed
    assert not marker.exists()
    assert original.read_text() == "value = 1\n"
    assert result["edits"][0]["new_content"] == "value = 2\n"


@pytest.mark.parametrize(
    "path",
    [
        "../outside.py",
        "/etc/passwd",
        "src/../../private.py",
        ".private/credentials.txt",
        ".env.local",
        "src/.env",
        "credentials.json",
        "src/password.txt",
        "src/key.pem",
        "src\\outside.py",
        "src/new\nline.py",
    ],
)
def test_sensitive_or_traversing_snapshot_paths_rejected(path):
    with pytest.raises(CodexBridgeError) as caught:
        CodexBridge(enabled=True).propose("Explain this", [snapshot(path=path)])
    assert caught.value.code == "input_invalid"


def test_hash_mismatch_and_known_secrets_rejected():
    source = snapshot()
    source["sha256"] = "0" * 64
    with pytest.raises(CodexBridgeError, match="hash"):
        CodexBridge(enabled=True).propose("Explain this", [source])
    with pytest.raises(CodexBridgeError, match="sensitive"):
        CodexBridge(enabled=True).propose(
            "Explain this", [snapshot(content="-----BEGIN PRIVATE KEY-----")]
        )


def test_file_directory_collision_and_invalid_unicode_are_sanitized():
    with pytest.raises(CodexBridgeError) as caught:
        CodexBridge(enabled=True).propose("Explain", [snapshot("a"), snapshot("a/b")])
    assert caught.value.code == "input_invalid"
    with pytest.raises(CodexBridgeError) as caught:
        CodexBridge(enabled=True).propose("bad surrogate \ud800", [snapshot()])
    assert caught.value.code == "input_invalid"


def test_timeout_stops_fake_cli(monkeypatch, tmp_path):
    fake_cli(monkeypatch, tmp_path, "import time\ntime.sleep(10)\n")
    with pytest.raises(CodexBridgeError) as caught:
        CodexBridge(enabled=True, timeout_seconds=0.05).propose("Explain", [snapshot()])
    assert caught.value.code == "timeout"


def test_nonzero_cli_error_suppresses_raw_stderr(monkeypatch, tmp_path):
    fake_cli(
        monkeypatch,
        tmp_path,
        "import sys\nsys.stderr.write('private traceback and source')\nsys.exit(7)\n",
    )
    with pytest.raises(CodexBridgeError) as caught:
        CodexBridge(enabled=True).propose("Explain", [snapshot()])
    assert caught.value.code == "codex_failed"
    assert "private" not in str(caught.value)
    assert "traceback" not in str(caught.value)


def test_output_bound_stops_fake_cli(monkeypatch, tmp_path):
    fake_cli(
        monkeypatch,
        tmp_path,
        f"import sys\nsys.stdout.write('x' * {codex_bridge.MAX_OUTPUT_BYTES + 1024})\n",
    )
    with pytest.raises(CodexBridgeError) as caught:
        CodexBridge(enabled=True).propose("Explain", [snapshot()])
    assert caught.value.code == "output_limit"


@pytest.mark.parametrize(
    "response",
    [
        "not JSON",
        json.dumps({"summary": "Fine", "edits": [], "unexpected": True}),
        json.dumps({"summary": "Fine", "edits": [{"path": "../../private"}]}),
        json.dumps(
            {
                "summary": "Fine",
                "edits": [
                    {
                        "path": "src/example.py",
                        "original_sha256": "0" * 64,
                        "new_content": "value = 2\n",
                    }
                ],
            }
        ),
    ],
)
def test_untrusted_model_output_validated(monkeypatch, tmp_path, response):
    fake_cli(monkeypatch, tmp_path, f"print({response!r})\n")
    with pytest.raises(CodexBridgeError) as caught:
        CodexBridge(enabled=True).propose("Explain", [snapshot()])
    assert caught.value.code == "invalid_output"


def test_explanation_only_empty_edits_is_valid(monkeypatch, tmp_path):
    response = {"summary": "The file sets a constant.", "edits": []}
    fake_cli(monkeypatch, tmp_path, f"print({json.dumps(response)!r})\n")
    assert CodexBridge(enabled=True).propose("Explain", [snapshot()]) == response


def test_no_cli_has_actionable_sanitized_error(monkeypatch):
    monkeypatch.setattr(codex_bridge.shutil, "which", lambda name: None)
    with pytest.raises(CodexBridgeError) as caught:
        CodexBridge(enabled=True).propose("Explain", [snapshot()])
    assert caught.value.code == "codex_missing"


def test_environment_allowlist_does_not_copy_arbitrary_keys(monkeypatch):
    monkeypatch.setenv("ANOTHER_CUSTOM_PASSWORD", "do-not-copy")
    environment = codex_bridge._child_environment()
    assert "ANOTHER_CUSTOM_PASSWORD" not in environment
    assert set(environment).issubset(
        {"PATH", "HOME", "CODEX_HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "NO_COLOR"}
    )
    if "HOME" in os.environ:
        assert environment["HOME"] == os.environ["HOME"]
