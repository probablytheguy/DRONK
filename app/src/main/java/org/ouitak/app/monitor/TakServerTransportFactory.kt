package org.ouitak.app.monitor

import android.content.Context
import org.ouitak.app.core.*
import org.ouitak.app.storage.HistoryDatabase
import java.io.ByteArrayInputStream
import java.io.File
import java.security.MessageDigest
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate

object TakServerSettings {
    const val PREF_MODE="cotConnectionMode"
    const val PREF_HOST="takServerHost"
    const val PREF_PORT="takServerPort"
    const val PREF_CLIENT_ALIAS="takClientCertificateAlias"
    private const val CA_FILE="tak-server-trust-roots.der"

    fun mode(context:Context):CotConnectionMode = runCatching {
        CotConnectionMode.valueOf(HistoryDatabase.preferences(context).getString(PREF_MODE,CotConnectionMode.LOCAL_UDP.name)!!)
    }.getOrDefault(CotConnectionMode.LOCAL_UDP)

    fun saveMode(context:Context,mode:CotConnectionMode) {
        HistoryDatabase.preferences(context).edit().putString(PREF_MODE,mode.name).apply()
    }

    fun caFile(context:Context)=File(context.filesDir,CA_FILE)
    fun certificateAliasSelected(context:Context)=!HistoryDatabase.preferences(context).getString(PREF_CLIENT_ALIAS,null).isNullOrBlank()

    fun createTransport(context:Context,debugLocalPort:Int?=null):CotTransport = CotTransportSelector.select(
        mode(context),
        local={ LoopbackAtakTransport(debugLocalPort ?: 4242) },
        remoteTls={
            val prefs=HistoryDatabase.preferences(context)
            val host=prefs.getString(PREF_HOST,null)?.trim()?.takeIf { it.isNotEmpty() }
                ?: throw CotTransportFailure(CotTransportFailureKind.CONFIGURATION,"Configure the TAK Server hostname before connecting")
            val port=prefs.getInt(PREF_PORT,8089)
            if(host.length>253 || host.any { it.isWhitespace() })
                throw CotTransportFailure(CotTransportFailureKind.CONFIGURATION,"TAK Server hostname is invalid")
            if(port !in 1..65535)
                throw CotTransportFailure(CotTransportFailureKind.CONFIGURATION,"TAK Server port must be between 1 and 65535")
            val alias=prefs.getString(PREF_CLIENT_ALIAS,null)?.takeIf { it.isNotBlank() }
                ?: throw CotTransportFailure(CotTransportFailureKind.CLIENT_CREDENTIAL,"Select an Android TAK client certificate before connecting")
            TakServerTlsTransport(host,port,{ AndroidTakTlsSocketFactory(context,alias,caFile(context).takeIf { it.isFile }).create() })
        }
    )

    /** Parse only public CA certificates. Caller must show the fingerprint and obtain confirmation. */
    fun parseCaCertificates(bytes:ByteArray):List<X509Certificate> {
        require(bytes.size in 1..1_048_576) { "Trust certificate file is empty or too large" }
        val certs=CertificateFactory.getInstance("X.509").generateCertificates(ByteArrayInputStream(bytes))
            .filterIsInstance<X509Certificate>()
        require(certs.isNotEmpty() && certs.size<=32) { "No X.509 CA certificates found" }
        certs.forEach {
            it.checkValidity()
            require(it.basicConstraints>=0) { "Certificate ${it.subjectX500Principal.name} is not a CA certificate" }
        }
        return certs
    }

    fun fingerprint(certificate:X509Certificate):String = MessageDigest.getInstance("SHA-256").digest(certificate.encoded)
        .joinToString(":") { "%02X".format(it) }

    fun saveCaCertificates(context:Context,certificates:List<X509Certificate>) {
        require(certificates.isNotEmpty())
        // Atomic replace: trust configuration is all-or-nothing.
        val target=caFile(context);val temporary=File(context.filesDir,"$CA_FILE.tmp")
        try {
            temporary.outputStream().use { output -> certificates.forEach { output.write(it.encoded) } }
            if(!temporary.renameTo(target)) {
                target.delete()
                check(temporary.renameTo(target)) { "Could not save TAK server trust roots" }
            }
        } finally { temporary.delete() }
    }
}
