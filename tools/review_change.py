#!/usr/bin/env python3
"""Display a proposed diff; an explicit local command applies one proposal."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from changes import ChangeError, ChangeStore, review_digest
from repository import RepositoryError, RepositoryService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["show", "apply"])
    parser.add_argument("id")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--store", type=Path, default=Path("var/proposals"))
    parser.add_argument(
        "--max-file-bytes", type=int, choices=(128_000, 512_000), default=128_000
    )
    parser.add_argument(
        "--approve", action="store_true", help="Local approval after reviewing the diff"
    )
    args = parser.parse_args()
    try:
        store = ChangeStore(
            RepositoryService(args.repo), args.store, max_file_bytes=args.max_file_bytes
        )
        proposal = store.read(args.id)
        print(proposal["diff"])
        if args.action == "apply":
            result = store.apply(
                args.id,
                local_approval=args.approve,
                reviewed_digest=review_digest(proposal),
            )
            print(result["status"], result["path"])
        return 0
    except (ChangeError, RepositoryError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
