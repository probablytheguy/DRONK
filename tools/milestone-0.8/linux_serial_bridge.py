#!/usr/bin/env python3
"""Read-only Linux CDC-ACM adapter. Sends bounded binary chunks to shared-core JVM stdin."""
import argparse
import datetime as dt
import errno
import math
import os
import select
import signal
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"dronk"))
from configuration import load_settings, resolve, validate_credentials

DATA, CONNECTED, DISCONNECTED, STOP, STATUS = 1, 2, 3, 4, 5
MAX_READ = 4096
DOWNLOAD_MODE_MARKER = b"waiting for download"
SILENCE_ALERT_SECONDS = 60.0  # firmware status lines arrive every 20-30 s
NODE_VANISH_GRACE_SECONDS = 2.0  # udev may remove the node shortly after EIO
running = True


def log(message):
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")
    print(f"{stamp} {message}", file=sys.stderr, flush=True)


def stop_signal(_signum, _frame):
    global running
    running = False


def configure_tty(fd):
    """Configure raw 115200 8N1. Never calls TIOCMBIS/TIOCMBIC or writes payload."""
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0  # input flags: raw bytes; software flow control disabled
    attrs[1] = 0  # output processing disabled
    attrs[2] = termios.CLOCAL | termios.CREAD | termios.CS8  # no parity, one stop bit
    if hasattr(termios, "CRTSCTS"):
        attrs[2] &= ~termios.CRTSCTS
    attrs[3] = 0  # canonical mode, echo, and signals disabled
    attrs[4] = termios.B115200
    attrs[5] = termios.B115200
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 1
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def send_envelope(child, kind, payload=b""):
    if child.stdin is None:
        raise BrokenPipeError("shared-core process input is closed")
    if kind == DATA:
        child.stdin.write(struct.pack(">BI", kind, len(payload)))
        child.stdin.write(payload)
    else:
        child.stdin.write(bytes((kind,)))
    child.stdin.flush()


