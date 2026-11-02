package org.ouitak.app.core

import org.junit.Assert.*
import org.junit.Test
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.SocketException
import java.time.Instant

class LoopbackAtakTransportTest {
    @Test fun submitsExactlyOneUtf8EventToLoopbackWithoutClaimingIngestion() {
        DatagramSocket(0, InetAddress.getByName("127.0.0.1")).use { receiver ->
            receiver.soTimeout = 1000
            val now = Instant.parse("2026-01-01T00:00:00Z")
            val xml = CotGenerator().generate(Simulator().next(now)).replace("SIMULATION", "SIMULATION · test")
            val submission = LoopbackAtakTransport(receiver.localPort).send(xml)
            val packet = DatagramPacket(ByteArray(65507), 65507)
            receiver.receive(packet)
            assertEquals(xml, String(packet.data, 0, packet.length, Charsets.UTF_8))
            assertTrue(packet.address.isLoopbackAddress)
            assertEquals(xml.toByteArray(Charsets.UTF_8).size, submission.bytes)
            assertEquals("127.0.0.1:${receiver.localPort}/udp", submission.endpoint)
            assertTrue(submission.acknowledgement.contains("unverified"))
            receiver.soTimeout = 100
            assertThrows(java.net.SocketTimeoutException::class.java) { receiver.receive(packet) }
        }
    }
    @Test fun rejectsEmptyOversizedPayloadsAndInvalidPortsBeforeOpeningSocket() {
        val transport = LoopbackAtakTransport(socketFactory = { error("Must not open socket") })
        listOf("", "  ", "é".repeat(32754)).forEach { payload ->
            assertThrows(IllegalArgumentException::class.java) { transport.send(payload) }
        }
        listOf(0, -1, 65536).forEach { port ->
            assertThrows(IllegalArgumentException::class.java) { LoopbackAtakTransport(port) }
        }
    }
    @Test fun propagatesSocketFailureRatherThanReportingSuccess() {
        val closed = DatagramSocket().apply { close() }
        assertThrows(SocketException::class.java) {
            LoopbackAtakTransport(socketFactory = { closed }).send("<event/>")
        }
    }
    @Test fun closesSocketAfterSubmission() {
        val socket = DatagramSocket()
        LoopbackAtakTransport(socketFactory = { socket }).send("<event/>")
        assertTrue(socket.isClosed)
    }
}
