package org.ouitak.app.core

/** Unauthenticated receiver claims retained independently of aircraft identity.
 * observedAt is host receipt minus device queue delay, never invented RF UTC. */
data class DronkReception(
    val stream: String, val receiverId: String, val sessionId: String, val sequence: ULong,
    val uptimeUs: Long, val receivedUptimeUs: Long, val transport: String,
    val channel: Int, val frequencyMhz: Int, val rssiDbm: Int, val transmitter: String,
    val messageCounter: Int, val uaType: Int, val locationSecondsAfterHour: Double?,
    val operatorLocationType: Int?, val operatorSecondsSince2019: Long?,
    val operatorLocation: ReportedLocation, val publicReceiverId: String? = null
)
