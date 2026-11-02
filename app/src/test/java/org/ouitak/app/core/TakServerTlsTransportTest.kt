package org.ouitak.app.core

import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.io.File
import java.net.InetAddress
import java.net.ServerSocket
import java.security.KeyStore
import java.security.Principal
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import javax.net.ssl.*

class TakServerTlsTransportTest {
    private lateinit var directory:File
    private lateinit var serverStore:KeyStore
    private lateinit var clientStore:KeyStore
    private lateinit var unrelatedStore:KeyStore
    private lateinit var serverTrust:KeyStore
    private val password="test-only-password"

    @Before fun prepareCredentials() {
        directory=java.nio.file.Files.createTempDirectory("ouitak-tls-test-").toFile()
        val server=File(directory,"server.p12");val client=File(directory,"client.p12")
        val unrelated=File(directory,"unrelated.p12")
        runKeytool("-genkeypair","-alias","server","-keyalg","RSA","-keysize","2048","-validity","5",
            "-keystore",server.path,"-storetype","PKCS12","-storepass",password,"-keypass",password,
            "-dname","CN=localhost","-ext","SAN=dns:localhost","-ext","BC=ca:true")
        runKeytool("-genkeypair","-alias","client","-keyalg","RSA","-keysize","2048","-validity","5",
            "-keystore",client.path,"-storetype","PKCS12","-storepass",password,"-keypass",password,
            "-dname","CN=OUI-TAK TLS test client","-ext","BC=ca:true")
        runKeytool("-genkeypair","-alias","unrelated","-keyalg","RSA","-keysize","2048","-validity","5",
            "-keystore",unrelated.path,"-storetype","PKCS12","-storepass",password,"-keypass",password,
            "-dname","CN=unrelated CA","-ext","BC=ca:true")
        serverStore=loadStore(server);clientStore=loadStore(client);unrelatedStore=loadStore(unrelated)
        serverTrust=KeyStore.getInstance("PKCS12").apply {
            load(null,password.toCharArray())
            setCertificateEntry("client",clientStore.getCertificate("client"))
        }
    }

    @After fun removeCredentials() { directory.deleteRecursively() }

    @Test fun remoteTlsSendsLegacyXmlStreamAndAuthenticatesClientCertificate() {
        val endpoint=endpoint(expectClientCertificate=true,messageCount=2)
        val transport=transport("localhost",endpoint.port,clientFactory())
        try {
            val first=transport.send(xml("stable-one"))
            val second=transport.send(xml("stable-two"))
            assertEquals("localhost:${endpoint.port}/tls",first.endpoint)
            assertEquals("TLS write completed; TAK application receipt is not acknowledged",second.acknowledgement)
            assertEquals(CotConnectionState.CONNECTED,transport.status().state)
            assertTrue("endpoint failed=${endpoint.failure.get()}, messages=${endpoint.messages.size}",endpoint.finished.await(15,TimeUnit.SECONDS))
            assertNull(endpoint.failure.get())
            assertNotNull(endpoint.peer.get())
            assertEquals(2,endpoint.messages.size)
            assertTrue(endpoint.messages[0].startsWith("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<event"))
            assertTrue(endpoint.messages[0].endsWith("</event>"))
            assertFalse(endpoint.messages[0].endsWith("\n"))
            assertTrue(endpoint.messages[1].startsWith("<?xml version"))
        } finally { transport.close();endpoint.close() }
    }

    @Test fun separateConnectionAddressStillUsesCertificateHostname() {
        val endpoint=endpoint(expectClientCertificate=true,messageCount=1)
        val transport=TakServerTlsTransport("localhost",endpoint.port,{clientFactory()},connectAddress="127.0.0.1")
        try {
            transport.send(xml("separate-address"))
            assertTrue(endpoint.finished.await(15,TimeUnit.SECONDS))
            assertNull(endpoint.failure.get())
            assertEquals(1,endpoint.messages.size)
        } finally { transport.close();endpoint.close() }
    }

    @Test fun separateAddressCannotBypassCertificateHostnameValidation() {
        val endpoint=endpoint(expectClientCertificate=false,messageCount=1)
        val transport=TakServerTlsTransport("mismatch.example.invalid",endpoint.port,{clientFactory()},connectAddress="127.0.0.1")
        try {
            val failure=try { transport.send(xml("mismatch")); fail("Hostname mismatch accepted"); null }
                catch(e:CotTransportFailure) { e }
            assertEquals(CotTransportFailureKind.SERVER_CERTIFICATE,failure!!.kind)
            assertTrue(endpoint.messages.isEmpty())
        } finally { transport.close();endpoint.close() }
    }

