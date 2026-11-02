package org.ouitak.app.core

import java.math.BigDecimal
import java.time.Duration
import java.time.format.DateTimeFormatterBuilder

/** Legacy CoT XML; generation is independent of delivery or ATAK ingestion. */
/**
 * Outbound symbology (DRONK forwarding only). LIVE markers use standard MIL-STD-2525 CoT types so no client icon set
 * is needed: rotary-wing drone a-u-A-M-H-Q, fixed-wing drone a-u-A-M-F-Q (others a-u-A), operator a-u-G (ground
 * frame, distinct from the air frame). LAST KNOWN: [SPOT] turns the same UID into a coloured map spot (b-m-p-s-m) so a
 * last-known position can't be mistaken for a live track; [SAME_TYPE] keeps the type and only relabels and recolours.
 */
enum class LastKnownStyle { SPOT, SAME_TYPE }

class CotGenerator(private val staleAfter: Duration = Duration.ofSeconds(60), private val lastKnownStyle: LastKnownStyle = LastKnownStyle.SPOT) {
    companion object {
        const val AIRCRAFT_LAST_KNOWN_ARGB = -5197648   // 0xFFB0B0B0 light grey
        const val GROUND_LAST_KNOWN_ARGB = -11513776    // 0xFF505050 dark grey
        const val TAKEOFF_ARGB = -256                   // 0xFFFFFF00 yellow spot

        /** Standard CoT type for a broadcast aircraft, from the Remote ID UA type (ASTM F3411). */
        fun aircraftType(detection: Detection): String = when (detection.dronk?.uaType) {
            2, 3, 4 -> "a-u-A-M-H-Q"   // helicopter/multirotor, gyroplane, hybrid lift: rotary-wing UAV
            1, 5, 6 -> "a-u-A-M-F-Q"   // aeroplane, ornithopter, glider: fixed-wing UAV
            else -> "a-u-A"
        }
    }
    init { validateStaleAfter(staleAfter) }

    fun generate(detection: Detection): String = event(detection, detection.observedAt, detection.cotUid, detection.identifier, false)

    /** Only the outbound copy is filtered; local detection/exports remain intact. */
    fun generateForForwarding(detection: Detection, forwardPilotLocation: Boolean = false): String =
        event(detection, detection.observedAt, detection.cotUid, forwardingCallsign(detection), false, forwardPilotLocation,
            cotType = if (detection.dronk != null) aircraftType(detection) else "a-u-A")

    /** DRONK markers use the serial's last four characters as the map label; the full identifier stays in the
     * ouitak metadata and remarks. Other sources keep the full identifier. */
    fun forwardingCallsign(detection: Detection): String =
        if (detection.dronk != null && detection.identifier.length > 4) detection.identifier.takeLast(4) else detection.identifier

    /**
     * Separate outbound markers for the broadcast control-station (operator) and take-off locations, each with its
     * own UID linked to the aircraft. Only a VALID broadcast position produces one; nothing is derived from the
     * aircraft position. Empty when pilot-location forwarding is off.
     */
    fun generateGroundLocationsForForwarding(detection: Detection, forwardPilotLocation: Boolean): List<String> =
        if (!forwardPilotLocation) emptyList() else listOfNotNull(
            groundEvent(detection, detection.controlStationLocation, LastKnownMonitor.Role.OPERATOR),
            groundEvent(detection, detection.takeoffLocation, LastKnownMonitor.Role.TAKEOFF))

    /**
     * One LAST KNOWN update for a marker that stopped being refreshed: same UID (so TAK updates the existing marker),
     * the role's own last valid broadcast position, the real last-seen time, no kinematics, and a stale time at the
     * end of the last-known retention rather than a live refresh interval.
     */
    fun generateLastKnownForForwarding(marker: LastKnownMonitor.Marker, report: LastKnownReport, forwardPilotLocation: Boolean): String? =
        when (marker.role) {
            LastKnownMonitor.Role.AIRCRAFT -> event(marker.detection, marker.detection.observedAt, marker.detection.cotUid, forwardingCallsign(marker.detection),
                false, forwardPilotLocation, report,
                cotType = if (lastKnownStyle == LastKnownStyle.SPOT) "b-m-p-s-m" else aircraftType(marker.detection), argb = AIRCRAFT_LAST_KNOWN_ARGB)
            else -> if (forwardPilotLocation) groundEvent(marker.detection, marker.location, marker.role, report) else null
        }

