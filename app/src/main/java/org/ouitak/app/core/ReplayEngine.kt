package org.ouitak.app.core

import java.time.Duration
import java.time.Instant
import java.util.UUID

enum class ReplayState { READY, PLAYING, PAUSED, FINISHED }
data class ReplayFrame(val original: Detection, val displayAt: Instant, val session: UUID) {
    val uid: String get() = "ouitak-replay-$session-${original.cotUid}"
}

/** Monotonic elapsed nanoseconds supplied by host; stored timestamps never mutate. */
class ReplayEngine(records: List<Detection>, val session: UUID = UUID.randomUUID()) {
    val records = records.filter { it.position != null }.distinctBy(::historyKey).sortedWith(observationOrder)
    var state = ReplayState.READY; private set
    private var cursor = 0
    private var elapsed = 0.0
    private var anchor = 0L
    var speed = 1.0; private set
    fun setSpeed(value: Double, nowNanos: Long) {
        require(value.isFinite() && value in 0.25..64.0)
        accrue(nowNanos); speed = value
    }
    fun resume(nowNanos: Long) {
        if (state == ReplayState.PLAYING || state == ReplayState.FINISHED) return
        anchor = nowNanos; state = if (records.isEmpty()) ReplayState.FINISHED else ReplayState.PLAYING
    }
    fun pause(nowNanos: Long) { if (state == ReplayState.PLAYING) { accrue(nowNanos); state = ReplayState.PAUSED } }
    fun reset() { cursor = 0; elapsed = 0.0; state = ReplayState.READY }
    fun poll(nowNanos: Long, displayAt: Instant): List<ReplayFrame> {
        if (state != ReplayState.PLAYING) return emptyList()
        accrue(nowNanos)
        val result = mutableListOf<ReplayFrame>()
        while (cursor < records.size) {
            val delta = Duration.between(records.first().observedAt, records[cursor].observedAt)
            val due = delta.seconds.toDouble() * 1_000_000_000.0 + delta.nano
            if (due > elapsed) break
            result += ReplayFrame(records[cursor++], displayAt, session)
        }
        if (cursor == records.size) state = ReplayState.FINISHED
        return result
    }
    private fun accrue(nowNanos: Long) {
        if (state == ReplayState.PLAYING) {
            require(nowNanos >= anchor) { "Replay clock moved backwards" }
            elapsed += (nowNanos - anchor) * speed; anchor = nowNanos
        }
    }
}
