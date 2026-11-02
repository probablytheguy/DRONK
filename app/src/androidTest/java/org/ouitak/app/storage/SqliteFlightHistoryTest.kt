@file:Suppress("DEPRECATION", "OVERRIDE_DEPRECATION")
package org.ouitak.app.storage

import android.test.AndroidTestCase
import org.ouitak.app.core.*
import java.time.Instant

/** Runs against actual emulator SQLite, not mocked Android APIs. */
class SqliteFlightHistoryTest : AndroidTestCase() {
    private val name = "history-tests.sqlite"
    private lateinit var store: SqliteFlightHistory
    private val now = Instant.parse("2026-01-02T00:00:00Z")
    override fun setUp() { super.setUp(); context.deleteDatabase(name); store = SqliteFlightHistory(context,name) }
    override fun tearDown() { store.close(); context.deleteDatabase(name); super.tearDown() }
    fun testIndependentHistoriesOrderingAndDuplicates() {
        val records = SyntheticHistory.observations(now)
        records.reversed().forEach { assertTrue(store.append(it)) }
        assertFalse(store.append(records.first().copy(receivedAt=now.plusSeconds(1))))
        assertEquals(2,store.aircraft().size)
        store.aircraft().forEach { uid ->
            val values=store.observations(uid)
            assertEquals(4,values.size); assertTrue(values.zipWithNext().all { (a,b)->a.observedAt<=b.observedAt })
            assertTrue(values.all { it.cotUid==uid })
        }
    }
    fun testPersistenceAndAllLocationMetadata() {
        val d=SyntheticHistory.observations(now).first()
        store.append(d); store.close(); store=SqliteFlightHistory(context,name)
        assertEquals(d,store.observations(d.cotUid).single())
        assertEquals(4,store.observations(d.cotUid).single().locations.size)
    }
    fun testIndexedTrailQueryLimitsAndFiltersIndependentAircraftAndTimes() {
        val values=SyntheticHistory.observations(now)
        values.reversed().forEach { store.append(it) }
        assertEquals(8L,store.observationCount())
        for(uid in store.aircraft()) {
            val expected=values.filter { it.cotUid==uid }.takeLast(2)
            assertEquals(expected,store.recentPositions(uid,now.minusSeconds(30),now,2))
            assertTrue(store.recentPositions(uid,now.plusSeconds(1),now.plusSeconds(2),2).isEmpty())
        }
    }
    fun testRetentionUsesEachLocationsTimestamp() {
        val d=Simulator().next(now).copy(controlStationLocation=ReportedLocation(Position(1.0,2.0),now.minusSeconds(100),LocationProvenance.REMOTE_ID_BROADCAST,LocationValidity.VALID))
        store.append(d); assertEquals(1,store.cleanup(now.minusSeconds(50)))
        val retained=store.observations(d.cotUid).single()
        assertNull(retained.controlStationLocation); assertNotNull(retained.position)
        assertEquals(1,store.cleanup(now.plusSeconds(1))); assertTrue(store.aircraft().isEmpty())
    }
    fun testControlOnlyObservationAndDeleteAllPersists() {
        val d=Simulator().next(now).copy(position=null,controlStationLocation=ReportedLocation(Position(1.0,2.0),now,LocationProvenance.REMOTE_ID_BROADCAST,LocationValidity.VALID))
        store.append(d); assertNull(store.observations(d.cotUid).single().position)
        store.deleteAll(); store.close(); store=SqliteFlightHistory(context,name)
        assertTrue(store.all().isEmpty())
    }
    fun testMalformedStoredTimestampIsRejected() {
        val d=Simulator().next(now); store.append(d)
        store.writableDatabase.execSQL("UPDATE observations SET payload=replace(payload,?,?)",arrayOf(now.toString(),"not-a-timestamp"))
        try { store.observations(d.cotUid); fail("Malformed timestamp accepted") }
        catch (_: java.time.format.DateTimeParseException) { }
    }
    fun testRetentionPruningStillDeduplicatesOriginalAndPrunedReports() {
        val original=Simulator().next(now).copy(controlStationLocation=ReportedLocation(Position(1.0,2.0),now.minusSeconds(100),LocationProvenance.REMOTE_ID_BROADCAST,LocationValidity.VALID))
        assertTrue(store.append(original));store.cleanup(now.minusSeconds(50))
        val pruned=store.all().single()
        assertNull(pruned.controlStationLocation)
        assertFalse(store.append(pruned.copy(receivedAt=now.plusSeconds(1))))
        assertFalse(store.append(original))
        assertEquals(1,store.all().size)
    }
    fun testInvalidCoordinatesCannotBecomeStoredPosition() {
        try { Position(Double.NaN,1.0); fail("NaN accepted") } catch (_: IllegalArgumentException) { }
        val invalid=ReportedLocation.fromCoordinates(95.0,1.0,now,LocationProvenance.REMOTE_ID_BROADCAST)
        val d=Simulator().next(now).copy(controlStationLocation=invalid)
        store.append(d)
        assertEquals(LocationValidity.INVALID,store.observations(d.cotUid).single().controlStationLocation!!.validity)
        assertNull(store.observations(d.cotUid).single().controlStationLocation!!.position)
    }
}
