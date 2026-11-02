#!/usr/bin/env python3
"""Bounded, single-open raw capture. A read-only TTY open can still reset hardware."""
import argparse
import datetime
import os
from pathlib import Path
import select
import sys
import termios
import time


def configure(fd):
    attrs = termios.tcgetattr(fd)
    attrs[0] = attrs[1] = attrs[3] = 0
    attrs[2] = termios.CLOCAL | termios.CREAD | termios.CS8
    attrs[4] = attrs[5] = termios.B115200
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def capture(device, output, seconds=60):
    """One port-open attempt; errors propagate after descriptor/file cleanup."""
    if not 0 < seconds <= 300:
        raise ValueError("duration must be greater than zero and at most 300 seconds")
    deadline = time.monotonic() + seconds
    # Exclusive creation prevents overwriting evidence; permissions protect raw IDs/locations.
    out_fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(out_fd, "wb") as raw:
        fd = os.open(device, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            configure(fd)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                readable, _, exceptional = select.select([fd], [], [fd], remaining)
                if exceptional:
                    raise OSError("serial descriptor exceptional condition")
                if readable:
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        raise OSError("serial device disconnected (EOF)")
                    raw.write(chunk)
                    raw.flush()
        finally:
            os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="/dev/ttyACM0")
    parser.add_argument("--duration", type=float, default=60,
                        help="capture seconds, greater than 0 and at most 300 (default: 60)")
    parser.add_argument("--out-dir", type=Path,
                        default=Path.home() / ".local/state/ouitak/captures")
    parser.add_argument("--dry-run", action="store_true",
                        help="print plan only; no files, directories, or device opens")
    args = parser.parse_args()
    if not 0 < args.duration <= 300:
        parser.error("--duration must be greater than zero and at most 300 seconds")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output = args.out_dir / f"milestone-0.5-{stamp}.bin"
    print("WARNING: opening the TTY may reset the OUI-SPY and trigger boot-time Wi-Fi restoration. "
          "No zero-reset guarantee exists.", file=sys.stderr)
    print(f"{'DRY RUN: ' if args.dry_run else ''}one open; {args.device}; 115200 8N1; "
          f"maximum {args.duration:g} seconds; raw output: {output}")
    if args.dry_run:
        return 0
    try:
        args.out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        capture(args.device, output, seconds=args.duration)
    except (OSError, ValueError) as exc:
        print(f"Capture stopped: {exc}. Partial output, if created: {output}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f"Interrupted; partial output: {output}", file=sys.stderr)
        return 130
    print(f"Capture complete: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
