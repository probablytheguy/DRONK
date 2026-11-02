package org.ouitak.bridge

import org.junit.Assert.*
import org.junit.Test
import org.ouitak.app.core.*
import org.ouitak.app.core.serial.*
import java.time.Instant
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.CopyOnWriteArrayList

class QueuedForwardingTest {
    private val expires get() = Instant.now().plusSeconds(60)
    private fun await(predicate: () -> Boolean) {
        val deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(3)
        while(!predicate() && System.nanoTime()<deadline) Thread.sleep(5)
        assertTrue(predicate())
    }
    private class Blocked : CotTransport {
        val entered=CountDownLatch(1);val released=CountDownLatch(1)
        override fun send(cotXml:String):TransportSubmission {
            entered.countDown();released.await()
            throw CotTransportFailure(CotTransportFailureKind.CONNECTION,"test-only blocked socket closed")
        }
        override fun close() { released.countDown() }
    }
    @Test fun blockedSendNeverBlocksIngestAndOverflowKeepsRetainedOrder() {
        val blocked=Blocked();val sent=CopyOnWriteArrayList<String>()
        val replacement=object:CotTransport {
            override fun send(cotXml:String):TransportSubmission {
                sent.add(cotXml);return TransportSubmission("test",1,"unacknowledged")
            }
        }
        val sender=QueuedCotForwarder(blocked,{replacement},capacity=2,deadlineMillis=1000)
        try {
            sender.offer("first",expires);assertTrue(blocked.entered.await(1,TimeUnit.SECONDS))
            for(i in 1..1000) sender.offer("queued-$i",expires)
            assertEquals(2,sender.snapshot().queued)
            assertEquals(998L,sender.snapshot().queueDrops)
            await { sent.size==2 }
            assertEquals(listOf("queued-999","queued-1000"),sent.toList())
            assertEquals(1L,sender.snapshot().timeouts)
            assertEquals(1L,sender.snapshot().failed)
            assertEquals(2L,sender.snapshot().written)
        } finally { sender.close() }
    }
    @Test fun validatedReceiverObservationsContinueWhileTakIsBlocked() {
        val blocked=Blocked();val sender=QueuedCotForwarder(blocked,{blocked},capacity=4)
        val local=mutableListOf<ProcessedSerialDetection>()
        val hub=DronkReceiverHub(mapOf("front" to null),onProcessed={ result ->
            local.add(result)
            if(result.tracking is TrackUpdate.Updated) sender.offer(checkNotNull(result.cotXml),expires)
        })
        val fixture=javaClass.getResource("/dronk/observation.json")!!.readText()
        hub.connected("front")
        try {
            hub.feed("front",fixture.toByteArray());assertTrue(blocked.entered.await(1,TimeUnit.SECONDS))
            for(i in 1..100) {
                val frame=fixture.replace("SYNTH-C5-TEST","SYNTH-TEST-$i").replace("\"seq\":3","\"seq\":${i+3}")
                hub.feed("front",frame.toByteArray())
            }
            assertEquals(101,local.size)
            assertEquals(101L,hub.snapshots().single().accepted)
            assertEquals(4,sender.snapshot().queued)
            assertEquals(96L,sender.snapshot().queueDrops)
        } finally { sender.close() }
    }
    @Test fun stopInterruptsBlockedWriteAndAccountsPendingMessages() {
        val blocked=Blocked();val sender=QueuedCotForwarder(blocked,{blocked},capacity=2)
        sender.offer("one",expires);assertTrue(blocked.entered.await(1,TimeUnit.SECONDS))
        sender.offer("pending",expires)
        val started=System.nanoTime();sender.close()
        assertTrue(TimeUnit.NANOSECONDS.toMillis(System.nanoTime()-started)<1500)
        assertEquals(2L,sender.snapshot().shutdownDrops)
        assertFalse(sender.offer("after-stop",expires))
    }
    @Test fun expiredObservationsAreNotSentAfterBackpressure() {
        val blocked=Blocked();val now=java.util.concurrent.atomic.AtomicReference(Instant.parse("2026-10-30T12:00:00Z"))
        val sender=QueuedCotForwarder(blocked,{blocked},deadlineMillis=50,now={now.get()})
        try {
            sender.offer("one",now.get().plusSeconds(60));assertTrue(blocked.entered.await(1,TimeUnit.SECONDS))
            sender.offer("stale",now.get().plusSeconds(1));now.set(now.get().plusSeconds(2))
            await { sender.snapshot().staleDrops==1L }
            assertEquals(1L,sender.snapshot().attempts)
        } finally { sender.close() }
    }
    @Test fun pilotForwardingIsAnOutboundOnlyOptIn() {
        val out=mutableListOf<ProcessedSerialDetection>()
        val hub=DronkReceiverHub(mapOf("front" to null),onProcessed=out::add)
        hub.connected("front")
        hub.feed("front",javaClass.getResource("/dronk/observation.json")!!.readBytes())
        val result=out.single();val d=result.detection;val generator=CotGenerator()
        val disabled=generator.generateForForwarding(d)
        val enabled=generator.generateForForwarding(d,true)
        assertTrue(d.dronk!!.operatorLocation.position!=null)
        assertTrue(result.cotXml!!.contains("operatorLocationType"))
        assertFalse(disabled.contains("operatorLocationType"))
        assertFalse(disabled.contains("operatorSecondsSince2019"))
        assertTrue(disabled.contains("<operator forwarded=\"false\"/>"))
        assertTrue(enabled.contains("operatorLocationType"))
        assertTrue(DronkJson.encode(dronkAircraft(d)).contains("operator_position"))
        assertTrue(disabled.contains("uid=\"${d.cotUid}\""))
        assertTrue(enabled.contains("uid=\"${d.cotUid}\""))
    }
    @Test fun invalidLocalTlsInputsHaveSafeActionableCodes() {
        val directory=java.nio.file.Files.createTempDirectory("dronk-tls-input-test-")
        try {
            val password=directory.resolve("password");val client=directory.resolve("client.p12");val ca=directory.resolve("ca.pem")
            java.nio.file.Files.writeString(password,"test-only-password")
            java.nio.file.Files.writeString(client,"not-a-pkcs12")
            java.nio.file.Files.writeString(ca,"not-a-certificate")
            for(path in listOf(password,client,ca)) java.nio.file.Files.setPosixFilePermissions(path,java.nio.file.attribute.PosixFilePermissions.fromString("rw-------"))
            val config=BridgeConfig.parse(arrayOf("--live","--forward-live","--tak-host","tak.example.test",
                "--password-file",password.toString(),"--client-p12",client.toString(),"--ca-file",ca.toString()),emptyMap())
            try { tlsTransport(config);fail("Invalid PKCS12 accepted") }
            catch(error:BridgeFailure) { assertEquals("E_CLIENT_CREDENTIAL",error.code);assertFalse(error.message!!.contains(directory.toString())) }
            java.nio.file.Files.setPosixFilePermissions(password,java.nio.file.attribute.PosixFilePermissions.fromString("rw-r-----"))
            try { tlsTransport(config);fail("Readable-by-group password accepted") }
            catch(error:BridgeFailure) { assertEquals("E_CREDENTIAL_PERMS",error.code) }
        } finally { directory.toFile().deleteRecursively() }
    }
    @Test fun idlePeerDisconnectionUpdatesStatusWithoutBlockingIngest() {
        val remote=java.util.concurrent.atomic.AtomicReference(CotConnectionState.CONNECTED)
        val target=object:CotTransport {
            override fun send(cotXml:String)=TransportSubmission("test",1,"unacknowledged")
            override fun status()=CotTransportStatus(remote.get(),"test-only status")
        }
        val sender=QueuedCotForwarder(target,{target})
        try {
            sender.offer("one",expires);await {sender.snapshot().written==1L}
            remote.set(CotConnectionState.CONNECTION_ERROR)
            await {sender.snapshot().state=="CONNECTION_ERROR"}
            assertEquals("E_TAK_CONNECTION",sender.snapshot().errorCode)
        } finally {sender.close()}
    }
    @Test fun incompleteLiveConfigurationFailsWithEmptyEnvironment() {
        val options=listOf("--tak-host","--client-p12","--password-file","--ca-file")
        val complete=mapOf("--tak-host" to "tak.example.test","--client-p12" to "test.p12","--password-file" to "test-password","--ca-file" to "test-ca")
        for(missing in options) {
            val args=listOf("--live","--forward-live")+complete.filterKeys {it!=missing}.flatMap {listOf(it.key,it.value)}
            try { BridgeConfig.parse(args.toTypedArray(),emptyMap());fail("Incomplete LIVE accepted") }
            catch(error:BridgeFailure) {assertEquals("E_CONFIG_INCOMPLETE",error.code)}
        }
        for(mode in listOf("DEMO","CAPTURE")) {
            try { BridgeConfig.parse(arrayOf("--live","--forward-live"),mapOf("DRONK_MODE" to mode));fail("Mode crossed into LIVE") }
            catch(_:IllegalArgumentException) { }
        }
    }
}
