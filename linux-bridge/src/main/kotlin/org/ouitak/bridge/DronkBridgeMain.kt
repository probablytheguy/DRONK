package org.ouitak.bridge

import org.ouitak.app.core.*
import org.ouitak.app.core.serial.*
import java.io.DataInputStream
import java.time.Instant

/** Multiplex envelope: u8 kind, u16 stream index (65535 global), i64 receipt epoch
 * millis, u32 size, payload. This is a local pipe contract, never USB commands. */
internal fun runDronkBridge(args: Array<String>) {
    val streams = linkedMapOf<String, String?>()
    val remaining = mutableListOf<String>()
    val publicAliases = linkedMapOf<String,String>()
    var demo = false
    var i = 0
    while (i < args.size) {
        when (val arg = args[i++]) {
            "--stream" -> { val name = args[i++]; require(!streams.containsKey(name)); streams[name] = null }
            "--expected-receiver" -> { val pair = args[i++].split('=', limit=2); require(pair.size==2 && streams.containsKey(pair[0])); streams[pair[0]]=pair[1] }
            "--receiver-alias" -> { val pair=args[i++].split('=',limit=2); require(pair.size==2 && pair[0] in streams && pair[0] !in publicAliases); publicAliases[pair[0]]=pair[1] }
            "--demo" -> demo = true
            else -> remaining += arg
        }
    }
    if (demo) { require(streams.isEmpty() && remaining.isEmpty() && publicAliases.isEmpty()) { "DEMO cannot use devices or forwarding" }; streams["demo-east"] = null; streams["demo-west"] = null }
    require(streams.size in 1..8)
    val config = BridgeConfig.parse((remaining + if (demo) listOf("--synthetic") else emptyList()).toTypedArray())
    require((demo || !config.synthetic) && !config.forwardSynthetic)
    val transport = if (config.forwardLive) tlsTransport(config) else null
    val sender=transport?.let { QueuedCotForwarder(it,{tlsTransport(config)}) }
    val forwardingCot=CotGenerator(config.staleAfter, config.lastKnownStyle)
    fun event(value: Map<String, Any?>) = println(DronkJson.encode(value))
    val markers = LastKnownMonitor(config.staleAfter, config.lastKnownRetention)
    val interruptedAt = mutableMapOf<String, Instant>()  // stream -> last USB disconnect or transport error
    fun markerEvent(marker: LastKnownMonitor.Marker, state: String, reason: String, report: LastKnownReport?) = event(mapOf(
        "kind" to "marker_state", "uid" to marker.uid, "aircraft_uid" to marker.detection.cotUid, "id" to marker.detection.identifier,
        "identifier_type" to marker.detection.identifierType.name, "synthetic" to (marker.detection.source == ObservationSource.SIMULATION),
        "role" to marker.role.wire, "state" to state, "reason" to reason, "last_seen" to marker.lastSeen.toString(),
        "reported_at" to report?.reportedAt?.toString(), "stale_at" to report?.staleAt?.toString(), "stream" to marker.detection.dronk?.stream,
        "position" to marker.location.position?.let { mapOf("latitude" to it.latitude, "longitude" to it.longitude, "altitude_hae_m" to it.haeMeters) }))
    fun report(transitions: List<LastKnownMonitor.Transition>) = transitions.forEach { t ->
        forwardingCot.generateLastKnownForForwarding(t.marker, t.report, config.forwardPilotLocation)?.let { sender?.offer(it, t.report.staleAt) }
        markerEvent(t.marker, "LAST_KNOWN", t.reason.wire, t.report)
    }
    fun tak(): Map<String,Any?> {
        val stats=sender?.snapshot()
        return mapOf("kind" to "tak", "state" to (stats?.state ?: "DISABLED"),
            "attempts" to (stats?.attempts ?: 0), "written" to (stats?.written ?: 0), "failed" to (stats?.failed ?: 0),
            "queue_drops" to (stats?.queueDrops ?: 0), "queued" to (stats?.queued ?: 0), "timeouts" to (stats?.timeouts ?: 0),
            "stale_drops" to (stats?.staleDrops ?: 0), "shutdown_drops" to (stats?.shutdownDrops ?: 0),
            "error_code" to stats?.errorCode, "pilot_location_forwarding" to config.forwardPilotLocation, "acknowledged" to false)
    }
    val hub = DronkReceiverHub(streams, staleAfter=config.staleAfter, simulation=demo,
        legacyNamespace=System.getenv("DRONK_LEGACY_NAMESPACE") ?: "dronk-${stableHostname()}", receiverAliases=publicAliases,
        allowLegacyProvisional=config.allowProvisional,
        onReception={ r -> event(mapOf("kind" to "rid_reception", "reception" to dronkReception(r))) },
        onProcessed={ result ->
            if (result.tracking is TrackUpdate.Updated) {
                event(mapOf("kind" to "observation", "aircraft" to dronkAircraft(result.detection), "expires_at" to (result.tracking as TrackUpdate.Updated).track.expiresAt.toString()))
                val expiresAt=(result.tracking as TrackUpdate.Updated).track.expiresAt
                sender?.offer(forwardingCot.generateForForwarding(result.detection,config.forwardPilotLocation),expiresAt)
                // Operator and take-off markers only when broadcast with a valid position and forwarding is enabled.
                forwardingCot.generateGroundLocationsForForwarding(result.detection,config.forwardPilotLocation).forEach { sender?.offer(it,expiresAt) }
                // Same UIDs: a marker that was LAST KNOWN becomes live again with this update.
                markers.live(result.detection, config.forwardPilotLocation).forEach { markerEvent(it, "LIVE", "receptions_resumed", null) }
                event(tak())
            } else if (result.detection.dronk != null) {
                event(mapOf("kind" to "reception", "aircraft" to dronkAircraft(result.detection),
                    "reason" to (result.tracking as TrackUpdate.Ignored).reason))
            }
        }, onDiagnostic={ s -> event(mapOf("kind" to "receiver", "receiver" to mapOf(
            "stream" to s.stream, "connected" to s.connected, "receiver_id" to s.receiverId?.let { publicAliases[s.stream] ?: s.stream }, "session_id" to s.sessionId,
            "accepted" to s.accepted, "ignored" to s.ignored, "rejected" to s.rejected, "duplicates" to s.duplicates,
            "state" to s.state, "channel" to s.channel, "frequency_mhz" to s.frequencyMhz,
            "uptime_us" to s.uptimeUs, "reset_reason" to s.resetReason, "counters" to s.firmwareCounters, "last_seen" to s.lastSeen?.toString(), "transport_errors" to s.transportErrors,
            "scan" to s.scan?.let { scan -> mapOf("mode" to scan.mode, "dwell_ms" to scan.dwellMs, "hot_ttl_s" to scan.hotTtlS,
                "current" to scan.current, "on_channel_ms" to scan.onChannelMs, "switches" to scan.switches,
                "tune_errors" to scan.tuneErrors, "transition_drops" to scan.transitionDrops,
                "switch_us_last" to scan.switchUsLast, "switch_us_max" to scan.switchUsMax,
                "channels" to scan.channels.map { c -> mapOf("channel" to c.channel, "dwell_ms" to c.dwellMs,
                    "rf_frames" to c.rfFrames, "candidates" to c.candidates, "decoded" to c.decoded,
                    "last_decode_age_s" to c.lastDecodeAgeS, "hot" to c.hot) }) }))) })
    /** Periodic, driven by every pipe envelope including the host heartbeat, so it runs when no packets arrive. */
    fun checkMarkers(now: Instant = Instant.now()) {
        val receivers = hub.snapshots().associateBy { it.stream }
        report(markers.due(now) { m ->
            val stream = m.detection.dronk?.stream ?: return@due true
            val r = receivers[stream] ?: return@due false
            val heard = r.lastSeen
            r.connected && heard != null && heard.plusSeconds(15).isAfter(now) && interruptedAt[stream]?.isAfter(m.lastSeen) != true
        })
    }
    event(mapOf("kind" to "ready", "mode" to if(demo) "DEMO" else if(config.forwardLive) "LIVE" else "CAPTURE", "stale_seconds" to config.staleAfter.seconds))
    event(tak())
    try {
        if (demo) {
            streams.keys.forEach { hub.connected(it) }
            var seq = 0L
            while (true) {
                streams.keys.forEachIndexed { index, stream ->
                    val f = dronkDemo(seq).toMutableMap()
                    f["seq"] = seq*3
                    f["receiver_id"] = "DEMO-C5-${index+1}"
                    f["basic_id"] = mapOf("id_type" to 1,"ua_type" to 2,"value" to "SYNTH-DRONK-0${index+1}")
                    if(index==1) f["aircraft"] = mapOf("validity" to "valid","latitude" to 38.635+Math.sin(seq.toDouble()/10)*.003,"longitude" to -90.21,"altitude_hae_m" to null)
                    hub.feed(stream, (DronkJson.encode(f)+"\n").toByteArray())
                    val status=f.toMutableMap(); status["type"]="status"; status["state"]="monitoring"; status["seq"]=seq*3+1
                    status["counters"]=mapOf("rf_frames" to (seq+1)*23,"decoded_packs" to seq+1,"decoded_messages" to (seq+1)*3,"errors" to 0,"queue_drops" to 0)
                    status["scan"]=dronkDemoScan(seq)
                    hub.feed(stream,(DronkJson.encode(status)+"\n").toByteArray())
                    if (index == 0) hub.feed(stream, (DronkJson.encode(dronkDemoReception(f, seq*3+2))+"\n").toByteArray())
                }
                seq++
                checkMarkers()
                event(mapOf("kind" to "tracks", "uids" to hub.activeTracks().map { it.uid }))
                Thread.sleep(2000)
            }
        }
        val input = DataInputStream(System.`in`)
        val aliases = streams.keys.toList()
        while (true) {
            val kind = try { input.readUnsignedByte() } catch (_: java.io.EOFException) { break }
            val index = input.readUnsignedShort()
            val at = Instant.ofEpochMilli(input.readLong())
            val length = input.readInt()
            if(length !in 0..4096) throw BridgeFailure("E_PIPE_PROTOCOL")
            if (kind == 4) { if(index!=65535 || length!=0) throw BridgeFailure("E_PIPE_PROTOCOL"); break }
            if (kind == 5) { if(index!=65535 || length!=0) throw BridgeFailure("E_PIPE_PROTOCOL") }
            else {
                if(index !in aliases.indices) throw BridgeFailure("E_PIPE_PROTOCOL")
                val alias = aliases[index]
                when(kind) {
                    1 -> { if(length<=0) throw BridgeFailure("E_PIPE_PROTOCOL"); val bytes=ByteArray(length); input.readFully(bytes); hub.feed(alias, bytes, at) }
                    2 -> { if(length!=0) throw BridgeFailure("E_PIPE_PROTOCOL"); hub.connected(alias) }
                    3 -> { if(length!=0) throw BridgeFailure("E_PIPE_PROTOCOL"); hub.disconnected(alias); interruptedAt[alias]=at }
                    6 -> { if(length!=0) throw BridgeFailure("E_PIPE_PROTOCOL"); hub.transportError(alias); interruptedAt[alias]=at }
                    else -> throw BridgeFailure("E_PIPE_PROTOCOL")
                }
            }
            checkMarkers()
            event(mapOf("kind" to "tracks", "uids" to hub.activeTracks().map { it.uid }))
            event(tak())
        }
    } finally {
        streams.keys.forEach { hub.disconnected(it) }
        report(markers.stopAll(Instant.now()))  // monitoring ends: live markers become LAST KNOWN, not silently current
        sender?.flush(3000)
        sender?.close()
        event(tak())
    }
}

