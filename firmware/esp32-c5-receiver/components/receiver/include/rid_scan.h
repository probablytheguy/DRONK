#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Pure, host-testable Wi-Fi channel scheduler. No locks, radio calls or clocks:
 * the caller serializes access and passes monotonic time in microseconds.
 *
 * Hopping repeats six slots [6, 149, S, 6, 149, H|S]: the ASTM/Wi-Fi Aware
 * social channels anchor four of six slots, S advances through the remaining
 * channels, and H revisits channels that recently produced Remote ID (falling
 * back to S). Hot channels never stop the sweep, so new transmitters stay
 * discoverable. Fixed mode always returns its one channel. */

#define RID_SCAN_MAX_CHANNELS 20
#define RID_SCAN_DWELL_MIN_MS 50
#define RID_SCAN_DWELL_MAX_MS 2000
#define RID_SCAN_HOT_TTL_MIN_S 5
#define RID_SCAN_HOT_TTL_MAX_S 600

typedef struct {
    uint8_t channel;
    bool anchor;
    /* Saturating 32-bit telemetry keeps the status record bounded. */
    uint32_t dwell_ms, rf_frames, candidates, decoded;
    int64_t last_decode_us; /* -1: never */
    int64_t hot_until_us;   /* <= now: not hot */
} rid_scan_channel_t;

typedef struct {
    rid_scan_channel_t ch[RID_SCAN_MAX_CHANNELS];
    size_t count;
    bool hopping;
    unsigned dwell_ms;
    int64_t hot_ttl_us;
    unsigned slot;
    size_t sweep_next, hot_next;
    int current;           /* index of the tuned channel, -1 before the first tune */
    int64_t tuned_at_us;
    int64_t dwell_carry_us; /* sub-millisecond remainder of the current channel's dwell */
    uint32_t switches, tune_errors, transition_drops, switch_us_last, switch_us_max;
} rid_scan_t;

/* Hopping uses the built-in US non-DFS list; fixed mode uses fixed_channel.
 * false: unsupported channel, dwell or TTL (the scheduler is left unusable). */
bool rid_scan_init(rid_scan_t *scan, bool hopping, unsigned fixed_channel, unsigned dwell_ms, unsigned hot_ttl_s);
int rid_scan_index(const rid_scan_t *scan, unsigned channel);
/* Next channel to tune. Advances the slot pattern; never fails after init. */
unsigned rid_scan_next(rid_scan_t *scan, int64_t now_us);
/* The radio is now on channel: closes the previous channel's dwell. */
void rid_scan_tuned(rid_scan_t *scan, unsigned channel, int64_t now_us, uint32_t switch_us);
void rid_scan_tune_failed(rid_scan_t *scan);
void rid_scan_frame(rid_scan_t *scan, unsigned channel);
void rid_scan_transition_drop(rid_scan_t *scan);
/* Parser outcome for a frame received on channel. A candidate marks it hot. */
void rid_scan_result(rid_scan_t *scan, unsigned channel, bool candidate, bool decoded, int64_t now_us);
/* Dwell including the still-open current visit. */
uint32_t rid_scan_dwell_ms(const rid_scan_t *scan, size_t index, int64_t now_us);
