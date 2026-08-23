#!/usr/bin/env python3
"""Send a non-work phase boundary to the controlled perf coordinator."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def exchange(request_fifo: Path, ack_fifo: Path, event: str, phase: str) -> str:
    with request_fifo.open("w", encoding="ascii") as request:
        request.write(f"{event.upper()} {phase} {os.getpid()}\n")
        request.flush()
    with ack_fifo.open("r", encoding="ascii") as ack:
        response = ack.readline().strip()
    return response


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--event", choices=("stop",), required=True)
    args = parser.parse_args()
    request_raw = os.getenv("BENCH_PHASE_CONTROL_REQUEST_FIFO", "")
    ack_raw = os.getenv("BENCH_PHASE_CONTROL_ACK_FIFO", "")
    if not request_raw or not ack_raw:
        parser.error("phase-control request and acknowledgement FIFOs are required")
    response = exchange(Path(request_raw), Path(ack_raw), args.event, args.phase)
    expected = f"DONE {args.phase}"
    if response != expected:
        raise SystemExit(f"phase-control acknowledgement={response!r} expected={expected!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
