package org.ouitak.app.core

import java.io.IOException
import java.net.InetSocketAddress
import java.net.Socket
import java.nio.charset.StandardCharsets
import java.security.cert.CertPathValidatorException
import java.security.cert.CertificateException
import javax.net.ssl.SSLHandshakeException
import javax.net.ssl.SSLPeerUnverifiedException
import javax.net.ssl.SSLParameters
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.SNIHostName

/** TAK legacy XML CoT v0 streaming over TLS with mutual client-certificate auth. */
class TakServerTlsTransport(
    private val hostname:String,
    private val port:Int,
    private val socketFactory:()->SSLSocketFactory,
    private val connectTimeoutMillis:Int=5000,
    private val retryBaseMillis:Long=1000,
    private val retryMaximumMillis:Long=30000,
    private val nowMillis:()->Long=System::currentTimeMillis,
    private val connectAddress:String=hostname
):TakServerForwarding {
    @Volatile private var currentStatus=CotTransportStatus(CotConnectionState.READY,"TLS connection has not been opened")
    @Volatile private var socket:SSLSocket?=null
    @Volatile private var tcpSocket:Socket?=null
    private var failures=0
    private var retryAt=0L
    @Volatile private var closed=false

    init {
        require(hostname.isNotBlank() && hostname.length<=253 && hostname.none { it.isWhitespace() }) { "Invalid TAK Server hostname" }
        require(connectAddress.isNotBlank() && connectAddress.length<=253 && connectAddress.none { it.isWhitespace() }) { "Invalid TAK connection address" }
        require(port in 1..65535)
        require(connectTimeoutMillis in 1..120000)
        require(retryBaseMillis in 1..60000 && retryMaximumMillis>=retryBaseMillis)
    }

    override fun send(cotXml:String):TransportSubmission = synchronized(this) {
        if(closed) throw CotTransportFailure(CotTransportFailureKind.CONFIGURATION,"TAK TLS transport is closed")
        val payload=encodeLegacyStreamMessage(cotXml)
        val active=try { connectedSocket() } catch(e:CotTransportFailure) { throw e }
        try {
            active.outputStream.write(payload)
            active.outputStream.flush()
            currentStatus=CotTransportStatus(CotConnectionState.CONNECTED,"TLS session established with client certificate; stream write completed; server application receipt is not acknowledged")
            TransportSubmission("$hostname:$port/tls",payload.size,"TLS write completed; TAK application receipt is not acknowledged")
        } catch(e:IOException) {
            recordFailure(e)
            throw toFailure(e)
        }
    }

    override fun status():CotTransportStatus=currentStatus

    private fun connectedSocket():SSLSocket {
        socket?.takeIf { it.isConnected && !it.isClosed && !it.isOutputShutdown }?.let { return it }
        socket=null
        val wait=retryAt-nowMillis()
        if(wait>0) {
            currentStatus=CotTransportStatus(CotConnectionState.RETRY_WAIT,"TAK TLS reconnect in ${wait}ms")
            throw CotTransportFailure(CotTransportFailureKind.CONNECTION,"TAK TLS reconnect backoff active (${wait}ms)")
        }
        currentStatus=CotTransportStatus(CotConnectionState.CONNECTING,"Connecting to configured TAK Server with verified TLS identity")
        var candidate:SSLSocket?=null
        var tcp:Socket?=null
        try {
            val factory=socketFactory()
            tcp=Socket()
            tcpSocket=tcp
            if(closed) throw IOException("TLS transport closed")
            tcp.connect(InetSocketAddress(connectAddress,port),connectTimeoutMillis)
            // Layer with the certificate hostname as the TLS peer identity;
            // the TCP destination is never substituted for SAN/hostname checks.
            candidate=factory.createSocket(tcp,hostname,port,true) as? SSLSocket
                ?: throw CotTransportFailure(CotTransportFailureKind.CONFIGURATION,"TLS provider did not create an SSL socket")
            candidate.tcpNoDelay=true
            candidate.keepAlive=true
            candidate.soTimeout=5000
            val parameters=candidate.sslParameters
            parameters.endpointIdentificationAlgorithm="HTTPS" // PKIX trust plus DNS/IP SAN hostname validation.
            // Preserve DNS SNI and HTTPS identity checks independently of the TCP destination.
            if (!hostname.contains(':') && !hostname.matches(Regex("\\d{1,3}(\\.\\d{1,3}){3}"))) {
                parameters.serverNames=listOf(SNIHostName(hostname))
            }
            val protocols=candidate.supportedProtocols.filter { it=="TLSv1.3" || it=="TLSv1.2" }
            if(protocols.isEmpty()) throw CotTransportFailure(CotTransportFailureKind.CONFIGURATION,"No supported TLS 1.2+ protocol")
            parameters.protocols=protocols.toTypedArray()
            candidate.sslParameters=parameters
            candidate.startHandshake()
            if (candidate.session.localCertificates.isNullOrEmpty()) {
                throw CotTransportFailure(CotTransportFailureKind.CLIENT_CREDENTIAL,
                    "TLS server did not receive the configured client certificate")
            }
            if(closed) throw IOException("TLS transport closed")
            socket=candidate
            failures=0;retryAt=0
            currentStatus=CotTransportStatus(CotConnectionState.CONNECTED,"TLS session established; client certificate was presented")
            monitorPeer(candidate)
            return candidate
        } catch(e:Exception) {
            try { candidate?.close() } catch(_:Exception) { }
            try { tcp?.close() } catch(_:Exception) { }
            tcpSocket=null
            recordFailure(e)
            throw toFailure(e)
        }
    }

    /** Drain server-side stream data without logging it, and notice peer close before the next event. */
    private fun monitorPeer(active:SSLSocket) {
        Thread({
            val buffer=ByteArray(1024)
            try {
                while(!closed && !active.isClosed) {
                    val count=try { active.inputStream.read(buffer) }
                    catch(_:java.net.SocketTimeoutException) { continue }
                    if(count<0) {
                        synchronized(this) {
                            if(!closed && socket===active) recordFailure(IOException("TAK Server closed the TLS stream"))
                        }
                        return@Thread
                    }
                    // The legacy CoT client does not consume server-to-client events here.
                }
            } catch(e:IOException) {
                synchronized(this) {
                    if(!closed && socket===active) recordFailure(e)
                }
            }
        },"ouitak-tak-tls-peer-monitor").apply { isDaemon=true;start() }
    }

    private fun recordFailure(cause:Throwable) {
        if(closed) return
        try { tcpSocket?.close() } catch(_:Exception) { }
        tcpSocket=null
        try { socket?.close() } catch(_:Exception) { }
        socket=null
        failures=(failures+1).coerceAtMost(30)
        val delay=(retryBaseMillis shl (failures-1).coerceAtMost(20)).coerceAtMost(retryMaximumMillis)
        retryAt=nowMillis()+delay
        val failure=toFailure(cause)
        val state=when(failure.kind) {
            CotTransportFailureKind.CONNECTION -> CotConnectionState.CONNECTION_ERROR
            CotTransportFailureKind.AUTHENTICATION -> CotConnectionState.AUTHENTICATION_ERROR
            CotTransportFailureKind.SERVER_CERTIFICATE -> CotConnectionState.CERTIFICATE_ERROR
            CotTransportFailureKind.CLIENT_CREDENTIAL -> CotConnectionState.CLIENT_CREDENTIAL_ERROR
            CotTransportFailureKind.CONFIGURATION -> CotConnectionState.CONNECTION_ERROR
        }
        currentStatus=CotTransportStatus(state,"${failure.message}; retry scheduled in ${delay}ms")
    }

    private fun toFailure(error:Throwable):CotTransportFailure {
        if(error is CotTransportFailure) return error
        val chain=generateSequence(error) { it.cause }.toList()
        val text=chain.joinToString(" ") { it.message.orEmpty() }.lowercase()
        val certificateFailure=chain.any { it is CertificateException || it is CertPathValidatorException || it is SSLPeerUnverifiedException } ||
            listOf("hostname", "subject alternative", "trust anchor", "certification path", "certificate expired", "not yet valid").any { it in text }
        val explicitClientAuthFailure=listOf("bad_certificate", "certificate_required", "unknown_ca", "certificate_unknown", "peer not authenticated")
            .any { it in text }
        return when {
            certificateFailure -> CotTransportFailure(CotTransportFailureKind.SERVER_CERTIFICATE,"TAK Server certificate or hostname validation failed",error)
            explicitClientAuthFailure -> CotTransportFailure(CotTransportFailureKind.AUTHENTICATION,"TAK Server rejected TLS client-certificate authentication",error)
            error is SSLHandshakeException -> CotTransportFailure(CotTransportFailureKind.CONNECTION,"TAK TLS handshake failed; check server TLS policy and client-certificate requirements",error)
            error is IOException -> CotTransportFailure(CotTransportFailureKind.CONNECTION,"TAK Server TLS connection failed (${error.javaClass.simpleName})",error)
            else -> CotTransportFailure(CotTransportFailureKind.CONFIGURATION,"TAK TLS configuration failed (${error.javaClass.simpleName})",error)
        }
    }

    override fun close() {
        closed=true
        try { tcpSocket?.close() } catch(_:Exception) { }
        try { socket?.close() } catch(_:Exception) { }
        socket=null
        currentStatus=CotTransportStatus(CotConnectionState.CLOSED,"TAK TLS transport closed")
    }

    companion object {
        /** TAK legacy stream: XML header + LF + complete event; next event starts immediately. */
        fun encodeLegacyStreamMessage(cotXml:String):ByteArray {
            require(cotXml.isNotBlank()) { "Empty CoT payload" }
            val xml=cotXml.trimEnd('\r','\n')
            require(xml.startsWith("<?xml ") && xml.contains("?>\n<event") && xml.endsWith("</event>")) {
                "TAK TLS requires XML declaration, newline, one complete CoT event, and no trailing newline"
            }
            require(xml.indexOf("<event")<xml.lastIndexOf("</event>")) { "CoT event is incomplete" }
            return xml.toByteArray(StandardCharsets.UTF_8)
        }
    }
}
