package org.ouitak.app.core.serial

import org.junit.Assert.*
import org.junit.Test
import org.ouitak.app.core.*
import java.time.Instant
import kotlin.random.Random

class OfflineSerialParserTest {
    private val now = Instant.parse("2026-10-23T19:00:00Z")
    private val simulation = SerialContext(now, ObservationSource.SIMULATION)
    private fun fixture(name: String) = javaClass.getResourceAsStream("/serial/$name")!!.use { it.readBytes() }
    private val record get() = fixture("synthetic-source-detections.jsonl").toString(Charsets.UTF_8).lineSequence().first()
    private fun parse(text: String, context: SerialContext = simulation): List<SerialEvent> = buildList {
        OfflineSerialParser().feed((text + "\n").toByteArray(), context, ::add)
    }
    private fun candidate(text: String = record, context: SerialContext = simulation) =
        (parse(text, context).single() as SerialEvent.Candidate).value
    private fun replaced(key: String, value: String): String = record.replace(
        Regex("\"$key\":(?:\"[^\"]*\"|[^,}]+)")) { "\"$key\":$value" }

    @Test fun capturedTranscriptIsStableAcrossEverySplitPoint() {
        val bytes = fixture("captured-boot.sanitized.txt")
        fun decode(chunks: List<ByteArray>): List<SerialEvent> = buildList {
            val parser = OfflineSerialParser()
            chunks.forEach { parser.feed(it, SerialContext(now, ObservationSource.OUI_SPY_USB), ::add) }
        }
        val expected = decode(listOf(bytes))
        assertEquals(37, expected.size)
        assertFalse(expected.any { it is SerialEvent.Candidate || it is SerialEvent.Rejected })
        assertTrue(expected.any { it is SerialEvent.Ignored && it.reason == SerialEvent.IgnoreReason.BOOT })
        assertTrue(expected.any { it is SerialEvent.Ignored && it.reason == SerialEvent.IgnoreReason.STATUS })
        for (split in 0..bytes.size) assertEquals("split $split", expected,
            decode(listOf(bytes.copyOfRange(0, split), bytes.copyOfRange(split, bytes.size))))
        assertEquals(expected, decode(bytes.map { byteArrayOf(it) }))
    }

    @Test fun handlesMultipleSyntheticRecordsAndBothLineEndings() {
        val events = parse(record + "\r\n" + record)
        assertEquals(2, events.size)
        assertEquals(events[0], events[1])
        val c = (events[0] as SerialEvent.Candidate).value
        assertEquals("SYNTHETIC-RID-001", c.basicId)
        assertEquals(Position(37.0, -122.0, 120.0), c.aircraft.position)
        assertEquals(Position(38.0, -121.0), c.unresolvedPilot.position)
        assertEquals(-45, c.rssi)
    }

    @Test fun incompleteLinesWaitAndNeverCrossSessionBoundaries() {
        val parser = OfflineSerialParser()
        val events = mutableListOf<SerialEvent>()
        parser.feed(record.toByteArray(), simulation, events::add)
        assertTrue(events.isEmpty())
        parser.feed(byteArrayOf(13), simulation, events::add)
        parser.feed(byteArrayOf(10), simulation, events::add)
        assertTrue(events.single() is SerialEvent.Candidate)
        events.clear()
        parser.feed("{\"mac\":".toByteArray(), simulation, events::add)
        parser.reset()
        assertEquals(0, parser.bufferedBytes)
        parser.feed((record + "\n").toByteArray(), simulation, events::add)
        assertTrue(events.single() is SerialEvent.Candidate)
    }

    @Test fun rejectsOversizeOnceAndRecoversWithoutUnboundedMemory() {
        val parser = OfflineSerialParser(16)
        val events = mutableListOf<SerialEvent>()
        repeat(10000) {
            parser.feed(ByteArray(100) { 65 }, simulation, events::add)
            assertTrue(parser.bufferedBytes <= 16)
        }
        assertEquals(listOf(SerialEvent.Rejected(SerialEvent.RejectReason.OVERSIZED)), events)
        parser.feed("\n{\"status\":1}\n".toByteArray(), simulation, events::add)
        assertTrue(events.last() is SerialEvent.Ignored && (events.last() as SerialEvent.Ignored).reason == SerialEvent.IgnoreReason.STATUS)
        assertEquals("JSON fields=status", (events.last() as SerialEvent.Ignored).detail)
        val lines = mutableListOf<Frame>()
        LineFramer(3).feed("abc\nabcd\nxy\r\n".toByteArray(), lines::add)
        assertEquals(3, lines.size)
        assertArrayEquals("abc".toByteArray(), (lines[0] as Frame.Line).bytes)
        assertEquals(Frame.Oversized, lines[1])
        assertArrayEquals("xy".toByteArray(), (lines[2] as Frame.Line).bytes)
    }

