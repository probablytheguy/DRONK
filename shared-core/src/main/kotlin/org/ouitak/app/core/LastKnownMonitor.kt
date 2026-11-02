package org.ouitak.app.core

import java.time.Duration
import java.time.Instant

/** When a LAST KNOWN report was made and how long it stays meaningful; reason is a wire value of [LastKnownMonitor.Reason]. */
data class LastKnownReport(val reportedAt: Instant, val staleAt: Instant, val reason: String)

/**
 * Outbound marker lifecycle, per aircraft and per ground role (operator, take-off): LIVE while broadcasts keep
 * refreshing that role, LAST_KNOWN once none has for [timeout]. Each role keeps only its own last VALID broadcast
 * position, so an aircraft position never stands in for an operator position. A role is reported LAST_KNOWN once
 * per disappearance, never refreshed as if current. Thread-confined; the caller drives [due] periodically.
 */
class LastKnownMonitor(private val timeout: Duration, private val retention: Duration, private val maxEntries: Int = 1024) {
    enum class Role(val wire: String, val uidSuffix: String?) { AIRCRAFT("aircraft", null), OPERATOR("operator", "operator"), TAKEOFF("takeoff", "takeoff") }
    enum class Reason(val wire: String) { NO_RECEPTIONS("no_receptions"), RECEIVER_INTERRUPTED("receiver_interrupted"), MONITORING_STOPPED("monitoring_stopped") }
    data class Marker(val role: Role, val detection: Detection, val location: ReportedLocation) {
        val uid: String get() = role.uidSuffix?.let { "${detection.cotUid}-$it" } ?: detection.cotUid
        val lastSeen: Instant get() = location.observedAt
    }
    data class Transition(val marker: Marker, val reason: Reason, val report: LastKnownReport)
    private class Entry(val marker: Marker, var lastKnown: Boolean = false)
    private val entries = linkedMapOf<String, Entry>()

    init {
        validateStaleAfter(timeout)
        require(retention >= Duration.ofSeconds(1) && retention <= Duration.ofDays(1)) { "Last-known retention must be 1 second to 1 day" }
        require(maxEntries > 0)
    }

    /** Record a live (tracked) detection. Ground roles only when [includeGround]. Returns markers that were LAST_KNOWN and are live again. */
    fun live(detection: Detection, includeGround: Boolean): List<Marker> {
        val restored = mutableListOf<Marker>()
        fun put(role: Role, location: ReportedLocation?) {
            val p = location?.takeIf { it.validity == LocationValidity.VALID }?.position ?: return
            if (p.latitude == 0.0 && p.longitude == 0.0) return
            val marker = Marker(role, detection, location)
            val previous = entries[marker.uid]
            if (previous != null && location.observedAt.isBefore(previous.marker.lastSeen)) return
            if (previous?.lastKnown == true) restored += marker
            entries.remove(marker.uid)
            entries[marker.uid] = Entry(marker)
            while (entries.size > maxEntries) entries.remove(entries.keys.first())
        }
        put(Role.AIRCRAFT, detection.locations[LocationRole.AIRCRAFT])
        if (includeGround) {
            put(Role.OPERATOR, detection.controlStationLocation)
            put(Role.TAKEOFF, detection.takeoffLocation)
        }
        return restored
    }

    /** Markers whose role has not been refreshed for [timeout]; [receiverCovered] says whether the receiver that heard
     * the marker stayed available since then (otherwise the silence is a coverage interruption, not a disappearance). */
    fun due(now: Instant, receiverCovered: (Marker) -> Boolean): List<Transition> {
        entries.entries.removeAll { it.value.lastKnown && !it.value.marker.lastSeen.plus(timeout).plus(retention).isAfter(now) }
        return entries.values.filter { !it.lastKnown && !it.marker.lastSeen.plus(timeout).isAfter(now) }.map { e ->
            e.lastKnown = true
            transition(e.marker, if (receiverCovered(e.marker)) Reason.NO_RECEPTIONS else Reason.RECEIVER_INTERRUPTED, now)
        }
    }

    /** Monitoring is ending: every live marker becomes LAST_KNOWN now. */
    fun stopAll(now: Instant): List<Transition> = entries.values.filter { !it.lastKnown }.map { e ->
        e.lastKnown = true
        transition(e.marker, Reason.MONITORING_STOPPED, now)
    }

    fun lastKnown(uid: String): Boolean? = entries[uid]?.lastKnown

    private fun transition(marker: Marker, reason: Reason, now: Instant) =
        Transition(marker, reason, LastKnownReport(now, now.plus(retention), reason.wire))
}
