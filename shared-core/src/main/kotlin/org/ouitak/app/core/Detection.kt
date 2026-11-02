package org.ouitak.app.core

import java.time.Instant

enum class ObservationSource { SIMULATION, OUI_SPY_USB, DRONK_C5_USB }

/** WGS84 degrees; altitude is metres above the WGS84 ellipsoid, when known. */
data class Position(val latitude: Double, val longitude: Double, val haeMeters: Double? = null) {
    init {
        require(latitude.isFinite() && latitude in -90.0..90.0) { "Invalid latitude" }
        require(longitude.isFinite() && longitude in -180.0..180.0) { "Invalid longitude" }
        require(haeMeters == null || haeMeters.isFinite()) { "Invalid altitude" }
    }
}

/** Internal normalized observation, NOT an upstream firmware wire format. */
data class Detection(
    val identifier: String,
    val observedAt: Instant,
    val receivedAt: Instant,
    val position: Position?,
    val source: ObservationSource,
    val identifierType: IdentifierType = IdentifierType.SYNTHETIC,
    val speedMps: Double? = null,
    /** Direction of motion relative to true north; not aircraft yaw. */
    val headingDegrees: Double? = null,
    val aircraftLocationReport: ReportedLocation? = null,
    val controlStationLocation: ReportedLocation? = null,
    val takeoffLocation: ReportedLocation? = null,
    val receiverLocation: ReportedLocation? = null,
    val provisionalNamespace: String? = null,
    val dronk: DronkReception? = null
) {
    val identity: AircraftIdentity get() = AircraftIdentity(identifierType, identifier)
    val cotUid: String get() = identity.cotUid(source == ObservationSource.SIMULATION, provisionalNamespace)

    val locations: Map<LocationRole, ReportedLocation> get() = buildMap {
        put(LocationRole.AIRCRAFT, aircraftLocationReport ?: ReportedLocation(position, observedAt,
            if (source == ObservationSource.SIMULATION) LocationProvenance.SIMULATION else LocationProvenance.REMOTE_ID_BROADCAST,
            if (position != null) LocationValidity.VALID else LocationValidity.UNAVAILABLE))
        controlStationLocation?.let { put(LocationRole.CONTROL_STATION, it) }
        takeoffLocation?.let { put(LocationRole.TAKEOFF, it) }
        receiverLocation?.let { put(LocationRole.RECEIVER, it) }
    }

    init {
        require(aircraftLocationReport == null ||
            (aircraftLocationReport.position == position && aircraftLocationReport.observedAt == observedAt))
        AircraftIdentity(identifierType, identifier)
        require((identifierType == IdentifierType.PROVISIONAL_UNTYPED) == !provisionalNamespace.isNullOrBlank()) {
            "Only provisional identities carry a receiver namespace"
        }
        require(speedMps == null || (speedMps.isFinite() && speedMps >= 0)) { "Invalid speed" }
        require(headingDegrees == null || (headingDegrees.isFinite() && headingDegrees >= 0 && headingDegrees < 360)) { "Invalid heading" }
        require(identifier.isNotBlank() && identifier.length <= 128) { "Invalid identifier" }
        require(identifier.all { it.code in 0x20..0x7e }) { "Identifier must be printable ASCII" }
        require(!receivedAt.isBefore(observedAt)) { "Receipt precedes observation" }
        require(observedAt >= Instant.EPOCH && receivedAt <= Instant.parse("9999-12-31T23:58:59Z"))
    }
}
