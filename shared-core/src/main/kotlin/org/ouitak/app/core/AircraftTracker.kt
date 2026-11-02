package org.ouitak.app.core

import java.time.Clock
import java.time.Duration
import java.time.Instant

/** Located live state only: no trail, disk persistence or historical path. */
data class AircraftTrack(val latest: Detection, val expiresAt: Instant) {
    val uid: String get() = latest.cotUid
    val lastSeen: Instant get() = latest.observedAt
}

sealed class TrackUpdate {
    data class Updated(val track: AircraftTrack) : TrackUpdate()
    data class Ignored(val reason: String) : TrackUpdate()
}

/** Thread-confined, sensor/transport independent. Clock injection makes expiry
 * deterministic. Missing locations never refresh an earlier precise position.
 */
class AircraftTracker(
    staleAfter: Duration = Duration.ofSeconds(60),
    private val clock: Clock = Clock.systemUTC(),
    private val maxTracks: Int = 1024
) {
    var staleAfter: Duration = staleAfter
        set(value) { validateStaleAfter(value); field = value }
    private val latestByUid = linkedMapOf<String, Detection>()
    init { validateStaleAfter(staleAfter); require(maxTracks > 0) }

    fun observe(detection: Detection, now: Instant = clock.instant()): TrackUpdate {
        active(now)
        if (detection.position == null) return TrackUpdate.Ignored("No valid aircraft position; marker not created or refreshed")
        if (detection.observedAt > now) return TrackUpdate.Ignored("Future observation")
        if (!detection.observedAt.plus(staleAfter).isAfter(now)) return TrackUpdate.Ignored("Observation already expired")
        val previous = latestByUid[detection.cotUid]
        if (previous != null && !detection.observedAt.isAfter(previous.observedAt)) {
            return TrackUpdate.Ignored("Duplicate or older observation; marker unchanged")
        }
        if (previous == null && latestByUid.size >= maxTracks) return TrackUpdate.Ignored("Active aircraft limit reached")
        latestByUid[detection.cotUid] = detection
        return TrackUpdate.Updated(AircraftTrack(detection, detection.observedAt.plus(staleAfter)))
    }

    fun active(now: Instant = clock.instant()): List<AircraftTrack> {
        latestByUid.entries.removeAll { !it.value.observedAt.plus(staleAfter).isAfter(now) }
        return latestByUid.values.map { AircraftTrack(it, it.observedAt.plus(staleAfter)) }
    }

    fun clear() = latestByUid.clear()
}

fun validateStaleAfter(value: Duration) {
    require(value >= Duration.ofSeconds(1) && value <= Duration.ofDays(1)) { "Stale timeout must be 1 second to 1 day" }
}
