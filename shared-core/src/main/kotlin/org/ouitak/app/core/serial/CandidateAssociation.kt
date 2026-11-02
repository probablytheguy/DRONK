package org.ouitak.app.core.serial

import java.time.Instant
import org.ouitak.app.core.*

/** Out-of-band evidence from a reviewed adapter/test harness, NOT serial fields.
 * Bound to one immutable candidate. No current firmware profile produces this.
 * Null evidence always holds a legacy candidate; there is no MAC/last-ID join. */
data class AssociationEvidence(
    val candidate: SerialCandidate,
    val identifierType: IdentifierType,
    val observedAt: Instant,
    val description: String
)

sealed class AssociationResult {
    data class Held(val candidate: SerialCandidate, val reason: String) : AssociationResult()
    data class Associated(val observation: RemoteIdObservation) : AssociationResult()
}

object CandidateAssociation {
    fun associate(candidate: SerialCandidate, evidence: AssociationEvidence? = null): AssociationResult {
        fun held(reason: String) = AssociationResult.Held(candidate, reason)
        if (evidence == null) return held("Legacy schema lacks typed identity, validity and association evidence")
        if (evidence.candidate != candidate || evidence.description.isBlank()) return held("Evidence does not identify this candidate")
        val id = candidate.basicId ?: return held("Missing aircraft Basic ID")
        if (evidence.identifierType == IdentifierType.SYNTHETIC) return held("Unsupported Remote ID type")
        if (!CandidateValidator.validContext(candidate.context) || evidence.observedAt < Instant.EPOCH ||
            evidence.observedAt > candidate.context.receivedAt) return held("Invalid chronology")
        if (candidate.aircraft.validity == LocationValidity.INVALID) return held("Invalid aircraft coordinates")
        val context = candidate.context
        val provenance = if (context.source == ObservationSource.SIMULATION) LocationProvenance.SIMULATION else LocationProvenance.REMOTE_ID_BROADCAST
        val aircraft = ReportedLocation(candidate.aircraft.position, evidence.observedAt, provenance, candidate.aircraft.validity)
        val observation = RemoteIdObservation(id, evidence.identifierType, evidence.observedAt, context.receivedAt,
            aircraft.position?.latitude, aircraft.position?.longitude, aircraft.position?.haeMeters,
            source = context.source, aircraftLocation = aircraft, controlStationLocation = context.controlStation,
            takeoffLocation = context.takeoff, receiverLocation = context.sensor)
        return when (val normalized = RemoteIdNormalizer.normalize(observation)) {
            is NormalizationResult.Rejected -> held(normalized.reason)
            is NormalizationResult.Accepted -> AssociationResult.Associated(observation)
        }
    }
}