/** session_id + seq is the receiver's own key for one emitted record (provenance for later import/dedup);
 *  seq is a decimal string, as in an observation's rf.seq, because it is a u64. */
internal fun dronkReception(r: DronkRidReception): Map<String, Any?> = mapOf(
    "stream" to r.stream, "receiver_id" to r.publicReceiverId, "session_id" to r.sessionId, "seq" to r.sequence.toString(),
    "transmitter" to r.transmitter, "transport" to r.transport,
    "channel" to r.channel, "frequency_mhz" to r.frequencyMhz, "rssi_dbm" to r.rssiDbm, "message_counter" to r.messageCounter,
    "odid_protocol_version" to r.protocolVersion, "decoded_messages" to r.decodedMessages, "messages" to r.messages,
    "basic_ids" to r.basicIds.map { mapOf("id_type" to it.idType, "ua_type" to it.uaType, "value" to it.value) },
    "aircraft_location" to r.aircraftLocation, "reason" to r.reason, "observed_at" to r.observedAt.toString(),
    "received_at" to r.receivedAt.toString(), "synthetic" to r.synthetic)

internal fun dronkAircraft(d: Detection): Map<String, Any?> {
    fun position(p: Position?) = p?.let { mapOf("latitude" to it.latitude, "longitude" to it.longitude, "altitude_hae_m" to it.haeMeters) }
    val m = d.dronk
    return mapOf("uid" to d.cotUid, "id" to d.identifier, "identifier_type" to d.identifierType.name,
        "source" to d.source.name, "synthetic" to (d.source==ObservationSource.SIMULATION),
        "observed_at" to d.observedAt.toString(), "received_at" to d.receivedAt.toString(), "position" to position(d.position),
        "takeoff" to position(d.takeoffLocation?.position), "control_station" to position(d.controlStationLocation?.position),
        "rf" to m?.let { mapOf("stream" to it.stream, "receiver_id" to (it.publicReceiverId ?: it.stream), "session_id" to it.sessionId,
            "seq" to it.sequence.toString(), "uptime_us" to it.uptimeUs, "received_uptime_us" to it.receivedUptimeUs,
            "transport" to it.transport, "channel" to it.channel, "frequency_mhz" to it.frequencyMhz, "rssi_dbm" to it.rssiDbm,
            "transmitter" to it.transmitter, "message_counter" to it.messageCounter, "ua_type" to it.uaType,
            "location_timestamp_seconds_after_hour" to it.locationSecondsAfterHour,
            "operator_location_type" to it.operatorLocationType, "operator_timestamp_seconds_since_2019" to it.operatorSecondsSince2019,
            "operator_validity" to it.operatorLocation.validity.name, "operator_position" to position(it.operatorLocation.position),
            "time_basis" to "host_receipt_minus_device_queue_delay", "authenticated" to false) })
}

