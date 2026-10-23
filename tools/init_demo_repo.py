#!/usr/bin/env python3
"""Prepare a tiny private local Git checkout from synthetic tracked fixtures."""

import argparse
import shutil
import subprocess
from pathlib import Path


def create_demo(destination: str | Path = "var/demo-repo") -> Path:
    destination = Path(destination).resolve()
    if (destination / ".git").is_dir():
        return destination
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Refusing to initialize a nonempty unrelated directory.")
    destination.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[1] / "examples" / "demo"
    for name in ("uart.c", "README.md"):
        shutil.copyfile(source / name, destination / name)
    for arguments in (
        ["init", "--quiet"],
        ["add", "--", "uart.c", "README.md"],
        [
            "-c",
            "user.name=Coding Buddy Demo",
            "-c",
            "user.email=demo@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--quiet",
            "-m",
            "Add synthetic UART fixture",
        ],
    ):
        subprocess.run(
            ["git", *arguments], cwd=destination, check=True, capture_output=True
        )
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=Path("var/demo-repo"))
    print(create_demo(parser.parse_args().destination))
