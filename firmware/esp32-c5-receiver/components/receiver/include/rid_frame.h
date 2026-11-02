#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "opendroneid.h"
#include "rid_scan.h"

void rid_json_set_reset_reason(int reason);

/* Largest MPDU copied and parsed: 24-byte management header + 4-byte HT Control
 * + 2304-byte maximum (non-DMG) MMPDU body. Larger frames cannot be valid
 * management frames and are counted as oversize, never truncated. In practice
 * the driver's ~1.6 KB RX buffers bound delivery first; dump_len < sig_len is
 * rejected separately as a truncated capture. */
#define RID_MAX_FRAME 2332
#define RID_JSON_MAX 3072
#define RID_DECODER_REV "849c03adbe0cdd68997a462fafab397edcfe53f3"

typedef enum { RID_UNRELATED, RID_MALFORMED, RID_DECODE_ERROR, RID_DECODED, RID_UNSUPPORTED_VERSION, RID_UNSUPPORTED } rid_result_t;
typedef struct {
    bool candidate;
    bool detection;
    const char *transport;
    uint8_t transmitter[6];
    uint8_t message_counter;
    unsigned decoded_messages, unsupported_versions, skipped_messages;
    unsigned protocol_version;
    ODID_UAS_Data uas;
    int basic_id_index;
} rid_observation_t;

/* Callback length check, pure for host tests. Measured on C5/IDF v5.5.4: sig_len
 * includes a 4-byte FCS slot (not a valid CRC) and dump_len is sig_len + 4, so
 * the MPDU is sig_len - 4. BAD_LENGTH: under a 24-byte header or truncated
 * (dump_len < sig_len). OVERSIZE: MPDU over RID_MAX_FRAME. */
typedef enum { RID_CAPTURE_OK, RID_CAPTURE_BAD_LENGTH, RID_CAPTURE_OVERSIZE } rid_capture_t;
rid_capture_t rid_capture_check(unsigned sig_len, unsigned dump_len, size_t *mpdu_length);

/* Input: 802.11 management bytes without radiotap/FCS. No retained packet state. */
rid_result_t rid_parse_frame(const uint8_t *frame, size_t length, rid_observation_t *out);
bool rid_position_valid(double latitude, double longitude);
bool rid_channel_supported(unsigned channel);
unsigned rid_frequency_mhz(unsigned channel);

typedef struct {
    const char *receiver_id;
    const char *session_id;
    uint64_t sequence;
    int64_t uptime_us;
} rid_envelope_t;
typedef struct {
    uint64_t rf_frames, candidate_frames, decoded_packs, decoded_messages;
    uint64_t detections, errors, queue_drops, oversize_drops, output_errors;
    uint64_t malformed_frames, unsupported_versions, decode_failures, usb_output_drops;
    uint64_t skipped_messages;
    /* Decoded packs reported as reception records (valid RID, not trackable). */
    uint64_t rid_receptions;
    /* Largest MPDU length seen by the callback, and the most recent oversize frame. */
    uint32_t max_frame_len;
    struct { bool valid; unsigned length, channel, frame_control; } last_oversize;
    /* Callback metadata rejections, one per failing check (a frame may hit several). */
    uint64_t rx_reject_state, rx_reject_length, rx_reject_channel, rx_reject_rssi;
    /* Raw metadata of the most recent callback rejection (bring-up diagnostic). */
    struct {
        bool valid;
        unsigned rx_state, rxend_state, sig_len, dump_len, channel, second, bb_format, frame_control;
        int rssi;
    } last_reject;
} rid_counters_t;

void rid_account_result(rid_counters_t *c, rid_result_t result, const rid_observation_t *o);

/* All output is bounded, escaped JSON, LF terminated. false means no complete record. */
bool rid_json_detection(char *buf, size_t size, const rid_envelope_t *envelope,
                        const rid_observation_t *observation, unsigned channel,
                        int rssi_dbm, int64_t received_uptime_us);
/* scan may be NULL (no scan object). now_us is the scan clock for dwell/age fields. */
/* A strictly decoded pack that is not trackable (no single textual Basic ID or no
 * valid aircraft position in the same pack). Never emitted for candidates,
 * malformed frames or decode failures. Coordinates are deliberately omitted. */
bool rid_json_reception(char *buf, size_t size, const rid_envelope_t *envelope,
                        const rid_observation_t *observation, unsigned channel,
                        int rssi_dbm, int64_t received_uptime_us);
bool rid_basic_id_textual(const ODID_BasicID_data *id);
bool rid_json_status(char *buf, size_t size, const rid_envelope_t *envelope,
                     const char *state, unsigned channel, const rid_counters_t *counters,
                     unsigned free_heap, unsigned minimum_heap, const rid_scan_t *scan, int64_t now_us);
/* Hopping hello has a null requested_channel. */
bool rid_json_hello(char *buf, size_t size, const rid_envelope_t *envelope, unsigned channel, bool hopping);
bool rid_json_error(char *buf, size_t size, const rid_envelope_t *envelope,
                    const char *operation, int code);
