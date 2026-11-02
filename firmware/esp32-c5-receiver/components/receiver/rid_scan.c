#include "rid_scan.h"
#include <string.h>
#include "rid_frame.h"

/* Anchors first, then the sweep order. All are in rid_channel_supported();
 * DFS channels are excluded because the allowlist excludes them. */
static const uint8_t hop_channels[] = {6, 149, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11,
                                       36, 40, 44, 48, 153, 157, 161, 165};
_Static_assert(sizeof(hop_channels) <= RID_SCAN_MAX_CHANNELS, "hop list exceeds telemetry bound");

static void add32(uint32_t *value, uint64_t amount) {
    *value = amount > UINT32_MAX - *value ? UINT32_MAX : *value + (uint32_t)amount;
}

bool rid_scan_init(rid_scan_t *s, bool hopping, unsigned fixed_channel, unsigned dwell_ms, unsigned hot_ttl_s) {
    memset(s, 0, sizeof(*s));
    s->current = -1;
    if (dwell_ms < RID_SCAN_DWELL_MIN_MS || dwell_ms > RID_SCAN_DWELL_MAX_MS ||
        hot_ttl_s < RID_SCAN_HOT_TTL_MIN_S || hot_ttl_s > RID_SCAN_HOT_TTL_MAX_S) return false;
    if (!hopping && !rid_channel_supported(fixed_channel)) return false;
    const uint8_t *list = hopping ? hop_channels : (const uint8_t[]){(uint8_t)fixed_channel};
    size_t count = hopping ? sizeof(hop_channels) : 1;
    for (size_t i = 0; i < count; i++) {
        s->ch[i].channel = list[i];
        s->ch[i].anchor = hopping && (list[i] == 6 || list[i] == 149);
        s->ch[i].last_decode_us = -1;
    }
    s->count = count;
    s->hopping = hopping;
    s->dwell_ms = dwell_ms;
    s->hot_ttl_us = (int64_t)hot_ttl_s * 1000000;
    return true;
}

int rid_scan_index(const rid_scan_t *s, unsigned channel) {
    for (size_t i = 0; i < s->count; i++) if (s->ch[i].channel == channel) return (int)i;
    return -1;
}

static unsigned sweep(rid_scan_t *s) {
    for (size_t tried = 0; tried < s->count; tried++) {
        size_t i = s->sweep_next++ % s->count;
        if (!s->ch[i].anchor) return s->ch[i].channel;
    }
    return s->ch[0].channel;
}

static int hot(rid_scan_t *s, int64_t now_us) {
    for (size_t tried = 0; tried < s->count; tried++) {
        size_t i = s->hot_next++ % s->count;
        if (!s->ch[i].anchor && s->ch[i].hot_until_us > now_us) return s->ch[i].channel;
    }
    return -1;
}

unsigned rid_scan_next(rid_scan_t *s, int64_t now_us) {
    if (!s->hopping) return s->ch[0].channel;
    unsigned slot = s->slot++ % 6;
    if (slot == 0 || slot == 3) return 6;
    if (slot == 1 || slot == 4) return 149;
    if (slot == 5) {
        int channel = hot(s, now_us);
        if (channel >= 0) return (unsigned)channel;
    }
    return sweep(s);
}

static void close_dwell(rid_scan_t *s, int64_t now_us) {
    if (s->current < 0 || now_us <= s->tuned_at_us) return;
    int64_t elapsed = now_us - s->tuned_at_us + s->dwell_carry_us;
    add32(&s->ch[s->current].dwell_ms, (uint64_t)(elapsed / 1000));
    s->dwell_carry_us = elapsed % 1000;
}

void rid_scan_tuned(rid_scan_t *s, unsigned channel, int64_t now_us, uint32_t switch_us) {
    int index = rid_scan_index(s, channel);
    if (index < 0) { rid_scan_tune_failed(s); return; }
    close_dwell(s, now_us);
    if (index != s->current) s->dwell_carry_us = 0;
    s->current = index;
    s->tuned_at_us = now_us;
    add32(&s->switches, 1);
    s->switch_us_last = switch_us;
    if (switch_us > s->switch_us_max) s->switch_us_max = switch_us;
}

void rid_scan_tune_failed(rid_scan_t *s) { add32(&s->tune_errors, 1); }
void rid_scan_transition_drop(rid_scan_t *s) { add32(&s->transition_drops, 1); }

void rid_scan_frame(rid_scan_t *s, unsigned channel) {
    int index = rid_scan_index(s, channel);
    if (index >= 0) add32(&s->ch[index].rf_frames, 1);
}

void rid_scan_result(rid_scan_t *s, unsigned channel, bool candidate, bool decoded, int64_t now_us) {
    int index = rid_scan_index(s, channel);
    if (index < 0 || !candidate) return;
    rid_scan_channel_t *c = &s->ch[index];
    add32(&c->candidates, 1);
    c->hot_until_us = now_us + s->hot_ttl_us;
    if (decoded) { add32(&c->decoded, 1); c->last_decode_us = now_us; }
}

uint32_t rid_scan_dwell_ms(const rid_scan_t *s, size_t index, int64_t now_us) {
    uint32_t dwell = s->ch[index].dwell_ms;
    if ((int)index == s->current && now_us > s->tuned_at_us)
        add32(&dwell, (uint64_t)((now_us - s->tuned_at_us + s->dwell_carry_us) / 1000));
    return dwell;
}