    private fun groundEvent(detection: Detection, location: ReportedLocation?, role: LastKnownMonitor.Role, lastKnown: LastKnownReport? = null): String? {
        val p = location?.takeIf { it.validity == LocationValidity.VALID }?.position ?: return null
        if (p.latitude == 0.0 && p.longitude == 0.0) return null
        val (label, what) = if (role == LastKnownMonitor.Role.TAKEOFF) "TAKEOFF" to "take-off location" else "OPERATOR" to "control station (operator) location"
        val format = DateTimeFormatterBuilder().appendInstant(3).toFormatter()
        val seen = format.format(location.observedAt)
        val time = lastKnown?.let { format.format(it.reportedAt) } ?: seen
        val stale = format.format(lastKnown?.staleAt ?: location.observedAt.plus(staleAfter))
        val synthetic = detection.source == ObservationSource.SIMULATION
        val aircraft = detection.cotUid
        val state = if (lastKnown != null) " state=\"LAST_KNOWN\" lastKnownReason=\"${lastKnown.reason}\"" else " state=\"LIVE\""
        val takeoff = role == LastKnownMonitor.Role.TAKEOFF
        val type = if (lastKnown != null && lastKnownStyle == LastKnownStyle.SPOT || takeoff) "b-m-p-s-m" else "a-u-G"
        val color = if (lastKnown != null) GROUND_LAST_KNOWN_ARGB else if (takeoff) TAKEOFF_ARGB else null
        val aircraftCotType = if (detection.dronk != null) aircraftType(detection) else "a-u-A"
        val remarks = (lastKnown?.let { "LAST KNOWN, not live: last broadcast $seen (${reasonText(it.reason)}). " } ?: "") +
            "Remote ID broadcast $what for ${escape(detection.identifier)}: an unverified claim, not the aircraft position."
        return """<?xml version="1.0" encoding="UTF-8"?>
<event version="2.0" uid="$aircraft-${role.uidSuffix}" type="$type" how="${if (synthetic) "m-s" else "m-r"}" time="$time" start="$time" stale="$stale"><point lat="${decimal(p.latitude)}" lon="${decimal(p.longitude)}" hae="${decimal(p.haeMeters ?: 9999999.0)}" ce="9999999.0" le="9999999.0"/><detail><contact callsign="${escape("${if (synthetic) "SIMULATION " else ""}${forwardingCallsign(detection)} $label${if (lastKnown != null) " LAST KNOWN" else ""}")}"/>${color?.let { "<color argb=\"$it\"/>" } ?: ""}<link uid="$aircraft" type="$aircraftCotType" relation="p-p"/><ouitak version="1" verified="false" synthetic="$synthetic" role="${role.wire}" identifier="${escape(detection.identifier)}" aircraftUid="$aircraft" lastSeen="$seen"$state/><remarks>$remarks</remarks></detail></event>"""
    }

    private fun reasonText(reason: String) = when (reason) {
        LastKnownMonitor.Reason.RECEIVER_INTERRUPTED.wire -> "receiver coverage was interrupted; the aircraft may still be transmitting"
        LastKnownMonitor.Reason.MONITORING_STOPPED.wire -> "DRONK monitoring stopped"
        else -> "no Remote ID received since"
    }

    fun generateReplay(frame: ReplayFrame): String = event(frame.original, frame.displayAt, frame.uid,
        "REPLAY ${frame.original.identifier}", true)

