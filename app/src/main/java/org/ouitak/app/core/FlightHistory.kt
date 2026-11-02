package org.ouitak.app.core

import java.security.MessageDigest
import java.time.Duration
import java.time.Instant

/** Durable implementation is Android SQLite; core/replay do not depend on it. */
interface FlightHistory {
    fun append(detection: Detection): Boolean
    fun aircraft(): List<String>
    fun observations(aircraftUid: String): List<Detection>
    fun all(): List<Detection> = aircraft().flatMap { observations(it) }.sortedWith(observationOrder)
    fun cleanup(before: Instant): Int
    fun deleteAll()
}

val observationOrder: Comparator<Detection> = compareBy<Detection> { it.observedAt }.thenBy { historyKey(it) }

/** Re-receiving the same report is a duplicate even if receipt time changes. */
fun historyKey(d: Detection): String {
    val canonical = listOf(d.cotUid, d.observedAt, d.position, d.speedMps, d.headingDegrees,
        d.source, d.locations.toSortedMap().toString()).joinToString("\u0000")
    return MessageDigest.getInstance("SHA-256").digest(canonical.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }
}

/** A cutoff never permits an expired auxiliary location to survive in a newer report. */
fun retainedDetection(d: Detection, cutoff: Instant): Detection? {
    fun keep(r: ReportedLocation?) = r?.takeIf { !it.observedAt.isBefore(cutoff) }
    val aircraft = d.locations.getValue(LocationRole.AIRCRAFT)
    val keepAircraft = !aircraft.observedAt.isBefore(cutoff)
    val control = keep(d.controlStationLocation); val takeoff = keep(d.takeoffLocation); val receiver = keep(d.receiverLocation)
    if (!keepAircraft && control == null && takeoff == null && receiver == null) return null
    return d.copy(position = if (keepAircraft) d.position else null,
        speedMps = if (keepAircraft) d.speedMps else null, headingDegrees = if (keepAircraft) d.headingDegrees else null,
        aircraftLocationReport = if (keepAircraft) d.aircraftLocationReport else
            ReportedLocation(null, d.observedAt, aircraft.provenance, LocationValidity.UNAVAILABLE),
        controlStationLocation = control, takeoffLocation = takeoff, receiverLocation = receiver)
}

data class HistoryPolicy(val retention: Duration = Duration.ofDays(7), val trailPoints: Int = 20) {
    init { require(retention >= Duration.ofHours(1) && retention <= Duration.ofDays(30)); require(trailPoints in 2..100) }
}

/** Only observed valid positions; no interpolation or inferred history points. */
fun trailPoints(history: List<Detection>, now: Instant, policy: HistoryPolicy): List<Detection> =
    history.filter { it.position != null && it.observedAt <= now && !it.observedAt.isBefore(now.minus(policy.retention)) }
        .distinctBy(::historyKey).sortedWith(observationOrder).takeLast(policy.trailPoints)
