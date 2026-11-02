#pragma once
#include <stdbool.h>
#include <stdint.h>

#define RID_DRAIN_MAX_PACKETS 32u
#define RID_DRAIN_BUDGET_US 5000

typedef struct { unsigned processed; bool yield_required; } rid_drain_result_t;
/* Single consumer, bounded work slice. First receive may block for 100 ms;
 * subsequent receives never wait. Caller's receive/process share their context.
 * A bounded slice yields to lower-priority idle/system tasks under saturation. */
rid_drain_result_t rid_drain(void *context,
    bool (*receive)(void *, unsigned wait_ms), void (*process)(void *),
    int64_t (*now_us)(void *));
