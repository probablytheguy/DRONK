@file:Suppress("DEPRECATION")
package org.ouitak.app.storage

import android.test.AndroidTestCase
import org.ouitak.app.core.*
import java.io.File
import java.time.Duration
import java.time.Instant

/** Synthetic-only receiver fixtures. Run the two publish methods individually
 * to inspect ATAK before/after. No private application history is cleared.
 */
class HistoryAtakAcceptanceTest : AndroidTestCase() {
    private val now get() = Instant.now()
    private fun export(name:String, xml:String) {
        File(context.cacheDir,"acceptance").apply { mkdirs() }.resolve(name).writeText(xml)
        LoopbackAtakTransport().send(xml)
    }
    fun testPublishTwoAircraftTrailsAndLocationRoles() {
        val at=now
        val records=SyntheticHistory.observations(at)
        for(id in listOf("SIM-DRONE-001","SIM-DRONE-002")) {
            val points=records.filter { it.identifier==id }
            export("$id-aircraft.cot",CotGenerator().generate(points.last()))
            export("$id-trail.cot",TrailCotGenerator().generate(points,at))
            for(role in listOf(LocationRole.CONTROL_STATION,LocationRole.TAKEOFF,LocationRole.RECEIVER)) {
                val event=LocationCotGenerator(Duration.ofHours(1)).generate(points.last(),role,at)
                assertNotNull(event);export("$id-$role.cot",event!!)
            }
        }
        assertEquals(2,records.map { it.cotUid }.distinct().size)
    }
    fun testUpdateOnlyAircraftAAndLimitTrail() {
        val at=now
        val points=SyntheticHistory.observations(at.minusSeconds(1)).filter { it.identifier=="SIM-DRONE-001" }
        val moved=points.last().copy(observedAt=at,receivedAt=at,position=Position(10.0,-12.0,60.0))
        val selected=trailPoints(points+moved,at,HistoryPolicy(trailPoints=3))
        assertEquals(3,selected.size);assertEquals(moved,selected.last())
        export("SIM-DRONE-001-update-aircraft.cot",CotGenerator().generate(moved))
        export("SIM-DRONE-001-update-trail.cot",TrailCotGenerator().generate(selected,at))
    }
    fun testRetentionLateArrivalsAndMalformedRecords() {
        val name="acceptance-history.sqlite"
        context.deleteDatabase(name)
        SqliteFlightHistory(context,name).use { store ->
            val at=now
            val values=SyntheticHistory.observations(at)
            // Export, without sending, replay/deletion examples for external XSD checks.
            val frame=ReplayFrame(values.first(),at,java.util.UUID(0,4))
            val output=File(context.cacheDir,"acceptance").apply { mkdirs() }
            output.resolve("replay-aircraft.cot").writeText(CotGenerator().generateReplay(frame))
            output.resolve("replay-removal.cot").writeText(PresentationRemovalCot().generate(frame.uid,frame.original.position!!,at))
            values.reversed().forEach { store.append(it) }
            values.forEach { assertFalse(store.append(it)) }
            assertEquals(values.sortedWith(observationOrder),store.all())
            val cutoff=at.minusSeconds(6)
            store.cleanup(cutoff)
            assertTrue(store.all().all { d->d.locations.values.all { it.observedAt>=cutoff || it.position==null } })
            assertEquals(2,store.aircraft().size)
            store.cleanup(at.plusSeconds(1));assertTrue(store.all().isEmpty())
            val invalid=ReportedLocation.fromCoordinates(Double.NaN,10.0,at,LocationProvenance.REMOTE_ID_BROADCAST)
            assertEquals(LocationValidity.INVALID,invalid.validity)
            assertNull(invalid.position)
        }
        context.deleteDatabase(name)
    }
}
