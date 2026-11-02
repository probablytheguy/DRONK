package org.ouitak.app.core

import org.junit.Assert.*
import org.junit.Test
import java.time.Duration
import java.time.Instant
import java.util.UUID
import javax.xml.parsers.DocumentBuilderFactory
import org.xml.sax.InputSource
import java.io.StringReader

class HistoryReplayTest {
    private val now=Instant.parse("2026-01-02T00:00:00Z")
    private val records=SyntheticHistory.observations(now)
    private fun parse(xml:String)=DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(InputSource(StringReader(xml))).documentElement
    @Test fun replayResetRemovalCannotTargetLiveAircraftOrOtherApplications() {
        val d=records.first();val frame=ReplayFrame(d,now,UUID(0,1))
        val position=requireNotNull(d.position)
        val generator=PresentationRemovalCot()
        val root=parse(generator.generate(frame.uid,d.position!!,now))
        assertEquals("t-x-d-d",root.getAttribute("type"))
        assertEquals(frame.uid,(root.getElementsByTagName("link").item(0) as org.w3c.dom.Element).getAttribute("uid"))
        assertEquals(1,root.getElementsByTagName("__forcedelete").length)
        assertThrows(IllegalArgumentException::class.java) { generator.generate(d.cotUid,position,now) }
        assertThrows(IllegalArgumentException::class.java) { generator.generate("external-aircraft",position,now) }
    }
    @Test fun replayLocationRolesHaveIndependentAssociatedUids() {
        val d=records.first();val frame=ReplayFrame(d,now,UUID(0,1))
        val events=listOf(LocationRole.CONTROL_STATION,LocationRole.TAKEOFF,LocationRole.RECEIVER).map { role ->
            parse(LocationCotGenerator().generate(d,role,now,frame)!!)
        }
        assertEquals(3,events.map { it.getAttribute("uid") }.distinct().size)
        assertTrue(events.all { (it.getElementsByTagName("ouitak").item(0) as org.w3c.dom.Element).getAttribute("aircraftUid")==frame.uid })
        assertTrue(events.none { it.getAttribute("uid")==d.cotUid })
    }
    @Test fun keysDeduplicateReceiptsButPreserveDistinctObservations() {
        val d=records.first()
        assertEquals(historyKey(d),historyKey(d.copy(receivedAt=now.plusSeconds(10))))
        assertNotEquals(historyKey(d),historyKey(d.copy(position=Position(1.0,2.0))))
        assertNotEquals(historyKey(d),historyKey(d.copy(controlStationLocation=null)))
    }
    @Test fun trailSelectionOrdersDeduplicatesLimitsAndFiltersExpiry() {
        val a=records.filter { it.identifier.endsWith("001") }
        val result=trailPoints(a.reversed()+a.first(),now,HistoryPolicy(Duration.ofHours(1),2))
        assertEquals(a.takeLast(2),result)
        assertTrue(trailPoints(a,now.plusSeconds(3601),HistoryPolicy(Duration.ofHours(1))).isEmpty())
    }
    @Test fun retentionRemovesExpiredLocationsIndependently() {
        val d=records.last().copy(controlStationLocation=records.last().controlStationLocation!!.copy(observedAt=now.minusSeconds(100)))
        val retained=retainedDetection(d,now.minusSeconds(50))!!
        assertNotNull(retained.position); assertNull(retained.controlStationLocation); assertNotNull(retained.takeoffLocation)
        assertNull(retainedDetection(d,now.plusSeconds(1)))
    }
    @Test fun newerControlReportDoesNotRefreshOldAircraftPosition() {
        val d=records.first().copy(controlStationLocation=ReportedLocation(Position(1.0,2.0),now,LocationProvenance.REMOTE_ID_BROADCAST,LocationValidity.VALID))
        val retained=retainedDetection(d,now.minusSeconds(1))!!
        assertNull(retained.position); assertNotNull(retained.controlStationLocation)
        assertNull(retained.speedMps)
    }
    @Test fun historyPolicyValidatesLimits() {
        assertThrows(IllegalArgumentException::class.java) { HistoryPolicy(trailPoints=1) }
        assertThrows(IllegalArgumentException::class.java) { HistoryPolicy(Duration.ZERO) }
    }
    @Test fun replayOrdersBothAircraftWithoutMutatingHistory() {
        val replay=ReplayEngine(records.reversed()+records.first())
        replay.resume(0)
        val first=replay.poll(0,now)
        assertEquals(2,first.size)
        val rest=replay.poll(15_000_000_000,now.plusSeconds(15))
        assertEquals(6,rest.size); assertEquals(ReplayState.FINISHED,replay.state)
        assertEquals(records.first().observedAt,first.first().original.observedAt)
        assertTrue(first.all { it.displayAt==now && it.uid!=it.original.cotUid })
    }
    @Test fun pauseResumeAndSpeedPreserveCursorAndTime() {
        val r=ReplayEngine(records)
        r.resume(0); assertEquals(2,r.poll(0,now).size)
        r.pause(2_000_000_000); assertTrue(r.poll(100_000_000_000,now).isEmpty())
        r.resume(100_000_000_000); assertTrue(r.poll(102_000_000_000,now).isEmpty())
        assertEquals(2,r.poll(103_000_000_000,now).size)
        r.setSpeed(2.0,103_000_000_000)
        assertEquals(2,r.poll(105_500_000_000,now).size)
    }
    @Test fun resetRestartsAndEmptyReplayFinishes() {
        val r=ReplayEngine(records); r.resume(0); r.poll(0,now); r.reset()
        assertEquals(ReplayState.READY,r.state); r.resume(50); assertEquals(2,r.poll(50,now).size)
        val empty=ReplayEngine(emptyList()); empty.resume(0); assertEquals(ReplayState.FINISHED,empty.state)
    }
    @Test fun completedReplayCanResetAndReuseItsSeparateSessionIdentity() {
        val r=ReplayEngine(records);r.resume(0)
        val first=r.poll(0,now)
        r.poll(20_000_000_000,now);assertEquals(ReplayState.FINISHED,r.state)
        r.reset();r.resume(30_000_000_000)
        val reset=r.poll(30_000_000_000,now.plusSeconds(30))
        assertEquals(first.map { it.uid },reset.map { it.uid })
        assertTrue(reset.all { it.uid!=it.original.cotUid })
    }
    @Test fun replayRejectsInvalidSpeedAndBackwardsClock() {
        val r=ReplayEngine(records)
        listOf(0.0,Double.NaN,65.0).forEach { assertThrows(IllegalArgumentException::class.java) { r.setSpeed(it,0) } }
        r.resume(10); assertThrows(IllegalArgumentException::class.java) { r.poll(9,now) }
    }
    @Test fun replayCotUsesCurrentEventTimeOriginalLastSeenAndSeparateIdentity() {
        val d=records.first(); val frame=ReplayFrame(d,now,UUID(0,1))
        val root=parse(CotGenerator().generateReplay(frame))
        assertEquals(frame.uid,root.getAttribute("uid")); assertNotEquals(d.cotUid,root.getAttribute("uid"))
        assertEquals(now,Instant.parse(root.getAttribute("time")))
        assertTrue(root.textContent.contains("HISTORICAL REPLAY"))
        assertEquals(d.observedAt,Instant.parse((root.getElementsByTagName("ouitak").item(0) as org.w3c.dom.Element).getAttribute("originalObservedAt")))
    }
    @Test fun trailsArePerAircraftAndUpdatesReuseOneDrawingUid() {
        val a=records.filter { it.identifier.endsWith("001") }; val b=records.filter { it.identifier.endsWith("002") }
        val gen=TrailCotGenerator()
        val first=parse(gen.generate(a.take(2),now)); val update=parse(gen.generate(a,now)); val other=parse(gen.generate(b,now))
        assertEquals("u-d-f",first.getAttribute("type")); assertEquals(first.getAttribute("uid"),update.getAttribute("uid"))
        assertNotEquals(first.getAttribute("uid"),other.getAttribute("uid")); assertNotEquals(a.first().cotUid,first.getAttribute("uid"))
        assertEquals(4,update.getElementsByTagName("link").length)
        assertThrows(IllegalArgumentException::class.java) { gen.generate(listOf(a.first(),b.first()),now) }
        assertThrows(IllegalArgumentException::class.java) { gen.generate(a.reversed(),now) }
        assertNotEquals(first.getAttribute("uid"),parse(gen.generate(a,now,replayUid="replay-session")).getAttribute("uid"))
    }
}
