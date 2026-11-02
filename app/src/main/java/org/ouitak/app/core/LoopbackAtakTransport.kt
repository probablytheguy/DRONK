package org.ouitak.app.core

import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.nio.charset.StandardCharsets

/** Legacy CoT: exactly one UTF-8 XML event per UDP datagram, same device only.
 * ATAK must already be running with its UDP input enabled. There is no ACK.
 * Call on a worker thread; failures propagate to the caller.
 */
class LoopbackAtakTransport(
    private val port: Int = 4242,
    private val socketFactory: () -> DatagramSocket = { DatagramSocket() }
) : LocalAtakIntegration {
    init { require(port in 1..65535) { "Invalid UDP port" } }

    override fun send(cotXml: String): TransportSubmission {
        require(cotXml.isNotBlank()) { "Empty CoT payload" }
        val payload = cotXml.toByteArray(StandardCharsets.UTF_8)
        require(payload.size <= 65507) { "CoT exceeds maximum IPv4 UDP payload" }
        val loopback = InetAddress.getByAddress(byteArrayOf(127, 0, 0, 1))
        socketFactory().use { socket ->
            socket.send(DatagramPacket(payload, payload.size, loopback, port))
        }
        return TransportSubmission("127.0.0.1:$port/udp", payload.size,
            "Unsupported by UDP; ATAK ingestion unverified")
    }
}