    private fun event(detection: Detection, displayAt: java.time.Instant, uid: String, callsign: String, replay: Boolean, pilot: Boolean = true,
                      lastKnown: LastKnownReport? = null, cotType: String = "a-u-A", argb: Int? = null): String {
        val p = requireNotNull(detection.position) { "Cannot create a located CoT event without coordinates" }
        // ATAK timestamps are millisecond-based; avoid variable nanosecond precision.
        val format = DateTimeFormatterBuilder().appendInstant(3).toFormatter()
        val timeInstant = if (!replay && detection.identifierType == IdentifierType.PROVISIONAL_UNTYPED) detection.receivedAt else displayAt
        val time = format.format(lastKnown?.reportedAt ?: timeInstant)
        val originalTime = format.format(detection.observedAt)
        val stale = format.format(lastKnown?.staleAt ?: displayAt.plus(staleAfter))
        val how = if (replay || detection.source == ObservationSource.SIMULATION) "m-s" else "m-r"
        val provisional = detection.identifierType == IdentifierType.PROVISIONAL_UNTYPED
        val synthetic = detection.source == ObservationSource.SIMULATION
        val label = if (replay) "HISTORICAL REPLAY SIMULATION" else if (synthetic && provisional) "SYNTHETIC SKY SPY SERIAL" else if (synthetic) "SIMULATION" else if (provisional) "PROVISIONAL OUI-SPY USB" else if (detection.dronk != null) "DRONK C5 USB" else "OUI-SPY USB"
        val kinematics = if (lastKnown == null && detection.speedMps != null && detection.headingDegrees != null)
            "<track speed=\"${decimal(detection.speedMps)}\" course=\"${decimal(detection.headingDegrees)}\"/>" else ""
        // Project-owned extension, not an assumed ATAK API. Preserve partial data.
        val claims = buildString {
            append("<ouitak version=\"1\" verified=\"false\" synthetic=\"$synthetic\" identifier=\"${escape(detection.identifier)}\" identifierType=\"${detection.identifierType}\" replay=\"$replay\"")
            if (provisional) append(" timeBasis=\"receiver\" receivedAt=\"${format.format(detection.receivedAt)}\" identityNamespace=\"receiver-scoped\"")
            else append(" lastSeen=\"$originalTime\" originalObservedAt=\"$originalTime\"")
            p.haeMeters?.let { append(" altitudeHaeMeters=\"${decimal(it)}\"") }
            lastKnown?.let { append(" state=\"LAST_KNOWN\" lastKnownReason=\"${it.reason}\" lastKnownReportedAt=\"${format.format(it.reportedAt)}\"") }
            detection.speedMps?.let { append(" speedMps=\"${decimal(it)}\"") }
            detection.headingDegrees?.let { append(" headingDegrees=\"${decimal(it)}\"") }
            append("/>")
            detection.dronk?.let { m ->
                append("<dronk version=\"1\" authenticated=\"false\" timeBasis=\"host-receipt-minus-device-queue-delay\"")
                append(" receiverId=\"${escape(m.publicReceiverId ?: m.stream)}\" sessionId=\"${escape(m.sessionId)}\" stream=\"${escape(m.stream)}\"")
                append(" seq=\"${m.sequence}\" uptimeUs=\"${m.uptimeUs}\" receivedUptimeUs=\"${m.receivedUptimeUs}\"")
                append(" transport=\"${escape(m.transport)}\" channel=\"${m.channel}\" frequencyMhz=\"${m.frequencyMhz}\" rssiDbm=\"${m.rssiDbm}\"")
                append(" transmitter=\"${escape(m.transmitter)}\" messageCounter=\"${m.messageCounter}\" uaType=\"${m.uaType}\"")
                m.locationSecondsAfterHour?.let { append(" locationSecondsAfterHour=\"${decimal(it)}\"") }
                if (pilot) {
                    m.operatorSecondsSince2019?.let { append(" operatorSecondsSince2019=\"$it\"") }
                    m.operatorLocationType?.let { append(" operatorLocationType=\"$it\"") }
                    append("><operator validity=\"${m.operatorLocation.validity}\"")
                    m.operatorLocation.position?.let { op ->
                        append(" lat=\"${decimal(op.latitude)}\" lon=\"${decimal(op.longitude)}\"")
                        op.haeMeters?.let { append(" altitudeHaeMeters=\"${decimal(it)}\"") }
                    }
                } else append("><operator forwarded=\"false\"")
                append("/></dronk>")
            }
        }
        return """<?xml version="1.0" encoding="UTF-8"?>
<event version="2.0" uid="$uid" type="$cotType" how="$how" time="$time" start="$time" stale="$stale"><point lat="${decimal(p.latitude)}" lon="${decimal(p.longitude)}" hae="${decimal(p.haeMeters ?: 9999999.0)}" ce="9999999.0" le="9999999.0"/><detail><contact callsign="${escape((if(synthetic && provisional && !replay) "SYNTHETIC PROVISIONAL $callsign" else if (provisional && !replay) "PROVISIONAL $callsign" else callsign) + if (lastKnown != null) " LAST KNOWN" else "")}"/>${argb?.let { "<color argb=\"$it\"/>" } ?: ""}$kinematics$claims<remarks>${lastKnown?.let { "LAST KNOWN, not live: last Remote ID reception $originalTime (${reasonText(it.reason)}). " } ?: ""}$label: ${if (provisional) "untyped Basic ID; event time is receiver time, not a verified broadcast timestamp" else "identifier, coordinates and kinematics are unverified broadcast claims; synthetic when simulated"}.</remarks></detail></event>"""
    }

    private fun decimal(value: Double) = BigDecimal.valueOf(value).toPlainString()

    private fun escape(value: String): String = buildString {
        value.forEach { append(when (it) {
            '&' -> "&amp;"; '<' -> "&lt;"; '>' -> "&gt;"; '\"' -> "&quot;"; '\'' -> "&apos;"
            else -> it.toString()
        }) }
    }
}
