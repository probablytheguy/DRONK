package org.ouitak.app.core

import java.time.Instant
import java.util.UUID

/** Immutable replay envelope needed by the transport-independent CoT generator. */
data class ReplayFrame(val original: Detection, val displayAt: Instant, val session: UUID) {
    val uid: String get() = "ouitak-replay-$session-${original.cotUid}"
}
