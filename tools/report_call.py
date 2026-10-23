#!/usr/bin/env python3
"""Summarize the latest allowlisted call diagnostic session, never transcripts."""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from call_diagnostics import EVENTS, MAX_BYTES

ROOT = Path(__file__).resolve().parents[1]


def summarize(path, include_synthetic=False):
    latest = None
    sessions = defaultdict(Counter)
    modes = {}
    if Path(path).stat().st_size > MAX_BYTES + 4096:
        raise ValueError("Diagnostic reader limit reached.")
    with Path(path).open() as stream:
        for index, line in enumerate(stream):
            if index >= 30_000 or len(line) > 4096:
                raise ValueError("Diagnostic reader limit reached.")
            row = json.loads(line)
            if not isinstance(row, dict) or not re.fullmatch(
                r"[0-9a-f]{32}", str(row.get("session_id", ""))
            ):
                raise ValueError("Invalid diagnostic record.")
            if row.get("source") == "synthetic" and not include_synthetic:
                continue
            if row.get("source") not in {"live", "synthetic"}:
                raise ValueError("Invalid diagnostic source.")
            event = row.get("event")
            # Output is a bounded count projection; never echo arbitrary fields.
            if not isinstance(event, str) or event not in EVENTS:
                raise ValueError("Invalid diagnostic event.")
            latest = row["session_id"]
            sessions[latest][event] += 1
            mode = row.get("mode")
            if event == "session_start" and mode in ("vad", "adaptive"):
                modes[latest] = mode
    counts = sessions.get(latest, Counter())
    return {
        "latest_session": latest,
        "records": sum(counts.values()),
        "events": dict(counts),
        "interruption_mode": modes.get(latest),
        "caller_heard_stop_verified": False,
        "scope": "SDK lifecycle only; not SIP packet traces or acoustic measurements",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-synthetic", action="store_true")
    parser.add_argument("--path", type=Path)
    args = parser.parse_args()
    path = args.path or Path(
        os.getenv("CALL_LOG_PATH")
        or dotenv_values(ROOT / ".env.local").get("CALL_LOG_PATH")
        or "var/calls.jsonl"
    )
    if not path.is_absolute():
        path = ROOT / path
    try:
        print(json.dumps(summarize(path, args.include_synthetic), indent=2))
        return 0
    except FileNotFoundError:
        print(
            "No call diagnostics recorded yet. This does not prove a worker is running."
        )
        return 0
    except (OSError, ValueError):
        print("Call diagnostics unreadable; no raw records displayed.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
