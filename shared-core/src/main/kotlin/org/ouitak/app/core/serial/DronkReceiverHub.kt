package org.ouitak.app.core.serial

import org.ouitak.app.core.*
import java.nio.charset.CodingErrorAction
import java.time.Clock
import java.time.Duration
import java.time.Instant

/** Serialized by its owner; framing and session state are independent per stream. */
data class DronkReceiverSnapshot(
    val stream: String, val connected: Boolean, val receiverId: String?, val sessionId: String?,
    val accepted: Long, val ignored: Long, val rejected: Long, val duplicates: Long,
    val state: String, val channel: Int?, val frequencyMhz: Int?,
    val firmwareCounters: Map<String, ULong>, val lastSeen: Instant?, val transportErrors: Long,
    val uptimeUs: Long? = null, val resetReason: Int? = null, val scan: DronkScanStatus? = null
)

/** Receiver channel-scan telemetry from the latest status (C5 scan firmware). Counters saturate at 2^32-1. */
data class DronkScanChannel(val channel: Int, val dwellMs: Long, val rfFrames: Long, val candidates: Long,
                            val decoded: Long, val lastDecodeAgeS: Long?, val hot: Boolean)
data class DronkScanStatus(val mode: String, val dwellMs: Int, val hotTtlS: Int, val current: Int?, val onChannelMs: Long?,
                           val switches: Long, val tuneErrors: Long, val transitionDrops: Long, val switchUsLast: Long,
                           val switchUsMax: Long, val channels: List<DronkScanChannel>)

/** A strictly decoded Remote ID pack that is not trackable (receiver `reception` record). Never a track or CoT. */
data class DronkBasicId(val idType: Int, val uaType: Int, val value: String?)
data class DronkRidReception(val stream: String, val publicReceiverId: String, val sessionId: String, val sequence: ULong,
                             val observedAt: Instant, val receivedAt: Instant, val transport: String, val channel: Int,
                             val frequencyMhz: Int, val rssiDbm: Int, val transmitter: String, val messageCounter: Int,
                             val protocolVersion: Int, val decodedMessages: Int, val messages: List<String>,
                             val basicIds: List<DronkBasicId>, val aircraftLocation: String, val reason: String,
                             val synthetic: Boolean)

private val RECEPTION_MESSAGES = listOf("basic_id", "location", "auth", "self_id", "system", "operator_id")
private val RECEPTION_REASONS = setOf("no_basic_id", "no_textual_id", "ambiguous_id", "no_valid_position")
private val SUPPORTED_CHANNELS = (1..11).toSet() + setOf(36, 40, 44, 48, 149, 153, 157, 161, 165)
private const val U32_MAX = 4294967295L
private fun u32(value: Any?): Long = (value as? JsonNumber)?.text?.toLongOrNull()?.takeIf { it in 0..U32_MAX } ?: bad()
private fun scanStatus(f: Map<String, Any?>, statusChannel: Int?): DronkScanStatus {
    val mode = f.str("mode").also { if (it != "hop" && it != "fixed") bad() }
    val current = f.optionalInt("current", 1..165)?.also { if (it !in SUPPORTED_CHANNELS || it != statusChannel) bad() }
    val onChannel = if (f.containsKey("on_channel_ms") && f["on_channel_ms"] == null) null else u32(f["on_channel_ms"])
    if ((current == null) != (onChannel == null)) bad()
    val rows = f["channels"] as? List<*> ?: bad()
    if (rows.isEmpty() || rows.size > 20 || (mode == "fixed") != (rows.size == 1)) bad()
    val channels = rows.map { row ->
        val r = row as? List<*> ?: bad()
        if (r.size != 7) bad()
        val channel = u32(r[0]).toInt().also { if (it !in SUPPORTED_CHANNELS) bad() }
        val hot = when (u32(r[6])) { 0L -> false; 1L -> true; else -> bad() }
        DronkScanChannel(channel, u32(r[1]), u32(r[2]), u32(r[3]), u32(r[4]), r[5]?.let(::u32), hot)
    }
    if (channels.map { it.channel }.toSet().size != channels.size || current != null && channels.none { it.channel == current }) bad()
    return DronkScanStatus(mode, f.small("dwell_ms", 50..2000), f.small("hot_ttl_s", 5..600), current, onChannel,
        u32(f["switches"]), u32(f["tune_errors"]), u32(f["transition_drops"]), u32(f["switch_us_last"]), u32(f["switch_us_max"]), channels)
}

