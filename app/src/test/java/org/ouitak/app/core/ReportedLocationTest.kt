package org.ouitak.app.core

import org.junit.Assert.*
import org.junit.Test
import java.time.Duration
import java.time.Instant
import java.util.UUID

class ReportedLocationTest {
    private val now=Instant.parse("2026-01-02T00:00:00Z")
    private val d=SyntheticHistory.observations(now).last { it.identifier=="SIM-DRONE-002" }
    private fun xml(d:Detection,role:LocationRole,at:Instant=now)=LocationCotGenerator(Duration.ofSeconds(60)).generate(d,role,at)
    @Test fun independentReportsPreserveOwnTimeSourceValidityAndCoordinates() {
        assertEquals(4,d.locations.size)
        assertNotEquals(d.controlStationLocation!!.observedAt,d.takeoffLocation!!.observedAt)
        assertNotEquals(d.position,d.controlStationLocation!!.position)
        val receiver=d.copy(receiverLocation=d.receiverLocation!!.copy(provenance=LocationProvenance.SENSOR_RECEIVER))
        assertEquals(LocationProvenance.SENSOR_RECEIVER,receiver.locations.getValue(LocationRole.RECEIVER).provenance)
    }
    @Test fun missingReportsNeverProduceMarkersOrBorrowAnotherLocationsCoordinates() {
        val empty=Simulator().next(now)
        assertNull(xml(empty,LocationRole.CONTROL_STATION)); assertNull(xml(empty,LocationRole.TAKEOFF)); assertNull(xml(empty,LocationRole.RECEIVER))
        val takeoffOnly=empty.copy(takeoffLocation=d.takeoffLocation)
        assertNull(xml(takeoffOnly,LocationRole.CONTROL_STATION)); assertNotNull(xml(takeoffOnly,LocationRole.TAKEOFF))
    }
    @Test fun invalidAndUnavailableCoordinatesHaveNoPosition() {
        listOf(Double.NaN,Double.POSITIVE_INFINITY,91.0).forEach { latitude ->
            val report=ReportedLocation.fromCoordinates(latitude,1.0,now,LocationProvenance.REMOTE_ID_BROADCAST)
            assertEquals(LocationValidity.INVALID,report.validity); assertNull(report.position)
            assertNull(xml(d.copy(controlStationLocation=report),LocationRole.CONTROL_STATION))
        }
        assertEquals(LocationValidity.UNAVAILABLE,ReportedLocation.fromCoordinates(null,1.0,now,LocationProvenance.REMOTE_ID_BROADCAST).validity)
        assertEquals(LocationValidity.UNAVAILABLE,ReportedLocation.fromCoordinates(0.0,0.0,now,LocationProvenance.REMOTE_ID_BROADCAST).validity)
    }
    @Test fun associatedMarkersHaveSeparateUidsForAircraftAndRoles() {
        val a=SyntheticHistory.observations(now).first()
        val aControl=xml(a,LocationRole.CONTROL_STATION)!!
        val bControl=xml(d,LocationRole.CONTROL_STATION)!!
        assertTrue(aControl.contains("ouitak-location-${a.cotUid}-CONTROL_STATION"))
        assertTrue(bControl.contains("ouitak-location-${d.cotUid}-CONTROL_STATION"))
        assertFalse(aControl.contains(d.cotUid)); assertNotEquals(aControl,bControl)
        assertTrue(xml(a,LocationRole.TAKEOFF)!!.contains("${a.cotUid}-TAKEOFF"))
        assertTrue(aControl.contains("UNVERIFIED CONTROL STATION"))
    }
    @Test fun expiryUsesEachLocationTimeAndNotAircraftTime() {
        val old=d.controlStationLocation!!.copy(observedAt=now.minusSeconds(60))
        assertNull(xml(d.copy(controlStationLocation=old),LocationRole.CONTROL_STATION))
        assertNotNull(xml(d,LocationRole.TAKEOFF))
        assertNull(xml(d.copy(controlStationLocation=old.copy(observedAt=now.plusSeconds(1))),LocationRole.CONTROL_STATION))
    }
    @Test fun controlOnlyDoesNotFabricateAnAircraftAndReplayIsIsolated() {
        val only=RemoteIdObservation("SIM-CONTROL",IdentifierType.SERIAL_NUMBER,now,now,null,null,controlStationLocation=d.controlStationLocation)
        val result=(RemoteIdNormalizer.normalize(only) as NormalizationResult.Accepted).detection
        assertNull(result.position); assertTrue(AircraftTracker().observe(result,now) is TrackUpdate.Ignored)
        assertNotNull(xml(result,LocationRole.CONTROL_STATION))
        val frame=ReplayFrame(d,now,UUID(0,2))
        assertTrue(LocationCotGenerator().generate(d,LocationRole.CONTROL_STATION,now,frame)!!.contains(frame.uid))
    }
    @Test fun invalidTimestampAndInconsistentValidityAreRejected() {
        assertThrows(IllegalArgumentException::class.java) { ReportedLocation(Position(1.0,2.0),Instant.EPOCH.minusSeconds(1),LocationProvenance.SIMULATION,LocationValidity.VALID) }
        assertThrows(IllegalArgumentException::class.java) { ReportedLocation(null,now,LocationProvenance.SIMULATION,LocationValidity.VALID) }
        assertThrows(java.time.format.DateTimeParseException::class.java) { Instant.parse("2026-not-a-time") }
    }
}
