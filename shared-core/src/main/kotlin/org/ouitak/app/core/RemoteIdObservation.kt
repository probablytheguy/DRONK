package org.ouitak.app.core

import java.time.Instant

/** Decoded, associated aircraft Basic ID + Location semantic fields. Synthetic
 * fixtures use this boundary; this is NOT a USB/BLE/WiFi parser or wire format.
 * Future adapters must prove the association; never use pilot/receiver location.
 */
data class RemoteIdObservation(
    val basicId: String?,
    val identifierType: IdentifierType?,
    val observedAt: Instant,
    val receivedAt: Instant,
    val latitude: Double?,
    val longitude: Double?,
    val altitudeHaeMeters: Double? = null,
    val speedMps: Double? = null,
    val headingDegrees: Double? = null,
    val source: ObservationSource = ObservationSource.SIMULATION,
    val aircraftLocation: ReportedLocation? = null,
    val controlStationLocation: ReportedLocation? = null,
    val takeoffLocation: ReportedLocation? = null,
    val receiverLocation: ReportedLocation? = null
)

sealed class NormalizationResult {
    data class Accepted(val detection: Detection) : NormalizationResult()
    data class Rejected(val reason: String) : NormalizationResult()
}

object RemoteIdNormalizer {
    fun normalize(observation: RemoteIdObservation): NormalizationResult {
        val id = observation.basicId ?: return NormalizationResult.Rejected("Missing aircraft Basic ID")
        val type = observation.identifierType ?: return NormalizationResult.Rejected("Missing or unsupported identifier type")
        if (type == IdentifierType.SYNTHETIC) return NormalizationResult.Rejected("Legacy synthetic type is not a Remote ID Basic ID type")
        return try {
            AircraftIdentity(type, id) // No guessed ID, MAC fallback, trimming or case folding.
            val lat = observation.latitude
            val lon = observation.longitude
            // OpenDroneID (0,0) is unavailable, never a precise aircraft position.
            val position = if (observation.aircraftLocation != null) observation.aircraftLocation.position else if (lat == null || lon == null || (lat == 0.0 && lon == 0.0)) null else {
                try {
                    Position(lat, lon, observation.altitudeHaeMeters?.takeIf {
                        it.isFinite() && it > -1000.0 && it <= 31767.5
                    })
                } catch (_: IllegalArgumentException) { null }
            }
            NormalizationResult.Accepted(Detection(id, observation.aircraftLocation?.observedAt ?: observation.observedAt, observation.receivedAt,
                position, observation.source, type,
                observation.speedMps?.takeIf { it.isFinite() && it in 0.0..254.25 },
                observation.headingDegrees?.takeIf { it.isFinite() && it in 0.0..360.0 }?.let { if (it == 360.0) 0.0 else it },
                observation.aircraftLocation, observation.controlStationLocation, observation.takeoffLocation, observation.receiverLocation))
        } catch (e: IllegalArgumentException) {
            NormalizationResult.Rejected(e.message ?: "Invalid observation")
        }
    }
}
