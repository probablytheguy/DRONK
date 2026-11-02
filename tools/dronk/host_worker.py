#!/usr/bin/env python3
"""Owned monitoring subprocess. No HTTP or receiver control commands."""
import argparse, json, signal, subprocess, sys, threading, time, os
from capture_log import CaptureLog, CaptureError
from dronk_db import LiveIndexer
from configuration import validate_runtime, validate_credentials
from serial_sources import MultiReader, envelope, STATUS, STOP

output = threading.Lock()


def emit(line):
    """Bridge events (consumer thread) and capture status (main thread) share stdout, one line each."""
    with output:
        sys.stdout.write(line + "\n"); sys.stdout.flush()

def main():
    args = argparse.Namespace(**validate_runtime(json.loads(sys.stdin.readline())))
    validate_credentials(vars(args))
    devices, expected = args.devices, args.expected
    ready=threading.Event()
    running, requested = threading.Event(), threading.Event(); running.set()
    def request_stop(*_):
        requested.set(); running.clear()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, request_stop)
    def status():
        return dict(capture.status(), index=indexer.status() if indexer else None)
    timed_out = False
    child = consumer = None
    reader = capture = indexer = None
    end = "error"
    try:
        if getattr(args, "capture_dir", None) and (args.demo or devices):
            try:  # before the bridge or any TTY: no capture, no monitoring
                capture = CaptureLog(args.capture_dir, args.mode, list(devices) or ["demo-east", "demo-west"], synthetic=args.demo)
            except CaptureError as error:
                emit(json.dumps({"kind": "error", "code": "E_CAPTURE", "message": str(error)}))
                return 1
            if getattr(args, "db_path", None):
                # SQLite runs on its own thread; the capture file stays authoritative if it fails.
                indexer = LiveIndexer(args.db_path, capture, synthetic=args.demo)
                indexer.start()
            emit(json.dumps(status()))
        if args.demo or devices:
            command = [args.processor, "--dronk"]
            if args.demo:
                command += ["--demo"]
            else:
                for alias in devices:
                    command += ["--stream", alias]
                for alias, public in args.receiver_aliases.items():
                    command += ["--receiver-alias", alias + "=" + public]
                for alias, receiver in expected.items():
                    command += ["--expected-receiver", alias + "=" + receiver]
                if args.forward_live:
                    command += ["--live", "--forward-live", "--tak-host", args.tak_host, "--tak-port", str(args.tak_port),
                                "--tak-connect-address", args.tak_connect_address,
                                "--client-p12", args.client_p12, "--password-file", args.password_file, "--ca-file", args.ca_file]
                    command += ["--forward-pilot-location" if args.forward_pilot_location else "--no-forward-pilot-location"]
                else:
                    command += ["--capture-only"]
            environment=dict(os.environ)
            environment["DRONK_MODE"]=args.mode
            if getattr(args,'legacy_namespace',''):
                environment['DRONK_LEGACY_NAMESPACE']=args.legacy_namespace
            child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=environment)

            def consume():
                try:
                    while True:
                        line = child.stdout.readline(65537)
                        if not line:
                            break
                        if len(line) > 65536:
                            raise ValueError("Oversized processor event")
                        event=json.loads(line)
                        if event.get("kind")=="ready": ready.set()
                        if capture:
                            position = capture.bridge(event)
                            if position and indexer: indexer.offer(*position, event)
                        emit(line.decode().rstrip())
                except (ValueError, KeyError):
                    child.terminate()
                finally:
                    running.clear()
            consumer = threading.Thread(target=consume, daemon=True)
            consumer.start()
            if devices:
                deadline=time.monotonic()+15
                while not ready.is_set() and running.is_set() and child.poll() is None and time.monotonic()<deadline:
                    ready.wait(.05)
                if not ready.is_set():
                    if requested.is_set():  # Stop pressed during startup: a clean stop, no TTY opened
                        end = "requested"
                        return 0
                    raise ValueError("Bridge did not validate startup before receiver access")
                print("Opening selected TTYs may reset receivers. No zero-reset guarantee. No serial commands/DTR/RTS ioctls.", file=sys.stderr)
                def send(data):
                    if capture: capture.envelope(data)  # raw receiver bytes, before any validation
                    child.stdin.write(data); child.stdin.flush()
                reader = MultiReader(list(devices.values()), send)
        started, heartbeat, reported = time.monotonic(), 0, 0
        while running.is_set():
            if capture:
                capture.tick()
                if time.monotonic() - reported >= 2:
                    emit(json.dumps(status())); reported = time.monotonic()
            if args.duration and time.monotonic() - started >= args.duration:
                timed_out = True
                break
            if child and child.poll() is not None:
                break
            if reader:
                reader.pump()
                if time.monotonic() - heartbeat > 1:
                    send(envelope(STATUS)); heartbeat = time.monotonic()
            else:
                time.sleep(0.1)
        end = "requested" if requested.is_set() else "duration" if timed_out else "bridge_exit"
        if reader and any(s["fatal"] for s in reader.sources):
            end = "receiver_failure"
            return 1
        if child and not requested.is_set() and not timed_out:
            print("DRONK bridge exited unexpectedly", file=sys.stderr)
            return 3
        return 0
    finally:
        if capture:
            capture.closing = True
        if reader:
            try:
                reader.close()
            except BrokenPipeError:
                pass
        if child:
            if child.poll() is None:
                try:
                    if args.demo:
                        child.terminate()
                    else:
                        child.stdin.write(envelope(STOP)); child.stdin.flush()
                        child.stdin.close()
                    child.wait(timeout=5)
                except (BrokenPipeError, subprocess.TimeoutExpired):
                    child.kill(); child.wait()
        if consumer:
            consumer.join(timeout=2)  # flush bridge output before the final event
        if indexer:
            indexer.close()  # before the capture closes, so a db_error note still lands in the file
        if capture:
            capture.close(end)
            emit(json.dumps(status()))
        if timed_out:
            emit(json.dumps({"kind": "complete", "reason": "duration", "seconds": args.duration}))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("DRONK worker failed ("+type(error).__name__+"); private details withheld",file=sys.stderr)
        raise SystemExit(1)
