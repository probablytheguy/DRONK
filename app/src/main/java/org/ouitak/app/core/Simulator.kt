package org.ouitak.app.core

import java.time.Instant

/** Fictional textual Basic ID/Location observations; no RF transmission and no
 * assertion of actual firmware framing. Aircraft B is deliberately far away
 * so both markers can be distinguished on ATAK's offline world map.
 */
class Simulator {
    private val sequences = mutableMapOf<String, Int>()
    fun next(now: Instant = Instant.now(), identifier: String = "SIM-DRONE-001"): Detection {
        require(identifier in listOf("SIM-DRONE-001", "SIM-DRONE-002"))
        val sequence = (sequences.getOrDefault(identifier, 0) + 1) % 20
        sequences[identifier] = sequence
        val second = identifier == "SIM-DRONE-002"
        val observation = RemoteIdObservation(identifier, IdentifierType.SERIAL_NUMBER,
            now, now, (if (second) 8.0 else 0.001) + sequence * 0.00001,
            (if (second) 20.0 else 0.002) + sequence * 0.00001,
            altitudeHaeMeters = if (second) 60.0 else 30.0,
            speedMps = 2.0, headingDegrees = if (second) 90.0 else 45.0)
        return (RemoteIdNormalizer.normalize(observation) as NormalizationResult.Accepted).detection
    }
}
