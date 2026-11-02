"""Offline only: compile portable production parser/JSON against pinned decoder."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
VENDOR = ROOT / "components/odid/vendor"
CORE = ROOT / "components/receiver"
RID_MAX_FRAME = 2332  # components/receiver/include/rid_frame.h


def compile_command(output, *extra):
    return ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-DODID_DISABLE_PRINTF",
            "-I" + str(VENDOR), "-I" + str(CORE / "include"), *extra,
            str(CORE / "rid_frame.c"), str(CORE / "rid_scan.c"), str(CORE / "receiver_json.c"),
            str(VENDOR / "opendroneid.c"), str(TESTS / "host_adapter.c"), "-lm", "-o", str(output)]


class ReceiverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build = TESTS / ".build"
        build.mkdir(exist_ok=True)
        subprocess.run(compile_command(build / "receiver.so", "-shared", "-fPIC"), check=True)
        cls.lib = ctypes.CDLL(str(build / "receiver.so"))
        cls.lib.test_decode.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        for name in ("test_status", "test_message"):
            getattr(cls.lib, name).restype = ctypes.c_int
        cls.beacon = bytes.fromhex((TESTS / "fixtures/synthetic-beacon.hex").read_text())
        cls.nan = bytes.fromhex((TESTS / "fixtures/synthetic-nan.hex").read_text())

    def decode(self, frame, channel=149, size=3072):
        buf = ctypes.create_string_buffer(size)
        candidate, decoded = ctypes.c_int(), ctypes.c_int()
        result = self.lib.test_decode(frame, len(frame), buf, size, channel,
                                      ctypes.byref(candidate), ctypes.byref(decoded))
        return result, json.loads(buf.value) if buf.value else None, candidate.value, decoded.value

    def test_reset_reason_on_every_record_and_legacy_default(self):
        self.lib.rid_json_set_reset_reason(6)
        try:
            self.assertEqual(self.decode(self.beacon)[1]["reset_reason"], 6)
            self.assertEqual(self.decode(self.nan)[1]["reset_reason"], 6)
            buf = ctypes.create_string_buffer(3072)
            self.assertEqual(self.lib.test_status(buf, len(buf)), 1)
            self.assertEqual(json.loads(buf.value)["reset_reason"], 6)
            for which in (0, 1):
                self.assertEqual(self.lib.test_message(which, buf, len(buf)), 1)
                self.assertEqual(json.loads(buf.value)["reset_reason"], 6)
        finally:
            self.lib.rid_json_set_reset_reason(0)
        self.assertEqual(self.decode(self.beacon)[1]["reset_reason"], 0)

    def test_beacon_and_nan_same_pack(self):
        for frame, transport in ((self.beacon, "wifi_beacon"), (self.nan, "wifi_nan")):
            with self.subTest(transport=transport):
                result, obj, candidate, decoded = self.decode(frame)
                self.assertEqual((result, candidate, decoded), (3, 1, 3))
                self.assertEqual(obj["transport"], transport)
                self.assertEqual(obj["basic_id"]["value"], "SYNTH-C5-TEST")
                self.assertEqual(obj["aircraft"]["latitude"], 38)
                self.assertEqual(obj["aircraft"]["longitude"], -90)
                self.assertEqual(obj["aircraft"]["altitude_hae_m"], 123.5)
                self.assertEqual(obj["operator"]["location_type"], 0)
                self.assertAlmostEqual(obj["operator"]["location"]["latitude"], 38.1)
                self.assertIsNone(obj["receiver_utc"])
                self.assertEqual(obj["received_uptime_us"], 1000000)
                self.assertEqual(obj["frequency_mhz"], 5745)
                self.assertFalse(obj["authenticated"])

    def test_two_bands_and_invalid_channel(self):
        self.assertEqual(self.decode(self.beacon, 6)[1]["frequency_mhz"], 2437)
        self.assertIsNone(self.decode(self.beacon, 52)[1])

    def test_all_truncated_prefixes_do_not_emit(self):
        for frame in (self.beacon, self.nan):
            for end in range(len(frame)):
                self.assertIsNone(self.decode(frame[:end])[1], end)

    def test_unrelated_beacon(self):
        frame = bytes.fromhex((TESTS / "fixtures/unrelated-beacon.hex").read_text())
        self.assertEqual(self.decode(frame), (0, None, 0, 0))

    def test_subtype_classified_before_header_checks(self):
        # Synthetic copy of the 615-byte VHT compressed beamforming Action No Ack
        # layout seen on channel 149: subtype 14 with nonzero fragment bits.
        frame = bytearray(b'\xe0\x00\x20\x00' + bytes.fromhex('020000000001 020000000002') + bytes(6))
        frame += b'\x24\x00\x15\x00\x90\x84\x90' + bytes(range(256)) * 2 + bytes(74)
        self.assertEqual(len(frame), 615)
        self.assertEqual(self.decode(bytes(frame)), (0, None, 0, 0))
        for frame in (self.beacon, self.nan):
            result, obj, candidate, decoded = self.decode(frame)
            self.assertEqual((result, candidate, decoded), (3, 1, 3))
            self.assertEqual(obj["basic_id"]["value"], "SYNTH-C5-TEST")
            data = bytearray(frame); data[22] |= 4  # fragment number 4
            self.assertEqual(self.decode(bytes(data))[0], 1)
            self.assertIsNone(self.decode(bytes(data))[1])

    def test_ie_length_and_duplicate_pack_rejected(self):
        data = bytearray(self.beacon); data[37] = 255
        self.assertEqual(self.decode(bytes(data))[0], 1)
        self.assertEqual(self.decode(self.beacon + self.beacon[36:])[0], 1)

    def test_nan_lengths_and_unsupported_options(self):
        for offset, value in ((31, 255), (32, 255), (41, 0x14), (42, 255)):
            data = bytearray(self.nan); data[offset] = value
            self.assertIsNone(self.decode(bytes(data))[1])

    def test_malformed_pack_and_unsupported_version(self):
        for offset, value, result in ((43,0xf3,4),(44,24,1),(45,255,1),(46,0xf2,1),(46,0x03,3),(46,0x62,3)):
            data=bytearray(self.beacon); data[offset]=value
            self.assertEqual(self.decode(bytes(data))[0],result)
            self.assertIsNone(self.decode(bytes(data))[1])

    def test_versions_reserved_members_and_padding_fixtures(self):
        for transport in ('beacon','nan'):
            for case in ('v0','v1','v2','reserved','padding','future-member'):
                frame=bytes.fromhex((TESTS/'fixtures'/f'synthetic-{transport}-{case}.hex').read_text())
                result,obj,candidate,decoded=self.decode(frame)
                self.assertEqual((result,candidate),(3,1),(transport,case))
                self.assertEqual(obj['basic_id']['value'],'SYNTH-C5-TEST')
                if case.startswith('v'): self.assertEqual(obj['odid_protocol_version'],int(case[1:]))
                self.assertEqual(obj['aircraft']['latitude'],38)
                self.assertEqual(obj['aircraft']['longitude'],-90)
                self.assertEqual(decoded,2 if case=='future-member' else 3)
                if case in ('v0','v1'):
                    self.assertIsNone(obj['operator']['timestamp_seconds_since_2019'])
                if case=='v0': self.assertIsNone(obj['operator']['location']['altitude_hae_m'])
                if case=='future-member':
                    self.assertEqual(obj['operator']['location']['validity'],'unavailable')
            for prefix,case,result in (('unsupported','v3',4),('malformed','padding',1)):
                frame=bytes.fromhex((TESTS/'fixtures'/f'{prefix}-{transport}-{case}.hex').read_text())
                self.assertEqual(self.decode(frame)[0],result)
                self.assertIsNone(self.decode(frame)[1])

    def test_new_fixture_truncations_fail_closed(self):
        for path in (TESTS/'fixtures').glob('synthetic-*-*.hex'):
            frame=bytes.fromhex(path.read_text())
            for end in range(len(frame)):
                self.assertIsNone(self.decode(frame[:end])[1],(path.name,end))

    def test_maximum_pack_count_with_reserved_slots(self):
        data=bytearray(self.beacon)
        data.extend((b'\x62'+bytes(24))*6)
        data[45]=9; data[37]+=150
        self.assertIsNotNone(self.decode(bytes(data))[1])
        data[45]=10
        self.assertEqual(self.decode(bytes(data))[0],1)

    def test_legacy_reserved_system_bytes_never_become_new_fields(self):
        for version in (0,1):
            data=bytearray(self.beacon); data[96]=0x40|version
            obj=self.decode(bytes(data))[1]
            self.assertIsNone(obj['operator']['timestamp_seconds_since_2019'])
            if version==0: self.assertIsNone(obj['operator']['location']['altitude_hae_m'])

    def test_reserved_only_and_truncated_declared_slots(self):
        data=bytearray(self.beacon[:71]); data[37]=33; data[45]=1; data[46]=0x62
        self.assertEqual(self.decode(bytes(data)),(5,None,1,0))
        data[45]=2
        self.assertEqual(self.decode(bytes(data))[0],1)

    def test_duplicate_singleton_cannot_overwrite_location(self):
        data=bytearray(self.beacon); data[96:121]=data[71:96]
        self.assertEqual(self.decode(bytes(data))[0],2)
        self.assertIsNone(self.decode(bytes(data))[1])

    def test_diagnostic_classification_and_legacy_counters(self):
        self.lib.test_counters.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t]
        def counters(frame):
            buf=ctypes.create_string_buffer(3072)
            self.assertEqual(self.lib.test_counters(frame,len(frame),buf,len(buf)),1)
            return json.loads(buf.value)['counters']
        c=counters(self.beacon)
        self.assertEqual((c['rf_frames'],c['candidate_frames'],c['decoded_packs'],c['decoded_messages']),(1,1,1,3))
        for key in ('malformed_frames','unsupported_versions','decode_failures','queue_drops','usb_output_drops','output_errors'):
            self.assertEqual(c[key],0)
        for name,key in (('unsupported-beacon-v3','unsupported_versions'),('malformed-beacon-padding','malformed_frames')):
            frame=bytes.fromhex((TESTS/'fixtures'/f'{name}.hex').read_text())
            c=counters(frame)
            self.assertEqual(c[key],1)
            self.assertEqual(c['decoded_packs'],0)
            self.assertEqual(c['errors'],int(key=='malformed_frames'))
        data=bytearray(self.beacon);data[96]=0x22;data[97]=0;data[98]=255
        c=counters(bytes(data))
        self.assertEqual((c['decode_failures'],c['errors'],c['decoded_packs']),(1,1,0))
        self.assertIsNone(self.decode(bytes(data))[1])
        frame=bytes.fromhex((TESTS/'fixtures/synthetic-beacon-future-member.hex').read_text())
        c=counters(frame)
        self.assertEqual((c['unsupported_versions'],c['skipped_messages'],c['decoded_packs'],c['decoded_messages'],c['errors']),(1,1,1,2,0))

    def test_production_queue_drain_and_host_throughput(self):
        output=TESTS/'.build/drain'
        cmd=compile_command(output,'-fsanitize=address,undefined','-fno-omit-frame-pointer','-g')
        cmd.remove(str(TESTS/'host_adapter.c'))
        cmd.insert(-3,str(CORE/'rid_drain.c'))
        cmd.insert(-3,str(TESTS/'drain_harness.c'))
        subprocess.run(cmd,check=True)
        env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0:halt_on_error=1',UBSAN_OPTIONS='halt_on_error=1')
        subprocess.run([str(output),str(TESTS/'fixtures/synthetic-beacon.hex')],check=True,env=env)

    def test_zero_aircraft_not_replaced_by_operator(self):
        data = bytearray(self.beacon)
        data[76:84] = bytes(8)  # Location lat/lon, retains valid System location
        self.assertIsNone(self.decode(bytes(data))[1])

    def test_independent_operator_unavailable(self):
        data = bytearray(self.beacon)
        data[98:106] = bytes(8)  # System lat/lon
        obj = self.decode(bytes(data))[1]
        self.assertEqual(obj["aircraft"]["validity"], "valid")
        self.assertEqual(obj["operator"]["location"]["validity"], "unavailable")
        self.assertIsNone(obj["operator"]["location"]["latitude"])

    def test_invalid_operator_does_not_invalidate_aircraft(self):
        data = bytearray(self.beacon)
        data[98:102] = int(100 * 1e7).to_bytes(4, "little", signed=True)
        obj = self.decode(bytes(data))[1]
        self.assertEqual(obj["operator"]["location"]["validity"], "invalid")
        self.assertIsNone(obj["operator"]["location"]["longitude"])
        self.assertEqual(obj["aircraft"]["validity"], "valid")

    def test_absent_system_and_location(self):
        data = bytearray(self.beacon[:-25]); data[37] -= 25; data[45] = 2
        obj = self.decode(bytes(data))[1]
        self.assertEqual(obj["operator"]["location"]["validity"], "unavailable")
        self.assertIsNone(obj["operator"]["location_type"])
        data = bytearray(self.beacon[:71] + self.beacon[96:]); data[37] -= 25; data[45] = 2
        self.assertIsNone(self.decode(bytes(data))[1])

    def test_timestamp_and_altitude_sentinels(self):
        data = bytearray(self.beacon)
        data[92:94] = b'\xff\xff'
        data[86:88] = bytes(2)  # -1000 HAE sentinel
        data[116:120] = bytes(4)  # unknown System timestamp
        obj = self.decode(bytes(data))[1]
        self.assertIsNone(obj["location_timestamp_seconds_after_hour"])
        self.assertIsNone(obj["aircraft"]["altitude_hae_m"])
        self.assertIsNone(obj["operator"]["timestamp_seconds_since_2019"])

    def test_astm_oui_and_unrelated_nan(self):
        data = bytearray(self.beacon); data[38:41] = b'\x90\x3a\xe6'
        self.assertIsNotNone(self.decode(bytes(data))[1])
        data = bytearray(self.nan); data[33] = 0
        self.assertEqual(self.decode(bytes(data)), (0, None, 0, 0))

    def test_duplicate_basic_id_and_oversize_frame(self):
        data = bytearray(self.beacon)
        data[96:121] = data[46:71]  # second identical ID type instead of System
        self.assertEqual(self.decode(bytes(data))[0], 2)
        self.assertEqual(self.decode(self.beacon + bytes(RID_MAX_FRAME))[0], 1)

    @staticmethod
    def pad(frame, total):
        """Append non-RID vendor IEs so the beacon is exactly total bytes."""
        data, deficit = bytearray(frame), total - len(frame)
        assert deficit >= 2
        while deficit:
            take = min(257, deficit)
            if deficit - take == 1: take -= 2
            data += bytes([221, take - 2]) + bytes(take - 2)
            deficit -= take
        return bytes(data)

    def test_frame_length_boundaries(self):
        self.assertEqual(len(self.pad(self.beacon, RID_MAX_FRAME)), RID_MAX_FRAME)
        result, obj, _, _ = self.decode(self.pad(self.beacon, RID_MAX_FRAME))
        self.assertEqual((result, obj["basic_id"]["value"]), (3, "SYNTH-C5-TEST"))  # largest valid frame decodes
        self.assertEqual(self.decode(self.pad(self.beacon, RID_MAX_FRAME + 1))[0], 1)
        self.assertEqual(self.decode(self.pad(self.beacon, 1100))[0], 3)  # dropped as oversize before this change

    def test_capture_length_check(self):
        self.lib.test_capture.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_size_t)]
        OK, BAD, OVERSIZE = 0, 1, 2
        for sig, dump, verdict, mpdu in ((28, 32, OK, 24), (27, 31, BAD, 23), (0, 0, BAD, 0), (3, 7, BAD, 0),
                                         (100, 99, BAD, 96), (RID_MAX_FRAME + 4, RID_MAX_FRAME + 8, OK, RID_MAX_FRAME),
                                         (RID_MAX_FRAME + 5, RID_MAX_FRAME + 9, OVERSIZE, RID_MAX_FRAME + 1),
                                         (RID_MAX_FRAME + 5, RID_MAX_FRAME + 4, BAD, RID_MAX_FRAME + 1),
                                         (16383, 16387, OVERSIZE, 16379), (1028, 1032, OK, 1024), (1029, 1033, OK, 1025)):
            n = ctypes.c_size_t(12345)
            self.assertEqual((self.lib.test_capture(sig, dump, ctypes.byref(n)), n.value), (verdict, mpdu), (sig, dump))

    def reception(self, frame):
        self.lib.test_reception.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
        buf = ctypes.create_string_buffer(3072)
        emitted = self.lib.test_reception(frame, len(frame), buf, len(buf))
        return json.loads(buf.value) if emitted else None

    def test_reception_records_valid_untrackable_remote_id(self):
        self.assertIsNone(self.reception(self.beacon))  # trackable: observation, not reception
        no_fix = bytearray(self.beacon); no_fix[76:84] = bytes(8)  # aircraft 0,0 before GPS fix
        uuid = bytearray(self.beacon); uuid[47] = 0x32           # UTM UUID: binary, not textual
        no_id = bytearray(self.beacon[:46] + self.beacon[71:]); no_id[37] -= 25; no_id[45] = 2
        caa = bytearray(self.beacon[46:71]); caa[1] = 0x22; caa[2:22] = b"SYNTH-CAA".ljust(20, b"\0")
        two = bytearray(self.beacon + bytes(caa)); two[37] += 25; two[45] = 4
        for frame, reason, location, messages, ids in (
                (no_fix, "no_valid_position", "unavailable", ["basic_id", "location", "system"], [(1, "SYNTH-C5-TEST")]),
                (uuid, "no_textual_id", "valid", ["basic_id", "location", "system"], [(3, None)]),
                (no_id, "no_basic_id", "valid", ["location", "system"], []),
                (two, "ambiguous_id", "valid", ["basic_id", "location", "system"], [(1, "SYNTH-C5-TEST"), (2, "SYNTH-CAA")])):
            with self.subTest(reason=reason):
                self.assertIsNone(self.decode(bytes(frame))[1])  # never an observation/track
                obj = self.reception(bytes(frame))
                self.assertEqual((obj["type"], obj["reason"], obj["aircraft_location"], obj["trackable"]), ("reception", reason, location, False))
                self.assertEqual(obj["messages"], messages)
                self.assertEqual([(b["id_type"], b["value"]) for b in obj["basic_ids"]], ids)
                self.assertEqual((obj["transport"], obj["channel"], obj["frequency_mhz"], obj["authenticated"]), ("wifi_beacon", 149, 5745, False))
                self.assertGreaterEqual(obj["decoded_messages"], 2)
                self.assertNotIn("latitude", json.dumps(obj))  # no coordinates in reception records

    def test_reception_matches_host_fixture_contract(self):
        no_fix = bytearray(self.beacon); no_fix[76:84] = bytes(8)
        firmware = self.reception(bytes(no_fix))
        host = json.loads((ROOT.parents[1] / "linux-bridge/src/test/resources/dronk/reception.json").read_text())
        envelope = ("receiver_id", "session_id", "seq")
        self.assertEqual(list(firmware), list(host))  # same fields in the same order
        self.assertEqual({k: v for k, v in firmware.items() if k not in envelope},
                         {k: v for k, v in host.items() if k not in envelope})

    def test_candidates_malformed_and_failures_never_produce_reception(self):
        bad_ie = bytearray(self.beacon); bad_ie[37] = 255
        decode_error = bytearray(self.beacon); decode_error[96] = 0x22; decode_error[97] = 0; decode_error[98] = 255
        names = ("unsupported-beacon-v3", "malformed-beacon-padding", "unrelated-beacon", "unsupported-nan-v3", "malformed-nan-padding")
        frames = [bytes(bad_ie), bytes(decode_error), self.beacon[:60], self.pad(self.beacon, RID_MAX_FRAME + 1)]
        frames += [bytes.fromhex((TESTS / "fixtures" / f"{n}.hex").read_text()) for n in names]
        for frame in frames:
            self.assertIsNone(self.reception(frame))
            self.assertIsNone(self.decode(frame)[1])

    def test_reception_worst_case_fits_host_line_limit(self):
        self.lib.test_reception_worst_case.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        buf = ctypes.create_string_buffer(3072)
        self.assertEqual(self.lib.test_reception_worst_case(buf, len(buf)), 1)
        line = buf.value.rstrip(b"\n")
        self.assertLessEqual(len(line), 3070)
        obj = json.loads(line)
        self.assertEqual(obj["messages"], ["basic_id", "location", "auth", "self_id", "system", "operator_id"])
        self.assertEqual(obj["aircraft_location"], "invalid")

    def test_unsupported_or_empty_aircraft_id(self):
        for id_type in (0x32, 0x42):
            data = bytearray(self.beacon); data[47] = id_type
            self.assertIsNone(self.decode(bytes(data))[1])
        data = bytearray(self.beacon); data[48:68] = bytes(20)
        self.assertIsNone(self.decode(bytes(data))[1])

    def test_no_cross_packet_state(self):
        self.assertIsNotNone(self.decode(self.beacon)[1])
        data = bytearray(self.beacon); data[48:68] = bytes(20)
        self.assertIsNone(self.decode(bytes(data))[1])

    def test_escaped_id_and_small_output_buffer(self):
        data = bytearray(self.beacon); data[48:52] = b'A"\\B'
        obj = self.decode(bytes(data))[1]
        self.assertTrue(obj["basic_id"]["value"].startswith('A"\\B'))
        self.assertIsNone(self.decode(self.beacon, size=50)[1])

    def test_fragmented_and_protected_frames_rejected(self):
        for offset, value in ((1, 4), (1, 0x40), (22, 1)):
            data = bytearray(self.beacon); data[offset] = value
            self.assertIsNone(self.decode(bytes(data))[1])

    def test_status_counters_and_envelope_escaping(self):
        buf = ctypes.create_string_buffer(3072)
        self.assertEqual(self.lib.test_status(buf, len(buf)), 1)
        obj = json.loads(buf.value)
        self.assertEqual(obj["receiver_id"], 'quote"receiver')
        self.assertEqual(obj["session_id"], "session\\id")
        self.assertEqual(obj["counters"]["rf_frames"], 20)
        self.assertEqual(obj["counters"]["decoded_messages"], 15)
        self.assertEqual(obj["counters"]["queue_drops"], 7)
        self.assertEqual(obj["version"], 1)

    def test_hello_and_error(self):
        for which, kind in ((0, "hello"), (1, "error")):
            buf = ctypes.create_string_buffer(3072)
            self.assertEqual(self.lib.test_message(which, buf, len(buf)), 1)
            obj = json.loads(buf.value)
            self.assertEqual(obj["type"], kind)
            if not which:
                self.assertEqual(obj["firmware"], "C5-0.3-scan")
                self.assertFalse(obj["hardware_reception_verified"])
                self.assertTrue(obj["passive"])
                self.assertFalse(obj["hopping"])
                self.assertEqual(obj["requested_channel"], 6)
        buf = ctypes.create_string_buffer(3072)
        self.assertEqual(self.lib.test_message(2, buf, len(buf)), 1)
        obj = json.loads(buf.value)
        self.assertTrue(obj["hopping"])
        self.assertIsNone(obj["requested_channel"])

    def test_sanitizer_corpus(self):
        binary = TESTS / ".build/sanitize"
        subprocess.run(compile_command(binary, "-DSTANDALONE", "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g"), check=True)
        # This corpus exercises memory bounds/UB, not heap ownership. LeakSanitizer
        # cannot inspect threads in some sandbox/ptrace environments.
        env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0:halt_on_error=1", UBSAN_OPTIONS="halt_on_error=1")
        subprocess.run([str(binary), *map(str, sorted((TESTS / "fixtures").glob("*.hex")))], check=True, env=env)


class ScanSchedulerTests(unittest.TestCase):
    """Pure scheduler and scan telemetry; no radio, clock or FreeRTOS."""
    HOP = [6, 149, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 36, 40, 44, 48, 153, 157, 161, 165]
    SWEEP = [c for c in HOP if c not in (6, 149)]

    @classmethod
    def setUpClass(cls):
        build = TESTS / ".build"
        build.mkdir(exist_ok=True)
        subprocess.run(compile_command(build / "scan.so", "-shared", "-fPIC"), check=True)
        lib = cls.lib = ctypes.CDLL(str(build / "scan.so"))
        p, u, i64, b = ctypes.c_void_p, ctypes.c_uint, ctypes.c_int64, ctypes.c_bool
        for name, args, res in (("rid_scan_init", [p, b, u, u, u], b), ("rid_scan_next", [p, i64], u),
                                ("rid_scan_tuned", [p, u, i64, ctypes.c_uint32], None),
                                ("rid_scan_result", [p, u, b, b, i64], None), ("rid_scan_frame", [p, u], None),
                                ("rid_scan_index", [p, u], ctypes.c_int), ("rid_channel_supported", [u], b),
                                ("test_scan_status", [p, i64, ctypes.c_char_p, ctypes.c_size_t], ctypes.c_int),
                                ("test_status_worst_case", [ctypes.c_char_p, ctypes.c_size_t], ctypes.c_int),
                                ("test_scan_size", [], ctypes.c_size_t)):
            getattr(lib, name).argtypes = args
            getattr(lib, name).restype = res

    def scan(self, hopping=True, channel=6, dwell=250, ttl=60):
        s = ctypes.create_string_buffer(self.lib.test_scan_size())
        self.assertTrue(self.lib.rid_scan_init(s, hopping, channel, dwell, ttl))
        return s

    def status(self, s, now):
        buf = ctypes.create_string_buffer(3072)
        self.assertEqual(self.lib.test_scan_status(s, now, buf, len(buf)), 1)
        return json.loads(buf.value)["scan"]

    def run_slots(self, s, count, start=0, step=250_000):
        return [self.lib.rid_scan_next(s, start + i * step) for i in range(count)]

    def test_default_hop_plan_is_us_non_dfs_with_anchors(self):
        scan = self.status(self.scan(), 0)
        self.assertEqual(scan["mode"], "hop")
        self.assertEqual([c[0] for c in scan["channels"]], self.HOP)
        for channel in self.HOP:
            self.assertTrue(self.lib.rid_channel_supported(channel))
            self.assertFalse(52 <= channel <= 144 or 12 <= channel <= 14)

    def test_slot_pattern_anchor_share_and_full_sweep(self):
        slots = self.run_slots(self.scan(), 54)
        self.assertEqual([slots[i] for i in range(54) if i % 6 in (0, 3)], [6] * 18)
        self.assertEqual([slots[i] for i in range(54) if i % 6 in (1, 4)], [149] * 18)
        self.assertEqual([slots[i] for i in range(54) if i % 6 in (2, 5)], self.SWEEP)  # every channel in 9 cycles
        self.assertEqual(self.run_slots(self.scan(), 108)[54:], slots)

    def test_hot_channel_revisited_until_ttl_without_stopping_sweep(self):
        s = self.scan(ttl=60)
        self.lib.rid_scan_result(s, 36, True, False, 0)
        slots = self.run_slots(s, 60, step=1_000_000)  # 60 slots spanning the 60 s TTL
        hot_slots = [slots[i] for i in range(0, 60) if i % 6 == 5]
        self.assertEqual(hot_slots, [36] * 10)
        self.assertEqual([slots[i] for i in range(60) if i % 6 == 2], self.SWEEP[:10])  # sweep keeps advancing
        self.assertNotIn(36, [slots[i] for i in range(60) if i % 6 != 5][:30])
        later = self.run_slots(s, 6, start=61_000_000)
        self.assertEqual(later[:5], [6, 149, self.SWEEP[10], 6, 149])
        self.assertEqual(later[5], self.SWEEP[11])  # expired: back to sweeping

    def test_multiple_hot_channels_share_the_hot_slot(self):
        s = self.scan()
        for channel in (11, 157):
            self.lib.rid_scan_result(s, channel, True, True, 0)
        hot = [c for i, c in enumerate(self.run_slots(s, 24, step=1000)) if i % 6 == 5]
        self.assertEqual(sorted(hot[:2]), [11, 157])
        self.assertEqual(hot[2:], hot[:2])

    def test_anchor_noncandidate_and_unknown_channels_do_not_change_schedule(self):
        baseline = self.run_slots(self.scan(), 24)
        s = self.scan()
        self.lib.rid_scan_result(s, 6, True, True, 0)      # anchors are already visited
        self.lib.rid_scan_result(s, 36, False, False, 0)   # RF without a candidate
        self.lib.rid_scan_result(s, 52, True, True, 0)     # not a scan channel
        self.lib.rid_scan_frame(s, 52)
        self.assertEqual(self.lib.rid_scan_index(s, 52), -1)
        self.assertEqual(self.run_slots(s, 24), baseline)

    def test_fixed_mode_and_invalid_configuration(self):
        s = self.scan(hopping=False, channel=149)
        self.assertEqual(set(self.run_slots(s, 30)), {149})
        self.assertEqual(self.status(s, 0)["mode"], "fixed")
        buf = ctypes.create_string_buffer(self.lib.test_scan_size())
        for hopping, channel, dwell, ttl in ((False, 52, 250, 60), (False, 14, 250, 60), (False, 0, 250, 60),
                                             (True, 6, 49, 60), (True, 6, 2001, 60), (True, 6, 250, 4), (True, 6, 250, 601)):
            self.assertFalse(self.lib.rid_scan_init(buf, hopping, channel, dwell, ttl), (hopping, channel, dwell, ttl))
        self.assertTrue(self.lib.rid_scan_init(buf, True, 52, 250, 60))  # hop ignores the fixed channel

    def test_dwell_switch_and_decode_telemetry(self):
        s = self.scan()
        self.lib.rid_scan_tuned(s, 6, 0, 120)
        self.lib.rid_scan_tuned(s, 149, 250_000, 300)
        for _ in range(3): self.lib.rid_scan_frame(s, 149)
        self.lib.rid_scan_result(s, 149, True, True, 300_000)
        self.lib.rid_scan_result(s, 149, True, False, 350_000)
        self.lib.rid_scan_tuned(s, 999, 360_000, 1)  # unknown channel: tune error, state unchanged
        scan = self.status(s, 2_400_000)
        rows = {c[0]: c for c in scan["channels"]}
        self.assertEqual(rows[6][1:], [250, 0, 0, 0, None, 0])
        self.assertEqual(rows[149], [149, 2150, 3, 2, 1, 2, 1])
        self.assertEqual((scan["current"], scan["on_channel_ms"], scan["switches"], scan["tune_errors"]), (149, 2150, 2, 1))
        self.assertEqual((scan["switch_us_last"], scan["switch_us_max"]), (300, 300))

    def test_worst_case_status_fits_host_line_limit(self):
        buf = ctypes.create_string_buffer(3072)
        self.assertEqual(self.lib.test_status_worst_case(buf, len(buf)), 1)
        line = buf.value.rstrip(b"\n")
        self.assertLessEqual(len(line), 3070)  # DronkReceiverHub rejects longer lines
        obj = json.loads(line)
        self.assertEqual(len(obj["scan"]["channels"]), 20)
        self.assertEqual(obj["scan"]["channels"][0][1], 4294967295)


if __name__ == "__main__":
    unittest.main()
