package org.ouitak.app

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.security.KeyChain
import android.view.View
import android.text.InputType
import org.ouitak.app.monitor.MonitoringService
import org.ouitak.app.monitor.DebugSerialSourceFactory
import org.ouitak.app.monitor.TakServerSettings
import org.ouitak.app.core.CotConnectionMode
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.graphics.Color
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.EditText
import org.ouitak.app.core.AircraftTracker
import org.ouitak.app.core.CotGenerator
import org.ouitak.app.core.Detection
import org.ouitak.app.core.Simulator
import org.ouitak.app.core.TrackUpdate
import java.time.Duration
import java.util.concurrent.Executors

class MainActivity : Activity() {
    private val simulator = Simulator()
    private val tracker = AircraftTracker()
    private val history = ArrayDeque<Detection>() // Existing ephemeral observation log, not flight paths.
    private val handler = Handler(Looper.getMainLooper())
    private val sender = Executors.newSingleThreadExecutor()
    private lateinit var features:HistoryFeatures
    private lateinit var monitorStatus:TextView
    private var pendingPermissionAction:(()->Unit)?=null
    private var selected = "SIM-DRONE-001"
    private var running = false
    private lateinit var status: TextView
    private lateinit var detections: TextView
    private lateinit var tracks: TextView
    private lateinit var xml: TextView
    private var historyPreview: String? = null
    private lateinit var toggle: Button
    private lateinit var selection: Button
    private lateinit var expiry: Button
    private lateinit var delivery: TextView
    private lateinit var sendTest: Button
    private lateinit var sendPair: Button
    private val tick = object : Runnable {
        override fun run() {
            if (running) { generate(); handler.postDelayed(this, 2000) }
        }
    }
    private val refresh = object : Runnable {
        override fun run() { render(); handler.postDelayed(this, 1000) }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(24, 24, 24, 24)
            setBackgroundColor(Color.rgb(16, 27, 38))
        }
        panel.setOnApplyWindowInsetsListener { view, insets ->
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                val bars = insets.getInsets(android.view.WindowInsets.Type.systemBars())
                view.setPadding(24 + bars.left, 24 + bars.top, 24 + bars.right, 24 + bars.bottom)
            }
            insets
        }
        fun label(value: String, size: Float = 16f): TextView = TextView(this).apply {
            text = value; textSize = size; setTextColor(Color.rgb(222, 235, 242))
            setPadding(0, 12, 0, 12); panel.addView(this)
        }
        fun button(value: String, action: () -> Unit) = Button(this).apply {
            text = value; setOnClickListener { action() }; panel.addView(this)
        }
        label("OUI-TAK", 30f)
        label("SKY SPY USB · MILESTONE 0.5", 14f)
        status = label("")
        monitorStatus=label("Monitoring stopped",14f)
        val monitorControls=LinearLayout(this).apply { orientation=LinearLayout.VERTICAL;visibility=View.GONE }
        button("Monitoring controls") { monitorControls.visibility=if(monitorControls.visibility==View.GONE) View.VISIBLE else View.GONE }
        panel.addView(monitorControls)
        fun monitoringButton(text:String,action:()->Unit) = Button(this).apply {
            this.text=text;setOnClickListener { action() };monitorControls.addView(this)
        }
        monitoringButton("Start Monitoring") { startBackground(false,false) }
        monitoringButton("Start Monitoring with receiver GPS") { startBackground(true,false) }
        monitoringButton("Start Live OUI-SPY USB") { startBackground(false,false,true) }
        if(DebugSerialSourceFactory.available) monitoringButton("Run synthetic Sky Spy serial test (debug only)") {
            startBackground(false,false,false,true)
        }
        lateinit var legacyPolicyButton:Button
        legacyPolicyButton=monitoringButton(if(org.ouitak.app.storage.HistoryDatabase.preferences(this).getBoolean(MonitoringService.LEGACY_POLICY,true))
            "Legacy IDs: provisional markers enabled" else "Legacy IDs: provisional markers disabled") {
            val prefs=org.ouitak.app.storage.HistoryDatabase.preferences(this)
            val enabled=!prefs.getBoolean(MonitoringService.LEGACY_POLICY,true)
            prefs.edit().putBoolean(MonitoringService.LEGACY_POLICY,enabled).apply()
            legacyPolicyButton.text=if(enabled) "Legacy IDs: provisional markers enabled" else "Legacy IDs: provisional markers disabled"
        }
        monitoringButton("Stop Monitoring") { stopBackground() }
        monitoringButton("Request ESP32-S3 USB access") {
            if(MonitoringService.status.active) startService(Intent(this,MonitoringService::class.java).setAction(MonitoringService.REQUEST_USB))
            else monitorStatus.text="Start Live OUI-SPY USB to request device access"
        }
        monitoringButton("TAK delivery mode: ${TakServerSettings.mode(this)}") { chooseCotConnectionMode() }
        monitoringButton("Configure TAK Server host and port") { editTakServerAddress() }
        monitoringButton("Install TAK client certificate (.p12)") { chooseCredentialPackage() }
        monitoringButton("Select installed TAK client certificate") { chooseClientCertificate() }
        monitoringButton("Import TAK Server CA certificate") { chooseTrustCertificate() }
        label("Synthetic Remote ID claims only; no RF transmission. Open ATAK with UDP input 4242 enabled. Aircraft A is near 0°, 0°; B near 8°, 20°.", 14f)
        selection = button("") {
            selected = if (selected == "SIM-DRONE-001") "SIM-DRONE-002" else "SIM-DRONE-001"
            render()
        }
        button("Generate detection") { generate() }
        toggle = button("Start simulation") {
            running = !running
            handler.removeCallbacks(tick)
            if (running) handler.post(tick)
            render()
        }
        button("Clear history") { historyPreview=null; history.clear(); tracker.clear(); render() }
        sendTest = button("Send Test Detection to ATAK") { sendToAtak(listOf(selected)) }
        sendPair = button("Send two aircraft to ATAK") { sendToAtak(listOf("SIM-DRONE-001", "SIM-DRONE-002")) }
        expiry = button("") {
            val options = listOf(60L, 120L, 300L, 30L)
            tracker.staleAfter = Duration.ofSeconds(options[(options.indexOf(tracker.staleAfter.seconds) + 1) % options.size])
            render()
        }
        features=HistoryFeatures(this,
            preview={ event->historyPreview=event;xml.text=event },
            fixtures={ records-> records.forEach { d->tracker.observe(d);history.addFirst(d) };while(history.size>50) history.removeLast();render() },
            startReplay={ startBackground(false,true) },
            replayCommand={ action,speed-> if(MonitoringService.status.active) startService(Intent(this,MonitoringService::class.java).setAction(action).apply { speed?.let { putExtra("speed",it) } }) },
            stopMonitoring={ stopBackground() },network={ action->ensureNetwork(action) },
            cotTransportFactory={ TakServerSettings.createTransport(this) })
        features.attach(panel)
        delivery = label("ATAK: no send attempted. UDP has no acknowledgement.", 14f)
        label("ACTIVE AIRCRAFT · unverified claims", 18f)
        tracks = label("")
        label("DETECTION HISTORY · latest 50", 18f)
        detections = label("")
        label("LATEST CoT XML · preview", 18f)
        xml = label("", 12f).apply { setTextIsSelectable(true) }
        setContentView(ScrollView(this).apply { addView(panel) })
        render()
    }

    private fun record(identifier: String): TrackUpdate {
        historyPreview=null
        val detection = simulator.next(identifier = identifier)
        val update = tracker.observe(detection)
        features.record(detection)
        history.addFirst(detection)
        while (history.size > 50) history.removeLast()
        return update
    }

    private fun generate() { record(selected); render() }

    private fun sendToAtak(identifiers: List<String>) {
        if(!networkGranted()) { ensureNetwork { sendToAtak(identifiers) };return }
        val events = try {
            identifiers.map { id ->
                val update = record(id)
                require(update is TrackUpdate.Updated) { (update as TrackUpdate.Ignored).reason }
                val d = update.track.latest
                d to CotGenerator(tracker.staleAfter).generate(d)
            }
        } catch (e: IllegalArgumentException) {
            delivery.text = "Generation/tracking rejected: ${e.message}. No send attempted."
            return
        }
        render()
        sendTest.isEnabled = false
        sendPair.isEnabled = false
        delivery.text = "Generated ${events.size} event(s). Submitting via ${TakServerSettings.mode(this)}…"
        val timeout = tracker.staleAfter.seconds
        sender.execute {
            val results = try { TakServerSettings.createTransport(this).use { selectedTransport ->
                events.map { (detection, event) ->
                    try {
                        val submission = selectedTransport.send(event)
                        features.enrich(detection).forEach { selectedTransport.send(it) }
                        "${detection.identifier}\nUID: ${detection.cotUid}\n" +
                            "Generated: ${detection.observedAt}; submission: ${submission.bytes} bytes to ${submission.endpoint}\n" +
                            "Receipt is not independently acknowledged. ${submission.acknowledgement}."
                    } catch (e: Exception) {
                        "${detection.identifier}: event generated; transport FAILED: ${e.javaClass.simpleName}: ${e.message}."
                    }
                }
            } } catch(e:Exception) { listOf("Transport selection failed: ${e.javaClass.simpleName}: ${e.message}") }
            handler.post {
                if (!isDestroyed) {
                    delivery.text = results.joinToString("\n\n") + "\nInspect the configured receiver; delivery receipt is not guaranteed."
                    sendTest.isEnabled = true; sendPair.isEnabled = true
                }
            }
        }
    }

    private fun render() {
        val service=MonitoringService.status
        monitorStatus.text="Monitoring: ${if(service.active) service.mode else "stopped"} · ${service.submitted} CoT submissions\n${service.message}\nTransport: ${service.transportState} · ${service.transportMessage}\nDelivery: ${deliveryConfigurationSummary()}\nSerial accepted ${service.accepted}, ignored ${service.ignored}, rejected ${service.rejected}, disconnects ${service.disconnects} · receiver GPS ${service.gps}"
        val active = tracker.active()
        if(::features.isInitialized) features.refreshSummary()
        status.text = "Sensor: ${when { service.active && service.mode=="LIVE_USB" -> "ESP32-S3 USB serial"; service.active && service.mode=="EMULATED_SERIAL" -> "SYNTHETIC serial source"; else -> "disconnected" }}\nSimulation: ${if (running) "running" else "stopped"} · ${history.size} observations · ${active.size} local aircraft"
        selection.text = "Aircraft: $selected (tap to switch)"
        expiry.text = "Marker expiry: ${tracker.staleAfter.seconds}s (tap to change)"
        toggle.text = if (running) "Stop simulation" else "Start simulation"
        tracks.text = if (active.isEmpty()) "No active aircraft." else active.joinToString("\n\n") {
            "${it.latest.identifier} · ${it.latest.identifierType} · ${it.latest.source} · UNVERIFIED\n" +
                "${it.uid}\nLast seen ${it.lastSeen} · expires ${it.expiresAt}\n" +
                "${it.latest.position?.latitude}, ${it.latest.position?.longitude}; HAE ${it.latest.position?.haeMeters} m\n" +
                "Speed ${it.latest.speedMps ?: "unknown"} m/s; heading/course ${it.latest.headingDegrees ?: "unknown"}° true"
        }
        detections.text = if (history.isEmpty()) "No detections. Generate one or start simulation." else history.joinToString("\n\n") {
            "${it.identifier} · ${it.source}\n${it.observedAt}\n${it.position?.latitude}, ${it.position?.longitude} · HAE ${it.position?.haeMeters} m"
        }
        xml.text = historyPreview ?: history.firstOrNull()?.let { CotGenerator(tracker.staleAfter).generate(it) } ?: "No event generated."
    }

    private fun networkGranted() = android.os.Build.VERSION.SDK_INT<37 || checkSelfPermission("android.permission.ACCESS_LOCAL_NETWORK")==PackageManager.PERMISSION_GRANTED
    private fun deliveryConfigurationSummary():String {
        if(TakServerSettings.mode(this)==CotConnectionMode.LOCAL_UDP) return "local UDP 127.0.0.1:4242"
        val prefs=org.ouitak.app.storage.HistoryDatabase.preferences(this)
        return "TAK TLS ${prefs.getString(TakServerSettings.PREF_HOST,"(host unset)")}:${prefs.getInt(TakServerSettings.PREF_PORT,8089)} · client cert ${if(prefs.contains(TakServerSettings.PREF_CLIENT_ALIAS)) "selected" else "missing"} · trust ${if(TakServerSettings.caFile(this).isFile) "custom CA imported" else "Android system roots"}"
    }

    private fun chooseCotConnectionMode() {
        val modes=arrayOf("Local ATAK UDP (127.0.0.1:4242)","Remote TAK Server TLS with client certificate")
        AlertDialog.Builder(this).setTitle("CoT delivery mode").setItems(modes) { _,which ->
            TakServerSettings.saveMode(this,if(which==0) CotConnectionMode.LOCAL_UDP else CotConnectionMode.TAK_SERVER_TLS)
            monitorStatus.text="Delivery mode saved. Restart monitoring to apply it."
        }.setNegativeButton("Cancel",null).show()
    }

    private fun editTakServerAddress() {
        val prefs=org.ouitak.app.storage.HistoryDatabase.preferences(this)
        val form=LinearLayout(this).apply { orientation=LinearLayout.VERTICAL;setPadding(32,8,32,0) }
        val host=EditText(this).apply { hint="server.example.org";setSingleLine(true);setText(prefs.getString(TakServerSettings.PREF_HOST,"")) }
        val port=EditText(this).apply { hint="8089";inputType=InputType.TYPE_CLASS_NUMBER;setSingleLine(true);setText(prefs.getInt(TakServerSettings.PREF_PORT,8089).toString()) }
        form.addView(host);form.addView(port)
        AlertDialog.Builder(this).setTitle("TAK Server TLS endpoint").setMessage("Use the server administrator's hostname and TLS streaming port.")
            .setView(form).setPositiveButton("Save") { _,_ ->
                val h=host.text.toString().trim();val p=port.text.toString().toIntOrNull()
                if(h.isBlank() || p==null || p !in 1..65535) monitorStatus.text="Invalid TAK Server hostname or port"
                else prefs.edit().putString(TakServerSettings.PREF_HOST,h).putInt(TakServerSettings.PREF_PORT,p!!).apply()
            }.setNegativeButton("Cancel",null).show()
    }

    private fun chooseCredentialPackage() {
        startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).setType("application/x-pkcs12").addCategory(Intent.CATEGORY_OPENABLE),51)
    }

    private fun chooseTrustCertificate() {
        startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).setType("*/*").addCategory(Intent.CATEGORY_OPENABLE),52)
    }

    private fun chooseClientCertificate() {
        val prefs=org.ouitak.app.storage.HistoryDatabase.preferences(this)
        KeyChain.choosePrivateKeyAlias(this,{ alias -> runOnUiThread {
            if(alias==null) monitorStatus.text="No TAK client certificate selected"
            else {
                prefs.edit().putString(TakServerSettings.PREF_CLIENT_ALIAS,alias).apply()
                monitorStatus.text="Android TAK client certificate selected; private key remains in Android KeyChain"
            }
        } },arrayOf("RSA","EC"),null,prefs.getString(TakServerSettings.PREF_HOST,null),
            prefs.getInt(TakServerSettings.PREF_PORT,8089),prefs.getString(TakServerSettings.PREF_CLIENT_ALIAS,null))
    }

    @Deprecated("Uses the Activity result flow for Android's credential installation UI")
    override fun onActivityResult(requestCode:Int,resultCode:Int,data:Intent?) {
        super.onActivityResult(requestCode,resultCode,data)
        if(resultCode!=RESULT_OK || data?.data==null) return
        val bytes=try {
            contentResolver.openInputStream(data.data!!)?.use { input ->
                val out=java.io.ByteArrayOutputStream();val chunk=ByteArray(8192);var total=0
                val limit=if(requestCode==51) 16*1024*1024 else 1_048_576
                while(true) { val n=input.read(chunk);if(n<0) break;total+=n;require(total<=limit) { "Selected file exceeds allowed size" };out.write(chunk,0,n) }
                out.toByteArray()
            } ?: error("Could not read selected document")
        } catch(e:Exception) { monitorStatus.text="Certificate file read failed: ${e.javaClass.simpleName}";return }
        when(requestCode) {
            51 -> try {
                val install=KeyChain.createInstallIntent().putExtra(KeyChain.EXTRA_PKCS12,bytes)
                startActivity(install)
                monitorStatus.text="Android is installing the credential. Return and select its certificate alias after installation."
            } catch(e:Exception) { monitorStatus.text="Credential installation could not start: ${e.javaClass.simpleName}" }
            52 -> try {
                val certificates=TakServerSettings.parseCaCertificates(bytes)
                val details=certificates.joinToString("\n\n") { "${it.subjectX500Principal.name}\nSHA-256 ${TakServerSettings.fingerprint(it)}" }
                AlertDialog.Builder(this).setTitle("Trust these TAK Server CA certificates?")
                    .setMessage("Verify each fingerprint with your server administrator before trusting.\n\n$details")
                    .setPositiveButton("Trust and save") { _,_ ->
                        try { TakServerSettings.saveCaCertificates(this,certificates);monitorStatus.text="TAK TLS trust roots saved" }
                        catch(e:Exception) { monitorStatus.text="Trust certificate save failed: ${e.javaClass.simpleName}" }
                    }.setNegativeButton("Cancel",null).show()
            } catch(e:Exception) { monitorStatus.text="Trust import rejected: ${e.message}" }
        }
    }
    private fun ensureNetwork(action:()->Unit) {
        if(networkGranted()) action() else {
            pendingPermissionAction=action
            requestPermissions(arrayOf("android.permission.ACCESS_LOCAL_NETWORK"),40)
        }
    }
    private fun startBackground(gps:Boolean,replay:Boolean,live:Boolean=false,emulatedSerial:Boolean=false) {
        val permissions=mutableListOf<String>()
        if(!networkGranted()) permissions+="android.permission.ACCESS_LOCAL_NETWORK"
        if(android.os.Build.VERSION.SDK_INT>=33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED) permissions+=Manifest.permission.POST_NOTIFICATIONS
        if(gps && checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)!=PackageManager.PERMISSION_GRANTED) {
            permissions+=Manifest.permission.ACCESS_FINE_LOCATION;permissions+=Manifest.permission.ACCESS_COARSE_LOCATION
        }
        if(permissions.isNotEmpty()) {
            pendingPermissionAction={
                if(gps && checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)!=PackageManager.PERMISSION_GRANTED) monitorStatus.text="Receiver GPS monitoring requires location permission; use Start Monitoring without GPS instead"
                else if(networkGranted()) beginService(gps,replay,live,emulatedSerial)
                else monitorStatus.text="Local network permission denied; monitoring not started"
            }
            requestPermissions(permissions.toTypedArray(),41)
        } else beginService(gps,replay,live,emulatedSerial)
    }
    private fun beginService(gps:Boolean,replay:Boolean,live:Boolean=false,emulatedSerial:Boolean=false) {
        try { startForegroundService(Intent(this,MonitoringService::class.java).putExtra(MonitoringService.GPS,gps).putExtra(MonitoringService.REPLAY,replay).putExtra(MonitoringService.LIVE_USB,live).putExtra(MonitoringService.EMULATED_SERIAL,emulatedSerial)) }
        catch(e:RuntimeException) { monitorStatus.text="Monitoring start denied: ${e.javaClass.simpleName}" }
    }
    private fun stopBackground() {
        org.ouitak.app.storage.HistoryDatabase.preferences(this).edit().putBoolean("monitorRequested",false).apply()
        stopService(Intent(this,MonitoringService::class.java))
    }
    override fun onRequestPermissionsResult(requestCode:Int,permissions:Array<out String>,grantResults:IntArray) {
        super.onRequestPermissionsResult(requestCode,permissions,grantResults)
        val action=pendingPermissionAction;pendingPermissionAction=null
        if(requestCode==41) action?.invoke()
        else if(requestCode==40 && networkGranted()) action?.invoke()
        else monitorStatus.text="Permission denied; local history and simulation remain available"
    }

    override fun onStart() { super.onStart(); handler.post(refresh) }
    override fun onStop() {
        running = false
        handler.removeCallbacks(tick); handler.removeCallbacks(refresh)
        render()
        super.onStop()
    }
    override fun onDestroy() { features.close();sender.shutdown(); super.onDestroy() }
}
