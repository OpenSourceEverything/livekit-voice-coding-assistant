#!/usr/bin/env python3
"""Summarize numeric LiveKit evidence without requiring account credentials."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from telemetry import read_records, summarize_records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Numeric telemetry JSONL file")
    parser.add_argument("--json", action="store_true", help="Print structured JSON")
    parser.add_argument(
        "--include-synthetic",
        action="store_true",
        help="Show synthetic test rows in separately labeled groups",
    )
    parser.add_argument(
        "--require-live",
        action="store_true",
        help="Exit 1 when no live latency sample exists (not a qualification gate)",
    )
    args = parser.parse_args(argv)
    try:
        records, invalid = read_records(args.path)
    except (OSError, UnicodeError) as error:
        print(f"Cannot read telemetry file: {error}", file=sys.stderr)
        return 2
    summary = summarize_records(records, include_synthetic=args.include_synthetic)
    summary["invalid_rows"] = invalid
    if args.json:
        print(json.dumps(summary, indent=2, allow_nan=False))
    else:
        if not summary["live_latency_samples"]:
            print("No live latency measurements recorded. Audio/SIP runs are pending.")
        for group in summary["groups"]:
            prefix = f"{group['source']} / {group['transport']} / {group['metric']}"
            if group["unit"] == "count":
                print(f"{prefix}: total={group['total']:g}, records={group['n']}")
            else:
                print(
                    f"{prefix}: n={group['n']}, min={group['min']:.3f}s, "
                    f"p50={group['p50']:.3f}s, p95={group['p95']:.3f}s, "
                    f"max={group['max']:.3f}s"
                )
                if group["warning"]:
                    print(f"  {group['warning']}")
        for transport, missing in summary["missing_live_measurements"].items():
            print(f"Missing live {transport}: {', '.join(missing) or 'none'}")
        if summary["synthetic_samples_omitted"]:
            print(f"Synthetic samples omitted: {summary['synthetic_samples_omitted']}")
        for limitation in summary["limitations"]:
            print(limitation)
        if invalid:
            print(f"Malformed/invalid input rows skipped: {invalid}", file=sys.stderr)
    if invalid:
        return 2
    if args.require_live and not summary["live_latency_samples"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
