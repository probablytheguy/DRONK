package org.ouitak.bridge

import org.junit.Assert.*
import org.junit.Test
import org.ouitak.app.core.ObservationSource
import org.ouitak.app.core.serial.LiveSerialProcessor
import org.ouitak.app.core.serial.LegacyAssociationPolicy
import org.ouitak.app.core.serial.ProcessedSerialDetection
import org.ouitak.app.core.serial.SerialContext
import org.ouitak.app.core.serial.SerialEvent
import java.time.Clock
import java.time.Instant

class LiveBridgeProcessorTest {
    private val at = Instant.parse("2026-10-24T22:00:00Z")
    private val record = """{"mac":"02:11:22:33:44:55","rssi":-61,"drone_lat":38.627,"drone_long":-90.1994,"drone_altitude":130,"pilot_lat":38.63,"pilot_long":-90.20,"basic_id":"RID-TEST"}"""

    private fun processor(out: MutableList<ProcessedSerialDetection>, diagnostics: MutableList<SerialEvent> = mutableListOf()) =
        LiveSerialProcessor(
            receiverNamespace = "linux-test-receiver",
            policy = { LegacyAssociationPolicy.ALLOW_PROVISIONAL },
            clock = Clock.systemUTC(),
            onProcessed = out::add,
            onDiagnostic = { event, _ -> diagnostics += event }
        )

    @Test fun liveForwardingRequiresExplicitLiveModeAndRejectsSyntheticConflict() {
        val credentials = arrayOf("--tak-host", "tak.example.test", "--client-p12", "client.p12", "--password-file", "password", "--ca-file", "ca.pem")
        val config = BridgeConfig.parse(arrayOf("--live", "--forward-live", *credentials))
        assertTrue(config.live)
        assertTrue(config.forwardLive)
        assertFails { BridgeConfig.parse(arrayOf("--live", "--synthetic")) }
        assertFails { BridgeConfig.parse(arrayOf("--forward-live", *credentials)) }
        assertFails { BridgeConfig.parse(arrayOf("--synthetic", "--forward-live", *credentials)) }
    }

    @Test fun partialRecordUsesSharedParserTrackerAndCotPath() {
        val out = mutableListOf<ProcessedSerialDetection>()
        val p = processor(out)
        val bytes = (record + "\r\n").toByteArray()
        p.feed(bytes.copyOfRange(0, 41), SerialContext(at, ObservationSource.OUI_SPY_USB))
        assertTrue(out.isEmpty())
        p.feed(bytes.copyOfRange(41, bytes.size), SerialContext(at, ObservationSource.OUI_SPY_USB))
        val event = out.single()
        val cot = event.cotXml!!
        assertTrue(cot.contains("how=\"m-r\""))
        assertTrue(cot.contains("PROVISIONAL OUI-SPY USB"))
        assertTrue(cot.contains("event time is receiver time, not a verified broadcast timestamp"))
        assertEquals("RID-TEST", event.detection.identifier)
    }

    @Test fun malformedStatusAndMissingCoordinatesNeverReachCot() {
        val out = mutableListOf<ProcessedSerialDetection>()
        val diagnostics = mutableListOf<SerialEvent>()
        val p = processor(out, diagnostics)
        p.feed("{\"status\":\"scanning\"}\n{bad json}\n".toByteArray(), SerialContext(at, ObservationSource.OUI_SPY_USB))
        p.feed("""{"basic_id":"NO-POSITION","mac":"02:11:22:33:44:55"}""".plus("\n").toByteArray(),
            SerialContext(at, ObservationSource.OUI_SPY_USB))
        p.feed(record.replace("38.627", "999.0") .plus("\n").toByteArray(), SerialContext(at, ObservationSource.OUI_SPY_USB))
        assertTrue(out.isEmpty())
        assertTrue(diagnostics.any { it is SerialEvent.Ignored })
        assertTrue(diagnostics.any { it is SerialEvent.Rejected })
    }

    @Test fun disconnectDropsPartialLineAndLiveAndSimulationKeepDistinctIdentity() {
        val out = mutableListOf<ProcessedSerialDetection>()
        val p = processor(out)
        val line = (record + "\n").toByteArray()
        p.feed(line.copyOfRange(0, 30), SerialContext(at, ObservationSource.OUI_SPY_USB))
        p.disconnected()
        p.feed(line, SerialContext(at, ObservationSource.OUI_SPY_USB))
        p.feed(line, SerialContext(at, ObservationSource.SIMULATION))
        assertEquals(2, out.size)
        assertNotEquals(out[0].detection.cotUid, out[1].detection.cotUid)
        assertTrue(out[0].cotXml!!.contains("how=\"m-r\""))
        assertTrue(out[1].cotXml!!.contains("how=\"m-s\""))
    }

    @Test fun boundedSyntheticScenarioUsesSharedPipelineAndStableSyntheticIdentity() {
        val out = mutableListOf<ProcessedSerialDetection>()
        val p = processor(out)
        runSyntheticScenario(p, intervalMillis = 0)
        assertEquals(3, out.size)
        assertTrue(out.all { it.detection.source == ObservationSource.SIMULATION })
        assertEquals(1, out.map { it.detection.cotUid }.distinct().size)
        assertTrue(out.all { it.cotXml!!.contains("synthetic=\"true\"") })
        assertTrue(out.all { it.cotXml!!.contains("SYNTHETIC PROVISIONAL") })
        assertEquals(3, out.map { it.detection.position!!.latitude to it.detection.position!!.longitude }.distinct().size)
    }

    private fun assertFails(block: () -> Unit) {
        try { block(); fail("Expected invalid bridge mode to fail") } catch (_: IllegalArgumentException) { }
    }
}
