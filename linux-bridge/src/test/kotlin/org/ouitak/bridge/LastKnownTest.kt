package org.ouitak.bridge

import org.junit.Assert.*
import org.junit.Test
import org.ouitak.app.core.*
import org.ouitak.app.core.serial.*
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatterBuilder

/** LIVE -> LAST KNOWN -> LIVE marker lifecycle and its outbound CoT, from the real observation fixture. */
class LastKnownTest {
    private class Time(var now: Instant = Instant.parse("2026-11-02T20:00:00Z")) : Clock() {
        override fun instant() = now
        override fun getZone(): ZoneId = ZoneOffset.UTC
        override fun withZone(zone: ZoneId): Clock = this
    }
    private val time = Time()
    private val fixture = javaClass.getResource("/dronk/observation.json")!!.readText()
    private val retention = Duration.ofSeconds(600)
    private fun detection(): Detection {
        val out = mutableListOf<ProcessedSerialDetection>()
        DronkReceiverHub(mapOf("a" to null), clock = time, onProcessed = out::add).also { it.connected("a"); it.feed("a", fixture.toByteArray(), time.now) }
        return out.single().detection  // fixture: aircraft 38.0,-90.0; operator type 0 (take-off) 38.1,-90.1; UA type 2
    }
    private fun operator(d: Detection, at: Instant = d.observedAt) =
        d.copy(controlStationLocation = ReportedLocation(Position(38.2, -90.2, 150.0), at, LocationProvenance.REMOTE_ID_BROADCAST, LocationValidity.VALID),
            takeoffLocation = null)
    private fun monitor() = LastKnownMonitor(Duration.ofSeconds(60), retention)

    @Test fun silenceBecomesLastKnownOnceAndResumesWithTheSameUids() {
        val d = operator(detection())
        val m = monitor()
        assertEquals(emptyList<LastKnownMonitor.Marker>(), m.live(d, true))
        assertEquals(emptyList<LastKnownMonitor.Transition>(), m.due(d.observedAt.plusSeconds(59)) { true })
        val due = m.due(d.observedAt.plusSeconds(60)) { true }
        assertEquals(setOf(d.cotUid, "${d.cotUid}-operator"), due.map { it.marker.uid }.toSet())
        assertTrue(due.all { it.reason == LastKnownMonitor.Reason.NO_RECEPTIONS })
        assertEquals(emptyList<LastKnownMonitor.Transition>(), m.due(d.observedAt.plusSeconds(120)) { true })  // never re-reported as current
        val again = d.copy(observedAt = d.observedAt.plusSeconds(200), receivedAt = d.receivedAt.plusSeconds(200),
            aircraftLocationReport = d.aircraftLocationReport?.copy(observedAt = d.observedAt.plusSeconds(200)))
        val restored = m.live(operator(again, again.observedAt), true)
        assertEquals(setOf(d.cotUid, "${d.cotUid}-operator"), restored.map { it.uid }.toSet())
        assertEquals(false, m.lastKnown(d.cotUid))
    }

    @Test fun lostReceiverCoverageIsInterruptionNotDisappearance() {
        val d = detection()
        val m = monitor(); m.live(d, false)
        val t = m.due(d.observedAt.plusSeconds(61)) { false }.single()
        assertEquals(LastKnownMonitor.Reason.RECEIVER_INTERRUPTED, t.reason)
        assertEquals(LastKnownMonitor.Role.AIRCRAFT, t.marker.role)  // ground roles not tracked without pilot forwarding
    }

    @Test fun operatorAgesOnItsOwnBroadcastAndNeverBorrowsAircraftCoordinates() {
        val first = operator(detection())
        val m = monitor(); m.live(first, true)
        val later = first.copy(observedAt = first.observedAt.plusSeconds(40), receivedAt = first.receivedAt.plusSeconds(40),
            aircraftLocationReport = first.aircraftLocationReport?.copy(observedAt = first.observedAt.plusSeconds(40)),
            controlStationLocation = null, takeoffLocation = null)
        m.live(later, true)  // aircraft still broadcasting, operator position no longer included
        val t = m.due(first.observedAt.plusSeconds(60)) { true }.single()
        assertEquals("${first.cotUid}-operator", t.marker.uid)
        assertEquals(38.2, t.marker.location.position!!.latitude, 0.0)
        assertEquals(first.observedAt, t.marker.lastSeen)
        val xml = CotGenerator().generateLastKnownForForwarding(t.marker, t.report, true)!!
        assertTrue(xml.contains("lat=\"38.2\" lon=\"-90.2\""))
        assertFalse(xml.contains("lat=\"38.0\""))
    }

