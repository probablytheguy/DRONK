package org.ouitak.app.core.serial

import org.junit.Assert.*
import org.junit.Test
import org.ouitak.app.core.*
import java.time.Instant

class LiveSerialIngestionTest {
    private val received = Instant.parse("2026-10-24T18:00:00Z")
    private val line = """{"mac":"02:11:22:33:44:55","rssi":-61,"drone_lat":34.2,"drone_long":-117.3,"drone_altitude":123,"pilot_lat":0,"pilot_long":0,"basic_id":"RID-ALPHA"}""" + "\r\n"

    private fun pipeline(namespace: String = "receiver-a", sink: MutableList<Detection> = mutableListOf(), clock: () -> Instant = { received }) =
        SerialIngestionPipeline(receiverNamespace = namespace,
            policy = { LegacyAssociationPolicy.ALLOW_PROVISIONAL }, onDetection = sink::add, clock = clock)

    @Test fun partialReadsProduceOneProvisionalDetectionAndKeepPilotUnassociated() {
        val detections=mutableListOf<Detection>()
        val p=pipeline(sink=detections)
        val bytes=line.toByteArray()
        p.feed(bytes.copyOfRange(0,23),SerialContext(received,ObservationSource.OUI_SPY_USB))
        assertTrue(detections.isEmpty())
        p.feed(bytes.copyOfRange(23,bytes.size),SerialContext(received,ObservationSource.OUI_SPY_USB))
        assertEquals(1,detections.size)
        val d=detections.single()
        assertEquals("RID-ALPHA",d.identifier)
        assertEquals(IdentifierType.PROVISIONAL_UNTYPED,d.identifierType)
        assertEquals(received,d.receivedAt)
        assertEquals(received,d.observedAt) // provisional event clock is explicitly receiver time
        assertNull(d.controlStationLocation)
        assertNull(d.takeoffLocation)
        assertNull(d.receiverLocation)
        assertEquals(1,p.diagnostics.accepted)
    }

    @Test fun malformedJsonIsCountedAndDoesNotCreateDetection() {
        val out=mutableListOf<Detection>()
        val p=pipeline(sink=out)
        p.feed("{\"basic_id\":\"x\",\"drone_lat\":tru}\n".toByteArray(),SerialContext(received,ObservationSource.OUI_SPY_USB))
        assertTrue(out.isEmpty())
        assertEquals(1,p.diagnostics.rejected)
    }

    @Test fun disconnectDropsIncompleteFrameInsteadOfJoiningNextSession() {
        val out=mutableListOf<Detection>()
        val p=pipeline(sink=out)
        val partial=line.toByteArray()
        p.feed(partial.copyOfRange(0,partial.size-2),SerialContext(received,ObservationSource.OUI_SPY_USB))
        p.disconnected()
        p.feed("{}\n".toByteArray(),SerialContext(received.plusSeconds(1),ObservationSource.OUI_SPY_USB))
        assertTrue(out.isEmpty())
        assertEquals(1,p.diagnostics.ignored)
    }

    @Test fun provisionalIdentityIsReceiverScopedAndCotUsesReceiverTimeOnly() {
        val first=mutableListOf<Detection>(); val second=mutableListOf<Detection>()
        pipeline("receiver-a",first).feed(line.toByteArray(),SerialContext(received,ObservationSource.OUI_SPY_USB))
        pipeline("receiver-b",second).feed(line.toByteArray(),SerialContext(received,ObservationSource.OUI_SPY_USB))
        val a=first.single(); val b=second.single()
        assertNotEquals(a.cotUid,b.cotUid)
        val cot=CotGenerator().generate(a)
        assertTrue(cot.contains("timeBasis=\"receiver\""))
        assertTrue(cot.contains("receivedAt=\"2026-10-24T18:00:00.000Z\""))
        assertFalse(cot.contains("originalObservedAt="))
        assertFalse(cot.contains("lastSeen="))
        assertTrue(cot.contains("untyped Basic ID; event time is receiver time"))
        assertTrue(cot.contains("PROVISIONAL OUI-SPY USB"))
    }

    @Test fun realAndSimulatedEventsRemainExplicitlySeparate() {
        val detections=mutableListOf<Detection>()
        pipeline(sink=detections).feed(line.toByteArray(),SerialContext(received,ObservationSource.OUI_SPY_USB))
        val real=CotGenerator().generate(detections.single())
        val simulated=CotGenerator().generate(Simulator().next(received,"SIM-DRONE-001"))
        assertTrue(real.contains("how=\"m-r\""))
        assertTrue(real.contains("PROVISIONAL OUI-SPY USB"))
        assertTrue(simulated.contains("how=\"m-s\""))
        assertTrue(simulated.contains("SIMULATION"))
        assertNotEquals(detections.single().cotUid,Simulator().next(received,"SIM-DRONE-001").cotUid)
    }

    @Test fun policyAndRequiredFieldsAreEnforced() {
        val out=mutableListOf<Detection>()
        val p=SerialIngestionPipeline(receiverNamespace="r",policy={LegacyAssociationPolicy.DISABLED},onDetection=out::add,clock={received})
        p.feed(line.toByteArray(),SerialContext(received,ObservationSource.OUI_SPY_USB))
        assertTrue(out.isEmpty())
        assertEquals(1,p.diagnostics.ignored)
    }
}
