#!/usr/bin/env python3
"""Show recorded session tokens and partial cost estimates; never query accounts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from usage import format_usage, read_usage, summarize_usage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, nargs="?", default=Path("var/usage.jsonl"))
    parser.add_argument(
        "--json", action="store_true", help="Print structured numeric usage"
    )
    parser.add_argument(
        "--include-synthetic",
        action="store_true",
        help="Include separately labeled test sessions",
    )
    parser.add_argument(
        "--no-estimate",
        action="store_true",
        help="Omit the partial LLM/TTS dollar estimate",
    )
    parser.add_argument(
        "--project-id", help="Optional p_ project ID for its direct billing link"
    )
    args = parser.parse_args(argv)
    if args.project_id and re.fullmatch(r"p_[A-Za-z0-9]+", args.project_id) is None:
        parser.error("project ID must match p_ followed by letters and digits")
    try:
        summary = read_usage(
            args.path,
            include_synthetic=args.include_synthetic,
            estimate_costs=not args.no_estimate,
        )
    except FileNotFoundError:
        summary = summarize_usage([], estimate_costs=not args.no_estimate)
        summary["ledger_not_created"] = True
    except (OSError, ValueError):
        print(
            "Cannot read the usage ledger; check its location and permissions.",
            file=sys.stderr,
        )
        return 2
    if args.project_id:
        summary["account_billing_url"] = (
            f"https://cloud.livekit.io/projects/{args.project_id}/billing"
        )
    print(
        json.dumps(summary, indent=2, allow_nan=False)
        if args.json
        else format_usage(summary)
    )
    return (
        2 if summary.get("invalid_rows") or summary.get("reader_limit_reached") else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
