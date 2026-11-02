#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
#include <string.h>
#include "rid_frame.h"

int test_decode(const uint8_t *frame, size_t len, char *json, size_t size,
                unsigned channel, int *candidate, int *decoded) {
    rid_observation_t o;
    rid_result_t r = rid_parse_frame(frame, len, &o);
    *candidate = o.candidate;
    *decoded = o.decoded_messages;
    if (size) json[0] = 0;
    rid_envelope_t e = {"test-receiver", "test-session", 3, 2000000};
    if (r == RID_DECODED && o.detection)
        rid_json_detection(json, size, &e, &o, channel, -60, 1000000);
    return r;
}
/* 1 when a reception record (decoded, not trackable) is emitted; json is "" otherwise. */
int test_reception(const uint8_t *frame, size_t len, char *json, size_t size) {
    rid_observation_t o;
    rid_result_t r = rid_parse_frame(frame, len, &o);
    rid_envelope_t e = {"test-receiver", "test-session", 3, 2000000};
    if (size) json[0] = 0;
    return r == RID_DECODED && rid_json_reception(json, size, &e, &o, 149, -60, 1000000);
}
int test_capture(unsigned sig_len, unsigned dump_len, size_t *length) { return rid_capture_check(sig_len, dump_len, length); }
/* Largest reception record: every message type, two textual IDs needing escapes. */
int test_reception_worst_case(char *json, size_t size) {
    static char id[65];
    for (int i = 0; i < 64; i++) id[i] = 'w';
    rid_envelope_t e = {id, "ffffffffffffffffffffffffffffffff", UINT64_MAX, INT64_MAX};
    static rid_observation_t o;
    memset(&o, 0, sizeof(o));
    o.transport = "wifi_beacon"; o.decoded_messages = 9; o.protocol_version = 2; o.message_counter = 255;
    memset(o.transmitter, 0xff, 6);
    for (int i = 0; i < ODID_BASIC_ID_MAX_MESSAGES; i++) {
        o.uas.BasicIDValid[i] = 1; o.uas.BasicID[i].IDType = ODID_IDTYPE_SERIAL_NUMBER; o.uas.BasicID[i].UAType = 15;
        memset(o.uas.BasicID[i].UASID, '"', ODID_ID_SIZE); o.uas.BasicID[i].UASID[0] = 'X'; o.uas.BasicID[i].UASID[ODID_ID_SIZE - 1] = 'X';
    }
    o.uas.LocationValid = o.uas.SelfIDValid = o.uas.SystemValid = o.uas.OperatorIDValid = 1;
    o.uas.Location.Latitude = 91;
    for (int i = 0; i < ODID_AUTH_MAX_PAGES; i++) o.uas.AuthValid[i] = 1;
    return rid_json_reception(json, size, &e, &o, 165, -127, INT64_MAX);
}
int test_status(char *json, size_t size) {
    rid_envelope_t e = {"quote\"receiver", "session\\id", 4, 3000000};
    rid_counters_t c = {.rf_frames = 20, .candidate_frames = 10, .decoded_packs = 5,
        .decoded_messages = 15, .detections = 3, .errors = 2, .queue_drops = 7, .oversize_drops = 1};
    return rid_json_status(json, size, &e, "monitoring", 149, &c, 10000, 9000, NULL, 0);
}
int test_message(int which, char *json, size_t size) {
    rid_envelope_t e = {"receiver", "session", 0, 0};
    return which == 1 ? rid_json_error(json, size, &e, "tune", -1) : rid_json_hello(json, size, &e, 6, which == 2);
}
size_t test_scan_size(void) { return sizeof(rid_scan_t); }
int test_scan_status(const rid_scan_t *scan, int64_t now_us, char *json, size_t size) {
    rid_envelope_t e = {"receiver", "session", 1, now_us};
    rid_counters_t c = {.rf_frames = 1};
    return rid_json_status(json, size, &e, "monitoring", 6, &c, 10000, 9000, scan, now_us);
}
/* Largest status the firmware can emit: every counter saturated, longest IDs,
 * last-reject snapshot present and a full hopping scan object. */
