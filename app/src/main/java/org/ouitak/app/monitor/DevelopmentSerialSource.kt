package org.ouitak.app.monitor

import java.io.Closeable

/** Variant-provided development byte source. Release builds provide no source. */
interface DevelopmentSerialSource : Closeable {
    fun start(onBytes: (ByteArray) -> Unit, onDisconnected: () -> Unit, onStatus: (String) -> Unit)
}