private fun bad(): Nothing = throw InvalidSerialData()
private fun Map<String, Any?>.str(key: String): String = this[key] as? String ?: bad()
private fun Map<String, Any?>.integer(key: String): Long = (this[key] as? JsonNumber)?.text?.toLongOrNull() ?: bad()
private fun Map<String, Any?>.unsigned(key: String): ULong = (this[key] as? JsonNumber)?.text?.toULongOrNull() ?: bad()
private fun Map<String, Any?>.num(key: String): Double? {
    if (!containsKey(key)) bad()
    if (this[key] == null) return null
    return (this[key] as? JsonNumber)?.text?.toDoubleOrNull()?.takeIf { it.isFinite() } ?: bad()
}
private fun Map<String, Any?>.obj(key: String): Map<String, Any?> {
    @Suppress("UNCHECKED_CAST")
    return this[key] as? Map<String, Any?> ?: bad()
}
private fun Map<String, Any?>.small(key: String, range: IntRange): Int = integer(key).also {
    if (it !in range.first.toLong()..range.last.toLong()) bad()
}.toInt()
private fun Map<String, Any?>.optionalInt(key: String, range: IntRange): Int? =
    if (containsKey(key) && this[key] == null) null else small(key, range)

class DronkReceiverHub(
    streams: Map<String, String?>,
    private val clock: Clock = Clock.systemUTC(),
    private val staleAfter: Duration = Duration.ofSeconds(60),
    private val allowLegacyProvisional: Boolean = true,
    private val simulation: Boolean = false,
    private val legacyNamespace: String = "dronk",
    private val receiverAliases: Map<String, String> = emptyMap(),
    private val onProcessed: (ProcessedSerialDetection) -> Unit,
    private val onDiagnostic: (DronkReceiverSnapshot) -> Unit = {},
    private val onReception: (DronkRidReception) -> Unit = {}
) {
    private val tracker = AircraftTracker(staleAfter, clock)
    private val cot = CotGenerator(staleAfter)
    private val fingerprints = linkedMapOf<String, Instant>()
    private inner class Stream(val alias: String, val expected: String?) {
        val framer = LineFramer()
        var connected = false
        var receiver: String? = null
        var session: String? = null
        var seq: ULong? = null
        var uptime = -1L
        var resetReason: Int? = null
        val retired = linkedSetOf<String>()
        var accepted = 0L; var ignored = 0L; var rejected = 0L; var duplicates = 0L
        var state = "disconnected"
        var channel: Int? = null; var frequency: Int? = null
        var firmware = emptyMap<String, ULong>()
        var scan: DronkScanStatus? = null
        var lastSeen: Instant? = null
        var transportErrors = 0L
        val legacy = LiveSerialProcessor("$legacyNamespace-$alias", { if (allowLegacyProvisional) LegacyAssociationPolicy.ALLOW_PROVISIONAL else LegacyAssociationPolicy.DISABLED },
            staleAfter, clock, tracker, onProcessed = { result ->
                if (result.tracking is TrackUpdate.Updated) accepted++ else ignored++
                onProcessed(result)
            }, onDiagnostic = { event, _ ->
                if (event is SerialEvent.Rejected) rejected++
                if (event is SerialEvent.Ignored) ignored++
            })
        fun snapshot() = DronkReceiverSnapshot(alias, connected, receiver, session, accepted, ignored, rejected, duplicates,
            state, channel, frequency, firmware.toMap(), lastSeen, transportErrors, uptime.takeIf { it >= 0 }, resetReason, scan)
    }
    private val receivers: Map<String, Stream>
    init {
        require(receiverAliases.keys.all { it in streams })
        val publicIds=streams.keys.map { receiverAliases[it] ?: it }
        require(publicIds.toSet().size==publicIds.size && publicIds.all {
            it.matches(Regex("[A-Za-z0-9_-]{1,32}")) && !it.contains(Regex("(?i)c5-[0-9a-f]{12}|(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}"))
        }) { "Invalid public receiver alias" }
        require(streams.size in 1..8 && streams.keys.all { it.matches(Regex("[A-Za-z0-9_-]{1,32}")) })
        receivers = streams.mapValues { Stream(it.key, it.value) }
    }
    fun snapshots(): List<DronkReceiverSnapshot> = receivers.values.map { it.snapshot() }
    fun activeTracks(now: Instant = clock.instant()) = tracker.active(now)
    fun connected(stream: String) { receivers.getValue(stream).apply { connected = true; state = "awaiting_data" }; notify(stream) }
    fun disconnected(stream: String) {
        receivers.getValue(stream).apply { connected = false; state = "disconnected"; framer.reset(); legacy.disconnected() }
        notify(stream)
    }
    private fun notify(stream: String) = onDiagnostic(receivers.getValue(stream).snapshot())
    fun transportError(stream: String) {
        disconnected(stream)
        receivers.getValue(stream).apply { state = "transport_error"; transportErrors++ }
        notify(stream)
    }
    fun feed(stream: String, bytes: ByteArray, receivedAt: Instant = clock.instant()) {
        val s = receivers.getValue(stream)
        require(s.connected) { "Stream must be connected before feeding" }
        s.framer.feed(bytes) { frame ->
            if (frame is Frame.Oversized) s.rejected++
            else {
                val raw = (frame as Frame.Line).bytes
                try { line(s, raw, receivedAt) } catch (_: IllegalArgumentException) { s.rejected++ }
                catch (_: java.nio.charset.CharacterCodingException) { s.rejected++ }
            }
            onDiagnostic(s.snapshot())
        }
    }
    private fun line(s: Stream, raw: ByteArray, at: Instant) {
        if (at < Instant.EPOCH || at > clock.instant().plusSeconds(5)) bad()
        val text = Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).decode(java.nio.ByteBuffer.wrap(raw)).toString().trim()
        val fields = if (text.startsWith("{")) try { StrictJson(text).objectValue() } catch (_: InvalidSerialData) { null } else null
        if (fields == null && text.startsWith("{") && (s.receiver != null || s.expected != null)) bad()
        if (fields == null || fields.keys.none { it in setOf("protocol", "version", "receiver_id", "session_id") }) {
            if (s.receiver != null || s.expected != null) { s.ignored++; return }
            s.legacy.feed(raw + byteArrayOf(10), SerialContext(at, if (simulation) ObservationSource.SIMULATION else ObservationSource.OUI_SPY_USB))
            s.lastSeen = at; s.state = "legacy_receiving"
            return
        }
        if (raw.size > 3070 || fields.str("protocol") !in setOf("ouitak.receiver", "dronk.receiver") || fields.integer("version") != 1L) bad()
        val id = fields.str("receiver_id").also { if (it.isBlank() || it.length > 64 || it.any { c -> c.code !in 32..126 }) bad() }
        val session = fields.str("session_id").also { if (!it.matches(Regex("[0-9a-f]{32}"))) bad() }
        val seq = fields.unsigned("seq")
        val uptime = fields.integer("uptime_us").also { if (it < 0) bad() }
        if (!fields.containsKey("receiver_utc") || fields["receiver_utc"] != null) bad()
        val type = fields.str("type")
        if (type !in setOf("hello", "status", "error", "observation", "reception")) bad()
        if (s.expected != null && id != s.expected || s.receiver != null && id != s.receiver) bad()
        if (receivers.values.any { it !== s && it.receiver == id }) bad() // collision: fail closed
        if (session in s.retired) bad()
        if (s.session == session && (s.seq?.let { seq <= it } == true || uptime < s.uptime)) {
            s.duplicates++; s.ignored++; return
        }
        if (s.session != null && s.session != session && type != "hello" && s.state != "awaiting_data") bad()
        // Validate the entire record before advancing session/sequence or counters.
        var scan: DronkScanStatus? = null
        var reception: DronkRidReception? = null
        val detection = when (type) {
            "observation" -> observation(s, fields, id, session, seq, uptime, at)
            "reception" -> { reception = reception(s, fields, session, seq, uptime, at); null }
            "status" -> {
                if (fields.str("state") !in setOf("monitoring", "stopped_error")) bad()
                val channel = validateChannel(fields, nullable = true)
                if (fields.containsKey("scan")) scan = scanStatus(fields.obj("scan"), channel)
                fields.obj("counters").forEach { (k, v) ->
                    if (k.length > 64 || (v as? JsonNumber)?.text?.toULongOrNull() == null) bad()
                }
                null
            }
            "hello" -> {
                if (fields["passive"] != true || fields["hopping"] !is Boolean || fields["ble"] != false) bad()
                fields.str("firmware"); fields.str("board"); fields.str("decoder_revision")
                null
            }
            else -> { fields.str("operation").also { if (it.length > 128) bad() }; fields.integer("code"); null }
        }
        val resetReason = if (fields.containsKey("reset_reason")) fields.small("reset_reason", 0..255) else null
        if (s.session != session) {
            s.session?.let { s.retired.add(it) }
            if (s.retired.size > 16) s.retired.remove(s.retired.first())
            s.firmware = emptyMap(); s.channel = null; s.frequency = null; s.resetReason = null; s.scan = null
        }
        if (resetReason != null) s.resetReason = resetReason
        s.receiver = id; s.session = session; s.seq = seq; s.uptime = uptime; s.lastSeen = at
        s.state = when (type) { "status" -> fields.str("state"); "error" -> "receiver_error"; "hello" -> "initializing"; else -> "receiving" }
        if (reception != null) {
            s.channel = reception.channel; s.frequency = reception.frequencyMhz
            onReception(reception)
            return
        }
        if (type == "status") {
            s.channel = fields.optionalInt("channel", 1..165); s.frequency = fields.optionalInt("frequency_mhz", 2400..6000)
            s.firmware = fields.obj("counters").mapValues { (_, v) -> (v as JsonNumber).text.toULong() }
            s.scan = scan
        }
        if (detection == null) { s.ignored++; return }
        val m = detection.dronk!!
        s.channel = m.channel; s.frequency = m.frequencyMhz
        val fingerprint = listOf(detection.identity, detection.position, m.operatorLocationType, m.operatorLocation.position,
            m.operatorLocation.validity, m.locationSecondsAfterHour, m.operatorSecondsSince2019, simulation).toString()
        fingerprints.entries.removeAll { Duration.between(it.value, at) >= Duration.ofSeconds(2) }
        if (fingerprints.containsKey(fingerprint)) {
            s.duplicates++; s.ignored++
            onProcessed(ProcessedSerialDetection(detection, TrackUpdate.Ignored("Duplicate payload within two seconds; receiver metadata retained"), null))
            return
        }
        if (fingerprints.size >= 2048) fingerprints.remove(fingerprints.keys.first())
        fingerprints[fingerprint] = at
        val tracking = tracker.observe(detection, clock.instant())
        if (tracking is TrackUpdate.Updated) s.accepted++ else s.ignored++
        onProcessed(ProcessedSerialDetection(detection, tracking, if (tracking is TrackUpdate.Updated) cot.generate(detection) else null))
    }
    private fun validateChannel(f: Map<String, Any?>, nullable: Boolean = false): Int? {
        val ch = if (nullable) f.optionalInt("channel", 1..165) else f.small("channel", 1..165)
        val freq = if (nullable) f.optionalInt("frequency_mhz", 2400..6000) else f.small("frequency_mhz", 2400..6000)
        if (ch == null) { if (freq != null) bad(); return null }
        if (!(ch in 1..11 || ch in listOf(36,40,44,48,149,153,157,161,165)) || freq != if (ch <= 11) 2407 + ch * 5 else 5000 + ch * 5) bad()
        return ch
    }
    private fun location(f: Map<String, Any?>, at: Instant): ReportedLocation {
        val validity = when (f.str("validity")) { "valid" -> LocationValidity.VALID; "invalid" -> LocationValidity.INVALID; "unavailable" -> LocationValidity.UNAVAILABLE; else -> bad() }
        val lat = f.num("latitude"); val lon = f.num("longitude"); val alt = f.num("altitude_hae_m")
        if (alt != null && alt !in -999.999999..31767.5) bad()
        val position = if (validity == LocationValidity.VALID) {
            if (lat == null || lon == null || lat == 0.0 && lon == 0.0) bad()
            Position(lat, lon, alt)
        } else { if (lat != null || lon != null || alt != null) bad(); null }
        return ReportedLocation(position, at, if (simulation) LocationProvenance.SIMULATION else LocationProvenance.REMOTE_ID_BROADCAST, validity)
    }
    private fun observedAt(f: Map<String, Any?>, uptime: Long, at: Instant): Instant {
        val rx = f.integer("received_uptime_us").also { if (it !in 0..uptime || uptime - it > 86400000000L) bad() }
        return at.minusNanos(Math.multiplyExact(uptime - rx, 1000)).also { if (it < Instant.EPOCH) bad() }
    }
    /** Strict: every field is checked and must be self-consistent; anything else rejects the line. */
    private fun reception(s: Stream, f: Map<String, Any?>, session: String, seq: ULong, uptime: Long, at: Instant): DronkRidReception {
        if (f["trackable"] != false || f["authenticated"] != false) bad()
        val observedAt = observedAt(f, uptime, at)
        val transport = f.str("transport").also { if (it !in setOf("wifi_beacon", "wifi_nan")) bad() }
        val channel = validateChannel(f)!!
        val mac = f.str("transmitter").also { if (!it.matches(Regex("[0-9a-f]{2}(:[0-9a-f]{2}){5}"))) bad() }
        val messages = (f["messages"] as? List<*> ?: bad()).map { it as? String ?: bad() }
        // Firmware order, no duplicates, at least one decoded message type.
        if (messages.isEmpty() || messages != RECEPTION_MESSAGES.filter { it in messages } || messages.size != messages.toSet().size) bad()
        val ids = (f["basic_ids"] as? List<*> ?: bad()).map { raw ->
            @Suppress("UNCHECKED_CAST") val b = raw as? Map<String, Any?> ?: bad()
            if (b.keys != setOf("id_type", "ua_type", "value")) bad()
            val type = b.small("id_type", 0..15)
            val value = b["value"]?.let { v -> (v as? String)?.takeIf {
                type in 1..2 && it.length in 1..20 && it.all { c -> c.code in 32..126 } && it.first() != ' ' && it.last() != ' ' } ?: bad() }
            DronkBasicId(type, b.small("ua_type", 0..15), value)
        }
        val location = f.str("aircraft_location").also { if (it !in setOf("valid", "invalid", "unavailable")) bad() }
        val reason = f.str("reason").also { if (it !in RECEPTION_REASONS) bad() }
        val textual = ids.count { it.value != null }
        if (ids.size > 2 || ("basic_id" in messages) != ids.isNotEmpty() || ("location" !in messages && location != "unavailable")) bad()
        val expected = when { ids.isEmpty() -> "no_basic_id"; textual == 0 -> "no_textual_id"; textual > 1 -> "ambiguous_id"; else -> "no_valid_position" }
        if (reason != expected || reason == "no_valid_position" && location == "valid") bad()
        return DronkRidReception(s.alias, receiverAliases[s.alias] ?: s.alias, session, seq, observedAt, at, transport, channel,
            f.small("frequency_mhz", 2400..6000), f.small("rssi_dbm", -127..0), mac, f.small("message_counter", 0..255),
            f.small("odid_protocol_version", 0..2), f.small("decoded_messages", 1..9).also { if (it < messages.size) bad() },
            messages, ids, location, reason, simulation)
    }
    private fun observation(s: Stream, f: Map<String, Any?>, id: String, session: String, seq: ULong, uptime: Long, at: Instant): Detection {
        if (f.str("association") != "same_pack" || f["authenticated"] != false) bad()
        val rx = f.integer("received_uptime_us").also { if (it !in 0..uptime) bad() }
        if (uptime - rx > 86400000000L) bad()
        val observedAt = at.minusNanos(Math.multiplyExact(uptime - rx, 1000))
        if (observedAt < Instant.EPOCH) bad()
        val transport = f.str("transport").also { if (it !in setOf("wifi_beacon", "wifi_nan")) bad() }
        val channel = validateChannel(f)!!
        val rssi = f.small("rssi_dbm", -127..0)
        val mac = f.str("transmitter").also { if (!it.matches(Regex("[0-9a-f]{2}(:[0-9a-f]{2}){5}"))) bad() }
        val basic = f.obj("basic_id")
        val idType = when (basic.small("id_type", 1..2)) { 1 -> IdentifierType.SERIAL_NUMBER; else -> IdentifierType.CAA_REGISTRATION_ID }
        val aircraft = location(f.obj("aircraft"), observedAt)
        if (aircraft.validity != LocationValidity.VALID) bad()
        val op = f.obj("operator")
        val opType = op.optionalInt("location_type", 0..2)
        val operator = location(op.obj("location"), observedAt)
        if (opType == null && operator.validity != LocationValidity.UNAVAILABLE) bad()
        val seconds = f.num("location_timestamp_seconds_after_hour")?.also { if (it < 0 || it >= 3600) bad() }
        val sysTime = if (op.containsKey("timestamp_seconds_since_2019") && op["timestamp_seconds_since_2019"] == null) null
            else op.integer("timestamp_seconds_since_2019").also { if (it !in 1..4294967295L) bad() }
        val observation = RemoteIdObservation(basic.str("value"), idType, observedAt, at, aircraft.position!!.latitude,
            aircraft.position.longitude, aircraft.position.haeMeters, source = if (simulation) ObservationSource.SIMULATION else ObservationSource.DRONK_C5_USB,
            aircraftLocation = aircraft, takeoffLocation = operator.takeIf { opType == 0 }, controlStationLocation = operator.takeIf { opType == 1 || opType == 2 })
        val normalized = RemoteIdNormalizer.normalize(observation) as? NormalizationResult.Accepted ?: bad()
        return normalized.detection.copy(dronk = DronkReception(s.alias, id, session, seq, uptime, rx, transport, channel,
            f.small("frequency_mhz", 2400..6000), rssi, mac, f.small("message_counter", 0..255), basic.small("ua_type", 0..15), seconds, opType, sysTime, operator, receiverAliases[s.alias] ?: s.alias))
    }
}

/** Small JSON serializer for host telemetry; received strings never become executable markup. */
object DronkJson {
    fun encode(value: Any?): String = when (value) {
        null -> "null"
        is String -> "\"" + buildString { value.forEach { c -> append(when(c) { '"' -> "\\\""; '\\' -> "\\\\"; else -> if(c < ' ') "\\u%04x".format(c.code) else c.toString() }) } } + "\""
        is Boolean, is Number, is ULong -> value.toString()
        is Map<*, *> -> value.entries.joinToString(",", "{", "}") { encode(it.key.toString()) + ":" + encode(it.value) }
        is Iterable<*> -> value.joinToString(",", "[", "]") { encode(it) }
        else -> encode(value.toString())
    }
}
