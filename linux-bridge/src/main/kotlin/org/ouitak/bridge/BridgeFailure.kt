package org.ouitak.bridge

/** Codes/actions are a closed vocabulary. Never use exception details or input values. */
internal class BridgeFailure(val code: String) : Exception(code)
internal fun bridgeFailureCode(error: Exception): String = when (error) {
    is BridgeFailure -> error.code
    is java.io.EOFException -> "E_PIPE_TRUNCATED"
    is IllegalArgumentException, is IndexOutOfBoundsException -> "E_CONFIG"
    else -> "E_BRIDGE_INTERNAL"
}
internal fun bridgeFailureAction(code: String): String = when (code) {
    "E_CONFIG", "E_CONFIG_INCOMPLETE" -> "Check mode, explicit TAK hostname, port and required credential-file settings."
    "E_CREDENTIAL_FILE" -> "Check that configured credential files exist, are regular files and are readable."
    "E_CREDENTIAL_PERMS" -> "Protect client and password files with mode 0600; verify ownership."
    "E_PASSWORD_ENCODING" -> "Use a bounded UTF-8 password file; do not put its contents in arguments or logs."
    "E_CLIENT_CREDENTIAL" -> "Check the PKCS12 identity and password file locally."
    "E_TLS_TRUST" -> "Check the explicit CA bundle and server certificate chain; keep verification enabled."
    "E_TLS_TRUST_EXPIRED" -> "Check certificate dates and the system clock; keep verification enabled."
    "E_PIPE_TRUNCATED", "E_PIPE_PROTOCOL" -> "Restart host monitoring and inspect the reader framing/version."
    else -> "Stop host monitoring and report the sanitized error code."
}
