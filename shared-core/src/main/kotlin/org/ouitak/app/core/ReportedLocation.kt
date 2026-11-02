package org.ouitak.app.core

import java.time.Instant

enum class LocationRole { AIRCRAFT, CONTROL_STATION, TAKEOFF, RECEIVER }
enum class LocationProvenance { REMOTE_ID_BROADCAST, SENSOR_RECEIVER, SIMULATION }
enum class LocationValidity { VALID, UNAVAILABLE, INVALID }

/** Coordinates, provenance and observation time belong to this report alone. */
data class ReportedLocation(
    val position: Position?,
    val observedAt: Instant,
    val provenance: LocationProvenance,
    val validity: LocationValidity
) {
    init {
        require(observedAt >= Instant.EPOCH && observedAt <= Instant.parse("9999-12-31T23:58:59Z"))
        require((validity == LocationValidity.VALID) == (position != null))
    }
    companion object {
        fun fromCoordinates(lat: Double?, lon: Double?, at: Instant, provenance: LocationProvenance,
                            hae: Double? = null): ReportedLocation {
            if (lat == null || lon == null || (provenance != LocationProvenance.SENSOR_RECEIVER && lat == 0.0 && lon == 0.0))
                return ReportedLocation(null, at, provenance, LocationValidity.UNAVAILABLE)
            return try { ReportedLocation(Position(lat, lon, hae), at, provenance, LocationValidity.VALID) }
            catch (_: IllegalArgumentException) { ReportedLocation(null, at, provenance, LocationValidity.INVALID) }
        }
    }
}
