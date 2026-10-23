#!/usr/bin/env python3
"""One local status view. No model, account, GitHub, or deployment requests."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from usage import format_usage, read_usage, summarize_usage

ROOT = Path(__file__).resolve().parents[1]


def local_status(
    root: Path = ROOT,
    *,
    environment: Mapping[str, str] | None = None,
    include_synthetic: bool = False,
) -> dict:
    """Read selected config values; never return credential or PIN values."""
    values = dotenv_values(root / ".env.local")
    values.update(os.environ if environment is None else environment)

    def enabled(name: str, default: str = "false") -> bool:
        return str(values.get(name, default)).lower() == "true"

    repo = Path(values.get("REPO_ROOT") or "var/demo-repo")
    if not repo.is_absolute():
        repo = root / repo
    ledger = Path(values.get("USAGE_PATH") or "var/usage.jsonl")
    if not ledger.is_absolute():
        ledger = root / ledger
    cloud_demo = values.get("COPILOT_MODE") == "cloud-demo"
    pin = values.get("PHONE_ACCESS_PIN") or ""
    phone_configured = re.fullmatch(r"[0-9]{6,12}", pin) is not None
    project_id = values.get("LIVEKIT_PROJECT_ID") or ""
    project_valid = re.fullmatch(r"p_[A-Za-z0-9]+", project_id) is not None
    usage_error = False
    try:
        usage = read_usage(ledger, include_synthetic=include_synthetic)
    except FileNotFoundError:
        usage = summarize_usage([])
        usage["ledger_not_created"] = True
    except (OSError, ValueError):
        usage = summarize_usage([])
        usage_error = True
    if project_valid:
        usage["account_billing_url"] = (
            f"https://cloud.livekit.io/projects/{project_id}/billing"
        )
    return {
        "mode": "cloud sample" if cloud_demo else "laptop worker",
        "workspace": str(repo.resolve()),
        "workspace_exists": repo.is_dir(),
        "credentials_configured": all(
            values.get(name)
            for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
        ),
        "phone_pin_configured": phone_configured,
        "interruption_mode": "adaptive" if enabled("ADAPTIVE_INTERRUPTION") else "vad",
        "phone_proposals_enabled": phone_configured
        and not cloud_demo
        and enabled("ENABLE_PROPOSALS", "true")
        and enabled("ENABLE_PHONE_PROPOSALS"),
        "background_jobs_enabled": not cloud_demo and enabled("ENABLE_BACKGROUND_JOBS"),
        "background_model_worker_enabled": not cloud_demo
        and enabled("ENABLE_BACKGROUND_JOBS")
        and enabled("ENABLE_JOB_CODEX"),
        "session_status": "not queried; configuration is not proof a worker is running",
        "usage": usage,
        "usage_read_failed": usage_error,
    }


def format_status(status: dict) -> str:
    workspace = json.dumps(status["workspace"], ensure_ascii=True)
    lines = [
        "Voice coding assistant: local status (no network requests)",
        f"Mode: {status['mode']}; workspace: {workspace}",
        "Workspace: " + ("available" if status["workspace_exists"] else "missing"),
        "LiveKit credentials: "
        + ("configured (hidden)" if status["credentials_configured"] else "missing"),
        "Browser: start make dev, then open the matching LiveKit Agent Console.",
        "Configured interruption mode: "
        + status["interruption_mode"]
        + " (configuration only; restart worker after changes).",
        "Phone: "
        + (
            "PIN configured; use make call-report to inspect recorded call events."
            if status["phone_pin_configured"]
            else "disabled; a private keypad PIN and verified call routing are needed."
        ),
        "Phone proposals: "
        + (
            "enabled; separate local approval still required."
            if status["phone_proposals_enabled"]
            else "disabled."
        ),
        "PR creation / phone approval / deployment: not implemented.",
        "Background proposal jobs: "
        + (
            "enabled; saved jobs require a separate worker (liveness not queried)."
            if status["background_jobs_enabled"]
            else "disabled."
        ),
    ]
    if status["usage_read_failed"]:
        lines.append("Usage ledger unreadable; no usage or account total is known.")
    else:
        lines.append(format_usage(status["usage"], compact=True))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--include-synthetic", action="store_true")
    args = parser.parse_args()
    try:
        status = local_status(include_synthetic=args.include_synthetic)
    except (OSError, ValueError):
        print(
            "Cannot read local configuration; no network request was made.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(status, indent=2) if args.json else format_status(status))
    usage = status["usage"]
    return (
        2
        if (
            status["usage_read_failed"]
            or usage.get("invalid_rows")
            or usage.get("reader_limit_reached")
        )
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
