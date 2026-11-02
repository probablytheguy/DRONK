package org.ouitak.app.core.serial

import org.ouitak.app.core.AircraftTracker
import org.ouitak.app.core.CotGenerator
import org.ouitak.app.core.Detection
import org.ouitak.app.core.TrackUpdate
import java.time.Clock
import java.time.Duration
import java.time.Instant

/** The transport-independent live serial -> tracked position -> CoT path.
 * Android and Linux adapters feed identical bytes and context into this class.
 */
data class ProcessedSerialDetection(
    val detection: Detection,
    val tracking: TrackUpdate,
    val cotXml: String?
)

class LiveSerialProcessor(
    receiverNamespace: String,
    policy: () -> LegacyAssociationPolicy,
    staleAfter: Duration = Duration.ofSeconds(60),
    private val clock: Clock = Clock.systemUTC(),
    tracker: AircraftTracker = AircraftTracker(staleAfter, clock),
    onProcessed: (ProcessedSerialDetection) -> Unit,
    onDiagnostic: (SerialEvent, SerialDiagnostics) -> Unit = { _, _ -> }
) {
    private val tracker = tracker
    private val cotGenerator = CotGenerator(staleAfter)
    private val ingestion = SerialIngestionPipeline(
        receiverNamespace = receiverNamespace,
        policy = policy,
        clock = clock::instant,
        onDetection = { detection ->
            val result = tracker.observe(detection, clock.instant())
            val cot = if (result is TrackUpdate.Updated) cotGenerator.generate(detection) else null
            onProcessed(ProcessedSerialDetection(detection, result, cot))
        },
        onDiagnostic = onDiagnostic
    )

    val diagnostics: SerialDiagnostics get() = ingestion.diagnostics

    fun feed(bytes: ByteArray, context: SerialContext) = ingestion.feed(bytes, context)

    fun disconnected() = ingestion.disconnected()

    fun activeTracks(now: Instant = clock.instant()) = tracker.active(now)

}