int test_status_worst_case(char *json, size_t size) {
    static char id[65];
    for (int i = 0; i < 64; i++) id[i] = 'w';
    rid_envelope_t e = {id, "ffffffffffffffffffffffffffffffff", UINT64_MAX, INT64_MAX};
    rid_counters_t c;
    memset(&c, 0xff, sizeof(c));
    c.last_reject.valid = true; c.last_reject.rssi = -128;
    rid_scan_t s;
    if (!rid_scan_init(&s, true, 0, RID_SCAN_DWELL_MAX_MS, RID_SCAN_HOT_TTL_MAX_S)) return 0;
    rid_scan_tuned(&s, 149, 0, UINT32_MAX);
    s.switches = s.tune_errors = s.transition_drops = s.switch_us_last = s.switch_us_max = UINT32_MAX;
    for (size_t i = 0; i < s.count; i++) {
        rid_scan_channel_t *ch = &s.ch[i];
        ch->dwell_ms = ch->rf_frames = ch->candidates = ch->decoded = UINT32_MAX;
        ch->last_decode_us = 0; ch->hot_until_us = INT64_MAX;
    }
    return rid_json_status(json, size, &e, "stopped_error", 165, &c, UINT32_MAX, UINT32_MAX, &s, INT64_MAX);
}
#ifdef STANDALONE
/* Exercise production parsing, accounting and serialization under sanitizers. */
static void exercise(const uint8_t *frame, size_t n) {
    rid_observation_t out;
    rid_result_t result=rid_parse_frame(frame,n,&out);
    rid_counters_t counters={.rf_frames=1};
    rid_account_result(&counters,result,&out);
    size_t mpdu;
    assert(rid_capture_check((unsigned)n + 4, (unsigned)n + 8, &mpdu) == (n < 24 ? RID_CAPTURE_BAD_LENGTH : RID_CAPTURE_OK) && mpdu == n);
    rid_envelope_t e={"sanitizer","synthetic-session",0,2000000};
    char json[RID_JSON_MAX];
    rid_scan_t scan;
    assert(rid_scan_init(&scan,true,0,250,60));
    rid_scan_tuned(&scan,149,0,10);
    rid_scan_frame(&scan,149);
    rid_scan_result(&scan,149,out.candidate,result==RID_DECODED,1000000);
    assert(rid_json_status(json,sizeof(json),&e,"monitoring",149,&counters,10000,9000,&scan,2000000));
    /* Exactly one record type for a decoded pack; none for anything else. */
    bool observation=rid_json_detection(json,sizeof(json),&e,&out,149,-60,1000000);
    bool reception=rid_json_reception(json,sizeof(json),&e,&out,149,-60,1000000);
    assert(observation==(result==RID_DECODED && out.detection));
    assert(reception==(result==RID_DECODED && !out.detection));
    if (observation) assert(!rid_json_detection(json,16,&e,&out,149,-60,1000000));
    if (reception) assert(!rid_json_reception(json,16,&e,&out,149,-60,1000000));
}
/* Deterministic scheduler fuzz: arbitrary channels/results/clock steps, including
 * unsupported channels and saturating counters, must stay in bounds. */
static void exercise_scan(void) {
    uint32_t r = 0x12345678;
    char json[RID_JSON_MAX];
    rid_envelope_t e={"sanitizer","synthetic-session",0,0};
    rid_counters_t counters={0};
    for (int hopping = 0; hopping < 2; hopping++) {
        rid_scan_t s;
        assert(rid_scan_init(&s, hopping, 149, 50, 5));
        int64_t now = 0;
        for (int i = 0; i < 200000; i++) {
            r = r * 1664525u + 1013904223u;
            unsigned channel = (r >> 8) % 200;
            now += (r >> 20) % 400000;
            switch (r % 5) {
            case 0: assert(rid_scan_index(&s, rid_scan_next(&s, now)) >= 0); break;
            case 1: rid_scan_tuned(&s, channel, now, r); break;
            case 2: rid_scan_frame(&s, channel); rid_scan_transition_drop(&s); break;
            case 3: rid_scan_result(&s, channel, r & 64, r & 128, now); break;
            default: rid_scan_tune_failed(&s);
            }
        }
        assert(rid_json_status(json, sizeof(json), &e, "monitoring", 6, &counters, 1, 1, &s, now));
    }
}
/* All prefixes and all one-byte mutations of every checked-in fixture. */
int main(int argc, char **argv) {
    exercise_scan();
    for (int a = 1; a < argc; a++) {
        FILE *f = fopen(argv[a], "r"); if (!f) return 2;
        uint8_t frame[RID_MAX_FRAME]; size_t n = 0; unsigned byte;
        while (fscanf(f, "%x", &byte) == 1 && n < sizeof(frame)) frame[n++] = byte;
        fclose(f);
        for (size_t i = 0; i <= n; i++) exercise(frame, i);
        for (size_t i = 0; i < n; i++) {
            uint8_t saved = frame[i];
            for (unsigned j = 0; j < 256; j++) { frame[i] = j; exercise(frame, n); }
            frame[i] = saved;
        }
    }
    puts("sanitizer corpus passed");
    return 0;
}
#endif

int test_counters(const uint8_t *frame, size_t length, char *json, size_t size) {
    rid_observation_t o;
    rid_result_t result=rid_parse_frame(frame,length,&o);
    rid_counters_t counters={.rf_frames=1};
    rid_account_result(&counters,result,&o);
    rid_envelope_t e={"test-receiver","test-session",0,0};
    return rid_json_status(json,size,&e,"monitoring",149,&counters,10000,9000,NULL,0);
}