/** Synthetic hopping telemetry for DEMO; the demo observations report channel 149, so it is current and hot. */
internal fun dronkDemoScan(seq: Long): Map<String, Any?> {
    val hop = listOf(6, 149, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 36, 40, 44, 48, 153, 157, 161, 165)
    return mapOf("mode" to "hop", "dwell_ms" to 250, "hot_ttl_s" to 60, "current" to 149, "on_channel_ms" to 120,
        "switches" to seq * 8, "tune_errors" to 0, "transition_drops" to seq, "switch_us_last" to 450, "switch_us_max" to 900,
        "channels" to hop.map { ch -> val anchor = ch == 6 || ch == 149
            listOf(ch, seq * if (anchor) 667 else 111, seq * (if (anchor) 40 else 3), if (ch == 149) seq + 1 else 0,
                if (ch == 149) seq + 1 else 0, if (ch == 149) 0 else null, if (ch == 149) 1 else 0) })
}

/** A third synthetic transmitter whose Remote ID decodes but has no position: detected, never tracked. */
internal fun dronkDemoReception(envelope: Map<String, Any?>, seq: Long): Map<String, Any?> =
    envelope.filterKeys { it in setOf("protocol", "version", "receiver_id", "session_id", "uptime_us", "receiver_utc", "received_uptime_us") } + mapOf(
        "type" to "reception", "seq" to seq, "odid_protocol_version" to 2, "transport" to "wifi_beacon", "channel" to 6,
        "frequency_mhz" to 2437, "rssi_dbm" to -71, "transmitter" to "02:00:00:00:00:09", "message_counter" to seq % 256,
        "decoded_messages" to 2, "messages" to listOf("basic_id", "location"),
        "basic_ids" to listOf(mapOf("id_type" to 1, "ua_type" to 2, "value" to "SYNTH-NOFIX-03")),
        "aircraft_location" to "unavailable", "trackable" to false, "reason" to "no_valid_position", "authenticated" to false)