def open_reader(path):
    # Linux cdc-acm asserts DTR and RTS together when activating the TTY and
    # clears both together when closing. This adapter does not subsequently
    # change either line. Opening may cause a normal reset on some boards.
    fd = os.open(path, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        configure_tty(fd)
        return fd
    except Exception:
        os.close(fd)
        raise


def close_reader(fd, child, connected, reconnecting=True):
    """End one USB epoch and tell shared-core to discard a partial line."""
    try:
        os.close(fd)
    except OSError:
        pass
    if connected and child.poll() is None:
        send_envelope(child, DISCONNECTED)
        log("USB CDC disconnected/reset; partial frame discarded; "
            + ("waiting to reconnect" if reconnecting else "reader stopping"))


class DownloadModeDetector:
    """Spots the ESP32 ROM bootloader banner, including when split across reads."""

    def __init__(self):
        self.tail = b""
        self.reported = False

    def reset(self):
        self.tail = b""
        self.reported = False

    def feed(self, chunk):
        window = self.tail + chunk
        self.tail = window[-(len(DOWNLOAD_MODE_MARKER) - 1):]
        if self.reported or DOWNLOAD_MODE_MARKER not in window:
            return False
        self.reported = True
        return True


def wait_for_node_removal(path, grace_seconds):
    """True if the device node disappears within the grace period."""
    deadline = time.monotonic() + grace_seconds
    while True:
        if not os.path.exists(path):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--synthetic", action="store_true",
                        help="run a bounded synthetic Sky Spy fixture; never opens a serial device")
    source.add_argument("--live", action="store_true",
                        help="read the OUI-SPY and forward valid observations over authenticated TLS")
    parser.add_argument("--device", default=None,
                        help="serial device (default /dev/ttyACM0); synthetic mode cannot use a device")
    parser.add_argument("--processor", required=True, help="shared-core JVM executable")
    parser.add_argument("--duration-seconds", type=float, default=0.0,
                        help="optional bounded capture duration; 0 runs until interrupted")
    parser.add_argument("--forward-synthetic", action="store_true",
                        help="send only the explicit synthetic fixture through authenticated TLS")
    parser.add_argument("--disable-provisional", action="store_true")
    parser.add_argument("--silence-alert-seconds", type=float, default=SILENCE_ALERT_SECONDS,
                        help="alert when a connected device sends no bytes for this long")
    parser.add_argument("--tak-host")
    parser.add_argument("--tak-connect-address")
    parser.add_argument("--config")
    parser.add_argument("--forward-pilot-location",action=argparse.BooleanOptionalAction,default=None)
    parser.add_argument("--mode")
    parser.add_argument("--tak-port", type=int)
    parser.add_argument("--client-p12")
    parser.add_argument("--password-file")
    parser.add_argument("--ca-file")
    args, unknown = parser.parse_known_args()
    if unknown: parser.error("Unsupported adapter option; values withheld")
    if not math.isfinite(args.duration_seconds) or not 0 <= args.duration_seconds <= 3600:
        parser.error("--duration-seconds must be between 0 and 3600")
    if not math.isfinite(args.silence_alert_seconds) or not 5 <= args.silence_alert_seconds <= 3600:
        parser.error("--silence-alert-seconds must be between 5 and 3600")
    try:
        if args.forward_synthetic: raise ValueError("DEMO/synthetic mode cannot forward to TAK")
        args.demo=args.synthetic
        args.forward_live=args.live
        data=load_settings(args.config)
        mode=args.mode or ('DEMO' if args.synthetic else 'LIVE' if args.live else data['mode'])
        args.mode=mode
        device=args.device
        if mode!='DEMO' and not device:
            configured=list(data['receivers'].values())
            if len(configured)>1: raise ValueError("Legacy adapter requires one explicit receiver")
            device=configured[0] if configured else '/dev/ttyACM0'
        config=resolve(args,{'legacy':device} if device else {},{})
        validate_credentials(config)
        args.forward_pilot_location=config['forward_pilot_location']
        args.synthetic=config['demo']; args.live=config['forward_live']; args.device=device
        for key in ('tak_host','tak_port','tak_connect_address','client_p12','password_file','ca_file'):
            setattr(args,key,config.get(key))
    except ValueError as error:
        parser.error(str(error))

    command = [args.processor]
    if args.synthetic:
        command.append("--synthetic")
        if args.forward_synthetic:
            command.append("--forward-synthetic")
    elif args.live:
        command += ["--live", "--forward-live"]
    else:
        command.append("--capture-only")
    if args.disable_provisional:
        command.append("--disable-provisional")
    if args.forward_synthetic or args.live:
        command += ["--tak-host", args.tak_host, "--tak-port", str(args.tak_port), "--tak-connect-address",args.tak_connect_address,
                    "--client-p12", args.client_p12, "--password-file", args.password_file, "--ca-file", args.ca_file]
    if args.live:
        command += ["--forward-pilot-location" if args.forward_pilot_location else "--no-forward-pilot-location"]
    environment=dict(os.environ,DRONK_MODE=args.mode)
    child = subprocess.Popen(command, stdin=subprocess.PIPE, bufsize=0,env=environment)
    signal.signal(signal.SIGINT, stop_signal)
    signal.signal(signal.SIGTERM, stop_signal)
    fd = None
    connected = False
    fatal = False
    retry_seconds = 1.0
    download_mode = DownloadModeDetector()
    last_byte_at = time.monotonic()
    silence_alerted = False
    last_heartbeat = time.monotonic()
    deadline = time.monotonic() + args.duration_seconds if args.duration_seconds > 0 else None
    if args.synthetic:
        log("synthetic source selected; serial device will not be opened")
        child.stdin.close()
        return child.wait()
    device = args.device or "/dev/ttyACM0"
    log(f"reader starting mode={'live-TLS' if args.live else 'capture-only'} device=selected baud=115200 framing=8N1 "
        f"readOnly=true modemLineToggles=none forwardLive={args.live}")
    try:
        while running and child.poll() is None and (deadline is None or time.monotonic() < deadline):
            if fd is None:
                try:
                    fd = open_reader(device)
                    connected = True
                    retry_seconds = 1.0
                    download_mode.reset()
                    last_byte_at = time.monotonic()
                    silence_alerted = False
                    send_envelope(child, CONNECTED)
                    log("USB CDC opened and configured; no serial bytes written; no DTR/RTS ioctl issued")
                except FileNotFoundError:
                    connected = False
                    log(f"device not present; retry in {retry_seconds:.0f}s")
                    time.sleep(retry_seconds)
                    retry_seconds = min(retry_seconds * 2, 8.0)
                    continue
                except OSError as exc:
                    connected = False
                    log(f"open/configuration error={exc.__class__.__name__}; retry in {retry_seconds:.0f}s")
                    time.sleep(retry_seconds)
                    retry_seconds = min(retry_seconds * 2, 8.0)
                    continue
            try:
                readable, _, exceptional = select.select([fd], [], [fd], 0.5)
                if exceptional:
                    raise OSError(errno.EIO, "serial descriptor exceptional condition")
                if readable:
                    chunk = os.read(fd, MAX_READ)
                    if chunk:
                        last_byte_at = time.monotonic()
                        if silence_alerted:
                            log("serial bytes resumed after silence")
                            silence_alerted = False
                        if download_mode.feed(chunk):
                            log("ERROR: OUI-SPY is in ESP32 ROM download mode ('waiting for download'); "
                                "firmware is NOT running and no detections will arrive. "
                                "Unplug and replug the receiver (do not hold BOOT).")
                        send_envelope(child, DATA, chunk)
                    else:
                        if not os.path.exists(device):
                            raise OSError(errno.ENODEV, "serial device node disappeared")
                        time.sleep(0.05)
                if time.monotonic() - last_heartbeat >= 10:
                    send_envelope(child, STATUS)
                    last_heartbeat = time.monotonic()
                    log(f"reader heartbeat connected={connected} device=selected baud=115200")
                silent_for = time.monotonic() - last_byte_at
                if not silence_alerted and silent_for >= args.silence_alert_seconds:
                    silence_alerted = True
                    log(f"ALERT: no serial bytes for {silent_for:.0f}s (status lines expected every 20-30s); "
                        "receiver may be hung or in ROM download mode. Unplug and replug the receiver.")
            except OSError as exc:
                # Reopening a node that still exists can reset the board; only
                # an actual USB disappearance justifies waiting to reconnect.
                if wait_for_node_removal(device, NODE_VANISH_GRACE_SECONDS):
                    close_reader(fd, child, connected)
                    fd = None
                    connected = False
                    time.sleep(retry_seconds)
                    retry_seconds = min(retry_seconds * 2, 8.0)
                else:
                    log(f"ERROR: read error={exc.__class__.__name__} errno={exc.errno} while device node still "
                        "present; stopping instead of reopening (a reopen may reset the board). "
                        "Check the receiver, then restart the bridge.")
                    close_reader(fd, child, connected, reconnecting=False)
                    fd = None
                    connected = False
                    fatal = True
                    break
        if child.poll() is None:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                fd = None
            send_envelope(child, STOP)
    except BrokenPipeError:
        log("shared-core processor exited; stopping reader")
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if child.stdin is not None:
            child.stdin.close()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.terminate()
            child.wait(timeout=3)
    return 1 if fatal else (child.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main())
