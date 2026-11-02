"""Read-only, explicit multi-TTY sources. No device discovery, commands or line toggles."""
import os
import select
import struct
import termios
import time

DATA, CONNECTED, DISCONNECTED, STOP, STATUS, ERROR = 1, 2, 3, 4, 5, 6


def envelope(kind, index=65535, data=b""):
    return struct.pack(">BHqI", kind, index, time.time_ns() // 1_000_000, len(data)) + data


def open_reader(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        a = termios.tcgetattr(fd)
        a[0] = a[1] = a[3] = 0
        a[2] = termios.CLOCAL | termios.CREAD | termios.CS8
        a[4] = a[5] = termios.B115200
        a[6][termios.VMIN] = a[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, a)
        return fd
    except BaseException:
        os.close(fd)
        raise


class MultiReader:
    def __init__(self, paths, send):
        if not 1 <= len(paths) <= 8 or len({os.path.realpath(p) for p in paths}) != len(paths):
            raise ValueError("Select 1–8 distinct explicit serial devices")
        self.sources = [dict(path=p, fd=None, fatal=False, wait_remove=None, retry_at=0) for p in paths]
        self.send = send

    def _close(self, index):
        s = self.sources[index]
        if s["fd"] is not None:
            try:
                os.close(s["fd"])
            finally:
                s["fd"] = None
            self.send(envelope(DISCONNECTED, index))

    def pump(self, timeout=0.2):
        now = time.monotonic()
        for index, s in enumerate(self.sources):
            if s["fatal"]:
                continue
            present = os.path.exists(s["path"])
            if s["wait_remove"] is not None:
                if not present:
                    s["wait_remove"] = None
                    s["retry_at"] = now + 1
                elif now >= s["wait_remove"]:
                    s["fatal"] = True  # stop this stream; no reopen while node remains
                    self.send(envelope(ERROR, index))
                continue
            if s["fd"] is not None and not present:
                self._close(index)
                s["retry_at"] = now + 1
            if s["fd"] is None and present and now >= s["retry_at"]:
                try:
                    # Prevent aliases opening the same live device twice.
                    inode = (os.stat(s["path"]).st_dev, os.stat(s["path"]).st_ino)
                    if any(other["fd"] is not None and (os.fstat(other["fd"]).st_dev, os.fstat(other["fd"]).st_ino) == inode for other in self.sources):
                        raise OSError("Duplicate device inode")
                    s["fd"] = open_reader(s["path"])
                    self.send(envelope(CONNECTED, index))
                except OSError:
                    s["fatal"] = True  # includes configuration failure; no retry
                    self.send(envelope(ERROR, index))
        fds = [s["fd"] for s in self.sources if s["fd"] is not None]
        if not fds:
            time.sleep(timeout)
            return
        ready, _, errors = select.select(fds, [], fds, timeout)
        for index, s in enumerate(self.sources):
            fd = s["fd"]
            if fd not in ready and fd not in errors:
                continue
            try:
                if fd in errors:
                    raise OSError("Exceptional descriptor")
                chunk = os.read(fd, 4096)
                if not chunk:
                    raise OSError("Serial EOF")
                self.send(envelope(DATA, index, chunk))
            except OSError:
                self._close(index)
                s["wait_remove"] = time.monotonic() + 2

    def close(self):
        for index in range(len(self.sources)):
            self._close(index)
