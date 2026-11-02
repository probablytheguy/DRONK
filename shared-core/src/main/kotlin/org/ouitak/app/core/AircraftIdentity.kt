package org.ouitak.app.core

import java.nio.charset.StandardCharsets
import java.util.UUID

/** Supported textual Basic ID types. Binary UTM/session IDs are deliberately
 * deferred until a verified canonical representation is available.
 */
enum class IdentifierType { SYNTHETIC, SERIAL_NUMBER, CAA_REGISTRATION_ID, PROVISIONAL_UNTYPED }

data class AircraftIdentity(val type: IdentifierType, val identifier: String) {
    init {
        val limit = if (type == IdentifierType.SYNTHETIC) 128 else 20
        require(identifier.isNotBlank() && identifier.length <= limit) { "Invalid aircraft identifier length" }
        require(identifier == identifier.trim()) { "Identifier has surrounding whitespace" }
        require(identifier.all { it.code in 0x20..0x7e }) { "Identifier must be printable ASCII" }
    }

    /** Versioned namespace + type + exact identifier. Position, time and sensor
     * do not affect identity. Simulation cannot collide with real broadcasts.
     * This deterministic key is NOT authentication or anonymization.
     */
    fun cotUid(simulated: Boolean, provisionalNamespace: String? = null): String {
        val namespace = if (simulated) "org.ouitak.remoteid.simulation.v1" else "org.ouitak.remoteid.broadcast.v1"
        val scope = if (type == IdentifierType.PROVISIONAL_UNTYPED) {
            require(!provisionalNamespace.isNullOrBlank()) { "Provisional identity requires receiver namespace" }
            "\u0000receiver:$provisionalNamespace"
        } else ""
        return "ouitak-rid-" + UUID.nameUUIDFromBytes(
            "$namespace\u0000${type.name}\u0000$identifier$scope".toByteArray(StandardCharsets.UTF_8))
    }
}
