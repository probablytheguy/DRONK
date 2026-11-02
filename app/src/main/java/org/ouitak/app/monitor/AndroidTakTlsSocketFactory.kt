package org.ouitak.app.monitor

import android.content.Context
import android.security.KeyChain
import android.security.KeyChainException
import org.ouitak.app.core.CotTransportFailure
import org.ouitak.app.core.CotTransportFailureKind
import java.io.ByteArrayInputStream
import java.io.File
import java.security.KeyStore
import java.security.PrivateKey
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import javax.net.ssl.SSLEngine
import javax.net.ssl.SSLContext
import javax.net.ssl.X509ExtendedKeyManager
import javax.net.ssl.TrustManager
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager

/** Uses Android's user credential store. Private-key bytes are never exported or persisted by OUI-TAK. */
class AndroidTakTlsSocketFactory(
    private val context:Context,
    private val clientCertificateAlias:String,
    private val additionalTrustFile:File?
) {
    fun create()=try {
        val privateKey:PrivateKey=KeyChain.getPrivateKey(context,clientCertificateAlias)
            ?: throw clientCredentialError()
        val chain=KeyChain.getCertificateChain(context,clientCertificateAlias)
            ?.filterIsInstance<X509Certificate>()?.toTypedArray()?.takeIf { it.isNotEmpty() }
            ?: throw clientCredentialError()
        val trustManagers=trustManagers()
        val keyManager=AndroidKeyChainClientKeyManager(clientCertificateAlias,privateKey,chain)
        SSLContext.getInstance("TLS").apply { init(arrayOf(keyManager),trustManagers,null) }.socketFactory
    } catch(e:CotTransportFailure) { throw e }
      catch(e:KeyChainException) { throw CotTransportFailure(CotTransportFailureKind.CLIENT_CREDENTIAL,"Android could not access the selected TAK client certificate",e) }
      catch(e:InterruptedException) { Thread.currentThread().interrupt();throw CotTransportFailure(CotTransportFailureKind.CLIENT_CREDENTIAL,"Android client-certificate access was interrupted",e) }
      catch(e:Exception) { throw CotTransportFailure(CotTransportFailureKind.CONFIGURATION,"Could not prepare TAK TLS credentials (${e.javaClass.simpleName})",e) }

    private fun trustManagers():Array<TrustManager> {
        val system=TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply { init(null as KeyStore?) }
            .trustManagers.filterIsInstance<X509TrustManager>().firstOrNull()
            ?: throw CotTransportFailure(CotTransportFailureKind.CONFIGURATION,"System X.509 trust manager is unavailable")
        val file=additionalTrustFile?.takeIf { it.isFile } ?: return arrayOf(system)
        val certs=try {
            val bytes=file.readBytes()
            require(bytes.size in 1..1_048_576) { "Trust file is empty or too large" }
            CertificateFactory.getInstance("X.509").generateCertificates(ByteArrayInputStream(bytes))
                .filterIsInstance<X509Certificate>().toList().also { require(it.isNotEmpty()) }
        } catch(e:Exception) {
            throw CotTransportFailure(CotTransportFailureKind.CONFIGURATION,"Imported TAK trust certificate could not be read",e)
        }
        val store=KeyStore.getInstance(KeyStore.getDefaultType()).apply {
            load(null,null)
            certs.forEachIndexed { index,certificate -> setCertificateEntry("tak-import-$index",certificate) }
        }
        val imported=TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply { init(store) }
            .trustManagers.filterIsInstance<X509TrustManager>().firstOrNull()
            ?: throw CotTransportFailure(CotTransportFailureKind.CONFIGURATION,"Imported X.509 trust manager is unavailable")
        return arrayOf(CompositeX509TrustManager(listOf(system,imported)))
    }

    private fun clientCredentialError() = CotTransportFailure(
        CotTransportFailureKind.CLIENT_CREDENTIAL,"Selected Android TAK client certificate has no accessible private key or certificate chain")
}

/** Presents a KeyChain handle directly; it never copies or encodes the private key. */
private class AndroidKeyChainClientKeyManager(
    private val alias:String,
    private val privateKey:PrivateKey,
    private val chain:Array<X509Certificate>
):X509ExtendedKeyManager() {
    override fun getClientAliases(keyType:String?,issuers:Array<out java.security.Principal>?):Array<String>? =
        if(eligible(keyType,issuers)) arrayOf(alias) else null
    override fun chooseClientAlias(keyType:Array<out String>?,issuers:Array<out java.security.Principal>?,socket:java.net.Socket?):String? =
        if(keyType.orEmpty().any { eligible(it,issuers) }) alias else null
    override fun chooseEngineClientAlias(keyType:Array<out String>?,issuers:Array<out java.security.Principal>?,engine:SSLEngine?):String? =
        if(keyType.orEmpty().any { eligible(it,issuers) }) alias else null
    override fun getCertificateChain(requestedAlias:String?):Array<X509Certificate>? = if(requestedAlias==alias) chain else null
    override fun getPrivateKey(requestedAlias:String?):PrivateKey? = if(requestedAlias==alias) privateKey else null
    override fun getServerAliases(keyType:String?,issuers:Array<out java.security.Principal>?):Array<String>? = null
    override fun chooseServerAlias(keyType:String?,issuers:Array<out java.security.Principal>?,socket:java.net.Socket?):String? = null

    private fun eligible(keyType:String?,issuers:Array<out java.security.Principal>?):Boolean {
        if(keyType==null || !privateKey.algorithm.equals(keyType.substringBefore('_'),ignoreCase=true)) return false
        if(issuers.isNullOrEmpty()) return true
        val accepted=chain.flatMap { listOf(it.issuerX500Principal,it.subjectX500Principal) }.toSet()
        return issuers.any { it in accepted }
    }
}

private class CompositeX509TrustManager(private val delegates:List<X509TrustManager>):X509TrustManager {
    override fun checkClientTrusted(chain:Array<out X509Certificate>,authType:String) = check { it.checkClientTrusted(chain,authType) }
    override fun checkServerTrusted(chain:Array<out X509Certificate>,authType:String) = check { it.checkServerTrusted(chain,authType) }
    override fun getAcceptedIssuers():Array<X509Certificate> = delegates.flatMap { it.acceptedIssuers.toList() }.distinctBy { it.subjectX500Principal }.toTypedArray()
    private fun check(action:(X509TrustManager)->Unit) {
        var first:Exception?=null
        for(manager in delegates) try { action(manager);return } catch(e:Exception) { if(first==null) first=e }
        throw first ?: java.security.cert.CertificateException("No trust manager accepted certificate")
    }
}
