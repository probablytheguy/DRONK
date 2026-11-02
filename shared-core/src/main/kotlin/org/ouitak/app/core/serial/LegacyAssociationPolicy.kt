package org.ouitak.app.core.serial

import org.ouitak.app.core.*

/** Explicitly permits a display-only track for the legacy Sky Spy schema. */
enum class LegacyAssociationPolicy { DISABLED, ALLOW_PROVISIONAL }

object ProvisionalAssociation {
    fun associate(candidate: SerialCandidate, policy: LegacyAssociationPolicy, receiverNamespace: String): Detection? {
        if (policy != LegacyAssociationPolicy.ALLOW_PROVISIONAL || receiverNamespace.isBlank()) return null
        val id = candidate.basicId?.takeIf { it.isNotBlank() } ?: return null
        val position = candidate.aircraft.position ?: return null
        if (candidate.aircraft.validity != LocationValidity.VALID) return null
        val at = candidate.context.receivedAt
        val aircraft = ReportedLocation(position, at,
            if (candidate.context.source == ObservationSource.SIMULATION) LocationProvenance.SIMULATION else LocationProvenance.REMOTE_ID_BROADCAST,
            LocationValidity.VALID)
        return Detection(identifier = id, observedAt = at, receivedAt = at, position = position,
            source = candidate.context.source, identifierType = IdentifierType.PROVISIONAL_UNTYPED,
            aircraftLocationReport = aircraft, receiverLocation = candidate.context.sensor,
            provisionalNamespace = receiverNamespace)
    }
}

data class SerialDiagnostics(val accepted: Long = 0, val ignored: Long = 0, val rejected: Long = 0)

/** Testable bridge between byte chunks and provisional observations. */
class SerialIngestionPipeline(
    private val parser: OfflineSerialParser = OfflineSerialParser(),
    private val receiverNamespace: String,
    private val policy: () -> LegacyAssociationPolicy,
    private val onDetection: (Detection) -> Unit,
    private val onDiagnostic: (SerialEvent, SerialDiagnostics) -> Unit = { _, _ -> },
    private val clock: () -> java.time.Instant = { java.time.Instant.now() }
) {
    var diagnostics = SerialDiagnostics()
        private set
    private var lastReceipt: java.time.Instant? = null

    fun feed(bytes: ByteArray, context: SerialContext) {
        parser.feed(bytes, context) { event ->
            when (event) {
                is SerialEvent.Candidate -> {
                    val receivedAt = listOfNotNull(clock(), event.value.context.receivedAt, lastReceipt?.plusNanos(1))
                        .maxOrNull()!!
                    lastReceipt = receivedAt
                    val candidate = event.value.copy(context = event.value.context.copy(receivedAt = receivedAt))
                    val detection = ProvisionalAssociation.associate(candidate, policy(), receiverNamespace)
                    if (detection == null) {
                        diagnostics = diagnostics.copy(ignored = diagnostics.ignored + 1)
                    } else {
                        diagnostics = diagnostics.copy(accepted = diagnostics.accepted + 1)
                        onDetection(detection)
                    }
                }
                is SerialEvent.Ignored -> diagnostics = diagnostics.copy(ignored = diagnostics.ignored + 1)
                is SerialEvent.Rejected -> diagnostics = diagnostics.copy(rejected = diagnostics.rejected + 1)
            }
            onDiagnostic(event, diagnostics)
        }
    }

    /** A partial record cannot be joined to a later USB session. */
    fun disconnected() { parser.reset(); lastReceipt = null }
}
