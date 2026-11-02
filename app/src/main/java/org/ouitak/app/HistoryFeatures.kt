package org.ouitak.app

import android.app.Activity
import android.app.AlertDialog
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import org.ouitak.app.core.*
import org.ouitak.app.monitor.MonitoringService
import org.ouitak.app.monitor.TakServerSettings
import org.ouitak.app.storage.HistoryDatabase
import java.time.Duration
import java.time.Instant
import java.util.concurrent.Executors

/** UI adapter; SQLite, serialization and delivery stay off the main thread. */
class HistoryFeatures(
    private val activity:Activity,
    private val preview:(String)->Unit,
    private val fixtures:(List<Detection>)->Unit,
    private val startReplay:()->Unit,
    private val replayCommand:(String,Double?)->Unit,
    private val stopMonitoring:()->Unit,
    private val network:(()->Unit)->Unit,
    private val cotTransportFactory:()->CotTransport={ LoopbackAtakTransport() }
) {
    private val context=activity.applicationContext
    private val worker=Executors.newSingleThreadExecutor()
    private val main=Handler(Looper.getMainLooper())
    private val prefs=HistoryDatabase.preferences(context)
    private lateinit var summary:TextView
    private lateinit var recording:Button
    private lateinit var retention:Button
    private lateinit var length:Button
    private lateinit var locationExpiry:Button
    private lateinit var speed:Button
    private var stored=0L
    private var aircraft=0
    private var busy=false
    private var lastSummary=0L
    private var details=""
    fun attach(panel:LinearLayout) {
        val controls=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL; visibility=View.GONE }
        Button(activity).apply { text="History and replay controls";setOnClickListener { controls.visibility=if(controls.visibility==View.GONE) View.VISIBLE else View.GONE };panel.addView(this) }
        panel.addView(controls)
        fun button(text:String,action:()->Unit)=Button(activity).apply { this.text=text;setOnClickListener { action() };controls.addView(this) }
        recording=button("") { prefs.edit().putBoolean("recording",!prefs.getBoolean("recording",true)).apply();refreshLabels() }
        retention=button("") {
            val options=listOf(604800L,3600L,86400L,2592000L)
            prefs.edit().putLong("retention",options[(options.indexOf(prefs.getLong("retention",604800))+1)%options.size]).apply()
            runWork { HistoryDatabase.cleanup(context); "Retention cleanup completed" }
        }
        length=button("") {
            val options=listOf(20,50,100,5)
            prefs.edit().putInt("trailPoints",options[(options.indexOf(prefs.getInt("trailPoints",20))+1)%options.size]).apply();refreshLabels()
        }
        locationExpiry=button("") {
            val options=listOf(300L,60L,3600L)
            prefs.edit().putLong("locationExpiry",options[(options.indexOf(prefs.getLong("locationExpiry",300))+1)%options.size]).apply();refreshLabels()
        }
        button("Load synthetic flight history") {
            stopMonitoring()
            val values=SyntheticHistory.observations(Instant.now())
            prefs.edit().putBoolean("recording",true).apply()
            runWork {
                values.forEach { HistoryDatabase.record(context,it) };HistoryDatabase.cleanup(context)
                main.post { if(!activity.isDestroyed) fixtures(values) }
                "Loaded 8 synthetic observations for 2 aircraft; geometry demo, not realistic flight performance"
            }
        }
        button("Send stored trails to ATAK") { network { publishTrails() } }
        speed=button("") {
            val options=listOf(1f,2f,4f,16f,0.5f)
            val value=options[(options.indexOf(prefs.getFloat("replaySpeed",1f))+1)%options.size]
            prefs.edit().putFloat("replaySpeed",value).apply();replayCommand(MonitoringService.SPEED,value.toDouble());refreshLabels()
        }
        button("Replay stored history") { startReplay() }
        button("Pause replay") { replayCommand(MonitoringService.PAUSE,null) }
        button("Resume replay") { replayCommand(MonitoringService.RESUME,null) }
        button("Reset replay") { replayCommand(MonitoringService.RESET,null) }
        button("Delete all stored observations") {
            AlertDialog.Builder(activity).setTitle("Delete all stored observations?")
                .setMessage("Stops monitoring and replay, disables recording, and deletes this app's local history. ATAK retains its own previously received objects.")
                .setNegativeButton("Cancel",null).setPositiveButton("Delete") { _,_->
                    stopMonitoring();runWork { HistoryDatabase.deleteAndDisable(context);"All local history deleted; recording OFF" }
                }.show()
        }
        summary=TextView(activity).apply { textSize=14f;setPadding(0,12,0,12);controls.addView(this) }
        refreshLabels();refreshSummary(true)
    }
    fun refreshLabels() {
        if(!::summary.isInitialized) return
        recording.text="History recording: ${if(prefs.getBoolean("recording",true)) "ON" else "OFF"}"
        retention.text="Retention: ${prefs.getLong("retention",604800)/3600} hours"
        length.text="Trail length: ${prefs.getInt("trailPoints",20)} points"
        locationExpiry.text="Reported location expiry: ${prefs.getLong("locationExpiry",300)}s"
        speed.text="Replay speed: ${prefs.getFloat("replaySpeed",1f)}x"
        summary.text="Stored: $stored observations · $aircraft aircraft\n$details\nReplay: ${MonitoringService.status.replayState}; no replay writes to history."
    }
    fun refreshSummary(force:Boolean=false) {
        refreshLabels()
        if(busy || (!force && android.os.SystemClock.elapsedRealtime()-lastSummary<5000)) return
        lastSummary=android.os.SystemClock.elapsedRealtime();busy=true
        worker.execute {
            try {
                HistoryDatabase.cleanup(context)
                val store=HistoryDatabase.get(context);val ids=store.aircraft();val count=store.observationCount()
                main.post { if(!activity.isDestroyed) { stored=count;aircraft=ids.size;busy=false;refreshLabels() } }
            } catch(e:Exception) { finish("History error: ${e.javaClass.simpleName}",false) }
        }
    }
    fun record(d:Detection) { worker.execute { try { HistoryDatabase.record(context,d) } catch(e:Exception) { finish("History write failed: ${e.javaClass.simpleName}",false) } } }
    /** Called by the existing send worker, not the Activity UI thread. */
    fun enrich(d:Detection):List<String> {
        HistoryDatabase.record(context,d);HistoryDatabase.cleanup(context)
        val now=Instant.now();val policy=HistoryDatabase.policy(context)
        val points=HistoryDatabase.get(context).recentPositions(d.cotUid,now.minus(policy.retention),now,policy.trailPoints)
        return buildList {
            if(points.size>=2) add(TrailCotGenerator().generate(points,now))
            for(role in listOf(LocationRole.CONTROL_STATION,LocationRole.TAKEOFF,LocationRole.RECEIVER))
                LocationCotGenerator(Duration.ofSeconds(prefs.getLong("locationExpiry",300))).generate(d,role,now)?.let { add(it) }
        }
    }
    private fun publishTrails() { runWork {
        HistoryDatabase.cleanup(context);val store=HistoryDatabase.get(context);var sent=0
        cotTransportFactory().use { cotTransport ->
            for(uid in store.aircraft()) {
                val observations=store.observations(uid);val now=Instant.now()
                val points=trailPoints(observations,now,HistoryDatabase.policy(context))
                if(points.size>=2) {
                    val event=TrailCotGenerator().generate(points,now)
                    cotTransport.send(event);sent++
                    main.post { if(!activity.isDestroyed) preview(event) }
                }
                for(role in listOf(LocationRole.CONTROL_STATION,LocationRole.TAKEOFF,LocationRole.RECEIVER)) {
                    val latest=observations.filter { it.locations.containsKey(role) }.maxByOrNull { it.locations.getValue(role).observedAt }
                    if(latest!=null) LocationCotGenerator(Duration.ofSeconds(prefs.getLong("locationExpiry",300))).generate(latest,role,now)?.let {
                        cotTransport.send(it);sent++
                    }
                }
            }
        }
        "$sent trail/location submissions through ${TakServerSettings.mode(context)}; receiver ingestion is not acknowledged."
    } }
    private fun runWork(block:()->String) {
        worker.execute { try { finish(block()) } catch(e:Exception) { finish("History action failed: ${e.javaClass.simpleName}",false) } }
    }
    private fun finish(message:String,query:Boolean=true) { main.post { if(!activity.isDestroyed) { details=message;busy=false;if(query) refreshSummary(true) else refreshLabels() } } }
    fun close() { worker.shutdown() }
}
