package org.ouitak.app.core.serial

import java.nio.ByteBuffer
import java.nio.charset.CharacterCodingException
import java.nio.charset.CodingErrorAction

/** Offline byte consumer only. No USB/Android/file/network dependency or side effects.
 * One owner per stream. Callback exceptions belong to the caller, not the decoder.
 * Context is sampled for the chunk completing a line; incomplete lines have no time. */
class OfflineSerialParser(maxLineBytes: Int = 4096) {
    private val framer = LineFramer(maxLineBytes)
    val bufferedBytes: Int get() = framer.bufferedBytes
    fun reset() = framer.reset()

    fun feed(bytes: ByteArray, context: SerialContext, emit: (SerialEvent) -> Unit) {
        framer.feed(bytes) { frame ->
            emit(when (frame) {
                Frame.Oversized -> SerialEvent.Rejected(SerialEvent.RejectReason.OVERSIZED)
                is Frame.Line -> parse(frame.bytes, context)
            })
        }
    }

    private fun parse(bytes: ByteArray, context: SerialContext): SerialEvent {
        val text = try {
            Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString().trim(' ', '\t', '\r')
        } catch (_: CharacterCodingException) {
            return SerialEvent.Rejected(SerialEvent.RejectReason.INVALID_UTF8)
        }
        if (text.isEmpty()) return SerialEvent.Ignored(SerialEvent.IgnoreReason.EMPTY)
        if (text.startsWith("ESP-ROM:")) return SerialEvent.Ignored(SerialEvent.IgnoreReason.BOOT, "ESP-ROM banner")
        if (text.startsWith("rst:")) return SerialEvent.Ignored(SerialEvent.IgnoreReason.BOOT, "reset-reason line")
        if (text.startsWith("OUI SPY UNIFIED FIRMWARE")) return SerialEvent.Ignored(SerialEvent.IgnoreReason.BOOT, "Unified firmware banner")
        if (text == "{\"   [+] Device is active and scanning...\"}")
            return SerialEvent.Ignored(SerialEvent.IgnoreReason.STATUS, "legacy scanning text object")
        if (!text.startsWith("{") && !text.startsWith("[")) return SerialEvent.Ignored(SerialEvent.IgnoreReason.DIAGNOSTIC)
        // Bracketed firmware diagnostics are not arrays or JSON fragments.
        if (listOf("[OUI-SPY]", "[SKY-SPY]").any { text.startsWith(it) }) return SerialEvent.Ignored(SerialEvent.IgnoreReason.DIAGNOSTIC)
        val fields = try { StrictJson(text).objectValue() } catch (_: InvalidSerialData) {
            return SerialEvent.Rejected(SerialEvent.RejectReason.INVALID_JSON)
        }
        val detectionKeys = setOf("mac", "basic_id", "rssi", "drone_lat", "drone_long", "drone_altitude", "pilot_lat", "pilot_long")
        if (fields.keys.none { it in detectionKeys })
            return SerialEvent.Ignored(SerialEvent.IgnoreReason.STATUS, "JSON fields=${fields.keys.sorted().joinToString(",")}")
        if (!CandidateValidator.validContext(context)) return SerialEvent.Rejected(SerialEvent.RejectReason.INVALID_CONTEXT)
        return try { SerialEvent.Candidate(CandidateValidator.validate(fields, context)) }
        catch (_: InvalidSerialData) { SerialEvent.Rejected(SerialEvent.RejectReason.INVALID_FIELDS) }
    }
}
