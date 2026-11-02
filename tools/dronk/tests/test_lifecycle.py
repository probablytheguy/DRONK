"""Host monitoring lifecycle: unlimited sessions, timed captures, Stop and exits."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from test_support import IsolatedConfigTestCase

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import command_center as cc

PROCESSOR = cc.ROOT / "linux-bridge/build/install/linux-bridge/bin/linux-bridge"
FIXTURE = cc.ROOT / "linux-bridge/src/test/resources/dronk/observation.json"


def children(pid):
    try:
        return Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
    except FileNotFoundError:
        return []


class LifecycleTests(IsolatedConfigTestCase):
    def config(self, demo=True, duration=0, **kw):
        return dict(demo=demo, devices={}, expected={}, processor=str(PROCESSOR), forward_live=False, duration=duration, **kw)

    def wait_for(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(): return
            time.sleep(.02)
        self.fail("Timed out waiting for host state")

    def host(self, state):
        return state.snapshot()["host"]

    def demo(self, duration=0):
        state = cc.State(True)
        control = cc.HostController(state, self.config(duration=duration))
        self.addCleanup(control.stop)
        control.start()
        self.wait_for(lambda: self.host(state)["state"] == "RUNNING")
        return state, control

    def test_console_cli_defaults_to_unlimited_and_keeps_explicit_duration(self):
        for extra, banner in (([], b"monitoring until Stop DRONK"), (["--duration", "1"], b"timed capture 1 s per session")):
            child = subprocess.Popen([sys.executable, str(HERE / "command_center.py"), "--demo", "--port", "0", *extra],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                first = child.stdout.readline()
                self.assertIn(b"host STOPPED", first)
                self.assertIn(banner, first)
                time.sleep(1.5)  # longer than any explicit session limit: the console itself never times out
                self.assertIsNone(child.poll())
            finally:
                child.send_signal(signal.SIGTERM)
                child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0)
        result = subprocess.run([sys.executable, str(HERE / "command_center.py"), "--demo", "--port", "0", "--duration", "-1"],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 2)

    def test_unlimited_session_runs_until_stop_and_stops_gracefully(self):
        state, control = self.demo()
        child = control.child
        time.sleep(3)
        self.assertEqual(self.host(state)["state"], "RUNNING")
        self.assertIsNone(child.poll())
        descendants = children(child.pid)
        control.stop()
        self.assertEqual(child.returncode, 0)  # SIGTERM handled; not killed
        for pid in descendants:
            self.assertFalse(Path(f"/proc/{pid}").exists())
        self.assertEqual(self.host(state), dict(state="STOPPED", pid=None, error=None, reason=None))

    def test_timed_capture_completes_without_error_and_console_restarts(self):
        state, control = self.demo(duration=1)
        first = control.child.pid
        self.wait_for(lambda: self.host(state)["state"] != "RUNNING")
        self.assertEqual(self.host(state), dict(state="STOPPED", pid=None, error=None, reason="Timed capture complete"))
        self.assertEqual(state.snapshot()["mode"], "DISCONNECTED")
        control.start()
        self.assertNotEqual(control.child.pid, first)
        self.wait_for(lambda: self.host(state)["state"] == "RUNNING")
        control.stop()
        self.assertEqual(self.host(state)["state"], "STOPPED")

    def test_capture_timed_session_with_pty_keeps_tak_disabled(self):
        master, slave = os.openpty()
        self.addCleanup(os.close, master); self.addCleanup(os.close, slave)
        state = cc.State()
        config = self.config(demo=False, duration=2, mode="CAPTURE")
        config["devices"] = {"front": os.ttyname(slave)}
        control = cc.HostController(state, config)
        self.addCleanup(control.stop)
        control.start()
        self.assertFalse(control.config["forward_live"])
        self.wait_for(lambda: any(r.get("connected") for r in state.snapshot()["receivers"]))
        os.write(master, (FIXTURE.read_text().strip() + "\n").encode())
        self.wait_for(lambda: state.snapshot()["mode"] == "CAPTURE")
        child = control.child
        self.wait_for(lambda: self.host(state)["state"] == "STOPPED")
        snapshot = state.snapshot()
        self.assertEqual(snapshot["host"]["reason"], "Timed capture complete")
        self.assertIsNone(snapshot["host"]["error"])
        self.assertEqual((snapshot["operation_mode"], snapshot["tak"]["state"]), ("CAPTURE", "DISABLED"))
        self.assertEqual(child.returncode, 0)
        self.assertFalse(snapshot["receivers"][0]["connected"])

    def test_unexpected_worker_exit_is_an_error(self):
        state, control = self.demo()
        control.child.kill()
        self.wait_for(lambda: self.host(state)["state"] == "ERROR")
        self.assertEqual(self.host(state)["error"], "Host process exited unexpectedly (signal 9)")
        self.assertEqual(state.snapshot()["mode"], "DISCONNECTED")

    def test_unexpected_bridge_exit_is_an_error_not_a_clean_stop(self):
        state, control = self.demo()
        bridge = children(control.child.pid)
        self.assertTrue(bridge)
        os.kill(int(bridge[0]), signal.SIGKILL)
        self.wait_for(lambda: self.host(state)["state"] == "ERROR")
        self.assertEqual(self.host(state)["error"], "Host process exited unexpectedly (code 3)")

    def test_stop_racing_an_exit_ends_stopped(self):
        state, control = self.demo()
        control.child.kill()
        control.stop()
        time.sleep(.5)  # let the consumer finish; it must not report the requested stop as an error
        self.assertEqual(self.host(state), dict(state="STOPPED", pid=None, error=None, reason=None))

    def test_failed_stop_never_leaves_stopping(self):
        state, control = self.demo()
        child = control.child
        with patch.object(child, "terminate", side_effect=PermissionError):
            with self.assertRaises(OSError):
                control.stop()
        self.assertEqual(self.host(state)["state"], "ERROR")
        self.assertEqual(self.host(state)["error"], "Host process could not be stopped cleanly")
        self.assertIs(control.child, child)
        control.start()  # still owned and alive: no second worker
        self.assertIs(control.child, child)
        control.stop()  # retry succeeds
        self.assertEqual(child.returncode, 0)
        self.assertEqual(self.host(state), dict(state="STOPPED", pid=None, error=None, reason=None))
