package org.ouitak.app.core

import org.junit.Assert.*
import org.junit.Test
import java.io.StringReader
import java.time.Duration
import java.time.Instant
import javax.xml.parsers.DocumentBuilderFactory
import org.xml.sax.InputSource

class CotGeneratorTest {
    private val time = Instant.parse("2026-01-01T12:00:00Z")
    private val detection = Detection("SIM-<&\"'>", time, time, Position(0.0, 180.0), ObservationSource.SIMULATION)
    private fun parse(xml: String) = DocumentBuilderFactory.newInstance().apply {
        setFeature("http://apache.org/xml/features/disallow-doctype-decl", true)
    }.newDocumentBuilder().parse(InputSource(StringReader(xml)))

    @Test fun generatesParseableEventWithEscapedIdentityAndTimes() {
        val root = parse(CotGenerator().generate(detection)).documentElement
        assertEquals("event", root.tagName)
        assertEquals("2.0", root.getAttribute("version"))
        assertEquals("a-u-A", root.getAttribute("type"))
        assertEquals("m-s", root.getAttribute("how"))
        assertEquals(time, Instant.parse(root.getAttribute("time")))
        assertEquals(time, Instant.parse(root.getAttribute("start")))
        assertEquals(time.plusSeconds(60), Instant.parse(root.getAttribute("stale")))
        val point = root.getElementsByTagName("point").item(0) as org.w3c.dom.Element
        assertEquals("0.0", point.getAttribute("lat"))
        assertEquals("180.0", point.getAttribute("lon"))
        assertEquals("9999999.0", point.getAttribute("hae"))
        val contact = root.getElementsByTagName("contact").item(0) as org.w3c.dom.Element
        assertEquals(detection.identifier, contact.getAttribute("callsign"))
        assertTrue(root.textContent.contains("SIMULATION"))
    }
    @Test fun uidIsStableForTrackAndSeparateAcrossSources() {
        fun uid(d: Detection) = parse(CotGenerator().generate(d)).documentElement.getAttribute("uid")
        assertEquals(uid(detection), uid(detection.copy(observedAt = time.plusSeconds(1), receivedAt = time.plusSeconds(1))))
        assertNotEquals(uid(detection), uid(detection.copy(source = ObservationSource.OUI_SPY_USB)))
    }
    @Test fun rejectsMissingPositionAndInvalidExpiry() {
        assertThrows(IllegalArgumentException::class.java) { CotGenerator().generate(detection.copy(position = null)) }
        listOf(Duration.ZERO, Duration.ofSeconds(-1), Duration.ofDays(2)).forEach { duration ->
            assertThrows(IllegalArgumentException::class.java) { CotGenerator(duration) }
        }
    }
    @Test fun tinyCoordinatesUseXmlDecimalAndUsbIsRelayed() {
        val xml = CotGenerator().generate(detection.copy(position = Position(0.00000001, -0.00000001), source = ObservationSource.OUI_SPY_USB))
        val root = parse(xml).documentElement
        assertEquals("m-r", root.getAttribute("how"))
        val point = root.getElementsByTagName("point").item(0) as org.w3c.dom.Element
        assertEquals("0.000000010", point.getAttribute("lat"))
    }
    @Test fun includesAvailableMotionAltitudeAndLastSeenAsUnverifiedClaims() {
        val root = parse(CotGenerator().generate(detection.copy(
            position = Position(1.0, 2.0, 30.0), speedMps = 2.0, headingDegrees = 45.0))).documentElement
        val track = root.getElementsByTagName("track").item(0) as org.w3c.dom.Element
        assertEquals("2.0", track.getAttribute("speed"))
        assertEquals("45.0", track.getAttribute("course"))
        val detail = root.getElementsByTagName("ouitak").item(0) as org.w3c.dom.Element
        assertEquals("false", detail.getAttribute("verified"))
        assertEquals("30.0", detail.getAttribute("altitudeHaeMeters"))
        assertEquals(time, Instant.parse(detail.getAttribute("lastSeen")))
        assertTrue(root.textContent.contains("unverified broadcast claims"))
    }
    @Test fun partialMotionDoesNotInventCourseOrStandardTrack() {
        val root = parse(CotGenerator().generate(detection.copy(speedMps = 5.0))).documentElement
        assertEquals(0, root.getElementsByTagName("track").length)
        val detail = root.getElementsByTagName("ouitak").item(0) as org.w3c.dom.Element
        assertEquals("5.0", detail.getAttribute("speedMps"))
        assertFalse(detail.hasAttribute("headingDegrees"))
        assertFalse(detail.hasAttribute("altitudeHaeMeters"))
    }
    @Test fun trackingUpdatesSerializeSameUidWithNewPositionAndStaleTime() {
        val tracker = AircraftTracker()
        val first = Simulator().next(time)
        val second = first.copy(observedAt = time.plusSeconds(10), receivedAt = time.plusSeconds(10), position = Position(1.0, 2.0))
        val roots = listOf(first, second).map { d ->
            val update = tracker.observe(d, d.receivedAt) as TrackUpdate.Updated
            parse(CotGenerator(tracker.staleAfter).generate(update.track.latest)).documentElement
        }
        assertEquals(roots[0].getAttribute("uid"), roots[1].getAttribute("uid"))
        assertEquals(time.plusSeconds(70), Instant.parse(roots[1].getAttribute("stale")))
        assertEquals("1.0", (roots[1].getElementsByTagName("point").item(0) as org.w3c.dom.Element).getAttribute("lat"))
        assertEquals(1, tracker.active(second.receivedAt).size)
    }
    @Test fun usesFixedUtcMillisecondsForAtak() {
        val precise = Instant.parse("2026-01-01T12:00:00.123456789Z")
        val root = parse(CotGenerator().generate(detection.copy(observedAt = precise, receivedAt = precise))).documentElement
        assertEquals("2026-01-01T12:00:00.123Z", root.getAttribute("time"))
        assertEquals("2026-01-01T12:01:00.123Z", root.getAttribute("stale"))
    }
    @Test fun emitsKnownHaeAndCustomExpiry() {
        val root = parse(CotGenerator(Duration.ofSeconds(120)).generate(detection.copy(position = Position(1.0, 2.0, 45.0)))).documentElement
        assertEquals(time.plusSeconds(120), Instant.parse(root.getAttribute("stale")))
        assertEquals("45.0", (root.getElementsByTagName("point").item(0) as org.w3c.dom.Element).getAttribute("hae"))
    }
}