    @Test fun ignoresDiagnosticsAndStatusWithoutSalvagingEmbeddedJson() {
        listOf("[OUI-SPY] log $record", "prefix $record", "ESP-ROM:anything", "OUI SPY UNIFIED FIRMWARE v2.0",
            "{\"   [+] Device is active and scanning...\"}", "{\"status\":\"scanning\"}", "{\"heartbeat\":true}", "{\"ack\":true}")
            .forEach { assertTrue(it, parse(it).single() is SerialEvent.Ignored) }
        assertTrue(parse(record.dropLast(1) + ",\"status\":\"extension\"}").single() is SerialEvent.Candidate)
    }

    @Test fun rejectsStrictJsonViolationsAndRecoversAtNextLine() {
        val bad = listOf("{", "{\"a\":1,}", "{\"a\":1,\"a\":2}", "[]", "{\"a\":NaN}", "{\"a\":Infinity}",
            "{\"a\":01}", "{\"a\":1.}", "{\"a\":1e}", "{\"a\":1e999}", "{\"a\":\"\\q\"}", "{\"a\":\"\\uD800\"}",
            "{\"a\":\"\\uZZZZ\"}", "{} trailing", "{\"a\":\"unescaped\t\"}",
            "{\"a\":".repeat(20) + "0" + "}".repeat(20), record.dropLast(1) + ",\"basic_id\":\"other\"}")
        for (line in bad) {
            val events = parse(line + "\n" + record)
            assertTrue(line, events.first() is SerialEvent.Rejected)
            assertTrue(events.last() is SerialEvent.Candidate)
        }
    }

    @Test fun invalidUtf8IsRejectedAndValidUtf8CanSpanReads() {
        val events = mutableListOf<SerialEvent>()
        val parser = OfflineSerialParser()
        parser.feed(byteArrayOf(0xc3.toByte(), 10), simulation, events::add)
        assertEquals(SerialEvent.Rejected(SerialEvent.RejectReason.INVALID_UTF8), events.single())
        events.clear()
        val extended = record.dropLast(1) + ",\"note\":\"☃\"}\n"
        extended.toByteArray().forEach { parser.feed(byteArrayOf(it), simulation, events::add) }
        assertTrue(events.single() is SerialEvent.Candidate)
    }

    @Test fun validatesIdsMacsAndNumericTypesWithoutCoercion() {
        for (id in listOf(" ", "bad ", "x".repeat(21), "bad\\u0000", "é"))
            assertTrue(id, parse(replaced("basic_id", "\"$id\"")).single() is SerialEvent.Rejected)
        for (key in listOf("drone_lat", "rssi", "drone_altitude")) for (value in listOf("true", "\"45\"", "null", "1e999"))
            assertTrue("$key=$value", parse(replaced(key, value)).single() is SerialEvent.Rejected)
        for (value in listOf("-128", "1", "-40.5")) assertTrue(parse(replaced("rssi", value)).single() is SerialEvent.Rejected)
        for (value in listOf("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff", "garbage"))
            assertTrue(parse(replaced("mac", "\"$value\"")).single() is SerialEvent.Rejected)
    }

    @Test fun invalidMissingAndZeroCoordinatesRemainIndependent() {
        val invalid = candidate(replaced("drone_lat", "91"))
        assertEquals(LocationValidity.INVALID, invalid.aircraft.validity)
        assertNull(invalid.aircraft.position)
        assertNotNull(invalid.unresolvedPilot.position)
        val missingPair = candidate(record.replace("\"drone_long\":-122.0,", ""))
        assertEquals(LocationValidity.INVALID, missingPair.aircraft.validity)
        val zero = candidate(replaced("drone_lat", "0").replace("\"drone_long\":-122.0", "\"drone_long\":0"))
        assertEquals(LocationValidity.UNAVAILABLE, zero.aircraft.validity)
        assertEquals(0.0, candidate(replaced("drone_lat", "0")).aircraft.position!!.latitude, 0.0)
        assertNull(candidate(replaced("drone_altitude", "-1000")).aircraft.position!!.haeMeters)
        for (alt in listOf("-1001", "32000")) assertTrue(parse(replaced("drone_altitude", alt)).single() is SerialEvent.Rejected)
    }

