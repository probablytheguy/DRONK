@file:Suppress("DEPRECATION")
package org.ouitak.app.monitor

import android.app.Activity
import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.os.SystemClock
import android.test.InstrumentationTestCase
import org.ouitak.app.MainActivity
import org.ouitak.app.core.ObservationSource
import org.ouitak.app.core.serial.OfflineSerialParser
import org.ouitak.app.core.serial.SerialContext
import org.ouitak.app.core.serial.SerialEvent
import java.time.Instant
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.io.ByteArrayInputStream
import javax.xml.parsers.DocumentBuilderFactory

/** Real service lifecycle tests; socket submissions are not receiver acknowledgements. */
class MonitoringServiceTest : InstrumentationTestCase() {
    private lateinit var activity: Activity
    private val context get() = instrumentation.targetContext
    override fun setUp() {
        super.setUp()
        activity = instrumentation.startActivitySync(Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        instrumentation.runOnMainSync { context.stopService(Intent(context, MonitoringService::class.java)) }
        await { !MonitoringService.status.active }
    }
    override fun tearDown() {
        instrumentation.runOnMainSync {
            context.stopService(Intent(context, MonitoringService::class.java))
            activity.finish()
        }
        await { !MonitoringService.status.active }
        super.tearDown()
    }
    private fun await(condition: () -> Boolean) {
        val end = SystemClock.elapsedRealtime() + 10000
        while (!condition() && SystemClock.elapsedRealtime() < end) SystemClock.sleep(100)
        assertTrue("Service condition timed out: ${MonitoringService.status}", condition())
    }
    private fun start() {
        instrumentation.runOnMainSync { activity.startForegroundService(Intent(activity, MonitoringService::class.java)) }
        await { MonitoringService.status.active && MonitoringService.latest.size == 2 }
        val services = (context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager).getRunningServices(100)
        assertTrue(services.any { it.service.className == MonitoringService::class.java.name && it.foreground })
        assertFalse(MonitoringService.status.message.startsWith("Monitoring error"))
        assertTrue(MonitoringService.latest.all { it.receiverLocation==null }) // GPS is opt-in.
    }
    fun testOfflineMalformedSerialCannotDisruptForegroundSimulation() {
        start()
        val before = MonitoringService.status.submitted
        val parser = OfflineSerialParser(128)
        val context = SerialContext(Instant.now(), ObservationSource.SIMULATION)
        var rejected = 0
        repeat(1000) {
            parser.feed(("{\"a\":1,\"a\":2}\n{\"drone_lat\":true}\n" + "x".repeat(512) + "\n").toByteArray(), context) {
                if (it is SerialEvent.Rejected) rejected++
            }
        }
        assertEquals(3000, rejected)
        await { MonitoringService.status.submitted > before }
        assertTrue(MonitoringService.status.active)
        assertTrue(MonitoringService.latest.all { it.source == ObservationSource.SIMULATION })
        assertTrue(MonitoringService.status.message.contains("live USB is separate"))
    }

    fun testExplicitStartAndStopCeasesUpdates() {
        start()
        val before = MonitoringService.status.submitted
        await { MonitoringService.status.submitted >= before + 4 }
        instrumentation.runOnMainSync { context.stopService(Intent(context, MonitoringService::class.java)) }
        await { !MonitoringService.status.active }
        SystemClock.sleep(500)
        val stopped = MonitoringService.status.submitted
        SystemClock.sleep(3000)
        assertEquals(stopped, MonitoringService.status.submitted)
        assertTrue(MonitoringService.latest.isEmpty())
    }
    fun testPausedReplayCanSwitchToContinuousSimulation() {
        instrumentation.runOnMainSync { activity.startForegroundService(Intent(activity, MonitoringService::class.java).putExtra(MonitoringService.REPLAY,true)) }
        await { MonitoringService.status.mode=="REPLAY" }
        instrumentation.runOnMainSync { context.startService(Intent(context, MonitoringService::class.java).setAction(MonitoringService.PAUSE)) }
        await { MonitoringService.status.replayState in listOf("PAUSED","FINISHED") }
        val before=MonitoringService.status.submitted
        start()
        await { MonitoringService.status.mode=="SIMULATION" && MonitoringService.status.submitted>=before+4 }
    }
    fun testContinuesWithAtakForegroundAfterActivityFinishes() {
        start()
        val before = MonitoringService.status.submitted
        // Explicit launch of the verified emulator build avoids Android package
        // visibility filtering of getLaunchIntentForPackage. No app permission
        // or production package-visibility declaration is needed for this test.
        val atak = Intent().setClassName("com.atakmap.app.civ", "com.atakmap.app.ATAKActivityCiv")
        instrumentation.runOnMainSync { activity.startActivity(atak); activity.finish() }
        await { MonitoringService.status.submitted >= before + 6 }
        assertTrue(MonitoringService.status.active)
        assertEquals(setOf("SIM-DRONE-001", "SIM-DRONE-002"), MonitoringService.latest.map { it.identifier }.toSet())
        assertFalse(MonitoringService.status.message.startsWith("Monitoring error"))
    }

    fun testDebugSyntheticSerialRunsThroughParserTrackerCotAndUdpListener() {
        assertTrue("Debug variant must include the development serial source",DebugSerialSourceFactory.available)
        val listener=DatagramSocket(0,InetAddress.getByName("127.0.0.1")).apply { soTimeout=5000 }
        val received=mutableListOf<org.w3c.dom.Element>()
        val startAt=Instant.now()
        try {
            val intent=Intent(activity,MonitoringService::class.java)
                .putExtra(MonitoringService.EMULATED_SERIAL,true)
                .putExtra(MonitoringService.DEBUG_COT_PORT,listener.localPort)
            instrumentation.runOnMainSync { activity.startForegroundService(intent) }
            await { MonitoringService.status.mode=="EMULATED_SERIAL" && MonitoringService.status.accepted>=2 }
            val deadline=SystemClock.elapsedRealtime()+12000
            while(SystemClock.elapsedRealtime()<deadline) {
                val data=ByteArray(8192)
                val packet=DatagramPacket(data,data.size)
                listener.receive(packet)
                val factory=DocumentBuilderFactory.newInstance().apply { isNamespaceAware=false }
                val document=factory.newDocumentBuilder().parse(ByteArrayInputStream(packet.data,0,packet.length))
                received+=document.documentElement
                val a=received.filter { it.getElementsByTagName("contact").item(0)?.attributes?.getNamedItem("callsign")?.nodeValue?.contains("SYNTH-A")==true }
                val b=received.any { it.getElementsByTagName("contact").item(0)?.attributes?.getNamedItem("callsign")?.nodeValue?.contains("SYNTH-B")==true }
                if(a.size>=3 && b && MonitoringService.status.disconnects>0 && MonitoringService.status.rejected>0) break
            }
            val aircraft=received.mapNotNull { event ->
                val contact=event.getElementsByTagName("contact").item(0)?.attributes?.getNamedItem("callsign")?.nodeValue ?: return@mapNotNull null
                if(!contact.contains("SYNTH-A") && !contact.contains("SYNTH-B")) return@mapNotNull null
                event
            }
            assertTrue("two synthetic IDs must reach UDP listener",aircraft.any { it.getElementsByTagName("contact").item(0).attributes.getNamedItem("callsign").nodeValue.contains("SYNTH-A") })
            assertTrue("second synthetic ID must reach UDP listener",aircraft.any { it.getElementsByTagName("contact").item(0).attributes.getNamedItem("callsign").nodeValue.contains("SYNTH-B") })
            val aEvents=aircraft.filter { it.getElementsByTagName("contact").item(0).attributes.getNamedItem("callsign").nodeValue.contains("SYNTH-A") }
            assertTrue("duplicate and moved observations should be emitted",aEvents.size>=3)
            assertEquals("one stable UID per receiver-scoped aircraft",1,aEvents.map { it.getAttribute("uid") }.distinct().size)
            assertTrue("simulation must use synthetic how",aircraft.all { it.getAttribute("how")=="m-s" })
            assertTrue("simulation marker must carry synthetic label",aircraft.all { it.textContent.contains("SYNTHETIC SKY SPY SERIAL") })
            assertTrue("synthetic provenance must be explicit",aircraft.all { it.getElementsByTagName("ouitak").item(0).attributes.getNamedItem("synthetic").nodeValue=="true" })
            val points=aEvents.map { it.getElementsByTagName("point").item(0) }
            val latitudes=points.map { it.attributes.getNamedItem("lat").nodeValue.toDouble() }
            val longitudes=points.map { it.attributes.getNamedItem("lon").nodeValue.toDouble() }
            assertTrue(latitudes.all { it in -90.0..90.0 })
            assertTrue(longitudes.all { it in -180.0..180.0 })
            assertEquals("repeated sample exercises duplicate observations",latitudes[0],latitudes[1],0.0)
            assertTrue("moving source must change aircraft position",latitudes.distinct().size>=2)
            val times=aircraft.map { Instant.parse(it.getAttribute("time")) }
            assertTrue(times.all { !it.isBefore(startAt.minusSeconds(2)) && !it.isAfter(Instant.now().plusSeconds(2)) })
            assertTrue(aircraft.all { Instant.parse(it.getAttribute("stale")).epochSecond-Instant.parse(it.getAttribute("time")).epochSecond==60L })
            assertTrue("malformed serial frame must be rejected",MonitoringService.status.rejected>0)
            assertTrue("disconnect must reset the parser",MonitoringService.status.disconnects>0)
        } finally {
            listener.close()
            instrumentation.runOnMainSync { context.stopService(Intent(context,MonitoringService::class.java)) }
            await { !MonitoringService.status.active }
        }
    }
}
