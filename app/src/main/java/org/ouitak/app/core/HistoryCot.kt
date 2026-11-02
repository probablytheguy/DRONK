package org.ouitak.app.core

import java.math.BigDecimal
import java.time.Duration
import java.time.Instant
import java.time.format.DateTimeFormatterBuilder

internal fun xmlText(value: String) = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;").replace("'", "&apos;")
internal fun decimal(value: Double) = BigDecimal.valueOf(value).toPlainString()
internal fun cotTime(value: Instant) = DateTimeFormatterBuilder().appendInstant(3).toFormatter().format(value)

/** One open drawing per aircraft/presentation, not one permanent marker per fix.
 * Vertices are actual observations. Connecting lines are visual, not inferred fixes.
 */
class TrailCotGenerator {
    fun generate(points: List<Detection>, displayAt: Instant, staleAfter: Duration = Duration.ofSeconds(60),
                 replayUid: String? = null): String {
        validateStaleAfter(staleAfter)
        require(points.size in 2..100 && points.all { it.position != null })
        val uid = points.first().cotUid
        require(points.all { it.cotUid == uid }) { "Mixed aircraft in trail" }
        require(points.zipWithNext().all { (a,b) -> a.observedAt <= b.observedAt }) { "Unordered trail" }
        val last = points.last(); val p = requireNotNull(last.position)
        val trailUid = "ouitak-trail-${replayUid ?: uid}"
        val simulated = replayUid != null || points.any { it.source == ObservationSource.SIMULATION }
        val label = "${if (replayUid != null) "REPLAY HISTORY" else if (simulated) "SIM HISTORY" else "HISTORY"} ${last.identifier}"
        val links = points.joinToString("") { d ->
            val pos = requireNotNull(d.position)
            "<link point=\"${decimal(pos.latitude)},${decimal(pos.longitude)}${pos.haeMeters?.let { ",${decimal(it)}" } ?: ""}\"/>"
        }
        // Opaque white / pale yellow are valid Android ARGB ints and also fit
        // the conservative xs:byte color range in the public Free Form XSD.
        val color = if (last.identifier.endsWith("002")) -128 else -1
        return """<?xml version="1.0" encoding="UTF-8"?>
<event version="2.0" uid="$trailUid" type="u-d-f" time="${cotTime(displayAt)}" start="${cotTime(displayAt)}" stale="${cotTime(displayAt.plus(staleAfter))}" how="${if(simulated) "m-s" else "m-r"}"><point lat="${decimal(p.latitude)}" lon="${decimal(p.longitude)}" hae="${decimal(p.haeMeters ?: 9999999.0)}" ce="9999999.0" le="9999999.0"/><detail>$links<strokeColor value="$color"/><strokeWeight value="5.0"/><fillColor value="0"/><contact callsign="${xmlText(label)}"/><remarks>UNVERIFIED historical observations for aircraft UID ${xmlText(replayUid ?: uid)}. First ${cotTime(points.first().observedAt)}; last ${cotTime(last.observedAt)}. Lines connect observations; they are not additional observed positions.</remarks><archive/><labels_on value="true"/><color value="-1"/><precisionlocation altsrc="???"/></detail></event>"""
    }
}

/** Independent reported-location markers, never substitute them for aircraft position. */
class LocationCotGenerator(private val staleAfter: Duration = Duration.ofSeconds(60)) {
    init { validateStaleAfter(staleAfter) }
    fun generate(detection: Detection, role: LocationRole, now: Instant, replay: ReplayFrame? = null): String? {
        require(role != LocationRole.AIRCRAFT)
        val report = detection.locations[role] ?: return null
        val p = report.position ?: return null
        if (report.validity != LocationValidity.VALID || report.observedAt > now) return null
        if (replay == null && !report.observedAt.plus(staleAfter).isAfter(now)) return null
        val display = replay?.displayAt ?: report.observedAt
        val aircraftUid = replay?.uid ?: detection.cotUid
        val label = "${if (replay != null) "REPLAY " else if(report.provenance == LocationProvenance.SIMULATION) "SIM " else ""}UNVERIFIED ${role.name.replace('_',' ')} ${detection.identifier}"
        return """<?xml version="1.0" encoding="UTF-8"?>
<event version="2.0" uid="ouitak-location-$aircraftUid-${role.name}" type="b-m-p-s-m" time="${cotTime(display)}" start="${cotTime(display)}" stale="${cotTime(display.plus(staleAfter))}" how="${if (replay != null || report.provenance == LocationProvenance.SIMULATION) "m-s" else "m-r"}"><point lat="${decimal(p.latitude)}" lon="${decimal(p.longitude)}" hae="${decimal(p.haeMeters ?: 9999999.0)}" ce="9999999.0" le="9999999.0"/><detail><contact callsign="${xmlText(label)}"/><remarks>${xmlText(label)}. Associated aircraft UID ${xmlText(aircraftUid)}. Reported ${cotTime(report.observedAt)}; provenance ${report.provenance}; validity ${report.validity}. Takeoff is not a report of the operator's current position.</remarks><ouitak verified="false" aircraftUid="${xmlText(aircraftUid)}" role="$role" originalObservedAt="${cotTime(report.observedAt)}" provenance="${report.provenance}"/></detail></event>"""
    }
}

/** ATAK 5.5.1.8 CotDeleteImporter: delete only this application's presentations.
 * The point is an actual last displayed position, never a fabricated fix.
 */
class PresentationRemovalCot {
    fun generate(uid: String, position: Position, now: Instant): String {
        require(uid.startsWith("ouitak-trail-") || uid.startsWith("ouitak-replay-") || uid.startsWith("ouitak-location-ouitak-replay-"))
        return """<?xml version="1.0" encoding="UTF-8"?>
<event version="2.0" uid="ouitak-remove-${xmlText(uid)}" type="t-x-d-d" time="${cotTime(now)}" start="${cotTime(now)}" stale="${cotTime(now.plusSeconds(60))}" how="m-g"><point lat="${decimal(position.latitude)}" lon="${decimal(position.longitude)}" hae="${decimal(position.haeMeters ?: 9999999.0)}" ce="9999999.0" le="9999999.0"/><detail><link uid="${xmlText(uid)}" relation="none" type="none"/><__forcedelete/></detail></event>"""
    }
}
