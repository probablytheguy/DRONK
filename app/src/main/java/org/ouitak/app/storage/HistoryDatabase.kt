package org.ouitak.app.storage

import android.content.Context
import org.ouitak.app.core.Detection
import org.ouitak.app.core.HistoryPolicy
import java.time.Duration
import java.time.Instant

/** Shared app-private helper; lifetime is the process, not an Activity. */
object HistoryDatabase {
    private var instance: SqliteFlightHistory? = null
    @Synchronized fun get(context: Context): SqliteFlightHistory = instance ?: SqliteFlightHistory(context).also { instance=it }
    fun preferences(context: Context) = context.getSharedPreferences("history-settings",Context.MODE_PRIVATE)
    fun policy(context: Context): HistoryPolicy {
        val prefs=preferences(context)
        return HistoryPolicy(Duration.ofSeconds(prefs.getLong("retention",604800)),prefs.getInt("trailPoints",20))
    }
    @Synchronized fun record(context: Context, d: Detection) {
        if (preferences(context).getBoolean("recording",true)) get(context).append(d)
    }
    @android.annotation.SuppressLint("ApplySharedPref") // Storage worker: persist OFF before deletion completes.
    @Synchronized fun deleteAndDisable(context: Context) {
        check(preferences(context).edit().putBoolean("recording",false).commit()) { "Cannot disable history recording" }
        get(context).deleteAll()
    }
    @Synchronized fun cleanup(context: Context) = get(context).cleanup(Instant.now().minus(policy(context).retention))
}
