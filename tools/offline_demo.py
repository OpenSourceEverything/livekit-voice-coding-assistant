#!/usr/bin/env python3
"""Exercise search, source read, Git, and a proposed diff without inference."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from changes import ChangeStore
from repository import RepositoryService


def main() -> None:
    repository = RepositoryService("var/demo-repo")
    print(json.dumps(repository.search("uart5_dma_transmit"), indent=2))
    snapshot = repository.snapshot("uart.c")
    proposal = ChangeStore(repository, "var/proposals").stage(
        "uart.c",
        snapshot["content"].replace(
            "static uint8_t uart5_dma_buffer[256];",
            "static uint8_t uart5_dma_buffer[128];",
        ),
        snapshot["sha256"],
    )
    print(proposal["diff"])
    assert repository.snapshot("uart.c")["sha256"] == snapshot["sha256"]
    print(
        "Source unchanged. This is a demonstration proposal, not a verified improvement."
    )
    print("Proposal:", proposal["id"])


if __name__ == "__main__":
    main()
