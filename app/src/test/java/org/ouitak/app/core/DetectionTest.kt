package org.ouitak.app.core

import org.junit.Assert.*
import org.junit.Test
import java.time.Instant

class DetectionTest {
    private val now = Instant.parse("2026-01-01T00:00:00Z")
    private fun rejects(block: () -> Unit) { assertThrows(IllegalArgumentException::class.java, block) }

    @Test fun rejectsInvalidCoordinates() {
        listOf(Double.NaN, Double.POSITIVE_INFINITY, -90.01, 90.01).forEach { x -> rejects { Position(x, 0.0) } }
        listOf(Double.NaN, Double.NEGATIVE_INFINITY, -180.01, 180.01).forEach { x -> rejects { Position(0.0, x) } }
        rejects { Position(0.0, 0.0, Double.NaN) }
        Position(-90.0, 180.0)
    }
    @Test fun validatesIdentifiersAndChronology() {
        listOf("", " ", "x".repeat(129), "bad\u0000id").forEach { id ->
            rejects { Detection(id, now, now, null, ObservationSource.SIMULATION) }
        }
        rejects { Detection("valid", now, now.minusSeconds(1), null, ObservationSource.SIMULATION) }
        Detection("valid", now, now, null, ObservationSource.OUI_SPY_USB)
    }
    @Test fun simulatorIsExplicitAndLocated() {
        val sim = Simulator()
        val first = sim.next(now)
        val next = sim.next(now.plusSeconds(2))
        assertEquals(ObservationSource.SIMULATION, first.source)
        assertTrue(first.identifier.startsWith("SIM-"))
        assertEquals(first.identifier, next.identifier)
        assertNotEquals(first.position, next.position)
        assertEquals(now, first.observedAt)
    }
}
