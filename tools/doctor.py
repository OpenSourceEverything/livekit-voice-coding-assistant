#!/usr/bin/env python3
"""Report readiness without displaying credential values."""

import argparse
import importlib.metadata
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Check local setup without account credentials",
    )
    arguments = parser.parse_args()
    ready = sys.version_info[:2] == (3, 12)
    project_environment = Path(sys.prefix).resolve() == (ROOT / ".venv").resolve()
    ready = ready and project_environment
    print(
        f"Python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    print(
        "Project .venv: "
        + ("active" if project_environment else "not active; run through .tools/bin/uv")
    )
    for package in ("livekit-agents", "pytest", "ruff"):
        try:
            print(f"{package}: {importlib.metadata.version(package)}")
        except importlib.metadata.PackageNotFoundError:
            print(f"{package}: missing")
            ready = False
    for tool in ("git", "rg"):
        available = shutil.which(tool) is not None
        ready = ready and available
        print(f"{tool}: {'ready' if available else 'missing'}")
    for tool in ("lk", "uv"):
        available = (
            os.access(ROOT / ".tools/bin" / tool, os.X_OK)
            or shutil.which(tool) is not None
        )
        ready = ready and available
        print(f"{tool}: {'ready' if available else 'missing'}")
    if arguments.offline:
        print("LiveKit credentials: not required for offline checks")
    else:
        load_dotenv(ROOT / ".env.local")
        missing = [
            key
            for key in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
            if not os.getenv(key)
        ]
        ready = ready and not missing
        print(
            "LiveKit credentials: "
            + (
                "missing " + ", ".join(missing)
                if missing
                else "configured (values hidden)"
            )
        )
    print(
        "Codex CLI: "
        + (
            "available; optional and disabled by default"
            if shutil.which("codex")
            else "not installed; optional"
        )
    )
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
