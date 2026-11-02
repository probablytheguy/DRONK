import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("capture_serial", Path(__file__).with_name("capture_serial.py"))
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


class CaptureTests(unittest.TestCase):
    def run_capture(self, chunks=(), failure=None, configuration_failure=False, seconds=60):
        # Real regular-file output, entirely mocked serial open/read/configuration.
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capture.bin"
            real_open, real_close = capture.os.open, capture.os.close
            real_fdopen = capture.os.fdopen
            outputs = []
            def file_opening(*args):
                stream = real_fdopen(*args)
                outputs.append(stream)
                return stream
            def opening(path, flags, *args):
                if path == "mock-device":
                    return 98765
                return real_open(path, flags, *args)
            def closing(fd):
                if fd != 98765:
                    real_close(fd)
            count = len(chunks) + (1 if failure else 0)
            readiness = [([98765], [], [])] * count + [([], [], [])]
            ticks = [0] + [0] * (count + 1) + [seconds]
            with patch.object(capture.os, "open", side_effect=opening) as opened, \
                 patch.object(capture.os, "fdopen", side_effect=file_opening), \
                 patch.object(capture.os, "close", side_effect=closing) as closed, \
                 patch.object(capture, "configure", side_effect=OSError("configure") if configuration_failure else None), \
                 patch.object(capture.time, "monotonic", side_effect=ticks), \
                 patch.object(capture.select, "select", side_effect=readiness) as selected, \
                 patch.object(capture.os, "read", side_effect=list(chunks) + ([failure] if failure else [])):
                if failure or configuration_failure:
                    with self.assertRaises(OSError):
                        capture.capture("mock-device", output, seconds=seconds)
                else:
                    capture.capture("mock-device", output, seconds=seconds)
                    self.assertEqual(selected.call_args.args[3], seconds)
                port_calls = [c for c in opened.call_args_list if c.args[0] == "mock-device"]
                self.assertEqual(len(port_calls), 1)
                self.assertEqual(port_calls[0].args[1], capture.os.O_RDONLY | capture.os.O_NOCTTY | capture.os.O_NONBLOCK)
                closed.assert_called_once_with(98765)
            self.assertEqual(output.read_bytes(), b"".join(chunks))
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertTrue(outputs[0].closed)

    def test_timeout_and_raw_bytes(self):
        self.run_capture([b"\x00raw\r\n\xff"])

    def test_flight_duration_timeout_and_cleanup(self):
        self.run_capture([b"flight\r\n"], seconds=300)

    def test_invalid_duration_never_opens_files_or_device(self):
        for seconds in (0, -1, 301, float("nan"), float("inf"), -float("inf")):
            with self.subTest(seconds=seconds), patch.object(capture.os, "open") as opened:
                with self.assertRaises(ValueError):
                    capture.capture("mock-device", "unused.bin", seconds=seconds)
                opened.assert_not_called()

    def test_cli_duration_default_and_override(self):
        for options, expected in (([], 60), (["--duration", "300"], 300)):
            with self.subTest(options=options), \
                 patch("sys.argv", ["capture_serial.py"] + options), \
                 patch.object(capture, "capture") as recording, \
                 patch.object(capture.Path, "mkdir"), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(capture.main(), 0)
                self.assertEqual(recording.call_args.kwargs["seconds"], expected)

    def test_cli_invalid_duration_before_side_effects(self):
        for value in ("0", "-1", "301", "nan", "inf", "invalid"):
            with self.subTest(value=value), \
                 patch("sys.argv", ["capture_serial.py", "--duration", value]), \
                 patch.object(capture.os, "open") as opened, \
                 patch.object(capture.Path, "mkdir") as mkdir, \
                 contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    capture.main()
                self.assertEqual(error.exception.code, 2)
                opened.assert_not_called()
                mkdir.assert_not_called()

    def test_read_failure_preserves_partial_and_cleans_up(self):
        self.run_capture([b"partial"], OSError("read failed"))

    def test_disconnect_no_retry(self):
        self.run_capture([], OSError("disconnected"))

    def test_eof_no_retry(self):
        self.run_capture([b""], OSError("unused"))

    def test_configuration_failure_cleans_up(self):
        self.run_capture(configuration_failure=True)

    def test_dry_run_has_no_filesystem_or_serial_side_effects(self):
        with patch("sys.argv", ["capture_serial.py", "--dry-run", "--duration", "300"]), \
             patch.object(capture.os, "open") as opened, \
             patch.object(capture.Path, "mkdir") as mkdir, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(capture.main(), 0)
            opened.assert_not_called()
            mkdir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
