import importlib.util
import os
import pathlib
import pty
import struct
import sys
import termios
import tempfile
import unittest
from unittest import mock
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from test_support import IsolatedConfigTestCase

MODULE_PATH = pathlib.Path(__file__).with_name("linux_serial_bridge.py")
SPEC = importlib.util.spec_from_file_location("linux_serial_bridge", MODULE_PATH)
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class FakeChild:
    def __init__(self):
        self.stdin = mock.Mock()
        self.stdin.write = mock.Mock()
        self.stdin.flush = mock.Mock()
        self.returncode = 0
        self.poll = mock.Mock(return_value=None)


class LinuxSerialAdapterTests(IsolatedConfigTestCase):
    def test_explicit_synthetic_mode_never_opens_serial_device(self):
        child = FakeChild()
        child.wait = mock.Mock(return_value=0)
        with mock.patch.object(sys, "argv", ["linux_serial_bridge.py", "--synthetic", "--processor", "fake-core"]), \
                mock.patch.object(bridge.subprocess, "Popen", return_value=child) as popen, \
                mock.patch.object(bridge, "open_reader", side_effect=AssertionError("serial opened")) as open_reader:
            self.assertEqual(0, bridge.main())
        self.assertIn("--synthetic", popen.call_args.args[0])
        self.assertNotIn("--capture-only", popen.call_args.args[0])
        child.stdin.close.assert_called_once()
        open_reader.assert_not_called()

    def test_live_mode_selects_tls_forwarding_without_synthetic_source(self):
        child = FakeChild()
        child.wait = mock.Mock(return_value=0)
        arguments = ["linux_serial_bridge.py", "--live", "--device", "/dev/ttyACM-test", "--processor", "fake-core",
                     "--tak-host", "tak.example.test", "--client-p12", "client.p12", "--password-file", "password-file", "--ca-file", "ca.pem"]
        temporary=tempfile.TemporaryDirectory()
        for option in ('--client-p12','--password-file','--ca-file'):
            path=pathlib.Path(temporary.name)/option.removeprefix('--')
            path.write_text('test-only local fixture');path.chmod(0o600)
            arguments[arguments.index(option)+1]=str(path)
        previous_running = bridge.running
        bridge.running = False
        try:
            with mock.patch.object(sys, "argv", arguments), \
                    mock.patch.object(bridge.subprocess, "Popen", return_value=child) as popen, \
                    mock.patch.object(bridge, "open_reader") as open_reader:
                self.assertEqual(0, bridge.main())
            command = popen.call_args.args[0]
            self.assertIn("--live", command)
            self.assertIn("--forward-live", command)
            self.assertNotIn("--synthetic", command)
            self.assertNotIn("--forward-synthetic", command)
            open_reader.assert_not_called()
            child.stdin.write.assert_called_once_with(bytes((bridge.STOP,)))
        finally:
            bridge.running = previous_running
            temporary.cleanup()

    def test_termios_configures_raw_115200_8n1_without_line_control_calls(self):
        master, slave = pty.openpty()
        try:
            with mock.patch.object(termios, "tcgetattr", wraps=termios.tcgetattr) as get_attrs, \
                    mock.patch.object(termios, "tcsetattr", wraps=termios.tcsetattr) as set_attrs:
                bridge.configure_tty(slave)
                self.assertEqual(get_attrs.call_count, 1)
                self.assertEqual(set_attrs.call_count, 1)
            attrs = termios.tcgetattr(slave)
            self.assertEqual(attrs[4], termios.B115200)
            self.assertEqual(attrs[5], termios.B115200)
            self.assertTrue(attrs[2] & termios.CS8)
            self.assertFalse(attrs[2] & termios.PARENB)
            self.assertFalse(attrs[2] & termios.CSTOPB)
            self.assertEqual(attrs[3], 0)
        finally:
            os.close(master)
            os.close(slave)

    def test_open_reader_uses_read_only_descriptor_and_receives_pty_data(self):
        master, slave = pty.openpty()
        path = os.ttyname(slave)
        os.close(slave)
        fd = None
        try:
            with mock.patch.object(bridge.os, "open", wraps=os.open) as open_file:
                fd = bridge.open_reader(path)
                self.assertEqual(open_file.call_args.args[1], os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
            os.write(master, b"status\n")
            readable, _, _ = __import__("select").select([fd], [], [], 1)
            self.assertTrue(readable)
            self.assertEqual(os.read(fd, 64), b"status\n")
        finally:
            if fd is not None:
                os.close(fd)
            os.close(master)

    def test_simulated_usb_reconnect_discards_partial_epoch_and_accepts_next_frame(self):
        child = FakeChild()
        first_master, first_slave = pty.openpty()
        first_path = os.ttyname(first_slave)
        os.close(first_slave)
        first_fd = bridge.open_reader(first_path)
        bridge.send_envelope(child, bridge.CONNECTED)
        bridge.send_envelope(child, bridge.DATA, b'{"basic_id":"partial')
        bridge.close_reader(first_fd, child, connected=True)
        os.close(first_master)

        second_master, second_slave = pty.openpty()
        second_path = os.ttyname(second_slave)
        os.close(second_slave)
        second_fd = bridge.open_reader(second_path)
        try:
            bridge.send_envelope(child, bridge.CONNECTED)
            valid = (b'{"mac":"02:11:22:33:44:55","rssi":-60,"drone_lat":38.6,'
                     b'"drone_long":-90.2,"drone_altitude":100,"pilot_lat":38.6,'
                     b'"pilot_long":-90.2,"basic_id":"RID-RECONNECT"}\n')
            malformed = b'{bad json}\n'
            bridge.send_envelope(child, bridge.DATA, valid + malformed)
        finally:
            os.close(second_fd)
            os.close(second_master)

        writes = [call.args[0] for call in child.stdin.write.call_args_list]
        self.assertEqual(writes[0], bytes((bridge.CONNECTED,)))
        self.assertEqual(writes[1], struct.pack(">BI", bridge.DATA, len(b'{"basic_id":"partial')))
        self.assertEqual(writes[2], b'{"basic_id":"partial')
        self.assertEqual(writes[3], bytes((bridge.DISCONNECTED,)))
        self.assertEqual(writes[4], bytes((bridge.CONNECTED,)))
        self.assertEqual(writes[5], struct.pack(">BI", bridge.DATA, len(valid + malformed)))
        self.assertEqual(writes[6], valid + malformed)

    def test_internal_chunk_protocol_preserves_binary_data_and_disconnect_kind(self):
        child = FakeChild()
        payload = b'{"basic_id":"SYNTHETIC\xff"}\r\n'
        bridge.send_envelope(child, bridge.DATA, payload)
        bridge.send_envelope(child, bridge.DISCONNECTED)
        self.assertEqual(child.stdin.write.call_args_list[0].args[0], struct.pack(">BI", bridge.DATA, len(payload)))
        self.assertEqual(child.stdin.write.call_args_list[1].args[0], payload)
        self.assertEqual(child.stdin.write.call_args_list[2].args[0], bytes((bridge.DISCONNECTED,)))
        self.assertEqual(child.stdin.flush.call_count, 2)

    def test_download_mode_banner_detected_across_chunk_boundary_once_per_epoch(self):
        detector = bridge.DownloadModeDetector()
        self.assertFalse(detector.feed(b"rst:0x1 boot:0x0 (DOWNLOAD(USB/UART0))\nwaiting for dow"))
        self.assertTrue(detector.feed(b"nload\n"))
        self.assertFalse(detector.feed(b"waiting for download\n"))
        detector.reset()
        self.assertTrue(detector.feed(b"waiting for download\n"))
        self.assertFalse(bridge.DownloadModeDetector().feed(b'{"basic_id":"RID"}\n'))

    def _run_live_with_read_error(self, node_vanishes):
        child = FakeChild()
        child.wait = mock.Mock(return_value=0)
        master, slave = pty.openpty()
        path = os.ttyname(slave)
        os.close(slave)
        opens = []
        real_open = bridge.open_reader

        def counting_open(device):
            opens.append(device)
            if len(opens) > 1:
                bridge.running = False
                raise FileNotFoundError(device)
            return real_open(device)

        arguments = ["linux_serial_bridge.py", "--device", path, "--processor", "fake-core"]
        previous_running = bridge.running
        bridge.running = True
        try:
            with mock.patch.object(sys, "argv", arguments), \
                    mock.patch.object(bridge.subprocess, "Popen", return_value=child), \
                    mock.patch.object(bridge, "open_reader", side_effect=counting_open), \
                    mock.patch.object(bridge.os, "read", side_effect=OSError(5, "EIO")), \
                    mock.patch.object(bridge, "wait_for_node_removal", return_value=node_vanishes), \
                    mock.patch.object(bridge.time, "sleep"):
                os.write(master, b"x")
                status = bridge.main()
        finally:
            bridge.running = previous_running
            os.close(master)
        return status, opens

    def test_read_error_with_node_present_stops_instead_of_reopening(self):
        status, opens = self._run_live_with_read_error(node_vanishes=False)
        self.assertEqual(1, status)
        self.assertEqual(1, len(opens))

    def test_read_error_after_node_vanished_waits_to_reconnect(self):
        status, opens = self._run_live_with_read_error(node_vanishes=True)
        self.assertEqual(0, status)
        self.assertEqual(2, len(opens))

    def test_silence_watchdog_alerts_once(self):
        child = FakeChild()
        child.wait = mock.Mock(return_value=0)
        master, slave = pty.openpty()
        path = os.ttyname(slave)
        os.close(slave)
        clock = [1000.0]

        def fake_monotonic():
            clock[0] += 20.0
            return clock[0]

        arguments = ["linux_serial_bridge.py", "--device", path, "--processor", "fake-core",
                     "--duration-seconds", "300"]
        messages = []
        try:
            with mock.patch.object(sys, "argv", arguments), \
                    mock.patch.object(bridge.subprocess, "Popen", return_value=child), \
                    mock.patch.object(bridge.time, "monotonic", side_effect=fake_monotonic), \
                    mock.patch.object(bridge.select, "select", return_value=([], [], [])), \
                    mock.patch.object(bridge, "log", side_effect=messages.append):
                self.assertEqual(0, bridge.main())
        finally:
            os.close(master)
        alerts = [m for m in messages if m.startswith("ALERT: no serial bytes")]
        self.assertEqual(1, len(alerts))


if __name__ == "__main__":
    unittest.main()
