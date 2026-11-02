"""Aircraft detections: identity separate from position, export, dedup, upgrade to tracking, field replay."""
import csv
import io
import json
from pathlib import Path

from test_detection_panel import PanelStateCase, iso, cc

FIELD_LOG = Path(__file__).with_name("fixtures") / "field-2026-11-02-avata-receptions.jsonl"


def rows(state):
    return list(csv.DictReader(io.StringIO(cc.export_detections_csv(state.snapshot()["detections"]).decode())))


class DetectionRegistryTests(PanelStateCase):
    def rid_reception(self, value="SYNTH-NOFIX", reason="no_valid_position", id_type=1, rssi=-50, synthetic=False,
                      basic_ids=None, receiver="front"):
        self.state.apply({"kind": "rid_reception", "reception": {
            "stream": receiver, "receiver_id": receiver, "transmitter": "02:00:00:00:00:09", "transport": "wifi_beacon",
            "channel": 149, "frequency_mhz": 5745, "rssi_dbm": rssi, "reason": reason, "synthetic": synthetic,
            "basic_ids": [{"id_type": id_type, "ua_type": 2, "value": value}] if basic_ids is None else basic_ids,
            "received_at": iso(self.now), "messages": ["basic_id", "location", "system"], "aircraft_location": "unavailable"}})

    def tracked(self, value="SYNTH-NOFIX", lat=40.1, lon=-75.2, synthetic=False):
        self.state.apply({"kind": "observation", "expires_at": iso(self.now + 60), "reason": "test", "aircraft": {
            "uid": "uid-" + value, "id": value, "identifier_type": "SERIAL_NUMBER", "synthetic": synthetic,
            "received_at": iso(self.now), "position": {"latitude": lat, "longitude": lon, "altitude_hae_m": 30.0},
            "rf": {"receiver_id": "front", "transport": "wifi_beacon", "channel": 149, "frequency_mhz": 5745,
                   "rssi_dbm": -48, "transmitter": "02:00:00:00:00:09"}}})

    def detections(self):
        return self.state.snapshot()["detections"]

    def test_basic_id_without_gps_creates_detection(self):
        self.rid_reception()
        d, = self.detections()
        self.assertEqual((d["aircraft_id"], d["identifier_type"], d["state"], d["position_valid"], d["reason"]),
                         ("SYNTH-NOFIX", "SERIAL_NUMBER", "DETECTED_NO_POSITION", False, "no_valid_position"))
        self.assertEqual((d["receiver_id"], d["frequency_mhz"], d["channel"], d["rssi_dbm"], d["transport"]),
                         ("front", 5745, 149, -50, "wifi_beacon"))
        self.assertEqual((d["first_seen"], d["last_seen"], d["observation_count"]), (iso(self.now), iso(self.now), 1))
        self.assertEqual(d["messages"], ["basic_id", "location", "system"])
        self.assertFalse(d["synthetic"])

    def test_detection_in_csv_and_json_exports(self):
        self.rid_reception()
        row, = rows(self.state)
        self.assertEqual(list(row), cc.DETECTION_COLUMNS)
        self.assertEqual((row["aircraft_id"], row["state"], row["position_valid"], row["reason"], row["frequency_mhz"]),
                         ("SYNTH-NOFIX", "DETECTED_NO_POSITION", "False", "no_valid_position", "5745"))
        exported = json.loads(json.dumps(self.detections(), allow_nan=False))
        self.assertEqual(exported[0]["aircraft_id"], "SYNTH-NOFIX")

    def test_repeated_receptions_update_one_detection(self):
        self.rid_reception(rssi=-60)
        first = iso(self.now)
        self.now += 5; self.rid_reception(rssi=-44, receiver="rear")
        d, = self.detections()
        self.assertEqual((d["observation_count"], d["first_seen"], d["last_seen"]), (2, first, iso(self.now)))
        self.assertEqual((d["rssi_dbm"], d["rssi_min_dbm"], d["rssi_max_dbm"], d["receiver_ids"]), (-44, -60, -44, ["front", "rear"]))

    def test_missing_gps_produces_no_marker_or_coordinates(self):
        self.receiver(); self.rid_reception()
        snap = self.state.snapshot()
        self.assertEqual(snap["aircraft"], [])  # active tracks: what is drawn and what TAK is fed from
        self.assertEqual(snap["history"], [])
        self.assertIsNone(snap["detections"][0]["last_position"])
        row, = rows(self.state)
        self.assertEqual((row["last_latitude"], row["last_longitude"], row["last_position_at"]), ("", "", ""))
        self.assertEqual(self.state.snapshot()["tak"]["written"], 0)

    def test_valid_gps_upgrades_same_identity_to_tracking(self):
        self.rid_reception()
        self.now += 3; self.tracked()
        d, = self.detections()
        self.assertEqual((d["state"], d["position_valid"], d["observation_count"], d["reason"]), ("TRACKING", True, 2, None))
        self.assertEqual((d["last_position"]["latitude"], d["last_position_at"], d["uid"]), (40.1, iso(self.now), "uid-SYNTH-NOFIX"))
        self.now += 3; self.rid_reception()  # fix lost: not tracking, last position kept with its own time
        d, = self.detections()
        self.assertEqual((d["state"], d["position_valid"], d["last_position_at"]), ("DETECTED_NO_POSITION", False, iso(self.now - 3)))

    def test_stale_after_tracker_timeout(self):
        self.rid_reception()
        self.now += 59
        self.assertEqual(self.detections()[0]["state"], "DETECTED_NO_POSITION")
        self.now += 1
        self.assertEqual(self.detections()[0]["state"], "STALE")
        self.rid_reception()
        d, = self.detections()
        self.assertEqual((d["state"], d["observation_count"]), ("DETECTED_NO_POSITION", 2))

    def test_unusable_identity_creates_no_detection(self):
        self.rid_reception(reason="no_basic_id", basic_ids=[])
        self.rid_reception(reason="no_textual_id", value=None)
        self.rid_reception(reason="ambiguous_id", basic_ids=[{"id_type": 1, "ua_type": 2, "value": "A"},
                                                             {"id_type": 1, "ua_type": 2, "value": "B"}])
        self.rid_reception(id_type=0)  # "None" ID type is not an identity
        self.rid_reception(basic_ids=[{"id_type": 1, "ua_type": 2, "value": "A"}, {"id_type": 2, "ua_type": 2, "value": "B"}])
        self.assertEqual(self.detections(), [])
        self.assertEqual(rows(self.state), [])

    def test_same_value_different_identifier_type_is_not_merged(self):
        self.rid_reception(id_type=1); self.rid_reception(id_type=2)
        self.assertEqual(sorted(d["identifier_type"] for d in self.detections()), ["CAA_REGISTRATION_ID", "SERIAL_NUMBER"])

    def test_synthetic_isolation_is_preserved(self):
        with self.assertRaises(ValueError):
            self.rid_reception(synthetic=True)
        with self.assertRaises(ValueError):
            self.tracked(synthetic=True)
        self.assertEqual(self.detections(), [])
        demo = cc.State(demo=True)
        with self.assertRaises(ValueError):
            demo.apply({"kind": "rid_reception", "reception": {"synthetic": False, "received_at": iso(self.now)}})
        self.assertEqual(demo.snapshot()["detections"], [])

    def test_detections_survive_new_host_session(self):
        self.rid_reception()
        self.state.new_session()
        self.assertEqual(len(self.detections()), 1)

    def test_detection_registry_is_bounded(self):
        for i in range(cc.DETECTION_LIMIT + 5):
            self.now += 0.001; self.rid_reception(value=f"SYNTH-{i}")
        found = {d["aircraft_id"] for d in self.detections()}
        self.assertEqual(len(found), cc.DETECTION_LIMIT)
        self.assertNotIn("SYNTH-0", found)  # oldest last_seen evicted first

    def test_csv_export_neutralises_formula_identifiers(self):
        self.rid_reception(value="=CMD()")
        self.assertEqual(rows(self.state)[0]["aircraft_id"], "'=CMD()")


