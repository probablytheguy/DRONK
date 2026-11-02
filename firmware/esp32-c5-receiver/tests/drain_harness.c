/* Host-only queue/scheduler model. Exercises the production drain and decoder;
 * it is not a FreeRTOS/ESP radio benchmark. Never linked into firmware. */
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "rid_drain.h"
#include "rid_frame.h"

typedef struct {
    unsigned queued, processed, receives, waits, refills;
    unsigned consumed;
    int64_t clock, step;
    bool real_clock;
    uint8_t frame[RID_MAX_FRAME];
    size_t length;
    rid_counters_t counters;
} model_t;
static bool enqueue(model_t *m) {
    m->counters.rf_frames++;
    if (m->queued == 8) { m->counters.queue_drops++; return false; }
    m->queued++; return true;
}
static bool receive(void *context, unsigned wait_ms) {
    model_t *m=context; m->receives++;
    if (wait_ms) { assert(wait_ms==100); m->waits++; }
    if (!m->queued) return false;
    m->queued--; m->consumed++; return true;
}
static void process(void *context) {
    model_t *m=context;
    m->processed++; m->clock+=m->step;
    if (m->length) {
        rid_observation_t o;
        rid_result_t result=rid_parse_frame(m->frame,m->length,&o);
        assert(result==RID_DECODED && o.detection);
        rid_account_result(&m->counters,result,&o);
    }
    if (m->refills) { m->refills--; assert(enqueue(m)); }
}
static int64_t now_us(void *context) {
    model_t *m=context;
    if (!m->real_clock) return m->clock;
    struct timespec t; assert(clock_gettime(CLOCK_MONOTONIC,&t)==0);
    return (int64_t)t.tv_sec*1000000+t.tv_nsec/1000;
}
static void correctness(void) {
    model_t m={0};
    rid_drain_result_t r=rid_drain(&m,receive,process,now_us);
    assert(!r.processed && !r.yield_required && m.waits==1);
    for(unsigned i=0;i<100;i++) enqueue(&m);
    assert(m.counters.queue_drops==92 && m.queued==8);
    r=rid_drain(&m,receive,process,now_us);
    assert(r.processed==8 && !r.yield_required && m.queued==0);
    assert(m.waits==2); /* first receive only, never a per-packet wait */
    assert(enqueue(&m)); m.refills=100;
    r=rid_drain(&m,receive,process,now_us);
    assert(r.processed==32 && r.yield_required);
    m.step=2000;
    r=rid_drain(&m,receive,process,now_us);
    assert(r.processed==3 && r.yield_required); /* bounded time even at low rates */
    assert(m.counters.rf_frames==m.counters.queue_drops+m.consumed+m.queued);
}
int main(int argc,char **argv) {
    correctness();
    assert(argc==2);
    model_t m={.real_clock=true,.refills=1000000};
    FILE *f=fopen(argv[1],"r"); assert(f);
    unsigned byte;
    while(fscanf(f,"%x",&byte)==1) { assert(m.length<RID_MAX_FRAME); m.frame[m.length++]=(uint8_t)byte; }
    fclose(f); assert(enqueue(&m));
    unsigned yields=0;
    int64_t started=now_us(&m);
    for(unsigned i=0;i<40;i++) {
        rid_drain_result_t r=rid_drain(&m,receive,process,now_us);
        assert(r.processed && r.processed<=RID_DRAIN_MAX_PACKETS && r.yield_required);
        yields++;
        struct timespec tick={.tv_nsec=10000000}; /* explicitly model 100-Hz idle opportunity */
        assert(nanosleep(&tick,NULL)==0);
    }
    double seconds=(now_us(&m)-started)/1000000.0;
    assert(m.counters.decoded_packs==m.processed && yields==40);
    printf("queue model: %u decoded packets, %u bounded-slice yields, %.3f seconds, %.1f packets/s; old per-packet 100-Hz ceiling=100/s\n",
        m.processed,yields,seconds,m.processed/seconds);
    /* No absolute wall-clock speed assertion: shared host scheduling is variable. */
    return 0;
}
