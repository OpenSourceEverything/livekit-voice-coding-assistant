#!/usr/bin/env python3
"""Read-only VMS inventory. Never install, patch, restart or approve anything."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI = "src/video_monitoring_system/gui.py"


class PreflightError(ValueError):
    pass


def package_manifest(root):
    root = Path(root)
    paths = sorted(root.rglob("*.py"))
    if not paths or any(path.is_symlink() for path in paths):
        raise PreflightError("Expected a regular Python source package.")
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
        if path.is_file()
    }


def summarize_package(manifest):
    if (
        not isinstance(manifest, dict)
        or not manifest
        or len(manifest) > 1000
        or any(
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9_/]+\.py", name)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            for name, digest in manifest.items()
        )
    ):
        raise PreflightError("Invalid Python package fingerprint manifest.")
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return {
        "python_module_count": len(manifest),
        "python_package_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def run(arguments):
    result = subprocess.run(arguments, capture_output=True, timeout=25, check=False)
    if result.returncode or len(result.stdout) > 128_000:
        raise PreflightError("Read-only inventory failed; no changes were attempted.")
    return result.stdout.decode("utf-8")


def checkout(path, *, runner=run):
    path = Path(path).resolve(strict=True)
    source = path / GUI
    if source.is_symlink() or not source.is_file():
        raise PreflightError("Expected a regular tracked VMS GUI module.")
    prefix = ["git", "-C", str(path)]
    runner([*prefix, "ls-files", "--error-unmatch", GUI])
    return {
        "path": str(path),
        "commit": runner([*prefix, "rev-parse", "HEAD"]).strip(),
        "branch": runner([*prefix, "branch", "--show-current"]).strip(),
        "dirty": bool(runner([*prefix, "status", "--porcelain"])),
        "gui_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "_modules": package_manifest(source.parent),
    }


def probe_home(host, installed_gui, *, runner=run):
    # A private, operator-controlled profile chooses the pinned SSH destination.
    # Neither voice nor model output supplies these arguments.
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", host):
        raise PreflightError("Invalid SSH destination.")
    path = Path(installed_gui)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or not str(path).startswith("/opt/video-monitoring-system/")
        or path.name != "gui.py"
    ):
        raise PreflightError("Installed GUI must be beneath the VMS installation.")
    program = "\n".join(
        (
            "import hashlib, importlib.metadata, json, pathlib, subprocess",
            f"p = pathlib.Path({str(path)!r})",
            "assert p.is_file() and not p.is_symlink()",
            "services = {}",
            "for name in ('vms-recorder', 'vms-frigate'):",
            "    r = subprocess.run(['systemctl', 'show', name, '-p', 'ActiveState', '-p', 'ExecMainStartTimestamp'], capture_output=True, text=True, check=True, timeout=5)",
            "    services[name] = dict(line.split('=', 1) for line in r.stdout.splitlines() if '=' in line)",
            "modules = {f.relative_to(p.parent).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(p.parent.rglob('*.py')) if f.is_file() and not f.is_symlink()}",
            "result = {'package_version': importlib.metadata.version('video-monitoring-system'), 'gui_sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'services': services, '_modules': modules}",
            "print(json.dumps(result))",
        )
    )
    result = json.loads(
        runner(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ConnectTimeout=7",
                host,
                "/opt/video-monitoring-system/bin/python -c " + shlex.quote(program),
            ]
        )
    )
    if not re.fullmatch(r"[0-9a-f]{64}", result.get("gui_sha256", "")):
        raise PreflightError("Home inventory returned an invalid source fingerprint.")
    summarize_package(result.get("_modules"))
    return result


def inventory(profile, *, include_home=False, runner=run):
    primary = checkout(profile["primary_repo"], runner=runner)
    pr = checkout(profile["pr_repo"], runner=runner)
    result = {
        "primary": primary,
        "pr_checkout": pr,
        "home": None,
        "deployment_ready": False,
        "reason": "Inventory only. No phone request, approved artifact or deployment runner.",
    }
    if include_home:
        home = probe_home(profile["ssh_host"], profile["installed_gui"], runner=runner)
        result.update(
            home=home,
            installed_matches_primary_gui=home["gui_sha256"] == primary["gui_sha256"],
            installed_matches_pr_gui=home["gui_sha256"] == pr["gui_sha256"],
            installed_matches_primary_python=home["_modules"] == primary["_modules"],
            installed_matches_pr_python=home["_modules"] == pr["_modules"],
            pr_python_differences=sorted(
                name
                for name in home["_modules"].keys() | pr["_modules"].keys()
                if home["_modules"].get(name) != pr["_modules"].get(name)
            ),
        )
        if not result["installed_matches_pr_python"]:
            result["reason"] = (
                "Home and PR source differ. Do not deploy the whole clean checkout. "
                "Resolve the baseline or separately review a home-only backport."
            )
        else:
            result["reason"] = (
                "Installed Python source matches the PR checkout. "
                "This does not verify dependencies, service units or deployment approval."
            )
    for item in (primary, pr, result["home"]):
        if item is not None:
            item.update(summarize_package(item.pop("_modules")))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=ROOT / "var/vms-home.json")
    parser.add_argument(
        "--probe-home", action="store_true", help="Read-only SSH inventory"
    )
    args = parser.parse_args()
    try:
        profile = json.loads(args.profile.read_text())
        print(json.dumps(inventory(profile, include_home=args.probe_home), indent=2))
        return 0  # Successful inventory does not mean deployment is ready.
    except (KeyError, OSError, ValueError, subprocess.SubprocessError):
        print(
            "Inventory unavailable; no changes attempted. Check the private profile and SSH access.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
