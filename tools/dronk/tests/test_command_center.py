import copy
import http.client
import importlib.util
import json
import os
from pathlib import Path
import struct
import socket
import urllib.request
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from test_support import IsolatedConfigTestCase
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import command_center as cc
import serial_sources as serial

PROCESSOR = cc.ROOT / "linux-bridge/build/install/linux-bridge/bin/linux-bridge"
FIXTURE = cc.ROOT / "linux-bridge/src/test/resources/dronk/observation.json"


class DashboardTests(IsolatedConfigTestCase):
    def setUp(self):
        super().setUp()
        self.state = cc.State()
        self.server = cc.make_server(self.state, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.stopping.set(); self.server.shutdown(); self.server.server_close()
        self.thread.join()

    def get(self, path, headers=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        client.request("GET", path, headers=headers or {})
        response = client.getresponse()
        data = response.read()
        result = response.status, dict(response.getheaders()), data
        client.close()
        return result

    def test_static_page_and_security_headers(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"DRONK Command Center", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.get("/../command_center.py")[0], 404)

    def test_disconnected_and_export(self):
        self.assertEqual(json.loads(self.get("/api/state")[2])["mode"], "DISCONNECTED")
        self.assertEqual(json.loads(self.get("/api/export")[2]), [])
        self.assertTrue(self.get("/api/export?format=csv")[2].startswith(b"aircraft_id,identifier_type,state,position_valid,"))
        self.assertEqual(json.loads(self.get("/api/export?format=json&table=history")[2]), [])
        self.assertIn(b"received_at", self.get("/api/export?format=csv&table=history")[2])
        self.assertEqual(self.get("/api/export?table=raw")[0], 400)

    def test_host_origin_and_control_routes_rejected(self):
        self.assertEqual(self.get("/api/state", {"Host": "evil.example"})[0], 403)
        self.assertEqual(self.get("/api/state", {"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.get("/api/state", {"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.get("/api/control")[0], 404)
        c = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        c.request("POST", "/api/control", b"reset")
        self.assertEqual(c.getresponse().status, 403); c.close()

    def test_sse_snapshot(self):
        c = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        c.request("GET", "/events")
        r = c.getresponse()
        self.assertEqual(r.status, 200)
        self.assertEqual(r.readline().strip(), b"event: snapshot")
        self.assertEqual(json.loads(r.readline().decode().removeprefix("data: "))["mode"], "DISCONNECTED")
        c.close()

    def test_history_bounded_and_expired_tracks_hidden(self):
        a = dict(uid="u", id="=DANGEROUS()", synthetic=False, position=None, source="DRONK_C5_USB",
                 observed_at="2020-01-01T00:00:00Z", received_at="2020-01-01T00:00:00Z", rf=None)
        for _ in range(1005):
            self.state.apply(dict(kind="observation", aircraft=a, expires_at="2020-01-01T00:01:00Z"))
        self.assertEqual(len(self.state.snapshot()["history"]), 1000)
        self.assertEqual(self.state.snapshot()["aircraft"], [])
        self.assertIn(b"'=DANGEROUS()", cc.export_csv([a]))
        self.assertIsNone(self.state.snapshot()["history"][0]["position"])

    def test_synthetic_isolation(self):
        with self.assertRaises(ValueError):
            self.state.apply(dict(kind="observation", aircraft=dict(synthetic=True)))

    def test_control_requires_origin_token_and_only_fixed_actions(self):
        controller = unittest.mock.Mock()
        self.server.controller = controller
        token = json.loads(self.get("/api/control-token")[2])["token"]
        origin = f"http://127.0.0.1:{self.server.server_port}"
        def post(body, csrf=token, source=origin):
            c = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
            c.request("POST", "/api/control", json.dumps(body), {"Origin":source,"X-DRONK-CSRF":csrf,"Content-Type":"application/json"})
            response=c.getresponse(); response.read(); c.close()
            return response.status
        self.assertEqual(post({"action":"start"}, csrf="wrong"),403)
        self.assertEqual(post({"action":"start"}, source="https://evil.example"),403)
        self.assertEqual(post({"action":"flash"}),400)
        controller.start.assert_not_called()
        self.assertEqual(post({"action":"start"}),200)
        self.assertEqual(post({"action":"stop"}),200)
        controller.start.assert_called_once(); controller.stop.assert_called_once()
        self.assertEqual(self.get("/api/control?action=start")[0],404)


class HostProcessTests(IsolatedConfigTestCase):
    def config(self, **kw):
        return dict(demo=False, devices={}, expected={}, processor=str(PROCESSOR), forward_live=False, duration=0, **kw)

    def wait_for(self, predicate):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            if predicate(): return
            time.sleep(.02)
        self.fail("Timed out waiting for host process state")

    def test_console_launch_serves_stopped_without_worker(self):
        with socket.socket() as available:
            available.bind(('127.0.0.1',0))
            port=available.getsockname()[1]
        child=subprocess.Popen([sys.executable,str(HERE/'command_center.py'),'--demo','--port',str(port)],stdout=subprocess.PIPE)
        try:
            def ready():
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/state',timeout=.2) as response:
                        snapshot=json.load(response)
                    return snapshot['host']['state']=='STOPPED' and snapshot['host']['pid'] is None
                except OSError: return False
            self.wait_for(ready)
            children=Path(f'/proc/{child.pid}/task/{child.pid}/children').read_text().strip()
            self.assertEqual(children,'')
        finally:
            child.terminate()
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait(); raise
            child.stdout.close()

    def test_manual_start_stop_restart_closes_actual_pty_process(self):
        master, slave=os.openpty(); path=os.ttyname(slave)
        state=cc.State(); config=self.config(); config['devices']={'test':path}; config['receiver_aliases']={'test':'north'}
        control=cc.HostController(state,config)
        try:
            self.assertEqual(state.snapshot()['host']['state'],'STOPPED')
            self.assertIsNone(control.child)
            control.start(); child=control.child
            self.wait_for(lambda: any(r.get('connected') for r in state.snapshot()['receivers']))
            fixture=json.loads(FIXTURE.read_text()); fixture['reset_reason']=1
            os.write(master,(json.dumps(fixture)+'\n').encode())
            self.wait_for(lambda: len(state.snapshot()['history'])==1)
            receiver=state.snapshot()['receivers'][0]
            self.assertEqual(receiver['reset_reason'],1)
            self.assertEqual(receiver['receiver_id'],'north')
            self.assertNotIn('c5-fixture-a',json.dumps(state.snapshot()))
            self.assertGreater(receiver['uptime_us'],0)
            descendants=Path(f"/proc/{child.pid}/task/{child.pid}/children").read_text().split()
            self.assertTrue(descendants)
            pid=child.pid
            self.assertTrue(Path(f'/proc/{pid}').exists())
            control.start(); self.assertEqual(control.child.pid,pid)
            control.stop()
            self.assertIsNotNone(child.poll())
            self.assertFalse(Path(f'/proc/{pid}').exists())
            for descendant in descendants:
                self.assertFalse(Path(f"/proc/{descendant}").exists())
            self.assertEqual(state.snapshot()['host']['state'],'STOPPED')
            self.assertFalse(state.snapshot()['receivers'][0]['connected'])
            self.assertEqual(state.snapshot()['mode'],'DISCONNECTED')
            control.stop(); control.start()
            self.assertNotEqual(control.child.pid,pid)
            self.wait_for(lambda: any(r.get('connected') for r in state.snapshot()['receivers']))
        finally:
            control.stop(); os.close(master); os.close(slave)

    def test_demo_has_no_usb_and_crash_is_not_restarted(self):
        state=cc.State(True); config=self.config(); config['demo']=True
        control=cc.HostController(state,config)
        try:
            control.start(); self.wait_for(lambda: state.snapshot()['mode']=='DEMO')
            child=control.child
            child.kill()
            self.wait_for(lambda: state.snapshot()['host']['state']=='ERROR')
            self.assertIs(control.child,child)
            self.assertEqual(state.snapshot()['mode'],'DISCONNECTED')
        finally: control.stop()

    def test_no_devices_fails_without_process(self):
        control=cc.HostController(cc.State(),self.config())
        with self.assertRaises(ValueError): control.start()
        self.assertIsNone(control.child)


class SerialTests(IsolatedConfigTestCase):
    def setUp(self):
        super().setUp()
        self.fds = []

    def pty(self):
        master, slave = os.openpty(); self.fds += [master, slave]
        return master, os.ttyname(slave)

    def tearDown(self):
        for fd in self.fds:
            try: os.close(fd)
            except OSError: pass

    def test_two_ptys_independent_envelopes_and_no_serial_writes(self):
        a, pa = self.pty(); b, pb = self.pty(); events=[]
        reader=serial.MultiReader([pa, pb], events.append)
        try:
            reader.pump(0)
            os.write(a, b'first\n'); os.write(b, b'second\n')
            reader.pump(.1)
            data={struct.unpack(">BHqI", e[:15])[1]: e[15:] for e in events if e[0]==serial.DATA}
            self.assertEqual(data, {0:b'first\n',1:b'second\n'})
            for s in reader.sources:
                self.assertEqual(os.readlink(f'/proc/self/fd/{s["fd"]}').startswith('/dev/pts/'),True)
        finally: reader.close()

    def test_open_failure_never_retries(self):
        _, path=self.pty()
        reader=serial.MultiReader([path], lambda _:None)
        with patch.object(serial,"open_reader",side_effect=OSError("failed")) as opened:
            reader.pump(0);reader.pump(0)
            self.assertEqual(opened.call_count,1)
            self.assertTrue(reader.sources[0]["fatal"])

    def test_aliases_of_same_device_rejected(self):
        _, path=self.pty()
        with self.assertRaises(ValueError):serial.MultiReader([path,path],lambda _:None)

    def test_read_error_node_present_stops_only_failed_stream(self):
        a,pa=self.pty();b,pb=self.pty();events=[]
        reader=serial.MultiReader([pa,pb],events.append)
        try:
            reader.pump(0);os.write(a,b'error\n')
            with patch.object(serial.os,"read",side_effect=OSError("EIO")):
                reader.pump(.1)
            with patch.object(serial.time,"monotonic",return_value=time.monotonic()+3):
                reader.pump(0)
            self.assertTrue(reader.sources[0]["fatal"])
            self.assertFalse(reader.sources[1]["fatal"])
            self.assertTrue(any(e[0]==serial.ERROR for e in events))
            os.write(b,b'healthy\n');reader.pump(.1)
            self.assertTrue(any(e[0]==serial.DATA and e[15:]==b'healthy\n' for e in events))
        finally:reader.close()

    def test_reconnect_only_after_path_disappears_and_returns(self):
        a,pa=self.pty();b,pb=self.pty();events=[]
        with tempfile.TemporaryDirectory() as directory:
            link=Path(directory)/"receiver";link.symlink_to(pa)
            reader=serial.MultiReader([str(link)],events.append)
            try:
                reader.pump(0);link.unlink();reader.pump(0)
                self.assertIsNone(reader.sources[0]["fd"])
                link.symlink_to(pb)
                with patch.object(serial.time,"monotonic",return_value=time.monotonic()+2):reader.pump(0)
                os.write(b,b'new-epoch\n');reader.pump(.1)
                self.assertEqual(sum(e[0]==serial.CONNECTED for e in events),2)
                self.assertTrue(any(e[0]==serial.DATA and e[15:]==b'new-epoch\n' for e in events))
            finally:reader.close()

    def test_end_to_end_two_receiver_fixture_and_disconnect(self):
        self.assertTrue(PROCESSOR.exists(),"Build :linux-bridge:installDist before integration tests")
        a,pa=self.pty(); b,pb=self.pty()
        child=subprocess.Popen([str(PROCESSOR),"--dronk","--stream","a","--stream","b","--capture-only"],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        lines=[]
        consumer=threading.Thread(target=lambda:[lines.append(json.loads(line)) for line in child.stdout],daemon=True);consumer.start()
        def send(data):child.stdin.write(data);child.stdin.flush()
        reader=serial.MultiReader([pa,pb],send)
        try:
            reader.pump(0)
            fixture=FIXTURE.read_bytes()
            os.write(a,fixture[:35]);reader.pump(.1)
            os.write(b,fixture.replace(b'c5-fixture-a',b'c5-fixture-b').replace(b'SYNTH-C5-TEST',b'OTHER-FIXTURE'));reader.pump(.1)
            os.write(a,fixture[35:]);reader.pump(.1)
            send(serial.envelope(serial.STATUS))
            deadline=time.monotonic()+5
            while sum(e.get("kind")=="observation" for e in lines)<2 and time.monotonic()<deadline:time.sleep(.02)
            observations=[e for e in lines if e.get("kind")=="observation"]
            self.assertEqual(len(observations),2)
            self.assertEqual({e["aircraft"]["rf"]["stream"] for e in observations},{"a","b"})
            self.assertEqual({e["aircraft"]["position"]["latitude"] for e in observations},{38})
            os.write(a,fixture);reader.pump(.1) # sequence replay: no marker
            reader.close();send(serial.envelope(serial.STOP));child.stdin.close();child.wait(timeout=5);consumer.join(timeout=2)
            self.assertEqual(child.returncode,0,child.stderr.read().decode())
            self.assertEqual(sum(e.get("kind")=="observation" for e in lines),2)
        finally:
            try:reader.close()
            except BrokenPipeError:pass
            if child.poll() is None:child.kill();child.wait()
            if not child.stdin.closed:
                try:child.stdin.close()
                except BrokenPipeError:pass
            child.stdout.close();child.stderr.close()


if __name__=="__main__":unittest.main()
