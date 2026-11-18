#!/usr/bin/env python3
"""Select an agent, show its status, and stream its Cloud logs."""

import argparse
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LK = ROOT / ".tools/bin/lk"
LOG_RETRY_SECONDS = 5
SLEEPING_MESSAGE = "agent has shut down due to inactivity"


def select_agent():
    result = subprocess.run(
        [LK, "agent", "list", "--json"], cwd=ROOT, capture_output=True, text=True
    )
    if result.returncode:
        print(result.stderr or result.stdout, file=sys.stderr, end="")
        return None
    try:
        agents = json.loads(result.stdout)["agents"]
        if not isinstance(agents, list):
            raise ValueError
        agents = [
            agent
            for agent in agents
            if isinstance(agent, dict)
            and isinstance(agent.get("agentId"), str)
            and agent["agentId"]
        ]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        print("Could not read the LiveKit agent list.", file=sys.stderr)
        return None
    if not agents:
        print("No agents found in this LiveKit project.")
        return None
    for number, agent in enumerate(agents, 1):
        name = json.dumps(agent.get("agentName") or "Unnamed")
        print(f"{number}. {name} [{agent['agentId']}]")
    while True:
        try:
            number = int(input(f"Select agent [1-{len(agents)}]: ")) - 1
            if 0 <= number < len(agents):
                return agents[number]["agentId"]
        except EOFError:
            return None
        except ValueError:
            print("Choose one of the listed numbers.")
            continue
        print("Choose one of the listed numbers.")


def open_private_log(path):
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(
        path,
        os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
    )
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError("log destination must be a regular file")
    os.fchmod(fd, 0o600)
    return fd


def stream_logs(command, path=None):
    log = os.fdopen(open_private_log(path), "ab", buffering=0) if path else None
    process = None
    try:
        process = subprocess.Popen(
            command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        agent_sleeping = False
        for line in process.stdout:
            # The CLI writes this exact message when Cloud has scaled the worker
            # to zero. It is a state notice, not an agent log to save repeatedly.
            if SLEEPING_MESSAGE in line.decode("utf-8", errors="replace").lower():
                agent_sleeping = True
                continue
            # The selected agent and project were already shown by the wrapper.
            # Suppress these CLI headers so waiting retries do not fill the screen.
            if line.startswith((b"Using project [", b"Using agent [")):
                continue
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
            if log:
                log.write(line)
        status = process.wait()
        return None if agent_sleeping else status
    except KeyboardInterrupt:
        return 130
    finally:
        if process and process.poll() is None:
            process.terminate()
            process.wait()
        if process and process.stdout:
            process.stdout.close()
        if log:
            log.close()


def follow_logs(command, path=None, retry_seconds=LOG_RETRY_SECONDS):
    """Keep tailing after Cloud's inactivity shutdown until logs become available."""
    while True:
        status = stream_logs(command, path)
        if status is not None:
            return status
        print(
            "Agent is sleeping; waiting for a call/session to wake it "
            f"(checking again in {retry_seconds}s; Ctrl+C to stop).",
            flush=True,
        )
        try:
            time.sleep(retry_seconds)
        except KeyboardInterrupt:
            return 130


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("agent_id", nargs="?", help="Omit to list and select an agent")
    parser.add_argument("--deployment", help="Optional deployment ID")
    parser.add_argument("--save", type=Path, help="Append logs to a private file")
    args = parser.parse_args()
    if not LK.is_file():
        parser.error("LiveKit CLI missing; run make setup first")

    agent_id = args.agent_id or select_agent()
    if not agent_id:
        return 2
    status = subprocess.run([LK, "agent", "status", "--id", agent_id], cwd=ROOT)
    if status.returncode:
        return status.returncode

    command = [LK, "agent", "logs", "--id", agent_id]
    if args.deployment:
        command.extend(["--deployment", args.deployment])
    try:
        print(f"Saving logs to {args.save}" if args.save else "Live logs:", flush=True)
        return follow_logs(command, args.save)
    except (OSError, ValueError) as error:
        print(f"Cannot stream/save logs: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
