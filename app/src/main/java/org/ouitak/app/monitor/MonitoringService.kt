package org.ouitak.app.monitor

import android.Manifest
import android.app.*
import android.content.*
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.hardware.usb.UsbManager
import android.hardware.usb.UsbDevice
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.*
import org.ouitak.app.MainActivity
import org.ouitak.app.R
import org.ouitak.app.core.*
import org.ouitak.app.storage.HistoryDatabase
import java.time.Duration
import java.time.Instant
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean
import org.ouitak.app.core.serial.*

/** User-started simulation, replay, or live OUI-SPY USB monitor. */
class MonitoringService : Service() {
    data class Status(val active:Boolean=false,val submitted:Long=0,val message:String="Stopped",val gps:Boolean=false,val mode:String="SIMULATION",val replayState:String="READY",val accepted:Long=0,val ignored:Long=0,val rejected:Long=0,val disconnects:Long=0,val transportState:String="READY",val transportMessage:String="Local UDP")
    companion object {
        @Volatile var status=Status(); private set
        const val STOP="org.ouitak.app.STOP_MONITORING"
        const val REPLAY="replay"
        const val PAUSE="org.ouitak.app.PAUSE_REPLAY"
        const val RESUME="org.ouitak.app.RESUME_REPLAY"
        const val RESET="org.ouitak.app.RESET_REPLAY"
        const val SPEED="org.ouitak.app.SPEED_REPLAY"
        const val GPS="receiverGps"
        const val USB_PERMISSION="org.ouitak.app.USB_PERMISSION"
        const val REQUEST_USB="org.ouitak.app.REQUEST_USB"
        const val LIVE_USB="liveUsb"
        const val EMULATED_SERIAL="emulatedSerial"
        const val DEBUG_COT_PORT="debugCotPort"
        const val LEGACY_POLICY="legacyAssociationPolicy"
        @Volatile var latest:List<Detection> = emptyList(); private set
        private const val CHANNEL="monitoring"
    }
    private lateinit var thread:HandlerThread
    private lateinit var worker:Handler
    private val simulator=Simulator()
    private val tracker=AircraftTracker()
    private var transport:CotTransport=LoopbackAtakTransport()
    private var receiverLocation:ReportedLocation?=null
    private var gpsEnabled=false
    private var usbCount=0
    private var usbGranted=0
    private var lastCleanup=0L
    private var lastNotification=0L
    private var replayMode=false
    private var liveMode=false
    private var emulatedMode=false
    @Volatile private var permissionPending=false
    private var usbReader:Thread?=null
    private val readerStop=AtomicBoolean(false)
    private lateinit var usbTransport:Esp32UsbSerialTransport
    private var serialPipeline:LiveSerialProcessor?=null
    private var developmentSerialSource:DevelopmentSerialSource?=null
    private var receiverNamespace=""
    private var replayEngine:ReplayEngine?=null
    private val replayPaths=mutableMapOf<String,MutableList<Detection>>()
    private lateinit var wake:PowerManager.WakeLock
    private val locationListener=object:LocationListener {
        override fun onLocationChanged(location:Location) {
            receiverLocation=ReportedLocation.fromCoordinates(location.latitude,location.longitude,
                Instant.ofEpochMilli(location.time),LocationProvenance.SENSOR_RECEIVER,
                if(location.hasAltitude()) location.altitude else null)
        }
    }
    private val usbReceiver=object:BroadcastReceiver() {
        override fun onReceive(context:Context,intent:Intent) { worker.post {
            if(intent.action==USB_PERMISSION) permissionPending=false
            refreshUsb()
        } }
    }
    override fun onCreate() {
        super.onCreate()
        thread=HandlerThread("oui-monitor",android.os.Process.THREAD_PRIORITY_BACKGROUND).apply { start() }
        worker=Handler(thread.looper)
        usbTransport=Esp32UsbSerialTransport(this)
        wake=(getSystemService(POWER_SERVICE) as PowerManager).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"OUI-TAK:monitor").apply { setReferenceCounted(false) }
        val filter=IntentFilter().apply { addAction(UsbManager.ACTION_USB_DEVICE_ATTACHED); addAction(UsbManager.ACTION_USB_DEVICE_DETACHED); addAction(USB_PERMISSION) }
        // The flags overload exists since API 26; export flags apply on API 33+.
        // USB system broadcasts require an exported receiver. Incoming broadcasts
        // only trigger an authoritative UsbManager rescan, never trust payloads.
        registerReceiver(usbReceiver,filter,if(Build.VERSION.SDK_INT>=33) Context.RECEIVER_EXPORTED else 0)
        (getSystemService(NOTIFICATION_SERVICE) as NotificationManager).createNotificationChannel(NotificationChannel(CHANNEL,"OUI-TAK monitoring",NotificationManager.IMPORTANCE_LOW))
    }
    override fun onStartCommand(intent:Intent?,flags:Int,startId:Int):Int {
        if(intent?.action==STOP) { HistoryDatabase.preferences(this).edit().putBoolean("monitorRequested",false).apply(); stopSelf(); return START_NOT_STICKY }
        val prefs=HistoryDatabase.preferences(this)
        if(intent?.action in listOf(PAUSE,RESUME,RESET,SPEED)) {
            worker.post {
                val engine=replayEngine
                when(intent?.action) {
                    PAUSE -> engine?.pause(System.nanoTime())
                    RESUME -> engine?.resume(System.nanoTime())
                    RESET -> { clearReplayPresentation(); engine?.reset(); replayPaths.clear() }
                    SPEED -> engine?.setSpeed(intent.getDoubleExtra("speed",1.0),System.nanoTime())
                }
                status=status.copy(replayState=engine?.state?.name ?: "READY")
                // Wake the scheduler only for a user command. Paused/finished
                // replay keeps its session available for Reset without polling.
                lastNotification=0L
                worker.removeCallbacks(loop);worker.post(loop)
            }
            return START_NOT_STICKY
        }
        if(intent?.action==REQUEST_USB) {
            worker.post {
                requestTargetUsbPermission()
                refreshUsb()
            }
            return START_NOT_STICKY
        }
        if(intent==null) { stopSelf(); return START_NOT_STICKY }
        // A new explicit mode can opt out of a previous GPS session.
        try { (getSystemService(LOCATION_SERVICE) as LocationManager).removeUpdates(locationListener) } catch(_:SecurityException) { }
        receiverLocation=null
        gpsEnabled=(!intent.getBooleanExtra(REPLAY,false) && intent.getBooleanExtra(GPS,false)) && checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)==PackageManager.PERMISSION_GRANTED
        try {
            val notification=notification(if(intent.getBooleanExtra(REPLAY,false)) "Historical replay" else if(intent.getBooleanExtra(LIVE_USB,false)) "Live OUI-SPY USB monitoring" else "SIMULATION monitoring")
            if(Build.VERSION.SDK_INT>=34) startForeground(4,notification,ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE or if(gpsEnabled) ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION else 0)
            else if(Build.VERSION.SDK_INT>=29) startForeground(4,notification,if(gpsEnabled) ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION else 0)
            else startForeground(4,notification)
        } catch(e:RuntimeException) {
            status=Status(message="Monitoring start denied: ${e.javaClass.simpleName}")
            prefs.edit().putBoolean("monitorRequested",false).apply(); stopSelf(); return START_NOT_STICKY
        }
        prefs.edit().putBoolean("monitorRequested",true).apply()
        status=status.copy(active=true,gps=gpsEnabled,message="Starting ${if(intent.getBooleanExtra(LIVE_USB,false)) "live USB" else "simulation/replay"} monitor")
        worker.post {
            replayMode=intent.getBooleanExtra(REPLAY,false)
            val requestedLive=!replayMode && intent.getBooleanExtra(LIVE_USB,false)
            val requestedEmulated=!replayMode && !requestedLive && intent.getBooleanExtra(EMULATED_SERIAL,false) && DebugSerialSourceFactory.available
            if(liveMode!=requestedLive || emulatedMode!=requestedEmulated || replayMode) {
                readerStop.set(true);usbReader?.interrupt();usbReader=null;serialPipeline?.disconnected();serialPipeline=null
                developmentSerialSource?.close();developmentSerialSource=null
                if(wake.isHeld) wake.release()
                tracker.clear();latest=emptyList()
            }
            liveMode=requestedLive
            emulatedMode=requestedEmulated
            try { transport.close() } catch(_:Exception) { }
            transport=try {
                TakServerSettings.createTransport(this,if(emulatedMode) intent.getIntExtra(DEBUG_COT_PORT,4242) else null)
            } catch(e:CotTransportFailure) { UnavailableCotTransport(e) }
            if(replayMode) {
                HistoryDatabase.cleanup(this)
                replayEngine=ReplayEngine(HistoryDatabase.get(this).all())
                replayPaths.clear()
                replayEngine!!.setSpeed(prefs.getFloat("replaySpeed",1f).toDouble(),System.nanoTime())
                replayEngine!!.resume(System.nanoTime())
            } else replayEngine=null
            status=status.copy(mode=if(replayMode) "REPLAY" else if(liveMode) "LIVE_USB" else if(emulatedMode) "EMULATED_SERIAL" else "SIMULATION",replayState=replayEngine?.state?.name ?: "READY",accepted=0,ignored=0,rejected=0,disconnects=0)
            refreshTransportStatus()
            if(emulatedMode) startEmulatedSerial()
            else if(liveMode) startUsbReader()
        }
        if(gpsEnabled) {
            try { (getSystemService(LOCATION_SERVICE) as LocationManager).requestLocationUpdates(LocationManager.GPS_PROVIDER,10000L,10f,locationListener,thread.looper) }
            catch(_:SecurityException) { gpsEnabled=false; status=status.copy(gps=false,message="GPS permission unavailable; monitoring continues without receiver GPS") }
            catch(_:IllegalArgumentException) { gpsEnabled=false; status=status.copy(gps=false,message="GPS provider unavailable; monitoring continues without receiver GPS") }
        }
        // A previous replay may be idle, with no scheduled loop. Every explicit
        // start must schedule the selected mode, not only service creation.
        worker.post { worker.removeCallbacks(loop);loop.run() }
        return START_NOT_STICKY
    }
    private fun notification(message:String):Notification {
        val open=PendingIntent.getActivity(this,0,Intent(this,MainActivity::class.java),PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val stop=PendingIntent.getService(this,1,Intent(this,javaClass).setAction(STOP),PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        return Notification.Builder(this,CHANNEL).setSmallIcon(R.drawable.ic_launcher).setContentTitle("OUI-TAK · ${status.mode} monitor")
            .setContentText(message).setContentIntent(open).setOngoing(true).setOnlyAlertOnce(true)
            .addAction(Notification.Action.Builder(null,"Stop Monitoring",stop).build()).build()
    }
    private fun refreshUsb() {
        val devices=usbTransport.devices()
        usbCount=devices.size; usbGranted=devices.count { it.permitted }
    }
    private fun requestTargetUsbPermission() {
        val device=usbTransport.devices().firstOrNull() ?: return
        if(device.permitted || permissionPending) return
        permissionPending=true
        usbTransport.requestPermission(device.device,PendingIntent.getBroadcast(this,2,
            Intent(USB_PERMISSION).setPackage(packageName),PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
    }
    private fun serialPolicy():LegacyAssociationPolicy = if(HistoryDatabase.preferences(this).getBoolean(LEGACY_POLICY,true))
        LegacyAssociationPolicy.ALLOW_PROVISIONAL else LegacyAssociationPolicy.DISABLED
    private fun onWorker(block:()->Unit) {
        if(Looper.myLooper()==thread.looper) block() else worker.post(block)
    }
    private fun createSerialPipeline() {
        val prefs=HistoryDatabase.preferences(this)
        receiverNamespace=prefs.getString("receiverNamespace",null) ?: UUID.randomUUID().toString().also {
            prefs.edit().putString("receiverNamespace",it).apply()
        }
        serialPipeline=LiveSerialProcessor(receiverNamespace=receiverNamespace,policy=::serialPolicy,tracker=tracker,
            onProcessed={ result -> onWorker { acceptLiveDetection(result) } },
            onDiagnostic={ event,diagnostics -> onWorker { updateSerialDiagnostics(event,diagnostics) } })
    }
    private fun startEmulatedSerial() {
        createSerialPipeline()
        val source=DebugSerialSourceFactory.create() ?: run {
            emulatedMode=false;status=status.copy(mode="SIMULATION",message="Debug serial source unavailable in this build");return
        }
        developmentSerialSource=source
        if(!wake.isHeld) wake.acquire()
        source.start(onBytes={ bytes ->
            onWorker {
                if(emulatedMode) serialPipeline?.feed(bytes,SerialContext(Instant.now(),ObservationSource.SIMULATION))
            }
        },onDisconnected={
            onWorker {
                if(emulatedMode) {
                    serialPipeline?.disconnected()
                    status=status.copy(disconnects=status.disconnects+1,message="SYNTHETIC serial disconnected; parser frame reset")
                }
            }
        },onStatus={ message -> onWorker { if(emulatedMode) status=status.copy(message=message) } })
    }
    private fun startUsbReader() {
        if(usbReader?.isAlive==true) return
        readerStop.set(false)
        if(!wake.isHeld) wake.acquire()
        createSerialPipeline()
        usbReader=Thread({
            val bytes=ByteArray(1024)
            while(!readerStop.get() && status.active && liveMode) {
                try {
                    refreshUsb()
                    val device=usbTransport.devices().firstOrNull()?.device
                    if(device==null) {
                        onWorker { if(liveMode) status=status.copy(message="Live USB: waiting for ESP32-S3 (303a:1001)") }
                        Thread.sleep(1000); continue
                    }
                    if(!(getSystemService(USB_SERVICE) as UsbManager).hasPermission(device)) {
                        requestTargetUsbPermission()
                        onWorker { if(liveMode) status=status.copy(message="Live USB: waiting for device permission") }
                        Thread.sleep(1000); continue
                    }
                    usbTransport.open(device).use { connection ->
                        onWorker { if(liveMode) status=status.copy(message="USB connected at 115200 8N1; reading Sky Spy lines") }
                        while(!readerStop.get() && status.active && liveMode) {
                            val count=connection.read(bytes)
                            if(count<0) break
                            if(count>0) {
                                val chunk=bytes.copyOf(count)
                                val context=SerialContext(Instant.now(),ObservationSource.OUI_SPY_USB)
                                onWorker { if(liveMode && !readerStop.get()) serialPipeline?.feed(chunk,context) }
                            }
                        }
                    }
                    onWorker {
                        serialPipeline?.disconnected()
                        if(!readerStop.get() && liveMode) status=status.copy(disconnects=status.disconnects+1,message="USB disconnected; waiting to reconnect")
                    }
                } catch(e:Exception) {
                    onWorker {
                        serialPipeline?.disconnected()
                        if(!readerStop.get() && liveMode) status=status.copy(message="USB serial error: ${e.javaClass.simpleName}; retrying")
                    }
                    try { Thread.sleep(1000) } catch (_:InterruptedException) { break }
                }
            }
        },"oui-usb-serial").apply { isDaemon=true; start() }
    }
    private fun updateSerialDiagnostics(event:SerialEvent,counters:SerialDiagnostics) {
        val latestEvent=when(event) {
            is SerialEvent.Rejected -> "rejected ${event.reason}"
            is SerialEvent.Ignored -> "ignored ${event.reason}"
            is SerialEvent.Candidate -> "validated candidate"
        }
        status=status.copy(accepted=counters.accepted,ignored=counters.ignored,rejected=counters.rejected,
            message="${if(emulatedMode) "SYNTHETIC SERIAL" else "LIVE USB"} · accepted ${counters.accepted} · ignored ${counters.ignored} · rejected ${counters.rejected} · latest $latestEvent")
    }
    private fun acceptLiveDetection(result:ProcessedSerialDetection) {
        val detection=result.detection
        if(!status.active || (!liveMode && !emulatedMode)) return
        latest=(latest.filterNot { it.cotUid==detection.cotUid }+detection).takeLast(100)
        if(result.tracking !is TrackUpdate.Updated) return
        try {
            sendCot(checkNotNull(result.cotXml))
            status=status.copy(submitted=status.submitted+1)
        } catch(e:Exception) {
            status=status.copy(message="Detection parsed; ${transport.status().message}")
        }
    }
    private fun sendCot(event:String):TransportSubmission {
        return try { transport.send(event).also { refreshTransportStatus() } }
        catch(e:Exception) { refreshTransportStatus();throw e }
    }
    private fun refreshTransportStatus() {
        val state=transport.status()
        status=status.copy(transportState=state.state.name,transportMessage=state.message)
    }
    private fun clearReplayPresentation() {
        val session=replayEngine?.session ?: return
        val now=Instant.now()
        for(points in replayPaths.values) {
            val last=points.lastOrNull() ?: continue
            val frame=ReplayFrame(last,now,session)
            val p=last.position ?: continue
            val removal=PresentationRemovalCot()
            sendCot(removal.generate(frame.uid,p,now))
            if(points.size>=2) sendCot(removal.generate("ouitak-trail-${frame.uid}",p,now))
            for(role in listOf(LocationRole.CONTROL_STATION,LocationRole.TAKEOFF,LocationRole.RECEIVER))
                points.mapNotNull { it.locations[role]?.position }.lastOrNull()?.let { sendCot(removal.generate("ouitak-location-${frame.uid}-$role",it,now)) }
        }
    }
    private val loop=object:Runnable {
        override fun run() {
            if(!status.active) return
            val replayRunning=!replayMode || replayEngine?.state==ReplayState.PLAYING
            if(liveMode) { if(!wake.isHeld) wake.acquire() }
            else if(replayRunning) wake.acquire(30000L) // Bounded renewal only during active processing.
            else if(wake.isHeld) wake.release()
            try {
                refreshUsb()
                val now=Instant.now()
                if(replayMode) {
                    val engine=replayEngine
                    val frames=engine?.poll(System.nanoTime(),now).orEmpty()
                    for(frame in frames) {
                        if(!status.active) break
                        sendCot(CotGenerator().generateReplay(frame))
                        val points=replayPaths.getOrPut(frame.original.cotUid) { mutableListOf() }
                        points+=frame.original
                        while(points.size>100) points.removeAt(0)
                        val selectedPoints=points.takeLast(HistoryDatabase.policy(this@MonitoringService).trailPoints)
                        if(selectedPoints.size>=2) sendCot(TrailCotGenerator().generate(selectedPoints,now,replayUid=frame.uid))
                        for(role in listOf(LocationRole.CONTROL_STATION,LocationRole.TAKEOFF,LocationRole.RECEIVER))
                            LocationCotGenerator(java.time.Duration.ofSeconds(HistoryDatabase.preferences(this@MonitoringService).getLong("locationExpiry",300))).generate(frame.original,role,now,frame)?.let { sendCot(it) }
                        status=status.copy(submitted=status.submitted+1)
                    }
                    status=status.copy(replayState=engine?.state?.name ?: "READY",message="HISTORICAL REPLAY ${engine?.state}; UDP ingestion unverified; Reset/Stop available")
                } else if(!liveMode) {
                    val observations=mutableListOf<Detection>()
                    for(id in listOf("SIM-DRONE-001","SIM-DRONE-002")) {
                        if(!status.active) break
                        val d=simulator.next(now,id).copy(receiverLocation=if(gpsEnabled) receiverLocation else null)
                        observations+=d
                        if(tracker.observe(d,now) is TrackUpdate.Updated) {
                            HistoryDatabase.record(this@MonitoringService,d)
                            sendCot(CotGenerator().generate(d))
                            val policy=HistoryDatabase.policy(this@MonitoringService)
                            val points=HistoryDatabase.get(this@MonitoringService).recentPositions(d.cotUid,now.minus(policy.retention),now,policy.trailPoints)
                            if(points.size>=2) sendCot(TrailCotGenerator().generate(points,now))
                            for(role in listOf(LocationRole.CONTROL_STATION,LocationRole.TAKEOFF,LocationRole.RECEIVER))
                                LocationCotGenerator(Duration.ofSeconds(HistoryDatabase.preferences(this@MonitoringService).getLong("locationExpiry",300))).generate(d,role,now)?.let { sendCot(it) }
                            status=status.copy(submitted=status.submitted+1)
                        }
                    }
                    latest=observations
                }
                if(SystemClock.elapsedRealtime()-lastCleanup>60000) { HistoryDatabase.cleanup(this@MonitoringService); lastCleanup=SystemClock.elapsedRealtime() }
                if(!replayMode && !liveMode && !emulatedMode) status=status.copy(message="SIMULATION → local UDP; USB devices $usbCount, access $usbGranted; live USB is separate",gps=gpsEnabled)
            } catch(e:Exception) { status=status.copy(message="Monitoring error: ${e.javaClass.simpleName}; will retry") }
            if(SystemClock.elapsedRealtime()-lastNotification>=2000) {
                lastNotification=SystemClock.elapsedRealtime()
                HistoryDatabase.preferences(this@MonitoringService).edit().putLong("monitorSubmitted",status.submitted).apply()
                if(status.active) (getSystemService(NOTIFICATION_SERVICE) as NotificationManager).notify(4,notification("${status.submitted} CoT submissions · ${status.mode} · Stop available"))
            }
            if(status.active && (!replayMode || replayEngine?.state==ReplayState.PLAYING) && !liveMode && !emulatedMode)
                worker.postDelayed(this,if(replayMode) 200L else 2000L)
            else if(!liveMode && !emulatedMode && wake.isHeld) wake.release()
        }
    }
    override fun onTimeout(startId:Int,fgsType:Int) { stopSelf() }
    override fun onDestroy() {
        status=status.copy(active=false,message="Stopped"); latest=emptyList()
        HistoryDatabase.preferences(this).edit().putBoolean("monitorRequested",false).putLong("monitorSubmitted",status.submitted).apply()
        worker.removeCallbacksAndMessages(null)
        readerStop.set(true);usbReader?.interrupt();usbReader=null;developmentSerialSource?.close();developmentSerialSource=null
        try { transport.close() } catch(_:Exception) { }
        serialPipeline?.disconnected();serialPipeline=null;liveMode=false;emulatedMode=false
        try { (getSystemService(LOCATION_SERVICE) as LocationManager).removeUpdates(locationListener) } catch(_:SecurityException) { }
        unregisterReceiver(usbReceiver)
        if(wake.isHeld) wake.release()
        thread.quitSafely(); stopForeground(STOP_FOREGROUND_REMOVE)
        super.onDestroy()
    }
    override fun onBind(intent:Intent?) = null
}
