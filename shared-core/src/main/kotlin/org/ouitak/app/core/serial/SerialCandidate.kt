package org.ouitak.app.core.serial

import java.time.Instant
import org.ouitak.app.core.*

/** Numeric plausibility only: upstream omits per-field validity and timestamps. */
data class CandidateLocation(val position: Position?, val validity: LocationValidity) {
    init { require((validity == LocationValidity.VALID) == (position != null)) }
}

/** Trusted caller metadata, never read from serial JSON. Role reports are supplied
 * independently; pilot_lat/long is deliberately NOT assigned a location role. */
data class SerialContext(
    val receivedAt: Instant,
    val source: ObservationSource,
    val controlStation: ReportedLocation? = null,
    val takeoff: ReportedLocation? = null,
    val sensor: ReportedLocation? = null
)

data class SerialCandidate(
    val mac: String?,
    val basicId: String?,
    val rssi: Int?,
    val aircraft: CandidateLocation,
    val unresolvedPilot: CandidateLocation,
    val context: SerialContext
)

sealed class SerialEvent {
    enum class IgnoreReason { EMPTY, DIAGNOSTIC, BOOT, STATUS }
    enum class RejectReason { OVERSIZED, INVALID_UTF8, INVALID_JSON, INVALID_FIELDS, INVALID_CONTEXT }
    data class Ignored(val reason: IgnoreReason, val detail: String? = null) : SerialEvent()
    data class Rejected(val reason: RejectReason) : SerialEvent()
    data class Candidate(val value: SerialCandidate) : SerialEvent()
}

/** Validates schema/numeric ranges; never associates identities or creates CoT. */
internal object CandidateValidator {
    private val end = Instant.parse("9999-12-31T23:58:59Z")
    fun validContext(context: SerialContext): Boolean {
        if (context.receivedAt < Instant.EPOCH || context.receivedAt > end) return false
        fun valid(report: ReportedLocation?, realProvenance: LocationProvenance): Boolean {
            return report == null || (report.observedAt <= context.receivedAt && report.provenance ==
                if (context.source == ObservationSource.SIMULATION) LocationProvenance.SIMULATION else realProvenance)
        }
        return valid(context.controlStation, LocationProvenance.REMOTE_ID_BROADCAST) &&
            valid(context.takeoff, LocationProvenance.REMOTE_ID_BROADCAST) && valid(context.sensor, LocationProvenance.SENSOR_RECEIVER)
    }
    fun validate(fields: Map<String, Any?>, context: SerialContext): SerialCandidate {
        fun string(key: String): String? {
            if (!fields.containsKey(key)) return null
            return fields[key] as? String ?: throw InvalidSerialData()
        }
        fun number(key: String): Double? {
            if (!fields.containsKey(key)) return null
            return (fields[key] as? JsonNumber)?.text?.toDoubleOrNull()?.takeIf { it.isFinite() }
                ?: throw InvalidSerialData()
        }
        fun location(latKey: String, lonKey: String, altitudeKey: String? = null): CandidateLocation {
            val lat = number(latKey)
            val lon = number(lonKey)
            val alt = altitudeKey?.let { number(it) }
            if (alt != null && alt != -1000.0 && (alt <= -1000.0 || alt > 31767.5)) throw InvalidSerialData()
            if (lat == null && lon == null) return CandidateLocation(null, LocationValidity.UNAVAILABLE)
            if (lat == null || lon == null) return CandidateLocation(null, LocationValidity.INVALID)
            if (lat !in -90.0..90.0 || lon !in -180.0..180.0) return CandidateLocation(null, LocationValidity.INVALID)
            if (lat == 0.0 && lon == 0.0) return CandidateLocation(null, LocationValidity.UNAVAILABLE)
            return CandidateLocation(Position(lat, lon, alt?.takeUnless { it == -1000.0 }), LocationValidity.VALID)
        }
        val id = string("basic_id")?.takeUnless { it.isEmpty() }
        if (id != null && (id.length > 20 || id != id.trim() || id.any { it.code !in 0x20..0x7e })) throw InvalidSerialData()
        val mac = string("mac")?.lowercase()
        if (mac != null && (!Regex("[0-9a-f]{2}(:[0-9a-f]{2}){5}").matches(mac) ||
                mac == "00:00:00:00:00:00" || mac == "ff:ff:ff:ff:ff:ff")) throw InvalidSerialData()
        val rssi = number("rssi")
        if (rssi != null && (rssi !in -127.0..0.0 || rssi % 1.0 != 0.0)) throw InvalidSerialData()
        return SerialCandidate(mac, id, rssi?.toInt(), location("drone_lat", "drone_long", "drone_altitude"),
            location("pilot_lat", "pilot_long"), context)
    }
}