    @Test fun closeInterruptsARealTlsWriteToAStalledLoopbackPeer() {
        val context=SSLContext.getInstance("TLS").apply {
            val km=KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()).apply { init(serverStore,password.toCharArray()) }
            val tm=TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply { init(serverTrust) }
            init(km.keyManagers,tm.trustManagers,null)
        }
        val listener=context.serverSocketFactory.createServerSocket(0,1,InetAddress.getByName("127.0.0.1")) as SSLServerSocket
        listener.needClientAuth=true
        val ready=CountDownLatch(1);val release=CountDownLatch(1);val sent=CountDownLatch(1);val closed=CountDownLatch(1)
        val peer=AtomicReference<SSLSocket>()
        val server=Thread({
            try { (listener.accept() as SSLSocket).use { socket ->
                peer.set(socket);socket.startHandshake();ready.countDown();release.await(10,TimeUnit.SECONDS)
            } } catch(_:Exception) { }
        },"test-stalled-tls-peer").apply { isDaemon=true;start() }
        val transport=TakServerTlsTransport("localhost",listener.localPort,{clientFactory()},connectAddress="127.0.0.1")
        val payload=xml("blocked-write").replace("<detail/>","<detail><remarks>"+"x".repeat(16*1024*1024)+"</remarks></detail>")
        val writer=Thread({try { transport.send(payload) } catch(_:Exception) { } finally {sent.countDown()}},"test-stalled-tls-writer").apply { isDaemon=true;start() }
        try {
            assertTrue(ready.await(5,TimeUnit.SECONDS))
            assertFalse("Peer unexpectedly consumed the full write",sent.await(100,TimeUnit.MILLISECONDS))
            Thread({transport.close();closed.countDown()},"test-tls-cancel").apply {isDaemon=true;start()}
            assertTrue("close waited for the send monitor",closed.await(3,TimeUnit.SECONDS))
            assertTrue("Stalled write did not exit",sent.await(3,TimeUnit.SECONDS))
        } finally {
            release.countDown();peer.get()?.close();listener.close();transport.close()
            writer.join(2000);server.join(2000)
        }
    }

    @Test fun untrustedServerCertificateIsRejected() {
        val endpoint=endpoint(expectClientCertificate=false,messageCount=1)
        val wrongTrust=KeyStore.getInstance("PKCS12").apply {
            load(null,password.toCharArray());setCertificateEntry("unrelated",unrelatedStore.getCertificate("unrelated"))
        }
        val transport=transport("localhost",endpoint.port,clientFactory(wrongTrust))
        try {
            val failure=try { transport.send(xml("untrusted"));fail("Untrusted server certificate was accepted");null }
                catch(e:CotTransportFailure) { e }
            assertEquals("${failure?.cause?.javaClass?.name}: ${failure?.cause?.message}; chain=${generateSequence(failure?.cause){it.cause}.map { it.javaClass.name+":"+it.message }.toList()}",CotTransportFailureKind.SERVER_CERTIFICATE,failure!!.kind)
            assertEquals(CotConnectionState.CERTIFICATE_ERROR,transport.status().state)
        } finally { transport.close();endpoint.close() }
    }

    @Test fun hostnameMismatchIsRejectedEvenWhenCaIsTrusted() {
        val endpoint=endpoint(expectClientCertificate=false,messageCount=1)
        val transport=transport("127.0.0.1",endpoint.port,clientFactory())
        try {
            val failure=try { transport.send(xml("wrong-host"));fail("Hostname mismatch was accepted");null }
                catch(e:CotTransportFailure) { e }
            assertEquals(CotTransportFailureKind.SERVER_CERTIFICATE,failure!!.kind)
            assertEquals(CotConnectionState.CERTIFICATE_ERROR,transport.status().state)
        } finally { transport.close();endpoint.close() }
    }

    @Test fun missingClientCredentialReportsAuthenticationError() {
        val transport=TakServerTlsTransport("localhost",8089,{ throw CotTransportFailure(CotTransportFailureKind.CLIENT_CREDENTIAL,"Selected Android certificate has no private key") })
        try {
            val failure=try { transport.send(xml("no-client-cert"));fail("Missing client certificate was accepted");null }
                catch(e:CotTransportFailure) { e }
            assertEquals(CotTransportFailureKind.CLIENT_CREDENTIAL,failure!!.kind)
            assertEquals(CotConnectionState.CLIENT_CREDENTIAL_ERROR,transport.status().state)
        } finally { transport.close() }
    }

    @Test fun refusedConnectionReportsFailureAndSchedulesReconnect() {
        val probe=ServerSocket(0,1,InetAddress.getByName("127.0.0.1"));val port=probe.localPort;probe.close()
        val transport=transport("localhost",port,clientFactory(),retryMillis=60000)
        try {
            val failure=try { transport.send(xml("no-server"));fail("No server was listening");null }
                catch(e:CotTransportFailure) { e }
            assertEquals(CotTransportFailureKind.CONNECTION,failure!!.kind)
            assertEquals(CotConnectionState.CONNECTION_ERROR,transport.status().state)
            assertTrue(transport.status().message.contains("retry scheduled"))
        } finally { transport.close() }
    }

    @Test fun selectorChoosesOnlyTheRequestedTransport() {
        var local=0;var remote=0
        val l=object:CotTransport { override fun send(cotXml:String)=TransportSubmission("local",1,"local") }
        val r=object:CotTransport { override fun send(cotXml:String)=TransportSubmission("tls",1,"remote") }
        assertSame(l,CotTransportSelector.select(CotConnectionMode.LOCAL_UDP,{local++;l},{remote++;r}))
        assertEquals(1,local);assertEquals(0,remote)
        assertSame(r,CotTransportSelector.select(CotConnectionMode.TAK_SERVER_TLS,{local++;l},{remote++;r}))
        assertEquals(1,local);assertEquals(1,remote)
    }

    private data class Endpoint(val port:Int,val messages:MutableList<String>,val peer:AtomicReference<Principal?>,
        val failure:AtomicReference<Throwable?>,val finished:CountDownLatch,val listener:SSLServerSocket) {
        fun close(){ try { listener.close() } catch(_:Exception) {} }
    }

    private fun endpoint(expectClientCertificate:Boolean,messageCount:Int):Endpoint {
        val context=SSLContext.getInstance("TLS").apply {
            init(keyManagers(serverStore),trustManagers(serverTrust),null)
        }
        val listener=(context.serverSocketFactory.createServerSocket(0,4,InetAddress.getByName("127.0.0.1")) as SSLServerSocket).apply {
            needClientAuth=expectClientCertificate
        }
        val messages=mutableListOf<String>();val peer=AtomicReference<Principal?>();val failure=AtomicReference<Throwable?>()
        val finished=CountDownLatch(1)
        val endpoint=Endpoint(listener.localPort,messages,peer,failure,finished,listener)
        Thread({
            try {
                (listener.accept() as SSLSocket).use { socket ->
                    socket.soTimeout=12000;socket.startHandshake()
                    peer.set(if(expectClientCertificate) socket.session.peerPrincipal else null)
                    val buffer=ByteArray(4096);val accumulated=ByteArrayOutputStream()
                    while(messages.size<messageCount) {
                        val n=socket.inputStream.read(buffer);if(n<0) break
                        accumulated.write(buffer,0,n)
                        var text=accumulated.toString(Charsets.UTF_8.name())
                        while(text.contains("</event>") && messages.size<messageCount) {
                            val end=text.indexOf("</event>")+"</event>".length
                            messages+=text.substring(0,end);text=text.substring(end)
                        }
                        accumulated.reset();accumulated.write(text.toByteArray(Charsets.UTF_8))
                    }
                }
            } catch(e:Throwable) { failure.set(e) } finally { finished.countDown() }
        },"local-tak-tls-test-endpoint").apply { isDaemon=true;start() }
        return endpoint
    }

    private fun transport(host:String,port:Int,factory:SSLSocketFactory,retryMillis:Long=1)=
        TakServerTlsTransport(host,port,{factory},connectTimeoutMillis=1500,retryBaseMillis=retryMillis,retryMaximumMillis=retryMillis)

    private fun clientFactory(trust:KeyStore?=null,includeClientIdentity:Boolean=true):SSLSocketFactory {
        val keyManagers=if(includeClientIdentity) keyManagers(clientStore) else null
        val trustStore=trust ?: KeyStore.getInstance("PKCS12").apply {
            load(null,password.toCharArray());setCertificateEntry("server",serverStore.getCertificate("server"))
        }
        return SSLContext.getInstance("TLS").apply { init(keyManagers,trustManagers(trustStore),null) }.socketFactory
    }

    private fun keyManagers(store:KeyStore)=KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()).run {
        init(store,password.toCharArray());keyManagers
    }
    private fun trustManagers(store:KeyStore)=TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).run {
        init(store);trustManagers
    }
    private fun loadStore(file:File)=KeyStore.getInstance("PKCS12").apply { file.inputStream().use { load(it,password.toCharArray()) } }
    private fun xml(id:String)="<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<event version=\"2.0\" uid=\"$id\" type=\"a-u-A\" how=\"m-r\" time=\"2026-10-24T12:00:00.000Z\" start=\"2026-10-24T12:00:00.000Z\" stale=\"2026-10-24T12:01:00.000Z\"><point lat=\"1.0\" lon=\"2.0\" hae=\"9999999.0\" ce=\"9999999.0\" le=\"9999999.0\"/><detail/></event>"

    private fun runKeytool(vararg args:String) {
        val executable=File(System.getProperty("java.home"),"bin/keytool").absolutePath
        val process=ProcessBuilder(listOf(executable)+args).redirectErrorStream(true).start()
        val output=process.inputStream.bufferedReader().readText()
        check(process.waitFor()==0) { "keytool failed: $output" }
    }
}
