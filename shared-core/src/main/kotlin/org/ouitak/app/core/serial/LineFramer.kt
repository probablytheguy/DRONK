package org.ouitak.app.core.serial

sealed class Frame {
    data class Line(val bytes: ByteArray) : Frame()
    data object Oversized : Frame()
}

/** Single-owner stream state. Memory is bounded independently of input/read size.
 * LF terminates records; one trailing CR is stripped. Limits include that CR.
 * Results are streamed to the caller rather than accumulated per input chunk. */
class LineFramer(val maxLineBytes: Int = 4096) {
    init { require(maxLineBytes in 1..65536) }
    private val buffer = ByteArray(maxLineBytes)
    private var size = 0
    private var dropping = false
    val bufferedBytes: Int get() = size

    fun feed(bytes: ByteArray, emit: (Frame) -> Unit) {
        for (byte in bytes) {
            if (byte == 10.toByte()) {
                if (!dropping) {
                    val length = if (size > 0 && buffer[size - 1] == 13.toByte()) size - 1 else size
                    val line = buffer.copyOf(length)
                    size = 0
                    emit(Frame.Line(line))
                }
                size = 0
                dropping = false
            } else if (!dropping) {
                if (size == maxLineBytes) {
                    size = 0
                    dropping = true
                    emit(Frame.Oversized)
                } else buffer[size++] = byte
            }
        }
    }

    /** Disconnect/end-of-file: incomplete data is never parsed or joined to a new session. */
    fun reset() { size = 0; dropping = false }
}