class FieldLogReplayTests(PanelStateCase):
    """Replays the 66 real receptions from the 2026-11-02 indoor Avata test (identifiers replaced)."""
    def test_field_log_yields_one_positionless_detection(self):
        events = [json.loads(line) for line in FIELD_LOG.read_text().splitlines()]
        self.now = cc.epoch(events[-1]["reception"]["received_at"]) + 1
        self.receiver()
        for event in events:
            self.state.apply(event)
        snap = self.state.snapshot()
        d, = snap["detections"]
        self.assertEqual((d["aircraft_id"], d["state"], d["position_valid"], d["reason"], d["observation_count"]),
                         ("FIELDTEST0000000001", "DETECTED_NO_POSITION", False, "no_valid_position", 66))
        self.assertEqual((d["channel"], d["frequency_mhz"], d["transport"], d["rssi_min_dbm"], d["rssi_max_dbm"]),
                         (149, 5745, "wifi_beacon", -60, -44))
        self.assertEqual((d["first_seen"], d["last_seen"]), ("2026-11-02T13:39:17.759Z", "2026-11-02T13:40:38.079Z"))
        self.assertEqual((snap["aircraft"], snap["history"], snap["detection"]["state"]), ([], [], "DETECTED"))
        self.assertEqual(len(rows(self.state)), 1)
