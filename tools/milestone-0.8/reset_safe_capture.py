#!/usr/bin/env python3
"""Receive-only OUI-SPY capture: DTR+RTS dropped together, reconnect, timestamped raw log."""
import argparse
import datetime as dt
import errno
import fcntl
import os
import select
import struct
import sys
import termios
import time

TIOCEXCL, TIOCMGET, TIOCMBIC = 0x540C, 0x5415, 0x5417
TIOCM_DTR, TIOCM_RTS = 0x002, 0x004
RESET_MARKERS = (b"ESP-ROM:esp32s3", b"rst:0x", b"waiting for download")


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def open_port(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)  # kernel: 00 -> 11 atomically
    try:
        fcntl.ioctl(fd, TIOCEXCL)  # block other non-root openers (they would toggle lines)
        # One TIOCMBIC = one SET_CONTROL_LINE_STATE: 11 -> 00, never 01 (reset) or 10 (download flag).
        fcntl.ioctl(fd, TIOCMBIC, struct.pack("i", TIOCM_DTR | TIOCM_RTS))
        attrs = termios.tcgetattr(fd)
        attrs[0] = attrs[1] = attrs[3] = 0
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL | termios.HUPCL  # no CRTSCTS
        attrs[4] = attrs[5] = termios.B115200  # never B0: cdc-acm drops DTR only -> RTS=1,DTR=0 reset
        attrs[6][termios.VMIN], attrs[6][termios.VTIME] = 0, 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        lines = struct.unpack("i", fcntl.ioctl(fd, TIOCMGET, struct.pack("i", 0)))[0]
        return fd, lines & (TIOCM_DTR | TIOCM_RTS)
    except Exception:
        os.close(fd)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", required=True, help="Explicit receiver path; no hardware-specific default")
    parser.add_argument("--out-dir", default=os.path.expanduser("~/.local/state/ouitak/captures"))
    parser.add_argument("--seconds", type=float, default=0, help="0 = until Ctrl-C")
    parser.add_argument("--settle", type=float, default=3.0, help="wait after node reappears before reopening")
    parser.add_argument("--continue-on-reset", action="store_true")
    args = parser.parse_args()
    os.makedirs(args.out_dir, mode=0o700, exist_ok=True)
    stem = os.path.join(args.out_dir, "skyspy-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    old_umask = os.umask(0o077)
    raw, idx = open(stem + ".bin", "wb"), open(stem + ".idx.tsv", "w")
    os.umask(old_umask)
    idx.write("utc\tmonotonic\toffset\tlength\tevent\n")

    def mark(event, length=0):
        idx.write(f"{now()}\t{time.monotonic():.3f}\t{raw.tell()}\t{length}\t{event}\n")
        idx.flush()

    deadline = time.monotonic() + args.seconds if args.seconds else None
    fd, tail = None, b""
    try:
        while deadline is None or time.monotonic() < deadline:
            if fd is None:
                if not os.path.exists(args.device):
                    time.sleep(0.5)
                    continue
                time.sleep(args.settle)  # don't land a reopen inside a boot-strap window
                try:
                    fd, lines = open_port(args.device)
                except OSError as exc:
                    mark(f"OPEN_FAIL {errno.errorcode.get(exc.errno, exc.errno)}")
                    time.sleep(2)
                    continue
                state = "+".join(n for bit, n in ((TIOCM_DTR, "DTR"), (TIOCM_RTS, "RTS")) if lines & bit) or "low"
                mark(f"OPEN lines={state}")
                tail = b""
            try:
                readable, _, exceptional = select.select([fd], [], [fd], 0.5)
                if exceptional:
                    raise OSError(errno.EIO, "exceptional")
                if not readable:
                    continue
                chunk = os.read(fd, 4096)
                if not chunk:
                    raise OSError(errno.ENODEV, "hangup")
                mark("DATA", len(chunk))
                raw.write(chunk)
                raw.flush()
                window = tail + chunk
                tail = window[-32:]
                if any(marker in window for marker in RESET_MARKERS):
                    mark("RESET_EVIDENCE")
                    print(f"{now()} reset/ROM output seen", file=sys.stderr)
                    if not args.continue_on_reset:
                        break
            except OSError as exc:
                mark(f"DISCONNECT {errno.errorcode.get(exc.errno, exc.errno)}")
                try:
                    os.close(fd)
                except OSError:
                    pass
                fd = None
    except KeyboardInterrupt:
        pass
    finally:
        if fd is not None:
            os.close(fd)  # HUPCL: 00 -> 00, no transition
        mark("STOP")
        raw.close()
        idx.close()
        print(f"{now()} saved {stem}.bin / .idx.tsv", file=sys.stderr)


if __name__ == "__main__":
    main()
