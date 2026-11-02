package org.ouitak.app.monitor

import java.util.concurrent.atomic.AtomicBoolean

object DebugSerialSourceFactory {
    const val available = true
    fun create(): DevelopmentSerialSource = SkySpyScenarioSource()
}

/** Deterministic byte-level emulator feed that deliberately mimics USB chunking. */
private class SkySpyScenarioSource : DevelopmentSerialSource {
    private val running=AtomicBoolean(false)
    @Volatile private var worker:Thread?=null

    override fun start(onBytes:(ByteArray)->Unit,onDisconnected:()->Unit,onStatus:(String)->Unit) {
        if(!running.compareAndSet(false,true)) return
        worker=Thread({
            var sample=0
            while(running.get()) {
                try {
                    onStatus("SYNTHETIC Sky Spy serial scenario: malformed, split, duplicate, moving, two IDs")
                    onBytes("{\"basic_id\":\"BROKEN\",\"drone_lat\":oops}\r\n".toByteArray())
                    val a=record("SYNTH-A",34.200000+sample*0.0002,-117.300000,123)
                    val b=record("SYNTH-B",34.205000,-117.305000,141)
                    // Split one line across writes to exercise USB read fragmentation.
                    val split=a.length/2
                    onBytes(a.substring(0,split).toByteArray());Thread.sleep(80)
                    onBytes((a.substring(split)+"\r\n").toByteArray())
                    onBytes((b+"\n").toByteArray())
                    // Identical payload repeated as a second receive observation.
                    onBytes((a+"\n").toByteArray())
                    // Leave a partial record, disconnect, and prove the parser resets.
                    onBytes("{\"mac\":\"02:fa:ke".toByteArray())
                    onStatus("SYNTHETIC serial disconnect; incomplete line discarded")
                    onDisconnected()
                    Thread.sleep(900)
                    onStatus("SYNTHETIC serial source reconnected")
                    onBytes((record("SYNTH-A",34.2002+sample*0.0002,-117.3000,123)+"\n").toByteArray())
                    sample=(sample+1)%20
                    Thread.sleep(1500)
                } catch (_:InterruptedException) { break }
            }
        },"oui-debug-serial-scenario").apply { isDaemon=true; start() }
    }

    private fun record(id:String,lat:Double,lon:Double,alt:Int):String =
        """{"mac":"02:00:00:00:00:01","rssi":-58,"drone_lat":%.6f,"drone_long":%.6f,"drone_altitude":%d,"pilot_lat":34.210000,"pilot_long":-117.310000,"basic_id":"%s"}""".format(java.util.Locale.US,lat,lon,alt,id)

    override fun close() {
        running.set(false);worker?.interrupt();worker=null
    }
}
