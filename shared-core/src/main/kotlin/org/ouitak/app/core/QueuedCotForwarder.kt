package org.ouitak.app.core

import java.io.Closeable
import java.time.Instant
import java.util.ArrayDeque
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** One bounded FIFO and one daemon sender. Intake never waits for network I/O.
 * A timeout closes the active transport, not a monitor held by send(). Retained
 * entries stay ordered; overflow evicts oldest pending data. No retry/replay of
 * the timed-out event. A non-cooperative transport can strand only one sender,
 * while intake/drop accounting and bounded shutdown continue. */
class QueuedCotForwarder(
    initialTransport: CotTransport,
    private val replacement: () -> CotTransport,
    private val capacity: Int = 64,
    private val deadlineMillis: Long = 10_000,
    private val now: () -> Instant = Instant::now
) : Closeable {
    data class Snapshot(val attempts: Long, val written: Long, val failed: Long,
        val queueDrops: Long, val staleDrops: Long, val shutdownDrops: Long,
        val timeouts: Long, val queued: Int, val state: String, val errorCode: String?)
    private data class Entry(val xml: String, val expiresAt: Instant)
    private data class Flight(val transport: CotTransport, var timedOut: Boolean = false)
    private val lock = Object()
    private val queue = ArrayDeque<Entry>()
    private var stopped = false
    private var attempts = 0L; private var written = 0L; private var failed = 0L
    private var queueDrops = 0L; private var staleDrops = 0L; private var shutdownDrops = 0L; private var timeouts = 0L
    private var state = "READY"
    private var error: String? = null
    private var active: Flight? = null
    @Volatile private var transport: CotTransport? = initialTransport
    private val watchdog = Executors.newSingleThreadScheduledExecutor { r -> Thread(r, "dronk-tak-deadline").apply { isDaemon = true } }
    private val sender: Thread

    init {
        require(capacity in 1..4096 && deadlineMillis in 1..120_000)
        sender = Thread({ consume() }, "dronk-tak-sender").apply { isDaemon = true; start() }
    }

    fun offer(xml: String, expiresAt: Instant): Boolean = synchronized(lock) {
        if (stopped) return false
        if (!expiresAt.isAfter(now())) { staleDrops++; return false }
        if (queue.size == capacity) { queue.removeFirst(); queueDrops++ }
        queue.addLast(Entry(xml, expiresAt)); lock.notifyAll(); true
    }

    fun snapshot(): Snapshot = synchronized(lock) {
        Snapshot(attempts, written, failed, queueDrops, staleDrops, shutdownDrops,
            timeouts, queue.size, state, error)
    }

    private fun take(): Entry? {
        while (true) {
            synchronized(lock) {
                if (stopped) return null
                if (queue.isNotEmpty()) return queue.removeFirst()
                lock.wait(250)
            }
            // Poll the transport only on the sender thread. A status provider
            // can never hold up receiver ingestion or its snapshot lock.
            val remote=transport?.status()?.state
            synchronized(lock) {
                if (!stopped && state=="CONNECTED" && remote!=null && remote!=CotConnectionState.CONNECTED) {
                    state=remote.name; error="E_TAK_CONNECTION"
                }
            }
        }
    }

    private fun consume() {
        while (true) {
            val entry = take() ?: return
            if (!entry.expiresAt.isAfter(now())) { synchronized(lock) { staleDrops++ }; continue }
            var flight: Flight? = null
            var timer: java.util.concurrent.ScheduledFuture<*>? = null
            try {
                synchronized(lock) { if (stopped) { shutdownDrops++; return }; attempts++ }
                val target = transport ?: replacement().also { transport = it }
                val current = Flight(target)
                flight = current
                synchronized(lock) {
                    if (stopped) { shutdownDrops++; target.close(); return }
                    active = current; if (state != "CONNECTED") state = "CONNECTING"  // a send on an open stream is not a reconnect
                    timer = watchdog.schedule({ timeout(current) }, deadlineMillis, TimeUnit.MILLISECONDS)
                }
                target.send(entry.xml)
                synchronized(lock) {
                    active = null
                    if (!current.timedOut && !stopped) { written++; state = "CONNECTED"; error = null }
                }
            } catch (failure: Exception) {
                synchronized(lock) {
                    active = null
                    if (!stopped && flight?.timedOut != true) { failed++; state = "CONNECTION_ERROR"; error = safeCode(failure) }
                }
            } finally {
                timer?.cancel(false)
                synchronized(lock) { if (active === flight) active = null }
                if (flight?.timedOut == true) transport = null
            }
        }
    }

    private fun timeout(flight: Flight) {
        val close = synchronized(lock) {
            if (stopped || active !== flight || flight.timedOut) false
            else { flight.timedOut = true; failed++; timeouts++; state = "CONNECTION_ERROR"; error = "E_TAK_TIMEOUT"; true }
        }
        if (close) try { flight.transport.close() } catch (_: Exception) { }
    }

    /** Bounded wait for queued and in-flight entries (e.g. final LAST KNOWN reports) before [close]. */
    fun flush(timeoutMillis: Long) {
        val deadline = System.nanoTime() + timeoutMillis * 1_000_000
        synchronized(lock) {
            while (!stopped && (queue.isNotEmpty() || active != null)) {
                val left = (deadline - System.nanoTime()) / 1_000_000
                if (left <= 0) return
                lock.wait(minOf(left, 50))
            }
        }
    }

    override fun close() {
        synchronized(lock) {
            if (stopped) return
            stopped = true; shutdownDrops += queue.size + if(active != null && active?.timedOut == false) 1 else 0; queue.clear(); state = "CLOSED"; lock.notifyAll()
        }
        watchdog.shutdownNow()
        // TakServerTlsTransport.close is deliberately not synchronized with send.
        try { transport?.close() } catch (_: Exception) { }
        sender.join(1000)
    }

    companion object {
        fun safeCode(failure: Exception): String {
            if (failure.message in setOf("E_CREDENTIAL_FILE","E_CREDENTIAL_PERMS","E_CLIENT_CREDENTIAL","E_TLS_TRUST","E_TLS_TRUST_EXPIRED","E_PASSWORD_ENCODING")) return failure.message!!
            val kind = (failure as? CotTransportFailure)?.kind
            return when (kind) {
                CotTransportFailureKind.SERVER_CERTIFICATE -> {
                    val detail = generateSequence(failure.cause) { it.cause }.joinToString(" ") { it.message.orEmpty() }.lowercase()
                    when {
                        "subject alternative" in detail || "no name matching" in detail -> "E_TLS_HOSTNAME"
                        "expired" in detail || "not yet valid" in detail -> "E_TLS_TRUST_EXPIRED"
                        else -> "E_TLS_TRUST"
                    }
                }
                CotTransportFailureKind.CLIENT_CREDENTIAL -> "E_CLIENT_CREDENTIAL"
                CotTransportFailureKind.AUTHENTICATION -> "E_CLIENT_AUTH"
                CotTransportFailureKind.CONFIGURATION -> "E_TLS_CONFIG"
                else -> "E_TAK_CONNECTION"
            }
        }
    }
}
