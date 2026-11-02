package org.ouitak.app.storage

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import org.json.JSONObject
import org.ouitak.app.core.*
import java.time.Instant

/** App-private SQLite. No network, raw logging, external storage or cloud sync.
 * Queries/transactions run on the caller's storage worker, never the UI thread.
 */
class SqliteFlightHistory(context: Context, name: String = "flight-history.sqlite") :
    SQLiteOpenHelper(context.applicationContext, name, null, 1), FlightHistory {
    override fun onConfigure(db: SQLiteDatabase) {
        super.onConfigure(db)
        // This PRAGMA returns a row; Android rejects it through execSQL.
        db.rawQuery("PRAGMA secure_delete=ON", null).use { cursor ->
            check(cursor.moveToFirst() && cursor.getInt(0) == 1) { "SQLite secure deletion unavailable" }
        }
    }
    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("CREATE TABLE observations (record_id TEXT PRIMARY KEY, aircraft_uid TEXT NOT NULL, sec INTEGER NOT NULL, nano INTEGER NOT NULL, payload TEXT NOT NULL)")
        db.execSQL("CREATE INDEX aircraft_time ON observations(aircraft_uid,sec,nano)")
    }
    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        error("History migration required; refusing destructive upgrade")
    }
    override fun append(detection: Detection): Boolean {
        require(detection.locations.values.any { it.position != null || it.validity == LocationValidity.INVALID }) { "No location observation" }
        // Retention can prune auxiliary fields while retaining the original key
        // to reject re-receipts. Also reject a duplicate of that pruned payload.
        val key=historyKey(detection)
        readableDatabase.query("observations",arrayOf("payload"),"aircraft_uid=? AND sec=? AND nano=?",
            arrayOf(detection.cotUid,detection.observedAt.epochSecond.toString(),detection.observedAt.nano.toString()),null,null,null).use { cursor ->
            while(cursor.moveToNext()) if(historyKey(decode(cursor.getString(0)))==key) return false
        }
        return writableDatabase.insertWithOnConflict("observations", null, values(detection), SQLiteDatabase.CONFLICT_IGNORE) != -1L
    }
    override fun aircraft(): List<String> = readableDatabase.rawQuery(
        "SELECT DISTINCT aircraft_uid FROM observations ORDER BY aircraft_uid", null).use { c -> buildList {
            while (c.moveToNext()) add(c.getString(0))
        } }
    override fun observations(aircraftUid: String): List<Detection> = readableDatabase.query(
        "observations", arrayOf("payload"), "aircraft_uid=?", arrayOf(aircraftUid), null, null, "sec,nano,record_id").use { c -> buildList {
            while (c.moveToNext()) add(decode(c.getString(0)))
        } }
    fun observationCount(): Long = readableDatabase.rawQuery("SELECT count(*) FROM observations",null).use {
        check(it.moveToFirst());it.getLong(0)
    }
    /** Walk the time index only until the requested valid vertices are found.
     * Continuous monitoring must not deserialize an entire retained flight.
     */
    fun recentPositions(uid: String, from: Instant, through: Instant, limit: Int): List<Detection> {
        require(limit in 2..100)
        return readableDatabase.query("observations",arrayOf("payload"),
            "aircraft_uid=? AND (sec>? OR (sec=? AND nano>=?)) AND (sec<? OR (sec=? AND nano<=?))",
            arrayOf(uid,from.epochSecond.toString(),from.epochSecond.toString(),from.nano.toString(),through.epochSecond.toString(),through.epochSecond.toString(),through.nano.toString()),
            null,null,"sec DESC,nano DESC,record_id DESC").use { cursor ->
            buildList {
                while(size<limit && cursor.moveToNext()) decode(cursor.getString(0)).takeIf { it.position!=null }?.let { add(it) }
            }.reversed()
        }
    }
    override fun cleanup(before: Instant): Int {
        val db = writableDatabase
        var changed = 0
        db.beginTransaction()
        try {
            db.query("observations", arrayOf("record_id", "payload"), null, null, null, null, null).use { c ->
                while (c.moveToNext()) {
                    val original = decode(c.getString(1))
                    val retained = retainedDetection(original, before)
                    if (retained == null) { db.delete("observations", "record_id=?", arrayOf(c.getString(0))); changed++ }
                    else if (retained != original) {
                        db.update("observations", ContentValues().apply { put("payload", encode(retained)) }, "record_id=?", arrayOf(c.getString(0))); changed++
                    }
                }
            }
            db.setTransactionSuccessful()
        } finally { db.endTransaction() }
        return changed
    }
    override fun deleteAll() {
        // Secure deletion clears deleted SQLite cells; there is no WAL/sidecar history.
        // onConfigure enables secure deletion for this connection.
        writableDatabase.delete("observations", null, null)
        writableDatabase.execSQL("VACUUM")
    }
    private fun values(d: Detection) = ContentValues().apply {
        put("record_id", historyKey(d)); put("aircraft_uid", d.cotUid)
        put("sec", d.observedAt.epochSecond); put("nano", d.observedAt.nano); put("payload", encode(d))
    }
    private fun encode(d: Detection): String = JSONObject().apply {
        put("identifier", d.identifier); put("identifierType", d.identifierType.name); put("source", d.source.name)
        put("observedAt", d.observedAt.toString()); put("receivedAt", d.receivedAt.toString())
        put("aircraftExplicit", d.aircraftLocationReport != null)
        put("speed", d.speedMps); put("heading", d.headingDegrees)
        put("locations", JSONObject().apply { d.locations.forEach { (role, report) ->
            put(role.name, JSONObject().apply {
                put("time", report.observedAt.toString()); put("provenance", report.provenance.name); put("validity", report.validity.name)
                report.position?.let { put("lat", it.latitude); put("lon", it.longitude); put("hae", it.haeMeters) }
            })
        } })
    }.toString()
    private fun decode(payload: String): Detection {
        val o = JSONObject(payload)
        val locations = o.getJSONObject("locations")
        fun number(j: JSONObject, name: String) = if (j.has(name) && !j.isNull(name)) j.getDouble(name) else null
        fun report(role: LocationRole): ReportedLocation? {
            if (!locations.has(role.name)) return null
            val j = locations.getJSONObject(role.name)
            val validity = LocationValidity.valueOf(j.getString("validity"))
            val position = if (validity == LocationValidity.VALID) Position(j.getDouble("lat"), j.getDouble("lon"), number(j, "hae")) else null
            return ReportedLocation(position, Instant.parse(j.getString("time")), LocationProvenance.valueOf(j.getString("provenance")), validity)
        }
        val aircraft = requireNotNull(report(LocationRole.AIRCRAFT))
        return Detection(o.getString("identifier"), Instant.parse(o.getString("observedAt")), Instant.parse(o.getString("receivedAt")),
            aircraft.position, ObservationSource.valueOf(o.getString("source")), IdentifierType.valueOf(o.getString("identifierType")),
            number(o,"speed"), number(o,"heading"), if (o.optBoolean("aircraftExplicit")) aircraft else null, report(LocationRole.CONTROL_STATION), report(LocationRole.TAKEOFF), report(LocationRole.RECEIVER))
    }
}