    @Test fun missingIdsAndDuplicatesNeverBorrowAssociations() {
        val noId = candidate(record.replace(",\"basic_id\":\"SYNTHETIC-RID-001\"", ""))
        assertNull(noId.basicId)
        assertNull(candidate(replaced("basic_id", "\"\"")).basicId)
        val events = parse(record + "\n" + record + "\n" + record.replace("SYNTHETIC-RID-001", ""))
        assertEquals(3, events.size) // Raw duplicates retained; dedup belongs to tracker/history.
        events.forEach { assertTrue(CandidateAssociation.associate((it as SerialEvent.Candidate).value) is AssociationResult.Held) }
        assertTrue(CandidateAssociation.associate(noId, AssociationEvidence(noId, IdentifierType.SERIAL_NUMBER, now, "synthetic test")) is AssociationResult.Held)
    }

    @Test fun contextAndAssociationChronologyAreValidated() {
        for (time in listOf(Instant.EPOCH.minusSeconds(1), Instant.parse("+10000-01-01T00:00:00Z")))
            assertEquals(SerialEvent.Rejected(SerialEvent.RejectReason.INVALID_CONTEXT), parse(record, simulation.copy(receivedAt = time)).single())
        val c = candidate()
        for (time in listOf(now.plusSeconds(1), Instant.EPOCH.minusSeconds(1)))
            assertTrue(CandidateAssociation.associate(c, AssociationEvidence(c, IdentifierType.SERIAL_NUMBER, time, "test")) is AssociationResult.Held)
        assertTrue(CandidateAssociation.associate(c, AssociationEvidence(c.copy(basicId = "other"), IdentifierType.SERIAL_NUMBER, now, "test")) is AssociationResult.Held)
    }

    @Test fun independentlyPreservesAllLocationRolesAndProvenance() {
        fun location(lat: Double, seconds: Long) = ReportedLocation(Position(lat, 10.0), now.minusSeconds(seconds), LocationProvenance.SIMULATION, LocationValidity.VALID)
        val context = simulation.copy(controlStation = location(11.0, 3), takeoff = location(22.0, 4), sensor = location(33.0, 5))
        val c = candidate(context = context)
        val result = CandidateAssociation.associate(c, AssociationEvidence(c, IdentifierType.SERIAL_NUMBER, now.minusSeconds(1), "explicit synthetic association")) as AssociationResult.Associated
        val detection = (RemoteIdNormalizer.normalize(result.observation) as NormalizationResult.Accepted).detection
        assertEquals(context.controlStation, detection.controlStationLocation)
        assertEquals(context.takeoff, detection.takeoffLocation)
        assertEquals(context.sensor, detection.receiverLocation)
        assertEquals(c.aircraft.position, detection.position)
        assertNotEquals(c.unresolvedPilot.position, detection.controlStationLocation!!.position)
        assertEquals(4, detection.locations.size)
        assertEquals(ObservationSource.SIMULATION, detection.source)
    }

    @Test fun realAndSyntheticSourcesRemainDistinctAndWireCannotOverrideSource() {
        fun associate(source: ObservationSource): Detection {
            val c = candidate(record.dropLast(1) + ",\"source\":\"SIMULATION\",\"observed_at\":\"invented\"}", SerialContext(now, source))
            val associated = CandidateAssociation.associate(c, AssociationEvidence(c, IdentifierType.SERIAL_NUMBER, now, "out-of-band test evidence")) as AssociationResult.Associated
            return (RemoteIdNormalizer.normalize(associated.observation) as NormalizationResult.Accepted).detection
        }
        val simulated = associate(ObservationSource.SIMULATION)
        val real = associate(ObservationSource.OUI_SPY_USB)
        assertNotEquals(simulated.cotUid, real.cotUid)
        assertEquals(ObservationSource.OUI_SPY_USB, real.source)
    }

    @Test fun untrustedInputCannotThrowIntoServiceCallerAndParserHasNoServiceWiring() {
        val random = Random(5)
        val parser = OfflineSerialParser(256)
        repeat(10000) {
            parser.feed(random.nextBytes(random.nextInt(512)), simulation) { }
            assertTrue(parser.bufferedBytes <= 256)
        }
        parser.reset()
        val events = mutableListOf<SerialEvent>()
        parser.feed((record + "\n").toByteArray(), simulation, events::add)
        assertTrue(events.single() is SerialEvent.Candidate)
    }
}
