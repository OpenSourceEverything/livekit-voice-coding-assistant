"""Read-only deployment inventory using only fake SSH/Git responses."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from vms_preflight import GUI, PreflightError, inventory, probe_home, summarize_package


def test_inventory_detects_drift_and_never_mutates(tmp_path):
    roots = [tmp_path / "primary", tmp_path / "pr"]
    for root, source in zip(
        roots, (b"installed source", b"public source"), strict=True
    ):
        path = root / GUI
        path.parent.mkdir(parents=True)
        path.write_bytes(source)
    calls = []

    def runner(command):
        calls.append(command)
        if command[0] == "ssh":
            digest = hashlib.sha256(b"installed source").hexdigest()
            return json.dumps({"gui_sha256": digest, "_modules": {"gui.py": digest}})
        operation = command[3]
        assert operation in {"ls-files", "rev-parse", "branch", "status"}
        return {
            "ls-files": GUI,
            "rev-parse": "a" * 40,
            "branch": "main",
            "status": " M gui.py" if command[2] == str(roots[0]) else "",
        }[operation]

    profile = {
        "primary_repo": str(roots[0]),
        "pr_repo": str(roots[1]),
        "ssh_host": "owner@recorder.local",
        "installed_gui": "/opt/video-monitoring-system/lib/python3.13/site-packages/video_monitoring_system/gui.py",
    }
    local = inventory(profile, runner=runner)
    assert not any(call[0] == "ssh" for call in calls)
    assert local["primary"]["dirty"]
    assert not local["pr_checkout"]["dirty"]
    result = inventory(profile, include_home=True, runner=runner)
    assert result["installed_matches_primary_gui"]
    assert not result["installed_matches_pr_gui"]
    assert not result["installed_matches_pr_python"]
    assert result["installed_matches_primary_python"]
    assert result["pr_python_differences"] == ["gui.py"]
    assert not result["deployment_ready"]
    command = calls[-1]
    assert "StrictHostKeyChecking=yes" in command
    assert "BatchMode=yes" in command
    assert command[-1].startswith("/opt/video-monitoring-system/bin/python -c ")
    assert "sudo" not in command[-1] and "restart" not in command[-1]
    assert (roots[1] / GUI).read_bytes() == b"public source"

    # Matching just gui.py must not hide drift in another installed module.
    (roots[1] / GUI).write_bytes(b"installed source")
    (roots[1] / GUI).with_name("application.py").write_text("different = True\n")
    result = inventory(profile, include_home=True, runner=runner)
    assert result["installed_matches_pr_gui"]
    assert not result["installed_matches_pr_python"]
    assert result["pr_python_differences"] == ["application.py"]
    (roots[1] / GUI).with_name("application.py").unlink()
    result = inventory(profile, include_home=True, runner=runner)
    assert result["installed_matches_pr_python"]
    assert result["pr_python_differences"] == []
    assert not result["deployment_ready"]
    assert result["home"]["python_module_count"] == 1
    assert "_modules" not in json.dumps(result)


@pytest.mark.parametrize(
    "manifest", [None, {}, {"../private.py": "0" * 64}, {"gui.py": "invalid"}]
)
def test_invalid_package_manifests_refused(manifest):
    with pytest.raises(PreflightError):
        summarize_package(manifest)


@pytest.mark.parametrize(
    "host,path",
    [
        ("-oProxyCommand=bad", "/opt/video-monitoring-system/gui.py"),
        ("owner@recorder", "/etc/passwd"),
        ("owner@recorder", "/opt/video-monitoring-system/../gui.py"),
    ],
)
def test_unsafe_destinations_refused_without_ssh(host, path):
    def never(_):
        pytest.fail("Invalid destination must not invoke SSH")

    with pytest.raises(PreflightError):
        probe_home(host, path, runner=never)