internal fun dronkDemo(seq: Long): Map<String, Any?> = mapOf(
    "protocol" to "dronk.receiver", "version" to 1, "type" to "observation", "receiver_id" to "DEMO-C5",
    "session_id" to "00000000000000000000000000000001", "seq" to seq, "uptime_us" to seq*2000000,
    "received_uptime_us" to seq*2000000, "receiver_utc" to null, "transport" to "wifi_beacon", "channel" to 149,
    "frequency_mhz" to 5745, "rssi_dbm" to -58, "transmitter" to "02:00:00:00:00:01", "message_counter" to seq%256,
    "association" to "same_pack", "authenticated" to false, "basic_id" to mapOf("id_type" to 1,"ua_type" to 2,"value" to "SYNTH-DRONK-01"),
    "aircraft" to mapOf("validity" to "valid", "latitude" to 38.627+Math.sin(seq.toDouble()/15)*0.004,
        "longitude" to -90.1994+Math.cos(seq.toDouble()/15)*0.004, "altitude_hae_m" to 130),
    "location_timestamp_seconds_after_hour" to (seq*2)%3600,
    "operator" to mapOf("location_type" to null,"location" to mapOf("validity" to "unavailable","latitude" to null,
        "longitude" to null,"altitude_hae_m" to null),"timestamp_seconds_since_2019" to null))
