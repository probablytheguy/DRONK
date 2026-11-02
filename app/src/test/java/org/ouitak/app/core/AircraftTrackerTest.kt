package org.ouitak.app.core

import org.junit.Assert.*
import org.junit.Test
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.time.ZoneOffset

class AircraftTrackerTest {
    private val time = Instant.parse("2026-01-01T00:00:00Z")
    private fun observation(id: String = "SIM-DRONE-001", at: Instant = time, lat: Double? = 0.001) =
        RemoteIdObservation(id, IdentifierType.SERIAL_NUMBER, at, at, lat, 0.002,
            altitudeHaeMeters = 30.0, speedMps = 2.0, headingDegrees = 45.0)
    private fun detection(o: RemoteIdObservation) = (RemoteIdNormalizer.normalize(o) as NormalizationResult.Accepted).detection
    private fun tracker() = AircraftTracker(clock = Clock.fixed(time, ZoneOffset.UTC))

    @Test fun extractsTypedBroadcastIdentifierWithoutUsingReceiverIdentity() {
        val d = detection(observation())
        assertEquals("SIM-DRONE-001", d.identifier)
        assertEquals(IdentifierType.SERIAL_NUMBER, d.identifierType)
    }
    @Test fun stableUidAcrossInstancesPositionsAndTimesWithTypeAndSimulationIsolation() {
        val first = detection(observation())
        assertEquals(first.cotUid, detection(observation(at = time.plusSeconds(2), lat = 0.003)).cotUid)
        assertEquals("ouitak-rid-ed6b73d8-87d7-32de-bab1-4203787cc791", first.cotUid)
        assertEquals("ouitak-rid-564e6284-f477-379c-a773-74af831130fa", detection(observation("SIM-DRONE-002")).cotUid)
        assertNotEquals(first.cotUid, first.copy(identifierType = IdentifierType.CAA_REGISTRATION_ID).cotUid)
        assertNotEquals(first.cotUid, first.copy(source = ObservationSource.OUI_SPY_USB).cotUid)
    }
    @Test fun repeatedReportsUpdateOneTrackAndLastSeen() {
        val tracker = tracker()
        val first = tracker.observe(detection(observation())) as TrackUpdate.Updated
        val later = time.plusSeconds(20)
        val update = tracker.observe(detection(observation(at = later, lat = 0.004)), later) as TrackUpdate.Updated
        assertEquals(first.track.uid, update.track.uid)
        assertEquals(1, tracker.active(later).size)
        assertEquals(0.004, update.track.latest.position!!.latitude, 0.0)
        assertEquals(later, update.track.lastSeen)
        assertEquals(time.plusSeconds(80), update.track.expiresAt)
    }
    @Test fun simultaneousAircraftAndIdentifierTypesStaySeparate() {
        val tracker = tracker()
        tracker.observe(detection(observation()))
        tracker.observe(detection(observation("SIM-DRONE-002")))
        tracker.observe(detection(observation().copy(identifierType = IdentifierType.CAA_REGISTRATION_ID)))
        assertEquals(3, tracker.active().size)
        assertEquals(3, tracker.active().map { it.uid }.toSet().size)
    }
    @Test fun missingOrMalformedIdentifiersNeverGetSharedAnonymousIdentity() {
        listOf(null, "", " ", "a".repeat(21), "id\n", "\u0000", "é", " serial").forEach { id ->
            assertTrue(RemoteIdNormalizer.normalize(observation().copy(basicId = id)) is NormalizationResult.Rejected)
        }
        assertTrue(RemoteIdNormalizer.normalize(observation().copy(identifierType = null)) is NormalizationResult.Rejected)
        assertTrue(RemoteIdNormalizer.normalize(observation().copy(identifierType = IdentifierType.SYNTHETIC)) is NormalizationResult.Rejected)
    }
    @Test fun missingMalformedUnavailableCoordinatesNeverCreateOrRefreshMarker() {
        val tracker = tracker()
        tracker.observe(detection(observation()))
        val later = time.plusSeconds(30)
        listOf(observation(lat = null), observation(lat = Double.NaN), observation(lat = 91.0),
            observation().copy(longitude = Double.POSITIVE_INFINITY), observation(lat = 0.0).copy(longitude = 0.0)).forEach { o ->
            val d = detection(o.copy(observedAt = later, receivedAt = later))
            assertNull(d.position)
            assertTrue(tracker.observe(d, later) is TrackUpdate.Ignored)
            assertThrows(IllegalArgumentException::class.java) { CotGenerator().generate(d) }
        }
        assertEquals(time, tracker.active(later).single().lastSeen)
        assertTrue(tracker.active(time.plusSeconds(60)).isEmpty())
    }
    @Test fun expiryIsExactlySixtySecondsAfterLastLocatedObservation() {
        val tracker = tracker()
        tracker.observe(detection(observation()))
        assertEquals(1, tracker.active(time.plusSeconds(59)).size)
        assertTrue(tracker.active(time.plusSeconds(60)).isEmpty())
        assertTrue(tracker.observe(detection(observation()), time.plusSeconds(60)) is TrackUpdate.Ignored)
    }
    @Test fun configurableExpiryAndDuplicatesNeverRewindOrRefreshTracks() {
        val tracker = tracker()
        tracker.staleAfter = Duration.ofSeconds(120)
        val d = detection(observation())
        tracker.observe(d)
        assertTrue(tracker.observe(d) is TrackUpdate.Ignored)
        val later = time.plusSeconds(10)
        tracker.observe(detection(observation(at = later)), later)
        assertTrue(tracker.observe(d, later) is TrackUpdate.Ignored)
        assertEquals(later, tracker.active(time.plusSeconds(120)).single().lastSeen)
        assertTrue(tracker.active(time.plusSeconds(130)).isEmpty())
        listOf(Duration.ZERO, Duration.ofDays(2)).forEach { ttl ->
            assertThrows(IllegalArgumentException::class.java) { tracker.staleAfter = ttl }
        }
    }
    @Test fun unknownMotionValuesAreMissingAndNorthIsNormalized() {
        val d = detection(observation().copy(altitudeHaeMeters = -1000.0, speedMps = 255.0, headingDegrees = 361.0))
        assertNull(d.position!!.haeMeters); assertNull(d.speedMps); assertNull(d.headingDegrees)
        assertEquals(0.0, detection(observation().copy(headingDegrees = 360.0)).headingDegrees!!, 0.0)
        val invalid = detection(observation().copy(altitudeHaeMeters = Double.NaN, speedMps = -1.0, headingDegrees = Double.NaN))
        assertNull(invalid.position!!.haeMeters); assertNull(invalid.speedMps); assertNull(invalid.headingDegrees)
    }
    @Test fun futureAndInvalidTimestampReportsAreRejectedAndClearRemovesTracks() {
        val tracker = tracker()
        assertTrue(tracker.observe(detection(observation(at = time.plusSeconds(1)))) is TrackUpdate.Ignored)
        assertTrue(RemoteIdNormalizer.normalize(observation().copy(receivedAt = time.minusSeconds(1))) is NormalizationResult.Rejected)
        tracker.observe(detection(observation())); tracker.clear()
        assertTrue(tracker.active().isEmpty())
    }
    @Test fun changedIdentifiersStaySeparateWithoutProximityCorrelation() {
        val tracker = tracker()
        tracker.observe(detection(observation("OLD-ID")))
        tracker.observe(detection(observation("NEW-ID", time.plusSeconds(1))), time.plusSeconds(1))
        assertEquals(2, tracker.active(time.plusSeconds(1)).size)
    }
    @Test fun capacityBoundsNewIdentitiesWithoutBreakingExistingUpdates() {
        val tracker = AircraftTracker(clock = Clock.fixed(time, ZoneOffset.UTC), maxTracks = 1)
        tracker.observe(detection(observation()))
        assertTrue(tracker.observe(detection(observation("SECOND"))) is TrackUpdate.Ignored)
        assertTrue(tracker.observe(detection(observation(at = time.plusSeconds(1))), time.plusSeconds(1)) is TrackUpdate.Updated)
        assertEquals(1, tracker.active(time.plusSeconds(1)).size)
    }
}