    @Test fun stopReportsEveryLiveMarkerAndRetentionForgetsOldOnes() {
        val d = operator(detection())
        val m = monitor(); m.live(d, true)
        val stopped = m.stopAll(d.observedAt.plusSeconds(5))
        assertEquals(2, stopped.size)
        assertTrue(stopped.all { it.reason == LastKnownMonitor.Reason.MONITORING_STOPPED && it.report.staleAt == d.observedAt.plusSeconds(5).plus(retention) })
        m.due(d.observedAt.plusSeconds(60).plus(retention).plusSeconds(1)) { true }
        assertNull(m.lastKnown(d.cotUid))
    }

    @Test fun cotSymbologyAndLastKnownLabelling() {
        val d = operator(detection())
        val cot = CotGenerator()
        val live = cot.generateForForwarding(d, true)
        assertTrue(live.contains("uid=\"${d.cotUid}\" type=\"a-u-A-M-H-Q\""))  // UA type 2: rotary-wing UAV
        assertTrue(live.contains("callsign=\"TEST\""))                       // last four characters on the map
        assertTrue(live.contains("identifier=\"SYNTH-C5-TEST\""))            // full serial in metadata
        val liveOperator = cot.generateGroundLocationsForForwarding(d, true).single()
        assertTrue(liveOperator.contains("type=\"a-u-G\"") && liveOperator.contains("callsign=\"TEST OPERATOR\""))
        assertTrue(liveOperator.contains("<link uid=\"${d.cotUid}\" type=\"a-u-A-M-H-Q\" relation=\"p-p\"/>"))

        val m = monitor(); m.live(d, true)
        val now = d.observedAt.plusSeconds(60)
        val (aircraft, ground) = m.due(now) { true }.partition { it.marker.role == LastKnownMonitor.Role.AIRCRAFT }
        val lk = cot.generateLastKnownForForwarding(aircraft.single().marker, aircraft.single().report, true)!!
        assertTrue(lk.contains("uid=\"${d.cotUid}\" type=\"b-m-p-s-m\""))     // same UID, no longer an air track symbol
        assertTrue(lk.contains("callsign=\"TEST LAST KNOWN\""))
        val ms = DateTimeFormatterBuilder().appendInstant(3).toFormatter()
        assertTrue(lk.contains("time=\"${ms.format(now)}\" start=\"${ms.format(now)}\" stale=\"${ms.format(now.plus(retention))}\""))
        assertTrue(lk.contains("lastSeen=\"${ms.format(d.observedAt)}\""))               // actual last broadcast, not the report time
        assertTrue(lk.contains("lat=\"38.0\" lon=\"-90.0\""))
        assertFalse(lk.contains("<track "))                                    // no kinematics on a last-known position
        assertTrue(lk.contains("<color argb=\"${CotGenerator.AIRCRAFT_LAST_KNOWN_ARGB}\"/>"))
        val lkOperator = cot.generateLastKnownForForwarding(ground.single().marker, ground.single().report, true)!!
        assertTrue(lkOperator.contains("uid=\"${d.cotUid}-operator\" type=\"b-m-p-s-m\""))
        assertTrue(lkOperator.contains("callsign=\"TEST OPERATOR LAST KNOWN\""))
        assertTrue(lkOperator.contains("<color argb=\"${CotGenerator.GROUND_LAST_KNOWN_ARGB}\"/>"))
        assertNull(cot.generateLastKnownForForwarding(ground.single().marker, ground.single().report, false))  // pilot forwarding off
        val sameType = CotGenerator(lastKnownStyle = LastKnownStyle.SAME_TYPE).generateLastKnownForForwarding(aircraft.single().marker, aircraft.single().report, true)!!
        assertTrue(sameType.contains("type=\"a-u-A-M-H-Q\"") && sameType.contains("TEST LAST KNOWN"))
    }
}
