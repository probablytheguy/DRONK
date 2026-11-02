#include "rid_drain.h"

rid_drain_result_t rid_drain(void *context,
    bool (*receive)(void *, unsigned), void (*process)(void *), int64_t (*now_us)(void *)) {
    rid_drain_result_t result = {0};
    if (!receive(context, 100)) return result;
    int64_t started = now_us(context);
    for (;;) {
        process(context);
        result.processed++;
        if (result.processed >= RID_DRAIN_MAX_PACKETS || now_us(context) - started >= RID_DRAIN_BUDGET_US) {
            result.yield_required = true;
            return result;
        }
        if (!receive(context, 0)) return result;
    }
}
