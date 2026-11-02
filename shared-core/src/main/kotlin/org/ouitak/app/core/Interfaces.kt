package org.ouitak.app.core

import java.io.Closeable

/** Future adapters must document firmware version and framing before implementation. */
interface SensorParser { fun parse(frame: ByteArray): List<Detection> }
interface OuiSpyUsbSerialInput {
    fun start(onFrame: (ByteArray) -> Unit)
    fun stop()
}
/** Submission is not proof of receiver delivery or ingestion. */
data class TransportSubmission(val endpoint: String, val bytes: Int, val acknowledgement: String)
enum class CotConnectionMode { LOCAL_UDP, TAK_SERVER_TLS }
enum class CotConnectionState { READY, CONNECTING, CONNECTED, RETRY_WAIT, AUTHENTICATION_ERROR, CERTIFICATE_ERROR, CLIENT_CREDENTIAL_ERROR, CONNECTION_ERROR, CLOSED }
data class CotTransportStatus(val state:CotConnectionState,val message:String)
enum class CotTransportFailureKind { CONNECTION, AUTHENTICATION, SERVER_CERTIFICATE, CLIENT_CREDENTIAL, CONFIGURATION }
class CotTransportFailure(val kind:CotTransportFailureKind,message:String,cause:Throwable?=null):Exception(message,cause)

interface CotTransport : Closeable {
    fun send(cotXml: String): TransportSubmission
    fun status():CotTransportStatus = CotTransportStatus(CotConnectionState.READY,"Ready")
    override fun close() {}
}
interface LocalAtakIntegration : CotTransport
interface TakServerForwarding : CotTransport
interface Rak4631MeshtasticTransport : CotTransport

/** Keeps mode selection explicit and testable; remote mode never falls back to UDP. */
object CotTransportSelector {
    fun select(mode:CotConnectionMode,local:()->CotTransport,remoteTls:()->CotTransport):CotTransport = when(mode) {
        CotConnectionMode.LOCAL_UDP -> local()
        CotConnectionMode.TAK_SERVER_TLS -> remoteTls()
    }
}

/** Explicit failure transport prevents invalid remote settings from silently falling back to UDP. */
class UnavailableCotTransport(private val failure:CotTransportFailure):CotTransport {
    override fun send(cotXml:String):TransportSubmission=throw failure
    override fun status():CotTransportStatus {
        val state=when(failure.kind) {
            CotTransportFailureKind.CLIENT_CREDENTIAL -> CotConnectionState.CLIENT_CREDENTIAL_ERROR
            CotTransportFailureKind.SERVER_CERTIFICATE -> CotConnectionState.CERTIFICATE_ERROR
            CotTransportFailureKind.AUTHENTICATION -> CotConnectionState.AUTHENTICATION_ERROR
            CotTransportFailureKind.CONNECTION -> CotConnectionState.CONNECTION_ERROR
            CotTransportFailureKind.CONFIGURATION -> CotConnectionState.CONNECTION_ERROR
        }
        return CotTransportStatus(state,failure.message ?: "Transport unavailable")
    }
}
